#!/usr/bin/env python3
"""bangumi_to_enazos_seele：把 Bangumi 标记列表转成 enazo 词库。

数据来源二选一：

    python src/bangumi_to_enazo.py                  # 用 local.json 里的用户列表
    python src/bangumi_to_enazo.py --user sai       # 临时指定一个用户
    python src/bangumi_to_enazo.py --input in/collection.xlsx   # 已有的收藏导出表

产出按日期存档，同一天重跑覆盖当天的文件：

    bin/exports/2026-10-20/sai.txt
    bin/exports/2026-10-20/report.md

中间数据都在 bin/data 下：收藏快照 collections/、条目缓存 subjects/。
收藏列表按 updated_at 倒序增量拉取，条目别名抓到一次就不再请求。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as date_cls
from pathlib import Path

from bangumi.client import Http
from bangumi.collection import TYPE_NAMES, CollectionStore, SyncStats
from bangumi.sheet import read_collection
from bangumi.subject import SubjectStore
from enazo.bank import DASH_MODES, build_line, check_lines, sanitize

ROOT = Path(__file__).resolve().parent.parent  # 代码在 src/ 下，项目根是上一层
LOCAL_JSON = ROOT / "local.json"
DATA_DIR = ROOT / "bin" / "data"
EXPORTS_DIR = ROOT / "bin" / "exports"

SUBJECT_ID_RE = re.compile(r"/subject/(\d+)")
STATUS_NAMES = tuple(TYPE_NAMES.values())
name_to_type = {name: type_id for type_id, name in TYPE_NAMES.items()}
INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\s]+')


def safe_name(text: str, fallback: str) -> str:
    """文件名用昵称：去掉 Windows 不允许的字符，空了就退回用户标识。"""
    cleaned = INVALID_FILENAME.sub("_", (text or "").strip()).strip("_")
    return (cleaned or fallback)[:60]


# ---------------------------------------------------------------- 配置


def load_local_config() -> dict:
    if not LOCAL_JSON.exists():
        return {}
    try:
        return json.loads(LOCAL_JSON.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"local.json 读不出来（{exc}），这次只看命令行参数", file=sys.stderr)
        return {}


def parse_args(argv=None):
    config = load_local_config()
    parser = argparse.ArgumentParser(
        description="把 Bangumi 标记列表转成 enazo 词库，按日期存档。"
    )
    parser.add_argument("--user", action="append", default=[], help="Bangumi 用户名，可重复")
    parser.add_argument("--input", type=Path, help="改用收藏导出表（xlsx / csv）")
    parser.add_argument(
        "--subject-type",
        type=int,
        default=config.get("subject_type", 2),
        help="条目类型，默认 2=动画",
    )
    parser.add_argument("--date", default="", help="产出目录用的日期，默认今天")
    parser.add_argument("--full", action="store_true", help="全量重拉收藏列表，不做增量")
    parser.add_argument("--offline", action="store_true", help="只用本地缓存和快照，不发请求")
    parser.add_argument(
        "--status",
        default=config.get("status", "看过"),
        help=f"导出哪些标记状态，逗号分隔；默认只导看过。可选 {'/'.join(STATUS_NAMES)}/all",
    )
    parser.add_argument("--dash", choices=sorted(DASH_MODES), default=config.get("dash", "all"))
    parser.add_argument("--no-hint", action="store_true", help="不输出 $ 后面的提示")
    parser.add_argument(
        "--hint-sep",
        default=",",
        help="多条评论并列时用的分隔符，默认半角逗号（题库标准写法）；传空串表示直接相接",
    )
    parser.add_argument("--check", action="store_true", help="写完后体检输出格式")
    parser.add_argument(
        "--delay", type=float, default=config.get("delay", 0.5), help="两次请求的间隔秒数"
    )
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条，用于试水")
    parser.add_argument(
        "--workers",
        type=int,
        default=config.get("workers", 6),
        help="并发抓条目详情的线程数，默认 6",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=config.get("rate", 6.0),
        help="全局每秒最多几个请求，默认 6；0 表示不限速（按 --delay 串行）",
    )
    parser.add_argument(
        "--progress-every", type=int, default=50, help="每处理 N 条打一条进度，0 表示不打"
    )
    args = parser.parse_args(argv)
    args.users = args.user or config.get("users") or []
    return args


# ---------------------------------------------------------------- 数据源


def load_profiles() -> dict:
    """昵称缓存，离线跑也能用昵称命名产出。"""
    path = DATA_DIR / "profiles.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_profiles(profiles: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "profiles.json").write_text(
        json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_statuses(value: str) -> list[tuple[int, str]]:
    """把 --status 拆成 [(收藏类型, 中文名), ...]；all 表示五种状态都要。"""
    parts = [part.strip() for part in (value or "").replace("，", ",").split(",") if part.strip()]
    if not parts or parts == ["all"] or parts == ["全部"]:
        return sorted(TYPE_NAMES.items())
    unknown = [part for part in parts if part not in STATUS_NAMES]
    if unknown:
        raise ValueError(
            f"不认识的标记状态：{'、'.join(unknown)}"
            f"（可选 {'/'.join(STATUS_NAMES)}/all）"
        )
    return [(name_to_type[part], part) for part in parts]


def load_from_users(args, statuses):
    """按用户、按收藏类型同步，合并成一个列表。"""
    http = Http(rate=args.rate, delay=args.delay)
    store = CollectionStore(DATA_DIR / "collections", http)
    profiles = load_profiles()
    results = {}
    for user in args.users:
        profile = None if args.offline else http.get_json(f"/v0/users/{user}")
        if profile:
            profiles[user] = {
                "username": profile.get("username") or user,
                "nickname": profile.get("nickname") or "",
            }
            save_profiles(profiles)
        nickname = profiles.get(user, {}).get("nickname", "")
        merged: dict[int, dict] = {}
        pages = added = updated = 0
        errors: list[str] = []
        for type_id, status_name in statuses:
            if args.offline:
                snapshot = store.load(user, type_id)
                items = list((snapshot.get("items") or {}).values())
                if not items:
                    errors.append(f"{status_name}：本地没有这个状态的快照")
            else:
                items, page_stats = store.sync(
                    user, args.subject_type, type_id, full=args.full
                )
                pages += page_stats.pages
                added += page_stats.added
                updated += page_stats.updated
                if page_stats.error:
                    errors.append(f"{status_name}：{page_stats.error}")
            for item in items:
                merged[item["id"]] = item

        stats = SyncStats(
            user=user,
            pages=pages,
            added=added,
            updated=updated,
            total=len(merged),
            incremental=not args.full,
            error="；".join(errors),
            nickname=nickname,
        )
        ordered = sorted(
            merged.values(), key=lambda item: item.get("updated_at", ""), reverse=True
        )
        results[user] = (ordered, stats)
    return results, http


def load_from_sheet(args) -> tuple[dict, Http]:
    rows = read_collection(args.input)
    items = []
    for row in rows:
        match = SUBJECT_ID_RE.search(row.get("url") or "")
        items.append(
            {
                "id": int(match.group(1)) if match else 0,
                "name": row.get("name") or "",
                "jp_name": row.get("alias") or "",
                "comment": row.get("comment") or "",
                "status": row.get("status") or "",
                "updated_at": "",
            }
        )
    stats = SyncStats(user=args.input.stem, total=len(items), incremental=False)
    return {args.input.stem: (items, stats)}, Http(rate=args.rate, delay=args.delay)


# ---------------------------------------------------------------- 转词库


def filter_status(items, statuses):
    """按中文状态名过滤；五种全要就不筛。"""
    wanted = {name for _, name in statuses}
    if len(wanted) >= len(STATUS_NAMES):
        return items
    return [item for item in items if (item.get("status") or "") in wanted]


def prefetch_subjects(items, args, subjects: SubjectStore) -> int:
    """并发把缺的条目详情抓回来；本地已有的直接跳过。返回抓取个数。"""
    if args.offline:
        return 0

    seen: set[int] = set()
    ids: list[int] = []
    for item in items:
        sid = item.get("id")
        if sid and sid not in seen:
            seen.add(sid)
            ids.append(int(sid))

    missing = [sid for sid in ids if not subjects.has(sid)]
    if not missing:
        return 0

    print(
        f"  条目详情缺 {len(missing)} 个（已有 {len(ids) - len(missing)} 个缓存），"
        f"{args.workers} 线程并发，全局限速 {args.rate:g} 次/秒",
        flush=True,
    )
    started = time.monotonic()
    done = 0
    pool = ThreadPoolExecutor(max_workers=max(1, args.workers))
    try:
        futures = [pool.submit(subjects.get, sid) for sid in missing]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:  # 单个条目失败不该把整批带崩
                print(f"    抓取异常，已跳过：{type(exc).__name__}: {exc}", flush=True)
            done += 1
            if args.progress_every and (done % args.progress_every == 0 or done == len(missing)):
                elapsed = time.monotonic() - started
                speed = done / elapsed if elapsed > 0 else 0
                remaining = (len(missing) - done) / speed if speed > 0 else 0
                print(
                    f"    [{done:>4}/{len(missing)}] {done * 100 / len(missing):5.1f}%"
                    f" · {speed:.1f} 条/秒 · 用时 {format_duration(elapsed)}"
                    f" · 剩 ~{format_duration(remaining)}",
                    flush=True,
                )
    finally:
        pool.shutdown(wait=True)
    return len(missing)


def build_for_user(label, items, args, subjects: SubjectStore):
    lines: list[str] = []
    no_alias: list[str] = []
    alias_counts: list[int] = []
    renamed: list[tuple[str, str]] = []
    no_id: list[str] = []
    total = len(items)
    started = time.monotonic()
    interrupted = False

    try:
        for index, item in enumerate(items, 1):
            name = item.get("name") or item.get("jp_name") or ""
            subject = subjects.peek(item["id"]) if item.get("id") else None
            if item.get("id") and subject is None:
                no_id.append(name)

            line = build_line(
                name,
                item.get("jp_name") or "",
                item.get("comment") or "",
                subject,
                args.dash,
                not args.no_hint,
                args.hint_sep,
            )
            if not line:
                no_alias.append(name or f"第 {index} 条")
            else:
                tokens = line.split("$", 1)[0].split(",")
                alias_counts.append(len(tokens))
                if len(tokens) < 2:
                    no_alias.append(name)
                clean_name = sanitize(name, args.dash)
                if clean_name and clean_name != name:
                    renamed.append((name, clean_name))
                lines.append(line)

            if args.progress_every and (index % args.progress_every == 0 or index == total):
                report_progress(index, total, name, started, len(lines), subjects)
    except KeyboardInterrupt:
        interrupted = True
        print("\n收到中断，正在写出已完成的部分…", flush=True)

    return lines, no_alias, alias_counts, renamed, no_id, interrupted


def report_progress(index, total, name, started, written, subjects):
    elapsed = time.monotonic() - started
    rate = index / elapsed if elapsed > 0 else 0
    remaining = (total - index) / rate if rate > 0 else 0
    print(
        f"  [{index:>4}/{total}] {index * 100 / total:5.1f}%"
        f" · 写出 {written}"
        f" · 别名缓存 {subjects.hits} 请求 {subjects.fetches}"
        f" · 用时 {format_duration(elapsed)} 剩 ~{format_duration(remaining)}"
        f" · {name[:18]}",
        flush=True,
    )


def format_duration(seconds: float) -> str:
    total = int(seconds)
    if total >= 3600:
        return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"
    if total >= 60:
        return f"{total // 60}:{total % 60:02d}"
    return f"{total}s"


# ---------------------------------------------------------------- 产出


def write_report(path: Path, args, export_date: str, results: dict, checks: dict):
    out = [
        "# 词库导出报告",
        "",
        f"- 导出日期：{export_date}",
        f"- 标记状态：{args.status}",
        "- 词库文件：`bin/exports/<用户名>-<日期>.txt`",
        "",
    ]
    for user, payload in results.items():
        stats: SyncStats = payload["stats"]
        title = f"{user}（{stats.nickname}）" if stats.nickname else user
        out += [
            f"## {title}",
            "",
            f"- 收藏条数：{stats.total}（本次新增 {stats.added}，变动 {stats.updated}）",
            f"- 收藏列表请求：{stats.pages} 页" + ("（增量）" if stats.incremental else "（全量）"),
            f"- 写出词条：{payload['lines']} 条，平均 {payload['average']:.2f} 个别名",
            f"- 别名缓存命中 {payload['hits']}，新取 {payload['fetches']}",
            f"- 失败 {payload['failures']} 条，没补到别名的 {len(payload['no_alias'])} 条",
        ]
        if stats.error:
            out.append(f"- 列表同步：{stats.error}")
        if payload["no_alias"]:
            out += ["", "  没补到别名：", ""]
            out += [f"  - {name}" for name in payload["no_alias"][:10]]
        if payload["failures_detail"]:
            out += ["", "  接口失败：", ""]
            seen: set[str] = set()
            for url, reason in payload["failures_detail"]:
                if url in seen:
                    continue
                seen.add(url)
                out.append(f"  - {url}：{reason}")
                if len(seen) >= 10:
                    break
        issues = checks.get(user) or []
        if issues:
            out += ["", f"  格式自检 {len(issues)} 处：", ""]
            out += [f"  - {issue}" for issue in issues[:10]]
        out.append("")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    args = parse_args(argv)
    export_date = args.date or date_cls.today().isoformat()
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.input and not args.input.exists():
        print(f"找不到表格：{args.input}", file=sys.stderr)
        return 2
    if not args.input and not args.users:
        print(
            "没看到用户列表。在 local.json 里写 {\"users\": [\"你的bgm用户名\"]}，"
            "或者用 --user / --input 指定。",
            file=sys.stderr,
        )
        return 2

    try:
        statuses = parse_statuses(args.status)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    try:
        if args.input:
            sources, http = load_from_sheet(args)
        else:
            sources, http = load_from_users(args, statuses)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    subjects = SubjectStore(DATA_DIR / "subjects", http, offline=args.offline)
    results = {}
    checks = {}
    exit_code = 0

    for user, (items, stats) in sources.items():
        items = filter_status(items, statuses)
        if args.limit:
            items = items[: args.limit]

        label = f"{user}（{stats.nickname}）" if stats.nickname else user
        print(f"\n=== {label} ===", flush=True)
        print(
            f"收藏 {stats.total} 条（{'/'.join(name for _, name in statuses)}）"
            f"，本次：新增 {stats.added} / 变动 {stats.updated}，列表请求 {stats.pages} 页",
            flush=True,
        )
        if stats.error:
            print(f"  ! {stats.error}", flush=True)

        before_hits, before_fetches = subjects.hits, subjects.fetches
        before_failures = len(http.failures)
        prefetch_interrupted = False
        try:
            prefetch_subjects(items, args, subjects)
        except KeyboardInterrupt:
            prefetch_interrupted = True
            print("\n收到中断，先写已抓到的部分…", flush=True)

        lines, no_alias, alias_counts, renamed, no_id, interrupted = build_for_user(
            user, items, args, subjects
        )
        if interrupted or prefetch_interrupted:
            exit_code = 130

        target = EXPORTS_DIR / f"{safe_name(stats.nickname, user)}-{export_date}.txt"
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")

        extra = sum(alias_counts) - len(alias_counts)
        average = extra / len(alias_counts) if alias_counts else 0
        issues = check_lines(lines, args.dash) if args.check else []
        checks[user] = issues

        results[user] = {
            "stats": stats,
            "lines": len(lines),
            "average": average,
            "hits": subjects.hits - before_hits,
            "fetches": subjects.fetches - before_fetches,
            "failures": len(http.failures) - before_failures,
            "failures_detail": http.failures[before_failures:],
            "no_alias": no_alias,
            "renamed": renamed,
            "no_id": no_id,
        }

        print(
            f"写出 {len(lines)} 条 -> {target}\n"
            f"别名缓存命中 {results[user]['hits']}，新取 {results[user]['fetches']}，"
            f"失败 {results[user]['failures']}；每行平均 {average:.2f} 个别名",
            flush=True,
        )
        if args.check:
            print(
                "格式自检：" + ("没发现问题" if not issues else f"{len(issues)} 处问题，见报告"),
                flush=True,
            )

    report_path = EXPORTS_DIR / f"report-{export_date}.md"
    write_report(report_path, args, export_date, results, checks)
    print(f"\n报告 -> {report_path}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

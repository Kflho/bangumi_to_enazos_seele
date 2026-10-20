#!/usr/bin/env python3
"""bangumi_to_enazos_seele：把 Bangumi 标记列表转成 enazo 词库。

输入是 Bangumi 收藏导出表（gm_scripts 的导出脚本产出的 xlsx / csv），
逐个条目调 Bangumi 接口把别名补齐，输出 enazo 的批量录入文本：

    词条名,别名一,别名二$小狐狸提示

用法：
    python bangumi_to_enazo.py --input in/collection.xlsx
    python bangumi_to_enazo.py --offline            # 只用缓存，不联网
    python bangumi_to_enazo.py --status 看过,在看    # 只导出这几类标记
    python bangumi_to_enazo.py --limit 20           # 先跑 20 行看格式

接口直连会超时，得挂代理。命中缓存后重跑不发请求。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from bangumi.client import BangumiClient
from bangumi.sheet import read_collection
from enazo.bank import DASH_MODES, build_line, check_lines, sanitize

ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "in" / "collection.xlsx"
DEFAULT_OUTPUT = ROOT / "out" / "bank.txt"
DEFAULT_REPORT = ROOT / "out" / "report.md"
DEFAULT_CACHE = ROOT / "cache" / "subjects"

SUBJECT_ID_RE = re.compile(r"/subject/(\d+)")
STATUS_NAMES = ("想看", "看过", "在看", "搁置", "抛弃")


# ---------------------------------------------------------------- 参数


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="把 Bangumi 标记列表转成 enazo 词库（一行一条）。"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="收藏导出表（xlsx / csv）")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="输出的词库文件")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="核对报告")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="条目缓存目录")
    parser.add_argument("--delay", type=float, default=0.5, help="两次请求之间的间隔秒数")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 行，用于试水")
    parser.add_argument("--offline", action="store_true", help="只用缓存，不发请求")
    parser.add_argument(
        "--status",
        default="",
        help=f"只导出这些观看状态，逗号分隔（{'/'.join(STATUS_NAMES)}）；留空为全部",
    )
    parser.add_argument("--no-hint", action="store_true", help="不输出 $ 后面的小狐狸提示")
    parser.add_argument(
        "--dash",
        choices=sorted(DASH_MODES),
        default="all",
        help="；".join(f"{k}={v}" for k, v in DASH_MODES.items()),
    )
    parser.add_argument(
        "--progress-every", type=int, default=10, help="每处理 N 行打一条进度，0 表示不打"
    )
    parser.add_argument("--check", action="store_true", help="写完后体检输出格式")
    return parser.parse_args(argv)


# ---------------------------------------------------------------- 主流程


def filter_by_status(rows, wanted: str, limit: int):
    if wanted:
        wanted_set = {part.strip() for part in wanted.replace("，", ",").split(",") if part.strip()}
        unknown = wanted_set - set(STATUS_NAMES)
        if unknown:
            raise ValueError(f"不认识的观看状态：{'、'.join(sorted(unknown))}")
        rows = [row for row in rows if (row.get("status") or "") in wanted_set]
    if limit:
        rows = rows[:limit]
    return rows


def collect(rows, client: BangumiClient, dash_mode: str, with_hint: bool, every: int):
    lines: list[str] = []
    no_id: list[str] = []
    no_alias: list[str] = []
    alias_counts: list[int] = []
    renamed: list[tuple[str, str]] = []
    total = len(rows)
    started = time.monotonic()
    interrupted = False

    try:
        for index, row in enumerate(rows, 1):
            name = row.get("name") or ""
            jp_name = row.get("alias") or ""
            hint = row.get("comment") or ""
            match = SUBJECT_ID_RE.search(row.get("url") or "")

            subject = None
            if match:
                subject = client.get(int(match.group(1)))
            else:
                no_id.append(name)

            line = build_line(name, jp_name, hint, subject, dash_mode, with_hint)
            if not line:
                no_alias.append(name or f"第 {index} 行")
            else:
                tokens = line.split("$", 1)[0].split(",")
                alias_counts.append(len(tokens))
                if len(tokens) < 2:
                    no_alias.append(name)
                clean_name = sanitize(name, dash_mode)
                if clean_name and clean_name != name:
                    renamed.append((name, clean_name))
                lines.append(line)

            if every and (index % every == 0 or index == total):
                report_progress(index, total, name, started, len(lines), client)
    except KeyboardInterrupt:
        interrupted = True
        print("\n收到中断，正在写出已完成的部分…", flush=True)

    return lines, no_id, no_alias, alias_counts, renamed, interrupted


def report_progress(index, total, name, started, written, client):
    elapsed = time.monotonic() - started
    rate = index / elapsed if elapsed > 0 else 0
    remaining = (total - index) / rate if rate > 0 else 0
    print(
        f"  [{index:>4}/{total}] {index * 100 / total:5.1f}%"
        f" · 写出 {written}"
        f" · 缓存 {client.hits} 请求 {client.fetches} 失败 {len(client.failures)}"
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


# ---------------------------------------------------------------- 报告


def write_report(
    args, rows, lines, client, no_id, no_alias, alias_counts, average, renamed, issues
):
    args.report.parent.mkdir(parents=True, exist_ok=True)
    extra = sum(alias_counts) - len(alias_counts)
    out = [
        "# 词库生成报告",
        "",
        f"- 输入：`{args.input.name}`",
        f"- 输出：`{args.output.name}`（{len(lines)} 条）",
        f"- 表格数据行：{len(rows)}",
        f"- 接口命中缓存 {client.hits}，新请求 {client.fetches}，"
        f"被限流 {client.throttled} 次，失败 {len(client.failures)}",
        f"- 每行平均别名数：{average:.2f}（不含词条名本身，合计 {extra} 个）",
        f"- 清洗档位：`--dash {args.dash}`（{DASH_MODES[args.dash]}）",
        "",
    ]

    if renamed:
        out += [f"## 被换成 - 的词条名（{len(renamed)} 条，前 15 条）", ""]
        out += [f"- `{before}` → `{after}`" for before, after in renamed[:15]]
        out.append("")

    if no_alias:
        out += [f"## 没补到额外别名的词条（{len(no_alias)} 条，前 15 条）", ""]
        out += [f"- {name}" for name in no_alias[:15]]
        out.append("")

    if no_id:
        out += [f"## 地址列没有 subject id（{len(no_id)} 条）", ""]
        out += [f"- {name}" for name in no_id[:15]]
        out.append("")

    if client.failures:
        out += [f"## 接口失败（{len(client.failures)} 条）", ""]
        out += [f"- `{sid}`：{reason}" for sid, reason in client.failures[:30]]
        out.append("")

    if issues:
        out += [f"## 格式自检（{len(issues)} 处）", ""]
        out += [f"- {issue}" for issue in issues[:30]]
        out.append("")

    if lines:
        out += ["## 输出样式抽样", "", "```text"]
        out += lines[:3]
        out += ["```", ""]

    args.report.write_text("\n".join(out) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 入口


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        rows = read_collection(args.input)
        rows = filter_by_status(rows, args.status, args.limit)
    except (FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2

    if not rows:
        print("表格里没有可导出的行（检查 --status 过滤条件）", file=sys.stderr)
        return 2

    client = BangumiClient(args.cache, args.delay, args.offline)
    cached = len(list(args.cache.glob("*.json"))) if args.cache.exists() else 0
    print(f"共 {len(rows)} 行；本地已有 {cached} 条缓存，缺的自动去请求", flush=True)
    print(
        f"清洗档位 --dash {args.dash}（{DASH_MODES[args.dash]}），请求间隔 {args.delay}s；"
        "中途 Ctrl+C 会写出已完成的部分，重跑接着来",
        flush=True,
    )

    lines, no_id, no_alias, alias_counts, renamed, interrupted = collect(
        rows, client, args.dash, not args.no_hint, args.progress_every
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")

    extra = sum(alias_counts) - len(alias_counts)
    average = extra / len(alias_counts) if alias_counts else 0
    issues = check_lines(lines, args.dash) if args.check else []
    write_report(
        args, rows, lines, client, no_id, no_alias, alias_counts, average, renamed, issues
    )

    print()
    print(f"写出 {len(lines)} 条 -> {args.output}", flush=True)
    print(
        f"命中缓存 {client.hits}，新请求 {client.fetches}，"
        f"被限流 {client.throttled} 次，失败 {len(client.failures)}",
        flush=True,
    )
    print(f"每行平均 {average:.2f} 个别名；只有词条名的 {len(no_alias)} 条", flush=True)
    if args.check:
        if issues:
            print(f"格式自检：{len(issues)} 处问题", flush=True)
            for issue in issues[:10]:
                print(f"  - {issue}", flush=True)
            if len(issues) > 10:
                print(f"  ……其余 {len(issues) - 10} 处见报告", flush=True)
        else:
            print("格式自检：没发现问题", flush=True)
    if interrupted:
        print("这次被中断了，写出的是已完成的部分；原样重跑会用缓存补完。", flush=True)
    if client.failures:
        print("失败的 id 记在报告里了。", flush=True)
    print(f"报告 -> {args.report}", flush=True)
    return 130 if interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())

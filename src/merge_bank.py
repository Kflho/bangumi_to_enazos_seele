#!/usr/bin/env python3
"""合并多个用户的词库。

按 Bangumi subject id 对齐：同一个条目只出现一次，别名取各来源的并集，
小狐狸提示则是各人评论并列（用逗号分隔，题库标准写法）。

数据全部来自本地快照和缓存，不会联网：

    bin/data/collections/<user>-t<type>.json   收藏快照
    bin/data/subjects/<id>.json                条目详情

用法：

    python src/merge_bank.py --users 469325 869334 chiotto 723737
    python src/merge_bank.py --users a b --date 2026-10-20 --dash seps

产出：

    bin/exports/<题库名>-<日期>/<题库名>-<日期>.txt
    bin/exports/<题库名>-<日期>/report.md

题库名默认取 local.json 的 merge_title，也可以用 --title 指定。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date as date_cls
from pathlib import Path

from enazo.bank import DASH_MODES, build_tokens, check_lines, clean_hint

ROOT = Path(__file__).resolve().parent.parent  # 代码在 src/ 下，项目根是上一层
DATA_DIR = ROOT / "bin" / "data"
EXPORTS_DIR = ROOT / "bin" / "exports"
LOCAL_JSON = ROOT / "local.json"

TYPE_NAME = "看过"


def load_config() -> dict:
    if not LOCAL_JSON.exists():
        return {}
    try:
        return json.loads(LOCAL_JSON.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def parse_args(argv=None):
    config = load_config()
    parser = argparse.ArgumentParser(description="按 subject id 合并多个用户的词库。")
    parser.add_argument(
        "--users",
        nargs="+",
        default=config.get("merge_users"),
        help="要合并的用户名，按顺序排在前面；默认取 local.json 的 merge_users",
    )
    parser.add_argument("--collection-type", type=int, default=2, help="收藏类型，默认 2=看过")
    parser.add_argument("--date", default="", help="目录里的日期，默认今天")
    parser.add_argument(
        "--title",
        default=config.get("merge_title", "merged"),
        help="题库名，默认取 local.json 的 merge_title，没有就用 merged",
    )
    parser.add_argument("--dash", choices=sorted(DASH_MODES), default="all", help="清洗档位")
    parser.add_argument("--hint-sep", default=",", help="多条评论之间的分隔符，默认逗号")
    return parser.parse_args(argv)


# ---------------------------------------------------------------- 读本地数据


def load_snapshot(user: str, collection_type: int) -> dict:
    path = DATA_DIR / "collections" / f"{user}-t{collection_type}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{user} 没有本地快照（{path.name}），先跑一次 bangumi_to_enazo.py --user {user}"
        )
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_subject(sid: int, cache: dict) -> dict | None:
    if sid in cache:
        return cache[sid]
    path = DATA_DIR / "subjects" / f"{sid}.json"
    data = None
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            data = None
    cache[sid] = data
    return data


# ---------------------------------------------------------------- 合并


def merge_users(users, snapshots, dash_mode, hint_sep):
    """返回 (按顺序的条目列表, 每个用户的 id 集合)。"""
    merged: dict[int, dict] = {}
    order: list[int] = []
    ids_by_user: dict[str, set[int]] = {}
    subject_cache: dict[int, dict | None] = {}

    for user in users:
        items = (snapshots[user].get("items") or {}).values()
        ordered = sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)
        ids_by_user[user] = {int(item["id"]) for item in ordered if item.get("id")}

        for item in ordered:
            sid = item.get("id")
            if not sid:
                continue
            sid = int(sid)
            subject = load_subject(sid, subject_cache)
            tokens = build_tokens(
                item.get("name") or item.get("jp_name") or "",
                item.get("jp_name") or "",
                subject,
                dash_mode,
            )
            if not tokens:
                continue

            entry = merged.get(sid)
            if entry is None:
                entry = {"tokens": [], "keys": set(), "hints": [], "sources": []}
                merged[sid] = entry
                order.append(sid)

            for token in tokens:
                key = token.casefold()
                if key not in entry["keys"]:
                    entry["keys"].add(key)
                    entry["tokens"].append(token)

            hint = clean_hint(item.get("comment") or "", hint_sep)
            if hint and hint not in entry["hints"]:
                entry["hints"].append(hint)
            if user not in entry["sources"]:
                entry["sources"].append(user)

    return [(sid, merged[sid]) for sid in order], ids_by_user


def render(bank, hint_sep: str) -> list[str]:
    lines = []
    for _, entry in bank:
        line = ",".join(entry["tokens"])
        if entry["hints"]:
            line += "$" + hint_sep.join(entry["hints"])
        lines.append(line)
    return lines


# ---------------------------------------------------------------- 校验


def verify(lines, bank, ids_by_user) -> tuple[list[str], list[str]]:
    """返回 (错误, 提醒)。同名词条多半是不同作品，只提醒不算错。"""
    problems: list[str] = []

    for user, ids in ids_by_user.items():
        missing = ids - {sid for sid, _ in bank}
        if missing:
            sample = "、".join(str(sid) for sid in list(missing)[:5])
            problems.append(f"{user} 有 {len(missing)} 条没进合并结果：{sample}")

    names: dict[str, int] = {}
    for _, entry in bank:
        first = entry["tokens"][0].casefold()
        names[first] = names.get(first, 0) + 1
    duplicated = {name: count for name, count in names.items() if count > 1}
    warnings = []
    if duplicated:
        sample = "、".join(f"{name}×{count}" for name, count in list(duplicated.items())[:8])
        warnings.append(
            f"{len(duplicated)} 个词条名对应多个条目（多半是不同季/不同版本）：{sample}"
        )

    return problems, warnings


def write_report(path: Path, args, users, lines, bank, ids_by_user, problems, warnings, issues):
    alias_total = sum(len(entry["tokens"]) - 1 for _, entry in bank)
    average = alias_total / len(bank) if bank else 0
    out = [
        "# 合并题库报告",
        "",
        f"- 题库名：{args.title}",
        f"- 合并日期：{args.date}",
        f"- 成员：{'、'.join(users)}",
        f"- 收藏类型：{TYPE_NAME}",
        f"- 清洗档位：`--dash {args.dash}`",
        f"- 合并后词条：{len(lines)} 条",
        f"- 别名合计：{alias_total} 个，平均每行 {average:.2f} 个",
        "",
        "## 各用户贡献",
        "",
        "| 用户 | 收藏条数 | 唯一贡献 |",
        "|---|---|---|",
    ]
    for user in users:
        before = {sid for sid, entry in bank if user in entry["sources"]}
        others = {sid for sid, entry in bank if user in entry["sources"] and len(entry["sources"]) == 1}
        out.append(f"| {user} | {len(ids_by_user[user])} | {len(others)}（合共 {len(before)} 条出现） |")
    out.append("")

    out += ["## 校验", ""]
    if problems:
        out += [f"- 错误：{problem}" for problem in problems]
    else:
        out.append("- 每个用户的条目都进了合并结果")
    out += [f"- 提醒：{warning}" for warning in warnings]
    if issues:
        out += ["", f"格式自检 {len(issues)} 处：", ""]
        out += [f"- {issue}" for issue in issues[:10]]
    else:
        out.append("- 格式自检通过（无空行、无多个 `$`、无残留分隔符）")
    out.append("")

    if lines:
        out += ["## 输出抽样", "", "```text"]
        out += lines[:3]
        out += ["```", ""]

    path.write_text("\n".join(out) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 入口


def main(argv=None) -> int:
    args = parse_args(argv)
    args.date = args.date or date_cls.today().isoformat()
    if not args.users:
        print("没指定要合并谁：用 --users，或者在 local.json 里写 merge_users", file=sys.stderr)
        return 2
    users = list(dict.fromkeys(args.users))

    try:
        snapshots = {user: load_snapshot(user, args.collection_type) for user in users}
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2

    bank, ids_by_user = merge_users(users, snapshots, args.dash, args.hint_sep)
    lines = render(bank, args.hint_sep)
    if not lines:
        print("没有可合并的条目", file=sys.stderr)
        return 2

    out_dir = EXPORTS_DIR / f"{args.title}-{args.date}"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{args.title}-{args.date}.txt"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    problems, warnings = verify(lines, bank, ids_by_user)
    issues = check_lines(lines, args.dash)
    write_report(
        out_dir / "report.md", args, users, lines, bank, ids_by_user, problems, warnings, issues
    )

    print(f"合并 {len(users)} 个用户：{'、'.join(users)}")
    for user in users:
        print(f"  {user}: 收藏 {len(ids_by_user[user])} 条")
    print(f"合并后 {len(lines)} 条，平均每行 {(sum(len(e['tokens']) - 1 for _, e in bank) / len(bank)):.2f} 个别名")
    print(f"产出 -> {target}")
    print(f"报告 -> {out_dir / 'report.md'}")
    if problems:
        print("校验发现问题：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
    for warning in warnings:
        print(f"提醒：{warning}")
    if issues:
        print(f"格式自检 {len(issues)} 处问题（见报告）", file=sys.stderr)
    return 0 if not problems and not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())

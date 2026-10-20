#!/usr/bin/env python3
"""对已有的题库 txt 做一遍优化：补去子标题、去季度、纯文本、最短包含四类别名。

    python src/optimize_bank.py --input 题库.txt
    python src/optimize_bank.py --input 题库.txt --output 优化后.txt
    python src/optimize_bank.py --input 题库.txt --dry-run   # 只看会加多少

输入输出都是 enazo 的一行一条格式：`词条名,别名一,别名二$小狐狸提示`。
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from enazo.optimize import dedupe_by_name, find_conflicts, optimize_entries

ROOT = Path(__file__).resolve().parent.parent


def parse_bank(path: Path) -> tuple[list[dict], list[str]]:
    entries: list[dict] = []
    raw_lines: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        raw_lines.append(line)
        head, _, hint = line.partition("$")
        parts = [part.strip() for part in head.split(",")]
        entries.append(
            {
                "name": parts[0] if parts else "",
                "alias": [part for part in parts[1:] if part],
                "hint": hint,
            }
        )
    return entries, raw_lines


def render(entries: list[dict]) -> list[str]:
    lines = []
    for entry in entries:
        head = ",".join([entry["name"]] + list(entry.get("alias") or []))
        hint = entry.get("hint") or ""
        lines.append(f"{head}${hint}" if hint else head)
    return lines


def write_report(path: Path, source: Path, target: Path, stats: Counter, samples, conflicts, total, merged):
    added = sum(stats.values())
    out = [
        "# 题库优化报告",
        "",
        f"- 输入：`{source.name}`",
        f"- 输出：`{target.name}`",
        f"- 词条：{total} 条",
        f"- 新增别名：{added} 个，平均每条 {added / total:.2f} 个" if total else "- 新增别名：0",
        "",
        "## 各规则的产出",
        "",
        "| 规则 | 新增 |",
        "|---|---|",
    ]
    for rule, count in stats.most_common():
        out.append(f"| {rule} | {count} |")
    out.append("")

    if samples:
        out += ["## 抽样", ""]
        for name, alias, rule in samples:
            out.append(f"- `{name}` 加 `{alias}`（{rule}）")
        out.append("")

    if merged:
        out += ["## 同名词条合并", "", f"合并掉 {len(merged)} 条：", ""]
        out += [f"- `{name}`" for name in merged[:20]]
        out.append("")

    out += ["## 歧义别名", ""]
    if conflicts:
        out.append(f"有 {len(conflicts)} 个别名同时挂在多个词条上，猜中时可能算到别的条目：")
        out.append("")
        out += [f"- `{alias}`" for alias in conflicts[:20]]
    else:
        out.append("没有歧义别名。")
    out.append("")

    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="给题库补几类容易猜的别名。")
    parser.add_argument("--input", type=Path, required=True, help="要优化的题库 txt")
    parser.add_argument("--output", type=Path, help="输出路径，默认在原文件名后加 -optimized")
    parser.add_argument("--report", type=Path, help="报告路径，默认和输出放一起")
    parser.add_argument("--min-substring", type=int, default=3, help="最短包含别名的最小长度")
    parser.add_argument("--min-alias", type=int, default=3, help="补出来的别名最短几字，挡掉 Re 这种碎片")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"找不到题库：{args.input}", file=sys.stderr)
        return 2

    entries, _ = parse_bank(args.input)
    if not entries:
        print("题库是空的", file=sys.stderr)
        return 2

    before = len(entries)
    entries, merged = dedupe_by_name(entries)
    if merged:
        print(f"同名词条合并 {before - len(entries)} 条：{'、'.join(merged[:8])}")

    stats, samples = optimize_entries(entries, args.min_substring, args.min_alias)
    conflicts = find_conflicts(entries)
    lines = render(entries)

    total = len(entries)
    added = sum(stats.values())
    print(f"词条 {total} 条，新增别名 {added} 个（平均 {added / total:.2f} 个/条）")
    for rule, count in stats.most_common():
        print(f"  {rule}: {count}")
    if conflicts:
        print(f"歧义别名 {len(conflicts)} 个（同一个挂多个词条）")

    if args.dry_run:
        print("--dry-run，没有写文件")
        return 0

    target = args.output or args.input.with_name(args.input.stem + "-optimized.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"写出 -> {target}")

    report = args.report or target.with_name(target.stem + "-report.md")
    write_report(report, args.input, target, stats, samples, conflicts, total, merged)
    print(f"报告 -> {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

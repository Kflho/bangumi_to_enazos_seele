"""题库优化：给词条补几类更容易猜中的别名。

四类补法，都在原别名后面追加，不删原来的：

1. **去季度**：`无职转生 第三季` → `无职转生`；
2. **去子标题**：`攻壳机动队：无罪` → `攻壳机动队`；行尾括号和 `～…～` 也一并去掉；
3. **纯文本**：去掉标点，`Re:从零开始的异世界生活` → `Re从零开始的异世界生活`；
4. **最短包含**：在整本题库里找「被这个词条名包含」的最短条目名，
   比如库里有 `攻壳机动队`，`攻壳机动队 S.A.C. 2nd GIG` 就能拿到它当别名。

补出来的别名会去重（忽略大小写），也不会和词条名本身重复。
"""

from __future__ import annotations

import re
from collections import Counter

# 季度标记
SEASON_PATTERNS = [
    re.compile(r"第\s*[一二三四五六七八九十百零两0-9]+\s*[季期部]"),
    re.compile(r"Season\s*[0-9]+", re.IGNORECASE),
    re.compile(r"[0-9]+\s*(?:st|nd|rd|th)\s*Season", re.IGNORECASE),
    re.compile(r"[（(]\s*第\s*[0-9一二三四五六七八九十]+\s*[季期]\s*[）)]"),
]
# 子标题分隔符（冒号类）
SUBTITLE_SPLIT = re.compile(r"[：:]")
# 行尾的 ~副标题~ 和 (xxx)，允许前面带一个清洗用的短横
TILDE_TAIL = re.compile(r"[\s\-]*[～~][^～~]*[～~]\s*$")
BRACKET_TAIL = re.compile(r"[\s\-]*[（(【\[][^（）()【\[\]]*[）)】\]]\s*$")
# 纯文本要去掉的标点
PUNCT_CHARS = (
    "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
    "！？。，、；：…—～·「」『』（）【】《》〈〉“”‘’"
    "★☆♡♥♪／＼｜"
)
PUNCT_RE = re.compile("[" + re.escape(PUNCT_CHARS) + "]")
SPACE_RE = re.compile(r"\s+")
# 标题里的空格和标点早先被换成了短横，删词之后会留下连续或边缘的短横
DASH_RUN = re.compile(r"-{2,}")

MIN_SUBSTRING = 3
MIN_ALIAS = 3


def _tidy(text: str) -> str:
    """收拾删词留下的空白和连续短横。"""
    text = SPACE_RE.sub(" ", text or "")
    text = DASH_RUN.sub("-", text)
    return text.strip(" -")


def season_base(name: str) -> str:
    """去掉「第 X 季 / Season X」这类季度标记。"""
    text = name or ""
    for pattern in SEASON_PATTERNS:
        text = pattern.sub("", text)
    return _tidy(text)


def subtitle_base(name: str) -> str:
    """去掉冒号后面的子标题、行尾括号和 ~副标题~。"""
    text = (name or "").split("：")[0]
    text = text.split(":")[0]
    text = TILDE_TAIL.sub("", text)
    text = BRACKET_TAIL.sub("", text)
    return _tidy(text)


def plain_text(name: str) -> str:
    """去掉标点，留一个方便手打的纯文本形式。"""
    return _tidy(PUNCT_RE.sub("", name or ""))


def shortest_contained(name: str, names: list[str], minimum: int = MIN_SUBSTRING):
    """在整库里找被 name 包含的最短条目名。"""
    best = None
    for other in names:
        if other == name or len(other) < minimum:
            continue
        if other not in name:
            continue
        if best is None or len(other) < len(best):
            best = other
    return best


def candidates_for(
    name: str,
    names: list[str],
    minimum: int = MIN_SUBSTRING,
    min_alias: int = MIN_ALIAS,
):
    """按规则给出 (别名, 规则名) 列表，顺序即追加顺序。"""
    out: list[tuple[str, str]] = []
    base_season = season_base(name)
    base_sub = subtitle_base(name)

    if base_sub and base_sub != name:
        out.append((base_sub, "去子标题"))
    if base_season and base_season != name:
        out.append((base_season, "去季度"))

    # 两种基础形式再叠一层：先去季度再去子标题、以及各自的纯文本
    for text, label in (
        (subtitle_base(base_season), "去季度+去子标题"),
        (season_base(base_sub), "去子标题+去季度"),
    ):
        if text and text != name and text not in {c for c, _ in out}:
            out.append((text, label))

    for text, label in (
        (plain_text(name), "纯文本"),
        (plain_text(base_season), "纯文本+去季度"),
        (plain_text(base_sub), "纯文本+去子标题"),
    ):
        if text and text != name and text not in {c for c, _ in out}:
            out.append((text, label))

    contained = shortest_contained(name, names, minimum)
    if contained:
        out.append((contained, "最短包含"))
        plain_contained = plain_text(contained)
        if plain_contained != contained:
            out.append((plain_contained, "最短包含+纯文本"))

    return [(alias, label) for alias, label in out if len(alias) >= min_alias]


def optimize_entries(
    entries: list[dict],
    minimum: int = MIN_SUBSTRING,
    min_alias: int = MIN_ALIAS,
):
    """给每个词条补别名。entries 里每项要有 name 和 alias 两个键。

    返回 (统计, 样例)；统计按规则名计数，样例取前几条新加的别名。
    """
    names = [entry.get("name") or "" for entry in entries]
    stats: Counter = Counter()
    samples: list[tuple[str, str, str]] = []

    for entry in entries:
        name = entry.get("name") or ""
        if not name:
            continue
        aliases = list(entry.get("alias") or [])
        seen = {alias.casefold() for alias in aliases}
        seen.add(name.casefold())

        for alias, rule in candidates_for(name, names, minimum, min_alias):
            alias = alias.strip()
            if not alias:
                continue
            key = alias.casefold()
            if key in seen:
                continue
            seen.add(key)
            aliases.append(alias)
            stats[rule] += 1
            if len(samples) < 8:
                samples.append((name, alias, rule))

        entry["alias"] = aliases

    return stats, samples


def find_conflicts(entries: list[dict]) -> list[str]:
    """同一个别名挂到多个词条上时列出来，方便人工判断。"""
    owners: dict[str, set[str]] = {}
    for entry in entries:
        name = entry.get("name") or ""
        for alias in entry.get("alias") or []:
            owners.setdefault(alias.casefold(), set()).add(name)
    return [alias for alias, names in owners.items() if len(names) > 1]


def dedupe_by_name(entries: list[dict], sep: str = ","):
    """把同名词条合并成一条，跟页面上的「删除名称重复词条」一个意思。

    保留先出现的那条，别名和提示取并集。entries 里认三个键：
    `name`、`alias`（列表）、以及 `reminds`（列表）或 `hint`（字符串）。

    返回 (合并后的列表, 被合并掉的词条名)。
    """
    kept: list[dict] = []
    index: dict[str, dict] = {}
    merged: list[str] = []

    for entry in entries:
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        first = index.get(name)
        if first is None:
            index[name] = entry
            kept.append(entry)
            continue

        seen = {alias.casefold() for alias in first.get("alias") or []}
        for alias in entry.get("alias") or []:
            key = alias.casefold()
            if key not in seen:
                seen.add(key)
                first.setdefault("alias", []).append(alias)

        if "reminds" in first or "reminds" in entry:
            joined = list(first.get("reminds") or []) + list(entry.get("reminds") or [])
            first["reminds"] = list(dict.fromkeys(part for part in joined if part))

        if "hint" in first or "hint" in entry:
            head = (first.get("hint") or "").strip()
            tail = (entry.get("hint") or "").strip()
            if tail and tail not in head:
                first["hint"] = f"{head}{sep}{tail}" if head else tail

        if "sources" in first or "sources" in entry:
            joined = list(first.get("sources") or []) + list(entry.get("sources") or [])
            first["sources"] = list(dict.fromkeys(joined))

        merged.append(name)

    return kept, merged

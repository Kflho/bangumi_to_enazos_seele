"""enazo 词库行的拼装与清洗。

批量录入格式（详见 README）：

    词条名,别名一,别名二$小狐狸提示

- 一行一条，换行就是词条边界；
- 分隔符是 `,` `，` `、` `;` `；` `/`，出现在名字内部会被当成切口；
- `$` 之后全部是小狐狸提示，不再切分。
"""

from __future__ import annotations

import re

# enazo 认的别名分隔符
SEPARATORS = ",，、;；/"
SEP_RE = re.compile("[" + re.escape(SEPARATORS) + "]+")
SPACE_RE = re.compile(r"\s+")
DASH_RUN = re.compile(r"-{2,}")
NEWLINE_RE = re.compile(r"[\r\n]+")

# infobox 里算「别名」的键，按优先级排
INFOBOX_ALIAS_KEYS = ("中文名", "别名", "英文名", "罗马字")

# --dash 的三种档位
DASH_MODES = {
    "all": "分隔符和空白都换成 -",
    "seps": "只换分隔符，空白保留",
    "none": "都不换，只去换行（字段自带的逗号会切坏词条）",
}


def sanitize(value: str, dash_mode: str = "all") -> str:
    """把会被 enazo 当成切割符的字符换成 -，其余字符原样保留。"""
    text = NEWLINE_RE.sub("", value or "").strip()
    if dash_mode == "none":
        return text
    if dash_mode == "all":
        text = SPACE_RE.sub("-", text)
    text = SEP_RE.sub("-", text)
    text = DASH_RUN.sub("-", text)
    return text.strip("-")


def infobox_aliases(subject: dict) -> list[str]:
    """从条目信息里抠出所有别名写法。"""
    found: list[str] = []
    for box in subject.get("infobox") or []:
        if box.get("key") not in INFOBOX_ALIAS_KEYS:
            continue
        value = box.get("value")
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    if item.get("v"):
                        found.append(str(item["v"]))
                elif isinstance(item, str):
                    found.append(item)
    return found


def build_tokens(
    name: str, jp_name: str, subject: dict | None, dash_mode: str = "all"
) -> list[str]:
    """按「中文名 → 日文原名 → 别名框」排队，去重后返回。"""
    raw: list[str] = [name]
    if subject:
        raw.append(subject.get("name_cn") or "")
    raw.append(jp_name)
    if subject:
        raw.append(subject.get("name") or "")
        raw.extend(infobox_aliases(subject))

    tokens: list[str] = []
    seen: set[str] = set()
    for value in raw:
        token = sanitize(value, dash_mode)
        if not token:
            continue
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        tokens.append(token)
    return tokens


def build_line(
    name: str,
    jp_name: str,
    hint: str,
    subject: dict | None,
    dash_mode: str = "all",
    with_hint: bool = True,
) -> str:
    """拼一整行；词条名为空或全被清洗掉时返回空串。"""
    tokens = build_tokens(name, jp_name, subject, dash_mode)
    if not tokens:
        return ""
    line = ",".join(tokens)
    if with_hint:
        cleaned = NEWLINE_RE.sub("", hint or "").strip()
        if cleaned:
            line += "$" + cleaned
    return line


def check_lines(lines: list[str], dash_mode: str = "all") -> list[str]:
    """体检生成结果，返回问题描述；空列表表示没问题。

    查的是格式层面能自动判定的事：空行、多半截词条名、多个 `$`、
    清洗后仍然残留的分隔符。猜题体验好坏不在此列。
    """
    issues: list[str] = []
    for number, line in enumerate(lines, 1):
        text = line.strip()
        if not text:
            issues.append(f"第 {number} 行是空行")
            continue
        if text.count("$") > 1:
            issues.append(f"第 {number} 行有多个 $：{text[:40]}")
        head = text.split("$", 1)[0]
        tokens = head.split(",")
        if not tokens[0].strip():
            issues.append(f"第 {number} 行没有词条名：{text[:40]}")
        if dash_mode != "none":
            for token in tokens:
                if SEP_RE.search(token):
                    issues.append(f"第 {number} 行残留分隔符：{token[:40]}")
                    break
        if dash_mode == "all":
            for token in tokens:
                if SPACE_RE.search(token):
                    issues.append(f"第 {number} 行残留空白：{token[:40]}")
                    break
    return issues

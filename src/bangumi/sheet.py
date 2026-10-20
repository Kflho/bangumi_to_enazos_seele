"""读 Bangumi 收藏导出表。

表格由 gm_scripts 的「Bangumi 收藏导出」脚本产出，xlsx 和 csv 都能导。
列名按官方写死的几个中文名认，同时兼容表头被改成英文的情况。
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

# 规范键 -> 认得的所有表头写法（第一个是导出脚本用的那个）
COLUMN_ALIASES = {
    "name": ("名称", "name", "title"),
    "alias": ("别名", "greyName", "subtitle"),
    "url": ("地址", "url", "subjectUrl"),
    "comment": ("吐槽", "comment"),
    "status": ("观看状态", "类别", "interestType"),
}

REQUIRED = ("name", "url")

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
TEXT = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"


# ------------------------------------------------------------------ xlsx


def _column_index(ref: str) -> int:
    """A1 / AB12 -> 0 起列号。"""
    letters = re.match(r"[A-Z]+", ref).group(0)
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    return index - 1


def _first_sheet_path(z: zipfile.ZipFile) -> str:
    workbook = ET.fromstring(z.read("xl/workbook.xml"))
    sheet = workbook.find("m:sheets/m:sheet", NS)
    if sheet is None:
        raise ValueError("工作簿里没有工作表")
    rel_id = sheet.attrib.get(f"{{{NS['r']}}}id")
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    for rel in rels.findall("rel:Relationship", NS):
        if rel.attrib.get("Id") == rel_id:
            target = rel.attrib.get("Target", "").lstrip("/")
            return target if target.startswith("xl/") else "xl/" + target
    raise ValueError("找不到工作表文件")


def _cell_text(cell, shared: list[str]) -> str:
    kind = cell.attrib.get("t")
    if kind == "s":
        value = cell.find("m:v", NS)
        if value is None or value.text is None:
            return ""
        try:
            return shared[int(value.text)]
        except (ValueError, IndexError):
            return ""
    if kind == "inlineStr":
        node = cell.find("m:is", NS)
        return "" if node is None else "".join(t.text or "" for t in node.iter(TEXT))
    value = cell.find("m:v", NS)
    return value.text if value is not None and value.text else ""


def _read_xlsx(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in item.iter(TEXT)))
        sheet = ET.fromstring(z.read(_first_sheet_path(z)))

    rows: list[list[str]] = []
    for row in sheet.findall(".//m:sheetData/m:row", NS):
        cells: dict[int, str] = {}
        for cell in row.findall("m:c", NS):
            ref = cell.attrib.get("r", "")
            if ref:
                cells[_column_index(ref)] = _cell_text(cell, shared)
        width = max(cells) + 1 if cells else 0
        rows.append([cells.get(i, "") for i in range(width)])
    return rows


# ------------------------------------------------------------------ csv


def _read_csv(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"认不出 {path.name} 的编码，转成 UTF-8 再试")
    return [row for row in csv.reader(io.StringIO(text))]


# ------------------------------------------------------------------ 统一入口


def _canonical(header: str) -> str | None:
    header = header.strip()
    if not header:
        return None
    for key, names in COLUMN_ALIASES.items():
        if header in names:
            return key
    return None


def read_collection(path: Path) -> list[dict[str, str]]:
    """读收藏表，返回 [{name, alias, url, comment, status}, ...]。"""
    if not path.exists():
        raise FileNotFoundError(f"找不到表格：{path}")
    rows = _read_csv(path) if path.suffix.lower() == ".csv" else _read_xlsx(path)
    if not rows:
        return []

    header = rows[0]
    index: dict[str, int] = {}
    for i, cell in enumerate(header):
        key = _canonical(cell)
        if key and key not in index:
            index[key] = i

    missing = [key for key in REQUIRED if key not in index]
    if missing:
        names = "、".join(COLUMN_ALIASES[key][0] for key in missing)
        raise ValueError(f"表头里找不到「{names}」列，确认是 Bangumi 收藏导出表")

    records: list[dict[str, str]] = []
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        records.append(
            {key: (row[i].strip() if i < len(row) else "") for key, i in index.items()}
        )
    return records

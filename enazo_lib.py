#!/usr/bin/env python3
"""enazo 词库本地管理：直接调词库接口，不用开页面点按钮。

词库的「唯一标识码」就是钥匙——接口不校验登录，谁能拿到码谁就能改库，
所以别把这个码贴到公开地方。

用法：

    python enazo_lib.py stat                    # 看词库现状
    python enazo_lib.py pull                    # 拉一份 json 备份
    python enazo_lib.py import 题库.txt          # 用题库替换全部词条
    python enazo_lib.py import 题库.txt --append # 追加（同名合并别名和提示）
    python enazo_lib.py clear                   # 清空（打删除标记）
    python enazo_lib.py push 备份.json           # 把本地 json 推回去

词库名放在 local.json 的 enazo_lib 字段；也可以 --name 临时指定。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCAL_JSON = ROOT / "local.json"
DATA_DIR = ROOT / "bin" / "data" / "enazo"
BASE = "https://draw-hd2.enazo.cn/api/draw"

# 站点自己的分隔符：别名和提示都按这个切
SEP_RE = re.compile(r"[,，;；、/]")
DEFAULT_TIP = 2
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0",
    "Accept": "application/json",
    "Origin": "https://enazo.cn",
    "Referer": "https://enazo.cn/",
}


# ---------------------------------------------------------------- 配置


def load_config() -> dict:
    if not LOCAL_JSON.exists():
        return {}
    try:
        return json.loads(LOCAL_JSON.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------- 接口


def _request(url: str, payload: dict | None = None) -> dict:
    data = None
    headers = dict(HEADERS)
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
    return json.loads(body) if body else {}


def get_lib(name: str) -> dict:
    return _request(f"{BASE}/lib/{name}")


def post_lib(name: str, lib: dict) -> dict:
    return _request(f"{BASE}/lib/{name}", lib)


def backup(name: str, lib: dict) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = DATA_DIR / f"{name}-{stamp}.json"
    path.write_text(json.dumps(lib, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 解析


def split_words(text: str) -> list[str]:
    """复刻站点的切分：按 , ， ; ； 、 / 切，去空去重，保持顺序。"""
    seen: list[str] = []
    for part in SEP_RE.split(text or ""):
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    return seen


def make_id() -> str:
    """站点的 id 是 8 位小写字母数字，页面加载时缺了会自己补，这里先给上。"""
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    return "".join(random.choices(alphabet, k=8))


def parse_line(line: str, tip: int = DEFAULT_TIP) -> dict | None:
    """复刻 createTagByText：支持 #备注 $小狐狸提示 ^角色。"""
    text = line.strip()
    if not text:
        return None

    remark = ""
    match = re.search(r"#(.+)$", text)
    if match:
        remark = match.group(1).strip()
        text = text[: match.start()]

    reminds: list[str] = []
    match = re.search(r"\$(.+)$", text)
    if match:
        reminds = split_words(match.group(1))
        text = text[: match.start()]

    characters: list[str] = []
    match = re.search(r"\^(.+)$", text)
    if match:
        characters = split_words(match.group(1))
        text = text[: match.start()]

    words = split_words(text)
    if not words:
        return None

    tag = {
        "id": make_id(),
        "alias": words[1:],
        "tip": tip,
        "name": words[0],
        "reminds": reminds,
        "characters": characters,
        "created": int(time.time() * 1000),
    }
    if remark:
        tag["remark"] = remark
    return tag


def read_bank(path: Path) -> list[dict]:
    tags = []
    used: set[str] = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        tag = parse_line(line)
        if not tag:
            continue
        while tag["id"] in used:
            tag["id"] = make_id()
        used.add(tag["id"])
        tags.append(tag)
    return tags


# ---------------------------------------------------------------- 命令


def cmd_stat(name: str) -> int:
    lib = get_lib(name)
    tags = lib.get("tags") or []
    removed = lib.get("removedItems") or []
    print(f"词库：{name}")
    print(f"  作者：{lib.get('author') or '-'}")
    print(f"  词条：{len(tags)} 条")
    print(f"  已删除标记：{len(removed)} 条")
    print(f"  最后修改：{lib.get('modified') or '-'}")
    if tags:
        sample = tags[0]
        print(f"  样例：{sample.get('name')} | 别名 {len(sample.get('alias') or [])} | 提示 {len(sample.get('reminds') or [])}")
    return 0


def cmd_pull(name: str) -> int:
    lib = get_lib(name)
    path = backup(name, lib)
    print(f"已保存 {len(lib.get('tags') or [])} 条 -> {path}")
    return 0


def cmd_clear(name: str) -> int:
    lib = get_lib(name)
    tags = lib.get("tags") or []
    if not tags:
        print("词库已经是空的")
        return 0
    print(f"当前 {len(tags)} 条，备份 -> {backup(name, lib)}")
    now = int(time.time() * 1000)
    removed = lib.get("removedItems") or []
    for tag in tags:
        tag["removed"] = now
        removed.append(tag)
    lib["removedItems"] = removed
    lib["tags"] = []
    post_lib(name, lib)

    after = get_lib(name)
    print(f"已清空：词条 {len(after.get('tags') or [])} 条，删除标记 {len(after.get('removedItems') or [])} 条")
    return 0


def cmd_import(name: str, bank: Path, append: bool, limit: int) -> int:
    if not bank.exists():
        print(f"找不到题库：{bank}", file=sys.stderr)
        return 2
    new_tags = read_bank(bank)
    if limit:
        new_tags = new_tags[:limit]
    if not new_tags:
        print("题库里没有解析出词条", file=sys.stderr)
        return 2

    lib = get_lib(name)
    old_tags = lib.get("tags") or []
    print(f"词库现有 {len(old_tags)} 条，本次导入 {len(new_tags)} 条（备份 -> {backup(name, lib)}）")

    if append:
        index = {tag.get("name"): tag for tag in old_tags}
        added = 0
        for tag in new_tags:
            exists = index.get(tag["name"])
            if exists:
                exists["alias"] = list(dict.fromkeys((exists.get("alias") or []) + tag["alias"]))
                exists["reminds"] = list(dict.fromkeys((exists.get("reminds") or []) + tag["reminds"]))
            else:
                old_tags.append(tag)
                index[tag["name"]] = tag
                added += 1
        lib["tags"] = old_tags
        print(f"追加 {added} 条，其余同名合并")
    else:
        lib["tags"] = new_tags
        print("按替换方式写入")

    post_lib(name, lib)

    after = get_lib(name)
    tags = after.get("tags") or []
    print(f"写入完成，服务端现在 {len(tags)} 条")
    if tags:
        first = tags[0]
        print(f"  第一条：{first.get('name')} | 别名 {len(first.get('alias') or [])} | 提示 {len(first.get('reminds') or [])}")
        if not first.get("id"):
            print("  注意：新词条没有 id 字段（页面保存时才会补），如遇编辑异常告诉我")
    return 0


def cmd_meta(name: str, updates: dict) -> int:
    """改词库信息：名称、简介、介绍、作者、群号、专区。只改传进来的字段。"""
    clean = {key: value for key, value in updates.items() if value is not None}
    if not clean:
        print("没有要改的字段", file=sys.stderr)
        return 2
    lib = get_lib(name)
    print(f"备份 -> {backup(name, lib)}")
    before = {key: lib.get(key) for key in clean}
    lib.update(clean)
    post_lib(name, lib)

    after = get_lib(name)
    for key, value in clean.items():
        shown = value if not isinstance(value, str) or len(value) <= 60 else value[:60] + "…"
        print(f"  {key}: {before.get(key)!r} -> {after.get(key)!r}" if shown else f"  {key}: -> {value!r}")
    return 0


def cmd_copy(dest: str, source: str, with_meta: bool) -> int:
    """把 source 词库的条目整份复制到 dest；--meta 时连简介、群号、专区和作者一起搬。"""
    if not source:
        print("copy 需要 --source 指定来源词库", file=sys.stderr)
        return 2
    if source == dest:
        print("来源和目标是同一个词库", file=sys.stderr)
        return 2

    src = get_lib(source)
    dst = get_lib(dest)
    print(f"来源 {source}: {len(src.get('tags') or [])} 条")
    print(f"目标 {dest}: {len(dst.get('tags') or [])} 条（备份 -> {backup(dest, dst)}）")

    dst["tags"] = json.loads(json.dumps(src.get("tags") or []))
    if with_meta:
        for key in ("author", "summary", "resume", "groupNumber", "robotGroupNumber", "types"):
            if key in src:
                dst[key] = src[key]

    post_lib(dest, dst)
    after = get_lib(dest)
    print(f"写入完成，{dest} 现在 {len(after.get('tags') or [])} 条")
    if with_meta:
        print(f"  简介：{after.get('summary')}")
        print(f"  群号：{after.get('groupNumber')}  专区：{after.get('types')}")
    return 0


def cmd_push(name: str, path: Path) -> int:
    if not path.exists():
        print(f"找不到文件：{path}", file=sys.stderr)
        return 2
    lib = json.loads(path.read_text(encoding="utf-8-sig"))
    post_lib(name, lib)
    after = get_lib(name)
    print(f"已推送，服务端现在 {len(after.get('tags') or [])} 条")
    return 0


def main(argv=None) -> int:
    config = load_config()
    parser = argparse.ArgumentParser(description="enazo 词库本地管理")
    parser.add_argument("command", choices=["stat", "pull", "clear", "import", "copy", "meta", "push"])
    parser.add_argument("target", nargs="?", help="import/push 用的文件路径")
    parser.add_argument("--name", default=config.get("enazo_lib", ""), help="词库唯一标识码")
    parser.add_argument("--source", help="copy 时的来源词库")
    parser.add_argument("--meta", action="store_true", help="copy 时连简介、群号、专区和作者一起搬")
    parser.add_argument("--append", action="store_true", help="追加而不是替换")
    parser.add_argument("--limit", type=int, default=0, help="只导入前 N 条，试水用")
    parser.add_argument("--title", help="词库名称")
    parser.add_argument("--summary", help="简介（一句话）")
    parser.add_argument("--resume", help="介绍（长文）")
    parser.add_argument("--author", help="作者")
    parser.add_argument("--group", help="题库群聊群号")
    parser.add_argument("--robot-group", help="粉红妹猜画交互群号")
    parser.add_argument("--zone", action="append", help="希望所在专区，可重复")
    args = parser.parse_args(argv)

    if not args.name:
        print("没指定词库：在 local.json 里写 enazo_lib，或者用 --name", file=sys.stderr)
        return 2

    if args.command == "stat":
        return cmd_stat(args.name)
    if args.command == "pull":
        return cmd_pull(args.name)
    if args.command == "clear":
        return cmd_clear(args.name)
    if args.command == "meta":
        zones = None
        if args.zone is not None:
            # 传 none / - 表示清空专区
            if [item.strip().lower() for item in args.zone] in (["none"], ["-"], ["无"]):
                zones = []
            else:
                zones = [
                    z.strip()
                    for item in args.zone
                    for z in item.replace("，", ",").split(",")
                    if z.strip()
                ]
        return cmd_meta(
            args.name,
            {
                "name": args.title,
                "summary": args.summary,
                "resume": args.resume,
                "author": args.author,
                "groupNumber": args.group,
                "robotGroupNumber": args.robot_group,
                "types": zones,
            },
        )
    if args.command == "copy":
        return cmd_copy(args.name, args.source, args.meta)
    if args.command in ("import", "push") and not args.target:
        print(f"{args.command} 需要一个文件路径", file=sys.stderr)
        return 2
    if args.command == "import":
        return cmd_import(args.name, Path(args.target), args.append, args.limit)
    return cmd_push(args.name, Path(args.target))


if __name__ == "__main__":
    raise SystemExit(main())

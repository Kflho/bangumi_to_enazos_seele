"""按用户同步收藏列表，尽量少发请求。

接口按 `updated_at` 倒序返回，所以只要翻到比上次同步更旧的记录就能停：
收藏没变动的话，一次同步只要 1 个请求（每页 100 条）。

快照落在 bin/data/collections/<user>.json，里面有每条记录的 updated_at，
下次同步靠它判断哪些是新增、哪些变过。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .client import Http

TYPE_NAMES = {1: "想看", 2: "看过", 3: "在看", 4: "搁置", 5: "抛弃"}
PAGE_SIZE = 100


@dataclass
class SyncStats:
    user: str
    nickname: str = ""
    pages: int = 0
    added: int = 0
    updated: int = 0
    total: int = 0
    incremental: bool = True
    error: str = ""


@dataclass
class CollectionStore:
    data_dir: Path
    http: Http
    _cache: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, user: str, collection_type: int) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in user)
        return self.data_dir / f"{safe}-t{collection_type}.json"

    def load(self, user: str, collection_type: int = 2) -> dict:
        path = self._path(user, collection_type)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return {"user": user, "collection_type": collection_type, "last_sync": None, "items": {}}

    def save(self, user: str, snapshot: dict) -> None:
        path = self._path(user, snapshot.get("collection_type", 2))
        path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------ 同步

    def sync(
        self,
        user: str,
        subject_type: int = 2,
        collection_type: int = 2,
        full: bool = False,
    ) -> tuple[list[dict], SyncStats]:
        snapshot = self.load(user, collection_type)
        items: dict[str, dict] = snapshot.get("items") or {}
        last_sync = snapshot.get("last_sync")
        stats = SyncStats(user=user, incremental=bool(last_sync) and not full)

        offset = 0
        total = snapshot.get("total") or 0
        newest = last_sync or ""

        while True:
            page = self.http.get_json(
                f"/v0/users/{user}/collections",
                {
                    "subject_type": subject_type,
                    "type": collection_type,
                    "limit": PAGE_SIZE,
                    "offset": offset,
                },
            )
            if page is None:
                stats.error = "收藏列表请求失败"
                break

            data = page.get("data") or []
            total = page.get("total", total) or total
            if not data:
                break
            stats.pages += 1

            reached_known = False
            for raw in data:
                record = self._normalize(raw)
                if not record:
                    continue
                if record["updated_at"] > newest:
                    newest = record["updated_at"]
                if stats.incremental and last_sync and record["updated_at"] <= last_sync:
                    reached_known = True
                key = str(record["id"])
                old = items.get(key)
                if old is None:
                    stats.added += 1
                elif old.get("updated_at") != record["updated_at"]:
                    stats.updated += 1
                items[key] = record

            offset += PAGE_SIZE
            if reached_known or offset >= total:
                break

        snapshot["collection_type"] = collection_type
        snapshot["items"] = items
        snapshot["last_sync"] = newest or last_sync
        snapshot["total"] = len(items)
        snapshot["fetched_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        self.save(user, snapshot)

        stats.total = len(items)
        ordered = sorted(items.values(), key=lambda item: item.get("updated_at", ""), reverse=True)
        return ordered, stats

    @staticmethod
    def _normalize(raw: dict) -> dict | None:
        subject = raw.get("subject") or {}
        sid = raw.get("subject_id") or subject.get("id")
        if not sid:
            return None
        return {
            "id": int(sid),
            "name": subject.get("name_cn") or subject.get("name") or "",
            "name_cn": subject.get("name_cn") or "",
            "jp_name": subject.get("name") or "",
            "comment": raw.get("comment") or "",
            "type": raw.get("type"),
            "status": TYPE_NAMES.get(raw.get("type"), ""),
            "rate": raw.get("rate"),
            "tags": raw.get("tags") or [],
            "updated_at": raw.get("updated_at") or "",
        }

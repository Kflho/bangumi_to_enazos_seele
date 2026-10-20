"""条目详情缓存。

别名字段基本不会变，抓到一次就落盘，之后重跑直接读本地，不再发请求。
多个线程会同时来取，所以计数用锁保护；每个条目一个文件，写盘互不干扰。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from .client import Http


class SubjectStore:
    def __init__(self, cache_dir: Path, http: Http, offline: bool = False):
        self.cache_dir = cache_dir
        self.http = http
        self.offline = offline
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.hits = 0
        self.fetches = 0

    def _path(self, sid: int) -> Path:
        return self.cache_dir / f"{sid}.json"

    def has(self, sid: int) -> bool:
        return self._path(sid).exists()

    def peek(self, sid: int) -> dict | None:
        """只读本地缓存，不发请求；拼行阶段用这个，避免失败条目被重复请求。"""
        path = self._path(sid)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        with self._lock:
            self.hits += 1
        return data

    def get(self, sid: int, refresh: bool = False) -> dict | None:
        path = self._path(sid)
        if path.exists() and not refresh:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = None
            if data is not None:
                with self._lock:
                    self.hits += 1
                return data
        if self.offline:
            return None

        data = self.http.get_json(f"/v0/subjects/{sid}")
        if data is not None:
            try:
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
            with self._lock:
                self.fetches += 1
        return data

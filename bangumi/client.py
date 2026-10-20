"""Bangumi 条目接口：带缓存、重试和限流退避。

只调公开的 v0 接口，不登录、不带 token：
    GET https://api.bgm.tv/v0/subjects/{id}

接口要求 User-Agent 非空，不带会被挡。抓回来的 JSON 原样落盘，重跑命中缓存就
不再请求，所以中断了直接重跑即可。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.bgm.tv/v0/subjects/{sid}"
USER_AGENT = "bangumi_to_enazos_seele/1.0 (https://github.com/Kflho/bangumi_to_enazos_seele)"

MAX_ATTEMPTS = 3
TIMEOUT = 25
MAX_DELAY = 5.0


class BangumiClient:
    def __init__(self, cache_dir: Path, delay: float = 0.5, offline: bool = False):
        self.cache_dir = cache_dir
        self.delay = delay
        self.offline = offline
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.fetches = 0
        self.throttled = 0
        self.failures: list[tuple[int, str]] = []

    # -------------------------------------------------------------- 缓存

    def _cache_file(self, sid: int) -> Path:
        return self.cache_dir / f"{sid}.json"

    def get(self, sid: int) -> dict | None:
        cached = self._cache_file(sid)
        if cached.exists():
            try:
                self.hits += 1
                return json.loads(cached.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        if self.offline:
            return None

        data = self._fetch(sid)
        if data is not None:
            cached.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            self.fetches += 1
            time.sleep(self.delay)
        return data

    # -------------------------------------------------------------- 请求

    def _back_off(self, attempt: int, headers) -> None:
        """限流就往后退：优先听 Retry-After，同时把间隔翻倍。"""
        self.throttled += 1
        wait = 0.0
        if headers is not None:
            retry_after = headers.get("Retry-After")
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = 0.0
        wait = max(wait, 15.0 * (attempt + 1))
        self.delay = min(self.delay * 2, MAX_DELAY)
        print(
            f"    被限流，等 {wait:.0f}s，间隔调到 {self.delay:.2f}s（可中断，重跑接着来）",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(wait)

    def _fetch(self, sid: int) -> dict | None:
        request = urllib.request.Request(
            API.format(sid=sid),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        for attempt in range(MAX_ATTEMPTS):
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    self.failures.append((sid, "404 条目不存在"))
                    return None
                if exc.code == 429 or exc.code >= 500:
                    self._back_off(attempt, exc.headers)
                    continue
                self.failures.append((sid, f"HTTP {exc.code}"))
                return None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                reason = getattr(exc, "reason", exc)
                if attempt == MAX_ATTEMPTS - 1:
                    self.failures.append((sid, f"{type(exc).__name__}: {reason}"))
                    return None
                time.sleep(2**attempt)
        return None

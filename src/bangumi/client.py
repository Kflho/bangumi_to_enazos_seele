"""Bangumi 接口的公共请求层：并发、全局限速、重试与退避。

并发只在本地起线程，请求节流是全局的：不论开几个 worker，两次请求的发起间隔
都不小于 `1 / rate` 秒。这样加并发只吃「网络往返时间」，不会把接口打爆。
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.bgm.tv"
USER_AGENT = (
    "bangumi_to_enazos_seele/1.0 "
    "(https://github.com/Kflho/bangumi_to_enazos_seele)"
)

MAX_ATTEMPTS = 3
TIMEOUT = 15
MAX_INTERVAL = 5.0


class Http:
    """带全局限速和退避的 JSON 请求器，可被多个线程共用。"""

    def __init__(self, rate: float = 6.0, delay: float = 0.0):
        # rate 是全局每秒请求数；delay 是旧参数，rate=0 时按 delay 串行节流
        self.rate = rate
        self.delay = delay
        self.interval = 1.0 / rate if rate > 0 else delay
        self._lock = threading.Lock()
        self._next_start = 0.0
        self.requests = 0
        self.throttled = 0
        self.failures: list[tuple[str, str]] = []

    # -------------------------------------------------------------- 限速

    def _wait_slot(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_start)
            self._next_start = start + self.interval
            wait = start - now
        if wait > 0:
            time.sleep(wait)

    def _bump(self, name: str) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + 1)

    def _record_failure(self, url: str, reason: str) -> None:
        with self._lock:
            self.failures.append((url, reason))

    # -------------------------------------------------------------- 请求

    def get_json(self, path: str, params: dict | None = None) -> dict | None:
        url = path if path.startswith("http") else API_BASE + path
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )

        for attempt in range(MAX_ATTEMPTS):
            self._wait_slot()
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    body = response.read()
                payload = json.loads(body)
                self._bump("requests")
                return payload
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    self._record_failure(url, "404 不存在")
                    return None
                if exc.code == 403:
                    self._record_failure(url, "403 没有权限（可能是私密收藏）")
                    return None
                if exc.code == 429 or exc.code >= 500:
                    self._back_off(attempt, exc.headers)
                    continue
                self._record_failure(url, f"HTTP {exc.code}")
                return None
            except (json.JSONDecodeError, http.client.HTTPException, ValueError) as exc:
                # 代理偶尔把响应截断，读出来的 JSON 不完整，重试即可
                if attempt == MAX_ATTEMPTS - 1:
                    self._record_failure(url, f"{type(exc).__name__}")
                    return None
                time.sleep(2**attempt)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                reason = getattr(exc, "reason", exc)
                if attempt == MAX_ATTEMPTS - 1:
                    self._record_failure(url, f"{type(exc).__name__}: {reason}")
                    return None
                time.sleep(2**attempt)
        return None

    def _back_off(self, attempt: int, headers) -> None:
        """被限流就往后退：优先听 Retry-After，同时把整体速率降一半。"""
        self._bump("throttled")
        wait = 0.0
        if headers is not None:
            retry_after = headers.get("Retry-After")
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = 0.0
        wait = max(wait, 10.0 * (attempt + 1))
        with self._lock:
            self.interval = min(max(self.interval * 2, 0.5), MAX_INTERVAL)
        print(
            f"    被限流，等 {wait:.0f}s，请求间隔调到 {self.interval:.2f}s"
            "（可中断，重跑接着来）",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(wait)

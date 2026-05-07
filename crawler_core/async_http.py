"""异步并发 HTTP 抓取（基于 httpx）。

设计：
- 单例 AsyncHTTPClient，内部维护一个独立后台事件循环线程
- 工具层是同步 def，通过 run_coroutine_threadsafe(...).result() 桥接
- httpx 默认 HTTP/2，每域名共享 connection pool
- 仅做 GET（异步并发主战场），其它请求走 requests/curl_cffi

httpx 是可选依赖，未安装时 batch 工具退化为线程池并发的 requests。
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Awaitable, Callable, Iterable
from urllib.parse import urlparse

try:
    import httpx  # type: ignore
    HAS_HTTPX = True
except ImportError:
    httpx = None  # type: ignore
    HAS_HTTPX = False


class AsyncBackend:
    """单例后台事件循环 + httpx AsyncClient。

    线程安全：任何线程都可以调 submit(coro)，结果通过 Future.result() 取。
    """

    def __init__(self, *, http2: bool = True, timeout: float = 30.0,
                 max_connections: int = 100, max_keepalive: int = 20,
                 verify_tls: bool = True):
        self.http2 = http2 and HAS_HTTPX
        self.timeout = timeout
        self.max_connections = max_connections
        self.max_keepalive = max_keepalive
        self.verify_tls = verify_tls
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client = None
        self._lock = threading.Lock()
        self._closed = False

    def _start(self) -> None:
        if self._loop is not None:
            return
        ready = threading.Event()

        def run_loop() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=run_loop, name="async-http-loop", daemon=True)
        self._thread.start()
        ready.wait(timeout=5)

    def _ensure_client(self):
        if not HAS_HTTPX:
            raise RuntimeError("httpx 未安装；请安装 httpx[http2] 启用异步并发抓取")
        if self._closed:
            raise RuntimeError("AsyncBackend 已关闭")
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is not None:
                return self._client
            self._start()
            limits = httpx.Limits(max_connections=self.max_connections,
                                  max_keepalive_connections=self.max_keepalive)
            kwargs = {
                "limits": limits,
                "timeout": self.timeout,
                "verify": self.verify_tls,
                "follow_redirects": True,
            }
            if self.http2:
                # 优雅降级：h2 包不存在时退回 HTTP/1.1
                try:
                    self._client = httpx.AsyncClient(http2=True, **kwargs)
                    return self._client
                except ImportError:
                    self.http2 = False
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    def submit(self, coro: Awaitable):
        self._start()
        if self._loop is None:
            raise RuntimeError("event loop 未就绪")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def run(self, coro: Awaitable, timeout: float | None = None):
        future = self.submit(coro)
        return future.result(timeout=timeout)

    async def _fetch_one(self, url: str, *, headers: dict | None = None,
                         method: str = "GET", body=None,
                         per_url_timeout: float | None = None) -> dict:
        client = self._ensure_client()
        started = time.time()
        try:
            resp = await client.request(
                method,
                url,
                headers=headers,
                content=body if isinstance(body, (bytes, bytearray)) else None,
                json=body if not isinstance(body, (bytes, bytearray, str, type(None))) else None,
                data=body if isinstance(body, str) else None,
                timeout=per_url_timeout or self.timeout,
            )
            text = resp.text
            return {
                "url": url,
                "ok": resp.status_code < 400,
                "status": resp.status_code,
                "html": text,
                "elapsed_ms": int((time.time() - started) * 1000),
            }
        except Exception as exc:
            return {
                "url": url,
                "ok": False,
                "status": 0,
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_ms": int((time.time() - started) * 1000),
            }

    async def _fetch_batch(self, urls: list[str], *, concurrency: int = 5,
                            headers: dict | None = None,
                            per_url_timeout: float | None = None,
                            policy_check: Callable[[str], None] | None = None) -> list[dict]:
        sem = asyncio.Semaphore(max(1, int(concurrency)))

        async def worker(url: str) -> dict:
            try:
                if policy_check:
                    policy_check(url)
            except Exception as exc:
                return {"url": url, "ok": False, "status": 0,
                        "error": f"policy_blocked: {exc}", "elapsed_ms": 0}
            async with sem:
                return await self._fetch_one(url, headers=headers,
                                              per_url_timeout=per_url_timeout)

        return await asyncio.gather(*[worker(u) for u in urls])

    def fetch_batch(self, urls: list[str], *, concurrency: int = 5,
                    headers: dict | None = None,
                    per_url_timeout: float | None = None,
                    policy_check: Callable[[str], None] | None = None,
                    overall_timeout: float | None = None) -> list[dict]:
        if not HAS_HTTPX:
            return self._fetch_batch_threaded(urls, concurrency=concurrency,
                                              headers=headers,
                                              per_url_timeout=per_url_timeout,
                                              policy_check=policy_check)
        return self.run(
            self._fetch_batch(urls, concurrency=concurrency, headers=headers,
                              per_url_timeout=per_url_timeout, policy_check=policy_check),
            timeout=overall_timeout,
        )

    def _fetch_batch_threaded(self, urls: list[str], *, concurrency: int,
                               headers: dict | None,
                               per_url_timeout: float | None,
                               policy_check: Callable[[str], None] | None) -> list[dict]:
        """没装 httpx 时的退化路径：用 requests + ThreadPoolExecutor。"""
        import requests as _rq

        results: list[dict] = [{"url": u, "ok": False, "status": 0,
                                  "error": "pending"} for u in urls]

        def fetch(idx: int, url: str) -> None:
            started = time.time()
            try:
                if policy_check:
                    policy_check(url)
                resp = _rq.get(url, headers=headers,
                               timeout=per_url_timeout or 30,
                               verify=True)
                results[idx] = {
                    "url": url,
                    "ok": resp.status_code < 400,
                    "status": resp.status_code,
                    "html": resp.text,
                    "elapsed_ms": int((time.time() - started) * 1000),
                }
            except Exception as exc:
                results[idx] = {
                    "url": url,
                    "ok": False,
                    "status": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                    "elapsed_ms": int((time.time() - started) * 1000),
                }

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = [pool.submit(fetch, i, u) for i, u in enumerate(urls)]
            for future in futures:
                future.result()
        return results

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._loop and self._client is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(self._client.aclose(), self._loop)
                future.result(timeout=5)
            except Exception:
                pass
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=2)


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower()

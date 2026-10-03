"""
Embedded Cloudflare Turnstile solver (in-process, no sidecar).

Ported from GROK-CLI's `src/core/turnstile.py` and adapted to the Conso
pipeline. Runs Camoufox (anti-detect Firefox) inside this process, so no
external HTTP solver service is needed.

Design (the parts that matter):
  - a **persistent** browser, started lazily and reused across solves,
  - a dedicated asyncio loop in a daemon thread, exposing a blocking `solve()`,
  - an `asyncio.Semaphore` for concurrency (not a lock),
  - one isolated browser **context per solve** (closed afterwards),
  - optional per-solve proxy (Playwright proxy dict).

Conso note: the Conso Supabase project rejects stub-page Turnstile tokens
(`invalid-input-response`), so the real verify-human page must be loaded. This
solver drives the widget on a **real page** (`real_page=True` default) with the
whitelisted redirect_uri, which is the path that produced accepted tokens.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Coroutine
from urllib.parse import urlparse

# Python 3.14 + multiprocessing resource_tracker guard (as in GROK-CLI).
try:
    import camoufox.addons

    camoufox.addons.Lock = threading.Lock  # type: ignore[assignment]
except Exception:
    pass

# Conso verify-human only renders the widget with a whitelisted redirect_uri.
CONSO_REDIRECT_URI = "https://bjibbmkefnaamkenamdppfengeepadpi.chromiumapp.org/"
CONSO_VERIFY_URL = "https://www.conso.xyz/verify-human"

# Stub page that explicitly calls turnstile.render() from the api.js onload
# callback. This is the path that actually produces a token in Camoufox:
# relying on the real page's Next.js <Script> fails (widget renders before
# api.js is ready / CSP blocks the inline render), but an explicit onload
# callback renders reliably.
STUB_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js?onload=__consoCb" async defer></script>
<script>function __consoCb(){window.turnstile.render("#cf-slot", __CONSO_OPTS__);}</script>
</head><body><div id="cf-slot"></div><p id="ip-display"></p></body></html>"""


class InternalSolverError(RuntimeError):
    """Raised when the embedded solver cannot produce a token."""


@dataclass(frozen=True)
class SolverConfig:
    headless: bool = True
    timeout_seconds: int = 90
    proxy: str | None = None


class InternalTurnstileSolver:
    """Persistent in-process Camoufox Turnstile solver."""

    def __init__(
        self,
        headless: bool = True,
        timeout_seconds: int = 90,
        proxy: str | None = None,
        max_concurrent: int = 4,
    ) -> None:
        self.config = SolverConfig(
            headless=headless,
            timeout_seconds=max(10, int(timeout_seconds)),
            proxy=proxy,
        )
        self.max_concurrent = max(1, int(max_concurrent))
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_lock = threading.Lock()
        self._loop_ready = threading.Event()
        self._camoufox: Any = None
        self._browser: Any = None
        self._browser_lock: asyncio.Lock | None = None
        self._sem: asyncio.Semaphore | None = None
        self._closed = False
        self._last_solve = 0.0
        self._cooldown = float(os.environ.get("SOLVER_SOLVE_DELAY", "0") or 0)

    @property
    def ready(self) -> bool:
        return self._browser is not None and not self._closed

    def warm(self, timeout: float = 60.0) -> bool:
        """Start the browser ahead of the first solve (blocks until ready).

        Calling this before a run removes the first-solve latency (browser
        launch + api.js load). Returns True once the browser is up.
        """
        if self.ready:
            return True
        try:
            self._submit(self._ensure_browser(), timeout=timeout)
            return self.ready
        except Exception:
            return False

    def warm_async(self) -> threading.Thread:
        """Warm the browser on a background thread (non-blocking)."""
        t = threading.Thread(target=self.warm, name="conso-solver-warm", daemon=True)
        t.start()
        return t

    @staticmethod
    def is_available() -> bool:
        try:
            import camoufox  # noqa: F401

            return True
        except ImportError:
            return False

    # -- loop plumbing -----------------------------------------------------
    def _start_loop(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        with self._loop_lock:
            if self._thread and self._thread.is_alive():
                return
            self._loop_ready.clear()
            self._thread = threading.Thread(
                target=self._loop_main, name="conso-internal-solver", daemon=True
            )
            self._thread.start()
            if not self._loop_ready.wait(timeout=15):
                raise InternalSolverError("internal solver event loop failed to start")

    def _loop_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    def _submit(self, coro: Coroutine[Any, Any, Any], timeout: float | None = None) -> Any:
        if self._closed:
            raise InternalSolverError("internal solver is closed")
        self._start_loop()
        if self._loop is None:
            raise InternalSolverError("internal solver loop unavailable")
        future: Future[Any] = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # -- browser -----------------------------------------------------------
    async def _ensure_browser(self) -> None:
        if self._browser is not None:
            return
        if self._browser_lock is None:
            self._browser_lock = asyncio.Lock()
        async with self._browser_lock:
            if self._browser is not None:
                return
            if self._sem is None:
                self._sem = asyncio.Semaphore(self.max_concurrent)
            try:
                from camoufox import DefaultAddons
                from camoufox.async_api import AsyncCamoufox
            except ImportError as exc:  # pragma: no cover
                raise InternalSolverError(
                    "Camoufox is not installed. Run: pip install 'camoufox[geoip]>=0.4.0' "
                    "&& python -m camoufox fetch"
                ) from exc
            self._camoufox = AsyncCamoufox(
                headless=self.config.headless,
                exclude_addons=[DefaultAddons.UBO],
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            try:
                self._browser = await self._camoufox.start()
            except Exception as exc:
                await self._close_async()
                raise InternalSolverError(f"failed to start embedded Camoufox: {exc}") from exc

    @staticmethod
    def _proxy_config(proxy: str | None) -> dict[str, str] | None:
        if not proxy:
            return None
        parsed = urlparse(proxy)
        if not parsed.scheme or not parsed.hostname or not parsed.port:
            raise InternalSolverError(f"invalid solver proxy: {proxy}")
        result = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            result["username"] = parsed.username
        if parsed.password:
            result["password"] = parsed.password
        return result

    async def _solve_async(
        self,
        url: str,
        sitekey: str,
        *,
        action: str | None,
        cdata: str | None,
        timeout_seconds: int,
        proxy: str | None,
        real_page: bool,
    ) -> str:
        await self._ensure_browser()
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.max_concurrent)

        async with self._sem:
            target = url if url.endswith("/") else url + "/"
            opts: dict[str, Any] = {"sitekey": sitekey}
            if action:
                opts["action"] = action
            if cdata:
                opts["cdata"] = cdata
            page_data = STUB_HTML.replace("__CONSO_OPTS__", json.dumps(opts))
            deadline = time.monotonic() + timeout_seconds

            proxy_cfg = self._proxy_config(proxy or self.config.proxy)
            ctx = (
                await self._browser.new_context(proxy=proxy_cfg)
                if proxy_cfg
                else await self._browser.new_context()
            )
            try:
                page = await ctx.new_page()
                if real_page:
                    # Load the real page, then inject an explicit render call so
                    # the widget is driven by our onload callback (the page's own
                    # Next.js <Script> render is unreliable in Camoufox).
                    await page.goto(target, wait_until="domcontentloaded", timeout=30_000)
                    await page.evaluate(
                        """(opts) => new Promise((resolve) => {
                            const go = () => { window.turnstile.render("#cf-slot", opts); resolve(); };
                            if (window.turnstile) { go(); return; }
                            const s = document.createElement('script');
                            s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?onload=__consoCb2";
                            window.__consoCb2 = go;
                            document.head.appendChild(s);
                        })""",
                        opts,
                    )
                    try:
                        await page.evaluate(
                            """() => { if (!document.querySelector('#cf-slot')) {
                                const d = document.createElement('div'); d.id = 'cf-slot';
                                document.body.prepend(d); } }"""
                        )
                    except Exception:
                        pass
                else:
                    await page.route(target, lambda route: route.fulfill(body=page_data, status=200))
                    await page.goto(target, wait_until="domcontentloaded", timeout=25_000)

                while time.monotonic() < deadline:
                    try:
                        token = await page.evaluate(
                            "() => { const e=document.querySelector('[name=cf-turnstile-response]'); return e?e.value:'' }"
                        )
                        if token and len(token) > 50:
                            return token
                    except Exception:
                        pass
                    await asyncio.sleep(0.3)

                raise InternalSolverError(
                    f"embedded Turnstile solve timed out after {timeout_seconds}s "
                    f"(the solve IP is probably rate-flagged by Cloudflare — wait ~30 min, raise SOLVER_SOLVE_DELAY, use residential proxies, or switch to "
                    f"CAPTCHA_PROVIDER=capsolver/2captcha)"
                )
            finally:
                try:
                    await ctx.close()
                except Exception:
                    pass

    def solve(
        self,
        url: str,
        sitekey: str,
        *,
        action: str | None = None,
        cdata: str | None = None,
        timeout_seconds: int | None = None,
        proxy: str | None = None,
        real_page: bool = False,
    ) -> str:
        if not url or not sitekey:
            raise InternalSolverError("url and sitekey are required")
        # Cooldown between solves (Cloudflare rate-flags a rapid-solve IP).
        if self._cooldown > 0:
            wait = self._last_solve + self._cooldown - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last_solve = time.time()
        timeout = max(10, int(timeout_seconds or self.config.timeout_seconds))
        try:
            return self._submit(
                self._solve_async(
                    url, sitekey, action=action, cdata=cdata,
                    timeout_seconds=timeout, proxy=proxy, real_page=real_page,
                ),
                timeout=timeout + 45,
            )
        except TimeoutError as exc:
            raise InternalSolverError(
                f"embedded solver operation timed out after {timeout}s"
            ) from exc

    # -- teardown ----------------------------------------------------------
    async def _close_async(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
        self._browser = None
        self._camoufox = None

    def close(self) -> None:
        if self._closed:
            return
        if self._loop and self._loop.is_running():
            try:
                self._submit(self._close_async(), timeout=20)
            except Exception:
                pass
            self._closed = True
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread:
                self._thread.join(timeout=5)
        else:
            self._closed = True

    def __enter__(self) -> "InternalTurnstileSolver":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


# -- process-wide singleton ------------------------------------------------
_default_solver: InternalTurnstileSolver | None = None
_default_lock = threading.Lock()


def get_default_solver(
    *,
    headless: bool | None = None,
    timeout_seconds: int | None = None,
    proxy: str | None = None,
    max_concurrent: int | None = None,
) -> InternalTurnstileSolver:
    """Return the process-wide embedded solver instance."""
    global _default_solver
    with _default_lock:
        if _default_solver is None or _default_solver._closed:
            if headless is None:
                headless = os.environ.get("SOLVER_HEADLESS", "1") != "0"
            if timeout_seconds is None:
                timeout_seconds = int(os.environ.get("SOLVER_TIMEOUT", "90"))
            if max_concurrent is None:
                max_concurrent = int(os.environ.get("SOLVER_MAX_CONCURRENT", "2") or 2)
            _default_solver = InternalTurnstileSolver(
                headless=headless, timeout_seconds=timeout_seconds,
                proxy=proxy, max_concurrent=max_concurrent,
            )
        return _default_solver


def close_default_solver() -> None:
    global _default_solver
    with _default_lock:
        if _default_solver is not None:
            _default_solver.close()
            _default_solver = None

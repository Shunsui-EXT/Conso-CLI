# GROK-CLI captcha handling — what we can learn

Source: `/home/elzanom/Documents/AI-FARM/GROK-CLI` + its external solver
`/home/elzanom/WORKER/9router-add/captcha-solver/universal_solver.py`.

## 1. Two solver backends, both supported

| Backend | File | What it is |
|---|---|---|
| `internal` (default) | `src/core/turnstile.py` | In-process **Camoufox** (anti-detect Firefox) — no sidecar |
| `external` | `src/core/solver.py` → `:8877` | HTTP POST to `universal_solver.py` |
| `auto` | — | try internal, fall back to external |

Config: `config.toml [solver] mode="internal" url="http://127.0.0.1:8877" headless=true timeout_seconds=90`.

## 2. The internal solver (the interesting part)

`InternalTurnstileSolver` — key design points we do NOT have:

1. **Persistent browser, lazy start.** One Camoufox instance, started on first
   solve and **reused**. We launch a fresh browser per solve.
2. **Dedicated asyncio loop in a daemon thread**, with a blocking `solve()` API
   (`asyncio.run_coroutine_threadsafe`). Sync code can call it from many threads.
3. **Concurrency via `asyncio.Semaphore(max_concurrent=8)`** — not a lock.
   Multiple solves run at once, each in its **own context** (`browser.new_context()`).
4. **Context per solve, closed after** — isolation, no state leakage.
5. **Proxy per context** via Playwright proxy dict
   (`{server, username, password}`), parsed from a URL.
6. **Stub-page solve**: `page.route(target, fulfill(body=HTML))` + click the
   widget + poll `[name=cf-turnstile-response]`.

## 3. `universal_solver.py` — the page-pool design

This is a more mature version of the same idea:

```
THREADS=2, PAGES_PER_THREAD=1  ->  max_task = threads * pages
self.page_pool = asyncio.Queue()      # ready-to-use (page, ctx) pairs
_build_pool(): for each thread -> new_context() -> new_page() -> pool.put()
solve_turnstile(): page, ctx = await pool.get()  ...  pool.put() in finally
```

- **True parallelism** through the pool: N solves take N pages concurrently.
- **`if pool.qsize() == 0: raise 429 "pool full, retry"`** — backpressure.
- **Retry with proxy rotation**: round 1 direct/sticky, round 2 swaps in the
  next proxy (new context).
- **Cleanup + periodic restart** (`_cleanup_expired`, `_periodic_restart(10min)`)
  — the equivalent of our "restart on flag", but automatic and timed.
- Capsolver fallback for other captcha types.

## 4. THE key finding for Conso

Straight from `universal_solver.py`:

> Turnstile: local stub-page tokens (~666 chars) get rejected by real sites
> (e.g. accounts.x.ai). Prefer Capsolver when key is present unless
> `TURNSTILE_LOCAL=1` is set.

And the fallback rule:

```python
if (t == "turnstile" and CAPSOLVER_API_KEY and SOLVER_MODE in ("auto","capsolver")
    and len(tok) < 700):
    pass   # short stub token -> fall through to Capsolver
```

So Grok's own team concluded that **stub-page Turnstile tokens are rejected**,
and route Turnstile to a **managed solver (Capsolver)** when a key exists.

This matches what we found empirically on Conso: the route-intercept/stub token
was rejected by Supabase (`invalid-input-response`), and only the **real-page**
solve worked. Grok solves the same problem by paying for Capsolver.

## 5. Concurrency model (registration)

`main.py` is a **sequential loop** (`for i in range(count)`), with a per-proxy
rotation (`pool.next()`) and a jittered delay (`delay + random(0, delay*0.5)`).
`workers.count=2` in config is passed to the engine but the top loop is serial —
so Grok registers **one at a time**, relying on the solver's internal parallelism
only for captcha.

## 6. Concrete improvements for our Conso pipeline

| Lesson | Action for us |
|---|---|
| Persistent browser + page pool | replace our sidecar's `_solve_lock` with a page pool (THREADS×PAGES) for real parallel solves |
| Semaphore instead of lock | allow N concurrent solves (helps when we later have multiple IPs) |
| Periodic restart (10 min) | our watchdog only restarts on failure; add a timed restart |
| Capsolver for Turnstile | if a key is available, prefer it over local stub solves (Grok's own conclusion) |
| Proxy per context | per-solve proxy (we do per-account proxy for HTTP only) |
| Jittered delay between accounts | we already pace; keep it |

## 7. What NOT to copy

- Grok's stub-page path still has the same Turnstile rejection problem — it is
  only viable because Capsolver covers it. For Conso (no key), **real-page**
  solve remains the working path.
- The top-level registration loop is serial; parallelism there would still hit
  the same per-IP solve limit.

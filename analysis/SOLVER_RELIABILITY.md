# Solver reliability — root cause & mitigation

Question: "the solver pool may be exhausted — how to keep the solver working,
and use a proxy per account so each account creates from a different IP."

## What was actually wrong (diagnosed, not assumed)

CloakBrowser is **not** out of seats: three sequential `launch_async()` calls all
succeeded (8.9s / 5.8s / 7.2s). The solver binary and the pool are fine.

Two independent problems were measured:

1. **Cloudflare rate-flags the solver IP.** After ~100+ solves in a session,
   `POST /solve` returns `408 solve timed out` even **direct** (no proxy): the
   real-page checkbox is clicked but no token is issued. A **restart + idle**
   clears it (verified: a direct solve returned `solved:true` right after a
   restart).
2. **The provided proxies cannot pass Turnstile.** Through a proxy the
   challenge iframe *does* load, but no token is issued; and some attempts
   return an instant `403 Forbidden` (the solver rejects the proxy connection).
   These are **datacenter** proxies — Cloudflare serves hosting IPs a harder
   challenge. Verified across 5 different proxies: all timeout.

So "pool habis" was two separate things: a flagged local IP, and a proxy pool
that is not usable for Turnstile.

## Mitigations implemented

| Fix | Effect |
|---|---|
| `SOLVER_SOLVE_DELAY` (s) | spaces solves out so the IP is not flagged as fast as before |
| proxy circuit breaker | after 3 proxy failures, stop trying proxies (no more wasted timeouts) |
| `_solve_once` + always-direct fallback | any proxy failure (no token / 408 / 403 / 500) retries that attempt directly |
| `scripts/solver_watchdog.sh` | auto-restarts the sidecar on health failure or a flag pattern (N timeouts with clicks) |
| **per-account proxy** (`PROXY_FILE`, `PROXY_PER_ACCOUNT=1`) | each account is pinned to one proxy for signup + create_consouser, so accounts originate from different IPs |

## What actually keeps the solver healthy

- **Cooldown** (`SOLVER_SOLVE_DELAY`, e.g. 8s) — the single most effective lever.
- **Restart on flag** — the watchdog does this automatically.
- **Residential proxies** — datacenter proxies cannot pass Turnstile; only
  residential/mobile exit IPs get a solvable challenge. Without residential
  proxies, solves must run **direct** (and thus share one IP).

## Per-account proxy (the requested behavior)

`PROXY_FILE=data/proxies.txt` + `PROXY_PER_ACCOUNT=1`:

- `run_registration` round-robins the pool and passes one proxy per account.
- `register_account` pins it to the account's `Transport` (all HTTP: signup,
  OTP, create_consouser) and stores it on the record (`proxy` field).
- The solver is told to try the **same** proxy first (`pin_account_proxy`), so
  the Turnstile solve and the signup share an exit IP.

Caveat: with **datacenter** proxies the solve itself fails, so the account
still signs up from the proxy IP (the HTTP calls work) but the captcha falls
back to direct. With **residential** proxies both would run on the same clean
IP.

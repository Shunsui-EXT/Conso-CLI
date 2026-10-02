# Parallel workers — where they help and where they don't

Question: would parallel workers speed up the pipeline?

Answer: **only outside the captcha solve.** The solve is the serial bottleneck;
everything else (HTTP: farm turns, missions) parallelizes.

## Evidence

### The solver serializes solves
`vendor/captcha-solver/turnstile/solve.py` guards every solve with a module
lock:

```python
_solve_lock = asyncio.Lock()
async with _solve_lock:                 # all solves run one at a time
    await cloakbrowser.launch_async(...)
```

So the server accepts concurrent requests (3 parallel `/solve` calls showed
`current: 3-4` tasks in `/status`) but executes them **one at a time**.

### Parallel solves do not help
Measured: 3 concurrent `/solve` calls → **all three timed out, WALL = 60s**
(not 180s). They ran concurrently but every one failed, because:

### The real bottleneck is the solve IP, not the worker count
Cloudflare rate-flags an IP that solves repeatedly. Measured:
- 3 sequential solves at **0s/5s** spacing → 1st OK, 2nd/3rd timeout.
- 3 sequential solves at **30s** spacing → 3/3 OK.
- After a burst, even the **first** solve times out for minutes (the IP is
  flagged); a restart does not change the IP, only an idle period clears it.
- Our solve IP is the local egress `114.10.39.69` (no proxy — datacenter
  proxies cannot pass Turnstile).

So throughput is bounded by **solves per IP per unit time**, not by workers.

## What parallel workers DO help

| Phase | Transport | Parallel helps? |
|---|---|---|
| Turnstile solve | local browser + lock | **No** — serialized |
| signup / create_consouser | HTTP | Limited — server `signup_velocity_exceeded` |
| farm turns (`append_prompt`) | HTTP | **Yes** |
| missions (`claim_*`) | HTTP | **Yes** |
| email OTP poll | HTTP | Yes |

## Recommended design

1. **Solve stays serial + cooldown** (`SOLVER_SOLVE_DELAY`, e.g. 45s). This is
   the throttle that keeps the IP unflagged.
2. **Farm/earn run in parallel** across accounts (HTTP, no lock). This is where
   a worker pool pays off.
3. **To parallelize registration**, you need **multiple solve IPs** — several
   solver instances, each behind a distinct residential proxy. With one IP,
   parallel registration just queues on the lock and then trips the rate flag.

## Implementation plan (farm-side parallelism)

- `run_registration` is already threaded (`ThreadPoolExecutor`), but the solve
  lock + cooldown serialize it — fine.
- Add parallelism to the **farm/earn** side: `scheduler.run_cycle` currently
  iterates accounts serially; parallelize it with a `ThreadPoolExecutor` bounded
  by `settings.max_concurrency`, each account using its own proxy. The solve is
  not involved in farming, so this scales.

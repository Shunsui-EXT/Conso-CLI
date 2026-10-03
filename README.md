# Conso Automation Pipeline (Conso-CLI)

Reverse-engineered automation pipeline for the **Conso: AI Usage Tracker**
Chrome extension (v0.1.4.0). It talks directly to the Conso backend (Supabase +
`conso.xyz`) — API-first, no browser needed for the account/turn logic.

It can **provision accounts**, **earn zaps** (missions + synthetic AI-usage
turns), **keep sessions alive**, and **run on a schedule** — all from the CLI.
While a run is active it renders a **single live panel** (header + progress +
zaps + log tail) and prints a summary when it finishes. There is no curses app,
no keybindings and no screen to navigate, so it runs fine over SSH, in cron, or
inside systemd.

> **Scope / disclaimer.** This is a reverse-engineering research tool that
> automates a third-party service. It is published for research and educational
> purposes. You are responsible for how you use it and for complying with the
> target service's terms and any applicable law. See [License](#license).

---

## Table of contents

1. [What it does](#what-it-does)
2. [Requirements](#requirements)
3. [Setup (step by step)](#setup-step-by-step)
4. [Configure (.env)](#configure-env)
5. [Verify it works](#verify-it-works)
6. [Quick start](#quick-start)
7. [CLI reference](#cli-reference)
8. [Common workflows](#common-workflows)
9. [How it works](#how-it-works)
10. [Live monitor](#live-monitor)
11. [Parallelism](#parallelism)
12. [Captcha solver](#captcha-solver)
13. [Limits & anti-abuse](#limits--anti-abuse)
14. [Troubleshooting](#troubleshooting)
15. [License](#license)

---

## What it does

| Phase | Command | Result |
|---|---|---|
| **Provision** | `register N` | Creates N accounts: Turnstile → signup → email OTP → user row → referral → onboarding |
| **Earn** | `earn` / `farm` | Claims daily/bonus missions and submits synthetic AI-usage turns for zaps |
| **Keep alive** | `session` / `recover` | Reuses/refreshes sessions without re-login; recovers via email OTP |
| **Automate** | `loop` / `pipeline` | Recurring daily cycle, or a one-shot register→earn chain |
| **Monitor** | (live) / `report` | Live single-panel output during a run, or a one-shot status table |

---

## Requirements

- **Python 3.10+** (tested on 3.14)
- A **Linux/macOS** shell (Windows works via WSL; native Windows is untested).
- Internet access to the Conso backend, `temp.tf` (email), and Cloudflare.
- The **internal captcha solver** needs Camoufox (a stealth Firefox). ~1.5 GB
  download on first fetch.

Everything else installs via `pip`.

---

## Setup (step by step)

Copy-paste the whole block. It works on a fresh machine.

```bash
# 1. Clone
git clone https://github.com/Shunsui-EXT/Conso-CLI.git conso
cd conso

# 2. Virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows (WSL): source .venv/bin/activate

# 3. Python dependencies
pip install -r requirements.txt

# 4. Captcha solver — internal Camoufox (recommended)
bash scripts/setup_internal_solver.sh
#   This installs camoufox + playwright==1.60, fetches the stealth browser
#   (~1.5 GB, one time), and verifies it launches.
#   Manual equivalent:
#     pip install 'camoufox[geoip]>=0.4.0'
#     pip install 'playwright==1.60'   # MUST be 1.60 to match the browser
#     python -m camoufox fetch

# 5. Config
cp .env.example .env               # defaults already work

# 6. Verify
python main.py doctor              # shows solver mode + browser report
python main.py solve               # solves one Turnstile (proves the solver)
python main.py test                # backend reachability + economy sanity
```

If step 6 prints `supabase: http=200` and `auth gate: ... captcha_failed`, you
are ready. `python main.py solve` should print a token (`solved: token_len=...`).

> **No `PYTHONPATH` needed.** `main.py` adds `src/` to the import path itself.
>
> **Optional:** `pip install -e .` gives you a `conso` command, so you can run
> `conso report` instead of `python main.py report`.

### Why `playwright==1.60`?

Camoufox ships a matching Firefox build. Playwright 1.61+ requires a newer
Camoufox browser than the one fetched, and it will re-download on every launch
(and hang). Pinning 1.60 keeps the fetched build valid.

---

## Configure (.env)

`cp .env.example .env` gives a **working default** (temp.tf email + internal
Camoufox solver). The values you may want to change:

| Variable | Default | Meaning |
|---|---|---|
| `VERIFIER` | `temptf` | Email provider for signup/OTP. `temptf` gives real gmail/outlook aliases. |
| `TEMPTF_PROVIDER` | `gmail` | `gmail` \| `outlook` \| `hotmail`. |
| `CAPTCHA_PROVIDER` | `internal` | `internal` (Camoufox) \| `service` (sidecar) \| `capsolver` \| `2captcha`. |
| `SOLVER_MAX_CONCURRENT` | `8` | Camoufox solve concurrency (8 is the measured sweet spot). |
| `SOLVER_SOLVE_DELAY` | `5` | Seconds between solves (keeps the IP from being flagged). |
| `DEFAULT_REFERRAL_CODE` | *(empty)* | Referral applied to every new account. |
| `PROXY_URLS` | *(empty)* | Inline proxy list (comma-separated HTTP/SOCKS5 URLs). |
| `PROXY_FILE` | *(empty)* | Path to a proxy file (one `http://user:pass@host:port` per line). Used when `PROXY_URLS` is empty. |
| `PROXY_PER_ACCOUNT` | `1` | Pin one proxy per account (spreads the signup-velocity limit). |
| `SOLVER_PROXY_FILE` | *(empty)* | Proxy for the **captcha solver only** (separate from `PROXY_FILE`). Leave empty normally. |
| `MIN_DELAY_SECONDS` / `MAX_DELAY_SECONDS` | `20` / `60` | Jittered pacing between turns on one account. |
| `REGISTER_MIN_DELAY_SECONDS` / `REGISTER_MAX_DELAY_SECONDS` | `2` / `6` | Jittered pacing between signups (registration only). |
| `SOLVER_SERIAL` | `1` | Serialise solves process-wide (see [Parallelism](#parallelism)). |

You do **not** need to touch `SUPABASE_URL` / `SUPABASE_KEY` — they are the
extension's public values, extracted and shipped.

---

## Verify it works

```bash
python main.py doctor
```

Example output:

```
doctor: dependency check
  playwright     present
  cloakbrowser   absent
  core src/ browser imports: NONE (browser-free)
doctor: CAPTCHA_PROVIDER=internal browser_free=False
doctor: transport=curl_cffi impersonate=chrome proxy=no
```

```bash
python main.py test
```

```
economy: in=34 out=97 q=4.3 zaps=0.1 usd=0.001557
supabase: http=200 email=True google=True signup_disabled=False autoconfirm=False
auth gate: http=400 error_code=captcha_failed (captcha required: True)
turnstile: sitekey=0x4AAAAAAEzmjKoKI6TA61_6 page=https://www.conso.xyz/verify-human
```

If `auth gate` shows `captcha_failed`, that is **correct** — it means the
captcha gate is live and your solver will be exercised.

---

## Quick start

**Fastest path — one account, fully provisioned, then earn:**

```bash
python main.py pipeline --register 1 --earn
```

You should see the [live monitor](#live-monitor) panel while it runs, then a
summary panel at the end:

```
22:48:01  farm: mansurkurtaran5+x@gmail.com daily budget filled (21.00)
22:48:01  register: mansurkurtaran5+x@gmail.com earned +21.0 zaps (status=ok)
╭─────────────────────────────── PIPELINE DONE ───────────────────────────────╮
│ registered    1                                                             │
│ active        1                                                             │
│ earned zaps   21.0                                                          │
╰─────────────────────────────────────────────────────────────────────────────╯
```

Redirect to a file and the panel is dropped automatically — you get plain logs:

```bash
python main.py pipeline --register 1 --earn > run.log 2>&1
```

---

## CLI reference

Every command is `python main.py <command>`. Add `--help` to any command for
its flags.

| Command | Purpose |
|---|---|
| `test` | Pre-flight: economy sanity, backend reachability, captcha posture, proxy health |
| `doctor` | Report the solver mode and whether the core is browser-free |
| `register N` | Provision N accounts (`--dry-run`, `--referral`, `--earn`, `--turns`) |
| `pipeline` | One-shot chain: `--register N --earn --loop --only-new --workers N` |
| `report` | Headless status (`--fmt table\|json\|csv`, `--refresh`) |
| `farm` | Missions + synthetic turns (`--turns`, `--email`, `--earn/--no-earn`) |
| `earn` | Claim missions/codes (`--list`, `--referral`, `--access`, `--missions`) |
| `loop` | Recurring daily cycle (`--once`, `--interval-hours`, `--workers`, `--daily-cap`) |
| `session` | Show/refresh stored sessions without re-login (`--force`) |
| `recover` | Refresh → password → email OTP recovery (`--email`) |
| `status` | Daily-limit / earning state per account (`--email`) |
| `solve` | Solve one Turnstile challenge (sanity check) |
| `proxies` | Health-check a proxy list (`--file`, `--limit`) |
| `export` | Dump the account store (`--fmt csv\|json`) |
| `verify` | Health-check the proxy pool |

---

## Common workflows

### A. Create 8 accounts and earn immediately

```bash
python main.py pipeline --register 8 --earn --only-new --workers 4
```

- `--register 8` — 8 new accounts (solver concurrency from `.env`).
- `--only-new` — earn only these 8, not the whole store.
- `--workers 4` — farm 4 accounts concurrently (HTTP-only, scales well).

### B. Daily earning for all existing accounts

```bash
python main.py loop --once            # one cycle now, exit
python main.py loop --interval-hours 24   # run every 24h (Ctrl-C to stop)
```

Each cycle claims missions and fills the daily zap budget for every account,
resuming safely across restarts (it records what already ran in `state.json`).

### C. Check everything is healthy

```bash
python main.py report --refresh       # table + summary, polls the server
python main.py report --fmt json | jq '.summary'
```

### D. A stuck account

```bash
python main.py recover --email user@example.com   # refresh -> password -> OTP
python main.py status --email user@example.com    # daily state, streak, banned
```

### E. Export accounts

```bash
python main.py export --fmt csv       # data/accounts.csv
python main.py export --fmt json      # prints JSON
```

---

## How it works

```
register:  temp.tf inbox (real gmail/outlook alias)         verifiers.py
           -> Turnstile solve (Camoufox, in-process)        internal_solver.py
           -> POST /auth/v1/signup + gotrue_meta_security {captcha_token}
           -> read 6-digit OTP from the inbox
           -> POST /auth/v1/verify {type:email,token}       client.py
           -> RPC create_consouser
           -> RPC redeem_referral_code
           -> PATCH consousers {consoname}   (onboarding)   client.py
           -> store                                         storage.py
farm:      session (cached / refreshed)
           -> economy.account_turn()                        economy.py
           -> RPC append_prompt {p_entry, p_base_zaps, p_spend_usd}
           -> credited_zaps (== submitted zaps)
```

**Order matters:** the referral is redeemed *before* onboarding, and the
consoname must be set *before* the first turn — otherwise the server bans the
account (`account_banned`).

**Captcha.** Conso enforces Cloudflare Turnstile on every Supabase auth
endpoint. The `internal` solver runs Camoufox in-process and renders the widget
from the api.js `onload` callback; the resulting token is accepted by Conso.

**Email.** Conso confirms accounts with a **6-digit OTP** (not a link).
`temp.tf` provides real `gmail.com` / `outlook.com` / `hotmail.com` plus-aliases
that pass Conso's email allowlist (disposable domains like mail.tm / ncaori are
blocked).

## Live monitor

`register`, `pipeline` and `loop` render one panel that refreshes in place.
It is not interactive — you never press anything; the run feeds it.

```
╭──────────────────────────────────────────────────────────────────────────╮
│  ██████╗ ██████╗ ███╗   ██╗███████╗ ██████╗                             │
│ ██╔════╝██╔═══██╗████╗  ██║██╔════╝██╔═══██╗   conso automation pipeline│
│ ██║     ██║   ██║██╔██╗ ██║███████╗██║   ██║   mode: register           │
│ ██║     ██║   ██║██║╚██╗██║╚════██║██║   ██║   * RUNNING                │
│ ╚██████╗╚██████╔╝██║ ╚████║███████║╚██████╔╝                            │
│  ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝╚══════╝ ╚═════╝                             │
│   phase captcha   elapsed 01:24   zaps 12.4   rate 8.9/min               │
│   [##################..........] 5/8  62%  eta 00:51                     │
│   ok 5  fail 0  accounts 8                                               │
│                                                                          │
│   + mansurkurtaran5+x@gmail.com captcha solved                           │
│   . mansurkurtaran5+x@gmail.com got code 123456                          │
╰──────────────────────────────────────────────────────────────────────────╯
```

What each row means:

| Row | Meaning |
|---|---|
| header | ASCII logo + tagline + `mode` (register / pipeline / loop) + status dot |
| `phase` | current stage of the run: `captcha` → `signup` → `otp` → `create` → `referral` → `consoname` → `missions` → `farm` |
| progress | completed/target, percent, ETA from item throughput |
| stats | `ok` / `fail` counts and the account target |
| log tail | last 6 events, coloured (`+` ok, `.` info, `!` warn, `-` fail) |

If stdout is not a terminal (cron, CI, a pipe) the panel is skipped and only
plain log lines are printed, so `python main.py loop --once > run.log` stays
readable.

---

## Parallelism

Registering N accounts already runs N workers (`ThreadPoolExecutor` +
`AdaptiveConcurrency`), but throughput is bounded by the **captcha**, not by
the pool. Measured on one residential IP:

| What runs in parallel | Result |
|---|---|
| 4 solves in parallel | **0/4** — every one times out |
| 3 solves serially, `SOLVER_SOLVE_DELAY=45` | **3/3** solved |
| 4 accounts, solves serialised | **4/4 active** |

Cloudflare hands out one usable challenge flow per source IP. Overlapping
solves fight over it and all of them lose. So by default:

- **solves are serialised process-wide** (`SOLVER_SERIAL=1`), while
- **everything else still overlaps** — signup, OTP, `create_consouser`,
  referral, onboarding run across `MAX_CONCURRENCY` workers.

What actually makes registration faster:

```bash
# 1. one proxy per account -> a different source IP per signup
PROXY_PER_ACCOUNT=1
PROXY_FILE=data/proxies.txt

# 2. keep registration off the 20-60s turn pacing
REGISTER_MIN_DELAY_SECONDS=2
REGISTER_MAX_DELAY_SECONDS=6

# 3. space the solves so the IP is not flagged
SOLVER_SOLVE_DELAY=45
```

Then:

```bash
python main.py register 8 --earn --workers 4
```

`--workers` parallelises the **earn** phase (pure HTTP, no captcha), which
scales freely.

### Making parallel solves actually work

The one-IP-one-flow limit disappears when each worker exits through a
**different** IP. Measured on this machine:

| Setup | Result |
|---|---|
| 2 parallel workers, both direct (one residential IP) | 1/2 solved |
| 2 parallel workers, two datacenter proxies | **0/2** |
| 3 serial, `SOLVER_SOLVE_DELAY=45` | 3/3 |

So parallel solves are possible, but only with distinct IPs that Cloudflare
accepts. Configure it:

```bash
SOLVER_PROXY_FILE=data/proxies.txt   # one URL per line
SOLVER_PROXY_MODE=worker             # each worker keeps one entry
SOLVER_SERIAL=0                      # let workers solve concurrently
```

`SOLVER_PROXY_MODE=worker` pins one entry per worker thread (verified: worker 0
and worker 1 got different IPs, each sticky across its attempts). A retry moves
that worker to the next entry instead of repeating the same failed IP.

**Datacenter proxies do not work here** — 100 proxies from one hosting ASN scored
0/2. The entries must be residential. With only one acceptable IP, leave
`SOLVER_SERIAL=1` and let registration overlap on everything except the solve.

---

## Captcha solver

**The solver starts automatically.** Every run (`register`, `pipeline`, `loop`)
calls `ensure_solver_ready()` first, which warms the browser (or starts the
sidecar) so the first solve is not paying launch latency.

**Solving is fail-fast and self-healing.** A rate-flagged IP never returns a
token, so instead of burning one long timeout per solve, each solve is split
into short attempts that rotate strategy:

```
attempt 1  -> direct IP        (SOLVER_ATTEMPT_TIMEOUT, default 45s)
attempt 2  -> next proxy       (SOLVER_PROXY_FILE, round-robin)
attempt 3  -> next proxy       (SOLVER_BACKOFF seconds apart, default 3s)
   ...     -> SOLVER_ATTEMPTS total (default 3)
```

Tune it in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `SOLVER_ATTEMPTS` | `3` | Attempts per solve before the account is marked failed. |
| `SOLVER_ATTEMPT_TIMEOUT` | `45` | Per-attempt budget in seconds. Lower = fail faster on a flagged IP. |
| `SOLVER_BACKOFF` | `3` | Seconds between attempts. |
| `SOLVER_PROXY_FILE` | *(empty)* | Proxy file used **by the solver**; each attempt rotates to the next line. |
| `SOLVER_MAX_CONCURRENT` | `8` | Parallel browser solves. |
| `SOLVER_SOLVE_DELAY` | `5` | Minimum seconds between solves (keeps the IP off Cloudflare's radar). |

**Provider chaining.** `CAPTCHA_PROVIDER` also accepts a comma-separated chain.
The first provider that returns a token wins, so a flagged local IP degrades
into a paid API instead of failing the registration:

```bash
CAPTCHA_PROVIDER=internal,capsolver,2captcha
```

Providers with no API key configured are skipped automatically, so
`internal,capsolver` is safe even before you buy a key.
- for `internal` — **warms the embedded Camoufox browser** before the first
  account, so the first solve is fast (no launch latency mid-run);
- for `service` — checks the sidecar health and **starts it automatically** via
  `scripts/start_solver.sh` if it is not running;
- for managed APIs / `none` — nothing to start.

You only need this one-time install (the browser binary):

```bash
bash scripts/setup_internal_solver.sh
```

That script:
1. `pip install 'camoufox[geoip]>=0.4.0'`
2. `pip install 'playwright==1.60'` (pinned — see below)
3. `python -m camoufox fetch` (downloads the stealth browser, ~1.5 GB, one time)
4. launches it once to verify

Then confirm it can solve:

```bash
python main.py solve      # -> solved: token_len=730 token=1.xxxx...
```

**If you prefer a managed captcha API** (browser-free, paid) instead of the
local browser, set `CAPTCHA_PROVIDER=capsolver` (or `2captcha`) and add the API
key in `.env`. No Camoufox download needed in that case.

**If you prefer the CloakBrowser sidecar** (Chromium, separate process):
`bash scripts/setup_solver.sh && bash scripts/start_solver.sh`, then
`CAPTCHA_PROVIDER=service`.

---

## Limits & anti-abuse

- **~10 credited turns per account per day.** Past that the server returns
  `200 0` (a soft flag) and bans on the next turn. The pipeline stops at the
  first zero-credit turn and clamps `turns` to the cap.
- **Per-credit ceiling (~3.2).** The server multiplies `p_base_zaps` by the
  account's `boost_factor` and credits 0 above the ceiling. The pipeline sizes
  each turn so the product lands just under it (≈2.5–3 zaps/turn).
- **Daily budget (~21 zaps).** The practical daily budget; the hard limit is
  ~30. The pipeline fills 21 and stops cleanly.
- **`signup_velocity_exceeded`** on rapid signups — spread by pinning a
  **different proxy per account** (`PROXY_PER_ACCOUNT=1`).
- **Turnstile rate-flagging** — an IP that solves too fast gets flagged; keep
  `SOLVER_SOLVE_DELAY` and lower `SOLVER_MAX_CONCURRENT` if solves time out.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ModuleNotFoundError: No module named 'conso'` | Run from the repo root (`python main.py ...`). `main.py` adds `src/` itself. |
| `Camoufox is not installed` | `bash scripts/setup_internal_solver.sh` (installs + fetches). |
| Solve hangs / re-downloads every launch | Playwright version mismatch — `pip install 'playwright==1.60'`. |
| `captcha_failed` on `test` | Expected — the captcha gate is active; the solver handles it. |
| `email_domain_not_allowed` on signup | temp.tf stopped giving allowed domains; try `TEMPTF_PROVIDER=outlook`. |
| Turnstile solves all time out (`rate-flagged`) | The solve IP is flagged by Cloudflare — wait ~30 min, raise `SOLVER_SOLVE_DELAY`, use residential proxies, or switch to `CAPTCHA_PROVIDER=capsolver` / `2captcha`. |
| `account_banned` | A turn was submitted before onboarding, or the daily cap was passed. Use `status` to inspect. |
| `signup_velocity_exceeded` | Too many signups from one IP — set `PROXY_FILE` (or `PROXY_URLS`) + `PROXY_PER_ACCOUNT=1`. Note: `PROXY_FILE` is for account traffic; `SOLVER_PROXY_FILE` is a different setting for the captcha solver only. |
| Turnstile solves all time out | The solve IP is flagged — wait ~30 min, or raise `SOLVER_SOLVE_DELAY`, or use residential proxies. |
| `report` shows 0 zaps | Stats are cached; add `--refresh` to poll the server. |

---

## Project layout

```
main.py                       entry point (python main.py <cmd>)
src/conso/
  constants.py                extracted constants (endpoints, model tables, prices)
  economy.py                  client-side zap/USD/quality math
  transport.py                curl_cffi TLS/JA3 fingerprinting + proxy pool
  client.py                   Supabase auth + PostgREST RPC + conso.xyz X-API
  captcha.py                  pluggable Turnstile solvers
  internal_solver.py          in-process Camoufox Turnstile solver
  verifiers.py                pluggable email adapters
  identity.py                 email/password/consoname generation
  session.py                  session manager (cached / refresh+persist / password / OTP)
  storage.py                  JSON+CSV account store, atomic writes, checkpoints
  concurrency.py              adaptive concurrency engine + jittered pacer
  limits.py                   daily-limit status + limit classification
  earnings.py                 missions, codes, account-row + summary
  pipeline.py                 registration + turn-farming orchestration
  scheduler.py                daily earn loop (resumable ledger)
  orchestrator.py             one-shot register -> earn -> loop chain
  ui.py                       Rich CLI output (banner, logs, summary, tables)
  monitor.py                  live single-panel monitor (header + progress + log tail)
  cli.py                      typer CLI (16 subcommands)
  config.py                   env/.env settings
scripts/                      setup_internal_solver.sh, setup/start_solver.sh (sidecar),
                              parallel_register.sh, fetch_extension.sh, solver_watchdog.sh
analysis/                     RE report, schema, solver studies, probes
extension_original/           downloaded CRX + unpacked extension (gitignored)
data/                         accounts.json / accounts.csv / state.json (gitignored)
```

---

## License

MIT — see [`LICENSE`](LICENSE). The reverse-engineered constants and protocol
details are provided for interoperability research.

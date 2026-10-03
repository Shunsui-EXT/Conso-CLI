# Conso Automation Pipeline (Conso-CLI)

Reverse-engineered automation pipeline for the **Conso: AI Usage Tracker**
Chrome extension (v0.1.4.0). It talks directly to the Conso backend (Supabase +
`conso.xyz`) — API-first, no browser needed for the account/turn logic.

It can **provision accounts**, **earn zaps** (missions + synthetic AI-usage
turns), **keep sessions alive**, and **run on a schedule**, with a Textual
terminal dashboard for live monitoring.

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
7. [Using the TUI](#using-the-tui)
8. [CLI reference](#cli-reference)
9. [Common workflows](#common-workflows)
10. [How it works](#how-it-works)
11. [Limits & anti-abuse](#limits--anti-abuse)
12. [Troubleshooting](#troubleshooting)
13. [License](#license)

---

## What it does

| Phase | Command | Result |
|---|---|---|
| **Provision** | `register N` | Creates N accounts: Turnstile → signup → email OTP → user row → referral → onboarding |
| **Earn** | `earn` / `farm` | Claims daily/bonus missions and submits synthetic AI-usage turns for zaps |
| **Keep alive** | `session` / `recover` | Reuses/refreshes sessions without re-login; recovers via email OTP |
| **Automate** | `loop` / `pipeline` | Recurring daily cycle, or a one-shot register→earn chain |
| **Monitor** | `dashboard` / `report` | Textual TUI, or a headless one-shot report |

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
> `conso dashboard` instead of `python main.py dashboard`.

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
| `PROXY_FILE` | *(empty)* | Path to a proxy list (one `http://user:pass@host:port` per line). |
| `PROXY_PER_ACCOUNT` | `1` | Pin one proxy per account (spreads the signup-velocity limit). |
| `MIN_DELAY_SECONDS` / `MAX_DELAY_SECONDS` | `20` / `60` | Jittered pacing between turns on one account. |

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

You should see a banner, coloured logs, and a summary panel:

```
╭────────────────────────── CONSO FARM  ·  pipeline ──────────────────────────╮
│ solver internal x16  verifier temptf  proxy 100 proxies  register 1 ...     │
╰─────────────────────────────────────────────────────────────────────────────╯
05:12:04  pipeline: registering 1 account(s)
05:12:18  register: xxx@gmail.com captcha solved
05:12:26  register: xxx@gmail.com got code 123456
05:12:27  [active] xxx@gmail.com :: registered
...
╭─────────────────────────────── PIPELINE DONE ───────────────────────────────╮
│ registered    1                                                             │
│ active        1                                                             │
│ earned zaps   12.07                                                         │
╰─────────────────────────────────────────────────────────────────────────────╯
```

**Interactive path — open the dashboard:**

```bash
python main.py            # no command -> opens the TUI
# or
python main.py dashboard
```

---

## Using the TUI

![Idle — portfolio health](docs/img/tui-idle.png)
![Running — live monitor](docs/img/tui-running.png)
![Pipeline](docs/img/tui-pipeline.png)
![Metrics](docs/img/tui-metrics.png)
![Alerts](docs/img/tui-alerts.png)
![Accounts](docs/img/tui-accounts.png)

On start a **menu** appears. Pick an action, fill the fields, press a button:

```
CONSO FARM — pilih aksi
  Register = akun baru + earn · Daily = earn akun lama · Monitor = lihat saja

  Jumlah akun (register):  [8]
  Turns per akun:          [10]
  Farm workers (paralel):  [2]
  Solver concurrent (0=env): [0]
  Referral code (kosong = dari .env): [ ]
  Only-new (earn akun baru saja)? 1=ya / 0=semua: [0]

  [ Register ]  [ Daily task ]  [ Monitor ]  [ Stop ]
```

- **Register** — provisions N new accounts, then earns for them.
- **Daily task** — runs the earn cycle (missions + turns) for existing accounts.
- **Monitor** — just watch, no run started.
- **Stop** — gracefully stops the active run.

**Views (keys):**

| Key | View | What it shows |
|---|---|---|
| `1` | Overview | **Idle**: portfolio health (store zaps, cap bar, top accounts, sparkline). **Running**: live monitor (progress, ETA, zaps/min sparkline, platform breakdown, errors). |
| `2` | Accounts | Table: `ST · EMAIL · STAGE · TOTAL · TODAY · STREAK · BOOST · PROXY · NOTE`, filterable. |
| `3` | Pipeline | Kanban of accounts by stage (queued → captcha → signup → otp → … → done / failed). |
| `4` | Metrics | zaps/min bar chart + current/avg/peak + success rate. |
| `5` | Alerts | Accounts needing attention (banned / failed / capped). |
| `l` | Logs | Realtime event stream. |
| `m` | Menu | Start another run. |
| `p` | — | Refresh server stats (total/today/streak/boost). |
| `x` | — | Stop the active run. |
| `q` | — | Quit. |

The TUI and the CLI share one event bus, so a headless `python main.py
pipeline ...` in another terminal feeds the same dashboard.

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
| `dashboard` | Launch the TUI control center |
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

### Captcha solver setup (internal)

The default solver runs **Camoufox** (a stealth Firefox) in-process — no sidecar
service. One-time setup:

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
| `account_banned` | A turn was submitted before onboarding, or the daily cap was passed. Use `status` to inspect. |
| `signup_velocity_exceeded` | Too many signups from one IP — set `PROXY_FILE` + `PROXY_PER_ACCOUNT=1`. |
| Turnstile solves all time out | The solve IP is flagged — wait ~30 min, or raise `SOLVER_SOLVE_DELAY`, or use residential proxies. |
| TUI looks blank | Press `m` for the menu, `2` for accounts, `p` to refresh stats. |
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
  tui/                        Textual dashboard (banner / events / state / app)
  cli.py                      typer CLI (16 subcommands)
  config.py                   env/.env settings
scripts/                      setup_internal_solver.sh, setup/start_solver.sh (sidecar),
                              parallel_register.sh, fetch_extension.sh, solver_watchdog.sh
analysis/                     RE report, schema, solver studies, probes
extension_original/           downloaded CRX + unpacked extension (gitignored)
data/                         accounts.json / accounts.csv / state.json (gitignored)
docs/img/                     TUI screenshots
```

---

## License

MIT — see [`LICENSE`](LICENSE). The reverse-engineered constants and protocol
details are provided for interoperability research.

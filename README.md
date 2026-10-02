# Conso Automation Pipeline

Reverse-engineered automation pipeline for the **Conso: AI Usage Tracker**
Chrome extension (v0.1.4.0). Talks directly to the Conso backend
(Supabase + `conso.xyz` API) — **API-first, no browser required for the account
and turn logic**.

Built by unpacking the extension and reproducing its client-side accounting
exactly. Full reverse-engineering write-up: [`analysis/RE_REPORT.md`](analysis/RE_REPORT.md).

> **Scope / disclaimer.** This is a reverse-engineering research tool that
> automates a third-party service. It is published for research and educational
> purposes. You are responsible for how you use it and for complying with the
> target service's terms and any applicable law. See [License](#license).

---

## What it does

| Phase | Command | Result |
|---|---|---|
| **Provision** | `register N` | Creates N accounts: Turnstile → signup → email OTP → user row → referral → onboarding |
| **Earn** | `earn` / `farm` | Claims daily/bonus missions and submits synthetic AI-usage turns for zaps |
| **Keep alive** | `session` / `recover` | Reuses/refreshes sessions without re-login; recovers via email OTP |
| **Automate** | `loop` / `pipeline` | Recurring daily cycle or a one-shot register→earn chain |
| **Monitor** | `dashboard` | Textual TUI: live batch, accounts, logs |

## From clone to ready (step by step)

```bash
# 1. Clone
git clone <your-repo-url> conso && cd conso

# 2. Virtualenv + deps
python3 -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Captcha solver (choose ONE)
#    a) internal Camoufox (recommended, no sidecar):
pip install 'camoufox[geoip]>=0.4.0' playwright==1.60
python -m camoufox fetch
#    b) OR the CloakBrowser sidecar:
# bash scripts/setup_solver.sh && bash scripts/start_solver.sh

# 4. Config
cp .env.example .env            # defaults already work (temptf + internal solver)

# 5. Verify everything is wired
python main.py doctor           # solver mode + browser-free report
python main.py test             # backend reachability + economy sanity

# 6. First account
python main.py pipeline --register 1 --earn
```

Then either use the CLI (`python main.py pipeline ...`) or the TUI
(`python main.py dashboard`).

> `main.py` adds `src/` to `sys.path` itself, so no `PYTHONPATH` is needed.
> (`pip install -e .` also works and gives you a `conso` console script.)

## Requirements

- Python 3.10+
- Runtime deps: `tiktoken`, `curl_cffi`, `httpx`, `python-dotenv`, `rich`,
  `typer`, `textual` (see [`requirements.txt`](requirements.txt)).
- The `internal` captcha solver additionally needs Camoufox:
  `pip install 'camoufox[geoip]>=0.4.0' && python -m camoufox fetch`
  (pin `playwright==1.60` to match the fetched browser build).
- The optional `service` solver sidecar needs the vendored
  `vendor/captcha-solver` (`bash scripts/setup_solver.sh`).

## Layout

```
main.py                       entry point (python main.py <cmd>)
src/conso/
  constants.py                every extracted constant (endpoints, model tables, prices)
  economy.py                  faithful port of the client-side zap/USD/quality math
  transport.py                curl_cffi TLS/JA3 fingerprinting + proxy pool
  client.py                   Supabase auth + PostgREST RPC + conso.xyz X-API
  captcha.py                  pluggable Turnstile solvers
  internal_solver.py          in-process Camoufox Turnstile solver (no sidecar)
  verifiers.py                pluggable email adapters
  identity.py                 email/password/consoname generation
  session.py                  session manager (cached reuse / refresh+persist / password)
  storage.py                  JSON+CSV account store, atomic writes, checkpoints
  concurrency.py              adaptive concurrency engine + jittered pacer
  limits.py                   daily-limit status + limit classification
  earnings.py                 missions, codes, account-row + summary
  pipeline.py                 registration + turn-farming orchestration
  scheduler.py                daily earn loop (resumable ledger)
  orchestrator.py             one-shot register -> earn -> loop chain
  tui/                        Textual dashboard (events / state / app)
  cli.py                      typer CLI (15 subcommands)
  config.py                   env/.env settings
extension_original/           downloaded CRX + unpacked extension (v0.1.4.0)
analysis/                     RE report, schema, solver studies, probes
scripts/                      solver setup/start, parallel register, watchdog
vendor/captcha-solver/        optional CloakBrowser sidecar (gitignored; 31 MB)
data/                         accounts.json / accounts.csv / state.json (gitignored)
```

## CLI reference

| Command | Purpose |
|---|---|
| `test` | Pre-flight: economy sanity, backend reachability, captcha posture, proxy health |
| `register N` | Provision N accounts (`--dry-run`, `--referral`, `--earn`, `--turns`) |
| `pipeline` | One-shot chain: `--register N --earn --loop --only-new --workers N` |
| `dashboard` | Launch the Textual TUI control center |
| `farm` | Missions + synthetic turns (`--turns`, `--email`, `--earn/--no-earn`) |
| `earn` | Claim missions/codes (`--list`, `--referral`, `--access`, `--missions`) |
| `loop` | Recurring daily cycle (`--once`, `--interval-hours`, `--workers`, `--daily-cap`) |
| `session` | Show/refresh stored sessions without re-login (`--force`) |
| `recover` | Refresh → password → email OTP recovery |
| `status` | Daily-limit / earning state per account |
| `solve` | Solve one Turnstile challenge (sanity check) |
| `proxies` | Health-check a proxy list |
| `doctor` | Report whether the pipeline is browser-free + what each layer uses |
| `export` | Dump the account store (`--fmt csv\|json`) |
| `verify` | Health-check the proxy pool |

## TUI dashboard

```bash
python main.py dashboard
```

Opens a Textual control center. On start it shows a **menu**; pick an action:

```
CONSO FARM — pilih aksi
  Register = akun baru + earn · Daily = earn akun lama · Monitor = lihat saja

  Jumlah akun (register):  [8]
  Turns per akun:          [10]
  Farm workers (paralel):  [2]
  Solver concurrent (0=env): [0]
  Referral code (kosong = dari .env): [ ]
  Only-new (earn akun baru saja)? 1=ya / 0=semua: [0]

  [ Register ]   [ Daily task ]   [ Monitor ]
```

- **Register** — provisions N new accounts, then earns for them (background
  thread; Overview updates live). Set a **referral code** here per run, or leave
  it blank to use `DEFAULT_REFERRAL_CODE` from `.env`.
- **Daily task** — runs the earn cycle (missions + turns) for existing accounts.
- **Monitor** — just watch, no run started.

**Views:**

- **Overview** — status, a progress bar, per-status account counts
  (active/running/failed/pending), zaps, rate/min, and the active
  solver/referral.
- **Accounts** — filterable table (type to filter by email/status); failures and
  running accounts are shown first.
- **Logs** — realtime event stream.

**Keys:**

| Key | View |
|---|---|
| `1` | Overview |
| `2` | Accounts |
| `l` | Logs |
| `m` | Menu (start another run) |
| `q` | Quit |

The TUI and the CLI share one event bus: a `python main.py pipeline ...` run in
another terminal emits the same events, so you can watch a headless run live.
Requires the `textual` dependency (in `requirements.txt`).

## Configuration

All via `.env` (see [`.env.example`](.env.example)).

| Var | Meaning |
|---|---|
| `VERIFIER` | `temptf` \| `mailtm` \| `ncaori` \| `imap` \| `tempmail` \| `none` |
| `TEMPTF_PROVIDER` | `gmail` \| `outlook` \| `hotmail` (real-provider aliases) |
| `CAPTCHA_PROVIDER` | `internal` \| `capsolver` \| `2captcha` \| `service` \| `manual` \| `none` |
| `SOLVER_MAX_CONCURRENT` | Camoufox solve concurrency (sweet spot **8**) |
| `SOLVER_SOLVE_DELAY` | Seconds between solves (keeps the IP unflagged) |
| `DEFAULT_REFERRAL_CODE` | Referral applied to every new account |
| `PROXY_FILE` / `PROXY_PER_ACCOUNT` | Proxy pool; one proxy pinned per account |
| `CONCURRENCY` / `MAX_CONCURRENCY` | Adaptive registration concurrency bounds |
| `IMPERSONATE` | curl_cffi TLS profile (`chrome`, `chrome124`, …) |

## How it works (end-to-end)

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
farm:      session (cached/refreshed)
           -> economy.account_turn()                        economy.py
           -> RPC append_prompt {p_entry, p_base_zaps, p_spend_usd}
           -> credited_zaps (== submitted zaps)
```

**Order matters:** the referral is redeemed *before* onboarding, and the
consoname must be set *before* the first turn — otherwise the server bans the
account (`account_banned`).

## Captcha (Turnstile)

Conso enforces Cloudflare Turnstile on **every** Supabase auth endpoint. Two
working backends:

- **`internal`** (default) — an in-process Camoufox (anti-detect Firefox)
  solver. Renders the widget from the api.js `onload` callback; the resulting
  token is accepted by Conso. No external service. Concurrency via
  `SOLVER_MAX_CONCURRENT` (measured sweet spot: **8**).
- **`service`** — the optional `vendor/captcha-solver` sidecar (CloakBrowser,
  Chromium) on `:8877`.
- **`capsolver` / `2captcha`** — managed APIs (pure HTTP, browser-free, paid).

```bash
python main.py doctor        # confirm which mode is active
python main.py solve         # solve one challenge
```

See [`analysis/INTERNAL_SOLVER.md`](analysis/INTERNAL_SOLVER.md) for the Camoufox
study and [`analysis/GROK_SOLVER_NOTES.md`](analysis/GROK_SOLVER_NOTES.md) for
the solver design comparison.

## Email verification

Conso confirms accounts with a **6-digit OTP** (not a link), sent to the signup
address. The pipeline reads it via a pluggable verifier:

- **`temptf`** (recommended) — [temp.tf](https://temp.tf) real
  `gmail.com` / `outlook.com` / `hotmail.com` plus-aliases. These pass Conso's
  email allowlist; disposable domains (mail.tm, ncaori) are **blocked**.
- `mailtm`, `ncaori`, `imap`, `tempmail` — alternative adapters.

## Limits & anti-abuse (measured)

- **~10 credited turns per account per day.** Past that the server returns
  `200 0` (soft flag) and bans on the next turn. The pipeline stops at the first
  zero-credit turn and clamps `turns` to the cap.
- **`signup_velocity_exceeded`** on rapid signups — spread by pinning a
  **different proxy per account** (`PROXY_PER_ACCOUNT=1`).
- **`rate_limited`** on repeated mission claims — idempotent per day.

Details: [`analysis/RE_REPORT.md`](analysis/RE_REPORT.md),
[`analysis/SOLVER_RELIABILITY.md`](analysis/SOLVER_RELIABILITY.md),
[`analysis/PARALLEL.md`](analysis/PARALLEL.md).

## Parallelism

- **Registration** is bounded by the captcha solve (one IP). One Camoufox
  browser saturates around `SOLVER_MAX_CONCURRENT=8`; going higher overloads
  the CPU (`register 10` at mc=16 → 9/10, slower).
- **Farm/earn** is pure HTTP and parallelizes across accounts
  (`--workers N`, `loop --workers N`).
- Multi-process registration (`scripts/parallel_register.sh`) works but does not
  beat a single process unless total solve concurrency ≈ CPU cores.

## Sessions (no re-login)

Supabase access tokens expire (~1h) and the **refresh token rotates on every
use**, so `SessionManager` persists the rotated token + expiry and reuses the
cached access token while valid. `farm`/`loop` run with `session=cached` or
`session=refreshed` — no Turnstile, no re-login. `recover` falls back to
password then email OTP.

## License

MIT — see [`LICENSE`](LICENSE). The reverse-engineered constants and protocol
details are provided for interoperability research.

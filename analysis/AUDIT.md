# Project Audit — Conso Automation Pipeline

Scope: full feature inventory + end-to-end flow of the pipeline in this repo.
Target: Conso: AI Usage Tracker (Chrome MV3 v0.1.4.0), backend Supabase +
`conso.xyz`.

## 1. What this project is

An API-first automation pipeline that talks directly to the Conso backend
(no browser) to:

1. **Provision accounts** — sign up, confirm by email OTP, create the user row,
   complete onboarding, redeem a referral.
2. **Earn zaps** — submit synthetic AI-usage turns and claim missions/codes.
3. **Keep accounts alive** — persist and auto-refresh sessions.

It was built by reverse-engineering the extension (see `RE_REPORT.md`,
`SCHEMA.md`) and reproducing its client-side accounting exactly.

## 2. Feature inventory (by module)

| Module | LOC | Responsibility |
|---|---|---|
| `constants.py` | 137 | Every extracted constant: endpoints, Supabase key, model→zap multipliers, per-model USD prices, image-token costs, dedup keys, tokenizer. |
| `economy.py` | 427 | Faithful port of the client-side math: `compute_zaps`, `compute_usd_cost`, prompt-quality scoring, image-token estimation, `js_isoformat`, `account_turn`/`build_entry`. |
| `transport.py` | 162 | curl_cffi TLS/JA3 fingerprinting; health-checked proxy pool with failure quarantine; retry + backoff; per-request timeout. |
| `client.py` | 310 | Supabase GoTrue auth (signup, password, OTP, id_token, refresh) + PostgREST RPCs + conso.xyz X-API. |
| `captcha.py` | 267 | Pluggable Turnstile solvers: `service` (self-hosted sidecar), `capsolver`, `2captcha`, `manual`, `none`. |
| `verifiers.py` | 414 | Pluggable email: `temptf`, `mailtm`, `ncaori`, `imap`, `tempmail`, `none`. |
| `identity.py` | 71 | Email/password/display-name/consoname generation. |
| `session.py` | 150 | SessionManager: cached-token reuse → refresh+persist rotation → password fallback. |
| `storage.py` | 126 | Thread-safe JSON+CSV account store, atomic writes, run checkpoints. |
| `concurrency.py` | 70 | Adaptive concurrency (backoff on 429/403) + jittered pacer. |
| `pipeline.py` | 344 | `register_account`, `run_registration` (concurrent, resumable), `farm_turns_for_account`, synthetic-turn generator. |
| `earnings.py` | 189 | Mission/code claims, account-row reader, earnings summary. |
| `limits.py` | 120 | Daily-limit status (`daily_zaps_earned`/date), limit classification, headroom. |
| `scheduler.py` | 190 | Daily earn loop: missions + capped turns on an interval, resumable ledger. |
| `cli.py` | ~400 | Typer CLI: 10 subcommands. |

Total: 17 modules, ~3,400 LOC (src) + 5 test files.

## 3. CLI surface

| Command | Purpose |
|---|---|
| `test` | Pre-flight: economy sanity, backend reachability, captcha posture, proxy health. |
| `register N` | Provision N accounts (adaptive concurrency, resumable, `--dry-run`, `--referral`). |
| `farm --turns N` | Claim missions then submit turns (`--earn/--no-earn`, `--email`, `--dry-run`). |
| `earn` | Claim daily/bonus missions and codes (`--list`, `--referral`, `--access`, `--missions`). |
| `status` | Show daily-limit / earning state (daily zaps, streak, boost, banned). |
| `loop` | Run the daily earn cycle on a schedule (`--once`, `--interval-hours`, `--daily-cap`). |
| `session` | Show/refresh stored sessions without re-login (`--force`). |
| `solve` | Solve one Turnstile via the configured solver (sanity check). |
| `export --fmt csv\|json` | Dump the account store. |
| `verify` | Health-check the proxy pool. |

## 4. End-to-end flow

### 4.1 Registration (`register_account`)

```
1. identity          build email/password/consoname            identity.py
                     (or verifier provisions its own inbox)
2. inbox             temp.tf GET /api/account -> real gmail alias   verifiers.py
3. captcha           POST :8877/solve (real_page Turnstile)     captcha.py
4. signup            POST /auth/v1/signup + gotrue_meta_security.captcha_token
5. OTP               poll temp.tf /api/check -> 6-digit code    verifiers.py
6. verify            POST /auth/v1/verify {type:email,token}    client.py
                     -> access_token + refresh_token + expires_at
7. user row          RPC create_consouser {p_google_id}         client.py
8. referral          RPC redeem_referral_code (DEFAULT_REFERRAL_CODE)
9. onboarding        PATCH consousers {consoname}               client.py
10. persist          Store.add(record)                          storage.py
```

Order matters: **referral before onboarding** (extension shows the referral
screen right after `createConsouser`); **consoname before any turn** (else
`account_banned`).

`run_registration` drives this concurrently with adaptive concurrency,
jittered pacing, and a `state.json` checkpoint so runs resume without
duplicating.

### 4.2 Farming (`farm_turns_for_account`)

```
1. session           SessionManager.ensure_session()
                       cached access token  -> source=cached
                       refresh + persist    -> source=refreshed
                       password (Turnstile) -> source=password
2. per turn          economy.account_turn()  -> inputTokens/outputTokens/
                       promptQuality/zaps/spend_usd
                     economy.build_entry()   -> p_entry (JS timestamp)
3. submit            RPC append_prompt {p_entry, p_base_zaps, p_spend_usd}
                     -> credited zaps (== submitted value)
4. pacing            jittered delay between turns; back off on 429/403
```

`farm` (CLI) runs `run_earnings` first (missions), then turns.

### 4.3 Earning (`run_earnings`)

```
before    read consousers row (total_zaps) + leaderboard rank
skip      get_todays_mission_claims -> set of already-claimed ids
claim     daily-checkin-v1     claim_daily_mission   +2
          tweet-about-conso-v1 claim_daily_mission   +3
          article-about-conso-v1 claim_bonus_mission +15
redeem    referral_code / access_code (optional)
after     read row + rank -> delta
```

Idempotent per day: repeats return `rate_limited` → treated as already-claimed.

### 4.4 Daily loop (`DailyLoop.run_cycle`)

```
for each active account:
  skip if already ran today (state.json ledger)
  ensure_session (refresh; Turnstile only if chain broke)
  if banned / over daily_cap -> skip
  run_earnings (missions)                earnings.py
  farm_turns_for_account (stops at first zero-credit turn)  pipeline.py
  record result in state.json daily_loop.<date>.<email>
sleep interval, repeat (SIGINT/SIGTERM stops gracefully)
```

### 4.5 Anti-abuse limits (measured)

- **Daily turn cap ~10/account**: `append_prompt` returns `200 0` from turn ~11
  and `account_banned` after. Count-based (identical under hammering and 3s
  pacing). The pipeline stops at the first zero-credit turn.
- **Mission claims**: idempotent per day (`rate_limited` / `already claimed`).
- **`signup_ip` / `signup_ip_subnet`** tracked per account → likely per-IP
  registration limits.
- Tweet/article mission claims are **not server-validated** (succeed without a
  real X post) — a server-side gap.

## 5. Data model touched

- **Supabase tables**: `consousers` (own row), `bonus_missions` (catalogue,
  anon-readable), `leaderboard_top` (anon-readable), `prompt_events`,
  `referrals`, `access_codes`, `mission_claims` (RLS-protected).
- **RPCs used**: `create_consouser`, `append_prompt`, `redeem_referral_code`,
  `redeem_access_code`, `claim_daily_mission`, `claim_bonus_mission`,
  `get_todays_mission_claims`, `get_my_leaderboard_rank`,
  `get_lifetime_platform_stats`, `get_daily_activity`, `get_hourly_activity`,
  `get_avg_session_minutes`, `is_consoname_available`.
- **conso.xyz API**: `/api/extension/x/{start,callback}`, `/api/access/*`.
- **Local store**: `data/accounts.json` (+ `.csv`), `data/state.json`.
  Record fields: email, password, consoname, user_id, access_token,
  refresh_token, expires_at, refreshed_at, proxy, created_at, status,
  total_zaps, note.

## 6. Verification status

**Verified against the live backend:**
- Registration end-to-end (temp.tf alias → Turnstile → signup → OTP → verify →
  create_consouser → onboarding), repeated clean runs.
- Farming: credited zaps equal submitted values exactly (server does not clamp
  `p_base_zaps`/`p_spend_usd`); rank improves.
- Missions: all three claimed; total_zaps 2.84 → 24.25, rank 27864 → 18802.
- Session persistence: repeated refreshes rotate the token and the chain stays
  valid; farm runs `source=cached` with no re-login.
- 15 unit tests pass; all modules compile.

**Open / unverified:**
- Rate limits at scale (many accounts), daily caps (`daily_zaps_earned`),
  whether repeated plus-aliases on one mailbox get flagged.
- `signup_ip` / `signup_ip_subnet` tracking suggests per-IP limits.
- Access-code quota is exhausted (`15000/15000`).
- Turnstile solve is stochastic (~1/2–5); registration can stall if the solver
  sidecar times out.

## 7. Operational notes

- Requires the solver sidecar running (`scripts/start_solver.sh`, `:8877`).
- `.env` holds working config: `VERIFIER=temptf`, `CAPTCHA_PROVIDER=service`,
  `DEFAULT_REFERRAL_CODE=CONSO-GG53G`.
- `vendor/` (solver, 31MB ONNX) is gitignored; fetch via `scripts/setup_solver.sh`.
- Git history: 8 commits.

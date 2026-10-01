# Conso Automation Pipeline

Reverse-engineered automation pipeline for the **Conso: AI Usage Tracker**
Chrome extension (v0.1.4.0). Talks directly to the Conso backend (Supabase +
`conso.xyz` API) — API-first, no browser required for turn submission.

See `analysis/RE_REPORT.md` for the full reverse-engineering write-up.

## Layout

```
main.py                     entry point (python main.py <cmd>)
src/conso/
  constants.py              every extracted constant (endpoints, model tables, prices)
  economy.py                faithful port of the client-side zap/USD/quality math
  transport.py              curl_cffi fingerprinting + proxy pool with quarantine
  client.py                 Supabase auth + RPC + conso.xyz X-API client
  identity.py               email/password/consoname generation
  verifiers.py              pluggable email-verification adapters (imap/tempmail)
  storage.py                JSON+CSV account store, atomic writes, checkpoints
  concurrency.py            adaptive concurrency engine + jittered pacer
  pipeline.py               registration + turn-farming orchestration
  cli.py                    typer CLI
extension_original/         downloaded CRX + unpacked extension
analysis/                   reverse-engineering report
data/                       accounts.json / accounts.csv / state.json (gitignored)
```

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill EMAIL_DOMAIN etc.
```

## Usage

```bash
export PYTHONPATH=src

# Pre-flight: transport, backend reachability, economy sanity
python main.py test

# Provision accounts (resumable; dry-run generates identities only)
python main.py register 10 --dry-run
python main.py register 10 --referral <CODE>

# Submit synthetic turns to earn zaps
python main.py farm --turns 20 --dry-run
python main.py farm --turns 20 --email user@domain

# Export / proxy health
python main.py export --fmt csv
python main.py verify
```

## Configuration

All via `.env` (see `.env.example`). Key values:

| Var | Meaning |
|---|---|
| `EMAIL_DOMAIN` | Catch-all / disposable domain for account emails (**required**) |
| `PROXY_URLS` | Comma-separated HTTP/SOCKS5 pool (empty = direct) |
| `VERIFIER` | `none` \| `imap` \| `tempmail` |
| `CONCURRENCY` / `MAX_CONCURRENCY` | Adaptive concurrency bounds |
| `MIN_DELAY_SECONDS` / `MAX_DELAY_SECONDS` | Jittered pacing between requests |
| `IMPERSONATE` | curl_cffi TLS profile (`chrome`, `chrome124`, …) |

## Status

- **Verified:** extension extraction, backend endpoints, auth settings, RPC
  signatures, zap formula, dedup semantics, tokenizer encoding, Turnstile
  sitekey + enforcement, id_token captcha bypass.
- **Captcha gate:** Conso enforces Cloudflare Turnstile on **all** Supabase auth
  endpoints. Registration/login need a solved token (`CAPTCHA_PROVIDER`), or a
  real Google id_token via `sign_in_id_token` (captcha-free).
- **Unverified:** whether the server clamps client-supplied `p_base_zaps` /
  `p_spend_usd`, and any server-side rate limits on `append_prompt`. The
  pipeline is built to submit exactly what the real client would; server
  clamping (if any) is observed at runtime.

## End-to-end flow

```
register:  identity -> [Turnstile solve] -> /auth/v1/signup -> confirm (verifier)
           -> /auth/v1/token?grant_type=password -> create_consouser -> store
farm:      refresh/login -> economy.account_turn() -> append_prompt -> credited_zaps
```

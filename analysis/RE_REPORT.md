# Conso AI Usage Tracker — Reverse Engineering Report

Target: `Conso: AI Usage Tracker` v0.1.4.0 (Chrome MV3)
Extension ID: `bjibbmkefnaamkenamdppfengeepadpi`
Artifact: `extension_original/conso-v0.1.4.0.crx` (15,791,733 bytes) → `extension_original/unpacked/`

## 1. Executive Summary

Conso is a client-side AI-usage tracker. Content-script injectors monkey-patch
`window.fetch` on four AI platforms, tee each completion request/response, and
post a `CONSO_DETECTED_TURN` message to a bridge script. The bridge computes a
zap credit and USD spend **locally**, deduplicates the turn, and writes it to a
Supabase backend via the `append_prompt` RPC.

Two consequences matter for automation:

1. **The accounting is client-side.** The extension — not the server — decides
   how many zaps a turn is worth and what it "cost". The backend RPC receives
   the already-computed numbers (`p_base_zaps`, `p_spend_usd`).
2. **Authentication is pluggable.** The extension uses Google OAuth, but the
   Supabase project has email/password auth enabled with open signup, so
   accounts can be provisioned without any OAuth provider.

## 2. Architecture

```
 AI platform tab (chatgpt.com / claude.ai / gemini.google.com / perplexity.ai)
   ├─ *-injector.js   (world: MAIN, document_start)  -> patches window.fetch,
   │                     tees the SSE stream, postMessage(CONSO_DETECTED_TURN)
   └─ *-bridge.js     (isolated)                     -> listens for the message,
                         dedups, computes zaps, calls append_prompt
 Extension service worker (background.js)
   └─ Supabase JS client (GoTrue auth + PostgREST RPC) -> jzxlayjrsdbyzykuiqns.supabase.co
 conso.xyz Next.js API
   └─ /api/extension/x/{start,callback}  (X OAuth proxy)
   └─ /verify-human                      (captcha flow)
```

## 3. Backend

| Surface | Value |
|---|---|
| Supabase URL | `https://jzxlayjrsdbyzykuiqns.supabase.co` |
| Publishable key | `sb_publishable_clAiRg6ffCznEAtg_bn19Q_yY0W5Hyd` |
| Auth | email/password enabled, Google enabled, `disable_signup=false`, `mailer_autoconfirm=false` |
| Table | `consousers` (RLS: anon denied → `42501`) |
| Related tables (leaked by PostgREST hints) | `prompt_events`, `bonus_missions` |
| Next.js API | `https://www.conso.xyz/api/extension/x/{start,callback}` |

### RPCs (PostgREST `/rest/v1/rpc/<name>`)

| RPC | Args | Purpose |
|---|---|---|
| `append_prompt` | `p_entry`, `p_base_zaps`, `p_spend_usd` | Record a turn and credit zaps |
| `create_consouser` | `p_google_id` | Create the user record |
| `is_consoname_available` | `p_consoname` | Display-name availability |
| `redeem_referral_code` | `p_code` | Apply a referral code |
| `redeem_access_code` | `p_code` | Apply an access code |
| `claim_daily_mission` | `p_mission_id`, `p_claim_ref` | Claim a daily mission |
| `claim_bonus_mission` | `p_mission_id` | Claim a bonus mission |
| `get_todays_mission_claims` | — | Today's claim state |
| `get_avg_session_minutes` | `p_platform`, `p_days` | Stats |
| `get_daily_activity` | `p_start`, `p_end`, `p_platform` | Stats |
| `get_hourly_activity` | `p_platform` | Stats |
| `get_lifetime_platform_stats` | — | Stats |
| `get_my_leaderboard_rank` | — | Rank |

## 4. Authentication

### Google (extension path)
`chrome.identity.launchWebAuthFlow` → `accounts.google.com/o/oauth2/v2/auth`
with `response_type=id_token`, `client_id=77519766304-hfrhb5gc5dp1kqano1ghsgroa2rf3ei1`,
`redirect_uri=<chromiumapp.org>`, `nonce=<random>` → `id_token` from the redirect
hash → `supabase.auth.signInWithIdToken({provider:"google", token, nonce})`.
The OAuth client is bound to the extension's redirect URI and cannot be reused
outside a Chrome extension.

### Email/password (automation path)
`POST /auth/v1/signup` → `POST /auth/v1/token?grant_type=password`. If email
confirmation is required, fetch the confirm link via an IMAP/catch-all verifier.

### X (optional linking)
`POST https://www.conso.xyz/api/extension/x/start` → returns `{url}` (X OAuth2
PKCE authorize URL) → user authorizes → redirect carries `code`+`state` →
`POST /api/extension/x/callback` body `{code, state}` → `{ok, xUserId, username}`.

### Captcha (Cloudflare Turnstile)
`chrome.identity.launchWebAuthFlow` → `https://www.conso.xyz/verify-human?redirect_uri=<...>`
→ redirect carries `captcha_token`.

The widget is Cloudflare Turnstile: page `https://www.conso.xyz/verify-human`,
script `https://challenges.cloudflare.com/turnstile/v0/api.js`, **sitekey
`0x4AAAAAAEzmjKoKI6TA61_6`**. The token is consumed by Supabase, and it is
enforced on **every** auth endpoint:

```
POST /auth/v1/signup                       -> 400 captcha_failed
POST /auth/v1/token?grant_type=password    -> 400 captcha_failed
POST /auth/v1/otp                          -> 400 captcha_failed
```

The token travels in the GoTrue envelope `gotrue_meta_security.captcha_token`
(confirmed: a dummy token changes the error from `no captcha_token found` to
`invalid-input-response`). Consequence: email/password signup and login
**require a solved Turnstile**.

The one captcha-free auth path is the id_token grant:

```
POST /auth/v1/token?grant_type=id_token  {provider:"google", id_token}
```

which is processed without any captcha check (an invalid token yields
`"Bad ID token"`, not `captcha_failed`). So a real Google/OIDC id_token is a
Turnstile bypass — but the extension's OAuth client is bound to the extension's
`chromiumapp.org` redirect and cannot be reused off-extension.

## 5. Turn detection

Injectors patch `window.fetch`, match a POST completion endpoint, clone the
response, read the SSE stream, and post:

```js
window.postMessage({ source: "conso-extension", type: "CONSO_DETECTED_TURN", payload }, origin)
```

Payload fields: `platform`, `model`, `conversationId`, `promptText`,
`responseText`, `promptImages`, `responseImages`, `responseMediaText`,
`inputFilesCount`, `hasNonImageAttachment`, `generatedFilesCount`, `requestKey`.

Request matchers:

| Platform | Match |
|---|---|
| chatgpt | `POST /backend-api/(f/)?conversation$` |
| claude | `POST /api/organizations/<id>/chat_conversations/<id>/completion` |
| gemini | gemini completion endpoint |
| perplexity | perplexity completion endpoint |

Dedup: `chrome.storage.local["conso:processedPrompts:" + platform]` = ring
buffer of the last **500** `requestKey`s. `requestKey` = `conversationId::requestId`
when a request id exists, else `conversationId::promptLen::responseLen`.

## 6. Zap economy

Constants (bridge.js): `TOKEN_DIVISOR=1e4`, `DEFAULT_MODEL_MULTIPLIER=0.25`,
`PLATFORM_MULTIPLIER={chatgpt:.3, claude:.3, gemini:.1, perplexity:.3}`,
`OUTPUT_ZAP_FACTOR=2.5`, `MEDIA_ZAP_FACTOR=0.5`, `IMAGE_TOKEN_USD=0.04`.

```
mult   = MODEL_MULTIPLIER[model]  (fallback: platform mult → 0.25)
zaps   = round2( ((in + out - media)/1e4 * quality * mult * 2.5)
               + (media/1e4 * quality * mult * 0.5) )
if has_non_image_attachment: zaps = round2(zaps * 3)
spend  = in/1e6 * price_in + out/1e6 * price_out   # USD
```

- `in`  = `tokens(promptText)` + image tokens of prompt images
- `out` = `tokens(responseText)` + generated-image token-equivalent + `tokens(responseMediaText)`
- `media` = prompt image tokens + generated-image token-equivalent + media text tokens
- Tokenizer: `gpt-tokenizer` **o200k_base** (Python: `tiktoken.get_encoding("o200k_base")`).
- `quality` ∈ [0.0, 5.0], computed from prompt-text word count band (60%),
  structural signals (30%), lexical type-token ratio (10%), combined as
  `min(1 + weighted**0.45 * 4, 5)`.

Full constant tables live in `src/conso/constants.py`; the ported functions in
`src/conso/economy.py`.

## 7. Anti-abuse surface

| Control | Behaviour |
|---|---|
| Dedup | 500-key ring buffer per platform (`requestKey`) |
| Quality gate | Low-quality prompts score near 0 → near-zero zaps |
| Attachment bonus | Non-image attachments multiply zaps by 3 (client-side) |
| RLS | `consousers` denies anon select; RPCs are security-definer |
| Captcha | `/verify-human` gated flow for certain actions |
| Auth | Supabase JWT required for user-scoped RPCs |

## 8. Automation implications

- Turn submission is a **direct RPC call**; no browser is required.
- Unique `requestKey`s defeat the client-side dedup (the server does not see
  the key — it is only a local cache guard — so server-side limits, if any, are
  the real constraint).
- The `p_base_zaps` and `p_spend_usd` values are client-supplied; whether the
  server clamps them is the key open question (see verification results).

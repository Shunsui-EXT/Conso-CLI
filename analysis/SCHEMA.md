# Conso Backend Schema (discovered)

Live PostgREST discovery against `https://jzxlayjrsdbyzykuiqns.supabase.co`
using the extension's publishable key. Codes are HTTP status from
`GET /rest/v1/<table>?limit=1`.

## Tables

| Table | Status | Notes |
|---|---|---|
| `consousers` | 401 (RLS) | Primary user record. `Grant SELECT ... TO anon` hint confirms RLS is on. |
| `prompt_events` | 401 (RLS) | One row per recorded turn (written by `append_prompt`). |
| `bonus_missions` | 200 | **Anon-readable.** Full mission catalogue. |
| `mobile_daily_missions` | 401 (RLS) | Daily missions (mobile variant). |
| `mission_claims` | 401 (RLS) | Mission claim ledger. |
| `referrals` | 401 (RLS) | Referral relations. |
| `access_codes` | 401 (RLS) | Access codes. |
| `leaderboard_top` | 200 | **Anon-readable.** Public leaderboard snapshot. |
| `user_platform_counts` | 401 (RLS) | Per-platform prompt counts. |
| `user_daily_platform_stats` | 401 (RLS) | Daily per-platform aggregates. |
| `user_hourly_platform_stats` | 401 (RLS) | Hourly per-platform aggregates. |

`bonus_missions` schema (columns): `id, active, zaps_reward, card_heading,
card_description, modal_title, modal_description, instructions[], min_length,
required_mentions[], connect_heading, connect_description,
connect_button_label, created_at, kind, frequency, min_words,
required_attachments`.

Seeded missions:

| id | kind | frequency | zaps_reward | requirement |
|---|---|---|---|---|
| `daily-checkin-v1` | checkin | daily | 2 | open + check in |
| `tweet-about-conso-v1` | tweet | daily | 3 | tweet + `@conso_xyz` + 1 attachment |
| `article-about-conso-v1` | tweet | once | 15 | X article ≥250 words + 2 attachments + `@conso_xyz` |

`leaderboard_top` columns: `id, consoname, total_zaps, current_streak,
x_username, x_profile_image, referral_count, platforms[]`.

## RPCs (PostgREST `/rest/v1/rpc/<name>`)

All confirmed to exist (empty-body call returns
`PGRST202: Could not find the function ... without parameters`, meaning the
function exists but requires arguments).

| RPC | Arg names (from client code) | RLS signal on empty call |
|---|---|---|
| `append_prompt` | `p_entry`, `p_base_zaps`, `p_spend_usd` | needs args |
| `create_consouser` | `p_google_id` | needs args |
| `is_consoname_available` | `p_consoname` | needs args |
| `claim_daily_mission` | `p_mission_id`, `p_claim_ref` | needs args |
| `claim_bonus_mission` | `p_mission_id` | needs args |
| `redeem_referral_code` | `p_code` | needs args |
| `redeem_access_code` | `p_code` | needs args |
| `get_todays_mission_claims` | — | returns (anon) |
| `get_my_leaderboard_rank` | — | returns (anon) |
| `get_lifetime_platform_stats` | — | → `user_platform_counts` (denied) |
| `get_hourly_activity` | `p_platform` | → `user_hourly_platform_stats` (denied) |
| `get_avg_session_minutes` | `p_platform`, `p_days` | → `prompt_events` (denied) |
| `get_daily_activity` | `p_start`, `p_end`, `p_platform` | needs args |

RPCs execute as security-definer functions over the RLS-protected tables, so
the anon role reaches the tables only *through* the RPCs, never directly.

## Auth

`GET /auth/v1/settings`:

```json
{ "external": {"email": true, "google": true}, "disable_signup": false, "mailer_autoconfirm": false }
```

- Email/password signup is **open** (`disable_signup=false`).
- Email confirmation is **required** (`mailer_autoconfirm=false`) → a verifier
  (IMAP/catch-all/TempMail) must fetch the confirm link.
- Email OTP is also supported by the popup (`signInWithOtp`), which stashes
  `{email, sentAt}` under `chrome.storage.session["conso:pendingEmailOtp"]`.

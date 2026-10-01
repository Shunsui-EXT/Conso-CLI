#!/usr/bin/env python3
"""Isolate the account_banned trigger: check before vs after append_prompt."""
import json, re, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone

sys.path.insert(0, "/home/elzanom/Documents/WEB3/CONSO/src")
from conso import economy  # noqa: E402

KEY = "sb_publishable_clAiRg6ffCznEAtg_bn19Q_yY0W5Hyd"
SB = "https://jzxlayjrsdbyzykuiqns.supabase.co"
RU = "https://bjibbmkefnaamkenamdppfengeepadpi.chromiumapp.org/"
VURL = "https://www.conso.xyz/verify-human?redirect_uri=" + urllib.parse.quote(RU, safe="")


def http(url, method="GET", body=None, headers=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


def solve():
    for _ in range(8):
        try:
            st, b = http("http://127.0.0.1:8877/solve", "POST",
                         {"type": "turnstile", "real_page": True,
                          "sitekey": "0x4AAAAAAEzmjKoKI6TA61_6", "url": VURL, "timeout_s": 100},
                         timeout=140)
            t = json.loads(b).get("token")
            if t:
                return t
        except Exception:
            pass
    return None


def read_otp(addr):
    for _ in range(12):
        st, b = http("https://temp.tf/api/check", "POST", {"email": addr, "wait": True}, timeout=45)
        try:
            for m in json.loads(b).get("data", []):
                mm = re.search(r"\b(\d{6})\b", m.get("body", ""))
                if mm:
                    return mm.group(1)
        except Exception:
            pass
        time.sleep(2)
    return None


def provision(provider="outlook"):
    st, b = http(f"https://temp.tf/api/account?providers={provider}&dot=0&plus=1", timeout=30)
    addr = json.loads(b)["email"]
    pw = "Aa1!Veltrix9Zq"
    tok = solve()
    if not tok:
        return None
    st, b = http(f"{SB}/auth/v1/signup", "POST",
                 {"email": addr, "password": pw, "gotrue_meta_security": {"captcha_token": tok}},
                 headers={"apikey": KEY})
    if not json.loads(b).get("id"):
        print("  signup failed:", b[:150])
        return None
    otp = read_otp(addr)
    if not otp:
        return None
    st, b = http(f"{SB}/auth/v1/verify", "POST", {"type": "email", "email": addr, "token": otp},
                 headers={"apikey": KEY})
    d = json.loads(b)
    if not d.get("access_token"):
        return None
    return {"email": addr, "uid": d["user"]["id"], "at": d["access_token"]}


def main():
    provider = sys.argv[1] if len(sys.argv) > 1 else "outlook"
    a = provision(provider)
    if not a:
        print("provision failed")
        return 1
    auth = {"apikey": KEY, "Authorization": f"Bearer {a['at']}"}
    print(f"account: {a['email']} uid={a['uid'][:8]}")

    st, b = http(f"{SB}/rest/v1/rpc/create_consouser", "POST", {"p_google_id": a["uid"]}, headers=auth)
    print(f"1) create_consouser BEFORE any turn: {st} {b[:200]}")

    turn = economy.account_turn(
        platform="claude", model="claude-opus-4-8",
        prompt_text=("Write a production-grade Python module that parses a JSON config, validates "
                     "every field against a schema, applies defaults, and returns a typed dataclass.\n"
                     "Output format: markdown table and code block, with error handling."),
        response_text="Below is a complete implementation.\n```python\ndef load(p): ...\n```\n" * 3,
    )
    entry = economy.build_entry(turn, timestamp=economy.js_isoformat(datetime.now(timezone.utc)))
    st, b = http(f"{SB}/rest/v1/rpc/append_prompt", "POST",
                 {"p_entry": entry, "p_base_zaps": turn.zaps, "p_spend_usd": turn.spend_usd}, headers=auth)
    print(f"2) append_prompt #1: {st} {b[:200]}")

    st, b = http(f"{SB}/rest/v1/rpc/create_consouser", "POST", {"p_google_id": a["uid"]}, headers=auth)
    print(f"3) create_consouser AFTER turn: {st} {b[:200]}")

    st, b = http(f"{SB}/rest/v1/rpc/append_prompt", "POST",
                 {"p_entry": entry, "p_base_zaps": turn.zaps, "p_spend_usd": turn.spend_usd}, headers=auth)
    print(f"4) append_prompt #2: {st} {b[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

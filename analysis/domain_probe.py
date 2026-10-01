#!/usr/bin/env python3
"""Characterize Conso's email-domain allowlist (standalone probe)."""
import json, time, urllib.parse, urllib.request, urllib.error

KEY = "sb_publishable_clAiRg6ffCznEAtg_bn19Q_yY0W5Hyd"
SUPA = "https://jzxlayjrsdbyzykuiqns.supabase.co/auth/v1/signup"
RU = "https://bjibbmkefnaamkenamdppfengeepadpi.chromiumapp.org/"
URL = "https://www.conso.xyz/verify-human?redirect_uri=" + urllib.parse.quote(RU, safe="")


def solve():
    for i in range(6):
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:8877/solve",
                data=json.dumps({"type": "turnstile", "real_page": True,
                                 "sitekey": "0x4AAAAAAEzmjKoKI6TA61_6",
                                 "url": URL, "timeout_s": 100}).encode(),
                headers={"Content-Type": "application/json"})
            d = json.loads(urllib.request.urlopen(req, timeout=130).read())
            if d.get("token"):
                return d["token"]
        except Exception:
            pass
    return None


def signup(domain, tok):
    body = json.dumps({"email": f"conso-probe-{int(time.time()*1000)}@{domain}",
                       "password": "Aa1!testpassword",
                       "gotrue_meta_security": {"captcha_token": tok}}).encode()
    req = urllib.request.Request(SUPA, data=body,
                                 headers={"apikey": KEY, "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=30)
        d = json.loads(r.read())
        return "ALLOWED (user " + str(d.get("id", ""))[:8] + ")" if d.get("id") else str(d)[:120]
    except urllib.error.HTTPError as e:
        d = json.loads(e.read())
        return f"BLOCKED {e.code} {d.get('msg') or d.get('error_code') or d}"


import sys
for dom in sys.argv[1:]:
    print(f"--- {dom} ---", flush=True)
    tok = solve()
    if not tok:
        print("  (no turnstile token)", flush=True)
        continue
    print("  ->", signup(dom, tok), flush=True)

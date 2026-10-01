#!/usr/bin/env python3
"""End-to-end Conso account provisioning test (temp.tf + Turnstile + OTP)."""
import json, re, sys, time, urllib.parse, urllib.request, urllib.error

KEY = "sb_publishable_clAiRg6ffCznEAtg_bn19Q_yY0W5Hyd"
SB = "https://jzxlayjrsdbyzykuiqns.supabase.co"
RU = "https://bjibbmkefnaamkenamdppfengeepadpi.chromiumapp.org/"
VERIFY_URL = "https://www.conso.xyz/verify-human?redirect_uri=" + urllib.parse.quote(RU, safe="")


def http(url, method="GET", body=None, headers=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


def solve_turnstile():
    for i in range(8):
        try:
            st, body = http("http://127.0.0.1:8877/solve", "POST",
                            {"type": "turnstile", "real_page": True,
                             "sitekey": "0x4AAAAAAEzmjKoKI6TA61_6",
                             "url": VERIFY_URL, "timeout_s": 100}, timeout=140)
            tok = json.loads(body).get("token")
            if tok:
                return tok
        except Exception:
            pass
    return None


def get_inbox(provider="gmail"):
    st, body = http(f"https://temp.tf/api/account?providers={provider}&dot=0&plus=1", timeout=30)
    return json.loads(body)["email"]


def read_otp(address, tries=10):
    for _ in range(tries):
        st, body = http("https://temp.tf/api/check", "POST",
                        {"email": address, "wait": True}, timeout=45)
        try:
            msgs = json.loads(body).get("data", [])
        except Exception:
            msgs = []
        for m in msgs:
            m2 = re.search(r"\b(\d{6})\b", m.get("body", ""))
            if m2:
                return m2.group(1)
        time.sleep(2)
    return None


def main():
    provider = sys.argv[1] if len(sys.argv) > 1 else "gmail"
    address = get_inbox(provider)
    password = "Aa1!Veltrix9Zq"
    print(f"inbox: {address}", flush=True)

    tok = solve_turnstile()
    print(f"turnstile: {'ok' if tok else 'FAILED'}", flush=True)
    if not tok:
        return 1

    st, body = http(f"{SB}/auth/v1/signup", "POST",
                    {"email": address, "password": password,
                     "gotrue_meta_security": {"captcha_token": tok}},
                    headers={"apikey": KEY}, timeout=30)
    d = json.loads(body)
    print(f"signup: {st} uid={d.get('id')} msg={d.get('msg')}", flush=True)
    if not d.get("id"):
        return 1

    otp = read_otp(address)
    print(f"otp: {otp}", flush=True)
    if not otp:
        return 1

    st, body = http(f"{SB}/auth/v1/verify", "POST",
                    {"type": "email", "email": address, "token": otp},
                    headers={"apikey": KEY}, timeout=30)
    d = json.loads(body)
    at = d.get("access_token", "")
    print(f"verify: {st} session={'yes' if at else 'no'} uid={d.get('user', {}).get('id')}", flush=True)
    if not at:
        return 1

    uid = d["user"]["id"]
    st, body = http(f"{SB}/rest/v1/rpc/create_consouser", "POST",
                    {"p_google_id": uid},
                    headers={"apikey": KEY, "Authorization": f"Bearer {at}"}, timeout=30)
    print(f"create_consouser: {st} {body[:400]}", flush=True)

    st, body = http(f"{SB}/rest/v1/rpc/get_my_leaderboard_rank", "POST", {},
                    headers={"apikey": KEY, "Authorization": f"Bearer {at}"}, timeout=30)
    print(f"leaderboard_rank: {st} {body[:200]}", flush=True)

    with open("/tmp/active_acct.json", "w") as fh:
        json.dump({"email": address, "password": password, "uid": uid, "access_token": at}, fh)
    print("SAVED /tmp/active_acct.json", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

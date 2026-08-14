"""Cloudflare Turnstile verification for the open-access Demo door.

Replaces the promo-code gate. Turnstile is a privacy-preserving CAPTCHA alternative: no puzzles for
the visitor in the common case, and no signup friction -- which matters because the Demo's whole
point is "click and watch it design".

What it actually buys us: an abuse anchor. With no code and no account, a scripted client could take
every demo slot indefinitely. Turnstile makes each entry cost a real browser, and the per-visitor
seat derived alongside it (auth.demo_seat) gives ctl_plane something to count against.

FAILS OPEN BY DESIGN. With TURNSTILE_SECRET unset, verify() returns True and logs a warning once.
The Demo must not be un-launchable because a key hasn't been provisioned yet -- the fleet run-hour
budget in ctl_plane is the backstop that actually bounds cost. Set the secret to arm the door.
"""
import json
import os
import urllib.parse
import urllib.request

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

SITE_KEY = os.environ.get("TURNSTILE_SITE_KEY", "").strip()
SECRET = os.environ.get("TURNSTILE_SECRET", "").strip()
TIMEOUT = float(os.environ.get("TURNSTILE_TIMEOUT", "4"))

_warned = False


def enabled() -> bool:
    return bool(SECRET)


def site_key() -> str:
    """Public key for the widget; empty string means the frontend renders no challenge."""
    return SITE_KEY


def verify(token: str, ip: str = "") -> tuple[bool, str]:
    """(ok, reason). Unconfigured -> (True, "disabled"). Network failure -> (True, "unreachable")."""
    global _warned
    if not SECRET:
        if not _warned:
            _warned = True
            print("[turnstile] TURNSTILE_SECRET unset -- demo door is OPEN. "
                  "The ctl_plane run-hour budget is the only cost backstop.", flush=True)
        return True, "disabled"

    if not token:
        return False, "missing token"

    data = {"secret": SECRET, "response": token}
    if ip:
        data["remoteip"] = ip
    body = urllib.parse.urlencode(data).encode()
    try:
        req = urllib.request.Request(VERIFY_URL, data=body,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            out = json.loads(r.read())
    except Exception as e:
        # Cloudflare unreachable: let the visitor in rather than hard-failing the whole demo.
        print(f"[turnstile] verify unreachable ({type(e).__name__}) -- allowing", flush=True)
        return True, "unreachable"

    if out.get("success"):
        return True, "ok"
    codes = ",".join(out.get("error-codes") or []) or "rejected"
    return False, codes

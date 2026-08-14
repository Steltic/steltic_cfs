"""Open-access Demo sessions + per-session, server-side LLM-credential store.

DEMO CUTOVER (2026-08-01): access is no longer by pre-issued promo code. Anyone can enter; the door
is Cloudflare Turnstile (app/turnstile.py) and the limits are enforced per anonymous VISITOR SEAT.

The seat is a salted hash of the client IP:

    demo-<sha256(SECRET_KEY | "seat" | ip)[:10]>

Two properties matter. It is *stable*, so a visitor cannot dodge the one-run-at-a-time and
runs-per-day caps by clearing cookies. And it is *irreversible* -- the raw IP is never stored, never
sent to the control plane and never logged, so the Privacy Policy's "usage metadata only" promise
holds literally.

The user's LLM base-url + API key are held ONLY in process memory (SESSION_CREDS), keyed by a random
session id kept in the signed cookie -- never written to disk, evaporating on logout or restart.
"""
import hashlib, secrets, time
from fastapi import Request, HTTPException
from . import config

# session_id -> {"base_url":..., "api_key":..., "model":..., "ts":...}   (memory only, never persisted)
SESSION_CREDS: dict[str, dict] = {}


# ---------------- anonymous demo seats ----------------
def client_ip(request: Request) -> str:
    """Caller IP. Cloud Run puts the real client first in X-Forwarded-For."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return (request.client.host if request.client else "") or "?"


def demo_seat(ip: str) -> str:
    """Stable, irreversible per-visitor id. The salt is SECRET_KEY, so seats are not portable
    between deployments and a leaked seat list cannot be reversed into IP addresses."""
    salt = config.SECRET_KEY or "steltic-demo"
    return "demo-" + hashlib.sha256(
        (salt + "|seat|" + (ip or "?")).encode("utf-8")).hexdigest()[:10]


def start_demo_session(request: Request, ip: str) -> str:
    """Begin a Demo session. The user id is the visitor seat, so the one-run-at-a-time and
    runs-per-day caps span every tab and device behind that address. Job folders stay per-session
    (sid), so two browsers on one seat never see each other's files."""
    sid = secrets.token_urlsafe(24)
    user = demo_seat(ip)
    request.session["sid"] = sid
    request.session["user"] = user
    request.session["ts"] = int(time.time())
    return user


# ---------------- sessions ----------------
def logout_session(request: Request):
    sid = request.session.get("sid")
    if sid:
        SESSION_CREDS.pop(sid, None)
    request.session.clear()


def current_user(request: Request) -> str:
    """FastAPI dependency: returns the visitor seat or raises 401."""
    user = request.session.get("user")
    ts = request.session.get("ts", 0)
    if not user:
        raise HTTPException(status_code=401, detail="not logged in")
    if time.time() - ts > config.SESSION_HOURS * 3600:
        logout_session(request)
        raise HTTPException(status_code=401, detail="session expired")
    # Cutover: a cookie from the promo-code era is still validly signed (same SECRET_KEY), so it
    # would sail past the Turnstile door for up to SESSION_HOURS -- and its `trial-<hash-of-code>`
    # seat is not IP-derived, so the demo's one-run lock and daily cap would be keyed to the wrong
    # identity. Retire those sessions on sight; the visitor just re-enters through the door.
    if config.DEMO_MODE and not str(user).startswith("demo-"):
        logout_session(request)
        raise HTTPException(status_code=401, detail="session expired")
    return user


def get_sid(request: Request) -> str:
    sid = request.session.get("sid")
    if not sid:
        raise HTTPException(status_code=401, detail="no session")
    return sid


def set_creds(sid: str, base_url: str, api_key: str, model: str, reasoning: str = "high",
              max_tokens: int = 32000, provider: str = ""):
    SESSION_CREDS[sid] = {"base_url": base_url.rstrip("/"), "api_key": api_key, "model": model,
                          "reasoning": reasoning, "max_tokens": int(max_tokens),
                          "provider": provider.strip(), "ts": int(time.time())}


def get_creds(sid: str) -> dict | None:
    return SESSION_CREDS.get(sid)

"""Client for the control plane hosted on the RAG VM (ctl_plane.py). Every feature FAILS SOFT in a
feature-appropriate way when the VM is unreachable, so the control plane never becomes a new outage
class beyond what RAG-down already causes:

  cancel_check   -> False           (Stop falls back to disconnect teardown, today's behavior)
  run_acquire    -> allowed         (availability over strict quota; logged)
  run_hb         -> silent          (budget accrual pauses; the run is not interrupted)
  cfg            -> {}              (baked env/config values apply)

DEMO CUTOVER (2026-08-01): the promo-code endpoints (`/ctl/codes`, `/ctl/redeem/*`) are gone -- access
is open, gated at the door by Turnstile, and quota is anchored on an anonymous salted seat.

Base URL derives from RAG_API_URL (…:8080/query -> …:8080). Bearer = RAG_API_TOKEN.
All calls are short-timeout urllib; caches keep per-token/per-request overhead near zero.
"""
import json, threading, time, urllib.request
from . import config

_TIMEOUT = 1.5
_lock = threading.Lock()
_cancel_cache: dict = {}          # key -> (checked_ts, value)
_cfg_cache = {"ts": 0.0, "cfg": {}}
CANCEL_POLL_SEC = 2.5
CANCEL_TRUE_TTL = 10.0            # a cached True is re-verified after this (a NEW run clears the VM flag)
CFG_TTL = float(__import__("os").environ.get("CTL_CACHE_SEC", "60"))


def _base() -> str:
    u = (config.RAG_API_URL or "").strip()
    if not u:
        return ""
    return u[: -len("/query")] if u.endswith("/query") else u.rstrip("/")


def _call(method: str, path: str, body: dict = None, timeout: float = _TIMEOUT):
    base = _base()
    if not base:
        raise RuntimeError("no RAG_API_URL")
    hdrs = {"Content-Type": "application/json"}
    if config.RAG_API_TOKEN:
        hdrs["Authorization"] = "Bearer " + config.RAG_API_TOKEN
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ---------------- cancel (out-of-band Stop) ----------------
def cancel_set(key: str) -> bool:
    try:
        _call("POST", "/ctl/cancel", {"key": key})
        return True
    except Exception:
        return False


def cancel_clear_local(key: str):
    """Called when a NEW run acquires the seat: the VM cleared its flag; clear our cache too so a
    stop pressed on the PREVIOUS run can never insta-kill this one."""
    with _lock:
        _cancel_cache.pop(key, None)


def cancel_check(key: str) -> bool:
    """Throttled: one HTTP probe per CANCEL_POLL_SEC per key; safe to call every token."""
    now = time.time()
    with _lock:
        ts, val = _cancel_cache.get(key, (0.0, False))
        if val and now - ts < CANCEL_TRUE_TTL:
            return True
        if not val and now - ts < CANCEL_POLL_SEC:
            return False
        _cancel_cache[key] = (now, False)          # claim the probe slot before the HTTP call
    try:
        got = bool(_call("GET", f"/ctl/cancel/{key}").get("cancelled"))
    except Exception:
        got = False
    with _lock:
        _cancel_cache[key] = (now, got)
    return got


# ---------------- run registry ----------------
def run_acquire(seat: str, building: str, fresh: bool = True):
    """(allowed, info). info on rejection: {"reason": "active"|"daily_limit"|"at_capacity", ...}.
    VM down -> (True, None): availability beats strict quota."""
    try:
        r = _call("POST", "/ctl/run/acquire", {"seat": seat, "building": building, "fresh": bool(fresh)})
        if r.get("ok"):
            return True, None
        info = {"reason": r.get("reason") or "active"}
        info.update(r.get("active") or {})
        for k in ("used", "max_per_day", "used_hours", "cap_hours", "remaining_hours"):
            if k in r: info[k] = r[k]
        return False, info
    except Exception:
        print("[ctl] run_acquire unreachable -- allowing run (fail-open)")
        return True, None


def run_hb(seat: str) -> dict:
    """Heartbeat + budget meter. Returns the VM's view of this run ({} when unreachable), which
    carries `elapsed_sec` -- the fleet-wide clock the 2 h demo cap is enforced against, so it
    survives the run moving between instances on a Continue."""
    try:
        return _call("POST", "/ctl/run/hb", {"seat": seat}, timeout=1.0) or {}
    except Exception:
        return {}


def run_release(seat: str):
    try:
        _call("POST", "/ctl/run/release", {"seat": seat}, timeout=1.0)
    except Exception:
        pass


# ---------------- live config / kill switch ----------------
def cfg() -> dict:
    now = time.time()
    if now - _cfg_cache["ts"] < CFG_TTL:
        return _cfg_cache["cfg"]
    try:
        got = _call("GET", "/ctl/config") or {}
        _cfg_cache.update(ts=now, cfg=got)
        return got
    except Exception:
        _cfg_cache["ts"] = now                     # don't hammer while down
        return _cfg_cache["cfg"]

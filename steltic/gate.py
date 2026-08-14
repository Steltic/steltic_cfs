"""Consent gate (records that a visitor accepted the Terms) + per-instance run quota.

Consent is persisted (data/consent.json) so it survives restarts; quota counters are in-memory (reset
on restart, which is fine -- they only protect against abuse within a running process). The
AUTHORITATIVE quota is fleet-wide, in deploy/rag-vm/ctl_plane.py: with concurrency>1 and several
instances, per-instance counters alone would let a visitor run once per instance."""
import json, os, time, threading, datetime
from . import config

_lock = threading.Lock()
_runs: dict[str, str] = {}              # run_key (sid:building) -> user   (runs currently in progress)
_cancel: set[str] = set()               # run_keys that have been asked to stop
_daily: dict[tuple, int] = {}           # (user, YYYY-MM-DD) -> runs started today


# ---------------- consent ----------------
def _load() -> dict:
    if config.CONSENT_FILE.exists():
        try: return json.loads(config.CONSENT_FILE.read_text(encoding="utf-8"))
        except Exception: return {}
    return {}

def record_consent(user: str):
    with _lock:
        d = _load()
        d[user] = {"version": config.TERMS_VERSION, "ts": int(time.time())}
        tmp = config.CONSENT_FILE.with_name(config.CONSENT_FILE.name + ".tmp~")
        tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
        os.replace(tmp, config.CONSENT_FILE)     # ATOMIC

def has_consent(user: str) -> bool:
    return _load().get(user, {}).get("version") == config.TERMS_VERSION


# ---------------- quota ----------------
def _today() -> str:
    return datetime.date.today().isoformat()

def _active_count(user: str) -> int:
    return sum(1 for u in _runs.values() if u == user)

def quota_check(user: str) -> str | None:
    """Return an error string if the user is over a limit, else None."""
    with _lock:
        if _active_count(user) >= config.MAX_CONCURRENT_PER_USER:
            return (f"you already have {_active_count(user)} run(s) in progress "
                    f"(max {config.MAX_CONCURRENT_PER_USER}) -- Stop one (or wait) before starting another")
        if config.MAX_DAILY_PER_USER > 0 and _daily.get((user, _today()), 0) >= config.MAX_DAILY_PER_USER:
            return f"daily run limit reached ({config.MAX_DAILY_PER_USER}/day)"
    return None

def quota_acquire(user: str, run_key: str):
    with _lock:
        _runs[run_key] = user
        _cancel.discard(run_key)
        _daily[(user, _today())] = _daily.get((user, _today()), 0) + 1

def quota_release(run_key: str):
    """Idempotent: free a run's concurrency slot (by run_key)."""
    with _lock:
        _runs.pop(run_key, None)
        _cancel.discard(run_key)

def request_cancel(run_key: str) -> bool:
    """Ask an in-progress run to stop at its next step. Returns True if it was active."""
    with _lock:
        if run_key in _runs:
            _cancel.add(run_key)
            return True
        return False

def is_cancelled(run_key: str) -> bool:
    return run_key in _cancel

# The promo-code brute-force guard that lived here is gone with the codes themselves (2026-08-01).
# The Demo door is Cloudflare Turnstile (app/turnstile.py) and abuse limits are enforced per
# anonymous visitor seat by the control plane (deploy/rag-vm/ctl_plane.py).


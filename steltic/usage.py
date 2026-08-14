"""Privacy-safe usage telemetry.

THE PROMISE (Privacy Policy): no design data ever leaves the user's session -- no briefs, no
building/file contents, no reports, no API keys. This module therefore records METADATA ONLY:
who (the anonymous trial seat id), what kind of event, and when. Project identity is a salted
hash so repeat work on the same project is countable without revealing its name.

Transport: one structured JSON line per event on stdout, tagged "steltic_usage". On Cloud Run
these land in Cloud Logging as jsonPayload (survive instance recycling; queryable in Logs
Explorer; sinkable to BigQuery for the trial-long record -- see DEPLOY_CLOUDRUN.md).

Never log: brief text, building names (hash only), file names/contents, keys, prompts, tokens'
CONTENT (counts only).
"""
import atexit, hashlib, json, os, secrets, signal, sys, time, datetime
from . import config

ENABLED = os.environ.get("USAGE_LOG", "1") not in ("0", "false", "no")
_BOOT_ID = secrets.token_hex(4)
_REVISION = os.environ.get("K_REVISION", "local")


def _emit(kind: str, **fields):
    if not ENABLED:
        return
    rec = {"steltic_usage": 1, "kind": kind,
           "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "instance": _BOOT_ID, "revision": _REVISION}
    rec.update({k: v for k, v in fields.items() if v is not None})
    try:
        print(json.dumps(rec), flush=True)
    except Exception:
        pass                                        # telemetry must never break the app


def project_tag(seat: str, building: str) -> str:
    """Stable per-seat project tag WITHOUT revealing the project name."""
    return hashlib.sha256(("proj:" + str(seat) + "|" + str(building)).encode("utf-8")).hexdigest()[:8]


# ---------------- event helpers ----------------
def seat_signin(seat: str):
    _emit("seat_signin", seat=seat)

def run_start(seat: str, building: str, model: str = None, provider: str = None,
              reasoning: str = None, resume: bool = False):
    _emit("run_start", seat=seat, project=project_tag(seat, building),
          model=model, provider=provider or None, reasoning=reasoning, resume=bool(resume))

def run_end(seat: str, building: str, outcome: str, duration_s: float,
            tokens_in: int = 0, tokens_out: int = 0, steps: int = 0,
            errors: int = 0, pauses: int = 0):
    _emit("run_end", seat=seat, project=project_tag(seat, building), outcome=outcome,
          duration_s=round(float(duration_s), 1), tokens_in=int(tokens_in),
          tokens_out=int(tokens_out), steps=int(steps), errors=int(errors), pauses=int(pauses))

def instance_boot():
    _emit("instance_boot")

def _on_term(*_):
    _emit("instance_stop")
    raise SystemExit(0)


def install_lifecycle():
    instance_boot()
    try:
        signal.signal(signal.SIGTERM, _on_term)     # Cloud Run sends SIGTERM before reclaiming
    except Exception:
        pass
    atexit.register(lambda: _emit("instance_stop_atexit"))

"""Central settings, all overridable by environment variables (see .env.example)."""
import os, pathlib, sys

BASE          = pathlib.Path(__file__).resolve().parent.parent      # repo root (dev) or site-packages (installed)

# --- .env self-loading (added 2026-08-03, mirrors the AISC webapp improvement) ---
# Parse KEY=VALUE lines from <repo root>/.env if present. REAL environment variables
# always win, so a deployment's --set-env-vars can never be shadowed by a stray file.
def _load_dotenv():
    p = BASE / ".env"
    if not p.exists():
        return
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass
_load_dotenv()
CONTRACT_DIR  = BASE / "contract"
STEEL_ENGINE  = BASE / "steel_engine"
FRONTEND_DIR  = BASE / "frontend"
EXAMPLES_DIR  = BASE / "test_buildings"

# DISCLAIMER.md lives at the repo root in a dev checkout; the wheel bundles a copy inside the package.
_pkg_disclaimer = pathlib.Path(__file__).resolve().parent / "DISCLAIMER.md"
DISCLAIMER_FILE = (BASE / "DISCLAIMER.md") if (BASE / "DISCLAIMER.md").exists() else _pkg_disclaimer


def _default_data_dir() -> pathlib.Path:
    """Per-user application data dir (overridable with DATA_DIR)."""
    env = os.environ.get("DATA_DIR")
    if env:
        return pathlib.Path(env)
    if sys.platform == "win32":
        root = pathlib.Path(os.environ.get("LOCALAPPDATA") or (pathlib.Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        root = pathlib.Path.home() / "Library" / "Application Support"
    else:
        root = pathlib.Path(os.environ.get("XDG_DATA_HOME") or (pathlib.Path.home() / ".local" / "share"))
    return root / "Steltic"


DATA         = _default_data_dir();       DATA.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR = DATA / "sessions";         SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# --- DEMO mode (added 2026-08-03) ---
# DEMO=1 locks the app to the built-in example briefs: the server resolves an example
# KEY to its brief file and IGNORES any client-supplied brief/images/building name --
# "you cannot enter your own data" is enforced in Python, not in the UI. Uploads and
# Restore return 403. Analysis fidelity is pinned to auto (preflight recommendation).
# Budget conventions MATCH the AISC control plane (ctl-install.md):
#   DEMO_RUNS_PER_DAY:      0 = unlimited (normal per-day convention)
#   DEMO_MONTHLY_RUN_HOURS: 0 = CLOSED (emergency brake), -1 = unlimited
DEMO                   = os.environ.get("DEMO", "0") == "1"
DEMO_MODE              = DEMO                       # alias -- the ported wrapper modules use this name
DEMO_MAX_RUN_SEC       = int(os.environ.get("DEMO_MAX_RUN_SEC", "7200"))      # per run
DEMO_RUNS_PER_DAY      = int(os.environ.get("DEMO_RUNS_PER_DAY", "3"))
DEMO_MONTHLY_RUN_HOURS = float(os.environ.get("DEMO_MONTHLY_RUN_HOURS", "-1"))  # local default: unlimited

# --- Cloud Run wrapper (ported from the AISC steel_by_webpage app, 2026-08-12) ---
# Sessions/door: in DEMO the /api/enter door (Turnstile + consent) starts a session and every
# limit keys off the anonymous visitor SEAT (auth.demo_seat). Locally (DEMO=0) the app creates
# an implicit "local" session -- no login, jobs stay under sessions/local/jobs as before.
SECRET_KEY    = os.environ.get("SECRET_KEY", "")           # MUST be set in prod (main.py warns)
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "12"))
TERMS_VERSION = os.environ.get("TERMS_VERSION", "2026-08-12")
CONSENT_FILE  = DATA / "consent.json"
_pkg_privacy = pathlib.Path(__file__).resolve().parent / "PRIVACY.md"
PRIVACY_FILE  = (BASE / "PRIVACY.md") if (BASE / "PRIVACY.md").exists() else _pkg_privacy
MAX_CONCURRENT_PER_USER = int(os.environ.get("MAX_CONCURRENT_PER_USER", "1"))  # demo: ONE run per visitor
MAX_DAILY_PER_USER      = int(os.environ.get("MAX_DAILY_PER_USER", "0"))       # <=0 = unlimited (ctl-plane is authoritative)
# Per-session disk budget (steltic/diskcap.py). On Cloud Run DATA_DIR=/tmp is TMPFS (RAM, shared
# across co-tenants) -- each session gets a budget and old jobs are evicted. 0 = disabled (local).
SESSION_DISK_MB = int(os.environ.get("SESSION_DISK_MB", "0"))
# Per-run soft deadline (sec). 0 = OFF. On Cloud Run (60-min request cap) set ~3300: the loop
# pauses + saves cleanly before the platform kills the SSE; the frontend auto-continues once.
RUN_SOFT_DEADLINE_SEC = int(os.environ.get("RUN_SOFT_DEADLINE_SEC", "0"))
# Mid-run browser snapshots: every N sec the run streams the whole job zip to the tab (0 = off).
CHECKPOINT_BUNDLE_SEC = int(os.environ.get("CHECKPOINT_BUNDLE_SEC", "600"))
RAG_HEALTH_CACHE_SEC  = int(os.environ.get("RAG_HEALTH_CACHE_SEC", "60"))      # /healthz/rag probe cache

# --- sandbox executor ---
EXECUTOR        = os.environ.get("EXECUTOR", "auto")        # auto | docker | subprocess
SANDBOX_IMAGE   = os.environ.get("SANDBOX_IMAGE", "steel-sandbox:latest")
SANDBOX_TIMEOUT = int(os.environ.get("SANDBOX_TIMEOUT", "900"))   # seconds per run_python
SANDBOX_MEM     = os.environ.get("SANDBOX_MEM", "2g")
SANDBOX_CPUS    = os.environ.get("SANDBOX_CPUS", "2")
SANDBOX_PIDS    = os.environ.get("SANDBOX_PIDS", "256")

# --- agent loop ---
MAX_STEPS       = int(os.environ.get("MAX_STEPS", "200"))
REPEAT_LIMIT    = int(os.environ.get("REPEAT_LIMIT", "10"))     # PAUSE if one tool-call signature recurs this many times
REPEAT_NUDGE    = int(os.environ.get("REPEAT_NUDGE", "3"))      # soft-nudge once when a signature recurs this many times
PROGRESS_STALL_NUDGE = int(os.environ.get("PROGRESS_STALL_NUDGE", "10"))  # steps w/ no new file or rc=0 -> nudge
PROGRESS_STALL_PAUSE = int(os.environ.get("PROGRESS_STALL_PAUSE", "20"))  # steps w/ no progress -> pause for review
MAX_CALLS       = int(os.environ.get("MAX_CALLS", "0"))         # hard cap on model calls per run (0 = use MAX_STEPS)
TOOL_OUT_CAP    = int(os.environ.get("TOOL_OUT_CAP", "16000"))
RAG_OUT_CAP     = int(os.environ.get("RAG_OUT_CAP", "60000"))   # spec RAG results (saved to file + evicted at completion) get a higher cap so full hits stay in context and the JSON stays valid for eviction
TEMPERATURE     = float(os.environ.get("TEMPERATURE", "0.3"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "1200"))
RETRIES         = int(os.environ.get("RETRIES", "8"))           # LLM-call retries before giving up
RETRY_BACKOFF_CAP = int(os.environ.get("RETRY_BACKOFF_CAP", "60"))  # max seconds between retries (backoff ramps up to this)

# --- RAG (engineering standards). Empty => the search tool returns a 'disabled' note and the agent
# relies on its own AISC knowledge. Point it at your own RAG server, or request access to the
# hosted Steltic RAG via the contact details at https://stelticai.com. ---
RAG_API_URL   = os.environ.get("RAG_API_URL", "")
RAG_API_TOKEN = os.environ.get("RAG_API_TOKEN", "")  # bearer token, sent on every RAG call when set
RAG_TOP_K          = int(os.environ.get("RAG_TOP_K", "5"))          # chunks per RAG search
RAG_KEEP_RECENT    = int(os.environ.get("RAG_KEEP_RECENT", "0"))    # 0 = OFF (recommended w/ provider caching). >0 = keep N recent RAG results full, stub older
RAG_SEARCH_SOFTCAP = int(os.environ.get("RAG_SEARCH_SOFTCAP", "20"))  # after this many searches, nudge the agent to stop searching and derive
# Spec (AISC/ASCE) RAG results are ALWAYS saved to jobs/<name>/rag/<slug>.txt and, once a design completes,
# evicted from the saved conversation to a file pointer (agent._evict_all_rag) so optimisation runs don't bloat.

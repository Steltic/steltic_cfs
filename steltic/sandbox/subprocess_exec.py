"""Runs model code in a child process of the BACKEND interpreter with rlimits + timeout.

Two very different deployments use this executor:

* **Local dev** -- no isolation at all beyond rlimits. Fine for one trusted user; never expose it.
* **Cloud Run (the Demo)** -- Cloud Run cannot do Docker-in-Docker, so this is the only option.
  There it runs with ``SANDBOX_UID`` set, which turns on the privilege drop below.

WHY THE PRIVILEGE DROP MATTERS (this is the whole reason >1 user can share an instance):

The agent's Python is written by *the user's LLM*, reached at a *user-supplied* ``base_url``. A user
who points Steltic at a server they control therefore chooses exactly what Python runs here -- no
jailbreak required, they are the LLM. Meanwhile ``auth.SESSION_CREDS`` holds every live session's
plaintext API key in the parent process's memory.

At concurrency=1 that is survivable: the only key on the box belongs to whoever is running. Above
that it is a cross-tenant key-exfiltration hole -- ``open('/proc/<app_pid>/mem')`` and done.

Dropping to a different, unprivileged UID closes it: Linux refuses ``/proc/<pid>/mem`` across UIDs
without CAP_SYS_PTRACE, which Cloud Run does not grant. Sandboxes still share one UID and so can read
each other's job folders -- acceptable by construction in the Demo, where every job is one of the
published example buildings and the data is already public.
"""
import os, sys, subprocess
from pathlib import Path
from .base import Executor, ExecResult, prepare_script

# uid/gid to drop to before exec'ing model code. 0/unset = no drop (local dev).
SANDBOX_UID = int(os.environ.get("SANDBOX_UID", "0") or 0)
SANDBOX_GID = int(os.environ.get("SANDBOX_GID", "0") or 0) or SANDBOX_UID
# Address-space cap per exec. Must fit CONCURRENCY x this inside the instance memory limit
# (Cloud Run: cpu=2/8GiB, concurrency=3 -> 3 x 2GB = 6GB, leaving ~2GB for the app + tmpfs).
SANDBOX_RLIMIT_AS_GB = float(os.environ.get("SANDBOX_RLIMIT_AS_GB", "2"))


def privilege_drop_active() -> bool:
    """True when model code will run under a different UID than the app (the isolation guarantee)."""
    return bool(SANDBOX_UID) and os.name == "posix" and SANDBOX_UID != os.getuid()


class SubprocessExecutor(Executor):
    name = "subprocess"

    def __init__(self, engine_dir: Path):
        self.engine = str(engine_dir)

    def healthcheck(self) -> tuple[bool, str]:
        try:
            r = subprocess.run([sys.executable, "-c", "import openseespy.opensees"],
                               capture_output=True, text=True, timeout=60,
                               env={**os.environ, "PYTHONPATH": self.engine})
            return (r.returncode == 0,
                    "" if r.returncode == 0 else "openseespy not importable in backend venv (subprocess mode)")
        except Exception as e:
            return False, str(e)

    def _prepare_writable(self, *paths: Path):
        """Hand the sandbox uid ownership of the dirs it must write, when the drop is active."""
        if not privilege_drop_active():
            return
        for p in paths:
            try:
                os.chown(p, SANDBOX_UID, SANDBOX_GID)
                os.chmod(p, 0o700)
            except Exception:
                pass                      # not root, or already owned -- the run still proceeds

    def run(self, code: str, jobs_dir: Path, building: str, timeout: int) -> ExecResult:
        wd = jobs_dir / building
        run_dir = wd / ".run"
        run_dir.mkdir(parents=True, exist_ok=True)
        script = run_dir / "_exec.py"
        script.write_text(prepare_script(code), encoding="utf-8")
        self._prepare_writable(wd, run_dir)
        if privilege_drop_active():
            try:
                os.chown(script, SANDBOX_UID, SANDBOX_GID)
                os.chmod(script, 0o600)
            except Exception:
                pass
        env = {**os.environ,
               "PYTHONPATH": self.engine,
               "MPLBACKEND": "Agg", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
               "STEEL_BUILDER_JOBS": str(jobs_dir),
               "PYTHONDONTWRITEBYTECODE": "1",
               "HOME": str(run_dir),                   # sandbox uid has no real home
               "MPLCONFIGDIR": str(run_dir / "mpl"),   # matplotlib must not write to the app's dir
               "PYTHONPYCACHEPREFIX": str(run_dir / "pyc")}

        drop = privilege_drop_active()

        def _limits():                       # POSIX-only: runs in the child, after fork, before exec
            try:
                import resource
                resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout + 5))
                cap = int(SANDBOX_RLIMIT_AS_GB * 1024**3)
                resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
            except Exception:
                pass
            if drop:
                # Order matters: setgid BEFORE setuid, or the uid change revokes the right to do it.
                # These are NOT wrapped in try/except -- a silent failure here would run user-chosen
                # code as the app user with every other tenant's API key in reach. Raising makes the
                # child die and surfaces as a failed exec instead.
                os.setgroups([])
                os.setgid(SANDBOX_GID)
                os.setuid(SANDBOX_UID)

        try:
            p = subprocess.run([sys.executable, str(script)], cwd=str(wd), env=env,
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout,
                               preexec_fn=_limits if os.name == "posix" else None)
        except subprocess.TimeoutExpired:
            return ExecResult(returncode=124, timed_out=True, error=f"timeout after {timeout}s")
        finally:
            try: script.unlink()
            except Exception: pass
        return ExecResult(stdout=p.stdout or "", stderr=p.stderr or "", returncode=p.returncode)

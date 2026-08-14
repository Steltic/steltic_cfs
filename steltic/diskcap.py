"""Per-session disk cap for the job tree.

On Cloud Run ``DATA_DIR=/tmp/data`` and **/tmp is tmpfs -- it is RAM, and it counts against the
instance memory limit**. With one user per instance that only ever hurt that user. Sharing an
instance between three tenants changes the failure mode: one session accumulating job files pushes
the instance into an OOM kill that takes *every* tenant down with it.

So each session gets a byte budget, swept before a run starts and periodically during it. Eviction
is ordered least-valuable first:

  1. ``.run/`` scratch of OTHER jobs (regenerated on the next exec)
  2. ``figs/`` of *archived* jobs (already excluded from the download zip; regenerate on demand)
  3. whole archived ``<building>__YYYYMMDD-HHMMSS`` folders, oldest first
  4. ``figs/`` of finished-but-not-archived jobs, oldest first

**The job currently running is never touched** -- not its files, not its scratch (which an exec may
be using right now), not its figures. So a session whose *live* job alone exceeds the budget cannot
be brought under it; the sweep logs that and gives up rather than corrupting the run. Size the cap
comfortably above one design (a typical job is ~10 MB; 600 MB is the deployed value).

Set ``SESSION_DISK_MB=0`` to disable (local/self-host, where the data dir is real disk).
"""
import os
import shutil
import time
from pathlib import Path

from . import config


def _tree_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def _rm(path: Path) -> int:
    """Remove a file or tree; return bytes reclaimed (0 on failure)."""
    try:
        size = _tree_bytes(path) if path.is_dir() else path.stat().st_size
    except OSError:
        return 0
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink()
        return size
    except OSError:
        return 0


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _candidates(jobs_dir: Path, keep: str | None):
    """Eviction candidates, least valuable first. `keep` is the in-flight building, never touched."""
    if not jobs_dir.is_dir():
        return
    jobs = [d for d in jobs_dir.iterdir() if d.is_dir() and d.name != keep]
    archived = sorted((d for d in jobs if "__" in d.name), key=_mtime)
    live = sorted((d for d in jobs if "__" not in d.name), key=_mtime)

    for d in archived + live:                      # 1. scratch anywhere
        run = d / ".run"
        if run.is_dir():
            yield run
    for d in archived:                             # 2. figures of archived jobs
        figs = d / "figs"
        if figs.is_dir():
            yield figs
    for d in archived:                             # 3. archived jobs outright, oldest first
        yield d
    for d in live:                                 # 4. figures of older finished jobs
        figs = d / "figs"
        if figs.is_dir():
            yield figs


def sweep(session_dir: Path, keep: str | None = None) -> tuple[int, int]:
    """Bring `session_dir` under the cap. Returns (bytes_before, bytes_freed).

    Best-effort and never raises: a failed sweep must not take a design run down with it.
    """
    cap_mb = getattr(config, "SESSION_DISK_MB", 0)
    if cap_mb <= 0 or not session_dir.is_dir():
        return 0, 0
    cap = cap_mb * 1024 * 1024
    try:
        used = _tree_bytes(session_dir)
    except Exception:
        return 0, 0
    before, freed = used, 0
    if used <= cap:
        return before, 0
    try:
        for victim in _candidates(session_dir / "jobs", keep):
            if used <= cap:
                break
            got = _rm(victim)
            used -= got
            freed += got
    except Exception:
        pass
    if freed:
        print(f"[diskcap] session {session_dir.name[:8]}…: freed {freed // 1048576} MB "
              f"({before // 1048576} -> {used // 1048576} MB, cap {cap_mb} MB)", flush=True)
    if used > cap:
        # Everything evictable is gone and we are still over: the live job alone exceeds the budget.
        # Nothing further is safe to delete, so say so loudly -- the cap is set too low for the work.
        print(f"[diskcap] session {session_dir.name[:8]}…: STILL OVER CAP "
              f"({used // 1048576} MB vs {cap_mb} MB) — only the in-flight job remains. "
              f"Raise SESSION_DISK_MB.", flush=True)
    return before, freed


def over_cap(session_dir: Path) -> bool:
    """True when the session is still over budget after a sweep (caller should refuse a new run)."""
    cap_mb = getattr(config, "SESSION_DISK_MB", 0)
    if cap_mb <= 0:
        return False
    try:
        return _tree_bytes(session_dir) > cap_mb * 1024 * 1024
    except Exception:
        return False


class PeriodicSweeper:
    """Sweeps at most every `interval` seconds -- cheap enough to call from the run loop."""

    def __init__(self, session_dir: Path, keep: str | None = None, interval: float = 120.0):
        self.session_dir, self.keep, self.interval = session_dir, keep, interval
        self._last = time.time()

    def maybe(self):
        now = time.time()
        if now - self._last < self.interval:
            return
        self._last = now
        sweep(self.session_dir, self.keep)

"""DEMO-mode support: the server-side example catalog and the run meter.

The catalog is the single source of truth for what the Demo can run (25 live briefs;
Ex20-24 racks descoped) -- the frontend dropdown is BUILT from /api/config, so the UI
can never offer something /api/run would reject, and a hand-crafted POST cannot smuggle
in a custom design (the server ignores client brief text entirely in demo mode).

The meter persists to DATA/demo_meter.json so restarts don't reset the counters:
  - runs per LOCAL DAY   (DEMO_RUNS_PER_DAY, 0 = unlimited)
  - run-hours per MONTH  (DEMO_MONTHLY_RUN_HOURS, 0 = CLOSED, -1 = unlimited)
  - per-run hard cap     (DEMO_MAX_RUN_SEC) -- enforced through the agent's cancel
    flag, NOT by breaking the loop, so save handlers run and the visitor still gets
    a complete package (same semantics as the AISC Cloud Run demo).
Metering ticks on every SSE event, so an abandoned run still bills its elapsed time.
SINGLE-PROCESS scope: on Cloud Run with >1 instance the AISC control plane owns these
numbers instead -- see DEMO_NOTES.md before deploying this layer multi-instance.
"""
import json, threading, time, datetime
from . import config

# (key, dropdown label) -- order = dropdown order. Labels mirror frontend/index.html.
EXAMPLE_CHOICES = [
    ("ex1",  "Ex1 — WSP shear walls, 4 levels"),
    ("ex2",  "Ex2 — Strap-braced, 6 levels"),
    ("ex3",  "Ex3 — Bearing wall, 8 levels, podium"),
    ("ex4",  "Ex4 — Steel-sheet walls, 5 levels, podium"),
    ("ex5",  "Ex5 — Strap-braced, 6 levels (dormitory)"),
    ("ex6",  "Ex6 — WSP, 5 levels, L-plan"),
    ("ex7",  "Ex7 — Steel sheet, 6 levels, Z-plan"),
    ("ex8",  "Ex8 — Strap-braced, 4 levels, T-plan"),
    ("ex9",  "Ex9 — Podium 5-over-2, U-plan"),
    ("ex10", "Ex10 — SBMF, 2 levels, high bay"),
    ("ex11", "Ex11 — Gypsum walls, 3 levels, coastal wind"),
    ("ex12", "Ex12 — Steel sheet, 8 levels, cross-plan"),
    ("ex13", "Ex13 — Type II perforated walls, 4 levels"),
    ("ex14", "Ex14 — Mixed systems, 3 levels, split-level"),
    ("ex15", "Ex15 — WSP, 6 levels, stepped bar"),
    ("ex16", "Ex16 — Portal frame, single-span warehouse"),
    ("ex17", "Ex17 — Portal, two-span, heavy snow"),
    ("ex18", "Ex18 — Portal + mezzanine, mixed R"),
    ("ex19", "Ex19 — Portal with monorail, fatigue"),
    ("ex25", "Ex25 — Purlin/girt uplift retrofit"),
    ("ex26", "Ex26 — Open canopy, Cn wind"),
    ("ex27", "Ex27 — Single-channel portal, torsion"),
    ("ex28", "Ex28 — Ag building, partially enclosed"),
    ("ex29", "Ex29 — Freestanding mezzanine in existing bldg"),
    ("ex30", "Ex30 — Cold-storage portal"),
]
DEMO_KEYS = {k for k, _ in EXAMPLE_CHOICES}


def demo_building(key: str) -> str:
    """Job/project name for a demo run -- derived server-side from the example key."""
    return key.replace("ex", "Ex", 1)


class DemoMeter:
    _PATH = config.DATA / "demo_meter.json"

    def __init__(self):
        self._lock = threading.Lock()
        self._active: dict[str, float] = {}       # building -> run start ts
        self._last_tick: dict[str, float] = {}
        d = {}
        try:
            d = json.loads(self._PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
        self._day = d.get("day", "")
        self._runs = int(d.get("runs", 0))
        self._month = d.get("month", "")
        self._used_sec = float(d.get("used_sec", 0.0))

    def _roll(self):
        now = datetime.datetime.now()
        day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
        if day != self._day:
            self._day, self._runs = day, 0
        if month != self._month:
            self._month, self._used_sec = month, 0.0

    def _save(self):
        try:
            self._PATH.write_text(json.dumps(dict(
                day=self._day, runs=self._runs, month=self._month,
                used_sec=round(self._used_sec, 1))), encoding="utf-8")
        except Exception:
            pass

    def allow_new_run(self):
        """(ok, reason). Checked at run START; continues of an existing job are free."""
        with self._lock:
            self._roll()
            if config.DEMO_MONTHLY_RUN_HOURS == 0:
                return False, "the Demo is temporarily closed (run budget exhausted)"
            if config.DEMO_MONTHLY_RUN_HOURS > 0 and \
                    self._used_sec >= config.DEMO_MONTHLY_RUN_HOURS * 3600.0:
                return False, "the Demo's monthly run budget is used up -- try next month"
            if config.DEMO_RUNS_PER_DAY > 0 and self._runs >= config.DEMO_RUNS_PER_DAY:
                return False, "daily Demo run limit reached (%d/day) -- come back tomorrow" \
                       % config.DEMO_RUNS_PER_DAY
            return True, ""

    def start(self, building: str, count_run: bool):
        with self._lock:
            self._roll()
            if count_run:
                self._runs += 1
            now = time.time()
            self._active[building] = now
            self._last_tick[building] = now
            self._save()

    def tick(self, building: str):
        """Meter elapsed time into the monthly budget (called per SSE event)."""
        with self._lock:
            now = time.time()
            last = self._last_tick.get(building)
            if last is not None:
                self._used_sec += max(0.0, now - last)
            self._last_tick[building] = now
            self._save()

    def expired(self, building: str) -> bool:
        """Per-run hard cap -- feeds the agent's cancel flag."""
        t0 = self._active.get(building)
        return bool(t0 and (time.time() - t0) >= config.DEMO_MAX_RUN_SEC)

    def finish(self, building: str):
        with self._lock:
            now = time.time()
            last = self._last_tick.pop(building, None)
            if last is not None:
                self._used_sec += max(0.0, now - last)
            self._active.pop(building, None)
            self._save()

    def status(self):
        with self._lock:
            self._roll()
            left_day = (-1 if config.DEMO_RUNS_PER_DAY <= 0
                        else max(0, config.DEMO_RUNS_PER_DAY - self._runs))
            if config.DEMO_MONTHLY_RUN_HOURS < 0:
                left_hr = -1.0
            else:
                left_hr = max(0.0, config.DEMO_MONTHLY_RUN_HOURS - self._used_sec / 3600.0)
            return dict(runs_left_today=left_day,
                        monthly_hours_left=round(left_hr, 2),
                        max_run_min=config.DEMO_MAX_RUN_SEC // 60)


METER = DemoMeter()

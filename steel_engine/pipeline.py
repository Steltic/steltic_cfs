"""
pipeline.py  --  the design entry point the agent runs. ONE straight-through call (NO user-review
pause). Do NOT hand-write report.html or your own model/design scripts.

    import pipeline
    name = "<the building name the user gave you>"
    res = pipeline.design_and_report(name, cfg)    # sanity -> DEMAND envelope -> figures -> report.html
    print(res)                                     # {model_valid, demands_written, figures, report_html, design_dir, root}

You DETERMINE the joints / base fixity explicitly and STATE them in the report, but you do NOT pause
to ask the user to approve the model. (build_and_preview(name, cfg) remains available as an OPTIONAL
self-review that builds just the 3 figures -- it is not a required hold.)

The framework computes the model, the ASCE 7 loads, the analysis, and the per-line / per-member
DEMANDS + the report. It computes NO capacity: YOU query the RAG, derive every capacity/D-C
yourself, and write them into the package -- design/calc_package_cfs.json on the CFS paths (wall
lines_x/lines_y, portal span_ft; FEET/psf schemas), design/calc_package.json on the hot-rolled grid
path. All outputs go to the building's solution folder: <jobs>/<name>/ (design/, report.html).
Re-render after filling with report.build_report(name) (CFS jobs dispatch to the CFS report);
re-running design_and_report re-seeds the package and backs a FILLED one up to *.filled.bak
(merge_fills(name) carries the fills back).

(Hot-rolled grid path only:) unusual geometry / non-rigid joints: the parametric builder makes a rectangular grid of rigid
elasticBeamColumn members. To model anything else (custom nodes, sloped roofs, per-member moment
releases, etc.), set cfg["custom_build"] = a function custom_build(cfg, transf) that builds the
OpenSees model and returns the standard info dict {cm, present, z, NF, ele:[(tag,kind,sec,n1,n2)]}
using engine3d.ntag(i,j,k)/mtag(k); the whole pipeline then runs on your model unchanged.
"""
import os, sys, subprocess, copy

_HERE = os.path.dirname(os.path.abspath(__file__))     # .../engine
_REPO = os.path.dirname(_HERE)                          # repo root (steel_builder)
for _p in (_HERE, _REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# engine3d needs openseespy (frame grid path). The CFS wall/portal cfgs dispatch to
# cfs_pipeline BEFORE engine3d is touched, so this module imports anywhere.
try:
    import engine3d as E
except Exception:
    E = None


def _is_cfs_cfg(cfg):
    """CFS cfgs are unmistakable: wall path carries lines_x/lines_y (WallLine lists),
    portal path carries span_ft. engine3d grid cfgs carry NX/SX/heights."""
    return isinstance(cfg, dict) and ("lines_x" in cfg or "span_ft" in cfg)


def _root(name):
    """Per-building solution folder -- ALL outputs (cfg, design, figs, report) land here.
    Routed to the removable jobs folder via STEEL_BUILDER_JOBS (set by the MCP server) so a fresh
    agent cannot see prior jobs; falls back to the repo root for standalone/dev use."""
    base = os.environ.get("STEEL_BUILDER_JOBS") or _REPO
    return os.path.join(base, name)


def build_and_preview(name, cfg=None):
    """OPTIONAL self-review: build the OpenSees model + the 3 reviewer figures and return the figure
    paths + modelling assumptions. NOT a required user hold -- you may inspect the figures yourself,
    but proceed straight to design_and_report (just STATE the joints explicitly in the report)."""
    if _is_cfs_cfg(cfg):
        return {"name": name, "NOTE": ("CFS path has no 3D grid preview -- call "
                "pipeline.design_and_report(name, cfg) directly; the CFS report scaffold "
                "carries the line/frame demand tables for self-review.")}
    if E is None:
        raise SystemExit("engine3d/openseespy unavailable -- grid preview needs openseespy")
    if cfg is not None:
        E.CFG[name] = copy.deepcopy(cfg)   # freeze the analysed model: the report renders exactly this cfg
    if name not in E.CFG:
        raise SystemExit("cfg '%s' is not registered -- pass cfg=<your building dict>." % name)
    c = E.CFG[name]
    root = _root(name); os.makedirs(root, exist_ok=True)
    try:
        import plot_model as PM
        figs = PM.figures(name, os.path.join(root, "figs"))
    except Exception as ex:
        figs = "figures failed: %s" % ex
    base = c.get("base", "fixed")
    has_releases = bool(c.get("releases"))
    if c.get("custom_build"):
        joints = ("CUSTOM model -- you defined the nodes/elements and any joint releases yourself in "
                  "custom_build(cfg, transf).")
    elif has_releases:
        joints = ("Beam-to-column and brace joints are RIGID / continuous EXCEPT the per-member moment "
                  "releases you set in cfg['releases'] (pinned / simple-shear connections at those "
                  "members). Interior gravity framing leans only if cfg['lean_gravity'] is set.")
    else:
        joints = ("ALL beam-to-column and brace joints are RIGID / continuous (elasticBeamColumn) -- "
                  "there are NO per-member moment releases. Interior gravity framing leans only if "
                  "cfg['lean_gravity'] is set. (Use cfg['releases'] for pinned / simple-shear joints.)")
    return {"name": name, "root": root, "figures": figs,
            "base_fixity": base, "joint_assumption": joints, "joints_have_releases": has_releases,
            "NOTE": ("Joint fixity is a KEY modelling decision -- state it EXPLICITLY in the report. "
                     "Record exactly what you made the base joints (%s) and the internal member "
                     "connections (see joint_assumption) and whether each came from the user's information "
                     "or is a default you chose. You do NOT pause for the user -- set "
                     "cfg['releases'] / cfg['base'] / cfg['lean_gravity'] or a custom_build as needed and "
                     "call design_and_report." % base)}


# ---------------------------------------------------------------- CFS path (CFS-13/14/29/32)

PKG_CFS = "calc_package_cfs.json"      # THE authoritative CFS package (consistency.package_path)
PKG_HR = "calc_package.json"           # hot-rolled grid path (and legacy name on old CFS jobs)

_SLOT_LISTS = ("wall_lines", "holddowns", "studs", "collectors", "members", "connections",
               "anchorage", "schedules")
_AGENT_SLOT_FIELDS = ("limit_state", "cited", "capacity", "DC", "checks", "waived", "rows",
                      "sheathing", "fastener_schedule", "selection")


def _package_is_filled(pkg):
    """True when the package carries agent work: any slot with a limit state / citation /
    capacity / D/C / checks / waiver / schedule rows / selection (component-mode auto-fills
    excluded)."""
    if not isinstance(pkg, dict):
        return False
    for key in _SLOT_LISTS:
        for e in pkg.get(key) or []:
            if not isinstance(e, dict):
                continue
            if e.get("cited") == "component-mode scope rule":
                continue
            if any(e.get(f) not in (None, "", [], {}) for f in _AGENT_SLOT_FIELDS):
                return True
    return False


def backup_filled_packages(design_dir, cfs_job=True):
    """CFS-14: never silently overwrite the agent's work. Before the pipeline re-seeds the
    package, a FILLED calc_package_cfs.json is copied to calc_package_cfs.json.filled.bak (an
    older, different .filled.bak is kept with a timestamp suffix). On a CFS job a LEGACY
    design/calc_package.json is moved aside the same way (filled -> .filled.bak, unfilled ->
    .stale.bak) so exactly ONE package file remains. Returns the warning strings (also
    printed)."""
    import json as _json, shutil as _sh, time as _time
    msgs = []
    names = (PKG_CFS, PKG_HR) if cfs_job else (PKG_HR,)
    for fname in names:
        p = os.path.join(design_dir, fname)
        if not os.path.exists(p):
            continue
        try:
            old = _json.load(open(p, encoding="utf-8"))
        except Exception:
            old = None
        filled = _package_is_filled(old)
        legacy = cfs_job and fname == PKG_HR
        if not filled and not legacy:
            continue                                   # an unfilled seed: overwrite is harmless
        bak = p + (".filled.bak" if filled else ".stale.bak")
        if os.path.exists(bak):
            try:
                same = open(bak, "rb").read() == open(p, "rb").read()
            except Exception:
                same = False
            if not same:
                _sh.move(bak, bak + "." + _time.strftime("%Y%m%d-%H%M%S",
                                                         _time.localtime(os.path.getmtime(bak))))
        if legacy:
            _sh.move(p, bak)
            msgs.append("WARNING: legacy design/%s on a CFS job moved to %s -- the ONE "
                        "authoritative CFS package is design/%s%s"
                        % (fname, os.path.basename(bak), PKG_CFS,
                           ("; it held agent fills: merge them with pipeline.merge_fills(name, "
                            "backup='%s')" % os.path.basename(bak)) if filled else ""))
        else:
            _sh.copy2(p, bak)
            msgs.append("WARNING: design/%s had agent capacities/fills -> backed up to %s before "
                        "re-seeding with fresh demands. Re-derive (or restore with "
                        "pipeline.merge_fills(name)) -- to only re-render the report use "
                        "report.build_report(name), which preserves your fills."
                        % (fname, os.path.basename(bak)))
    for m in msgs:
        print("[design] " + m)
    return msgs


def _slot_key(e):
    if not isinstance(e, dict):
        return None
    if e.get("id") is not None:
        return ("id", e["id"])
    if e.get("check") is not None:
        return ("check", e["check"])
    if e.get("line") is not None or e.get("story") is not None:
        return ("dls", e.get("direction"), e.get("line"), e.get("story"))
    return None


_DEMAND_RE = __import__("re").compile(r"(_kip|_plf|_kipin|_kip_by_story)$")


def _is_demand_field(f):
    """Seeded DEMAND fields (fresh values win on a re-run): forces / unit shears, never the
    agent-owned design values (DC, capacity, T_design_kip ...) or properties (k_kip_in)."""
    f = str(f)
    return bool(_DEMAND_RE.search(f)) and not f.startswith("k_") and \
        f not in ("capacity", "T_design_kip", "design_T_kip", "T_demand_kip", "demand_kip")


def merge_fills(name, backup=None, rel_change=0.005):
    """Carry the agent's fills from a backed-up package into the fresh seed (opt-in; CFS-14).
    Slots are matched by id (drift rows by check / direction-line-story). For a matched slot the
    agent's non-numeric fields (selections, limit states, citations, device class, text) and every
    field the seed leaves empty are carried; SEEDED numeric demands keep their fresh values. Where
    a seeded demand moved by more than rel_change, the slot's D/C is not trusted: it is moved to
    DC_before_rerun, DC is cleared and recheck_after_rerun names the change (the gates then fail
    until the agent re-derives it). Agent-added slots and top-level blocks are appended; fresh
    seed slots the backup did not have stay (unfilled -> the gate lists them: fill or waive).
    Returns a summary dict."""
    import json as _json
    root = _root(name)
    ddir = os.path.join(root, "design")
    p = os.path.join(ddir, PKG_CFS)
    bak = os.path.join(ddir, backup or (PKG_CFS + ".filled.bak"))
    if not os.path.exists(p) or not os.path.exists(bak):
        raise SystemExit("merge_fills: need design/%s and %s" % (PKG_CFS, os.path.basename(bak)))
    new, old = _json.load(open(p, encoding="utf-8")), _json.load(open(bak, encoding="utf-8"))
    summ = dict(carried=0, appended=[], rechecks=[], new_unfilled=[], blocks=[])
    for key in _SLOT_LISTS + ("drift_table",):
        oldlist = [e for e in (old.get(key) or []) if isinstance(e, dict)]
        if new.get(key) is None:
            if oldlist:
                new[key] = oldlist; summ["blocks"].append(key)
            continue
        oldmap = {_slot_key(e): e for e in oldlist if _slot_key(e) is not None}
        seen = set()
        for e in new[key]:
            k = _slot_key(e)
            o = oldmap.get(k)
            if not o:
                if key != "drift_table" and isinstance(e, dict):
                    summ["new_unfilled"].append(e.get("id"))
                continue
            seen.add(k)
            changed = []
            for f, v in list(e.items()):
                ov = o.get(f)
                if _is_demand_field(f) and isinstance(v, (int, float)) and \
                        not isinstance(v, bool) and isinstance(ov, (int, float)) and \
                        not isinstance(ov, bool) and key != "drift_table":
                    if abs(v - ov) > rel_change * max(abs(v), abs(ov), 1e-9):
                        changed.append("%s %s -> %s" % (f, ov, v))
            for f, v in o.items():
                fresh = e.get(f)
                seeded_num = isinstance(fresh, (int, float)) and not isinstance(fresh, bool) \
                    and _is_demand_field(f)
                if fresh in (None, "", [], {}) or not seeded_num:
                    if v not in (None, "", [], {}) or fresh in (None, "", [], {}):
                        e[f] = v
            summ["carried"] += 1
            if changed and e.get("DC") is not None:
                e["DC_before_rerun"] = e.pop("DC")
                e["DC"] = None
                e["recheck_after_rerun"] = "seeded demand changed on re-run: " + "; ".join(changed)
                summ["rechecks"].append(e.get("id"))
        for e in oldlist:                                   # agent-added slots / rows
            k = _slot_key(e)
            if k is None or k not in seen:
                if k is not None and any(_slot_key(x) == k for x in new[key]):
                    continue
                new[key].append(e)
                summ["appended"].append(e.get("id") or e.get("check") or str(k))
    for k, v in old.items():
        if k not in new:
            new[k] = v; summ["blocks"].append(k)
    if isinstance(old.get("capacity_design"), dict) and isinstance(new.get("capacity_design"), dict):
        for k, v in old["capacity_design"].items():
            if k not in new["capacity_design"]:
                new["capacity_design"][k] = v
    _json.dump(new, open(p, "w", encoding="utf-8"), indent=1)
    print("[merge_fills] %d slot(s) carried, %d agent slot(s) appended, %d need re-derivation "
          "(demand changed): %s; fresh seed slots NOT in the backup (fill or waive): %s; blocks: %s"
          % (summ["carried"], len(summ["appended"]), len(summ["rechecks"]), summ["rechecks"][:12],
             summ["new_unfilled"][:12], summ["blocks"]))
    return summ


def _cfs_engine_res(cfg):
    """The demand-side result the CFS report renders (same dispatch as cfs_pipeline)."""
    if cfg.get("structure_kind") == "component":
        import cfs_frame as CF
        res = CF.run(dict(cfg, base="fixed", structure_kind="portal"))
        res["preflight_warnings"] = [w for w in res.get("preflight_warnings", [])
                                     if "P-DELTA" not in w]
        return res
    if "span_ft" in cfg:
        import cfs_frame as CF
        return CF.run(cfg)
    import cfs_engine as CE
    return CE.run(cfg)


def cfs_framework_blocks(cfg, res, pkg):
    """Add the framework's independent checks to a WALL-path package (in place): the
    independent tributary recomputation (flags into model_vs_tributary_flags on flexible
    diaphragms -- CFS-29a), the Rayleigh period per direction and the ASCE 7-22 12.2.3.2
    two-stage block for podium / cfg['two_stage'] jobs (CFS-32)."""
    if "lines_x" not in cfg or "directions" not in res:
        return pkg
    import cfs_gates as G
    try:
        it = G.independent_tributary(cfg, res)
        pkg["independent_tributary"] = dict(method=it["method"], n_flags=len(it["flags"]),
                                            by_direction=it["by_direction"])
        if it["flags"]:
            pkg.setdefault("model_vs_tributary_flags", []).extend(it["flags"])
    except Exception as ex:                       # never silent: the failure is a flag
        pkg.setdefault("model_vs_tributary_flags", []).append(
            "independent tributary check FAILED to run: %s" % ex)
    per = {}
    try:
        per = G.rayleigh_period(cfg, res)
        pkg["period_rayleigh"] = per
    except Exception as ex:
        pkg["period_rayleigh"] = {"error": "Rayleigh period failed: %s" % ex}
    if str(cfg.get("structure_kind", "")).lower() == "podium" or cfg.get("two_stage"):
        try:
            import cfs_pipeline as CP
            pkg["two_stage_framework"] = G.two_stage(cfg, res, CP._rho_seismic(cfg), per)
        except Exception as ex:
            pkg["two_stage_framework"] = {"error": "two-stage evaluation failed: %s" % ex,
                                          "by_direction": {}}
    return pkg


def _cfs_design_and_report(name, cfg, do_report=True, keep_fills=False):
    import json as _json
    import cfs_pipeline as CP
    root = _root(name)
    ddir = os.path.join(root, "design")
    os.makedirs(ddir, exist_ok=True)
    pre = {}
    try:                                           # CFS preflight (storage / platform / units ...)
        import preflight as _PF
        pre["preflight"] = _PF.check(cfg)
        print(_PF.render(pre["preflight"]))
    except Exception as _pfe:
        pre["preflight"] = [("WARN", "preflight failed: %s" % _pfe)]
    backups = backup_filled_packages(ddir, cfs_job=True)       # CFS-14
    out = CP.design_and_report(name, cfg, outdir=root, do_report=False)
    out.update(pre)
    out["package_backup"] = backups
    res = _cfs_engine_res(cfg)
    pkg = _json.load(open(out["package"], encoding="utf-8"))
    pkg = cfs_framework_blocks(cfg, res, pkg)
    _json.dump(pkg, open(out["package"], "w", encoding="utf-8"), indent=1)
    if keep_fills and backups:
        try:
            out["merge_fills"] = merge_fills(name)
            pkg = _json.load(open(out["package"], encoding="utf-8"))
        except SystemExit as ex:
            out["merge_fills"] = str(ex)
    if "lines_x" in cfg:
        out["gate_flags"] = {"model_vs_tributary": pkg.get("model_vs_tributary_flags") or []}
        if pkg.get("two_stage_framework"):
            out["two_stage"] = {d: v.get("status") for d, v in
                                (pkg["two_stage_framework"].get("by_direction") or {}).items()}
        if pkg.get("period_rayleigh"):
            out["period_rayleigh"] = {d: v.get("T_rayleigh_s") for d, v in
                                      pkg["period_rayleigh"].items() if isinstance(v, dict)}
    if do_report:
        try:
            import report as RPT
            out["report_html"] = RPT.build_report_cfs(name, cfg, res, pkg, root)
        except Exception as ex:
            out["report_html"] = "report failed: %s" % ex
    out["NEXT_STEP"] = (
        "MANDATORY: fill EVERY slot of design/%s IN PLACE (capacity, cited clause, D/C) from the "
        "RAG -- wall lines need sheathing + fastener_schedule; hold-downs are TENSION devices; "
        "fix or justify every model_vs_tributary / drift / preflight flag (a failing drift row "
        "must be redesigned, not annotated); then run consistency.check(name) until it PASSES "
        "and re-render with report.build_report(name) (it reads the FILLED package; do NOT "
        "re-run design_and_report just to re-render -- that re-seeds the package; it backs a "
        "filled one up to .filled.bak)." % PKG_CFS)
    print("\n" + "=" * 72 + "\n>> NEXT STEP (do not skip): " + out["NEXT_STEP"] + "\n" + "=" * 72)
    return out


RESULTS_FILE = "pipeline_results.json"


def _write_results(root, name, r, preflight=None):
    """Persist the sanity-suite verdict to <root>/design/pipeline_results.json (checks, theta,
    dual-system split, model warnings, preflight findings) with the SHA-256 of the cfg.py it ran
    on. The app-side completion gate reads this file -- it never imports the engine or runs cfg.py --
    so a FAILED check (drift, model_complete, beam_deflection, model_declared/consistent, theta,
    dual_25pct) cannot be delivered silently, and a result older than cfg.py is detected (HR-14; hot-rolled route of this module, ported from steltic)."""
    import json, datetime, hashlib
    try:
        d = os.path.join(root, "design"); os.makedirs(d, exist_ok=True)
        try:
            with open(os.path.join(root, "cfg.py"), "rb") as f:
                cfg_sha = hashlib.sha256(f.read()).hexdigest()
        except Exception:
            cfg_sha = None
        rec = {"name": name, "time": datetime.datetime.now().isoformat(timespec="seconds"),
               "cfg_sha256": cfg_sha, "model_valid": bool(r.get("allp")),
               "checks": {k: bool(v) for k, v in (r.get("chk") or {}).items()},
               "failed": [k for k, v in (r.get("chk") or {}).items() if not v],
               "summary": {k: r.get(k) for k in ("T", "Ta", "Cs", "V", "W", "mdx", "mdy", "Cd", "gov")},
               "extra": r.get("extra") or {},
               "preflight": [list(x) for x in (preflight or [])]}
        with open(os.path.join(d, RESULTS_FILE), "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=1, default=lambda o: sorted(o) if isinstance(o, (set, frozenset)) else str(o))
    except Exception as ex:
        print("[pipeline] WARNING: could not write design/%s (%s) -- the completion gate will refuse "
              "the final answer until it exists" % (RESULTS_FILE, ex))


def design_and_report(name, cfg=None, do_report=True, keep_fills=False):
    """Run the full design (no user-review pause): register cfg, run sanity -> DEMAND envelope ->
    figures -> HTML report, all in process, writing to the solution folder steel_builder/<name>.
    Computes NO capacity; the agent derives those from the RAG and fills the calc package.
    CFS cfgs (wall lines_x/lines_y or portal span_ft) dispatch to the CFS path, which writes
    design/calc_package_cfs.json (the ONE authoritative CFS package), runs the CFS preflight,
    backs up a FILLED package to .filled.bak before re-seeding (keep_fills=True merges the fills
    back with pipeline.merge_fills), and adds the framework's independent checks (independent
    tributary, Rayleigh period, ASCE 7-22 12.2.3.2 two-stage block)."""
    if _is_cfs_cfg(cfg):
        return _cfs_design_and_report(name, cfg, do_report=do_report, keep_fills=keep_fills)
    if E is None:
        raise SystemExit("engine3d/openseespy unavailable and cfg is not a CFS cfg -- "
                         "the hot-rolled grid path needs the openseespy environment")
    if cfg is not None:
        E.CFG[name] = copy.deepcopy(cfg)   # freeze the analysed model: the report renders exactly this cfg
    if name not in E.CFG:
        raise SystemExit("cfg '%s' is not registered -- pass cfg=<your building dict>." % name)

    root = _root(name); os.makedirs(root, exist_ok=True)
    out = {"name": name, "root": root}
    # R22 preflight: cheap cfg lint BEFORE any solve (units, factors vs system, drift limit vs Ie,
    # height limits, model/diaphragm declarations). Findings print and return; ERRORs mean the cfg
    # is mis-declared -- fix them first rather than debugging analysis output.
    try:
        import preflight as _PF
        _pf = _PF.check(E.CFG.get(name))
        out["preflight"] = _pf
        print(_PF.render(_pf))
    except Exception as _pfe:
        out["preflight"] = [("WARN", "preflight failed: %s" % _pfe)]
    E.clear_caches()   # fresh per-run modal/elf memo so design + report share this run's solves

    # 1) engineering sanity-check suite
    r = E.report(name)
    out["model_valid"] = bool(r.get("allp"))
    _write_results(root, name, r, out.get("preflight"))
    import consistency as _CC                                   # early units/geometry heads-up (e.g. story heights in ft)
    out["geometry_warnings"] = _CC._geometry_issues(E.CFG.get(name))

    # 2) ASCE 7-22 LRFD combinations + per-member DEMAND envelope (NO capacities -- agent/RAG)
    import design_pipeline as DP
    out["demands_written"] = bool(DP.design(name, outdir=os.path.join(root, "design")))
    out["design_dir"] = os.path.join(root, "design")

    # 3) reviewer-grade figures (geometry / orientation / deformed)
    try:
        import plot_model as PM
        out["figures"] = PM.figures(name, os.path.join(root, "figs"))
    except Exception as ex:
        out["figures"] = "skipped (%s)" % ex

    # 3b) standalone OpenSees model files for the user to check independently:
    #     model_opensees.py (DYNAMIC -- mass/period/seismic) and model_static.py (STATIC -- gravity force diagrams)
    try:
        out["model_files"] = E.export_model(cfg, root, name=name)
    except Exception as ex:
        out["model_files"] = "skipped (%s)" % ex
    try:
        import static_model as SM
        out["model_static"] = SM.export_static_model(cfg, root, name=name)
    except Exception as ex:
        out["model_static"] = "skipped (%s)" % ex

    # 4) the HTML report (10-section + appendices) -> steel_builder/<name>/report.html
    if do_report:
        import report as RPT
        out["report_html"] = RPT.build_report(name, root=root)

    # End-of-run reminder the agent sees in the tool output, right before it replies to the user.
    out["NEXT_STEP"] = ("MANDATORY before you finish: (a) the model must be COMPLETE (model_complete must PASS -- every "
                        "element modelled, no missing floor beams) and you must run consistency.check(name) and reconcile "
                        "every flag (including a missing cfg.py); ALSO CONFIRM report Figure 2 (member orientation, "
                        "web/depth ticks) rendered -- if it reads 'not available', run plot_model.figures(name) and "
                        "re-render report.build_report before finishing; then (b) END YOUR REPLY with this closing note "
                        "to the user, VERBATIM (do not reword it and do not add other offers):\n"
                        "\"Several figures are OFF-by-default, to reduce the time to render the report. Once the design "
                        "is completed, ask me to generate these items and add them to the report (may take several "
                        "minutes to render). Want to try different lateral restraint locations or systems? Want to do "
                        "an optimization run to reduce member sizes? Tell me what you would like to change in the "
                        "building and I'm on it.\"\n"
                        "(For your own reference, NOT to be listed to the user unless they ask: the OFF-by-default "
                        "figures are cfg['force_diagrams'] (~20-30 s), cfg['force_summary'] (~20-30 s), "
                        "cfg['mode_figures'] (~12 s), cfg['deformed_shape_figure'], cfg['section_color_figure'] and "
                        "cfg['appendix_case_figures'] -- set the flag(s) and re-render report.build_report.) "
                        "(c) The report is ALREADY on the user\u2019s computer at jobs/<name>/report.html "
                        "(the jobs folder on their own disk); just give them that path -- do NOT copy it "
                        "anywhere or look for an \u2018outputs folder\u2019; the engine path IS their computer's disk.")
    print("\n" + "=" * 72 + "\n>> NEXT STEP (do not skip): " + out["NEXT_STEP"] + "\n" + "=" * 72)
    return out


if __name__ == "__main__":
    for nm in (sys.argv[1:] or ["B02"]):
        print(design_and_report(nm))

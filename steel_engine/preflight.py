"""preflight.py -- R22 pre-analysis cfg linter. Cheap, engine-free checks run BEFORE the first
OpenSees solve so a mis-declared cfg is caught in seconds, not after a full pipeline run.
Returns a list of (severity, message); severity in {"ERROR","WARN"}. Non-blocking by design --
pipeline.design_and_report prints the findings and puts them in its return dict.

Also hosts the CANONICAL Seismic Design Category function asce_sdc() (ASCE 7-22 sec.11.6) --
engine-free so engine3d.py and report.py both import THIS implementation instead of keeping
divergent copies."""


def asce_sdc(SDS, SD1, S1=0.0, risk_cat="II"):
    """Seismic Design Category per ASCE 7-22 sec.11.6: the WORSE of Table 11.6-1 (SDS) and
    Table 11.6-2 (SD1), with the Risk Category IV column, after the S1 override:
    S1 >= 0.75 -> SDC E for Risk Category I-III, F for Risk Category IV."""
    rc4 = str(risk_cat).strip().upper() in ("IV", "4")
    if S1 is not None and float(S1) >= 0.75:
        return "F" if rc4 else "E"
    def _tab(x, rows):                       # rows: (upper_bound, SDC for RC I-III, SDC for RC IV)
        for thr, c123, c4 in rows:
            if float(x) < thr:
                return c4 if rc4 else c123
        return "D"                           # top row of both tables: D for every Risk Category
    c1 = _tab(SDS, [(0.167, "A", "A"), (0.33, "B", "C"), (0.50, "C", "D")])   # Table 11.6-1
    c2 = _tab(SD1, [(0.067, "A", "A"), (0.133, "B", "C"), (0.20, "C", "D")])  # Table 11.6-2
    return max(c1, c2)


def risk_cat_from_Ie(Ie):
    """Risk Category inferred from the seismic importance factor (Table 1.5-2)."""
    Ie = float(Ie or 1.0)
    return "IV" if Ie >= 1.5 else ("III" if Ie >= 1.25 else "II")


def sdc_of_cfg(cfg):
    """SDC for a cfg: explicit cfg['sdc'] wins, else derived from cfg['seis'] via asce_sdc()."""
    if cfg.get("sdc"):
        return str(cfg["sdc"]).strip().upper()
    s = cfg.get("seis") or {}
    return asce_sdc(float(s.get("SDS", 0) or 0), float(s.get("SD1", 0) or 0),
                    float(s.get("S1", 0) or 0), risk_cat_from_Ie(s.get("Ie", 1.0)))

# ASCE 7-22 Table 12.2-1 anchor values for the common steel SFRS (R, Cd, Om0, SDC-D height ft)
_SYS = {
    "smf":  (8.0, 5.5, 3.0, None), "imf": (4.5, 4.0, 3.0, 35.0),
    "ebf":  (8.0, 4.0, 2.0, 160.0), "brbf": (8.0, 5.0, 2.5, 160.0),
    "scbf": (6.0, 5.0, 2.0, 160.0), "ocbf": (3.25, 3.25, 2.0, 35.0),
    "spsw": (7.0, 6.0, 2.0, 160.0), "dual": (None, None, None, None),
    "c-psw": (6.5, 5.5, 2.5, 160.0), "stmf": (7.0, 5.5, 3.0, 160.0),
}


def _is_cfs(cfg):
    return isinstance(cfg, dict) and ("lines_x" in cfg or "lines_y" in cfg or "span_ft" in cfg)


_CFS_KINDS = ("wall", "podium", "portal", "canopy", "purlin", "portal_singlechannel", "component")
import re as _re
_RACK_RE = _re.compile(r"\b(storage|pallet|selective|drive-in|cantilever)?\s*racks?\b")
_NONBLDG_RE = _re.compile(r"\b(platforms?|mezzanines?|vessels?|tanks?|bins?|silos?|"
                          r"equipment supports?)\b")


def _cfg_text(cfg):
    keys = ("arch", "occupancy", "structure_kind", "use", "description", "brief", "notes",
            "system")
    return " ".join(str(cfg.get(k, "")) for k in keys).lower()


def check_cfs(cfg):
    """R22 preflight for the CFS paths (wall: cfs_engine FEET/psf schema; portal: cfs_frame FEET
    schema). Runs from pipeline.design_and_report BEFORE the engine (CFS-39 companion: the
    storage / platform / nonbuilding screens used to live only on the hot-rolled path)."""
    out = []
    say = lambda sev, msg: out.append((sev, msg))
    try:
        import consistency as _CC
        for msg in _CC._geometry_issues(cfg):         # heights_ft / plan_ft / portal span lint
            say("ERROR", msg)
    except Exception as ex:
        say("WARN", "geometry lint unavailable: %s" % ex)
    s = cfg.get("seis") or {}
    for k in ("SDS", "SD1", "R", "Cd", "Ie"):
        if k not in s:
            say("ERROR", "cfg['seis'] missing '%s'" % k)
    sysname = str(cfg.get("system") or "").lower()
    txt = _cfg_text(cfg)
    try:
        import cfs_systems as _CS
        SYS = _CS.SYSTEMS
    except Exception:
        _CS, SYS = None, {}
    if not sysname:
        say("ERROR", "cfg['system'] not declared -- set the exact SFRS key (%s)"
                     % "/".join(sorted(SYS)))
    elif SYS and sysname not in SYS and "lines_x" in cfg:
        say("ERROR", "cfg['system']=%r is not in the CFS system table (%s)"
                     % (cfg.get("system"), "/".join(sorted(SYS))))
    elif sysname in SYS and s.get("R") is not None:
        r0 = SYS[sysname]["R"]
        if abs(float(s["R"]) - r0) > 0.01:
            say("WARN", "cfg['seis'] R=%.2f but %s is R=%.2f in Table 12.2-1 -- confirm"
                        % (float(s["R"]), sysname, r0))
    # ---- scope: storage racks are OUT OF SCOPE; Ch. 12 vs Ch. 15 classification ----
    if _RACK_RE.search(txt):
        say("ERROR", "storage racks / ASCE 7 Ch. 15 rack structures are OUT OF SCOPE of this "
                     "module (no rack system, no RMI MH16.1 path) -- say so to the user and stop")
    kind = str(cfg.get("structure_kind", "wall")).lower()
    if _NONBLDG_RE.search(txt) or kind in ("platform", "mezzanine"):
        say("WARN", "platform / mezzanine / equipment-support keywords: CLASSIFY per ASCE 7-22 "
                    "15.1.1 -- an OCCUPIED mezzanine or platform is a BUILDING structure (Ch. 12, "
                    "Table 12.2-1 system, beams/posts/joists designed to S100); a free-standing "
                    "unit inside a building is not a Ch. 13 component when it is self-supporting "
                    "(13.1.1); only an UNOCCUPIED nonbuilding structure goes to Ch. 15 (15.4.1(1)(a) "
                    "still permits Table 12.2-1 for structures similar to buildings). State the "
                    "classification; model a one-level mezzanine on the wall path with the top "
                    "level as a FLOOR (live + storage), not a roof")
    if kind not in _CFS_KINDS and kind not in ("platform", "mezzanine"):
        say("WARN", "structure_kind=%r is not one of %s" % (kind, "/".join(_CFS_KINDS)))
    # ---- storage weight (ASCE 7-22 12.7.2 item 1) ----
    _Lf = cfg.get("L_floor")
    storage_decl = any(cfg.get(k) for k in ("storage", "storage_levels", "storage_live_psf",
                                            "storage_psf"))
    storagey = (isinstance(_Lf, (int, float)) and _Lf >= 125) or \
        bool(_re.search(r"\b(storage|stock ?room|archives?|library stacks?)\b", txt))
    if storagey and not storage_decl:
        say("WARN", "storage occupancy suspected (L_floor=%s psf / storage keywords) but no "
                    "cfg['storage'] / cfg['storage_levels'] / cfg['storage_live_psf'] declared -- "
                    "ASCE 7-22 12.7.2 item 1 requires >= 25%% of the floor live load in areas used "
                    "for storage in the seismic weight W; declare it so W includes it (and use "
                    "L = 1.0 in the seismic combos: the 2.3.6 Exception 1 factor 0.5 applies only "
                    "where Lo <= 100 psf, not in garages or public assembly)" % (_Lf,))
    # ---- drift limit vs Risk Category (Table 12.12-1 row-1 maxima) ----
    rc = str(cfg.get("risk_cat", "II")).upper()
    dl = cfg.get("drift_limit")
    lim = {"I": 0.025, "II": 0.025, "III": 0.020, "IV": 0.015}.get(rc, 0.025)
    if isinstance(dl, (int, float)) and dl > lim + 1e-9:
        say("ERROR", "drift_limit=%.3f exceeds the largest Table 12.12-1 value for RC %s (%.3f)"
                     % (dl, rc, lim))
    # ---- podium / two-stage ----
    if kind == "podium" and not cfg.get("two_stage"):
        say("WARN", "structure_kind='podium' without cfg['two_stage'] -- the 12.2.3.2 two-stage "
                    "procedure is NOT applied (declare two_stage=dict(K_lower_kip_in, R_lower, "
                    "rho_lower, T_combined_s or W_lower_kip) to evaluate eligibility + reaction "
                    "amplification), else design the whole structure with the least R")
    # ---- both hazards ----
    if "wind" not in cfg and "wind_pressures_psf" not in cfg and \
            not any(w in txt for w in ("interior", "indoor", "enclosed within")):
        say("WARN", "no cfg['wind'] -- run BOTH hazards (an interior mezzanine may state 'no "
                    "wind', with the reason)")
    return out


def check(cfg):
    out = []
    say = lambda sev, msg: out.append((sev, msg))
    if not isinstance(cfg, dict):
        return [("ERROR", "cfg is not a dict")]
    if _is_cfs(cfg):
        return check_cfs(cfg)
    # ---- units ----
    H = [float(h) for h in (cfg.get("heights") or []) if isinstance(h, (int, float))]
    if not H:
        say("ERROR", "cfg['heights'] missing/empty")
    _dex0 = set(int(k) for k in (cfg.get("drift_exempt_stories") or {}))
    _small = [(i, h) for i, h in enumerate(H, start=1) if h < 72]
    _undeclared = [(i, h) for i, h in _small if i not in _dex0]
    if _undeclared:
        say("ERROR", "story height < 6 ft found (%s in, story %s): heights look like FEET -- engine "
                     "units are INCHES (13 ft story = 156). If a small inter-level offset is "
                     "INTENTIONAL (split-level), declare it in cfg['drift_exempt_stories'] with a reason."
                     % (_undeclared[0][1], _undeclared[0][0]))
    elif _small:
        say("WARN", "sub-6-ft story height(s) at %s are DECLARED inter-diaphragm offsets "
                    "(drift_exempt_stories) -- OK; design the step transfer detail"
                    % [i for i, _ in _small])
    for k in ("SX", "SY"):
        v = cfg.get(k)
        if isinstance(v, (int, float)) and 0 < v < 60:
            say("ERROR", "%s=%g in is < 5 ft: bay spacing looks like FEET (engine uses inches)" % (k, v))
    # ---- seismic block ----
    s = cfg.get("seis") or {}
    for k in ("SDS", "SD1", "R", "Cd", "Ie"):
        if k not in s:
            say("ERROR", "cfg['seis'] missing '%s'" % k)
    sysname = str(cfg.get("system") or "").lower()
    if not sysname:
        say("ERROR", "cfg['system'] not declared (consistency.check will FAIL) -- set the exact SFRS")
    R = float(s.get("R") or 0)
    for key, (r0, cd0, om0, hlim) in _SYS.items():
        if key in sysname and key != "dual" and r0 is not None:
            if R and abs(R - r0) > 0.51 and "dual" not in sysname:
                say("WARN", "cfg['seis'] R=%.2f but system '%s' is normally R=%.2f (Table 12.2-1) -- "
                            "confirm the brief" % (R, cfg.get("system"), r0))
            if H and hlim and float(s.get("SDS", 0) or 0) >= 0.50:
                hn = sum(H) / 12.0
                if hn > hlim + 0.5:
                    say("WARN", "h_n=%.0f ft exceeds the ~%.0f ft SDC-D Table 12.2-1 limit for '%s' -- "
                                "FLAG and resolve (12.2.5.4 increase / dual system / 12.2.1.1)"
                                % (hn, hlim, key.upper()))
            break
    # ---- Risk-Category drift limit ----
    Ie = float(s.get("Ie", 1.0) or 1.0)
    dl = float(cfg.get("drift_limit", 0.020) or 0.020)
    if Ie >= 1.5 and dl > 0.0101:
        say("ERROR", "Ie=%.2f (RC IV) but drift_limit=%.3f -- Table 12.12-1 requires 0.010" % (Ie, dl))
    elif 1.2 <= Ie < 1.5 and dl > 0.0151:
        say("ERROR", "Ie=%.2f (RC III) but drift_limit=%.3f -- Table 12.12-1 requires 0.015" % (Ie, dl))
    # moment-frame-only SFRS in SDC D-F: allowable drift is Delta_a/rho (ASCE 7-22 sec.12.12.1.1);
    # the engine applies the division in its drift gates -- flag it so the reduced target is expected
    _mf_only = (any(k in sysname for k in ("smf", "imf", "omf")) or "moment" in sysname) \
               and "dual" not in sysname
    if _mf_only and sdc_of_cfg(cfg) in ("D", "E", "F"):
        _rho = float(cfg.get("rho", 1.3) or 1.3)
        say("WARN", "moment-frame-only SFRS in SDC %s: allowable story drift is drift_limit/rho = "
                    "%.4f/%.2f = %.4f (12.12.1.1) -- the engine drift gates apply this division"
                    % (sdc_of_cfg(cfg), dl, _rho, dl / _rho))
    # ---- analyses vs R=3 ----
    if R and R <= 3.0 and "341" in str(cfg.get("system", "")):
        say("WARN", "R<=3: AISC 341 does NOT apply -- design to AISC 360 only and prove wind-vs-seismic")
    # ---- model declaration ----
    md = cfg.get("model")
    if not (isinstance(md, dict) and {"bases", "joints", "gravity"} <= set(md)):
        say("ERROR", "cfg['model'] = {'bases','joints','gravity'} declaration missing (HARD GATE "
                     "model_declared will FAIL)")
    # ---- diaphragm / split-level declarations (F-1) ----
    dia = cfg.get("diaphragm", "rigid")
    if dia not in ("rigid", "flexible", "semi-rigid"):
        say("ERROR", "cfg['diaphragm'] must be 'rigid' | 'flexible' | 'semi-rigid' (got %r)" % (dia,))
    dex = cfg.get("drift_exempt_stories") or {}
    if dex:
        for k, why in dict(dex).items():
            if not str(why).strip():
                say("ERROR", "drift_exempt_stories[%s] has no reason -- each exemption must carry a "
                             "one-line justification (e.g. 'split-level inter-diaphragm offset, step "
                             "ties designed')" % k)
        say("WARN", "drift gate will SKIP stories %s (declared inter-diaphragm offsets) -- their "
                    "racking must be addressed as a designed detail in calc_package"
                    % sorted(dict(dex).keys()))
    # ---- load sanity ----
    for k in ("D_floor", "L_floor"):
        v = cfg.get(k)
        if isinstance(v, (int, float)) and v > 400:
            say("WARN", "%s=%g psf is unusually high -- confirm units (psf)" % (k, v))
    _Lf = cfg.get("L_floor")
    if isinstance(_Lf, (int, float)) and _Lf >= 125 and not cfg.get("storage") \
            and not cfg.get("storage_levels"):
        say("WARN", "floor live suggests storage occupancy -- 12.7.2 requires >=25%% of storage live "
                    "in W; set cfg['storage'] (all floors) or cfg['storage_levels'] (L_floor=%g psf)" % _Lf)
    # ---- Tier A/B consultancy guards (EDGE_CASE_SWEEP) ----
    arch = (str(cfg.get("arch", "")) + " " + str(cfg.get("system", ""))).lower()
    if any(k in arch for k in ("gable", "pitch", "slope", "monoslope", "sloped")):
        say("WARN", "A2 sloped roof keywords in cfg: the engine models FLAT levels only -- model at the "
                    "mean roof height, then HAND-CHECK unbalanced/sliding snow (ASCE 7-22 7.6/7.9), eave "
                    "drift, and rafter thrust; state the idealization")
    if any(k in arch for k in ("platform", "vessel", "tank", "bin", "silo")):
        say("WARN", "B2 nonbuilding-structure keywords in cfg: classify per ASCE 7-22 15.1.1 -- an "
                    "OCCUPIED platform/mezzanine is a building (Ch. 12); only an unoccupied "
                    "nonbuilding structure uses Ch. 15 (Table 15.4-1/2, or 15.4.1(1)(a) Table "
                    "12.2-1 for structures similar to buildings)")
    try:
        LX = float(cfg.get("NX", 0)) * float(cfg.get("SX", 0)) / 12.0
        LY = float(cfg.get("NY", 0)) * float(cfg.get("SY", 0)) / 12.0
        if max(LX, LY) > 300.0:
            say("WARN", "B5 plan dimension %.0f ft > ~300 ft jointless: record the expansion/thermal "
                        "decision in calc_package (joint located, or thermal force statement)" % max(LX, LY))
    except Exception:
        pass
    for k, v in dict(cfg.get("extra_mass_floors") or {}).items():
        try:
            if abs(float(v)) > float(cfg.get("D_floor", 100) or 100):
                say("WARN", "extra_mass_floors[%s]=%g psf exceeds |D_floor| -- confirm the sign/magnitude" % (k, v))
        except Exception:
            pass
    return out



def render(res):
    if not res:
        return "[preflight] R22: no findings -- cfg passes the pre-analysis checks"
    lines = ["[preflight] R22 cfg linter: %d finding(s) -- fix ERRORs BEFORE trusting any analysis:" % len(res)]
    for sev, msg in res:
        lines.append("  [%s] %s" % (sev, msg))
    return "\n".join(lines)

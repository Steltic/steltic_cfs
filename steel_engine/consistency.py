"""
consistency.py -- numerical SELF-CONSISTENCY check for a finished design.

Loads <root>/<name>/design/calc_package.json (or a passed-in dict) and flags INTERNAL
inconsistencies before the report is finalised -- the class of bug where two scripts compute the
same quantity differently and the two are never reconciled (e.g. a 1/4" plate at D/C 1.00 in one
script vs a 9/16" plate at D/C 0.77 in another, both left in the package).

For every member AND connection it checks:
  * completeness -- a limit state, a D/C and a cited clause exist (top-level OR inside a `checks`
                    list -- connections legitimately carry these per limit-state);
  * bound        -- no D/C (anywhere) exceeds 1.0;
  * governing    -- the headline D/C equals the MAX of the per-limit-state check D/Cs (this is what
                    catches a headline 0.77 sitting on top of a check that is really 1.00);
  * recompute    -- where a demand and a capacity are both recoverable (numeric fields, or an
                    "Ru=.. -> phiRn=.." style demand string), D/C == demand / capacity to tolerance;
  * one-value    -- a labelled+unit quantity (plate thickness, D/C, ...) that appears more than once
                    in the entry's text must carry ONE value; conflicting numbers are flagged.

check(name) prints a PASS/FAIL summary and returns the list of issue strings ([] == consistent).
Never raises -- a malformed package is itself reported as an issue.
"""
import os, json, re

TOL = 0.04   # 4% relative tolerance on recomputed ratios


def _here_repo():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root (engine/..)


def _num(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        m = re.search(r"-?\d+(?:\.\d+)?", x.replace(",", ""))
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                return None
    return None


def _dc_num(x):
    """Strict D/C reader. Accepts a number, or a string that STARTS with a number ('0.93',
    '0.93 (governs)'). A qualitative status like 'OK (a=.625b_f ...)', 'PASS', 'N/A' returns None
    (a pass/status, NOT a ratio) -- so dimensional/geometry checks are never mis-read as a D/C."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        m = re.match(r"\s*(-?\d*\.?\d+)", x)   # anchored at the START, not search-anywhere
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def _close(a, b, tol=TOL):
    if a is None or b is None:
        return True
    if abs(a) < 1e-9 and abs(b) < 1e-9:
        return True
    return abs(a - b) / max(abs(a), abs(b), 1e-9) <= tol


def _find(d, *subs):
    """First numeric value in dict d whose key contains any of the substrings (case-insensitive)."""
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if any(s in str(k).lower() for s in subs):
            n = _num(v)
            if n is not None:
                return n
    return None


def _demand_capacity_from_string(s):
    """Parse a demand string like 'Ru=788 kip; phiRn=601 (web) -> 1023 kip (with 9/16" doubler)'
    into (demand, capacity). demand = the Ru/required value; capacity = the FINAL (largest, post-
    reinforcement) phiRn / value after an arrow. Returns (dem, cap) or (None, None)."""
    if not isinstance(s, str):
        return None, None
    low = s.lower()
    dem = None
    m = re.search(r"(?:ru|required|demand|mu|pu|vu)\s*[=:]?\s*(-?\d+(?:\.\d+)?)", low)
    if m:
        dem = float(m.group(1))
    cap = None
    # prefer a value after an arrow (final, reinforced capacity), else after phiRn/phi Rn
    arrow = re.findall(r"(?:->|→)\s*(-?\d+(?:\.\d+)?)", low)
    if arrow:
        cap = float(arrow[-1])
    else:
        m = re.search(r"(?:phirn|φrn|phi\s*rn|capacity)\s*[=:]?\s*(-?\d+(?:\.\d+)?)", low)
        if m:
            cap = float(m.group(1))
    return dem, cap


_LABEL_NUM = re.compile(r"([A-Za-z][A-Za-z _/\.-]{1,26}?)\s*[=:]?\s*(-?\d+(?:/\d+)?(?:\.\d+)?)\s*"
                        r"(in\.?|inch|ksi|kips?|k-?ft|k-?in|mm)\b", re.I)


def _gather_text(obj, acc):
    if isinstance(obj, str):
        acc.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _gather_text(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _gather_text(v, acc)


def _frac(tok):
    if "/" in tok:
        try:
            a, b = tok.split("/"); return float(a) / float(b)
        except Exception:
            return None
    return _num(tok)


def _one_value_issues(kind, cid, entry):
    texts = []
    _gather_text(entry, texts)
    seen = {}
    for t in texts:
        for m in _LABEL_NUM.finditer(t):
            label = re.sub(r"\s+", " ", m.group(1).strip().lower())
            label = re.sub(r"^(the|a|an|use|using|with|of|for|one|each)\s+", "", label)
            if len(label) < 3:
                continue
            unit = m.group(3).lower().replace(".", "").rstrip("s")
            val = _frac(m.group(2))
            if val is None:
                continue
            key = (label, unit)
            seen.setdefault(key, [])
            if all(abs(val - v) / max(abs(val), abs(v), 1e-9) > 0.02 for v in seen[key]):
                seen[key].append(val)
    out = []
    for (label, unit), vals in seen.items():
        if len(vals) > 1:
            out.append(f"[{kind} {cid}] quantity '{label}' ({unit}) appears as "
                       f"{', '.join(str(v) for v in vals)} -- one value only; reconcile")
    return out


_MILS_ASC = [18, 27, 30, 33, 43, 54, 68, 97, 118]


def _resize_hint(sec, kind=""):
    """Concrete fix for a D/C > 1.0 item so a weak agent iterates instead of shipping NG.
    CFS designators get the next-heavier mil in the same profile; wall slots get the schedule
    remedies; legacy W-shapes keep the depth-family hint (frame path)."""
    try:
        import re as _re
        if kind in ("wall", "wall_line"):
            return (" -> densify the edge-fastener schedule, go two-sided, thicken the sheathing "
                    "or ADD wall length, then re-run the pipeline")
        m = _re.match(r"^(\d{2,4}[STUF]\d{2,3})-(\d{2,3})$", str(sec or "").upper())
        if m:
            base, mil = m.group(1), int(m.group(2))
            nxt = [w for w in _MILS_ASC if w > mil]
            return (" -> try %s-%d (next mil up, same profile)" % (base, nxt[0])) if nxt else \
                   (" -> %s is the heaviest mil: go BUILT-UP (back-to-back/box per S240) and FLAG it" % sec)
        m = _re.match(r"(W\d+)X(\d+(?:\.\d+)?)", str(sec or "").upper())
        if not m:
            return ""
        import csv as _csv, os as _os
        fam, wt = m.group(1), float(m.group(2))
        path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "aisc_shapes.csv")
        wts = sorted(float(r["AISC_Manual_Label"].upper().split("X")[1])
                     for r in _csv.DictReader(open(path))
                     if r["AISC_Manual_Label"].upper().startswith(fam + "X"))
        nxt = [w for w in wts if w > wt + 0.1]
        return (" -> try %sX%g (next size up, same depth family)" % (fam, int(nxt[0]) if float(nxt[0]).is_integer() else nxt[0])) if nxt else \
               (" -> %s is the heaviest %s: use a BUILT-UP section and FLAG it" % (sec, fam))
    except Exception:
        return ""


def _entry_issues(kind, entry):
    out = []
    cid = entry.get("id", "?")
    checks = entry.get("checks") if isinstance(entry.get("checks"), list) else []

    # collect per-limit-state D/Cs (recomputing from numeric demand/capacity where available)
    check_dcs = []
    for c in checks:
        if not isinstance(c, dict):
            continue
        cls = c.get("limit_state", c.get("name", "check"))
        cdc = _dc_num(c.get("DC"))
        dem = _find(c, "demand", "ru", "required", "mu", "pu", "vu", "_kip", "_kft")
        cap = _find(c, "capacity", "phirn", "phi", "available", "design_strength")
        if dem is not None and cap not in (None, 0.0):
            rec = dem / cap
            if cdc is not None and not _close(rec, cdc):
                out.append(f"[{kind} {cid}] check '{cls}': D/C {cdc:.3f} != demand/capacity "
                           f"{dem:.3g}/{cap:.3g} = {rec:.3f} -- reconcile")
            elif cdc is None:
                cdc = rec
        if cdc is not None:
            check_dcs.append((cls, cdc))

    top_dc = _dc_num(entry.get("DC"))
    ls_present = bool(entry.get("limit_state")) or any(
        isinstance(c, dict) and c.get("limit_state") for c in checks)
    all_dcs = ([top_dc] if top_dc is not None else []) + [d for _, d in check_dcs]

    if not ls_present:
        out.append(f"[{kind} {cid}] no limit_state given (top-level or in a check)")
    if not entry.get("cited") and not any(isinstance(c, dict) and c.get("cited") for c in checks):
        out.append(f"[{kind} {cid}] no cited clause (AISI S100/S240/S400)")
    if not all_dcs:
        out.append(f"[{kind} {cid}] no D/C reported (top-level or in a check)")
    else:
        worst = max(all_dcs)
        if worst > 1.0 + 1e-9:
            if entry.get("waived"):
                pass   # explicitly waived with an engineering justification (completion-gate convention) --
                       # e.g. an EXISTING member in a retrofit/addition job carried as a retrofit-scope item
            else:
                _sec_ = (entry.get("inputs") or {}).get("section") or entry.get("section")
                out.append(f"[{kind} {cid}] worst D/C = {worst:.3f} > 1.0 (NG -- resize/redesign)"
                           + _resize_hint(_sec_, kind))
        if top_dc is not None and check_dcs:
            wcls, wd = max(check_dcs, key=lambda t: t[1])
            if not _close(wd, top_dc):
                out.append(f"[{kind} {cid}] headline D/C {top_dc:.3f} != worst limit-state D/C "
                           f"{wd:.3f} ('{wcls}') -- the two were never reconciled")

    # recompute from a free-text demand string (Ru=.. ; phiRn=.. -> CAP)
    dem, cap = _demand_capacity_from_string(entry.get("demand"))
    if dem is not None and cap not in (None, 0.0) and all_dcs:
        rec = dem / cap
        if not _close(rec, max(all_dcs)):
            out.append(f"[{kind} {cid}] demand/capacity {dem:.3g}/{cap:.3g} = {rec:.3f} "
                       f"!= reported D/C {max(all_dcs):.3f} -- reconcile")

    out += _one_value_issues(kind, cid, entry)
    return out


def _completeness_issues(pkg):
    """Reserved for commonly-omitted member CATEGORIES. The old infill/filler-beam heuristic
    was removed: secondary filler beams are needed only when the deck cannot span girder-to-
    girder, which the engineer decides per project, not a blanket requirement."""
    return []


def _isnum(x):
    try:
        float(x); return True
    except Exception:
        return False


def _load_cfg(root, name):
    """Best-effort load of the building cfg dict from <root>/cfg.py so geometry/units can be sanity-checked."""
    p = os.path.join(root, "cfg.py")
    if not os.path.exists(p):
        return None
    try:
        # Compile the SOURCE directly (not via importlib) so a stale cached .pyc on a coarse-mtime jobs
        # mount can never shadow the just-edited cfg.py -- the class of bug where an edit looks ignored. (P13)
        import types as _types
        src = open(p, encoding="utf-8").read()
        m = _types.ModuleType("cfg_chk_" + name); m.__file__ = p
        exec(compile(src, p, "exec"), m.__dict__)
        return getattr(m, "cfg", None)
    except Exception:
        return None


def _geometry_issues(cfg):
    """Units sanity for BOTH cfg schemas: the wall path (`heights_ft`, brief-facing FEET) and the
    frame path (`heights`, engine INCHES). Each schema's classic slip is entering the other's
    units."""
    out = []
    if not isinstance(cfg, dict):
        return out
    Hft = [float(h) for h in (cfg.get("heights_ft") or []) if _isnum(h)]
    if Hft:                                     # wall path: FEET expected (8-20 ft stories)
        big = [h for h in Hft if h > 50]
        if big:
            out.append("wall-path story heights look like INCHES, not feet (%s) -- heights_ft is "
                       "FEET: a 9.5 ft story is 9.5, not 114. Divide by 12 and re-run."
                       % ", ".join("%g" % h for h in big[:10]))
        tiny = [h for h in Hft if h < 6]
        if tiny:
            out.append("wall-path story height(s) under 6 ft (%s) -- confirm heights_ft is in FEET."
                       % ", ".join("%g" % h for h in tiny[:10]))
        p = cfg.get("plan_ft") or ()
        if any(_isnum(v) and float(v) > 1000 for v in p):
            out.append("plan_ft dimension over 1000 -- plan_ft is FEET, not inches.")
        return out
    H = [float(h) for h in (cfg.get("heights") or []) if _isnum(h)]   # frame path: INCHES
    _dex = set(int(k) for k in (cfg.get("drift_exempt_stories") or {}))
    small = [h for i, h in enumerate(H, start=1) if h < 72 and i not in _dex]
    if small:
        out.append("story heights look like FEET, not inches (%s) -- the frame engine uses INCHES: "
                   "a 13 ft story is 156, not 13. Multiply every height by 12 and re-run."
                   % ", ".join("%g" % h for h in small[:10]))
    tall = [h for h in H if h > 720]
    if tall:
        out.append("story height(s) over 60 ft (%s in) -- confirm the units are inches." % ", ".join("%g" % h for h in tall[:10]))
    for key, lab in (("SX", "X-bay"), ("SY", "Y-bay")):
        v = cfg.get(key)
        if _isnum(v) and 0 < float(v) < 60:
            out.append("%s spacing %s=%g in is implausibly small -- the frame engine uses INCHES (a 20 ft bay is 240)." % (lab, key, float(v)))
    return out



_WALL_SYSTEMS = ("wsp_shearwall", "steelsheet_wall", "gypsum_wall", "strap_braced", "sbmf")


def _design_basis_issues(cfg, name=None, pkg=None):
    """CFS design-basis gates: system declaration, R<=3 path, drift limit vs Risk Category,
    diaphragm idealization (FLEXIBLE is the light-frame default -- rigid must be justified),
    tier-vs-structure, drift-exempt declarations, collectors on irregular plans."""
    out=[]
    if not isinstance(cfg,dict): return out
    s=cfg.get("seis") or {}
    SDS=float(s.get("SDS",0) or 0); R=s.get("R")
    wall_path = bool(cfg.get("heights_ft") or cfg.get("lines_x"))
    sysname = str(cfg.get("system") or "").lower()
    try:
        import cfs_systems as _CS
    except Exception:
        _CS = None
    if not sysname:
        out.append("declare cfg['system'] = the EXACT SFRS from the brief (wsp_shearwall / "
                   "steelsheet_wall / gypsum_wall / strap_braced / sbmf / not_detailed) -- the "
                   "report otherwise INFERS it from R, which is ambiguous (R=6.5 is WSP or steel sheet)")
    elif _CS is not None and wall_path and sysname not in _CS.SYSTEMS:
        out.append("cfg['system']='%s' is not in the CFS system table (%s) -- use the exact key"
                   % (cfg.get("system"), "/".join(sorted(_CS.SYSTEMS))))
    if R is not None and float(R)<=3.0 and sysname not in ("sbmf","gypsum_wall"):
        # gypsum (R=2) is EXEMPT: it IS an S400 system (E5 supplies its capacities).
        # The note is also SUPPRESSED once the package confirms the governing hazard
        # (a 'governing_hazard' block or explicit 'wind governs'/'seismic governs'
        # statement) -- the note asks for exactly that confirmation.
        _blobR = json.dumps(pkg).lower() if isinstance(pkg, dict) else ""
        if not (isinstance(pkg, dict) and ("governing_hazard" in pkg or
                "wind governs" in _blobR or "seismic governs" in _blobR)):
            out.append("R=%.2f -> 'not specifically detailed for seismic' -- AISI S400 detailing does "
                       "NOT apply (no capacity-design chain); design to S100 (+S240 framing) only, and "
                       "CONFIRM whether wind or seismic governs each direction (record a "
                       "'governing_hazard' statement in the package to clear this note)"%float(R))
    # drift limit vs risk category (Table 12.12-1, light-frame 0.025 row where it applies)
    if _CS is not None and wall_path and sysname in _WALL_SYSTEMS:
        n_st = int(cfg.get("stories") or len(cfg.get("heights_ft") or []) or 0)
        rc = str(cfg.get("risk_cat","II"))
        want = _CS.drift_limit(sysname, n_st, rc)
        have = cfg.get("drift_limit")
        if have is not None and _isnum(have) and float(have) > want + 1e-6:
            out.append("cfg['drift_limit']=%.3f exceeds the Table 12.12-1 value %.3f for %s, %d "
                       "stories, RC %s -- tighten it" % (float(have), want, sysname, n_st, rc))
    # diaphragm: FLEXIBLE is the light-frame default; a RIGID declaration must be justified
    _dia = str(cfg.get("diaphragm","flexible" if wall_path else "rigid")).lower()
    if wall_path and _dia == "rigid" and isinstance(pkg,dict):
        _blob=json.dumps(pkg).lower()
        if not any(w in _blob for w in ("concrete", "gyp-crete", "topping", "12.3.1.2", "justif")):
            out.append("cfg['diaphragm']='rigid' on a light-frame wall building without justification "
                       "-- flexible is the 12.3.1.1 default; rigid-plate torsional redistribution "
                       "must be justified (e.g. concrete topping) or the model corrected")
    if _dia in ("flexible","semi-rigid") and isinstance(pkg,dict):
        _blob2=_cd_blob(pkg)+" "+json.dumps(pkg).lower()
        if "tributary" not in _blob2:
            out.append("cfg['diaphragm']='%s' declared but calc_package never distributes lateral "
                       "force by TRIBUTARY AREA -- per-line shears, chords and collectors must come "
                       "from the tributary model (ASCE 7-22 12.3.1)"%_dia)
    # analysis-fidelity tier vs declared structure kind
    if _CS is not None:
        warns=_CS.preflight_fidelity(cfg.get("structure_kind","wall"),
                                     cfg.get("analysis_fidelity",0))
        out += ["fidelity: " + w for w in warns]
    _dex = cfg.get("drift_exempt_stories") or {}
    if _dex and isinstance(pkg,dict):
        _blob3=json.dumps(pkg).lower()
        if not any(w in _blob3 for w in ("step","split-level","inter-diaphragm","offset")):
            out.append("drift_exempt_stories declared (%s) but calc_package has no step/split-level "
                       "transfer detail -- the exempted inter-diaphragm racking must be a DESIGNED "
                       "detail (shear transfer across the offset)"%sorted(_dex))
    # collectors: declared collector lines (or recorded plan irregularity) need designed entries
    coll_lines = cfg.get("collector_lines") or []
    _sp=((pkg or {}).get("framework_screen") or {}).get("plan") or {}
    if (coll_lines or _sp.get("reentrant") or _sp.get("setback")) and isinstance(pkg,dict):
        colls=(pkg.get("collectors") or []) + (pkg.get("connections") or [])
        has_coll=any(isinstance(c,dict) and "collector" in (str(c.get("id",""))+str(c.get("type",""))).lower()
                     and (c.get("DC") is not None or c.get("checks") or c.get("waived")) for c in colls)
        if not has_coll:
            out.append("re-entrant/step/declared collector lines present but NO designed collector in "
                       "calc_package -- collectors are a REQUIRED deliverable (Omega_0 per 12.10.2.1), "
                       "not 'delegated to the drawings'")
    return out


_SYS_REQUIRED = {
    "wsp_shearwall":   [("per-line sheathing + FASTENER schedule (S400 E1 basis)", ["fastener"]),
                        ("expected wall strength into collectors / story below", ["expected"])],
    "steelsheet_wall": [("per-line sheathing + FASTENER schedule (S400 E2 basis)", ["fastener"]),
                        ("expected wall strength into collectors / story below", ["expected"])],
    "strap_braced":    [("strap ductility An*Fu >= Ag*Fy", ["anfu","an fu","an*fu","ag fy","agfy","rupture"]),
                        ("capacity design from strap Ry*Fy*Ag (connections/chords/anchorage)",
                         ["ryfy","ry fy","ry*fy","expected"])],
    "sbmf":            [("expected beam strength at design drift", ["expected","design drift"])],
}

def _cd_blob(pkg):
    """All text in capacity_design -- dict KEYS + string values + numbers -- so a computed check stored with
    descriptive keys (Ic_provided: 1350) is detected, not just free-text notes."""
    cd=pkg.get("capacity_design") if isinstance(pkg,dict) else None
    parts=[]
    def walk(o):
        if isinstance(o,dict):
            for k,v in o.items(): parts.append(str(k)); walk(v)
        elif isinstance(o,list):
            for v in o: walk(v)
        else: parts.append(str(o))
    walk(cd if cd is not None else {})
    return " ".join(parts).lower()

def _named_not_computed_issues(pkg):
    out=[]; cd=pkg.get("capacity_design") if isinstance(pkg,dict) else None
    if cd is None: return out
    txt=[]; _gather_text(cd,txt)
    for t in txt:
        if not isinstance(t,str) or not re.search(r">=|<=|≥|≤",t): continue
        bare=set(v.lower() for v in re.findall(r"(?<![A-Za-z0-9])[A-Za-z](?![A-Za-z0-9])", t))  # single-letter symbols (t,h,L,e,...)
        if len(bare) >= 2:   # a symbolic formula with >=2 unresolved variables -> not numerically evaluated
            out.append("[capacity_design] '%s' is a symbolic REQUIREMENT, not a COMPUTED check -- substitute the section's numbers and give value vs limit + D/C"%t.strip()[:80])
    return out

def _system_checks_issues(cfg, pkg):
    out=[]
    if not isinstance(cfg, dict): return out
    sysname=str(cfg.get("system") or "").lower()
    if not sysname or not isinstance(pkg,dict): return out
    blob=_cd_blob(pkg)
    for key,reqs in _SYS_REQUIRED.items():
        if key in sysname:
            for label,kws in reqs:
                if not any(kw in blob for kw in kws):
                    out.append("system '%s' requires check: %s -- not found in capacity_design (add it, computed)"%(cfg.get("system"),label))
    return out

def _height_limit_issues(cfg):
    """Table 12.2-1 height limits via the CFS system table (65 ft walls/straps, 35 ft SBMF in
    SDC D-F; gypsum NP in E/F). SDC proxied from SDS/S1 as elsewhere in the repo."""
    out=[]
    if not isinstance(cfg, dict): return out
    s=cfg.get("seis") or {}
    Hft=[float(h) for h in (cfg.get("heights_ft") or []) if _isnum(h)]
    if not Hft:
        H=[float(h) for h in (cfg.get("heights") or []) if _isnum(h)]
        Hft=[h/12.0 for h in H]
    if not Hft: return out
    hn=sum(Hft); SDS=float(s.get("SDS",0) or 0); S1=float(s.get("S1",0) or 0)
    sysname=str(cfg.get("system") or "").lower()
    if SDS < 0.33:  sdc="B"
    elif SDS < 0.50: sdc="C"
    elif S1 >= 0.75: sdc="E"
    else: sdc="D"
    try:
        import cfs_systems as _CS
        if sysname in _CS.SYSTEMS:
            ok, msg = _CS.height_check(sysname, sdc, hn)
            if not ok:
                out.append(msg + " -- FLAG and resolve (podium base redefinition / system change); "
                           "note a podium building measures hn from the PODIUM TOP")
    except Exception:
        pass
    return out

def _transfer_issues(cfg, name, pkg):
    out=[]
    try:
        import engine3d as _E
        pir=_E.plan_irregularities(_E.CFG.get(name) or cfg)
    except Exception:
        return out
    if pir.get("setback") and isinstance(pkg,dict):
        txt=[]; _gather_text([pkg.get("capacity_design"), pkg.get("connections")],txt)
        blob=" ".join(t for t in txt if isinstance(t,str)).lower()
        if "transfer" not in blob and "backstay" not in blob:
            out.append("footprint SETBACK detected -- design the TRANSFER/backstay diaphragm chords/collectors and supporting members for Omega_0 (12.3.3.3) and report the backstay force")
    return out

def _nonparallel_issues(cfg, pkg):
    out=[]
    if isinstance(cfg, dict) and cfg.get("skew") and isinstance(pkg,dict):
        if "biaxial" not in _cd_blob(pkg):
            out.append("nonparallel/skewed frame -- resolve stiffness into BOTH principal directions and apply BIAXIAL SCWB/interaction at the skewed columns (not evident in capacity_design)")
    return out



def _consultancy_issues(cfg, pkg):
    """Tier A/B real-world guards (EDGE_CASE_SWEEP): each WARN clears when the package addresses
    the topic, so they are reconcilable by DOING the check, not by boilerplate."""
    out = []
    if not isinstance(cfg, dict) or not isinstance(pkg, dict):
        return out
    blob = json.dumps(pkg).lower()
    arch = (str(cfg.get("arch", "")) + " " + str(cfg.get("system", ""))).lower()
    mem = pkg.get("members") or []
    # A3 ponding: long-span flat roof beams and no ponding statement
    roof_long = any(isinstance(m, dict) and (m.get("inputs") or {}).get("role") == "roof"
                    and float((m.get("inputs") or {}).get("length_in") or 0) >= 480 for m in mem)
    if roof_long and "ponding" not in blob:
        out.append("long-span (>=40 ft) roof framing and NO ponding evaluation in calc_package -- "
                   "check ponding stability/impounded rain (AISC 360-22 App. 2 / ASCE 7-22 Ch. 8: "
                   "roof slope + secondary drainage head) and record it")
    # A6 footfall vibration: long floor spans or vibration-sensitive occupancy
    floor_long = any(isinstance(m, dict) and (m.get("inputs") or {}).get("role") == "floor"
                     and float((m.get("inputs") or {}).get("length_in") or 0) >= 480 for m in mem)
    sens = any(k in arch for k in ("lab", "laborator", "hospital", "gym", "assembly", "vibration"))
    if (floor_long or sens) and "vibration" not in blob:
        out.append("footfall VIBRATION serviceability not addressed (long floor spans and/or "
                   "vibration-sensitive occupancy) -- do the AISC Design Guide 11 screen (fn, a_peak "
                   "vs occupancy limit) and record it")
    # A8 seismic joint / pounding: multi-wing keywords and no joint decision
    if any(k in arch for k in ("twin", "two tower", "wings", "wing ")) and \
            not any(k in blob for k in ("seismic joint", "pounding", "joint width", "no seismic joint")):
        out.append("multi-wing/tower configuration and NO seismic-joint decision recorded -- either "
                   "size the joint (sum of Cd-amplified drifts, ASCE 7-22 12.12.3 + pounding check) "
                   "or record why the wings are intentionally connected (with the interaction designed)")
    # B6 snow drift at steps/parapets
    stepish = any(k in arch for k in ("step", "setback", "parapet", "penthouse", "tier", "wedding"))
    if float(cfg.get("snow", 0) or 0) > 0 and stepish and "drift" not in blob.replace("drift_", ""):
        out.append("snow present with roof steps/parapets/setbacks and no DRIFT surcharge in the "
                   "package (ASCE 7-22 7.7/7.8) -- add the drift check to the step-adjacent members")
    # B8 delegated-design register
    if any(k in blob for k in ("joist", "sji", " deck", "brb", "stair", "curtain wall")) and \
            "delegat" not in blob:
        out.append("delegated-design components referenced (joists/deck/BRBs/stairs/cladding) but no "
                   "'delegated_design' register in capacity_design -- list each delegated item, the "
                   "design criteria handed off, and the interface forces")
    return out


_HD_BANDS = {"strap": 5.0, "bolted": 20.0}      # kip; mirror wall_line.pick_holddown envelopes


def _cfs_slot_issues(cfg, pkg):
    """CFS-specific slot screens: sheathing+fastener completeness, hold-down band vs cumulative
    TENSION (and the sized-for-shear red flag), Type II mechanics, the net-uplift path, and
    unresolved pipeline gate flags."""
    out = []
    if not isinstance(pkg, dict):
        return out
    blob = json.dumps(pkg).lower()
    for w in pkg.get("wall_lines") or []:
        if isinstance(w, dict) and not w.get("waived") and \
                not (w.get("sheathing") and w.get("fastener_schedule")):
            out.append("[wall %s] no sheathing + fastener_schedule -- a wall capacity without them "
                       "is unverifiable (S400 tables are sheathing x fastener x sides)" % w.get("id"))
    for h in pkg.get("holddowns") or []:
        if not isinstance(h, dict) or h.get("waived"):
            continue
        T = _num(h.get("T_cum_kip"))
        dev = str(h.get("device_class") or "").lower()
        cap = _HD_BANDS.get(dev)
        if T is not None and cap is not None and T > cap * 1.02:
            out.append("[hold-down %s] cumulative tension %.1f kip exceeds the %s-class envelope "
                       "(~%.0f kip) -- switch to a COMPUTED continuous rod (tension + PL/AE "
                       "elongation + take-up; elongation feeds drift)" % (h.get("id"), T, dev, cap))
        _basis = str(h.get("basis", "")).lower()
        if "shear" in _basis and "tension" not in _basis:
            out.append("[hold-down %s] appears sized for SHEAR -- hold-downs resist the cumulative "
                       "OVERTURNING TENSION, never the shear (instant red flag)" % h.get("id"))
    # Type II (perforated) mechanics
    if ("type ii" in blob or "perforated" in blob):
        if "adjustment" not in blob:
            out.append("Type II (perforated) walls referenced but NO adjustment-factor calculation "
                       "in the package -- show the S400 Type II factor on the full-length capacity")
        if not ("distributed" in blob and ("track" in blob or "anchorage" in blob)):
            out.append("Type II walls need END hold-downs PLUS distributed track anchorage between "
                       "-- not evident in the package")
        if any(k in blob for k in ("every pier", "each pier", "per pier")):
            out.append("Type II wall with hold-downs at EVERY PIER -- that silently reverts the "
                       "wall to Type I; anchor the wall ENDS only, distributed track anchorage between")
    # net-uplift path on wind-relevant briefs
    wind_v = _num((cfg or {}).get("wind", {}).get("V")) if isinstance(cfg, dict) else None
    windy = (isinstance(cfg, dict) and str(cfg.get("governing", "")).lower() == "wind") or \
            (wind_v is not None and wind_v >= 115)
    if windy and not ("0.9" in blob and "uplift" in blob):
        out.append("wind-governed/high-wind brief and no 0.9D+1.0W NET-UPLIFT case in the package "
                   "-- the uplift path (roof-to-wall, wall-to-floor, floor-to-foundation) is a "
                   "REQUIRED anchorage design chain")
    # unresolved pipeline gates
    for key, what in (("model_vs_tributary_flags", "model-vs-tributary divergence"),
                      ("drift_flags", "drift limit exceedance")):
        flags = pkg.get(key) or []
        if flags and not pkg.get(key + "_resolution"):
            out.append("%d unresolved %s flag(s) -- fix the design and re-run, or record the "
                       "engineering justification in pkg['%s_resolution']" % (len(flags), what, key))
    return out


def check(name, root=None, pkg=None, verbose=True):
    """Run the self-consistency check. Returns a list of issue strings ([] == consistent)."""
    issues = []
    if pkg is None:
        if root is None:
            base = os.environ.get("STEEL_BUILDER_JOBS") or _here_repo()
            root = os.path.join(base, name)
        path = os.path.join(root, "design", "calc_package.json")
        if not os.path.exists(path):
            # CFS path (cfs_pipeline) writes calc_package_cfs.json -- fall back to it
            _cfs = os.path.join(root, "design", "calc_package_cfs.json")
            if os.path.exists(_cfs):
                path = _cfs
        if not os.path.exists(path):
            issues.append(f"calc_package.json not found at {path} -- run design_and_report first")
            if verbose:
                _print(name, issues)
            return issues
        try:
            pkg = json.load(open(path, encoding="utf-8"))
        except Exception as ex:
            issues.append(f"calc_package.json is not valid JSON: {ex}")
            if verbose:
                _print(name, issues)
            return issues

    members = pkg.get("members") or []
    conns = pkg.get("connections") or []
    if not isinstance(conns, list) or len(conns) == 0:
        issues.append("connections list is empty -- strap/uplift-clip/track-anchorage connections "
                      "are a REQUIRED deliverable (design them in place; demands-and-basis alone "
                      "is incomplete)")
    for kind, lst in (("member", members), ("connection", conns),
                      ("wall", pkg.get("wall_lines") or []),
                      ("hold-down", pkg.get("holddowns") or []),
                      ("stud", pkg.get("studs") or []),
                      ("collector", pkg.get("collectors") or [])):
        for m in lst:
            if isinstance(m, dict) and not m.get("waived"):
                issues += _entry_issues(kind, m)

    _root = root if root else os.path.join(os.environ.get("STEEL_BUILDER_JOBS") or _here_repo(), name)
    if not os.path.exists(os.path.join(_root, "cfg.py")):
        issues.append("jobs/%s/cfg.py not found -- write the building's cfg (including any custom_build) to cfg.py "
                      "FIRST and keep it; the saved OpenSees model must be reproducible and editable for later studies." % name)
    _dcfg = _load_cfg(_root, name)
    issues += _geometry_issues(_dcfg)                       # units/geometry sanity (story heights in ft, etc.)
    issues += _design_basis_issues(_dcfg, name, pkg)        # R1/R2/R8/R12/R14/R16
    issues += _height_limit_issues(_dcfg)                   # R19 system height limit
    issues += _transfer_issues(_dcfg, name, pkg)            # R7/R15 transfer/backstay
    issues += _nonparallel_issues(_dcfg, pkg)               # R10 skewed frame
    issues += _consultancy_issues(_dcfg, pkg)               # Tier A/B real-world guards
    issues += _named_not_computed_issues(pkg)               # R9 named-not-computed
    issues += _system_checks_issues(_dcfg, pkg)             # per-system S400 required checks
    issues += _cfs_slot_issues(_dcfg, pkg)                  # CFS slot screens (walls/HDs/TypeII/uplift/gates)
    issues += _completeness_issues(pkg)
    if verbose:
        _print(name, issues)
    return issues


def _print(name, issues):
    print("[consistency] %s: %d issue(s)" % (name, len(issues)))
    for i in issues:
        print("   -", i)
    print("RESULT:", "PASS (self-consistent)" if not issues else "FAIL (%d to reconcile)" % len(issues))


def _selftest():
    print("consistency self-test (CFS screens)")
    cfg = dict(stories=3, heights_ft=[10.0, 9.5, 9.5], plan_ft=(96.0, 48.0),
               seis=dict(SDS=1.0, SD1=0.5, S1=0.4, R=6.5, Cd=4.0, Ie=1.0),
               system="wsp_shearwall", risk_cat="II", structure_kind="wall",
               analysis_fidelity=0, diaphragm="flexible", governing="wind",
               wind=dict(V=140), collector_lines=["reentrant-NE"])
    bad = dict(
        wall_lines=[dict(id="wall-X-A-s1", sheathing=None, fastener_schedule=None,
                         limit_state="S400 E1", cited="S400 E1", DC=1.12,
                         inputs=dict(section=None))],
        holddowns=[dict(id="hd-X-A", T_cum_kip=27.0, device_class="bolted", DC=0.9,
                        limit_state="tension", cited="S400", basis="sized for the shear force"),
                   dict(id="hd-X-B", T_cum_kip=9.0, device_class="bolted", DC=0.5,
                        limit_state="tension", cited="S400",
                        basis="cumulative overturning tension")],
        studs=[dict(id="stud-typ", section="600S162-54", limit_state="E2+E4", cited="S100",
                    DC=1.08)],
        collectors=[], connections=[],
        drift_flags=["X line A story 1"],
        notes="Type II perforated walls on line A with hold-downs at every pier")
    iss = check("SELFTEST", pkg=bad, verbose=False)
    # cfg-based screens need the cfg passed through the module surface: run them directly
    iss += _design_basis_issues(cfg, "SELFTEST", bad)
    iss += _height_limit_issues(dict(cfg, heights_ft=[10.0] * 8))   # 80 ft > 65-ft SDC D limit
    iss += _geometry_issues(dict(cfg, heights_ft=[114.0, 9.5]))     # inches-in-feet slip
    iss += _cfs_slot_issues(cfg, bad)
    blob = "\n".join(iss)
    for want in ("sheathing + fastener_schedule", "exceeds the bolted-class envelope",
                 "sized for SHEAR", "adjustment-factor", "EVERY PIER", "NET-UPLIFT",
                 "unresolved drift limit", "600S162-68", "65", "look like INCHES",
                 "connections list is empty", "tributary"):
        assert want in blob, "missing screen: %s\n%s" % (want, blob)
    # a clean package + cfg raises none of the CFS screens
    good = dict(
        wall_lines=[dict(id="wall-X-A-s1", sheathing="7/16 OSB", fastener_schedule="#8@4/12",
                         limit_state="S400 E1", cited="S400 E1 (wind column)", DC=0.91,
                         capacity=1015, basis="tributary")],
        holddowns=[dict(id="hd-X-A", T_cum_kip=9.0, device_class="bolted", DC=0.55,
                        limit_state="tension", cited="S400", basis="cumulative tension")],
        studs=[dict(id="stud-typ", section="600S162-54", limit_state="E2+E4+G5",
                    cited="S100 E2/E4/G5", DC=0.82)],
        collectors=[dict(id="collector-NE", limit_state="tension", cited="12.10.2.1 + S100 D",
                         DC=0.7)],
        connections=[dict(id="uplift-clip", limit_state="screw shear", cited="S100 J", DC=0.8)],
        capacity_design=dict(system="wsp_shearwall",
                             note="expected wall strength to collectors; fastener schedule basis; "
                                  "0.9D+1.0W net uplift path designed; tributary distribution"),
        drift_flags=[], model_vs_tributary_flags=[])
    iss2 = [i for i in check("SELFTEST", pkg=good, verbose=False)]
    iss2 += _design_basis_issues(cfg, "SELFTEST", good) + _cfs_slot_issues(cfg, good)
    iss2 = [i for i in iss2 if "cfg.py not found" not in i]
    assert iss2 == [], iss2
    print("  bad package raised %d issue(s); clean package raised none" % len(iss))
    print("SELF-TEST PASS")


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["SELFTEST"] or not sys.argv[1:]:
        _selftest()
    else:
        for nm in sys.argv[1:]:
            check(nm)

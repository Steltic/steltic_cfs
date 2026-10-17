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

PACKAGE FILE (CFS-13). ONE authoritative package per job:
  * CFS jobs (wall path lines_x/lines_y, portal path span_ft) -> design/calc_package_cfs.json
    (written by cfs_pipeline; the name the report, the completion gate and the contract use);
  * hot-rolled grid jobs -> design/calc_package.json.
package_path() resolves it. A CFS job that only has a legacy design/calc_package.json is still
read (compatibility), and a CFS job that has BOTH files is an ISSUE (the duplicate is never
silently ignored -- merge it into calc_package_cfs.json and delete it).

check() also writes design/consistency_result.json (issues + the sha1/mtime of the package it
checked) -- the app-side completion gate requires that stamp to be clean and fresh.
"""
import os, json, re, hashlib, time

TOL = 0.04       # 4% relative tolerance on recomputed ratios
ABS_TOL = 0.01   # ...with an absolute floor: |D/C - demand/capacity| <= 0.01 always passes (CFS-30)

PKG_CFS = "calc_package_cfs.json"
PKG_HR = "calc_package.json"


def _here_repo():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root (engine/..)


def is_cfs_cfg(cfg):
    """Same test as pipeline._is_cfs_cfg: wall path carries lines_x/lines_y, portal span_ft."""
    return isinstance(cfg, dict) and ("lines_x" in cfg or "lines_y" in cfg or "span_ft" in cfg
                                      or bool(cfg.get("spans")))


def package_path(root, cfg=None):
    """(path, notes, issues) for the job's ONE authoritative calc package (CFS-13).
    path is None when no package exists. notes are informational (compatibility read);
    issues are gate failures (two package files on one CFS job)."""
    d = os.path.join(root, "design")
    p_cfs, p_hr = os.path.join(d, PKG_CFS), os.path.join(d, PKG_HR)
    has_cfs, has_hr = os.path.exists(p_cfs), os.path.exists(p_hr)
    cfs_job = has_cfs or is_cfs_cfg(cfg)
    notes, issues = [], []
    if not cfs_job:
        return (p_hr if has_hr else None), notes, issues
    if has_cfs and has_hr:
        issues.append("TWO package files: design/%s (authoritative, CFS) AND design/%s -- the "
                      "second is NOT read by the report/gate; merge any fills it holds into "
                      "design/%s and delete design/%s" % (PKG_CFS, PKG_HR, PKG_CFS, PKG_HR))
        return p_cfs, notes, issues
    if has_cfs:
        return p_cfs, notes, issues
    if has_hr:
        notes.append("legacy package name design/%s read on a CFS job (compatibility) -- the "
                     "authoritative CFS name is design/%s" % (PKG_HR, PKG_CFS))
        return p_hr, notes, issues
    return None, notes, issues


# ---- seed-owned package keys (CFS-29/30) -------------------------------------------------------
# Text the PIPELINE writes into the package must never satisfy (or trip) a gate that is meant to
# test the AGENT's work. These keys are skipped when the gates scan for agent evidence.
SEED_KEYS = frozenset((
    "basis", "note", "notes", "instruction", "seed_basis", "Om0_eff_basis", "dead_relief_basis",
    "demand_basis", "governing_basis", "feasibility_note", "rho_basis", "combos", "combos_note",
    "preflight_warnings", "wind_basis", "elf", "code", "building", "Ry_by_Fy", "Rt_by_Fy",
    "analysis_basis", "uplift_note", "Om0", "Om0_eff", "Ve_cap_by_story_kip", "T_cd_seed_kip",
    "dead_relief_kip_available", "Ve_cap_bay_by_story_kip", "T_cd_bay_seed_kip", "T_cum_kip",
    "T_bay_seed_kip", "T_wind_kip", "k_kip_in", "v_unit_plf", "V_kip", "v_wind_plf",
    "P_cum_kip_by_story", "trib_ft", "model_vs_tributary_flags", "drift_flags", "framework_screen",
    "two_stage_framework", "independent_tributary", "period_rayleigh", "sections", "kind",
    "structure_kind", "system", "rho", "id", "direction", "line", "story", "component_mode",
    # cfs-wallloads / cfs-walldrift seeds (framework text and numbers, never agent evidence)
    "Omega_E", "Omega_E_basis", "T_Om0_stack_kip", "T_OmegaE_Vn_stack_kip", "Vn_basis",
    "Vn_selected_kip", "type_ii_Ca_by_story", "Om0_basis", "wind_basis_by_dir",
    "diaphragm_basis", "drift_basis", "drift_limit_basis", "L_factor_basis", "Ca_basis",
    "capacity_design_applicability", "elf_by_direction", "weight_by_level_kip",
    "stability_warnings", "by_direction", "strap_ductility",
    "gravity_framing", "framework_slot", "v_elf_plf", "v_elf_by_story_plf", "v_cd_seed_plf",
    "v_cd_by_story_plf", "L_ft"))


def _walk_agent(obj, skip=SEED_KEYS):
    """Yield (key, value) leaves of obj, skipping seed-owned keys at every depth."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in skip:
                continue
            if isinstance(v, (dict, list)):
                yield str(k), None
                for kv in _walk_agent(v, skip):
                    yield kv
            else:
                yield str(k), v
    elif isinstance(obj, list):
        for v in obj:
            for kv in _walk_agent(v, skip):
                yield kv


def _agent_text(obj):
    """Lower-case blob of agent-owned KEYS + string values (seed-owned keys excluded)."""
    parts = []
    for k, v in _walk_agent(obj):
        parts.append(k)
        if isinstance(v, str):
            parts.append(v)
    return " ".join(parts).lower()


def _agent_has_number(obj):
    for k, v in _walk_agent(obj):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return True
        if isinstance(v, str) and re.search(r"\d", v):
            return True
    return False


_NEG = re.compile(r"\b(no|not|non|without|none|never|n/a)\b[\w\s,/()-]{0,25}$")


_NUM_RESULT = re.compile(r"(?:=|≤|≥|<=|>=|<|>|~)\s*-?\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*(?:kips?|k-?ft|kip-?ft|"
                         r"kip-?in|k-?in|in\.?|ksi|psf|plf|ft|%|rad|in\^?[234])(?![a-z])", re.I)
_CLAUSE_KEYS = ("cite", "cited", "clause", "basis", "method", "rule", "note", "notes", "reference", "ref")


def _has_numeric_result(obj, _key=""):
    """True when obj carries a COMPUTED number: a JSON number (not a bool) under a non-citation key,
    or a string with a value after '=' / a comparator or a number with an engineering unit. Clause
    numbers ('S400 E3.4.1', 'Eq. 12.8-2') do not count (HR-33 port from steltic)."""
    if isinstance(obj, bool) or obj is None:
        return False
    if isinstance(obj, (int, float)):
        return str(_key).lower() not in _CLAUSE_KEYS
    if isinstance(obj, str):
        return bool(_NUM_RESULT.search(obj))
    if isinstance(obj, dict):
        return any(_has_numeric_result(v, k) for k, v in obj.items())
    if isinstance(obj, list):
        return any(_has_numeric_result(v, _key) for v in obj)
    return False


def _hazard_numbers(gh):
    """(wind_ok, seismic_ok): the package's governing_hazard record carries a COMPUTED wind AND a
    computed seismic value (numbers, in a key or a sentence) -- a bare 'wind governs' does not."""
    wind = seis = False
    def walk(o, key=""):
        nonlocal wind, seis
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        elif _has_numeric_result(o, key):
            txt = (str(key) + " " + (o if isinstance(o, str) else "")).lower()
            wind = wind or bool(re.search(r"wind", txt))
            seis = seis or bool(re.search(r"seism|(^|_)e(q|h)?(_|$)|\bcs\b|\belf\b", txt))
    walk(gh)
    return wind, seis


def _mentions(blob, term):
    """True if `term` occurs in blob at least once NOT preceded (within ~25 chars on the same
    clause) by a negation (no / not / non / without / none / never / n/a) -- CFS-30: 'no Type II
    walls' must not trip the Type II screen."""
    for m in re.finditer(re.escape(term), blob):
        pre = blob[max(0, m.start() - 30):m.start()]
        pre = re.split(r"[.;:\n]", pre)[-1]
        if not _NEG.search(pre):
            return True
    return False


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


def _close(a, b, tol=TOL, abs_tol=ABS_TOL):
    if a is None or b is None:
        return True
    if abs(a - b) <= abs_tol:          # absolute floor: 0.010 vs 0.012 at tiny D/C is not a conflict
        return True
    return abs(a - b) / max(abs(a), abs(b), 1e-9) <= tol


_UNIT_SUFFIXES = ("_kip_in", "_kip_ft", "_kipin", "_kipft", "_k_in", "_k_ft", "_kips", "_kip",
                  "_kft", "_kin", "_plf", "_klf", "_lbs", "_lb", "_in", "_ksi", "_psf", "_kn")


def _norm_key(k):
    """'phiPn_kip' -> 'phipn', 'T_design_kip' -> 'tdesign', 'Mu_kipin' -> 'mu' (unit suffix and
    separators stripped) so demand/capacity keys are matched EXACTLY, never by substring."""
    k = str(k).strip().lower().replace("φ", "phi")
    changed = True
    while changed:
        changed = False
        for suf in _UNIT_SUFFIXES:
            if k.endswith(suf) and len(k) > len(suf):
                k = k[:-len(suf)]
                changed = True
    return re.sub(r"[\s_\-\.]", "", k)


# EXACT (normalized) demand / capacity field names (CFS-30: the old substring match read
# phiPn_kip as BOTH the demand ('_kip') and the capacity ('phi') -> '50/50 = 1.000').
DEMAND_KEYS = frozenset(("demand", "required", "requiredstrength", "ru", "pu", "mu", "vu", "tu",
                         "mux", "muy", "pr", "mr", "vr", "tr", "nu", "tdesign", "designt",
                         "tdemand", "demandt", "puplift", "tuplift", "vdesign", "pdesign",
                         "mdesign"))
CAPACITY_KEYS = frozenset(("capacity", "phirn", "phipn", "phimn", "phivn", "phitn", "phipne",
                           "phipnl", "phipnd", "phimne", "phimnl", "phimnd", "phivnw",
                           "available", "availablestrength", "designstrength", "phirnw",
                           "phitnrod", "phipnb", "phipnov", "phipnot"))


def _find(d, keys):
    """First numeric value in dict d whose NORMALIZED key is in `keys` (exact match)."""
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if _norm_key(k) in keys:
            n = _num(v)
            if n is not None:
                return n
    return None


def _is_interaction(name):
    """Interaction / combined rows (H1, H2, unity sums) are NOT a single demand/capacity ratio."""
    s = str(name or "").lower()
    return bool(re.search(r"\bh[123]\b|h1\.|h2\.|interaction|combined|unity|\+", s))


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
        dem = _find(c, DEMAND_KEYS)
        cap = _find(c, CAPACITY_KEYS)
        if _is_interaction(cls) or _is_interaction(c.get("name")):
            dem = cap = None                 # an H1 sum is not P/Pcap -- never recompute it
        if dem is not None and cap not in (None, 0.0):
            rec = dem / cap
            if cdc is not None and not _close(rec, cdc):
                out.append(f"[{kind} {cid}] check '{cls}': D/C {cdc:.3f} != demand/capacity "
                           f"{dem:.3g}/{cap:.3g} = {rec:.3f} -- reconcile")
            elif cdc is None:
                cdc = rec
        if cdc is not None:
            check_dcs.append((cls, cdc))
    # schedule rows (purlin/girt/strap/stud schedules) carry their own D/Cs -- they count too
    for r in (entry.get("rows") if isinstance(entry.get("rows"), list) else []):
        if isinstance(r, dict) and not r.get("waived"):
            rid = r.get("id") or r.get("member") or r.get("panel") or r.get("zone") or "?"
            rdc = _dc_num(r.get("DC"))
            rdem, rcap = _find(r, DEMAND_KEYS), _find(r, CAPACITY_KEYS)
            if rdc is not None and rdem is not None and rcap not in (None, 0.0) \
                    and not _is_interaction(r.get("limit_state")):
                if not _close(rdem / rcap, rdc):
                    out.append(f"[{kind} {cid}] row '{rid}': D/C {rdc:.3f} != demand/capacity "
                               f"{rdem:.3g}/{rcap:.3g} = {rdem / rcap:.3f} -- reconcile")
            if rdc is not None:
                check_dcs.append(("row %s" % rid, rdc))

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
                out += _waiver_issues(kind, entry)   # NG waivers need a scoped justification (CFS-29d)
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

    # one-value screen on the entry's own text; schedule ROWS are different items by design
    out += _one_value_issues(kind, cid, {k: v for k, v in entry.items() if k != "rows"})
    return out


def worst_dc(entry):
    """Worst numeric D/C over an entry's headline, checks and schedule rows (None if none)."""
    if not isinstance(entry, dict):
        return None
    vals = [_dc_num(entry.get("DC"))]
    for c in (entry.get("checks") if isinstance(entry.get("checks"), list) else []):
        if isinstance(c, dict):
            vals.append(_dc_num(c.get("DC")))
    for r in (entry.get("rows") if isinstance(entry.get("rows"), list) else []):
        if isinstance(r, dict):
            vals.append(_dc_num(r.get("DC")))
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


# a waiver of an NG (D/C > 1.0) result is acceptable ONLY for an item outside the design scope
WAIVER_SCOPES = ("existing", "by_others", "out_of_scope", "not_applicable")
_WAIVER_SCOPE_WORDS = ("existing", "retrofit", "by others", "out of scope", "not in scope",
                       "outside the scope", "delegated", "handed off", "eor of record",
                       "not applicable", "does not apply")


def _waiver_issues(kind, entry):
    """CFS-29(d): a waiver is an explicit, JUSTIFIED exemption -- never a silent pass.
      * every waiver needs a real justification (>= 15 characters of text);
      * a waived item that carries D/C > 1.0 additionally needs a declared scope
        (entry['waiver_scope'] in WAIVER_SCOPES, or the justification says existing /
        retrofit / by others / out of scope ...) -- a new member cannot be waived NG."""
    out = []
    cid = entry.get("id") or entry.get("check") or "?"
    w = entry.get("waived")
    txt = w if isinstance(w, str) else ""
    if len(txt.strip()) < 15:
        out.append("[%s %s] waived without an engineering justification (waived=%r) -- state "
                   "why the slot does not apply (>= one sentence) or design it" % (kind, cid, w))
    wd = worst_dc(entry)
    if wd is not None and wd > 1.0 + 1e-9:
        scope = str(entry.get("waiver_scope") or "").lower()
        scoped = scope in WAIVER_SCOPES or any(s in txt.lower() for s in _WAIVER_SCOPE_WORDS)
        if not scoped:
            out.append("[%s %s] WAIVED with D/C = %.3f > 1.0 -- an NG result may be waived only "
                       "for a scoped EXISTING / by-others / out-of-scope item: set "
                       "waiver_scope (%s) with the justification, or redesign"
                       % (kind, cid, wd, "/".join(WAIVER_SCOPES)))
    return out


def waived_ng_items(pkg):
    """[(kind, id, D/C, justification)] for every waived entry whose D/C exceeds 1.0 -- the
    report lists these explicitly (CFS-29d / CFS-31)."""
    out = []
    if not isinstance(pkg, dict):
        return out
    for kind, key in (("wall", "wall_lines"), ("hold-down", "holddowns"), ("stud", "studs"),
                      ("collector", "collectors"), ("member", "members"),
                      ("connection", "connections"), ("anchorage", "anchorage"),
                      ("schedule", "schedules"), ("drift", "drift_table")):
        for e in pkg.get(key) or []:
            if isinstance(e, dict) and e.get("waived"):
                wd = worst_dc(e)
                if wd is not None and wd > 1.0 + 1e-9:
                    out.append((kind, e.get("id") or e.get("check") or "?", wd, str(e["waived"])))
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
    if "span_ft" in cfg or "spans" in cfg:      # CFS PORTAL path (cfs_frame): FEET (CFS-12 lint)
        spans = [cfg.get("span_ft")] + [s.get("span_ft") for s in (cfg.get("spans") or [])
                                        if isinstance(s, dict)]
        for sp in spans:
            if _isnum(sp) and float(sp) > 200:
                out.append("portal span_ft=%g looks like INCHES -- the portal path (cfs_frame) is "
                           "FEET: a 48 ft span is 48, not 576" % float(sp))
            elif _isnum(sp) and float(sp) < 8:
                out.append("portal span_ft=%g ft is implausibly small for a portal frame -- "
                           "confirm FEET" % float(sp))
        e, a = cfg.get("eave_ft"), cfg.get("apex_ft")
        if _isnum(e) and float(e) > 60:
            out.append("portal eave_ft=%g looks like INCHES (cfs_frame takes FEET)" % float(e))
        if _isnum(e) and float(e) < 6:
            out.append("portal eave_ft=%g ft is implausibly low -- confirm FEET" % float(e))
        if _isnum(e) and _isnum(a) and float(a) < float(e) - 1e-6 and not cfg.get("monoslope"):
            out.append("portal apex_ft=%g < eave_ft=%g -- apex is the RIDGE height above the base "
                       "(monoslope frames set monoslope=True)" % (float(a), float(e)))
        s = cfg.get("spacing_ft")
        if _isnum(s) and (float(s) > 60 or float(s) < 3):
            out.append("portal spacing_ft=%g is implausible for CFS frames (typ. 8-30 ft) -- "
                       "confirm FEET" % float(s))
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
        # per-story wall-line presence (CFS-09): every story needs a resisting line per
        # direction; absent stories are EMPTY segment lists, never zero-length segments
        try:
            import wall_line as _WL
            _n = int(cfg.get("stories") or len(Hft))
            for _d, _key in (("X", "lines_x"), ("Y", "lines_y")):
                _ls = cfg.get(_key) or []
                if _ls and all(hasattr(_l, "segments") for _l in _ls):
                    out += _WL.presence_issues(_ls, range(1, _n + 1), _d)
        except Exception as ex:
            out.append("wall-line presence lint failed: %s" % ex)
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
    _s400_waived = False
    if R is not None and float(R) <= 3.0:
        # S400 applies to every S400 system except the A1.2.3 waiver (R = 3 in SDC B/C) --
        # gypsum/fiberboard (R = 2) IS an S400 E6 system with a capacity-design chain, so it never
        # gets the 'not detailed' note (cfs_systems.capacity_design_required, cfs-wallloads CFS-06)
        if _CS is not None and sysname in _CS.SYSTEMS:
            _sdc_r = _CS.sdc(SDS, float(s.get("SD1", 0) or 0), float(s.get("S1", 0) or 0),
                             str(cfg.get("risk_cat", "II")))
            _s400_waived = not _CS.capacity_design_required(sysname, float(R), _sdc_r)[0]
        else:
            _s400_waived = sysname not in ("sbmf", "gypsum_wall")
    if _s400_waived:
        # cleared by a governing_hazard record with COMPUTED wind and seismic values (numbers);
        # a bare 'wind governs' phrase no longer clears it (HR-33 port from steltic)
        _gh = pkg.get("governing_hazard") if isinstance(pkg, dict) else None
        if not all(_hazard_numbers(_gh)):
            out.append("R=%.2f -> S400 does not apply (not specifically detailed / S400 A1.2.3) -- no "
                       "capacity-design chain; design to S100 (+S240 framing) only, and CONFIRM whether "
                       "wind or seismic governs each direction: record calc_package['governing_hazard'] "
                       "with the computed wind AND seismic base shears per direction (numbers, e.g. "
                       "{'X': {'wind_kip': .., 'seismic_kip': ..}, 'Y': {...}}) to clear this note"
                       % float(R))
    # drift limit vs risk category (Table 12.12-1, light-frame 0.025 row where it applies)
    if _CS is not None and wall_path and sysname in _WALL_SYSTEMS:
        n_st = int(cfg.get("stories") or len(cfg.get("heights_ft") or []) or 0)
        rc = str(cfg.get("risk_cat","II"))
        want = _CS.drift_limit(sysname, n_st, rc,
                               finishes_accommodate=cfg.get("drift_tolerant_finishes"))
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
    if wall_path and _dia == "flexible":
        _db = (pkg or {}).get("diaphragm_basis") if isinstance(pkg, dict) else None
        _mat = str(cfg.get("diaphragm_material") or "").lower()
        _top = cfg.get("diaphragm_topping_in")
        _conc = "concrete" in _mat or (_isnum(_top) and float(_top) > 1.5)
        _mdd = cfg.get("diaphragm_MDD_ADVE")
        if isinstance(_db, dict) and _db.get("flexible_ok") is False:
            out.append("cfg['diaphragm']='flexible' is NOT permitted by ASCE 7-22 12.3.1.1/12.3.1.3: %s "
                       "-- model it semi-rigid (or rigid), or show MDD/ADVE > 2 (cfg['diaphragm_MDD_ADVE'])"
                       % "; ".join(str(w) for w in (_db.get("warnings") or [])[:2]))
        elif _conc and not (_isnum(_mdd) and float(_mdd) > 2.0):
            out.append("cfg['diaphragm']='flexible' with concrete / topping > 1.5 in. (%s) -- 12.3.1.1 "
                       "light-frame flexibility requires no concrete or nonstructural topping over 1.5 in.; "
                       "model it semi-rigid, or show MDD/ADVE > 2 (12.3.1.3)" % (_mat or "%s in." % _top))
    _cfs_pkg = isinstance(pkg, dict) and (pkg.get("wall_lines") is not None
                                          or pkg.get("kind") == "cfs_portal")
    # (CFS packages are distributed by the tributary solver BY CONSTRUCTION -- the keyword test
    #  below is only meaningful for hand-built frame-path packages; CFS-29b)
    if _dia in ("flexible","semi-rigid") and isinstance(pkg,dict) and not _cfs_pkg:
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
                       "calc_package -- collectors are a REQUIRED deliverable at the design level the "
                       "package seeds per level (collectors[*].by_direction: ASCE 7-22 12.10.2.1 max(a, b, c) "
                       "in SDC C-F; S400 B3.4 / 2.3.6 + Fpx in SDC A/B), not 'delegated to the drawings'")
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
    """R9: a symbolic inequality in the AGENT's capacity_design text ('An*Fu >= Ag*Fy') with no
    numbers substituted. The PIPELINE's own seed text (instruction / basis / note / seed_basis
    ...) is exempt (CFS-30: the raw seed used to fail its own check, training agents to
    rewrite framework text)."""
    out=[]; cd=pkg.get("capacity_design") if isinstance(pkg,dict) else None
    if cd is None: return out
    txt=[v for _k, v in _walk_agent(cd) if isinstance(v, str)]
    for t in txt:
        if not isinstance(t,str) or not re.search(r">=|<=|≥|≤",t): continue
        bare=set(v.lower() for v in re.findall(r"(?<![A-Za-z0-9])[A-Za-z](?![A-Za-z0-9])", t))  # single-letter symbols (t,h,L,e,...)
        if len(bare) >= 2:   # a symbolic formula with >=2 unresolved variables -> not numerically evaluated
            out.append("[capacity_design] '%s' is a symbolic REQUIREMENT, not a COMPUTED check -- substitute the section's numbers and give value vs limit + D/C"%t.strip()[:80])
    return out

def _evidence(obj, kws):
    """AGENT evidence for a required check: some dict node in obj (seed-owned keys excluded) whose
    own keys / string values mention one of kws AND whose agent-owned subtree carries a number
    (a computed value, not prose). CFS-29b: the seed text alone never satisfies it."""
    if isinstance(obj, dict):
        direct = []
        for k, v in obj.items():
            if str(k) in SEED_KEYS:
                continue
            direct.append(str(k).lower())
            if isinstance(v, str):
                direct.append(v.lower())
        txt = " ".join(direct)
        if any(kw in txt for kw in kws) and _agent_has_number(obj):
            return True
        return any(_evidence(v, kws) for k, v in obj.items()
                   if str(k) not in SEED_KEYS and isinstance(v, (dict, list)))
    if isinstance(obj, list):
        return any(_evidence(v, kws) for v in obj)
    return False


def _system_checks_issues(cfg, pkg):
    out=[]
    if not isinstance(cfg, dict): return out
    sysname=str(cfg.get("system") or "").lower()
    if not sysname or not isinstance(pkg,dict): return out
    cd = pkg.get("capacity_design") or {}
    # evidence may live in capacity_design OR in the designed slots themselves
    where = [cd] + [pkg.get(k) for k in ("holddowns", "connections", "studs", "members",
                                         "collectors", "anchorage")]
    for key,reqs in _SYS_REQUIRED.items():
        if key in sysname:
            for label,kws in reqs:
                if kws == ["fastener"]:
                    ok = any(isinstance(w, dict) and w.get("fastener_schedule")
                             for w in (pkg.get("wall_lines") or []))
                elif key == "strap_braced" and label.startswith("strap ductility") and \
                        isinstance((cd or {}).get("strap_ductility"), dict) and \
                        cd["strap_ductility"].get("ok") is not None:
                    # S400 E3.4.1(a) Method 2 is COMPUTED by the framework from the declared strap
                    # Ag/An/Fy/Fu (cfs_systems.strap_ductility_check) -- its verdict IS the evidence
                    sd = cd["strap_ductility"]
                    ok = sd.get("ok") is True
                    if not ok:
                        out.append("system '%s': STRAP DUCTILITY FAILS (S400 E3.4.1(a)): %s -- change "
                                   "the strap grade / net section (Rt*Fu*An >= Ry*Fy*Ag) and re-run"
                                   % (cfg.get("system"), str(sd.get("message") or "")[:200]))
                        continue
                else:
                    ok = any(_evidence(o, kws) for o in where if o)
                if not ok:
                    out.append("system '%s' requires check: %s -- no COMPUTED evidence in the "
                               "package (the pipeline's seed text does not count; add the check "
                               "with its numbers to capacity_design or the slot)"
                               % (cfg.get("system"), label))
    return out

_CD_GATE_SYSTEMS = ("strap_braced", "wsp_shearwall", "steelsheet_wall", "gypsum_wall")
# S400 wall systems with a capacity-design chain (gypsum E6 included -- cfs-wallloads CFS-06/07);
# whether B3.4 applies to THIS building is cfs_systems.capacity_design_required (A1.2.3 waiver)


def _capacity_design_numeric_issues(cfg, pkg):
    """NUMERIC capacity-design gate (R>3 wall systems): a hold-down or chord DESIGN demand
    that is <= 1.05x its ELF seed (T_bay_seed_kip / T_cum_kip) means the capacity-design
    amplification was never applied -- FAIL, not warn (the verified failure mode: reusing
    the raw ELF seed as the final anchorage demand while citing the capacity-design clause,
    ~2x undersized). Robust to missing keys: entries without a numeric design demand
    (an explicit demand field, or DC x capacity) fall through to the existing text-evidence
    checks unchanged."""
    out=[]
    if not isinstance(cfg, dict) and not isinstance(pkg, dict): return out
    sysname=str(((cfg or {}) if isinstance(cfg,dict) else {}).get("system")
                or (pkg or {}).get("system") or "").lower()
    if sysname not in _CD_GATE_SYSTEMS or not isinstance(pkg, dict): return out
    try:
        import cfs_systems as _CS
        _s = (cfg or {}).get("seis") or {}
        _req, _ = _CS.capacity_design_required(
            sysname, _s.get("R"), _CS.sdc(float(_s.get("SDS", 0) or 0), float(_s.get("SD1", 0) or 0),
                                          float(_s.get("S1", 0) or 0), str((cfg or {}).get("risk_cat", "II"))))
        if not _req:
            return out                              # S400 A1.2.3: no capacity-design chain
    except Exception:
        pass

    def _design_demand(e, tension_only=False):
        keys=("T_design_kip","design_T_kip","T_demand_kip","Tu_kip","T_u_kip")
        for k in keys + (() if tension_only else ("demand_kip","demand")):
            v=_num(e.get(k))
            if v is not None: return v, k
        if tension_only: return None, None
        dc=_num(e.get("DC")); cap=_num(e.get("capacity"))
        if dc is not None and cap is not None and cap > 0:
            return dc*cap, "DC x capacity"
        return None, None

    entries=[("hold-down", h) for h in (pkg.get("holddowns") or []) if isinstance(h,dict)]
    for m in (pkg.get("members") or []) + (pkg.get("studs") or []):
        if isinstance(m,dict) and "chord" in (str(m.get("id",""))
                                              +str((m.get("inputs") or {}).get("role",""))
                                              +str(m.get("role",""))).lower():
            entries.append(("chord", m))
    # anchorage slots / connections that carry the capacity-design seed (rod hardware, anchors)
    entries += [("anchorage", a) for a in (pkg.get("anchorage") or [])
                if isinstance(a, dict) and _num(a.get("T_cd_seed_kip")) is not None]
    entries += [("connection", c) for c in (pkg.get("connections") or [])
                if isinstance(c, dict) and _num(c.get("T_cd_seed_kip")) is not None]
    lines_cd=((pkg.get("capacity_design") or {}).get("lines") or {}) \
        if isinstance(pkg.get("capacity_design"), dict) else {}
    for kind, e in entries:
        if e.get("waived"): continue
        tcd=_num(e.get("T_cd_seed_kip"))
        if tcd is None and kind == "chord" and e.get("line") is not None:
            # a chord stud of a named line: the line's capacity-design tension seed
            ln=lines_cd.get("%s:%s" % (e.get("direction"), e.get("line"))) or \
                next((v for k, v in lines_cd.items() if str(k).split(":")[-1] == str(e.get("line"))
                      and isinstance(v, dict)), None)
            if isinstance(ln, dict):
                tcd=_num(ln.get("T_cd_bay_seed_kip")) or _num(ln.get("T_cd_seed_kip"))
        if tcd is not None and tcd > 0:
            # the framework's capacity-design seed: min(Omega_E*Vn stack, Omega_0 stack), / Ca for
            # Type II (cfs-wallloads CFS-06/18/20). The design tension may be lower only by the
            # 0.9D dead relief available, or where the agent computed its OWN Omega_E*Vn (numbers)
            d, src=_design_demand(e, tension_only=(kind in ("anchorage", "connection")))
            if d is None: continue
            relief=_num(e.get("dead_relief_kip_available")) or 0.0
            floor=max(tcd-relief, 0.0)
            own=any(_has_numeric_result(v, k) for k, v in e.items()
                    if re.search(r"omega_?e|(^|_)vn(_|$)|expected", str(k), re.I)
                    and str(k) not in SEED_KEYS)
            if d < 0.99*floor and not own:
                out.append("[%s %s] FAIL: design tension %.1f kip (%s) is below the capacity-design seed "
                           "T_cd = %.1f kip (min(Omega_E*Vn, Omega_0 stack), S400 B3.4)%s -- design to "
                           "T_cd, or record your own computed Omega_E*Vn stack (numbers) on the slot"
                           %(kind, e.get("id"), d, src, tcd,
                             (" less 0.9D relief %.1f kip" % relief) if relief else ""))
            continue
        seed=_num(e.get("T_bay_seed_kip"))
        if seed is None: seed=_num(e.get("T_cum_kip"))
        if seed is None or seed <= 0: continue
        d, src=_design_demand(e)
        if d is None: continue                      # missing data -> existing behavior
        Tw=_num(e.get("T_wind_kip"))
        if Tw is not None and Tw > seed and d >= 0.99*Tw:
            continue                                # wind governs and the demand reflects it
        if d <= 1.05*seed:
            out.append("[%s %s] FAIL: capacity-design amplification not applied: demand "
                       "%.1f kip (%s) equals ELF seed %.1f kip; S400 requires expected-"
                       "strength (capped at overstrength-level) design of chords/hold-downs/"
                       "anchorage -- design to min(Omega_E*Vn stack, T_cd_seed_kip)"
                       %(kind, e.get("id"), d, src, seed))
    return out


def _height_limit_issues_hr(cfg, pkg=None):
    """HOT-ROLLED route of this module (cfg without lines_x / span_ft): ASCE 7-22 Table 12.2-1 /
    12.2.5.4-7 system + height limits, the 11.6 SDC (a declared lower SDC is overridden) and the
    Table 12.2-1 / 12.8-2 factor checks -- the ERROR findings of preflight, the single source of
    the rules (HR-15/HR-20/HR-40 port from steltic). 12.2.5.4 increase evidence:
    capacity_design['height_limit'] = {'TIR_max': .., 'max_plane_share_X': .., ..} (numbers)."""
    out=[]
    try:
        import preflight as _PF
    except Exception as e:
        return ["system/height-limit checks could not run (preflight import failed: %s)"%e]
    ev=None
    cd=pkg.get("capacity_design") if isinstance(pkg,dict) else None
    hl=cd.get("height_limit") if isinstance(cd,dict) else None
    if isinstance(hl,dict):
        ev={}
        for k,v in hl.items():
            nk=re.sub(r"[^a-z0-9]","",str(k).lower())
            if isinstance(v,bool) or not isinstance(v,(int,float)): continue
            if nk in ("tirmax","tir"): ev["TIR_max"]=float(v)
            if nk in ("maxplanesharex","planesharex"): ev["max_plane_share_X"]=float(v)
            if nk in ("maxplanesharey","planesharey"): ev["max_plane_share_Y"]=float(v)
            if nk in ("maxplanesharexpct","planesharexpct"): ev["max_plane_share_X"]=float(v)/100.0
            if nk in ("maxplaneshareypct","planeshareypct"): ev["max_plane_share_Y"]=float(v)/100.0
        ev=ev or None
    for sev,msg in (_PF.sdc_findings(cfg) + _PF.system_limit_findings(cfg, ev) + _PF.factor_findings(cfg)):
        if sev=="ERROR": out.append(msg)
    return out


def _rbs_declaration_issues(cfg, pkg):
    """HOT-ROLLED route (HR-22 port): a calc package that designs RBS moment connections must have
    the AISC 358-22 5.7 Step 1 drift factor in the ENGINE drift gate (cfg['rbs'] /
    cfg['rbs_drift_factor']); otherwise gate, report and package show different drifts."""
    try:
        if not isinstance(cfg, dict) or is_cfs_cfg(cfg) or \
                not re.search(r"\bRBS\b|reduced[- ]beam[- ]section", json.dumps(pkg or {}), re.I):
            return []
        import engine3d as _E
        if any(_E.rbs_drift_factor(cfg, d)[0] > 1.0 for d in ("X", "Y")):
            return []
        return ["calc_package designs RBS (reduced beam section) connections but cfg declares no RBS -- the drift "
                "gate omits the AISC 358-22 5.7 Step 1 factor (up to 1.1 x drift) that the report applies: declare "
                "cfg['rbs'] = {'c_over_bf': c/bbf, 'dirs': 'X'|'Y'|'XY'} (or True / cfg['rbs_drift_factor']) and re-run "
                "design_and_report"]
    except Exception:
        return []


def _height_limit_issues(cfg, pkg=None):
    """Table 12.2-1 height limits via the CFS system table (65 ft walls/straps, 35 ft SBMF in
    every SDC B-F; gypsum NP in E/F). SDC from the CANONICAL cfs_systems.sdc (Tables
    11.6-1 AND 11.6-2, worse governs, incl. the S1>=0.75 E/F override) -- the old proxy
    ignored SD1 and mis-binned SD1-governed sites."""
    out=[]
    if not isinstance(cfg, dict): return out
    if not is_cfs_cfg(cfg) and cfg.get("heights"):
        return _height_limit_issues_hr(cfg, pkg)    # hot-rolled route: preflight's Table 12.2-1 rules
    s=cfg.get("seis") or {}
    Hft=[float(h) for h in (cfg.get("heights_ft") or []) if _isnum(h)]
    if not Hft:
        H=[float(h) for h in (cfg.get("heights") or []) if _isnum(h)]
        Hft=[h/12.0 for h in H]
    if not Hft: return out
    hn=sum(Hft); SDS=float(s.get("SDS",0) or 0); S1=float(s.get("S1",0) or 0)
    SD1=float(s.get("SD1",0) or 0)
    sysname=str(cfg.get("system") or "").lower()
    try:
        import cfs_systems as _CS
        sdc=_CS.sdc(SDS, SD1, S1, str(cfg.get("risk_cat","II")))
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
            out.append("footprint SETBACK detected -- design the TRANSFER/backstay diaphragm chords/collectors and supporting members for Omega_0 (12.3.3.4) and report the backstay force")
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
    # ('wing' as a WORD -- 'drawing', 'showing' no longer trigger it; HR-33 port)
    if re.search(r"\b(twin|two[- ]towers?|wings?)\b", arch) and \
            not any(k in blob for k in ("seismic joint", "seismic_joint", "pounding", "joint width",
                                        "no seismic joint")):
        out.append("multi-wing/tower configuration and NO seismic-joint decision recorded -- either "
                   "size the joint (sum of Cd-amplified drifts, ASCE 7-22 12.12.3 + pounding check) "
                   "or record why the wings are intentionally connected (with the interaction designed)")
    # B6 snow drift at steps/parapets
    stepish = any(k in arch for k in ("step", "setback", "parapet", "penthouse", "tier", "wedding"))
    if float(cfg.get("snow", 0) or 0) > 0 and stepish and "drift" not in blob.replace("drift_", ""):
        out.append("snow present with roof steps/parapets/setbacks and no DRIFT surcharge in the "
                   "package (ASCE 7-22 7.7/7.8) -- add the drift check to the step-adjacent members")
    # B8 delegated-design register. CFS-30: CFS joists/framing are DESIGNED by the agent (S240),
    # and seed text ('joist span', cfg joist_span_ft) mentions joists everywhere -- on a CFS
    # package only proprietary/manufacturer-designed items count, in AGENT text, not negated.
    _cfs = pkg.get("wall_lines") is not None or pkg.get("kind") == "cfs_portal"
    if _cfs:
        _ab = _agent_text(pkg)
        _deleg_kw = ("sji", "open-web", "open web", "proprietary", "pre-engineered truss",
                     "truss manufacturer", "brb", "stair", "curtain wall")
        _b8 = any(_mentions(_ab, k) for k in _deleg_kw)
    else:
        _b8 = any(k in blob for k in ("joist", "sji", " deck", "brb", "stair", "curtain wall"))
    if _b8 and "delegat" not in blob:
        out.append("delegated-design components referenced (joists/deck/BRBs/stairs/cladding) but no "
                   "'delegated_design' register in capacity_design -- list each delegated item, the "
                   "design criteria handed off, and the interface forces")
    return out


_HD_BANDS = {"strap": 5.0, "bolted": 20.0}      # kip; mirror wall_line.pick_holddown envelopes


def hd_design_demand(h):
    """(T_kip, source) -- the hold-down/chord DESIGN tension the agent filled: an explicit design
    field, else DC x capacity; (None, None) when the slot is not designed yet."""
    for k in ("T_design_kip", "design_T_kip", "T_demand_kip", "demand_kip", "Tu_kip", "T_u_kip",
              "demand"):
        v = _num(h.get(k))
        if v is not None:
            return v, k
    dc = _num(h.get("DC")); cap = _num(h.get("capacity"))
    if dc is not None and cap is not None and cap > 0:
        return dc * cap, "DC x capacity"
    return None, None


def hd_band_tension(h):
    """Tension a hold-down DEVICE must resist for the class-band screen (CFS-29e): the agent's
    design tension when filled, else the largest seed -- max(T_cum (rho-ELF), T_bay, T_cd_seed
    (Omega0-level capacity design), T_wind). Never the bare ELF T_cum when a larger seed exists."""
    d, _src = hd_design_demand(h)
    if d is not None:
        return d, "design"
    seeds = [_num(h.get(k)) for k in ("T_cum_kip", "T_bay_seed_kip", "T_cd_seed_kip",
                                      "T_wind_kip")]
    seeds = [x for x in seeds if x is not None]
    return (max(seeds), "max seed") if seeds else (None, None)


def _cfs_slot_issues(cfg, pkg):
    """CFS-specific slot screens: sheathing+fastener completeness, hold-down band vs the DESIGN
    tension (and the sized-for-shear red flag), Type II mechanics, the net-uplift path, and
    unresolved pipeline gate flags. Keyword screens read AGENT text only and ignore negated
    mentions (CFS-29b/CFS-30)."""
    out = []
    if not isinstance(pkg, dict):
        return out
    blob = json.dumps(pkg).lower()
    ablob = _agent_text(pkg)
    for w in pkg.get("wall_lines") or []:
        if not isinstance(w, dict) or w.get("waived"):
            continue
        if wall_slot_system(pkg, w, cfg) == "strap_braced":
            # strap bays: the design is the strap selection + its end connection (no sheathing)
            if not strap_slot_complete(w):
                out.append("[wall %s] strap bay without its strap selection (selection) and end "
                           "connection (connection) -- a strap-bay capacity without them is "
                           "unverifiable (S400 E3)" % w.get("id"))
        elif not (w.get("sheathing") and w.get("fastener_schedule")):
            out.append("[wall %s] no sheathing + fastener_schedule -- a wall capacity without them "
                       "is unverifiable (S400 tables are sheathing x fastener x sides)" % w.get("id"))
    for h in pkg.get("holddowns") or []:
        if not isinstance(h, dict) or h.get("waived"):
            continue
        T, src = hd_band_tension(h)
        dev = str(h.get("device_class") or "").strip().lower()
        cap = _HD_BANDS.get(dev)
        if T is not None and cap is not None and T > cap * 1.02:
            out.append("[hold-down %s] %s tension %.1f kip exceeds the %s-class envelope "
                       "(~%.0f kip) -- switch to a COMPUTED continuous rod (tension + PL/AE "
                       "elongation + take-up; elongation feeds drift)"
                       % (h.get("id"), src, T, dev, cap))
        if any("shear" in f and "tension" not in f
               for f in (str(h.get("basis", "")).lower(), str(h.get("limit_state", "")).lower())):
            out.append("[hold-down %s] appears sized for SHEAR -- hold-downs resist the cumulative "
                       "OVERTURNING TENSION, never the shear (instant red flag)" % h.get("id"))
    # Type II (perforated) mechanics -- only where Type II is actually DECLARED (not negated)
    # (AGENT text + the package's top-level notes, not the seeds: the wallloads capacity-design
    #  instruction itself mentions Type II; a cfg['type_ii'] declaration seeds holddowns.type_ii)
    tblob = (ablob + " " + str(pkg.get("notes") or "") + " " + str(pkg.get("note") or "")).lower()
    if _mentions(tblob, "type ii") or _mentions(tblob, "perforated") or \
            any(isinstance(h, dict) and h.get("type_ii") for h in pkg.get("holddowns") or []):
        if "adjustment" not in blob and "ca" not in [_norm_key(k) for k, _v in _walk_agent(pkg)]:
            out.append("Type II (perforated) walls referenced but NO adjustment-factor calculation "
                       "in the package -- show the S400 Type II factor on the full-length capacity")
        if not ("distributed" in ablob and ("track" in ablob or "anchorage" in ablob)):
            out.append("Type II walls need END hold-downs PLUS distributed track anchorage between "
                       "-- not evident in the package")
        if any(_mentions(tblob, k) for k in ("every pier", "each pier", "per pier")):
            out.append("Type II wall with hold-downs at EVERY PIER -- that silently reverts the "
                       "wall to Type I; anchor the wall ENDS only, distributed track anchorage between")
    # net-uplift path on wind-relevant briefs -- the seed's COMBOS_NOTE / combo labels / slot
    # notes do not count; the AGENT's design must carry it
    _w = (cfg or {}).get("wind") if isinstance(cfg, dict) else None
    wind_v = _num(_w.get("V")) if isinstance(_w, dict) else None
    windy = (isinstance(cfg, dict) and str(cfg.get("governing", "")).lower() == "wind") or \
            (wind_v is not None and wind_v >= 115)
    if windy and not ("0.9" in ablob and "uplift" in ablob):
        out.append("wind-governed/high-wind brief and no 0.9D+1.0W NET-UPLIFT design in the package "
                   "-- the uplift path (roof-to-wall, wall-to-floor, floor-to-foundation) is a "
                   "REQUIRED anchorage design chain (the seeded combo list alone does not count)")
    # unresolved pipeline gates
    for key, what in (("model_vs_tributary_flags", "model-vs-tributary divergence"),
                      ("drift_flags", "drift limit exceedance"),
                      ("stability_flags", "P-delta stability (ASCE 7-22 12.8.7 theta > theta_max)")):
        flags = pkg.get(key) or []
        if flags and not pkg.get(key + "_resolution"):
            out.append("%d unresolved %s flag(s) -- fix the design and re-run, or record the "
                       "engineering justification in pkg['%s_resolution']" % (len(flags), what, key))
    return out


_DRIFT_VALUE_KEYS = ("drift_design", "drift_amplified_design", "drift_amplified", "ratio",
                     "drift_ratio", "value_ratio")
_DRIFT_LIMIT_KEYS = ("limit", "limit_ratio", "drift_limit")


def _drift_table_issues(pkg):
    """CFS-29(c): every drift_table row is a CHECK. A failing row (ok False, D/C > 1, or value >
    limit) FAILS the gate -- a blanket *_resolution string never clears it; fix the design, or
    put the designed-schedule drift in the row (drift_design / drift_amplified + ok), or waive
    the ROW with a justification (e.g. a declared split-level offset). Rows the pipeline seeds
    with ok=None (portal eave sway / apex) need the agent's criterion + verdict."""
    out = []
    for r in (pkg.get("drift_table") or []) if isinstance(pkg, dict) else []:
        if not isinstance(r, dict):
            continue
        rid = r.get("check") or "%s line %s story %s" % (r.get("direction"), r.get("line"),
                                                          r.get("story"))
        if r.get("recheck_after_rerun"):
            out.append("[drift %s] %s" % (rid, str(r["recheck_after_rerun"])[:220]))
        if r.get("waived"):
            out += _waiver_issues("drift", dict(r, id=rid))
            continue
        val = next((_num(r.get(k)) for k in _DRIFT_VALUE_KEYS if _num(r.get(k)) is not None), None)
        lim = next((_num(r.get(k)) for k in _DRIFT_LIMIT_KEYS if _num(r.get(k)) is not None), None)
        dc = _dc_num(r.get("DC"))
        ok = r.get("ok")
        fail = (ok is False) or (dc is not None and dc > 1.0 + 1e-9) or \
               (val is not None and lim is not None and lim > 0 and val > lim * (1 + 1e-6))
        # an explicitly designed value that passes overrides a stale engine screen value
        dd = _num(r.get("drift_design")) if r.get("drift_design") is not None else \
            _num(r.get("drift_amplified_design"))
        if fail and dd is not None and lim and dd <= lim and ok is not False:
            fail = False
        if fail:
            shown = ("%.4g" % val) if val is not None else ("D/C %.3f" % dc if dc is not None else "?")
            out.append("[drift %s] FAILS: %s vs limit %s -- stiffen/redesign and re-run (a "
                       "drift_flags_resolution note does NOT clear a failing drift row; waive the "
                       "row only with a justification such as a declared split-level offset)"
                       % (rid, shown, ("%.4g" % lim) if lim is not None else "?"))
        elif ok is None and dc is None:
            out.append("[drift %s] has no verdict -- state the criterion (e.g. H/60, H/240 with "
                       "brittle finishes, L/240 apex) and set ok / DC" % rid)
    return out


def _theta_issues(pkg):
    """P-Delta stability coefficient (ASCE 7-22 12.8.7): any agent/framework block that reports a
    numeric theta FAILS when theta > its stated theta_max (or > 0.25, the absolute ceiling of
    Eq. 12.8-19) -- HR-14/CFS-29 port: a theta failure can never ship green."""
    out = []
    if not isinstance(pkg, dict):
        return out

    def walk(o, path):
        if isinstance(o, dict):
            th = None
            for k in ("theta", "theta_max_story", "theta_story_max"):
                if _num(o.get(k)) is not None and not isinstance(o.get(k), str):
                    th = float(o[k]); break
            if th is not None and not o.get("waived"):
                lim = None
                for k in ("theta_max", "theta_limit", "limit"):
                    v = o.get(k)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        lim = float(v); break
                lim = min(lim, 0.25) if lim else 0.25
                # a wall drift row's ok/False is the DRIFT verdict, not theta's (merge integration)
                _drift_row = "drift_amplified" in o or "drift_design" in o
                if th > lim + 1e-9 or (o.get("ok") is False and not _drift_row):
                    out.append("[stability %s] theta = %.3f > theta_max = %.3f (ASCE 7-22 12.8.7) "
                               "-- the structure must be stiffened/redesigned" % (path, th, lim))
            for k, v in o.items():
                if str(k) not in ("combos",):
                    walk(v, path + "." + str(k) if path else str(k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, "%s[%d]" % (path, i))
    walk(pkg, "")
    return out


def _two_stage_issues(cfg, pkg):
    """CFS-32: a podium / two-stage claim must be backed by the framework's 12.2.3.2 block
    (pkg['two_stage_framework'], written by pipeline.design_and_report) with every eligibility
    item EVALUATED and PASSING; the reported reaction amplification must be >= 1.0."""
    out = []
    if not isinstance(cfg, dict) or not isinstance(pkg, dict):
        return out
    claimed = str(cfg.get("structure_kind", "")).lower() == "podium" or bool(cfg.get("two_stage"))
    if not claimed:
        return out
    ts = pkg.get("two_stage_framework")
    if not isinstance(ts, dict):
        out.append("two-stage podium (ASCE 7-22 12.2.3.2) declared but the package has no "
                   "framework two_stage_framework block -- re-run pipeline.design_and_report "
                   "with cfg['two_stage'] (K_lower_kip_in, T_combined_s or W_lower_kip, R_lower, "
                   "rho_lower)")
        return out
    for dirn, d in (ts.get("by_direction") or {}).items():
        st = str(d.get("status", "")).upper()
        if st != "ELIGIBLE":
            out.append("two-stage %s: %s -- %s" % (dirn, st or "NOT EVALUATED",
                                                   "; ".join(d.get("messages") or [])
                                                   or "supply the missing podium data"))
    amp = _num(ts.get("amplification"))
    if amp is not None and amp < 1.0 - 1e-9:
        out.append("two-stage reaction amplification %.3f < 1.0 -- 12.2.3.2(d) floor is 1.0" % amp)
    return out


# ---- code-basis screens (US-practice review of the published CFS reports, 2026-10) -------------
# The design basis is AISI S100-16 (2020) w/S2-20 as referenced by IBC 2024, ASCE 7-22 LRFD.

# ASTM A449 (Table 3, mechanical properties by nominal diameter): (max dia in., Fy ksi, Fu ksi)
A449_GRADES = ((1.0, 92.0, 120.0), (1.5, 81.0, 105.0), (3.0, 58.0, 90.0))
# UNC threads per inch (ASME B1.1) for the tensile-stress area At = 0.7854 (d - 0.9743/n)^2
_UNC_TPI = {0.5: 13, 0.625: 11, 0.75: 10, 0.875: 9, 1.0: 8, 1.125: 7, 1.25: 7, 1.375: 6,
            1.5: 6, 1.75: 5, 2.0: 4.5, 2.25: 4.5, 2.5: 4.0, 2.75: 4.0, 3.0: 4.0}


def a449_strength(d_in):
    """(Fy, Fu) in ksi of an ASTM A449 rod of nominal diameter d_in (None above 3 in.)."""
    for dmax, fy, fu in A449_GRADES:
        if d_in <= dmax + 1e-6:
            return fy, fu
    return None


def rod_areas(d_in):
    """(Ag, At) in in.^2: gross area and UNC tensile-stress area of a threaded rod."""
    import math
    n = _UNC_TPI.get(round(d_in, 3)) or min(_UNC_TPI.items(), key=lambda kv: abs(kv[0] - d_in))[1]
    return math.pi * d_in ** 2 / 4.0, 0.7854 * (d_in - 0.9743 / n) ** 2


def a449_phiTn_max(d_in):
    """Upper bound of the LRFD tension strength of an A449 rod with the CORRECT grade for its
    diameter: min(0.90 Fy Ag yield, 0.75 Fu At rupture) (S100 D2/D3 form; AISC 360 J3.6
    0.75 x 0.75 Fu Ab is lower still)."""
    g = a449_strength(d_in)
    if not g:
        return None
    Ag, At = rod_areas(d_in)
    return min(0.90 * g[0] * Ag, 0.75 * g[1] * At)


_DIA_UNIT = r"\s*(?:in\.?|inch(?:es)?|\"|”|''|-in\b|-inch\b)"
_DIA_PATTERNS = (
    re.compile(r"(?<![\d/.])(\d)\s*-\s*(\d{1,2})/(\d{1,2})" + _DIA_UNIT, re.I),     # 1-1/2 in.
    re.compile(r"(?<![\d/.\-])(\d{1,2})/(\d{1,2})" + _DIA_UNIT, re.I),               # 7/8 in.
    re.compile(r"(?<![\d/.\-])(\d(?:\.\d{1,3})?)" + _DIA_UNIT, re.I),                # 1.25 in.
)


def parse_rod_diameter(text):
    """Largest nominal diameter (in.) quoted next to a rod / A449 / threaded-rod mention in the
    text, or None. Plate thicknesses etc. are ignored unless the word 'rod' (or A449, threaded,
    dia) sits within ~30 characters of the dimension."""
    if not isinstance(text, str) or not text:
        return None
    best = None
    taken = []
    for i, rx in enumerate(_DIA_PATTERNS):
        for m in rx.finditer(text):
            if any(a <= m.start() < b for a, b in taken):
                continue
            g = m.groups()
            try:
                if i == 0:
                    d = int(g[0]) + int(g[1]) / float(g[2])
                elif i == 1:
                    d = int(g[0]) / float(g[1])
                else:
                    d = float(g[0])
            except (ValueError, ZeroDivisionError):
                continue
            taken.append((m.start(), m.end()))
            if not (0.375 <= d <= 4.0):
                continue
            win = text[max(0, m.start() - 30): m.end() + 30].lower()
            if re.search(r"\brods?\b|a449|threaded|\bdia\b|diameter|ø", win):
                best = d if best is None else max(best, d)
    return best


def _entry_text(e):
    """All string leaves of an entry (its own fields + checks + rows) as one blob."""
    acc = []
    _gather_text(e, acc)
    return " | ".join(t for t in acc if isinstance(t, str))


_PHITN_RX = re.compile(r"(?:phi|φ|ϕ)\s*[_]?\s*t?\s*\*?\s*T\s*_?n\s*(?:\([^)]*\))?\s*[=:≈~]\s*"
                       r"(\d+(?:\.\d+)?)", re.I)


def _claimed_tension_capacity(e):
    """The LRFD tension strength an entry claims (kip): numeric capacity, a phiTn key in its
    checks, or 'phi*Tn = N' in its capacity / limit-state text."""
    cap = e.get("capacity")
    if isinstance(cap, (int, float)) and not isinstance(cap, bool):
        return float(cap)
    for c in [e] + [c for c in (e.get("checks") or []) if isinstance(c, dict)]:
        v = _find(c, frozenset(("phitn", "phitnrod", "phitny", "phitnr")))
        if v is not None:
            return v
    for k in ("capacity", "limit_state", "note"):
        m = _PHITN_RX.search(str(e.get(k) or ""))
        if m:
            return float(m.group(1))
    return None


_FU120_RX = re.compile(r"\bF_?u\s*[=:]?\s*120(?:\.0+)?(?![\d.])|(?<![\d.])120(?:\.0+)?\s*ksi\b|"
                       r"[*×x]\s*120(?:\.0+)?\s*[*×x]|\b0\.75\s*[*×x]\s*120(?![\d.])", re.I)
_FY81_RX = re.compile(r"\bF_?y\s*[=:]?\s*81(?:\.0+)?(?![\d.])|(?<![\d.])81(?:\.0+)?\s*ksi\b|"
                      r"\b0\.9\s*[*×x]\s*81(?![\d.])|\bF_?u\s*[=:]?\s*105(?![\d.])|"
                      r"(?<![\d.])105\s*ksi\b|\b0\.75\s*[*×x]\s*105(?![\d.])", re.I)


def _rod_grade_issues(pkg):
    """ASTM A449 threaded rods: strength steps down with diameter (Fy/Fu = 92/120 ksi to 1 in.,
    81/105 ksi over 1 to 1-1/2 in., 58/90 ksi over 1-1/2 to 3 in.). Flags a rod over 1 in.
    computed with Fu = 120 ksi, a rod over 1-1/2 in. computed with 81/105 ksi, and any claimed
    phiTn above min(0.9FyAg, 0.75FuAt) at the correct grade."""
    out = []
    if not isinstance(pkg, dict):
        return out
    pool = [("hold-down", h) for h in pkg.get("holddowns") or []] + \
           [("connection", c) for c in pkg.get("connections") or []] + \
           [("anchorage", a) for a in pkg.get("anchorage") or []]
    for kind, e in pool:
        if not isinstance(e, dict) or e.get("waived"):
            continue
        txt = _entry_text({k: v for k, v in e.items() if k not in SEED_KEYS})
        if not re.search(r"a\s*449", txt, re.I):
            continue
        d = parse_rod_diameter(txt)
        if d is None or d <= 1.0 + 1e-6:
            continue
        g = a449_strength(d)
        if not g:
            continue
        cid = e.get("id")
        if _FU120_RX.search(txt):
            out.append("[%s %s] ASTM A449 rod %.3g in. computed with Fu = 120 ksi -- A449 over 1 in. "
                       "is Fy/Fu = 81/105 ksi (to 1-1/2 in.) and 58/90 ksi (1-5/8 to 3 in.); this "
                       "rod is %g/%g ksi -- recompute the rod strength" % (kind, cid, d, g[0], g[1]))
        elif d > 1.5 + 1e-6 and _FY81_RX.search(txt):
            out.append("[%s %s] ASTM A449 rod %.3g in. computed with the 81/105-ksi grade -- over "
                       "1-1/2 in. A449 is Fy/Fu = 58/90 ksi; recompute the rod strength"
                       % (kind, cid, d))
        cap = _claimed_tension_capacity(e)
        ub = a449_phiTn_max(d)
        if cap is not None and ub and cap > 1.02 * ub:
            out.append("[%s %s] claimed rod strength %.1f kip exceeds the ASTM A449 strength of a "
                       "%.3g-in. rod at Fy/Fu = %g/%g ksi: min(0.9FyAg, 0.75FuAt) = %.1f kip -- "
                       "re-derive the rod (and its D/C)" % (kind, cid, cap, d, g[0], g[1], ub))
    return out


_SCREW_CTX = re.compile(r"\bscrews?\b|\bJ4(?:\.\d+)*\b|self[- ]drill|\bno\.\s?(?:6|8|10|12|14)\b|"
                        r"#\s?(?:8|10|12|14)\b", re.I)
_PHI055_RX = re.compile(r"(?:phi|φ|ϕ)\s*_?\s*[a-z]{0,2}\s*(?:=|:|of)?\s*0?\.55(?![\d])", re.I)
_OM280_RX = re.compile(r"(?:omega|Ω)\s*_?\s*[a-z]{0,2}\s*(?:=|:|of)?\s*2\.80?(?![\d])", re.I)


def _screw_phi_issues(pkg):
    """Screw connections (AISI S100 J4) checked with phi = 0.55 / Omega = 2.80. The design basis
    (S100-16 (2020) w/S2-20, as referenced by IBC 2024) uses phi = 0.50 / Omega = 3.00 for screw
    shear (tilting/bearing), pull-out and pull-over -- the higher factor is unconservative."""
    out = []
    if not isinstance(pkg, dict):
        return out
    for kind, key in (("connection", "connections"), ("collector", "collectors"),
                      ("hold-down", "holddowns"), ("wall", "wall_lines"), ("stud", "studs"),
                      ("member", "members"), ("anchorage", "anchorage"),
                      ("schedule", "schedules")):
        for e in pkg.get(key) or []:
            if not isinstance(e, dict) or e.get("waived"):
                continue
            texts = []
            _gather_text({k: v for k, v in e.items() if k not in SEED_KEYS}, texts)
            hit = None
            for t in texts:
                if not isinstance(t, str):
                    continue
                for rx in (_PHI055_RX, _OM280_RX):
                    for m in rx.finditer(t):
                        win = t[max(0, m.start() - 90): m.end() + 30]
                        if _SCREW_CTX.search(win) and not re.search(r"\bweld|\bbolt", win, re.I):
                            hit = m.group(0)
                            break
                    if hit:
                        break
                if hit:
                    break
            if hit:
                out.append("[%s %s] screw connection checked with '%s' -- AISI S100-16 (2020) w/S2-20 "
                           "(IBC 2024) J4 uses phi = 0.50 / Omega = 3.00 for screw connections; "
                           "re-derive the screw strengths and D/C" % (kind, e.get("id"), hit.strip()))
    return out


def _screw_shear_strength_issues(pkg):
    """A screw connection designed for tilting/bearing (S100 J4.3.1) must also check the shear
    strength of the screw itself (J4.3.2: the manufacturer's tested Pss) -- the published
    reports quoted per-screw strengths above typical No. 12 Pss values."""
    out = []
    for c in (pkg.get("connections") or []) if isinstance(pkg, dict) else []:
        if not isinstance(c, dict) or c.get("waived"):
            continue
        txt = _entry_text({k: v for k, v in c.items() if k not in SEED_KEYS})
        if not re.search(r"J4\.3\.1|tilting", txt, re.I) or not _SCREW_CTX.search(txt):
            continue
        if re.search(r"J4\.3\.2|\bP_?ss\b|screw shear strength|shear strength of the screw|"
                     r"fastener shear|screw (?:itself|body)|\bESR\b|manufacturer", txt, re.I):
            continue
        out.append("[connection %s] screw connection checked for tilting/bearing (S100 J4.3.1) "
                   "only -- add the shear strength of the screw itself (J4.3.2, from the "
                   "manufacturer's tested Pss) as a limit state" % c.get("id"))
    return out


_P_DEM_KEYS = frozenset(("pu", "pr", "pdesign", "pdemand", "pucomp", "prcomp", "nu"))
_P_CAP_KEYS = frozenset(("phipn", "pc", "phipne", "phipnl", "phipnd", "pa", "phicpn",
                         "phipnmin", "pnphi"))
_PHIPN_RX = re.compile(r"(?:phi|φ|ϕ)\s*_?c?\s*\*?\s*P\s*_?n[a-z]?\s*[=:≈~]\s*(\d+(?:\.\d+)?)", re.I)


def _interaction_issues(pkg):
    """Combined axial + bending checks: the interaction ratio can never be below its own axial
    term -- Pr/Pc for AISI S100 H1.2 (and AISC H1-1a); Pr/(2Pc) for AISC H1-1b, which applies
    only when Pr/Pc < 0.2. A correct H1-1b check whose D/C is below Pr/Pc is NOT flagged."""
    out = []
    if not isinstance(pkg, dict):
        return out
    for kind, key in (("member", "members"), ("stud", "studs")):
        for e in pkg.get(key) or []:
            if not isinstance(e, dict) or e.get("waived"):
                continue
            cid = e.get("id")
            for c in [c for c in (e.get("checks") or []) if isinstance(c, dict)]:
                name = "%s %s" % (c.get("limit_state") or "", c.get("name") or "")
                if not _is_interaction(name):
                    continue
                dc = _dc_num(c.get("DC"))
                P, Pc = _find(c, _P_DEM_KEYS), _find(c, _P_CAP_KEYS)
                if dc is None or P is None or not Pc:
                    continue
                txt = " ".join(str(v) for v in c.values() if isinstance(v, str))
                out += _interaction_floor(kind, cid, name.strip(), dc, P, Pc, txt)
            # entry level: the headline D/C (the worst pair) can never be below P/Pc of the
            # max-moment pair the slot carries
            if any(_is_interaction(str(c.get("limit_state") or c.get("name") or ""))
                   for c in (e.get("checks") or []) if isinstance(c, dict)) or \
                    _is_interaction(str(e.get("limit_state") or "")):
                dc = worst_dc(e)
                P = _num(e.get("P_design_kip")) if e.get("P_design_kip") is not None else \
                    _num(e.get("P_kip"))
                m = _PHIPN_RX.search(str(e.get("capacity") or ""))
                Pc = float(m.group(1)) if m else None
                if dc is not None and P and Pc:
                    txt = " ".join(str(e.get(k) or "") for k in ("limit_state", "capacity"))
                    out += _interaction_floor(kind, cid, "headline", dc, P, Pc, txt)
    return out


def _interaction_floor(kind, cid, name, dc, P, Pc, txt):
    ratio = abs(P) / Pc
    h1b = bool(re.search(r"H1-1b", txt + " " + name, re.I))
    out = []
    if h1b and ratio >= 0.2 + 0.005:
        out.append("[%s %s] check '%s' uses AISC H1-1b but Pr/Pc = %.3f >= 0.2 -- H1-1a governs "
                   "(H1-1b applies only when Pr/Pc < 0.2)" % (kind, cid, name, ratio))
    floor = ratio / 2.0 if h1b else ratio
    if dc + max(ABS_TOL, 0.02 * floor) < floor:
        out.append("[%s %s] interaction '%s' D/C %.3f is below its own axial term %s = %.3f "
                   "(Pr = %.3g, Pc = %.3g) -- the interaction sum cannot be smaller; reconcile"
                   % (kind, cid, name, dc, "Pr/(2Pc)" if h1b else "Pr/Pc", floor, P, Pc))
    return out


_SNOW16_RX = re.compile(r"(?<![\d.])(1\.6|0\.5|0\.2)\s*\*?\s*S(?![a-zA-Z])")


def _snow_factor_issues(pkg):
    """Governing combinations written with the ASCE 7-16 snow factors (1.6S primary, 0.5S
    companion, 0.2S seismic). ASCE 7-22 2.3.1 maps a strength-level snow load: 1.0S primary,
    0.3S companion, 0.15S in the seismic combination."""
    out = []
    if not isinstance(pkg, dict):
        return out
    for kind, key in (("member", "members"), ("connection", "connections"),
                      ("anchorage", "anchorage"), ("schedule", "schedules"), ("stud", "studs")):
        for e in pkg.get(key) or []:
            if not isinstance(e, dict) or e.get("waived"):
                continue
            labs = [e.get(k) for k in ("governing_combo", "combo", "uplift_combo")]
            for c in (e.get("checks") or []) + (e.get("rows") or []):
                if isinstance(c, dict):
                    labs += [c.get(k) for k in ("combo", "governing_combo")]
            hit = next((str(x) for x in labs if isinstance(x, str) and _SNOW16_RX.search(x)), None)
            if hit:
                out.append("[%s %s] combination '%s' uses an ASCE 7-16 snow factor -- ASCE 7-22 "
                           "2.3.1 uses 1.0S (0.3S as a companion, 0.15S with E); re-run with the "
                           "7-22 combinations" % (kind, e.get("id"), hit[:80]))
    return out


_DRIFT716_RX = re.compile(r"0\.43\s*[*x×]?\s*\(?\s*[\w.]+\s*\)?\s*\^\s*\(?\s*1\s*/\s*3", re.I)
_ROS5_RX = re.compile(r"\b5(?:\.0)?\s*-?\s*psf\s+rain[- ]on[- ]snow|rain[- ]on[- ]snow[^.;]{0,30}\b5(?:\.0)?\s*psf",
                      re.I)


def _snow_basis_issues(pkg):
    """ASCE 7-16 snow provisions in the designer's records: the drift height
    hd = 0.43 lu^(1/3) (pg + 10)^(1/4) - 1.5 (ASCE 7-22 Eq. 7.6-1 uses the winter wind parameter
    W2) and the 5-psf rain-on-snow surcharge (ASCE 7-22 7.10: 8 psf)."""
    txt = _agent_text(pkg) if isinstance(pkg, dict) else ""
    out = []
    if _DRIFT716_RX.search(txt):
        out.append("snow drift height computed with the ASCE 7-16 equation hd = 0.43 lu^(1/3)(pg+10)^(1/4) "
                   "- 1.5 -- ASCE 7-22 Eq. 7.6-1 uses the winter wind parameter W2; recompute the drifts")
    if _ROS5_RX.search(txt):
        out.append("rain-on-snow surcharge taken as 5 psf (ASCE 7-16) -- ASCE 7-22 7.10 uses 8 psf where it "
                   "applies; recheck")
    return out


def _h11_compression_issues(pkg):
    """AISI S100 H1.1 is combined TENSION and bending; a member whose governing axial force is
    compression is checked with H1.2 (combined compressive axial load and bending)."""
    out = []
    for kind, key in (("member", "members"), ("stud", "studs")):
        for e in (pkg.get(key) or []) if isinstance(pkg, dict) else []:
            if not isinstance(e, dict) or e.get("waived"):
                continue
            comp = (_num(e.get("Pc_paired_kip")) or 0.0) > 0.5 or \
                (_num(e.get("P_design_kip")) or 0.0) > 0.5 or \
                any((_find(c, frozenset(("pu", "pr", "pucomp"))) or 0.0) > 0.5 and
                    re.search(r"compress|\bH1\b|interaction", str(c.get("limit_state") or ""), re.I)
                    for c in (e.get("checks") or []) if isinstance(c, dict))
            if not comp:
                continue
            txt = _entry_text({k: v for k, v in e.items() if k not in SEED_KEYS})
            if re.search(r"\bH1\.1\b", txt) and not re.search(r"\bH1\.2\b", txt):
                out.append("[%s %s] combined check cites S100 H1.1 (tension + bending) for a member in "
                           "compression -- use H1.2 (compression + bending)" % (kind, e.get("id")))
    return out


def _rho_basis_issues(cfg, pkg):
    """rho = 1.0 in SDC D-F needs the ASCE 7-22 12.3.4.2 showing (condition (a): removal of one
    element does not reduce story strength by more than 33 % nor create an extreme torsional
    irregularity; condition (b): regular plan with at least two bays of perimeter SFRS per side,
    light-frame bays = 2L/h_sx), with numbers -- asserting the clause is not a check."""
    if not isinstance(cfg, dict) or not is_cfs_cfg(cfg) or not isinstance(pkg, dict):
        return []
    s = cfg.get("seis") or {}
    try:
        import cfs_systems as _CS
        cat = _CS.sdc(float(s.get("SDS", 0) or 0), float(s.get("SD1", 0) or 0),
                      float(s.get("S1", 0) or 0), str(cfg.get("risk_cat", "II")))
    except Exception:
        return []
    if cat not in ("D", "E", "F"):
        return []
    rb = cfg.get("rho_by_dir") or {}
    rhos = [_num(rb.get(d, pkg.get("rho", cfg.get("rho")))) for d in ("X", "Y")]
    if not any(r is not None and abs(r - 1.0) < 1e-9 for r in rhos):
        return []
    txt = " ".join([_agent_text(pkg), str(pkg.get("rho_basis") or ""),
                    json.dumps(pkg.get("rho_justification") or "")])
    if re.search(r"12\.3\.4\.2.{0,300}?\d", txt, re.S) and \
            re.search(r"\d+\s*(?:bays?|%|percent)|2l/h|33\s*%|torsion", txt, re.I):
        return []
    return ["rho = 1.0 in SDC %s without the ASCE 7-22 12.3.4.2 showing -- record condition (a) (strength "
            "loss <= 33 %% on removing a wall line, no extreme torsional irregularity) or (b) (regular plan, "
            ">= 2 bays of perimeter SFRS per side per story; light-frame bays = 2L/h_sx) with numbers in "
            "rho_justification, or use rho = 1.3" % cat]


def _seed_label_issues(pkg):
    """A designed collector whose printed basis is still the framework's 'SEEDED' label."""
    out = []
    for c in (pkg.get("collectors") or []) if isinstance(pkg, dict) else []:
        if not isinstance(c, dict) or c.get("waived"):
            continue
        designed = c.get("DC") is not None or bool(c.get("checks"))
        if not designed:
            continue

        def _seedy(k):
            return isinstance(c.get(k), str) and c[k].strip().upper().startswith("SEEDED")
        own = any(isinstance(c.get(k), str) and c[k].strip() and not _seedy(k)
                  for k in ("design_basis", "demand_basis_used", "capacity_design_basis"))
        printed = [k for k in ("selection", "description", "label", "design_basis") if _seedy(k)]
        if not printed and _seedy("basis") and not own:
            printed = ["basis"]
        if printed:
            lab = str(c.get(printed[0]))
            out.append("[collector %s] the framework seed label '%s' is left in place as the design "
                       "basis -- state the actual basis in design_basis (e.g. S400 expected strength "
                       "capped at the Omega0 level, or ASCE 7-22 12.10.2.1 Omega0)"
                       % (c.get("id"), lab[:70]))
    return out


_ANCH_RX = re.compile(r"anchor|sill|track[- ]to|wall[- ]to[- ](?:floor|podium|foundation|slab)|"
                      r"shear transfer|rim", re.I)
_UNIT_SHEAR_RX = re.compile(r"(\d+(?:\.\d+)?)\s*(klf|plf|kip/ft|lb/ft)", re.I)


def wall_slot_line(w):
    """Line name of a wall slot: its 'line' key, else parsed from 'wall-<dir>-<line>-s<story>'."""
    if w.get("line") is not None:
        return str(w["line"])
    m = re.match(r"^wall-([XY])-(.+)-s(\d+)$", str(w.get("id", "")))
    return m.group(2) if m else ""


def wall_slot_system(pkg, w, cfg=None):
    """System key of one wall slot: the slot's own 'system', its line's system
    (elf_by_direction[d].line_systems), the direction's system (system_by_direction /
    capacity_design_applicability / cfg system_by_dir), else the building system."""
    if w.get("system"):
        return str(w["system"]).lower()
    d = str(w.get("direction") or "")
    pkg = pkg if isinstance(pkg, dict) else {}
    ls = ((pkg.get("elf_by_direction") or {}).get(d) or {}).get("line_systems") or {}
    if isinstance(ls, dict) and ls.get(wall_slot_line(w)):
        return str(ls[wall_slot_line(w)]).lower()
    for src in (pkg.get("system_by_direction") or {},
                {k: (v or {}).get("system") for k, v in (pkg.get("capacity_design_applicability") or {}).items()
                 if isinstance(v, dict)},
                ((cfg or {}).get("system_by_dir") or {}) if isinstance(cfg, dict) else {}):
        if isinstance(src, dict) and src.get(d):
            return str(src[d]).lower()
    return str(((cfg or {}).get("system") if isinstance(cfg, dict) else None) or pkg.get("system") or "").lower()


def strap_slot_complete(w):
    """A strap bay is designed when it carries its strap selection (selection / strap / bracing,
    or the strap data in fastener_schedule) and the strap-end connection."""
    brace = any(w.get(k) for k in ("selection", "strap", "straps", "bracing", "strap_schedule",
                                   "fastener_schedule")) or \
        bool(w.get("sheathing") and not re.search(r"\bnone\b", str(w["sheathing"]), re.I))
    conn = any(w.get(k) for k in ("connection", "strap_connection", "fastener_schedule"))
    return brace and conn


def capacity_design_unit_shear(pkg):
    """{(direction, line): (v_ELF_plf, v_cd_plf)} at the base story: the rho-ELF unit shear of the
    wall slot and the framework's capacity-design (Omega0-capped / Omega_E*Vn) unit shear
    Ve_cap / L from capacity_design.lines -- the demand capacity-protected shear anchorage of
    that line is designed for (S400 B3.4)."""
    out = {}
    if not isinstance(pkg, dict):
        return out
    for a in pkg.get("anchorage") or []:         # seeded per-line anchorage slots (preferred)
        if isinstance(a, dict) and _num(a.get("v_cd_seed_plf")) is not None and a.get("line") is not None:
            out[(str(a.get("direction")), str(a["line"]))] = (_num(a.get("v_elf_plf")),
                                                               _num(a["v_cd_seed_plf"]))
    if out:
        return out
    lines = ((pkg.get("capacity_design") or {}).get("lines") or {}) \
        if isinstance(pkg.get("capacity_design"), dict) else {}
    slots = {}
    for w in pkg.get("wall_lines") or []:
        if isinstance(w, dict) and w.get("direction") and w.get("story") is not None:
            slots[(str(w["direction"]), wall_slot_line(w), int(w["story"]))] = w
    for key, ln in lines.items():
        if not isinstance(ln, dict):
            continue
        d, l = str(ln.get("direction") or str(key).split(":")[0]), str(ln.get("line") or str(key).split(":")[-1])
        ve = ln.get("Ve_cap_by_story_kip") or {}
        if not ve:
            continue
        k1 = min(ve, key=lambda s: int(s))
        w = slots.get((d, l, int(k1)))
        if not w:
            continue
        V, v = _num(w.get("V_kip")), _num(w.get("v_unit_plf"))
        if not V or not v:
            continue
        L_ft = V * 1000.0 / v
        out[(d, l)] = (v, _num(ve[k1]) * 1000.0 / L_ft)
    return out


def _shear_demand_plf(c):
    """Design unit shear (plf) of an anchorage slot / connection: v_design_plf-type fields, klf
    fields, or the first 'N klf' / 'N plf' in its demand text."""
    for k in ("v_design_plf", "v_demand_plf", "v_u_plf"):
        if _num(c.get(k)) is not None:
            return _num(c.get(k))
    for k in ("v_design_klf", "v_demand_klf", "v_u_klf"):
        if _num(c.get(k)) is not None:
            return 1000.0 * _num(c.get(k))
    m = _UNIT_SHEAR_RX.search(str(c.get("demand") or ""))
    if m:
        return float(m.group(1)) * (1000.0 if m.group(2).lower() in ("klf", "kip/ft") else 1.0)
    return None


def _anchorage_shear_cd_issues(cfg, pkg):
    """Shear anchorage (sill / bottom-track anchors, wall-to-floor transfer) of an S400 wall
    system that requires capacity design, designed at the rho-ELF unit shear although the
    framework's capacity-design shear of the lines is larger -- the published reports listed
    anchorage as capacity-protected (S400 E1.4.1.2 / E2.4.1.2 / E3.3 / E6.4.1.2) and then used
    the ELF shear. Clears when the slot shows its own expected-strength / Omega0 numbers."""
    out = []
    if not isinstance(pkg, dict) or not isinstance(cfg, dict):
        return out
    sysname = str(cfg.get("system") or pkg.get("system") or "").lower()
    if sysname not in _CD_GATE_SYSTEMS:
        return out
    try:
        import cfs_systems as _CS
        _s = cfg.get("seis") or {}
        if not _CS.capacity_design_required(
                sysname, _s.get("R"), _CS.sdc(float(_s.get("SDS", 0) or 0), float(_s.get("SD1", 0) or 0),
                                              float(_s.get("S1", 0) or 0),
                                              str(cfg.get("risk_cat", "II"))))[0]:
            return out
    except Exception:
        return out
    ref = capacity_design_unit_shear(pkg)
    if not ref:
        return out
    v_elf = max(a or 0.0 for a, _b in ref.values())
    v_cd = max(b for _a, b in ref.values())
    for a in pkg.get("anchorage") or []:         # seeded per-line slots: their own line's demand
        if not isinstance(a, dict) or a.get("waived") or _num(a.get("v_cd_seed_plf")) is None:
            continue
        dem = _shear_demand_plf(a)
        if dem is None:
            continue
        txt = _entry_text({k: v for k, v in a.items() if k not in SEED_KEYS})
        own = re.search(r"omega_?e\s*\*?\s*v_?n|expected strength.{0,40}\d", txt, re.I)
        if dem < 0.99 * _num(a["v_cd_seed_plf"]) and not own:
            out.append("[anchorage %s] shear anchorage designed for %.0f plf, below the line's "
                       "capacity-design unit shear v_cd_seed_plf = %.0f plf (rho-ELF %.0f plf; S400 B3.4) "
                       "-- design for it, or record the line's own expected-strength Omega_E*Vn (numbers)"
                       % (a.get("id"), dem, _num(a["v_cd_seed_plf"]), _num(a.get("v_elf_plf")) or 0.0))
    for c in pkg.get("connections") or []:
        if not isinstance(c, dict) or c.get("waived"):
            continue
        head = " ".join(str(c.get(k) or "") for k in ("id", "type", "joint", "selection",
                                                       "description"))
        if not _ANCH_RX.search(head) or re.search(r"uplift|hold[- ]?down|\brod\b|clip|strap",
                                                  head, re.I):
            continue
        dem = _shear_demand_plf(c)
        if dem is None:
            continue
        txt = _entry_text({k: v for k, v in c.items() if k not in SEED_KEYS})
        if re.search(r"om(?:ega)?_?0|Ω\s*0|Ω<sub>0|overstrength|expected|omega_?e|Ω_?E|"
                     r"capacity[- ]design|ve_?cap", txt, re.I):
            continue
        if dem <= 1.05 * v_elf and v_cd > 1.05 * dem:
            out.append("[connection %s] shear anchorage designed for %.0f plf, the rho-ELF unit shear "
                       "(max %.0f plf at the base story), while the capacity-design shear of the "
                       "lines (Omega0-capped / Omega_E*Vn, S400 B3.4) reaches %.0f plf -- design the "
                       "anchorage for it, or record the expected-strength basis (numbers) on the slot"
                       % (c.get("id"), dem, v_elf, v_cd))
    return out


_WV_EXPL_W = re.compile(r"\bW\s*(?:=|:|is)?\s*([\d,]+(?:\.\d+)?)\s*(?:kips?|k)?\b")
_WV_PROD = re.compile(r"(\d*\.\d+)\s*[x×*]\s*([\d,]+(?:\.\d+)?)\s*(?:kips?\s*)?=\s*"
                      r"([\d,]+(?:\.\d+)?)\s*kips?\b", re.I)
_WV_V = re.compile(r"\bV\b(?:_?(?:base|kip))?\s*(?:=|:)?\s*([^;\n]*?)(\d[\d,]*(?:\.\d+)?)\s*kips?\b")


def parse_W_V(obj):
    """(W, V) in kip quoted by a self-check block (dict with W/V keys, or text such as
    'V = 0.1538 x 1767 = 271.8 kip' / 'W 3876 / V 536.7 kip'); None where not found."""
    W = V = None
    if isinstance(obj, dict):
        for k, v in obj.items():
            nk = _norm_key(k)
            if nk in ("w", "wkip", "wtotal", "seismicweight") and _num(v) is not None:
                W = _num(v)
            if nk in ("v", "vkip", "vbase", "baseshear") and _num(v) is not None:
                V = _num(v)
        txt = " ".join(str(v) for v in obj.values() if isinstance(v, str))
    elif isinstance(obj, str):
        txt = obj
    else:
        return None, None
    if W is None:
        m = _WV_EXPL_W.search(txt)
        if m:
            W = float(m.group(1).replace(",", ""))
    pm = _WV_PROD.search(txt)
    if W is None and pm:
        W = float(pm.group(2).replace(",", ""))
    if V is None:
        m = _WV_V.search(txt)
        if m:
            V = float(m.group(2).replace(",", ""))
        elif pm:
            V = float(pm.group(3).replace(",", ""))
    return W, V


def elf_spot_check_compare(pkg, W_ref=None, V_ref=None):
    """[(quantity, quoted, reference)] for every W / V a package self-check quotes that differs
    from the reference (default: the framework ELF of the same run, pkg['elf']) by > 0.5 %."""
    sc = None
    for k in ("elf_spot_check", "elf_self_check", "self_check"):
        if isinstance(pkg, dict) and pkg.get(k):
            sc = pkg[k]
            break
    if sc is None:
        return None
    elf = (pkg or {}).get("elf") or {}
    W_ref = W_ref if W_ref is not None else _num(elf.get("W_kip"))
    V_ref = V_ref if V_ref is not None else _num(elf.get("V_kip"))
    W, V = parse_W_V(sc)
    diffs = []
    for q, a, b in (("W", W, W_ref), ("V", V, V_ref)):
        if a is not None and b:
            if abs(a - b) > 0.005 * abs(b) + 0.05:
                diffs.append((q, a, b))
    return dict(W=W, V=V, W_ref=W_ref, V_ref=V_ref, diffs=diffs)


def _elf_spot_check_issues(pkg):
    r = elf_spot_check_compare(pkg)
    if not r or not r["diffs"]:
        return []
    return ["ELF self-check quotes %s, but the analysis (Section 3) gives %s -- a self-check must use "
            "the same W and V as the analysis; reconcile the numbers or the claim 'matches'"
            % (", ".join("%s = %.1f kip" % (q, a) for q, a, _b in r["diffs"]),
               ", ".join("%s = %.1f kip" % (q, b) for q, _a, b in r["diffs"]))]


def code_basis_issues(cfg, pkg):
    """The 2026-10 US-practice screens in one call (the report's QA chapter lists them):
    screw phi/Omega (S2-20), the screw's own shear strength, ASTM A449 strength by diameter,
    the interaction lower bound (H1-1b aware), ASCE 7-16 snow factors / drift equation /
    rain-on-snow, H1.1 cited for members in compression, rho = 1.0 in SDC D-F without the
    12.3.4.2 showing, seed labels left as the design basis, shear anchorage at ELF on
    capacity-designed systems, and an ELF self-check that does not match the analysis."""
    out = []
    for fn, args in ((_screw_phi_issues, (pkg,)), (_screw_shear_strength_issues, (pkg,)),
                     (_rod_grade_issues, (pkg,)), (_interaction_issues, (pkg,)),
                     (_snow_factor_issues, (pkg,)), (_snow_basis_issues, (pkg,)),
                     (_h11_compression_issues, (pkg,)), (_rho_basis_issues, (cfg, pkg)),
                     (_seed_label_issues, (pkg,)),
                     (_anchorage_shear_cd_issues, (cfg, pkg)), (_elf_spot_check_issues, (pkg,))):
        try:
            out += fn(*args)
        except Exception as ex:                       # a screen must never break the gate
            out.append("code-basis screen %s failed: %s" % (fn.__name__, ex))
    return out


def check(name, root=None, pkg=None, verbose=True):
    """Run the self-consistency check. Returns a list of issue strings ([] == consistent).
    Reads the job's ONE authoritative package (package_path: calc_package_cfs.json on CFS jobs)
    and, when it read the package from disk, stamps design/consistency_result.json for the
    completion gate."""
    issues = []
    path = None
    if root is None:
        base = os.environ.get("STEEL_BUILDER_JOBS") or _here_repo()
        root = os.path.join(base, name)
    _dcfg = _load_cfg(root, name)
    if pkg is None:
        path, _notes, _pissues = package_path(root, _dcfg)
        issues += _pissues
        if verbose:
            for n in _notes:
                print("[consistency] note:", n)
        if path is None:
            want = PKG_CFS if is_cfs_cfg(_dcfg) else "%s (or %s on CFS jobs)" % (PKG_HR, PKG_CFS)
            issues.append(f"design/{want} not found under {root} -- run design_and_report first")
            if verbose:
                _print(name, issues)
            return issues
        try:
            pkg = json.load(open(path, encoding="utf-8"))
        except Exception as ex:
            issues.append(f"{os.path.basename(path)} is not valid JSON: {ex}")
            if verbose:
                _print(name, issues)
            _stamp(root, path, issues)
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
                      ("collector", pkg.get("collectors") or []),
                      ("anchorage", pkg.get("anchorage") or []),     # CFS-29c: never unchecked
                      ("schedule", pkg.get("schedules") or [])):
        for m in lst:
            if not isinstance(m, dict):
                continue
            if m.get("recheck_after_rerun"):
                # pipeline.merge_fills carried this slot's fills into a re-seeded package whose
                # demand moved: the carried checks were computed at the OLD demand (CFS-14)
                issues.append("[%s %s] %s -- the carried checks are at the OLD demand: re-derive them "
                              "at the new demand, then delete 'recheck_after_rerun'"
                              % (kind, m.get("id"), str(m["recheck_after_rerun"])[:200]))
            if m.get("waived"):
                issues += _waiver_issues(kind, m)                    # CFS-29d
            else:
                issues += _entry_issues(kind, m)

    if not os.path.exists(os.path.join(root, "cfg.py")):
        issues.append("jobs/%s/cfg.py not found -- write the building's cfg (including any custom_build) to cfg.py "
                      "FIRST and keep it; the saved OpenSees model must be reproducible and editable for later studies." % name)
    issues += _geometry_issues(_dcfg)                       # units/geometry sanity (story heights in ft, etc.)
    issues += _design_basis_issues(_dcfg, name, pkg)        # R1/R2/R8/R12/R14/R16
    issues += _height_limit_issues(_dcfg, pkg)              # R19 system height limit
    issues += _rbs_declaration_issues(_dcfg, pkg)           # HR-22 port (hot-rolled route)
    issues += _transfer_issues(_dcfg, name, pkg)            # R7/R15 transfer/backstay
    issues += _nonparallel_issues(_dcfg, pkg)               # R10 skewed frame
    issues += _consultancy_issues(_dcfg, pkg)               # Tier A/B real-world guards
    issues += _named_not_computed_issues(pkg)               # R9 named-not-computed
    issues += _system_checks_issues(_dcfg, pkg)             # per-system S400 required checks
    issues += _capacity_design_numeric_issues(_dcfg, pkg)   # NUMERIC gate: demand vs ELF seed (R>3 walls)
    issues += _cfs_slot_issues(_dcfg, pkg)                  # CFS slot screens (walls/HDs/TypeII/uplift/gates)
    issues += _drift_table_issues(pkg)                      # CFS-29c: failing drift rows FAIL
    issues += _theta_issues(pkg)                            # 12.8.7 theta <= theta_max
    issues += _two_stage_issues(_dcfg, pkg)                 # CFS-32 two-stage eligibility
    issues += code_basis_issues(_dcfg, pkg)                 # US-practice code-basis screens (2026-10)
    issues += _completeness_issues(pkg)
    if path is not None:
        _stamp(root, path, issues)
    if verbose:
        _print(name, issues)
    return issues


def _sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def _stamp(root, path, issues):
    """design/consistency_result.json: what the app-side completion gate reads (it is
    engine-free). Records WHICH package was checked (name + sha1 + mtime) so an edit after the
    check makes the stamp stale."""
    try:
        out = dict(package=os.path.basename(path), package_sha1=_sha1(path),
                   package_mtime=os.path.getmtime(path), checked_at=time.time(),
                   n_issues=len(issues), issues=list(issues)[:200],
                   result="PASS" if not issues else "FAIL")
        with open(os.path.join(root, "design", "consistency_result.json"), "w",
                  encoding="utf-8") as f:
            json.dump(out, f, indent=1)
    except Exception:
        pass


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
                 "connections list is empty"):
        assert want in blob, "missing screen: %s\n%s" % (want, blob)
    # canonical SDC: SD1-governed site (SDS=0.30 says B, SD1=0.25 says D) must use the
    # WORSE bin; the old SD1-blind proxy called this SDC B and missed the gypsum 35-ft cap
    _sd1cfg = dict(cfg, system="gypsum_wall", heights_ft=[10.0] * 4,
                   seis=dict(SDS=0.30, SD1=0.25, S1=0.10, R=2.0, Ie=1.0))
    assert any("35" in i for i in _height_limit_issues(_sd1cfg)), \
        "SD1-governed SDC D must trip the gypsum 35-ft screen"
    # NUMERIC capacity-design gate (R>3 wall systems): demand == ELF seed must FAIL ...
    cd_bad = dict(system="wsp_shearwall",
                  holddowns=[dict(id="hd-X-A", T_cum_kip=27.0, T_cd_seed_kip=51.9,
                                  device_class="rod", limit_state="tension", cited="S400",
                                  basis="cumulative tension", DC=0.90, capacity=30.0)])
    gate = _capacity_design_numeric_issues(dict(cfg, system="wsp_shearwall"), cd_bad)
    assert gate and "below the capacity-design seed" in gate[0], gate      # T_cd seed governs
    cd_bad_elf = dict(system="wsp_shearwall",
                      holddowns=[dict(id="hd-X-A", T_cum_kip=27.0, limit_state="tension",
                                      cited="S400", DC=0.90, capacity=30.0)])
    gate = _capacity_design_numeric_issues(dict(cfg, system="wsp_shearwall"), cd_bad_elf)
    assert gate and "capacity-design amplification not applied" in gate[0], gate
    # ... amplified demand passes, wind-governed demand passes, missing data stays silent
    cd_ok = dict(system="wsp_shearwall",
                 holddowns=[dict(id="hd-X-A", T_cum_kip=27.0, T_design_kip=51.9, DC=0.9,
                                 capacity=57.7)])
    assert _capacity_design_numeric_issues(dict(cfg, system="wsp_shearwall"), cd_ok) == []
    cd_wind = dict(system="wsp_shearwall",
                   holddowns=[dict(id="hd-X-A", T_cum_kip=20.0, T_wind_kip=28.0,
                                   T_design_kip=28.0)])
    assert _capacity_design_numeric_issues(dict(cfg, system="wsp_shearwall"), cd_wind) == []
    cd_na = dict(system="wsp_shearwall", holddowns=[dict(id="hd-X-A", T_cum_kip=27.0)])
    assert _capacity_design_numeric_issues(dict(cfg, system="wsp_shearwall"), cd_na) == []
    # gypsum (S400 E6) has a capacity-design chain too (CFS-06); the gate is scoped to systems
    # where S400 B3.4 applies (A1.2.3: R = 3 in SDC B/C is waived; not_detailed is not S400)
    assert _capacity_design_numeric_issues(dict(cfg, system="gypsum_wall"), cd_bad), \
        "gypsum E6 walls are capacity-design gated"
    assert _capacity_design_numeric_issues(dict(cfg, system="not_detailed"), cd_bad) == [], \
        "gate is scoped to S400 wall systems only"
    # a clean package + cfg raises none of the CFS screens
    good = dict(
        wall_lines=[dict(id="wall-X-A-s1", sheathing="7/16 OSB", fastener_schedule="#8@4/12",
                         limit_state="S400 E1", cited="S400 E1.3 Table E1.3-1 (seismic and other in-plane loads)", DC=0.91,
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
                                  "0.9D+1.0W net uplift path designed; tributary distribution",
                             expected_strength=dict(Omega_E=1.3, T_expected_kip=11.7),
                             net_uplift=dict(combo="0.9D+1.0W", T_uplift_kip=3.1, DC=0.4)),
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

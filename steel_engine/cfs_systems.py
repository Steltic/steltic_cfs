"""
cfs_systems.py -- CFS seismic-system table, drift limits, materials, and the analysis-fidelity
tier definitions + preflight (scope decision #8). Reference data + screening only; no capacities.

Sources the agent must still GROUND in the RAG: ASCE 7-22 Table 12.2-1 (light-frame CFS rows)
and AISI S400 (system provisions). This module exists so cfg construction and the
preflight screens have one authoritative in-repo table, exactly like engine3d.seis() did for
hot-rolled.
"""

E_KSI = 29500.0
G_KSI = 11300.0

# system key -> dict(R, Cd, Om0, height limit ft by SDC {B,C,D,E,F}, standard, notes)
#   light_frame -- light-frame (stud wall) construction: drives the 12.2.3.3 exception and the
#                  12.3.1.1(3) flexible-diaphragm condition (NOT the Table 12.12-1 drift row,
#                  which keys on story count + drift-tolerant finishes, see drift_limit)
#   s400        -- AISI S400 Chapter E section that governs the system (None = S400 n/a)
#   moment_frame -- system made solely of moment frames (12.12.1.1 Delta_a/rho in SDC D-F)
NL = None   # no limit
NP = 0.0    # not permitted
SYSTEMS = {
    # -- light-frame (CFS) bearing-wall systems, ASCE 7-22 Table 12.2-1 --
    "wsp_shearwall":   dict(R=6.5, Cd=4.0, Om0=3.0, hlim={"B": NL, "C": NL, "D": 65, "E": 65, "F": 65},
                            std="AISI S400 E1", s400="E1", light_frame=True,
                            label="CFS shear wall, wood structural panels"),
    "steelsheet_wall": dict(R=6.5, Cd=4.0, Om0=3.0, hlim={"B": NL, "C": NL, "D": 65, "E": 65, "F": 65},
                            std="AISI S400 E2", s400="E2", light_frame=True,
                            label="CFS shear wall, steel sheet"),
    # Gypsum board / fiberboard sheathed walls: AISI S400-20 SECTION E6 (United States and
    # Mexico; E5 is the Canada-only "other materials" section, E5.1). Table E6.3-1 is "for
    # Seismic Loads"; h:w <= 2:1 gypsum, <= 1:1 fiberboard (E6.3.1.1); Omega_E = 1.5 (E6.3.3);
    # chords/collectors/hold-downs/anchorage are capacity-protected (E6.4.1.2).
    # WIND capacity is NOT in S400: AISI S240-20 B5.2.2.3.4 (gypsum, Table B5.2.2.3-3) /
    # B5.2.2.3.5 (fiberboard) with phi_v = 0.65 LRFD (S240 B5.2.3).
    "gypsum_wall":     dict(R=2.0, Cd=2.0, Om0=2.5, hlim={"B": NL, "C": NL, "D": 35, "E": NP, "F": NP},
                            std="AISI S400 E6", s400="E6", light_frame=True,
                            label="CFS wall, gypsum board or fiberboard panels (S400 E6)"),
    "strap_braced":    dict(R=4.0, Cd=3.5, Om0=2.0, hlim={"B": NL, "C": NL, "D": 65, "E": 65, "F": 65},
                            std="AISI S400 E3", s400="E3", light_frame=True,
                            label="CFS strap-braced wall"),
    # SBMF: Table 12.2-1 row C.12 reads "35 35 35 35 35" -- the 35-ft limit applies in EVERY
    # SDC B-F (footnote n permits one-story systems up to 35 ft with dead load <= 35 psf).
    "sbmf":            dict(R=3.5, Cd=3.5, Om0=3.0, hlim={"B": 35, "C": 35, "D": 35, "E": 35, "F": 35},
                            std="AISI S400 E4", s400="E4", light_frame=False, moment_frame=True,
                            label="CFS special bolted moment frame"),
    # -- systems not specifically detailed (portals etc.), Table 12.2-1 row H --
    "not_detailed":    dict(R=3.0, Cd=3.0, Om0=3.0, hlim={"B": NL, "C": NL, "D": NP, "E": NP, "F": NP},
                            std="AISI S100 only (no S400)", s400=None, light_frame=False,
                            label="CFS system not specifically detailed"),
    # (storage racks EXCLUDED from scope 2026-07-30 -- no rack systems in this table)
}

LIGHT_FRAME_SYSTEMS = tuple(k for k, v in SYSTEMS.items() if v.get("light_frame"))

# AISI S400-20 Table A3.2-1, sheet and strip (A653, A1003, A1008, A1011, ...): (Ry, Rt) by Fy band
def ry_rt(Fy_ksi):
    """(Ry, Rt) for CFS sheet/strip per AISI S400-20 Table A3.2-1:
    Fy < 37 ksi -> (1.5, 1.2); 37 <= Fy < 40 -> (1.4, 1.1); 40 <= Fy < 50 -> (1.3, 1.1);
    Fy >= 50 -> (1.1, 1.1)."""
    F = float(Fy_ksi)
    if F < 37.0:
        return 1.5, 1.2
    if F < 40.0:
        return 1.4, 1.1
    if F < 50.0:
        return 1.3, 1.1
    return 1.1, 1.1


# specified minimum tensile strength Fu (ksi) for the common ASTM A1003 / A1008 / A1011 grades
FU_BY_FY = {33: 45.0, 37: 52.0, 40: 55.0, 50: 65.0}


def omega_E(system_key, v_finish_ratio=None, Fy_ksi=None):
    """Expected-strength factor Omega_E (United States/Mexico) of an AISI S400-20 system,
    returned as (Omega_E or None, basis text).
      wsp_shearwall   E1.3.3: (1.1 vn + v_finish)/vn <= 1.8, v_finish >= 0.1 vn  -> >= 1.2
      steelsheet_wall E2.3.3: same form                                           -> >= 1.2
      strap_braced    E3.3.3: (Ry Vn/w + v_finish)/(Vn/w) <= 1.8, v_finish >= 0.2 Vn/w
                      -> Ry + max(ratio, 0.2); Ry from Table A3.2-1 at the STRAP Fy
      gypsum_wall     E6.3.3: Omega_E = 1.5
    v_finish_ratio = v_finish/vn (or v_finish/(Vn/w) for straps); None -> the code minimum.
    SBMF (E4) and non-S400 systems have no Omega_E (E4.3 uses the expected moment)."""
    if system_key in ("wsp_shearwall", "steelsheet_wall"):
        r = 0.1 if v_finish_ratio is None else max(float(v_finish_ratio), 0.1)
        sec = "E1.3.3" if system_key == "wsp_shearwall" else "E2.3.3"
        return min(1.1 + r, 1.8), ("S400 %s: Omega_E = (1.1vn + v_finish)/vn <= 1.8 with "
                                    "v_finish/vn = %.2f (>= 0.1)" % (sec, r))
    if system_key == "strap_braced":
        r = 0.2 if v_finish_ratio is None else max(float(v_finish_ratio), 0.2)
        if Fy_ksi is None:
            return None, ("S400 E3.3.3: Omega_E = (Ry Vn/w + v_finish)/(Vn/w) <= 1.8 with "
                          "v_finish >= 0.2Vn/w -> Ry + %.2f; strap Fy not declared "
                          "(cfg['strap_Fy_ksi']): Gr 33 -> %.2f, Gr 50 -> %.2f"
                          % (r, min(ry_rt(33)[0] + r, 1.8), min(ry_rt(50)[0] + r, 1.8)))
        Ry = ry_rt(Fy_ksi)[0]
        return min(Ry + r, 1.8), ("S400 E3.3.3: Omega_E = Ry + v_finish/(Vn/w) = %.2f + %.2f "
                                  "(Ry Table A3.2-1 at Fy = %g ksi; v_finish >= 0.2Vn/w), "
                                  "<= 1.8" % (Ry, r, Fy_ksi))
    if system_key == "gypsum_wall":
        return 1.5, "S400 E6.3.3: Omega_E = 1.5 (gypsum board / fiberboard sheathing)"
    if system_key == "sbmf":
        return None, ("S400 E4.3: required strength from the EXPECTED moment at the bolted "
                      "connection (Ve per E4.3.3), need not exceed Omega_0 Eh (E4.3.1)")
    return None, "no S400 expected-strength factor (system not detailed per S400)"


def capacity_design_required(system_key, R=None, SDC=None):
    """(required, basis): AISI S400-20 B3.4 capacity design (expected strength of the
    designated mechanism, need not exceed the Omega_0-level load effect) applies to every
    S400 system EXCEPT where A1.2.3 waives S400: United States, SDC B or C AND the design
    R = 3 (then S100/S240 only). Gypsum/fiberboard walls (R = 2, S400 E6) are NOT waived --
    their chords, hold-downs, collectors and anchorage are capacity-protected (E6.4.1.2)."""
    sysd = SYSTEMS.get(system_key) or {}
    if not sysd.get("s400"):
        return False, ("%s: not an S400 system -- AISI S100/S240 strength design only"
                       % system_key)
    R = float(sysd["R"] if R is None else R)
    cat = (SDC or "").upper()
    if cat == "A":
        return False, ("SDC A: ASCE 7-22 11.7 / 1.4 minimum lateral force; S400 capacity "
                       "design not triggered (agent confirms)")
    if abs(R - 3.0) < 1e-9 and cat in ("B", "C"):
        return False, ("S400 A1.2.3: SDC %s with R = 3 -- members/connections need only be "
                       "designed to AISI S100/S240 (no capacity-design chain)" % cat)
    return True, ("S400 B3.4 + %s: elements outside the designated energy-dissipating "
                  "mechanism are designed for the expected strength (Omega_E*Vn) of the "
                  "mechanism, need not exceed the Omega_0-level load effect (R = %.2f, SDC %s; "
                  "A1.2.3 waiver does not apply)" % (sysd["std"], R, cat or "?"))


def wind_capacity_basis(system_key):
    """Where the WIND (in-plane, non-seismic) shear capacity of the selected wall comes from.
    There are no separate "wind columns" in S400-20: Tables E1.3-1/E2.3-1 are 'for Seismic
    and Other In-Plane Loads' (one vn column set, phi_v 0.60 LRFD per E1.3.2/E2.3.2);
    Table E6.3-1 (gypsum/fiberboard) is for SEISMIC loads only -- wind uses AISI S240."""
    if system_key == "gypsum_wall":
        return ("AISI S240-20 B5.2.2.3.4 Table B5.2.2.3-3 (gypsum) / B5.2.2.3.5 (fiberboard), "
                "phi_v = 0.65 LRFD (B5.2.3); combined-sheathing rule B5.2.2.3.6")
    if system_key == "wsp_shearwall":
        return ("AISI S400-20 Table E1.3-1 (seismic AND other in-plane loads -- one vn set), "
                "phi_v = 0.60 LRFD (E1.3.2); or S240 B5.2.2.3.3")
    if system_key == "steelsheet_wall":
        return ("AISI S400-20 Table E2.3-1 / E2.3.1.1.1 (seismic AND other in-plane loads), "
                "phi_v = 0.60 LRFD (E2.3.2); or S240 B5.2.2.3.2")
    if system_key == "strap_braced":
        return ("strap tension yielding Tn = Ag*Fy (S400 E3.3.1 / S100 D2), phi per S400 E3.3.2 "
                "/ S240 B5.3")
    return "AISI S100 member/connection strength"


def aspect_limit(system_key, sheathing=None):
    """Maximum h:w of a Type I shear-wall segment that may be counted.
    gypsum 2:1, fiberboard 1:1 (S400-20 E6.3.1.1 / Table E6.3-1); None = no fixed limit
    here (WSP/steel sheet reduce capacity above 2:1 per E1.3.1.1.1/E2.3.1.1.1 -- agent)."""
    if system_key == "gypsum_wall":
        return 1.0 if str(sheathing or "").lower().startswith("fiber") else 2.0
    return None


def strap_ductility_check(Fy_ksi, Fu_ksi=None, Ag_in2=None, An_in2=None, method=2,
                          Ry=None, Rt=None):
    """AISI S400-20 E3.4.1(a) strap-connection ductility, as a NUMERIC check.
      Method 1: welded, configured so gross-section yielding governs -- caller asserts.
      Method 2: BOTH (Rt Fu)/(Ry Fy) >= 1.2 AND Rt An Fu > Ry Ag Fy.
      Method 3: gross yielding under cyclic load shown by ASTM E2126 tests -- caller asserts.
    Ry/Rt default to Table A3.2-1 at Fy (coupon values: pass Ry = Rt = 1.0, user note).
    Returns dict(ok, method, ratio_RtFu_RyFy, RtAnFu_kip, RyAgFy_kip, message).
    Gr 33 (Fu 45): 1.2*45/(1.5*33) = 1.09 < 1.2 -> Method 2 NOT available (screwed/bolted
    Gr 33 straps need Method 1 or 3)."""
    Ry0, Rt0 = ry_rt(Fy_ksi)
    Ry = Ry0 if Ry is None else float(Ry)
    Rt = Rt0 if Rt is None else float(Rt)
    if Fu_ksi is None:
        Fu_ksi = FU_BY_FY.get(int(round(float(Fy_ksi))))
    out = dict(method=method, Ry=Ry, Rt=Rt, Fy_ksi=Fy_ksi, Fu_ksi=Fu_ksi)
    if method in (1, 3):
        out.update(ok=None, message="E3.4.1(a)(%d): %s -- not a computed check; document it"
                   % (method, "welded, gross-section yielding governs" if method == 1
                      else "ASTM E2126 cyclic tests demonstrate gross-section yielding"))
        return out
    if Fu_ksi is None:
        out.update(ok=False, message="E3.4.1(a)(2): Fu not given and not a tabulated grade -- "
                                     "cannot verify; NOT SATISFIED until Fu is stated")
        return out
    ratio = Rt * Fu_ksi / (Ry * Fy_ksi)
    out["ratio_RtFu_RyFy"] = round(ratio, 3)
    ok1 = ratio >= 1.2 - 1e-9
    ok2 = None
    if Ag_in2 is not None and An_in2 is not None:
        out["RtAnFu_kip"] = round(Rt * An_in2 * Fu_ksi, 2)
        out["RyAgFy_kip"] = round(Ry * Ag_in2 * Fy_ksi, 2)
        ok2 = out["RtAnFu_kip"] > out["RyAgFy_kip"]
    if not ok1:
        out.update(ok=False, message=(
            "E3.4.1(a)(2) FAILS: (Rt Fu)/(Ry Fy) = %.2f*%.0f/(%.2f*%.0f) = %.2f < 1.2 -- Method 2 "
            "is NOT available for this strap grade; use Method 1 (welded) or Method 3 (tests)"
            % (Rt, Fu_ksi, Ry, Fy_ksi, ratio)))
    elif ok2 is None:
        out.update(ok=None, message="E3.4.1(a)(2): ratio %.2f >= 1.2 OK; Rt An Fu > Ry Ag Fy "
                                    "NOT evaluated (give Ag and An)" % ratio)
    else:
        out.update(ok=bool(ok2), message="E3.4.1(a)(2): ratio %.2f >= 1.2; Rt An Fu = %.2f %s "
                   "Ry Ag Fy = %.2f kip" % (ratio, out["RtAnFu_kip"], ">" if ok2 else "<=",
                                            out["RyAgFy_kip"]))
    return out


# AISI S400-20 Table E1.3.1.2-1: Type II shear resistance adjustment factor Ca
_CA_RATIOS = (1.0 / 3.0, 0.5, 2.0 / 3.0, 5.0 / 6.0, 1.0)
_CA_TABLE = {   # percent full-height sheathing -> Ca at the opening-height ratios above
    10: (1.00, 0.69, 0.53, 0.43, 0.36), 20: (1.00, 0.71, 0.56, 0.45, 0.38),
    30: (1.00, 0.74, 0.59, 0.49, 0.42), 40: (1.00, 0.77, 0.63, 0.53, 0.45),
    50: (1.00, 0.80, 0.67, 0.57, 0.50), 60: (1.00, 0.83, 0.71, 0.63, 0.56),
    70: (1.00, 0.87, 0.77, 0.69, 0.63), 80: (1.00, 0.91, 0.83, 0.77, 0.71),
    90: (1.00, 0.95, 0.91, 0.87, 0.83), 100: (1.00, 1.00, 1.00, 1.00, 1.00),
}


def type_ii_Ca(pct_full_height, max_opening_height_ratio):
    """Ca per AISI S400-20 Table E1.3.1.2-1 (bilinear interpolation permitted, E1.3.1.2).
    pct_full_height = 100*Sum(Li)/total wall width (E1.3.1.2.1); ratio = max opening clear
    height / wall height (E1.3.1.2.2); ratio <= 1/3 -> 1.0. Outside the table (pct < 10)
    raises ValueError -- no extrapolation."""
    p = float(pct_full_height)
    r = max(float(max_opening_height_ratio), _CA_RATIOS[0])
    if p < 10.0 - 1e-9 or p > 100.0 + 1e-9 or r > 1.0 + 1e-9:
        raise ValueError("Type II Ca outside Table E1.3.1.2-1 (pct %.1f, ratio %.3f)" % (p, r))

    def row(pp):
        return _CA_TABLE[pp]

    def interp_r(vals):
        for (r0, v0), (r1, v1) in zip(zip(_CA_RATIOS, vals), zip(_CA_RATIOS[1:], vals[1:])):
            if r <= r1 + 1e-12:
                return v0 + (v1 - v0) * (r - r0) / (r1 - r0)
        return vals[-1]
    lo = max(10, min(90, int(p // 10) * 10))
    hi = lo + 10
    c0, c1 = interp_r(row(lo)), interp_r(row(hi))
    return c0 + (c1 - c0) * (p - lo) / 10.0


def cu_factor(SD1):
    """ASCE 7-22 Table 12.8-1 coefficient for upper limit on calculated period, Cu, with
    straight-line interpolation between the tabulated SD1 points:
    >=0.4 -> 1.4, 0.3 -> 1.4, 0.2 -> 1.5, 0.15 -> 1.6, <=0.1 -> 1.7."""
    pts = [(0.1, 1.7), (0.15, 1.6), (0.2, 1.5), (0.3, 1.4), (0.4, 1.4)]
    x = float(SD1)
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return 1.4


def sdc(SDS, SD1, S1=0.0, risk_cat="II"):
    """CANONICAL Seismic Design Category (ASCE 7-22 Section 11.6): the more severe of
    Table 11.6-1 (SDS) and Table 11.6-2 (SD1), plus the S1 >= 0.75 override -> E for
    Risk Category I-III, F for Risk Category IV. Use THIS everywhere SDC is derived
    (rho defaults, height limits, report) so the proxies cannot diverge."""
    rc4 = str(risk_cat).strip().upper() in ("IV", "4")
    if S1 is not None and float(S1) >= 0.75:
        return "F" if rc4 else "E"

    def _cat(x, rows):
        # rows = [(upper_bound, cat_I_III, cat_IV)]; last row is the >= catch-all
        for ub, c123, c4 in rows:
            if x < ub:
                return c4 if rc4 else c123
        return "D"
    c1 = _cat(float(SDS or 0.0), [(0.167, "A", "A"), (0.33, "B", "C"), (0.50, "C", "D")])
    c2 = _cat(float(SD1 or 0.0), [(0.067, "A", "A"), (0.133, "B", "C"), (0.20, "C", "D")])
    return max(c1, c2)


# ASCE 7-22 Table 12.12-1 drift limits (fraction of h_sx) by structure class / Risk Category
_DRIFT_ROWS = {   # (row 1: <= 4 stories, drift-tolerant finishes; row 4: all other structures)
    "row1": {"I": 0.025, "II": 0.025, "III": 0.020, "IV": 0.015},
    "other": {"I": 0.020, "II": 0.020, "III": 0.015, "IV": 0.010},
}


def drift_limit(system_key, n_stories, risk_cat="II", finishes_accommodate=None, SDC=None,
                rho=1.0, single_story_no_limit=False, with_basis=False):
    """Allowable story drift ratio Delta_a/h_sx per ASCE 7-22 Table 12.12-1.

    Row 1 (0.025/0.020/0.015 for RC I-II/III/IV) applies to ANY structure other than masonry
    shear-wall structures, FOUR STORIES OR LESS above the base, whose interior walls,
    partitions and ceilings are designed to accommodate the design-earthquake drifts -- the
    row is NOT tied to "light-frame" (SBMF and portal structures qualify too). Otherwise the
    'all other structures' row (0.020/0.015/0.010).
      finishes_accommodate -- True / False as declared (cfg['drift_tolerant_finishes']);
                 None = not declared: the PERMISSIVE row-1 value is returned for <= 4 stories
                 (upper bound used by screens that only reject drift_limit > allowed);
                 cfs_engine.run() always passes an explicit bool.
      single_story_no_limit -- footnote a: no drift limit for single-story structures with
                 drift-tolerant finishes (12.12.3 separation still applies) -> returns None.
      SDC, rho  -- 12.12.1.1: systems SOLELY of moment frames (SBMF) in SDC D-F: Delta_a/rho.
    Returns the ratio (or None for footnote a); with_basis=True returns (ratio, basis)."""
    rc = str(risk_cat).strip().upper()
    rc = {"1": "I", "2": "II", "3": "III", "4": "IV"}.get(rc, rc)
    if rc not in ("I", "II", "III", "IV"):
        rc = "II"
    acc = True if finishes_accommodate is None else bool(finishes_accommodate)
    row1 = acc and int(n_stories) <= 4
    if row1 and single_story_no_limit and int(n_stories) == 1:
        basis = "Table 12.12-1 footnote a: single story, finishes accommodate drift -> NO limit"
        return (None, basis) if with_basis else None
    dl = _DRIFT_ROWS["row1" if row1 else "other"][rc]
    basis = ("Table 12.12-1 %s, RC %s: %.3f h_sx" %
             ("row 1 (<= 4 stories, interior walls/partitions/ceilings accommodate drift%s)"
              % (" -- ASSUMED, not declared" if finishes_accommodate is None else "")
              if row1 else "'all other structures'", rc, dl))
    sysd = SYSTEMS.get(system_key) or {}
    if sysd.get("moment_frame") and str(SDC or "").upper() in ("D", "E", "F") and rho:
        dl = dl / float(rho)
        basis += "; 12.12.1.1 moment frames in SDC %s: Delta_a/rho = /%.2f" % (SDC, float(rho))
    return (dl, basis) if with_basis else dl


def seis_cfs(SDS, SD1, S1, system, Ie=1.0, TL=8.0, Ct=0.02, x=0.75):
    """cfg['seis'] builder from the CFS system table (drop-in analogue of engine3d.seis()).
    Ct/x default to the 'all other systems' approximate-period values -- light-frame CFS has no
    special Ta formula in 12.8.2.1."""
    s = SYSTEMS[system]
    return dict(SDS=SDS, SD1=SD1, S1=S1, R=s["R"], Cd=s["Cd"], Om0=s["Om0"], Ie=Ie, TL=TL,
                Ct=Ct, x=x, Cu=cu_factor(SD1), system=system)


def height_check(system, SDC, hn_ft):
    """(ok, message). NP -> not permitted; number -> limit; None -> no limit."""
    lim = SYSTEMS[system]["hlim"].get(SDC.upper())
    if lim is NP or (isinstance(lim, float) and lim == 0.0):
        return False, "%s is NOT PERMITTED in SDC %s (ASCE 7-22 Table 12.2-1)" % (system, SDC)
    if lim is NL or lim is None:
        return True, None
    if hn_ft > lim:
        return False, "%s exceeds the %s-ft height limit in SDC %s (hn = %.1f ft)" % (
            system, lim, SDC, hn_ft)
    return True, None


# ---------------- analysis fidelity tiers (scope decision #8) ----------------

TIERS = {
    0: dict(name="Standard", elements="elasticBeamColumn + Ae/I_eff iteration",
            for_="wall-framed buildings (walls are S400-calibrated springs)"),
    1: dict(name="Thin-walled members", elements="dispBeamColumnAsym/mixedBeamColumnAsym + Ae/I_eff",
            for_="portal frames, canopies, single-channel members"),
    2: dict(name="High fidelity", elements="Du&Hajjar elements + EWM effective fiber laws, incremental",
            for_="torsion-critical slender portals, verification passes"),
}

_TIER_MIN = {  # minimum sensible tier by declared structure kind
    "wall": 0, "podium": 0,
    # single elevated level on posts (storage mezzanine / work platform) on the wall path:
    # the only level is a FLOOR (live load, storage weight), not a roof
    "platform": 0, "mezzanine": 0,
    "portal": 1, "canopy": 1, "purlin": 0,
    "portal_singlechannel": 1,   # Tier 2 recommended; 1 is the floor
}


def preflight_fidelity(structure_kind, tier):
    """Tier-vs-structure screen (the brief-form tickbox is user-chosen; this is the WARN gate).
    Returns list of warning strings (empty = ok)."""
    out = []
    if tier not in TIERS:
        return ["analysis_fidelity=%r is not a tier (0, 1, 2)" % (tier,)]
    floor_ = _TIER_MIN.get(structure_kind)
    if floor_ is None:
        out.append("unknown structure kind %r -- declare one of %s"
                   % (structure_kind, sorted(_TIER_MIN)))
        return out
    if tier < floor_:
        out.append("Tier %d selected for %r but Tier %d is the sensible minimum (%s). "
                   "Proceeding would mis-state stiffness (warping/torsion or P-Delta path). "
                   "Raise cfg['analysis_fidelity'] or justify explicitly in the report."
                   % (tier, structure_kind, floor_, TIERS[floor_]["for_"]))
    if structure_kind == "portal_singlechannel" and tier < 2:
        out.append("single-channel portals: shear-center torsion at every load point -- Tier 2 "
                   "recommended; justify Tier %d explicitly (see Ex27)." % tier)
    return out


def _selftest():
    print("cfs_systems self-test")
    assert SYSTEMS["wsp_shearwall"]["R"] == 6.5 and SYSTEMS["strap_braced"]["R"] == 4.0
    assert SYSTEMS["gypsum_wall"]["R"] == 2.0 and SYSTEMS["sbmf"]["R"] == 3.5
    ok, msg = height_check("steelsheet_wall", "D", 78.0)
    assert not ok and "65" in msg, msg
    ok, _ = height_check("steelsheet_wall", "C", 78.0)
    assert ok
    ok, msg = height_check("gypsum_wall", "E", 20.0)
    assert not ok and "NOT PERMITTED" in msg
    ok, msg = height_check("sbmf", "B", 40.0)          # 35-ft limit in EVERY SDC B-F
    assert not ok and "35" in msg, msg
    # Table 12.8-1 Cu with interpolation
    assert cu_factor(0.45) == 1.4 and cu_factor(0.3) == 1.4 and cu_factor(0.05) == 1.7
    assert abs(cu_factor(0.25) - 1.45) < 1e-9 and abs(cu_factor(0.175) - 1.55) < 1e-9
    assert abs(cu_factor(0.125) - 1.65) < 1e-9
    # canonical SDC (Tables 11.6-1/2, worse governs; S1 >= 0.75 override)
    assert sdc(1.0, 0.5) == "D" and sdc(0.30, 0.05) == "B"
    assert sdc(0.30, 0.15) == "C", "SD1 table must govern when worse"
    assert sdc(0.40, 0.10, S1=0.80) == "E" and sdc(0.40, 0.10, S1=0.80, risk_cat="IV") == "F"
    assert sdc(0.30, 0.05, risk_cat="IV") == "C"
    assert drift_limit("wsp_shearwall", 4) == 0.025
    assert drift_limit("wsp_shearwall", 6) == 0.020
    # CFS-27: the 0.025 row is "<= 4 stories + drift-tolerant finishes", not "light-frame"
    assert drift_limit("sbmf", 1) == 0.025                       # undeclared -> permissive row 1
    assert drift_limit("sbmf", 2, finishes_accommodate=False) == 0.020
    assert drift_limit("wsp_shearwall", 3, finishes_accommodate=False) == 0.020
    assert drift_limit("wsp_shearwall", 4, "IV") == 0.015
    assert drift_limit("sbmf", 2, "II", True, SDC="D", rho=1.3) == 0.025 / 1.3   # 12.12.1.1
    assert drift_limit("sbmf", 1, single_story_no_limit=True) is None             # footnote a
    # CFS-06/07: gypsum is S400 E6 (US), capacity design required (R=2 is not the A1.2.3 R=3)
    assert SYSTEMS["gypsum_wall"]["std"] == "AISI S400 E6"
    req, _b = capacity_design_required("gypsum_wall", SDC="B")
    assert req and omega_E("gypsum_wall")[0] == 1.5
    assert not capacity_design_required("not_detailed", SDC="C")[0]
    assert aspect_limit("gypsum_wall") == 2.0 and aspect_limit("gypsum_wall", "fiberboard") == 1.0
    assert "0.65" in wind_capacity_basis("gypsum_wall") and "S240" in wind_capacity_basis("gypsum_wall")
    # CFS-18: strap Omega_E = Ry + 0.2 (>= finish floor), <= 1.8
    assert abs(omega_E("strap_braced", Fy_ksi=50)[0] - 1.3) < 1e-9
    assert abs(omega_E("strap_braced", Fy_ksi=33)[0] - 1.7) < 1e-9
    assert abs(omega_E("wsp_shearwall")[0] - 1.2) < 1e-9
    # CFS-19: S400 E3.4.1(a) Method 2 -- Gr 33 fails the 1.2 ratio, Gr 50 passes
    d33 = strap_ductility_check(33.0, Ag_in2=0.2, An_in2=0.2)
    assert d33["ok"] is False and abs(d33["ratio_RtFu_RyFy"] - 1.091) < 1e-3
    d50 = strap_ductility_check(50.0, Ag_in2=0.30, An_in2=0.25)
    assert d50["ok"] is True and d50["ratio_RtFu_RyFy"] == 1.3
    assert strap_ductility_check(50.0, Ag_in2=0.30, An_in2=0.20)["ok"] is False
    # CFS-20: Table E1.3.1.2-1
    assert abs(type_ii_Ca(60, 0.5) - 0.83) < 1e-9 and type_ii_Ca(30, 0.3) == 1.0
    assert abs(type_ii_Ca(65, 0.5) - 0.85) < 1e-9
    s = seis_cfs(1.0, 0.45, 0.45, "strap_braced")
    assert s["R"] == 4.0 and s["Cd"] == 3.5 and s["Cu"] == 1.4
    w = preflight_fidelity("portal_singlechannel", 0)
    assert w and any("Tier 2" in x for x in w)
    assert preflight_fidelity("wall", 0) == []
    assert preflight_fidelity("portal", 1) == []
    assert "rack_downaisle" not in SYSTEMS and "rack_drivein" not in _TIER_MIN  # descope 2026-07-30
    print("  systems: %d entries; tiers: %d; sample WARN: %s..." % (len(SYSTEMS), len(TIERS), w[0][:60]))
    print("SELF-TEST PASS")


if __name__ == "__main__":
    _selftest()

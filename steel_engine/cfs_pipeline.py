"""
cfs_pipeline.py -- Stage 3: CFS demand package + schedule seeds (NO capacities).

Consumes cfs_engine.run() and writes the CFS calc_package: per-wall-line shear slots (unit shear
demand, sheathing/fastener selection = agent), per-line hold-down/chord slots (cumulative tension,
device class from the envelope bands, rod switch flagged), drift table, collector seeds at
declared re-entrant/step lines, and stud-schedule seed rows (cumulative axial by story = agent's
web-crippling/interaction inputs). The agent fills limit_state/cited/capacity/DC exactly as in
the hot-rolled contract. LRFD only (scope decision #3).

Wall-path seeds (2026-10 fixes): companion L 0.5 only where 2.3.1/2.3.6 Exception 1 permits
(live_companion_factor); numeric capacity design for every S400 system that requires it
(S400 B3.4 / A1.2.3 -- incl. gypsum E6, Omega_E per E1.3.3/E2.3.3/E3.3.3/E6.3.3, T_cd =
min(Omega_E*Vn stack, Omega0 stack)); Type II Ca + distributed uplift (E1.4.2.2); strap
ductility E3.4.1(a) as a numeric check; Omega0 footnote-b cut only where 12.3.1 supports the
flexible idealization; collector Fpx seeds (12.10.1.1 / 12.10.2.1 by SDC); wind by face-width
tributaries with the Fig. 27.3-8 Case 2 envelope, z from grade, leeward qh, Gf (26.11.5) and
the 27.1.5 minimum; platform/mezzanine joist/beam/post seeds; per-direction systems (12.2.2).
Optional cfg keys: type_ii, selected_Vn_kip, strap_Fy_ksi / strap_Fu_ksi / strap_Ag_in2 /
strap_Ag_in2_per_strap / strap_An_in2 / strap_connection_method, v_finish_ratio,
diaphragm_material / diaphragm_topping_in / diaphragm_MDD_ADVE, occupancy / garage /
assembly, collector_lines dict entries, wind = dict(V, exposure, z_base_ft, n1_hz, damping,
Cp_ww, Cp_lw, Cnet, parapet_ft, roof_projection_ft, trib_width_ft), joist_spacing_in,
beam_span_ft, beam_trib_ft, post_trib_sf.
"""
import json, math
import wall_line as WL
import cfs_systems as CS
import cfs_engine as CE

COMBOS_NOTE = ("ASCE 7-22 2.3 LRFD; seismic cases carry (1.2+0.2SDS)D and 0.9-0.2SDS "
               "counteracting; NET-UPLIFT case 0.9D+1.0W is a REQUIRED anchorage case")

# S400-20 Table A3.2-1 expected-strength factors (sheet/strip; Fy < 37 ksi and Fy >= 50 ksi
# bands -- cfs_systems.ry_rt covers every band)
RY_BY_FY = {33: 1.5, 50: 1.1}
RT_BY_FY = {33: 1.2, 50: 1.1}

# S400 systems that get NUMERIC capacity-design seeds (chords/hold-downs/anchorage/collectors
# designed for the expected strength of the selected mechanism, need not exceed the
# Omega_0-level force -- S400 B3.4). Gypsum/fiberboard walls (R = 2, S400 E6.4.1.2) are IN:
# the A1.2.3 waiver is only for R = 3 in SDC B/C (cfs_systems.capacity_design_required
# decides per building; this tuple lists the wall systems the numeric seed machinery covers).
CD_WALL_SYSTEMS = ("strap_braced", "wsp_shearwall", "steelsheet_wall", "gypsum_wall")


def _sdc(cfg, dirn=None):
    s = _seis(cfg, dirn)
    return CS.sdc(s.get("SDS", 0.0), s.get("SD1", 0.0), s.get("S1", 0.0),
                  cfg.get("risk_cat", "II"))


def _seis(cfg, dirn=None):
    """Seismic parameters of a direction (12.2.2 / 12.2.3.3 via cfs_engine.direction_seismic);
    building-wide cfg['seis'] when dirn is None."""
    if dirn is None:
        return cfg["seis"]
    return CE.direction_seismic(cfg, dirn)["seis"]


def _system(cfg, dirn=None):
    if dirn is None:
        return cfg.get("system", "wsp_shearwall")
    return CE.direction_seismic(cfg, dirn)["system"]


def _rho_seismic(cfg, dirn=None):
    """Numeric redundancy factor rho (ASCE 7-22 12.3.4): 1.3 by default in SDC D/E/F
    (canonical cfs_systems.sdc from SDS/SD1/S1 + risk category), 1.0 in SDC B/C.
    cfg['rho'] overrides (e.g. the 12.3.4.2 conditions are met -> 1.0); cfg['rho_by_dir']
    = {'X':..,'Y':..} overrides per direction (12.3.4: rho is determined per direction).
    rho multiplies STRENGTH-design E only -- never drift (12.3.4.1 item 2), diaphragm
    Fpx (item 7), or Omega_0/expected-strength capacity-design quantities (item 5)."""
    if dirn is not None and (cfg.get("rho_by_dir") or {}).get(dirn) is not None:
        return float(cfg["rho_by_dir"][dirn])
    if cfg.get("rho") is not None:
        return float(cfg["rho"])
    return 1.3 if _sdc(cfg, dirn) in ("D", "E", "F") else 1.0


def _om0_of(cfg, dirn=None):
    """Omega_0 from the direction's seis (cfg['seis'] by default), else the declared system's
    Table 12.2-1 value. NO silent fallback -- there is no generic CFS Omega_0 default."""
    s = _seis(cfg, dirn)
    if s.get("Om0") is not None:
        return float(s["Om0"])
    sysname = _system(cfg, dirn)
    if sysname in CS.SYSTEMS:
        return float(CS.SYSTEMS[sysname]["Om0"])
    raise KeyError("cfg['seis']['Om0'] missing and cfg['system']=%r is not in the CFS "
                   "system table -- declare Omega_0 from ASCE 7-22 Table 12.2-1 "
                   "(no default exists)" % (sysname,))


_CONCRETE_WORDS = ("concrete", "gyp-crete", "gypcrete", "lightweight concrete", "topping",
                   "filled deck", "composite deck", "slab")


def diaphragm_idealization(cfg, res=None):
    """ASCE 7-22 12.3.1 for the wall path. Returns dict(declared, flexible_ok, note, warnings).
    'flexible' may be USED (tributary distribution AND the Table 12.2-1 footnote-b Omega_0
    reduction) only where 12.3.1.1 or 12.3.1.3 supports it:
      12.3.1.1 -- untopped steel deck / wood structural panels; for light-frame structures
                 (3)(a) no concrete or similar topping > 1.5 in. and (3)(b) every line
                 complies with the Table 12.12-1 drift limit;
      12.3.1.3 -- calculated: cfg['diaphragm_MDD_ADVE'] (delta_MDD / Delta_ADVE) > 2.
    cfg keys: diaphragm ('flexible' | 'semi-rigid' | 'rigid'), diaphragm_material (e.g.
    'wsp', 'steel deck', 'concrete'), diaphragm_topping_in (structural/nonstructural topping
    thickness). A concrete slab or topping > 1.5 in. declared 'flexible' WITHOUT the 12.3.1.3
    ratio is NOT accepted: the Omega_0 reduction is withheld and a warning raised."""
    decl = str(cfg.get("diaphragm", "flexible")).lower()
    mat = str(cfg.get("diaphragm_material", "") or "").lower()
    top = cfg.get("diaphragm_topping_in")
    top = float(top) if isinstance(top, (int, float)) else 0.0
    warns = []
    ratio = cfg.get("diaphragm_MDD_ADVE")
    calc_ok = isinstance(ratio, (int, float)) and float(ratio) > 2.0
    concrete = top > 1.5 or any(w in mat for w in _CONCRETE_WORDS if w != "topping")
    if decl != "flexible":
        return dict(declared=decl, flexible_ok=False, warnings=warns,
                    note="diaphragm declared %r -- no flexible idealization; Table 12.2-1 "
                         "footnote b Omega_0 reduction not applicable" % decl)
    if concrete and not calc_ok:
        warns.append("diaphragm declared FLEXIBLE but %s -- ASCE 7-22 12.3.1.1(3)(a) excludes "
                     "concrete/similar topping > 1.5 in. (12.3.1.2: concrete slabs may be "
                     "RIGID). Model semi-rigid (12.3.1) or document 12.3.1.3 "
                     "(cfg['diaphragm_MDD_ADVE'] > 2); the footnote-b Omega_0 reduction is "
                     "WITHHELD" % ("topping %.2f in." % top if top > 1.5 else
                                   "material %r" % mat))
        return dict(declared=decl, flexible_ok=False, warnings=warns,
                    note="flexible idealization NOT supported by 12.3.1.1 (concrete topping)")
    if calc_ok:
        return dict(declared=decl, flexible_ok=True, warnings=warns,
                    note="flexible per 12.3.1.3 (delta_MDD/Delta_ADVE = %.2f > 2)" % float(ratio))
    drift_bad = []
    for d, dd in ((res or {}).get("directions") or {}).items():
        if dd.get("drift_flags"):
            drift_bad.append(d)
    if drift_bad:
        warns.append("12.3.1.1(3)(b): lines in %s exceed the Table 12.12-1 drift limit -- the "
                     "light-frame FLEXIBLE idealization is not supported until every line "
                     "complies; footnote-b Omega_0 reduction WITHHELD" % "/".join(drift_bad))
        return dict(declared=decl, flexible_ok=False, warnings=warns,
                    note="flexible idealization pending drift compliance (12.3.1.1(3)(b))")
    return dict(declared=decl, flexible_ok=True, warnings=warns,
                note="flexible per 12.3.1.1 (%s; topping %.2f in. <= 1.5 in.; lines meet "
                     "Table 12.12-1)" % (mat or "untopped WSP/steel deck assumed -- declare "
                                         "cfg['diaphragm_material']", top))


def _om0_eff(cfg, dirn=None, res=None):
    """(Om0_eff, Om0, basis note). Table 12.2-1 footnote b: where Om0 >= 2.5, Om0 is
    permitted to be reduced by 0.5 for structures with FLEXIBLE diaphragms -- only where the
    flexible idealization is supported by 12.3.1 (diaphragm_idealization), never under a
    concrete topping / rigid / semi-rigid diaphragm."""
    Om0 = _om0_of(cfg, dirn)
    di = diaphragm_idealization(cfg, res)
    if di["flexible_ok"] and Om0 >= 2.5:
        return Om0 - 0.5, Om0, ("Om0_eff = Om0 - 0.5 = %.2f (Table 12.2-1 footnote b: "
                                "flexible diaphragm, Om0 >= 2.5; %s)" % (Om0 - 0.5, di["note"]))
    return Om0, Om0, ("Om0_eff = Om0 = %.2f (footnote b reduction not applicable: %s)"
                      % (Om0, di["note"] if Om0 >= 2.5 else "Om0 < 2.5"))


def _level_D(cfg, k):
    """Dead load (psf) of level k for gravity seeds: D_roof at a roof, D_floor at floors
    (a platform/mezzanine top level is a FLOOR -- cfs_engine.top_is_floor)."""
    roof = (k == cfg["stories"]) and not CE.top_is_floor(cfg)
    return cfg["D_roof"] if roof else cfg["D_floor"]


def _chord_dead_relief(cfg, line, dirn, w_bay_ft):
    """Dead-load relief AVAILABLE at a chord/hold-down (kip) -- NOT taken in the T_cd seed:
    (0.9 - 0.2*SDS) * D_chord_trib, with the EXPLICIT chord tributary = half-bay
    (w_bay/2 along the wall) x half joist span, D summed over the stories above.
    Joist span = cfg['joist_span_ft'] where declared, else the largest spacing to an
    adjacent lateral line in the same direction group (documented surrogate).
    Returns (kip or None, note)."""
    SDS = _seis(cfg, dirn)["SDS"]
    span = cfg.get("joist_span_ft")
    how = "cfg['joist_span_ft']"
    if span is None:
        lines = cfg["lines_x"] if dirn == "X" else cfg["lines_y"]
        pos = sorted(set(round(float(l.pos), 6) for l in lines))
        p = round(float(line.pos), 6)
        if p in pos:
            i = pos.index(p)
            gaps = ([pos[i] - pos[i - 1]] if i > 0 else []) + \
                   ([pos[i + 1] - pos[i]] if i < len(pos) - 1 else [])
            if gaps:
                span = max(gaps)
                how = "largest adjacent lateral-line spacing (surrogate)"
    if span is None or not w_bay_ft:
        return None, ("chord tributary not derivable from cfg geometry (no joist_span_ft, "
                      "no adjacent line) -- agent computes (0.9-0.2SDS)*D over half-bay x "
                      "half joist span and documents it before taking any relief")
    N = cfg["stories"]
    D_trib = 0.0
    for k in range(1, N + 1):
        D_trib += _level_D(cfg, k) * (w_bay_ft / 2.0) * (span / 2.0) / 1000.0
    return round((0.9 - 0.2 * SDS) * D_trib, 2), \
        ("(0.9-0.2*SDS)*D_chord_trib; chord trib = half-bay (%.1f ft) x half joist span "
         "(%.1f ft, %s), D summed over %d stories" % (w_bay_ft / 2.0, span / 2.0, how, N))


# ---------------- live-load companion factor (ASCE 7-22 2.3.1 Exc. 1 / 2.3.6 Exc. 1) ----------

def live_companion_factor(cfg):
    """(factor, basis) for the companion L in 2.3.1 combos 3a/4a and 2.3.6 combo 6 (incl. the
    Emh form): 0.5 is PERMITTED only where Lo <= 100 psf (Table 4.3-1) and the area is not a
    garage or a place of public assembly; otherwise 1.0. Lo = the largest floor live load
    (L_floor / L_by_level). Garage/assembly: cfg['garage'] / cfg['assembly'] True or
    cfg['occupancy'] containing 'garage', 'parking' or 'assembly'."""
    N = int(cfg["stories"])
    Lo = max([CE.level_live_psf(cfg, k) for k in range(1, N + 1)] +
             [float(cfg.get("L_floor", 0.0) or 0.0)])
    occ = str(cfg.get("occupancy", "") or "").lower()
    garage = bool(cfg.get("garage")) or "garage" in occ or "parking" in occ
    assembly = bool(cfg.get("assembly")) or "assembly" in occ
    if Lo > 100.0 or garage or assembly:
        why = []
        if Lo > 100.0:
            why.append("Lo = %.0f psf > 100 psf%s" % (Lo, " (storage)" if
                                                       (cfg.get("storage") or
                                                        cfg.get("storage_levels")) else ""))
        if garage:
            why.append("garage")
        if assembly:
            why.append("public assembly")
        return 1.0, ("L factor 1.0 in 2.3.1 combos 3a/4a and 2.3.6 combo 6: the 0.5 reduction "
                     "(Exception 1) is NOT permitted -- %s" % ", ".join(why))
    return 0.5, ("L factor 0.5 in 2.3.1 combos 3a/4a and 2.3.6 combo 6 (Exception 1: Lo = "
                 "%.0f psf <= 100 psf, not garage/assembly)" % Lo)


# ---------------- Stage 3b: wall-path wind machinery + LRFD combo enumeration ----------------

# ASCE 7-22 Table 26.11-1 gust constants (customary units)
_GUST = dict(B=dict(c=0.30, l=320.0, eps=1.0 / 3.0, zmin=30.0, bbar=0.47, abar=1.0 / 4.5),
             C=dict(c=0.20, l=500.0, eps=1.0 / 5.0, zmin=15.0, bbar=0.66, abar=1.0 / 6.4),
             D=dict(c=0.15, l=650.0, eps=1.0 / 8.0, zmin=7.0, bbar=0.78, abar=1.0 / 8.0))


def _rfun(eta):
    if eta <= 0:
        return 1.0
    return 1.0 / eta - (1.0 - math.exp(-2.0 * eta)) / (2.0 * eta * eta)


def gust_factor(V_mph, exposure, h_ft, B_ft, L_ft, n1_hz=None, beta=0.02):
    """Gust-effect factor per ASCE 7-22 26.11. Rigid (n1 >= 1 Hz or not flexible): G = 0.85
    (26.11.1). Flexible (n1 < 1 Hz): Gf by Eq. 26.11-10..17 with the Table 26.11-1 constants.
    Returns (G, info dict)."""
    if n1_hz is None or n1_hz >= 1.0:
        return 0.85, dict(rigid=True, n1_hz=n1_hz, note="rigid: G = 0.85 (26.11.1)")
    g = _GUST[exposure]
    zbar = max(0.6 * h_ft, g["zmin"])
    Iz = g["c"] * (33.0 / zbar) ** (1.0 / 6.0)
    Lz = g["l"] * (zbar / 33.0) ** g["eps"]
    Q = math.sqrt(1.0 / (1.0 + 0.63 * ((B_ft + h_ft) / Lz) ** 0.63))
    Vz = g["bbar"] * (zbar / 33.0) ** g["abar"] * (88.0 / 60.0) * V_mph
    N1 = n1_hz * Lz / Vz
    Rn = 7.47 * N1 / (1.0 + 10.3 * N1) ** (5.0 / 3.0)
    Rh = _rfun(4.6 * n1_hz * h_ft / Vz)
    RB = _rfun(4.6 * n1_hz * B_ft / Vz)
    RL = _rfun(15.4 * n1_hz * L_ft / Vz)
    R = math.sqrt(1.0 / beta * Rn * Rh * RB * (0.53 + 0.47 * RL))
    lg = math.sqrt(2.0 * math.log(3600.0 * n1_hz))
    gR = lg + 0.577 / lg
    gQ = gv = 3.4
    Gf = 0.925 * (1.0 + 1.7 * Iz * math.sqrt(gQ ** 2 * Q ** 2 + gR ** 2 * R ** 2)) / \
        (1.0 + 1.7 * gv * Iz)
    return Gf, dict(rigid=False, n1_hz=n1_hz, beta=beta, zbar=zbar, Iz=Iz, Lz=Lz, Q=Q, R=R,
                    gR=gR, Vz_fps=Vz, note="flexible (n1 = %.3f Hz < 1): Gf = %.3f "
                                           "(26.11.5, beta = %.3f)" % (n1_hz, Gf, beta))


def cp_leeward(L_over_B):
    """ASCE 7-22 Fig. 27.3-1 leeward wall Cp: L/B 0-1 -> -0.5, 2 -> -0.3, >= 4 -> -0.2
    (linear interpolation)."""
    r = float(L_over_B)
    if r <= 1.0:
        return -0.5
    if r <= 2.0:
        return -0.5 + 0.2 * (r - 1.0)
    if r <= 4.0:
        return -0.3 + 0.1 * (r - 2.0) / 2.0
    return -0.2


def _wind_z0(cfg):
    """Height of the MODEL base above grade (ft): cfg['wind']['z_base_ft'], else the podium
    height of a two-stage model (cfg['two_stage']['podium_height_ft']), else
    cfg['base_elevation_ft'], else 0. Wind heights (Kz, qh, h) are measured from GRADE."""
    w = cfg.get("wind") or {}
    if w.get("z_base_ft") is not None:
        return float(w["z_base_ft"])
    ts = cfg.get("two_stage") or {}
    if ts.get("podium_height_ft") is not None:
        return float(ts["podium_height_ft"])
    return float(cfg.get("base_elevation_ft", 0.0) or 0.0)


def _wind_n1(cfg, h_ft):
    """(n1 in Hz or None for rigid, basis). Explicit cfg['wind']['n1_hz'] wins; low-rise
    buildings (h <= 60 ft and h <= least plan dimension, 26.2) are permitted rigid (26.11.2);
    else 1/T_analytical; else the LOWER-BOUND estimate 1/(Cu*Ta) -- stated, agent confirms."""
    w = cfg.get("wind") or {}
    if w.get("n1_hz") is not None:
        return float(w["n1_hz"]), "n1 = %.3f Hz (cfg['wind']['n1_hz'])" % float(w["n1_hz"])
    if h_ft <= 60.0 and h_ft <= min(cfg["plan_ft"]):
        return None, "low-rise (h = %.1f ft): rigid permitted (26.11.2)" % h_ft
    if cfg.get("T_analytical"):
        T = float(cfg["T_analytical"])
        return 1.0 / T, "n1 = 1/T_analytical = %.3f Hz" % (1.0 / T)
    s = cfg["seis"]
    Ta = s["Ct"] * sum(cfg["heights_ft"]) ** s["x"]
    T = s.get("Cu", 1.4) * Ta
    return 1.0 / T, ("n1 ESTIMATED = 1/(Cu*Ta) = %.3f Hz (no analysis; supply "
                     "cfg['wind']['n1_hz'] -- 26.11.2)" % (1.0 / T))


def wind_story_forces(cfg, direction, detail=False):
    """SEEDED MWFRS story forces (kip) on the box building for one direction -- ASCE 7-22
    Ch. 27 directional procedure, Case 1 of Fig. 27.3-8 (see wind_line_screen for Case 2):
      windward  qz*G*Cp_ww (Cp_ww = 0.8) stepped per level, z measured FROM GRADE (model base
                + cfg['wind']['z_base_ft'] / two-stage podium height);
      leeward   qh*G*|Cp_lw(L/B)| (Fig. 27.3-1, h = mean roof height from grade) -- uniform;
      G         26.11: 0.85 rigid, Gf (26.11.5) where n1 < 1 Hz (see _wind_n1);
      minimum   27.1.5: >= 16 psf on the projected wall area (8 psf on roof projection);
      optional  cfg['wind']: Cnet (legacy net-coefficient override -> Cp_lw = -(Cnet-0.8)),
                Cp_ww, Cp_lw, parapet_ft (27.3.4, GCpn +1.5/-1.0 at qp), roof_projection_ft
                (vertical projection of a sloped roof above the top level, loaded at qh),
                n1_hz, damping (beta, default 0.02), Kzt, Ke, Kd (0.85).
    qz = 0.00256*Kz*Kzt*Ke*V^2 (Eq. 26.10-1) with Kd at the pressure equation (27.3-1).
    Returns (forces {story: kip}, basis dict); detail=True adds per-story pressures."""
    w = cfg.get("wind")
    if not w:
        return None, None
    import cfs_frame as CF
    exp = w.get("exposure", "C")
    B = cfg["plan_ft"][1] if direction == "X" else cfg["plan_ft"][0]   # face width normal to wind
    Ld = cfg["plan_ft"][0] if direction == "X" else cfg["plan_ft"][1]  # depth along wind
    Kzt, Ke, Kd = w.get("Kzt", 1.0), w.get("Ke", 1.0), w.get("Kd", 0.85)
    N = cfg["stories"]
    z0 = _wind_z0(cfg)
    H = cfg["heights_ft"]
    roof_proj = float(w.get("roof_projection_ft", 0.0) or 0.0)
    h_mean = z0 + sum(H) + 0.5 * roof_proj
    n1, n1_note = _wind_n1(cfg, h_mean)
    G, ginfo = gust_factor(w["V"], exp, h_mean, B, Ld, n1, float(w.get("damping", 0.02)))
    Cp_ww = float(w.get("Cp_ww", 0.8))
    if w.get("Cp_lw") is not None:
        Cp_lw, lw_note = float(w["Cp_lw"]), "cfg['wind']['Cp_lw']"
    elif w.get("Cnet") is not None:
        Cp_lw = -(float(w["Cnet"]) - Cp_ww)
        lw_note = "from cfg['wind']['Cnet'] = %.2f (Cp_lw = -(Cnet - Cp_ww))" % float(w["Cnet"])
    else:
        Cp_lw, lw_note = cp_leeward(Ld / B), "Fig. 27.3-1 at L/B = %.2f" % (Ld / B)

    def q(z):
        return 0.00256 * CF._kzf(z, exp) * Kzt * Ke * Kd * w["V"] ** 2

    qh = q(h_mean)
    forces, rows = {}, {}
    z = z0
    min_gov = []
    for k in range(1, N + 1):
        h = H[k - 1]
        trib = h / 2.0 + (H[k] / 2.0 if k < N else 0.0)
        z += h
        qz = q(z)
        p = G * (qz * Cp_ww + qh * abs(Cp_lw))                        # psf, net windward+leeward
        area = B * trib
        F = p * area / 1000.0
        extra = 0.0
        if k == N and roof_proj > 0:
            p_r = G * qh * (Cp_ww + abs(Cp_lw))
            extra += max(p_r, 8.0) * B * roof_proj / 1000.0          # 27.1.5: 8 psf roof proj
        if k == N and w.get("parapet_ft"):
            hp = float(w["parapet_ft"])
            qp = q(z + roof_proj + hp)
            extra += qp * (1.5 + 1.0) * B * hp / 1000.0               # 27.3.4 GCpn +1.5 / -1.0
        Fmin = 16.0 * area / 1000.0                                   # 27.1.5: 16 psf on walls
        if Fmin > F:
            min_gov.append(k)
            F = Fmin
        forces[k] = F + extra
        rows[k] = dict(z_ft=round(z, 2), qz_psf=round(qz, 2), p_net_psf=round(p, 2),
                       trib_ft=round(trib, 3), F_kip=round(forces[k], 3))
    basis = dict(V_mph=w["V"], exposure=exp, z_base_ft=z0, h_mean_ft=round(h_mean, 2),
                 qh_psf=round(qh, 2), G=round(G, 4), gust=ginfo["note"], n1_basis=n1_note,
                 Cp_ww=Cp_ww, Cp_lw=round(Cp_lw, 3), Cp_lw_basis=lw_note,
                 min_16psf_governs_stories=min_gov,
                 note="SEED (ASCE 7-22 Ch. 27 directional): windward qz*G*%.2f stepped per "
                      "level with z from grade (base +%.1f ft), leeward qh*G*%.2f at h = %.1f "
                      "ft, %s; 27.1.5 16-psf wall minimum%s; Kd=%.2f at Eq. 27.3-1. Case 2 "
                      "(Fig. 27.3-8, 0.75 x Case 1 with e = 0.15B) is enveloped per line in "
                      "wind_line_screen; agent verifies per ASCE 7 Ch. 27"
                      % (Cp_ww, z0, abs(Cp_lw), h_mean, ginfo["note"],
                         " (governs stories %s)" % min_gov if min_gov else "", Kd))
    if detail:
        basis["stories"] = rows
    return forces, basis


def _wind_line_tribs(lines, B, overrides=None):
    """Face-width tributary intervals for wind on a flexible diaphragm (ASCE 7-22 27.3.1 /
    Fig. 27.3-8: wind acts on the projected face; a flexible diaphragm delivers it by face
    width). Independent of the SEISMIC trib_scale (mass tributaries) and with NO 5% shift.
    Returns {position: (lo, hi, [lines])}; overrides = {line_name: width_ft} rescales the
    interval width of single-line positions (irregular plans -- state it)."""
    groups = {}
    for ln in sorted(lines, key=lambda l: l.pos):
        groups.setdefault(round(float(ln.pos), 6), []).append(ln)
    order = sorted(groups)
    out = {}
    for i, x in enumerate(order):
        lo = 0.0 if i == 0 else (order[i - 1] + x) / 2.0
        hi = B if i == len(order) - 1 else (x + order[i + 1]) / 2.0
        out[x] = (lo, hi, groups[x])
    return out


def _block_load(lo, hi, B, case, sign):
    """Integral over [lo, hi] of the Fig. 27.3-8 face load per unit Case-1 resultant/B:
    Case 1 -> uniform 1.0; Case 2 -> 0.75 with the torsional moment 0.75*P*0.15B applied as
    the Note-4 'equivalent distributed pressure block' on a flexible diaphragm: 0.75 +/- 0.45
    on the two halves of the face (couple 0.45*(B/2)*(B/2) = 0.75*0.15*B^2)."""
    lo, hi = max(lo, 0.0), min(hi, B)
    if hi <= lo:
        return 0.0
    if case == 1:
        return (hi - lo) / B
    m = B / 2.0
    a = max(0.0, min(hi, m) - lo)              # part on the first half
    b = max(0.0, hi - max(lo, m))              # part on the second half
    s = 1.0 if sign == "+" else -1.0
    return ((0.75 + 0.45 * s) * a + (0.75 - 0.45 * s) * b) / B


def wind_line_screen(cfg, res=None):
    """Per-direction wind distribution with WIND tributaries (face width, no seismic
    trib_scale, no 5% shift) and the Fig. 27.3-8 Case 1 + Case 2 (+/- e) envelope.
    Returns {dirn: dict(lines={line: dict(v_plf, T_base_kip, case_by_story)}, basis)} or None."""
    if not cfg.get("wind"):
        return None
    out = {}
    ovr = (cfg.get("wind") or {}).get("trib_width_ft") or {}
    for dirn, lines, B in (("X", cfg["lines_x"], cfg["plan_ft"][1]),
                           ("Y", cfg["lines_y"], cfg["plan_ft"][0])):
        wf, basis = wind_story_forces(cfg, dirn)
        tribs = _wind_line_tribs(lines, B)
        cases = (("1", 1, "+"), ("2+", 2, "+"), ("2-", 2, "-"))
        Fline = {c[0]: {k: {} for k in wf} for c in cases}
        scaled = []
        for x, (lo, hi, grp) in tribs.items():
            for cname, case, sgn in cases:
                frac = _block_load(lo, hi, B, case, sgn)
                if len(grp) == 1 and grp[0].name in ovr:
                    nat = (hi - lo) / B
                    frac = frac / nat * float(ovr[grp[0].name]) / B if nat > 0 else frac
                for k, F in wf.items():
                    Ls = [max(ln.length(k), 0.0) for ln in grp]
                    tot = sum(Ls)
                    for ln, Lw in zip(grp, Ls):
                        fr = (Lw / tot) if tot > 0 else 1.0 / len(grp)
                        Fline[cname][k][ln.name] = F * frac * fr
        if ovr:
            # renormalize Case 1 so the overridden widths still carry the whole story force
            for cname in Fline:
                for k, F in wf.items():
                    tot = sum(Fline[cname][k].values())
                    tgt = F * (1.0 if cname == "1" else 0.75)
                    if tot > 0:
                        for nm in Fline[cname][k]:
                            Fline[cname][k][nm] *= tgt / tot
        for ln in lines:
            if abs(getattr(ln, "trib_scale", 1.0) - 1.0) > 1e-9:
                scaled.append(ln.name)
        d = {}
        hts = {k: cfg["heights_ft"][k - 1] for k in wf}
        for ln in lines:
            v_env, case_by, T_env = {}, {}, 0.0
            for cname, _c, _s in cases:
                Vc = 0.0
                for k in sorted(wf, reverse=True):
                    Vc += Fline[cname][k][ln.name]
                    L = ln.length(k)
                    v = (Vc * 1000.0 / L) if L > 0 else None
                    if v is not None and (k not in v_env or v > v_env[k]):
                        v_env[k], case_by[k] = round(v, 0), cname
                distl = {k: {ln.name: dict(V_shifted=Fline[cname][k][ln.name])} for k in wf}
                ot = WL.overturning_stack(ln, distl, hts)
                T_env = max(T_env, ot[min(ot)]["T_kip"])
            d[ln.name] = dict(v_plf=v_env, T_base_kip=round(T_env, 1), case_by_story=case_by)
        basis = dict(basis, distribution=(
            "WIND tributaries by projected FACE WIDTH (27.3.1 / Fig. 27.3-8), independent of "
            "the seismic trib_scale and with no accidental 5%% shift; envelope of Case 1 and "
            "Case 2 (0.75 x Case 1, e = +/-0.15B as the Note-4 pressure block 1.20p/0.30p on "
            "the face halves) -- Cases 3/4 (0.75/0.563 on both axes) do not govern a "
            "single-direction line on a flexible diaphragm%s"
            % ("; trib_width_ft overrides: %s" % sorted(ovr) if ovr else "")),
            uplift_note="T_base = envelope overturning tension, NO dead-load relief and NO "
                        "roof uplift (conservative seed for the 0.9D+1.0W case without "
                        "relief; add roof uplift per S240 B5.2.4.2.1 where the roof is "
                        "anchored through the wall chords)")
        if scaled:
            basis["trib_scale_note"] = ("lines %s carry a SEISMIC trib_scale != 1 -- wind uses "
                                        "face-width tributaries instead (CFS-10); give "
                                        "cfg['wind']['trib_width_ft'] where the face width "
                                        "differs from the line spacing" % scaled)
        out[dirn] = dict(lines=d, basis=basis)
    return out


def _wind_line_screen(cfg, res):
    """Backward-compatible alias (pre-CFS-10 name)."""
    return wind_line_screen(cfg, res)


def enumerate_combos(cfg):
    """The LRFD combo LIST for the wall path (data, not solves -- the spring model is linear
    per direction, so combos scale/pair the E and W distributions). Seismic carries
    (1.2+0.2SDS)/(0.9-0.2SDS) vertical pairing and rho of the DIRECTION (12.3.4); wind
    carries the 1.0W pair incl. the REQUIRED 0.9D+1.0W net-uplift anchorage case. The
    companion live-load factor is 0.5 only where 2.3.1 / 2.3.6 Exception 1 permits it
    (live_companion_factor), else 1.0; the Emh (overstrength) combo 6 carries the same L and
    0.15S. Omega_0 per direction (12.2.2)."""
    fL, _Lb = live_companion_factor(cfg)
    Ls = "%.1fL" % fL
    # 7-22 2.3.1: S principal carries 1.0 (was 1.6); companion S is 0.3 (was 0.5).
    # Lr KEEPS 1.6 principal / 0.5 companion -- the 7-22 factor change is snow-only.
    out = [dict(label="1.4D", D=1.4),
           dict(label="1.2D+1.6L+(0.5Lr or 0.3S)", D=1.2, L=1.6, Lr=0.5, S=0.3),
           dict(label="1.2D+(1.6Lr or 1.0S)+%s" % Ls, D=1.2, L=fL, Lr=1.6, S=1.0)]
    for dirn in ("X", "Y"):
        SDS = _seis(cfg, dirn)["SDS"]
        rho = _rho_seismic(cfg, dirn)    # 12.3.4 via canonical SDC; cfg['rho'] overrides
        for sgn in ("+", "-"):
            # 7-22 2.3.6 combo 6: snow companion with seismic is 0.15S (was 0.2S)
            out.append(dict(label="(1.2+0.2SDS)D+rho*E%s%s+%s+0.15S" % (dirn, sgn, Ls),
                            D=1.2 + 0.2 * SDS, L=fL, S=0.15, E=rho, dir=dirn, sign=sgn))
            out.append(dict(label="(0.9-0.2SDS)D+rho*E%s%s" % (dirn, sgn),
                            D=0.9 - 0.2 * SDS, E=rho, dir=dirn, sign=sgn,
                            role="uplift/counteracting"))
    if cfg.get("wind"):
        for dirn in ("X", "Y"):
            for sgn in ("+", "-"):
                out.append(dict(label="1.2D+1.0W%s%s+%s+(0.5Lr or 0.3S)" % (dirn, sgn, Ls),
                                D=1.2, L=fL, Lr=0.5, S=0.3, W=1.0, dir=dirn, sign=sgn))
                out.append(dict(label="0.9D+1.0W%s%s" % (dirn, sgn), D=0.9, W=1.0,
                                dir=dirn, sign=sgn, role="NET UPLIFT anchorage case"))
    # 2.3.6 overstrength pair (combos 6 & 7 with Emh): BOTH the additive and the
    # counteracting case are REQUIRED for collectors/transfer/anchorage (rho NOT applied
    # to Omega_0-level E, 12.3.4.1 item 5). One pair per direction when Omega_0 differs.
    om = {d: _om0_of(cfg, d) for d in ("X", "Y")}
    dirs = [None] if om["X"] == om["Y"] else ["X", "Y"]
    for d in dirs:
        Om0 = om[d or "X"]                 # NO 2.5 fallback -- table value or explicit error
        SDS = _seis(cfg, d or "X")["SDS"]
        tag = "" if d is None else " %s" % d
        out.append(dict(label="overstrength%s: (1.2+0.2SDS)D+Om0*E+%s+0.15S (2.3.6 combo 6; "
                              "12.10.2.1)" % (tag, Ls),
                        D=1.2 + 0.2 * SDS, L=fL, S=0.15, E=Om0,
                        role="collectors/transfer/anchorage uplift", **({"dir": d} if d else {})))
        out.append(dict(label="overstrength counteracting%s: (0.9-0.2SDS)D+Om0*E (2.3.6 "
                              "combo 7)" % tag,
                        D=0.9 - 0.2 * SDS, E=Om0, role="collectors/transfer/anchorage uplift",
                        **({"dir": d} if d else {})))
    return out


def stud_axial_stack(cfg, trib_ft, spacing_in=16.0):
    """Cumulative factored axial (kip) per stud by story for a bearing wall with tributary
    trib_ft of floor each side sum. 1.2D+1.6L seed (agent refines with live reduction; storage
    live is NOT reducible, ASCE 7-22 4.7.3). A platform/mezzanine top level is a FLOOR and
    keeps its live load (cfs_engine.top_is_floor); a roof carries D only here (the agent adds
    Lr/S per 2.3.1 combo 3a)."""
    N = cfg["stories"]
    out = {}
    P = 0.0
    for k in range(N, 0, -1):
        roof = (k == N) and not CE.top_is_floor(cfg)
        D = _level_D(cfg, k)
        L = 0.0 if roof else CE.level_live_psf(cfg, k) if (
            cfg.get("L_floor") is not None or cfg.get("L_by_level")) else 40.0
        w = (1.2 * D + 1.6 * L) / 1000.0 * trib_ft * (spacing_in / 12.0)
        P += w
        out[k] = round(P, 2)
    return out


# ---------------- capacity-design / Type II / collector / gravity seed helpers ----------------

def _line_key(dirn, line):
    return "%s:%s" % (dirn, line.name)


def type_ii_spec(cfg, dirn, line):
    """Type II (perforated) declaration of a line -> dict(Ca={story: Ca}, basis) or None.
    cfg['type_ii'] = {'X:N': dict(Ca=0.86 | {story: Ca}) or dict(pct_full_height=61.2 |
    {story:..}, max_opening_height_ratio=0.47 | {story:..})} (key 'X:N' or the bare line
    name), or a WallLine attribute .type_ii (same dict, or True with .Ca). Ca per AISI
    S400-20 Table E1.3.1.2-1 (cfs_systems.type_ii_Ca). ValueError when declared but Ca
    cannot be determined -- no silent 1.0."""
    tt = cfg.get("type_ii") or {}
    spec = tt.get(_line_key(dirn, line)) or tt.get(line.name)
    if spec is None:
        spec = getattr(line, "type_ii", None)
        if spec is True:
            spec = {"Ca": getattr(line, "Ca", None)}
    if spec is None and getattr(line, "Ca", None) is not None:
        spec = {"Ca": line.Ca}
    if not spec:
        return None

    def at(v, k):
        if isinstance(v, dict):
            return v.get(k, v.get(str(k)))
        return v
    Ca, how = {}, None
    for k in sorted(line.segments):
        if spec.get("Ca") is not None:
            v, how = at(spec["Ca"], k), "declared Ca (S400 Table E1.3.1.2-1)"
        elif spec.get("pct_full_height") is not None and \
                spec.get("max_opening_height_ratio") is not None:
            v = CS.type_ii_Ca(at(spec["pct_full_height"], k),
                              at(spec["max_opening_height_ratio"], k))
            how = "S400 Table E1.3.1.2-1 (interpolated) from % full-height sheathing and " \
                  "max opening height ratio"
        else:
            v = None
        if v is None:
            raise ValueError("Type II line %s story %d: give Ca or pct_full_height + "
                             "max_opening_height_ratio (S400 E1.3.1.2)" % (_line_key(dirn, line), k))
        Ca[k] = float(v)
    return dict(Ca=Ca, basis=how)


def selected_Vn(cfg, dirn, line, sysname):
    """Nominal shear strength (kip) of the SELECTED assembly per story of one line, for the
    Omega_E*Vn capacity-design stack, or (None, reason):
      cfg['selected_Vn_kip'] = {'X:A': {story: Vn_line_kip}} (any system; Type II already
                               including Ca), or
      strap walls: cfg['strap_Ag_in2'] (tension-strap gross area per braced bay, all faces;
                   scalar, {story: Ag} or {line: {story: Ag}}) + cfg['strap_Fy_ksi'] ->
                   Vn = Ag*Fy*w/sqrt(h^2+w^2) per bay (S400 Eq. E3.3.1-1), summed over bays."""
    sel = (cfg.get("selected_Vn_kip") or {})
    sel = sel.get(_line_key(dirn, line)) or sel.get(line.name)
    if sel:
        return {int(k): float(v) for k, v in sel.items()}, "cfg['selected_Vn_kip']"
    if sysname == "strap_braced" and cfg.get("strap_Ag_in2") is not None and \
            cfg.get("strap_Fy_ksi") is not None:
        Ag = cfg["strap_Ag_in2"]
        if isinstance(Ag, dict) and (line.name in Ag or _line_key(dirn, line) in Ag):
            Ag = Ag.get(_line_key(dirn, line), Ag.get(line.name))
        Fy = float(cfg["strap_Fy_ksi"])
        out = {}
        for k, segs in line.segments.items():
            a = Ag.get(k, Ag.get(str(k))) if isinstance(Ag, dict) else Ag
            if a is None:
                return None, "strap_Ag_in2 has no entry for story %d" % k
            out[k] = sum(float(a) * Fy * w / math.hypot(h, w) for (w, h) in segs if w > 0)
        return out, ("S400 Eq. E3.3.1-1: Vn = Ag*Fy*w/sqrt(h^2+w^2) per bay (Ag = %s, Fy = %g "
                     "ksi)" % ("cfg['strap_Ag_in2']", Fy))
    return None, ("selected assembly not declared (cfg['selected_Vn_kip'] or, for straps, "
                  "cfg['strap_Ag_in2'] + cfg['strap_Fy_ksi']) -- Omega_E*Vn stack NOT "
                  "evaluated; the Omega0-level stack is the seed")


def _capacity_stack(cfg, line, dist, om0, OmE=None, Vn=None, Ca=None):
    """Top-down chord/hold-down tension stack at capacity-design level:
    story shear V_k = min(Omega_0 x V_ELF,k (pure ELF, shifted), Omega_E x Vn_k) [S400 B3.4:
    expected strength, need not exceed the Omega_0-level effect],
    T_k = sum_{j>=k} V_j h_j / Ca_j  /  L_k -- each story's chord increment divided by that
    story's Type II Ca (S400 Eq. E1.4.2.2-2: C = Vh/(Ca*Sum Li)); Ca = 1 for Type I.
    Returns {story: dict(V_cap, T, governs)}."""
    out = {}
    Vrun = M = 0.0
    for k in sorted(dist, reverse=True):
        Vrun += dist[k][line.name]["V_shifted"]
        V_om = om0 * Vrun
        V_cap, gov = V_om, "Omega0"
        if OmE is not None and Vn and Vn.get(k) is not None and OmE * Vn[k] < V_om:
            V_cap, gov = OmE * Vn[k], "Omega_E*Vn"
        ca = (Ca or {}).get(k, 1.0) or 1.0
        M += V_cap * cfg["heights_ft"][k - 1] / ca
        L = max(line.length(k), 1e-6)
        out[k] = dict(V_cap=V_cap, T=max(M / L, 0.0), governs=gov)
    return out


def collector_seeds(cfg, res):
    """NUMERIC collector seeds (ASCE 7-22 12.10) per declared collector line.
    Per direction and level: Fx (ELF), Fpx (12.10-1, bounds 12.10-2/-3), and the design basis
    by SDC:
      SDC C-F  12.10.2.1: max of (a) Om0 x Fx, (b) Om0 x Fpx(12.10-1, capped by 12.10-3),
               (c) Fpx(12.10-2) with 2.3.6 (no Om0); transfer forces included. (The wood
               light-frame exception does not apply to CFS.)
      SDC A/B  12.10.2.1 does not apply; S400 B3.4 capacity design where S400 applies
               (expected strength, need not exceed the Omega0 level = max(a, b)), with the
               12.10.1.1 Fpx (12.10-2 floor) as the minimum; S100/S240 strength with 2.3.6
               and Fpx where S400 is waived (A1.2.3).
    Om0 = the direction's Om0_eff (footnote b only where the flexible idealization holds).
    A collector_lines entry may be a string (whole-diaphragm level values; agent applies the
    collector's tributary fraction + run length) or dict(name, direction, trib_fraction,
    levels, transfer_kip={level: kip}) -> numeric demand_kip_by_level."""
    out = []
    specs = cfg.get("collector_lines", []) or []
    if not specs:
        return out
    tabs = {}
    for d in ("X", "Y"):
        dd = (res.get("directions") or {}).get(d) or {}
        e = dd.get("elf") or (res.get("elf_by_dir") or {}).get(d) or res["elf"]
        cat = _sdc(cfg, d)
        sysn = dd.get("system") or _system(cfg, d)
        cd_req, _cb = CS.capacity_design_required(sysn, _seis(cfg, d)["R"], cat)
        om0e, _om, om_note = _om0_eff(cfg, d, res)
        fp = CE.fpx(cfg, e=e, direction=d)
        rows = {}
        for x, r in fp.items():
            a = om0e * r["Fx"]
            b = om0e * min(r["Fpx_eq1"], r["Fpx_max"])
            c = r["Fpx_min"]
            if cat in ("C", "D", "E", "F"):
                dem, basis = max(a, b, c), "12.10.2.1 max(a,b,c)"
            elif cd_req:
                dem, basis = max(max(a, b), r["Fpx"]), \
                    "S400 B3.4 Omega0-level cap max(a,b) (12.10.2.1 n/a in SDC %s)" % cat
            else:
                dem, basis = r["Fpx"], "12.10.1.1 Fpx with 2.3.6 (S400 waived, SDC %s)" % cat
            rows[x] = dict(Fx_kip=round(r["Fx"], 2), Fpx_kip=round(r["Fpx"], 2),
                           Fpx_eq1_kip=round(r["Fpx_eq1"], 2), Fpx_min_kip=round(c, 2),
                           Fpx_max_kip=round(r["Fpx_max"], 2), a_Om0_Fx_kip=round(a, 2),
                           b_Om0_Fpx_kip=round(b, 2), c_Fpx_min_kip=round(c, 2),
                           design_level_kip=round(dem, 2), basis=basis)
        tabs[d] = dict(SDC=cat, Om0_eff=om0e, Om0_basis=om_note, levels=rows,
                       transfer_factor=(om0e if cat in ("C", "D", "E", "F") else 1.0))
    for sp in specs:
        if isinstance(sp, dict):
            nm = sp.get("name") or sp.get("line") or "collector"
            dirs = [sp["direction"]] if sp.get("direction") in ("X", "Y") else ["X", "Y"]
        else:
            nm, dirs = str(sp), ["X", "Y"]
        ent = dict(id="collector-%s" % nm, line=nm,
                   basis=("SEEDED numeric (ASCE 7-22 12.10.1.1 Fpx / 12.10.2.1): per-level "
                          "whole-diaphragm design forces below; the collector force = its "
                          "share of the level force accumulated along the run + transfer "
                          "forces%s" % ("" if isinstance(sp, dict) and sp.get("trib_fraction")
                                        is not None else " -- AGENT applies the tributary "
                                        "fraction/run length")),
                   by_direction={d: tabs[d] for d in dirs},
                   limit_state=None, cited=None, capacity=None, DC=None)
        if isinstance(sp, dict) and sp.get("trib_fraction") is not None:
            f = float(sp["trib_fraction"])
            tr = sp.get("transfer_kip") or {}
            lev = sp.get("levels")
            dem = {}
            for d in dirs:
                for x, r in tabs[d]["levels"].items():
                    if lev and x not in lev:
                        continue
                    t = float(tr.get(x, tr.get(str(x), 0.0)) or 0.0)
                    v = f * r["design_level_kip"] + t * tabs[d]["transfer_factor"]
                    dem[x] = round(max(dem.get(x, 0.0), v), 2)
            ent["demand_kip_by_level"] = dem
            ent["demand_basis"] = ("trib_fraction %.3f x design-level force + transfer x "
                                   "%s" % (f, "Om0" if any(tabs[d]["transfer_factor"] > 1
                                                          for d in dirs) else "1.0"))
        out.append(ent)
    return out


def gravity_framing_seeds(cfg):
    """Joist / beam / post seed slots for a FLOOR-bearing platform or mezzanine on the wall
    path (top level is a floor -- cfs_engine.top_is_floor), so the gravity framing is a
    tracked deliverable. Simple-span uniform-load seeds: wu = 1.2D + 1.6L (storage live
    NON-reducible, 4.7.3), Mu = wu L^2/8, Vu = wu L/2, deflection limits L/360 (live) and
    L/240 (D+L) per IBC Table 1604.3 floor members, with the required I for L/360.
    cfg keys: joist_span_ft, joist_spacing_in (16), beam_span_ft, beam_trib_ft
    (= joist_span_ft), post_trib_sf (= beam_span x beam_trib)."""
    if not CE.top_is_floor(cfg):
        return []
    N = cfg["stories"]
    D = _level_D(cfg, N)
    L = CE.level_live_psf(cfg, N)
    E = CS.E_KSI
    out = []

    def member(mid, span, trib_ft, what):
        if not span:
            return dict(id=mid, member=what, span_ft=None,
                        note="AGENT: span not declared (cfg) -- design the %s and state span, "
                             "trib, Mu/Vu, web crippling at supports and L/360 deflection"
                             % what, limit_state=None, cited=None, capacity=None, DC=None)
        wD, wL = D * trib_ft / 1000.0, L * trib_ft / 1000.0            # klf
        wu = 1.2 * wD + 1.6 * wL
        dl_L = span * 12.0 / 360.0
        dl_T = span * 12.0 / 240.0
        I_L = 5.0 * (wL / 12.0) * (span * 12.0) ** 4 / (384.0 * E * dl_L)
        I_T = 5.0 * ((wD + wL) / 12.0) * (span * 12.0) ** 4 / (384.0 * E * dl_T)
        return dict(id=mid, member=what, span_ft=span, trib_ft=round(trib_ft, 3),
                    wD_klf=round(wD, 4), wL_klf=round(wL, 4), wu_klf=round(wu, 4),
                    Mu_kipft=round(wu * span ** 2 / 8.0, 2), Vu_kip=round(wu * span / 2.0, 2),
                    R_service_kip=round((wD + wL) * span / 2.0, 2),
                    defl_limit_live_in=round(dl_L, 3), defl_limit_total_in=round(dl_T, 3),
                    I_req_in4=round(max(I_L, I_T), 2),
                    basis="1.2D+1.6L (L = %.0f psf%s); L/360 live, L/240 total (IBC Table "
                          "1604.3 floor members)" % (L, ", storage: non-reducible 4.7.3"
                                                    if CE.is_storage_level(cfg, N) else ""),
                    note="AGENT: section (S100 flexure F2/F3 incl. distortional, shear G2, web "
                         "crippling G5 at bearings, combined G6) + deflection vs the limits",
                    limit_state=None, cited=None, capacity=None, DC=None)
    js = cfg.get("joist_span_ft")
    sp_in = float(cfg.get("joist_spacing_in", 16.0))
    out.append(member("joist-typ", js, sp_in / 12.0, "joist"))
    bs = cfg.get("beam_span_ft")
    bt = cfg.get("beam_trib_ft", js)
    out.append(member("beam-typ", bs, bt or 0.0, "beam"))
    At = cfg.get("post_trib_sf") or ((bs or 0.0) * (bt or 0.0))
    if At:
        Pu = (1.2 * D + 1.6 * L) * At / 1000.0
        out.append(dict(id="post-typ", member="post", trib_sf=round(At, 1),
                        Pu_kip=round(Pu, 2), P_service_kip=round((D + L) * At / 1000.0, 2),
                        basis="1.2D+1.6L over %.0f sf (live kept: the platform level is a "
                              "FLOOR, not a roof)" % At,
                        note="AGENT: post section (S100 C/E compression, combined with the "
                             "strap-chord axial where the post is a chord), base plate",
                        limit_state=None, cited=None, capacity=None, DC=None))
    else:
        out.append(dict(id="post-typ", member="post", trib_sf=None,
                        note="AGENT: post tributary not derivable (declare beam_span_ft or "
                             "post_trib_sf) -- design the posts for 1.2D+1.6L with live kept",
                        limit_state=None, cited=None, capacity=None, DC=None))
    return out


def strap_ductility(cfg):
    """S400 E3.4.1(a) strap ductility as a NUMERIC check from cfg: strap_Fy_ksi (required),
    strap_Fu_ksi (default by grade), strap_Ag_in2_per_strap / strap_An_in2 (per strap, for the
    Rt An Fu > Ry Ag Fy test), strap_connection_method (1 welded / 2 / 3 tested; default 2)."""
    Fy = cfg.get("strap_Fy_ksi")
    if Fy is None:
        return dict(ok=None, message="NOT EVALUATED: declare cfg['strap_Fy_ksi'] (+ "
                                     "strap_An_in2/strap_Ag_in2 per strap, "
                                     "strap_connection_method) -- S400 E3.4.1(a) Methods 1-3")
    Ag = cfg.get("strap_Ag_in2_per_strap")
    An = cfg.get("strap_An_in2")
    Ag = float(Ag) if isinstance(Ag, (int, float)) else None
    An = float(An) if isinstance(An, (int, float)) else None
    return CS.strap_ductility_check(Fy, cfg.get("strap_Fu_ksi"), Ag, An,
                                    int(cfg.get("strap_connection_method", 2)))


def _cd_block_basis(sysname, cd_basis):
    """Capacity-design basis text per S400 system (the numeric seeds are per line)."""
    if sysname == "strap_braced":
        return ("S400 E3.3.3 + B3.4 (%s): chord studs, collectors, hold-downs and anchorage on "
                "every braced line are designed for the EXPECTED strength Omega_E*Vn of the "
                "SELECTED straps -- Omega_E = (Ry*Vn/w + v_finish)/(Vn/w) <= 1.8 with v_finish "
                ">= 0.2Vn/w (so >= Ry + 0.2: Gr 50 1.3, Gr 33 1.7) -- need not exceed the "
                "Omega_0-level force. Ry*Fy*Ag (expected strap yield force) is the demand for "
                "the STRAP CONNECTION and net section only (E3.4.1(a)), not the chord/"
                "anchorage basis" % cd_basis)
    if sysname == "gypsum_wall":
        return ("S400 E6.4.1.2 + E6.3.3 + B3.4: collectors, chord studs, other vertical "
                "boundary elements, hold-downs and anchorage are CAPACITY-PROTECTED -- design "
                "for Omega_E*Vn with Omega_E = 1.5 (E6.3.3), need not exceed the Omega_0-level "
                "force (Omega_0 = 2.5, Table 12.2-1). Foundations: E6.4.1.3 (no Omega_0)")
    if sysname == "sbmf":
        return ("S400 E4.3: connections, chord studs and anchorage on every frame are designed "
                "for the EXPECTED moment at the bolted connection (Ve per E4.3.3), need not "
                "exceed Omega_0*Eh (E4.3.1) -- never the ELF force alone")
    return ("%s: chord studs, hold-downs and anchorage on every shear-wall line are designed "
            "for the EXPECTED strength Omega_E*Vn of the SELECTED sheathing/fastener assembly "
            "(Omega_E = (1.1vn + v_finish)/vn <= 1.8, v_finish >= 0.1vn), capped at the "
            "Omega_0-level force -- never the ELF force alone" % CS.SYSTEMS[sysname]["std"])


def build_package(name, cfg, res):
    """The CFS calc_package dict (agent fills capacities). Seeds refuse silence: every wall
    line in every story gets a slot; hold-down slots carry the rod-switch feasibility note.
    Per-direction systems (12.2.2) and the 12.2.3.3 per-line exception carry their own
    rho / Omega_0 / Omega_E; every S400 system that requires capacity design (B3.4, incl.
    gypsum R=2 per A1.2.3/E6.4.1.2) gets numeric T_cd seeds = min(Omega_E*Vn stack where
    the selected assembly is declared, Omega_0-level stack); Type II lines divide chord/
    hold-down seeds by Ca and carry the distributed-uplift seed (S400 E1.4.2.2)."""
    e = res["elf"]
    sysname = cfg.get("system", "wsp_shearwall")
    rho = _rho_seismic(cfg)
    fL, L_basis = live_companion_factor(cfg)
    di = diaphragm_idealization(cfg, res)
    pkg = dict(building=name, code="AISI S100-16(R2020)+S2/S3, S240-20, S400-20 -- LRFD",
               system=sysname, combos_note=COMBOS_NOTE,
               combos=enumerate_combos(cfg),
               L_factor=fL, L_factor_basis=L_basis,
               rho=rho,
               rho_basis="ASCE 7-22 12.3.4 (canonical SDC); rho=%.2f is MULTIPLIED into the "
                         "seeded strength demands below (v_unit_plf, V_kip, T_cum/T_bay, "
                         "wind-vs-seismic comparison); NOT applied to drift (12.3.4.1 item "
                         "2), diaphragm Fpx (item 7), or Omega_0/expected-strength "
                         "capacity-design seeds (item 5)" % rho,
               elf=dict(V_kip=round(e["V"], 1), Cs=round(e["Cs"], 4), W_kip=round(e["W"], 0),
                        Ta_s=round(e["Ta"], 3),
                        note="pure ELF (no rho); per-slot seeds below already include rho"),
               diaphragm_basis=di,
               wall_lines=[], holddowns=[], studs=[], collectors=[], drift_table=[],
               preflight_warnings=list(res.get("preflight_warnings", [])) + di["warnings"])
    # seismic weight breakdown (12.7.2 items) -- storage / partitions visible in the package
    sw = {}
    for k in range(1, cfg["stories"] + 1):
        try:
            _w, bd = CE.story_weight(cfg, k, detail=True)
            sw[k] = {kk: (round(v, 2) if isinstance(v, float) else v) for kk, v in bd.items()}
        except TypeError:                     # a cfg-level story_weight override (no detail)
            sw = None
            break
    if sw:
        pkg["elf"]["weight_by_level_kip"] = sw
    dirs = list(res["directions"])
    dsys = {d: (res["directions"][d].get("system") or _system(cfg, d)) for d in dirs}
    mixed = len(set(dsys.values())) > 1 or any(res["directions"][d].get("line_systems")
                                               for d in dirs)
    if mixed or cfg.get("seis_by_dir") or cfg.get("rho_by_dir"):
        pkg["rho_by_dir"] = {d: _rho_seismic(cfg, d) for d in dirs}
        ebd = res.get("elf_by_dir") or {}
        pkg["elf_by_direction"] = {d: dict(V_kip=round(ebd[d]["V"], 1), Cs=round(ebd[d]["Cs"], 4),
                                           W_kip=round(ebd[d]["W"], 0), system=dsys[d],
                                           R=ebd[d].get("R"), Cd=ebd[d].get("Cd"),
                                           Om0=ebd[d].get("Om0"),
                                           line_systems=res["directions"][d].get("line_systems"))
                                   for d in dirs if d in ebd}
        if len(set(dsys.values())) > 1:
            pkg["system"] = " + ".join("%s (%s)" % (dsys[d], d) for d in dirs) + \
                " -- ASCE 7-22 12.2.2"
            pkg["system_by_direction"] = dsys
    wind = wind_line_screen(cfg, res)
    if wind:
        pkg["wind_basis"] = wind["X"]["basis"]
        pkg["wind_basis_by_dir"] = {d: wind[d]["basis"] for d in wind}
    # ---- capacity-design applicability + block (per direction; building system first) ----
    cd_dir = {}
    for d in dirs:
        s_d = dsys[d]
        req, cb = CS.capacity_design_required(s_d, _seis(cfg, d)["R"], _sdc(cfg, d))
        om0e_d, om0_d, om_note = _om0_eff(cfg, d, res)
        OmE, OmE_b = CS.omega_E(s_d, cfg.get("v_finish_ratio"), cfg.get("strap_Fy_ksi"))
        cd_dir[d] = dict(system=s_d, required=req, applicability=cb, Om0=om0_d, Om0_eff=om0e_d,
                         Om0_eff_basis=om_note, Omega_E=OmE, Omega_E_basis=OmE_b)
    pkg["capacity_design_applicability"] = {d: dict(system=v["system"], required=v["required"],
                                                    basis=v["applicability"])
                                            for d, v in cd_dir.items()}
    prim = dirs[0] if dirs else "X"
    if any(v["required"] or v["system"] == "sbmf" for v in cd_dir.values()):
        p = cd_dir[prim] if (cd_dir[prim]["required"] or cd_dir[prim]["system"] == "sbmf") \
            else next(v for v in cd_dir.values() if v["required"] or v["system"] == "sbmf")
        pkg["capacity_design"] = dict(
            basis=_cd_block_basis(p["system"], p["applicability"]),
            note="AGENT: after selecting the assembly, give Vn per line/story "
                 "(cfg['selected_Vn_kip'], or strap_Ag_in2 + strap_Fy_ksi) so the "
                 "Omega_E*Vn stack is computed, and design chords/hold-downs/anchorage/"
                 "collectors for min(Omega_E*Vn stack, Omega_0-level stack)")
        if p["system"] in ("strap_braced", "sbmf"):
            pkg["capacity_design"].update(Ry_by_Fy=RY_BY_FY, Rt_by_Fy=RT_BY_FY)
        if "strap_braced" in [v["system"] for v in cd_dir.values()] or any(
                "strap_braced" in (res["directions"][d].get("line_systems") or {}).values()
                for d in dirs):
            sd = strap_ductility(cfg)
            pkg["capacity_design"]["strap_ductility"] = dict(
                sd, basis="S400 E3.4.1(a): Method 1 welded (gross yielding governs) | Method 2 "
                          "(Rt Fu)/(Ry Fy) >= 1.2 AND Rt An Fu > Ry Ag Fy | Method 3 E2126 "
                          "tests; Gr 33 straps fail the Method-2 ratio (1.09)")
            if sd.get("ok") is False:
                pkg["preflight_warnings"].append("STRAP DUCTILITY FAILS: " + sd["message"])
        if any(v["system"] in CD_WALL_SYSTEMS and v["required"] for v in cd_dir.values()):
            pc = next(v for v in ([cd_dir[prim]] + list(cd_dir.values()))
                      if v["system"] in CD_WALL_SYSTEMS and v["required"])
            pkg["capacity_design"].update(
                Om0=pc["Om0"], Om0_eff=pc["Om0_eff"], Om0_eff_basis=pc["Om0_eff_basis"],
                Omega_E=pc["Omega_E"], Omega_E_basis=pc["Omega_E_basis"],
                lines={},
                seed_basis="T_cd_seed = min(Omega_E*Vn stack of the SELECTED assembly where "
                           "declared, Omega0-level stack), no dead relief (S400 B3.4) -- "
                           "relief only as documented",
                instruction="NUMERIC: per line, Ve_cap_by_story_kip = Om0_eff x V_ELF,story "
                            "(pure ELF, NO rho -- 12.3.4.1 item 5) and T_Om0_stack_kip = the "
                            "same top-down stack at Om0_eff-level shears; where Vn is "
                            "declared, T_OmegaE_Vn_stack_kip caps each story shear at "
                            "Omega_E*Vn and T_cd_seed_kip = the governing (lower) stack. The "
                            "FINAL hold-down/chord/anchorage demand is that seed -- NEVER the "
                            "raw ELF seed; consistency.check FAILS demands <= 1.05x the ELF "
                            "seed. Type II lines are divided by Ca (S400 E1.4.2.2.2). "
                            "dead_relief_kip_available is the DOCUMENTED (0.9-0.2SDS)D chord "
                            "relief -- NOT taken in the seed; take it only as documented.")
        if len(cd_dir) > 1 and len(set(v["system"] for v in cd_dir.values())) > 1:
            pkg["capacity_design"]["by_direction"] = {
                d: dict(v, basis=_cd_block_basis(v["system"], v["applicability"]))
                for d, v in cd_dir.items()}
    for dirn, dd in res["directions"].items():
        rho_d = _rho_seismic(cfg, dirn)
        lsys_map = dd.get("line_systems") or {}
        lines_dir = cfg["lines_x"] if dirn == "X" else cfg["lines_y"]
        dlim = None if dd.get("drift_limit_none") else dd.get("drift_limit")
        for lname, lr in dd["lines"].items():
            _ln = next((l for l in lines_dir if l.name == lname), None)
            lsysname = lsys_map.get(lname, dsys.get(dirn, sysname))
            wl = (wind or {}).get(dirn, {}).get("lines", {}).get(lname)
            tii = type_ii_spec(cfg, dirn, _ln) if _ln is not None else None
            Ca = tii["Ca"] if tii else None
            alim = CS.aspect_limit(lsysname, cfg.get("sheathing"))
            for k in sorted(lr):
                r = lr[k]
                v_w = (wl["v_plf"].get(k) if wl else None)
                slot = dict(
                    id="wall-%s-%s-s%d" % (dirn, lname, k), direction=dirn, story=k,
                    v_unit_plf=round(r["v_unit_plf"] * rho_d, 0),
                    V_kip=round(r["V"] * rho_d, 1),
                    demand_basis="tributary (%s diaphragm) + 5%% shift; includes rho=%.2f"
                                 % (dd.get("diaphragm", "flexible"), rho_d),
                    sheathing=None, fastener_schedule=None,       # AGENT (S400 table, cited)
                    limit_state=None, cited=None, capacity=None, DC=None)
                if lsysname != sysname:
                    slot["system"] = lsysname
                if Ca and Ca.get(k):
                    slot["type_ii_Ca"] = round(Ca[k], 3)
                    slot["v_unit_over_Ca_plf"] = round(r["v_unit_plf"] * rho_d / Ca[k], 0)
                    slot["demand_basis"] += ("; TYPE II: capacity Vn = Ca*vn*Sum Li (S400 "
                                             "E1.3.1.2) -- compare v_unit_over_Ca_plf with "
                                             "the tabulated vn")
                if alim and _ln is not None:
                    bad = [(L, hh) for (L, hh) in _ln.segments.get(k, [])
                           if L > 0 and (hh / L > alim + 1e-9 or L < 2.0)]
                    if bad:
                        slot["aspect_flags"] = [dict(L_ft=L, h_ft=hh, h_over_w=round(hh / L, 2))
                                                for (L, hh) in bad]
                        pkg["preflight_warnings"].append(
                            "%s line %s story %d: %d segment(s) exceed h:w %.0f:1 or are < 24 "
                            "in. -- may NOT be counted (S400-20 E6.3.1.1 / Table E6.3-1)"
                            % (dirn, lname, k, len(bad), alim))
                if v_w is not None:
                    slot["v_wind_plf"] = v_w
                    if wl.get("case_by_story", {}).get(k):
                        slot["wind_case"] = wl["case_by_story"][k]
                    slot["governing_basis"] = (
                        "wind (1.0W > rho*E at this line) -- wind capacity: %s"
                        % CS.wind_capacity_basis(lsysname)
                        if v_w > r["v_unit_plf"] * rho_d
                        else "seismic (rho=%.2f included in the comparison)" % rho_d)
                pkg["wall_lines"].append(slot)
                amp = r.get("drift_amplified", 0.0)
                pkg["drift_table"].append(dict(
                    direction=dirn, line=lname, story=k,
                    drift_amplified=round(amp, 5),
                    drift_single_story=round(r.get("drift_amplified_single_story", 0.0), 5),
                    drift_rotation_from_below=round(
                        r.get("drift_amplified_first_order", 0.0)
                        - r.get("drift_amplified_single_story", 0.0), 5),
                    theta=round(r.get("theta", 0.0), 4),
                    theta_max=round(r.get("theta_max", 0.0), 4),
                    limit=dlim,
                    ok=True if dlim is None else amp <= dlim))
            base_k = min(lr)
            base = lr[base_k]
            ca0 = 1.0
            if Ca and _ln is not None:
                # Type II: effective 1/Ca of the stack (per-story Ca_j, E1.4.2.2-2)
                _s1 = _capacity_stack(cfg, _ln, dd["dist"], 1.0, None, None, Ca)[base_k]["T"]
                _s0 = _capacity_stack(cfg, _ln, dd["dist"], 1.0, None, None, None)[base_k]["T"]
                ca0 = (_s0 / _s1) if _s1 > 0 else Ca.get(base_k, 1.0)
            T_seis = base["T_kip"] * rho_d / ca0      # rho-included ELF strength seed
            T_wind = (wl["T_base_kip"] / ca0) if wl else None
            T_gov = max(T_seis, T_wind or 0.0)
            _segs = _ln.segments.get(base_k, []) if _ln is not None else []
            # STRAP lines: overturning concentrates at BAY ends -- seed the PER-BAY
            # tension too (T_line spreads M over the whole line length; per bay:
            # T_bay = M/(n_bays*w_bay) = T_line * L_line/(n_bays*w_bay))
            T_bay = None
            if lsysname == "strap_braced" and _segs:
                _n, _w = len(_segs), _segs[0][0]
                T_bay = round(T_seis * _ln.length(base_k) / (_n * _w), 1)
                T_gov = max(T_gov, T_bay)
            dev, kdev, note = WL.pick_holddown(T_gov)
            hd = dict(
                id="hd-%s-%s" % (dirn, lname), line=lname, direction=dirn,
                T_cum_kip=round(T_seis, 1), device_class=dev,
                k_kip_in=kdev, feasibility_note=note,
                basis="cumulative overturning tension, top-down stack (includes rho=%.2f); "
                      "sized for TENSION (never the shear force); rods computed PL/AE + "
                      "take-up" % rho_d,
                limit_state=None, cited=None, capacity=None, DC=None)
            if Ca:
                hd["basis"] += ("; TYPE II: C = Vh/(Ca*Sum Li) (S400 Eq. E1.4.2.2-2) story by "
                                "story -- T_cum, T_wind and T_cd are divided by Ca (effective "
                                "Ca of the stack = %.3f)" % ca0)
            if T_bay is not None:
                hd["T_bay_seed_kip"] = T_bay
                hd["basis"] += ("; STRAP LINE: design anchorage for the PER-BAY tension "
                                "seed T_bay_seed_kip (includes rho; the line-level T_cum "
                                "spreads overturning over the whole line), then apply the "
                                "S400 E3.3.3 capacity design (Omega_E*Vn of the SELECTED "
                                "straps, capped at the Omega0 level -- T_cd_seed_kip)")
            if T_wind is not None:
                hd["T_wind_kip"] = round(T_wind, 1)
                if T_wind > T_seis:
                    hd["basis"] += ("; WIND GOVERNS the tension (0.9D+1.0W net-uplift case) "
                                    "-- the anchorage chain is a wind design")
            # NUMERIC capacity-design seed (every S400 wall system that requires it):
            # min(Omega_E*Vn, Omega0_eff x V_ELF) story shears + the SAME top-down stacking
            # as the ELF T_cum, from PURE ELF (no rho -- 12.3.4.1 item 5), NO dead relief
            cdl = cd_dir.get(dirn, {})
            l_req = cdl.get("required")
            if lsysname != cdl.get("system"):
                l_req = CS.capacity_design_required(lsysname, CS.SYSTEMS[lsysname]["R"],
                                                    _sdc(cfg, dirn))[0]
            if lsysname in CD_WALL_SYSTEMS and l_req and _ln is not None:
                if lsysname == cdl.get("system"):
                    Om0e = cdl["Om0_eff"]
                else:
                    om = CS.SYSTEMS[lsysname]["Om0"]
                    Om0e = om - 0.5 if (di["flexible_ok"] and om >= 2.5) else om
                OmE, OmE_b = CS.omega_E(lsysname, cfg.get("v_finish_ratio"),
                                        cfg.get("strap_Fy_ksi"))
                Vn, Vn_b = selected_Vn(cfg, dirn, _ln, lsysname)
                Ve_cap = {k: round(lr[k]["V"] * Om0e, 1) for k in lr}
                st_om = _capacity_stack(cfg, _ln, dd["dist"], Om0e, None, None, Ca)
                T_om = round(st_om[base_k]["T"], 1)
                T_cd, st_cd, T_ev = T_om, None, None
                if Vn and OmE is not None:
                    st_cd = _capacity_stack(cfg, _ln, dd["dist"], Om0e, OmE, Vn, Ca)
                    T_ev = round(st_cd[base_k]["T"], 1)
                    T_cd = min(T_om, T_ev)
                w_bay = _segs[0][0] if _segs else None
                relief, relief_note = _chord_dead_relief(cfg, _ln, dirn, w_bay)
                cd_entry = dict(
                    direction=dirn, line=lname, system=lsysname, Om0_eff=Om0e,
                    Omega_E=OmE, Omega_E_basis=OmE_b, Vn_selected_kip=Vn, Vn_basis=Vn_b,
                    Ve_cap_by_story_kip=Ve_cap, T_Om0_stack_kip=T_om,
                    T_OmegaE_Vn_stack_kip=T_ev, T_cd_seed_kip=T_cd,
                    dead_relief_kip_available=relief, dead_relief_basis=relief_note,
                    basis="T_cd_seed = min(Omega0-level stack, Omega_E*Vn_selected stack%s) "
                          "per S400 B3.4 capacity design -- relief only as documented"
                          % ("" if T_ev is not None else " -- NOT evaluated: Vn not declared"))
                if Ca:
                    cd_entry["type_ii_Ca_by_story"] = {k: round(v, 3) for k, v in Ca.items()}
                if lsysname == "strap_braced" and _segs:
                    _n = len(_segs)
                    cd_entry["Ve_cap_bay_by_story_kip"] = \
                        {k: round(v / _n, 1) for k, v in Ve_cap.items()}
                    cd_entry["T_cd_bay_seed_kip"] = \
                        round(T_cd * _ln.length(base_k) / (_n * _segs[0][0]), 1)
                    hd["T_cd_seed_kip"] = cd_entry["T_cd_bay_seed_kip"]
                else:
                    hd["T_cd_seed_kip"] = T_cd
                if "capacity_design" in pkg and "lines" in pkg["capacity_design"]:
                    pkg["capacity_design"]["lines"]["%s:%s" % (dirn, lname)] = cd_entry
                hd["basis"] += ("; CAPACITY DESIGN: final demand = min(Omega0-level, "
                                "expected-strength Omega_E*Vn stack of the SELECTED assembly) "
                                "= T_cd_seed_kip -- NEVER the raw ELF T_cum/T_bay seed "
                                "(see capacity_design block)")
                st_use = st_cd or st_om
            else:
                st_use = None
            if Ca:
                # S400 E1.4.2.2.1 / E1.4.2.2.3: unit shear into collectors and the uniform
                # uplift between Type II ends, t = v = V/(Ca*Sum Li) per story
                t_elf, t_cd = {}, {}
                for k in sorted(lr):
                    L = _ln.length(k) if _ln is not None else 0.0
                    if L <= 0 or not Ca.get(k):
                        continue
                    t_elf[k] = round(lr[k]["V"] * rho_d * 1000.0 / (Ca[k] * L), 0)
                    if st_use and st_use.get(k):
                        t_cd[k] = round(st_use[k]["V_cap"] * 1000.0 / (Ca[k] * L), 0)
                hd["type_ii"] = dict(
                    Ca_by_story={k: round(v, 3) for k, v in Ca.items()}, Ca_basis=tii["basis"],
                    uplift_t_plf_by_story_strength=t_elf,
                    uplift_t_plf_by_story_capacity=t_cd or None,
                    collector_v_plf_by_story=t_cd or t_elf,
                    note="S400 E1.4.2.2.3: Type II bottom plates at full-height sheathing are "
                         "anchored for a UNIFORM uplift t = v (E1.4.2.2.1: v = V/(Ca*Sum Li), "
                         "V = expected strength need not exceed the Omega0 level -> the "
                         "'capacity' row; 'strength' row = rho*E for reference); collectors "
                         "between Type II segments carry the same v; deflection per "
                         "E1.4.2.3 (Type I deflection / Ca)")
            pkg["holddowns"].append(hd)
        if dd.get("gate_flags"):
            pkg.setdefault("model_vs_tributary_flags", []).extend(dd["gate_flags"])
        if dd.get("drift_flags"):
            pkg.setdefault("drift_flags", []).extend(dd["drift_flags"])
        # 12.8.7 P-delta stability (theta > theta_max FAILS the gate; 0.10 < theta <= theta_max
        # is already folded into drift_amplified via 1/(1-theta)) and wall-line discontinuities
        if dd.get("drift_basis"):
            pkg["drift_basis"] = dd["drift_basis"]
        if dd.get("stability_flags"):
            pkg.setdefault("stability_flags", []).extend(dd["stability_flags"])
        if dd.get("stability_warnings"):
            pkg.setdefault("stability_warnings", []).extend(dd["stability_warnings"])
        for t in dd.get("transfers") or []:
            pkg.setdefault("discontinuity_transfers", []).append(dict(
                t, direction=dirn, id="transfer-%s-%s-L%d" % (dirn, t["line"], t["story"]),
                V_kip=round(t["V_kip"], 2), limit_state=None, cited=None, capacity=None,
                DC=None))
        if dd.get("drift_limit_basis"):
            pkg.setdefault("drift_limit_basis", {})[dirn] = dd["drift_limit_basis"]
    if "capacity_design" in pkg and "lines" in pkg["capacity_design"] and \
            not pkg["capacity_design"]["lines"]:
        pkg["capacity_design"]["lines"] = {}
    # stud schedule seed (typical bearing stud; agent adds section + checks incl. G5 at track)
    trib = cfg.get("stud_trib_ft", 2.0)
    pkg["studs"].append(dict(id="stud-typ-bearing", trib_ft=trib,
                             P_cum_kip_by_story=stud_axial_stack(cfg, trib),
                             note="AGENT: section per story group (never lighter below), "
                                  "sheathing-braced assumption STATED, web crippling G5 at "
                                  "track, interaction H1", limit_state=None, cited=None,
                             capacity=None, DC=None))
    gf = gravity_framing_seeds(cfg)
    if gf:
        pkg["gravity_framing"] = gf
    pkg["collectors"] = collector_seeds(cfg, res)
    return pkg


def write_package(name, cfg, res, outdir="."):
    import os
    pkg = build_package(name, cfg, res)
    p = os.path.join(outdir, "calc_package_cfs.json")
    json.dump(pkg, open(p, "w"), indent=1)
    return p, pkg


# ---------------- Stage 4: portal package ----------------

PORTAL_COMBOS_NOTE = (
    "ASCE 7-22 2.3.1 LRFD: 1.4D; 1.2D+1.6L+(0.5Lr or 0.3S) [crane/point L]; "
    "1.2D+(1.6Lr or 1.0S)+(L or 0.5W) -- BOTH branches (wind-free and every wind case); "
    "1.2D+1.0W+L+(0.5Lr or 0.3S); 0.9D+1.0W (NET UPLIFT, a REQUIRED anchorage case); 2.3.6 "
    "(1.2+0.2SDS)D+rho*E+L+0.15S and (0.9-0.2SDS)D+rho*E for +/-E, plus the Omega_0 pair "
    "(role 'overstrength'). Wind: both directions, both internal-pressure signs, both "
    "windward-roof Cp branches and the along-ridge case. Analysis: AISI S100-16 C1.1 direct "
    "analysis (0.90 EA/EI x tau_b, notional Ni = Yi/240, P-Delta).")


def _pair_rows(env):
    out = []
    for key, basis in (("M_max", "max |M|"), ("M_inside_max", "max M, inside flange in tension"),
                       ("M_outside_max", "max M, outside flange in tension"),
                       ("Pc_max", "max compression"), ("Pt_max", "max tension (uplift)"),
                       ("V_max", "max shear")):
        e = env.get(key)
        if e:
            out.append(dict(basis=basis, combo=e["combo"], label=e["label"],
                            M_kipin=e["M_kipin"], M_pos_kipin=e["M_pos_kipin"],
                            M_neg_kipin=e["M_neg_kipin"], Pc_kip=e["Pc_kip"],
                            Pt_kip=e["Pt_kip"], V_kip=e["V_kip"]))
    return out


def build_portal_package(name, cfg, res):
    """calc_package for the PORTAL path (cfs_frame.run() result). Same contract discipline:
    demand slots seeded, every capacity/citation field left for the agent.
    Member slots envelope EVERY column (col_L, col_R, interior) and EVERY rafter (raf_L,
    raf_R) over the strength combinations with wind from both sides (CFS-02); each carries
    the governing (max |M|) combo with its PAIRED P/V and the max-compression / max-tension /
    inside- and outside-flange pairs (CFS-26). Joint slots: BOTH knees and the apex (the
    rafter end AT the ridge -- a prior version seeded the apex with the eave-end moment),
    signed (+ = inside flange in tension). Monoslopes have no ridge joint. Anchorage: max
    shear, NET UPLIFT, compression and base moment, plus Omega_0-level seeds when seismic.
    Seismic (drift Cd delta_xe/Ie, theta, rho, Omega_0), SBMF screens/Ve, crane and fatigue
    slots when declared."""
    pkg = dict(building=name, code="AISI S100-16(R2020)+S2/S3, S400-20 -- LRFD; ASCE 7-22",
               kind="cfs_portal", system=cfg.get("system") or "portal frame",
               structure_kind=res["structure_kind"], combos_note=PORTAL_COMBOS_NOTE,
               combos=[dict(label=n, role=c.get("role", "strength"))
                       for n, c in res["combos"].items()],
               sections=res["sections"], members=[], connections=[], anchorage=[],
               schedules=[], drift_table=[],
               preflight_warnings=res.get("preflight_warnings", []),
               wind_basis=res.get("wind_basis", {}), notes=list(res.get("notes", [])))
    da = res.get("direct_analysis") or {}
    pkg["analysis_basis"] = dict(
        tier=cfg.get("analysis_fidelity", 1),
        direct_analysis=cfg.get("direct_analysis", True),
        direct_analysis_basis=da.get("basis"),
        max_second_order_amplification=da.get("max_amplification"),
        eff_stiffness=(res.get("eff_stiffness") or {}).get("ratios"),
        n_ply=(res.get("eff_stiffness") or {}).get("n_ply"),
        frame_self_weight_kip=res.get("frame_self_weight_kip"),
        note="Tier-1 effective-stiffness iterated on PER-PLY demands (S100 App. 1 EWM); "
             "direct analysis per AISI S100-16 C1.1 (0.90 EA/EI x tau_b, notional Ni = "
             "Yi/240 in the destabilising direction, P-Delta) on the strength combos; member "
             "capacities incl. distortional/global + H1 are the AGENT's (K = 1, C1.1.2)")
    for g, label in (("col", "columns (col_L, col_R%s)" % (", interior" if cfg.get("spans")
                                                           else "")),
                     ("raf", "rafters (raf_L, raf_R)")):
        env = (res.get("member_envelopes") or {}).get(g)
        if not env:
            continue
        mx = env["M_max"]
        pkg["members"].append(dict(
            id="frame-%s" % g, member=g, section=res["sections"][g],
            governing_combo=mx["combo"], governing_label=mx["label"],
            P_kip=mx["P_kip"], Pc_paired_kip=mx["Pc_kip"], Pt_paired_kip=mx["Pt_kip"],
            V_kip=mx["V_kip"], M_kipin=mx["M_kipin"],
            M_signed_kipin=(mx["M_pos_kipin"] if mx["M_pos_kipin"] >= -mx["M_neg_kipin"]
                            else mx["M_neg_kipin"]),
            demand_pairs=_pair_rows(env),
            envelope_basis="envelope of ALL %s over every strength combination, wind from "
                           "both sides; M + = inside flange in tension" % label,
            note="AGENT: EWM local+distortional+global, H1 interaction for EVERY demand pair "
                 "(max M with its P, max compression with its M, uplift tension with its M); "
                 "knee-region distortional with the UNBRACED inside flange; uplift-reversal "
                 "unbraced case",
            limit_state=None, cited=None, capacity=None, DC=None))
    kb = (res.get("member_envelopes") or {}).get("kb")
    if kb:
        pkg["members"].append(dict(
            id="frame-kb", member="kb", section=(cfg.get("knee_braces") or {}).get("section"),
            governing_combo=kb["Pc_max"]["combo"], P_kip=max(kb["Pc_max"]["Pc_kip"],
                                                             kb["Pt_max"]["Pt_kip"]),
            Pc_kip=kb["Pc_max"]["Pc_kip"], Pt_kip=kb["Pt_max"]["Pt_kip"], V_kip=0.0,
            M_kipin=0.0, demand_pairs=_pair_rows(kb),
            note="AGENT: pin-ended knee brace -- compression (E, K = 1) and tension + end "
                 "connections", limit_state=None, cited=None, capacity=None, DC=None))
    joints = res.get("joints") or {}

    def conn(jid, jname, keys, note):
        rows = {k: joints[k] for k in keys if k in joints}
        if not rows:
            return
        Mpos = max(r["M_pos"] for r in rows.values())
        Mneg = min(r["M_neg"] for r in rows.values())
        kp = max(rows, key=lambda k: rows[k]["M_pos"])
        kn = min(rows, key=lambda k: rows[k]["M_neg"])
        pkg["connections"].append(dict(
            id=jid, joint=jname, M_transfer_kipin=round(max(Mpos, -Mneg), 1),
            M_pos_kipin=round(Mpos, 1), combo_pos=rows[kp]["combo_pos"], at_pos=kp,
            M_neg_kipin=round(Mneg, 1), combo_neg=rows[kn]["combo_neg"], at_neg=kn,
            V_max_kip=round(max(r["V_max"] for r in rows.values()), 2),
            by_joint={k: dict(M_pos=v["M_pos"], combo_pos=v["combo_pos"], M_neg=v["M_neg"],
                              combo_neg=v["combo_neg"], V_max=v["V_max"])
                      for k, v in rows.items()},
            sign="M + = inside flange in tension (both signs must be detailed)",
            note=note, limit_state=None, cited=None, capacity=None, DC=None))
    conn("conn-knee", "knee", ("knee_L", "knee_R"),
         "AGENT: bolted gusset bracket at BOTH knees (windward and leeward envelope) -- bolt "
         "group, bearing, net section, gusset buckling, for BOTH moment signs; bracing "
         "assumption at the joint STATED")
    apex_keys = [k for k in joints if k.startswith("apex")]
    if apex_keys:
        conn("conn-apex", "apex", apex_keys,
             "AGENT: ridge splice -- moment AT THE RIDGE (rafter end at the apex node), both "
             "signs")
    elif cfg.get("monoslope"):
        pkg["notes"].append("monoslope: no ridge joint -- the rafter is continuous; add a "
                            "splice slot only if the rafter is spliced")
    valley = [k for k in joints if k.startswith("valley")]
    if valley:
        conn("conn-valley", "valley", valley, "AGENT: valley column-to-rafter joints")
    Vmax = Tup = Mmax = Cmax = 0.0
    up_combo = v_combo = c_combo = None
    per_base = {}
    for cname, cd in res["combos"].items():
        if cd.get("role") != "strength":
            continue
        for t, r in cd["reactions_kip"].items():
            pb = per_base.setdefault(str(t), dict(V=0.0, T=0.0, C=0.0, M=0.0))
            pb["V"] = max(pb["V"], abs(r[0]))
            pb["T"] = max(pb["T"], -r[1])
            pb["C"] = max(pb["C"], r[1])
            if len(r) > 2:
                pb["M"] = max(pb["M"], abs(r[2]))
                Mmax = max(Mmax, abs(r[2]))
            if abs(r[0]) > Vmax:
                Vmax, v_combo = abs(r[0]), cname
            if r[1] > Cmax:
                Cmax, c_combo = r[1], cname
            if r[1] < -Tup:
                Tup, up_combo = -r[1], cname
    anc = dict(
        id="base-anchor", V_base_kip=round(Vmax, 2), V_combo=v_combo,
        T_net_uplift_kip=round(Tup, 2), uplift_combo=up_combo,
        C_base_kip=round(Cmax, 2), C_combo=c_combo, M_base_kipin=round(Mmax, 1),
        per_base={k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in per_base.items()},
        note="AGENT: base plate + anchor rods; NET UPLIFT is a required case (0.9D+1.0W, every "
             "wind case)" + ("; FIXED base -- design the anchor group for M_base too"
                            if Mmax > 1.0 else ""),
        limit_state=None, cited=None, capacity=None, DC=None)
    om = [n for n, c in res["combos"].items() if c.get("role") == "overstrength"]
    if om:
        Vo = max(abs(r[0]) for n in om for r in res["combos"][n]["reactions_kip"].values())
        To = max(max(-r[1], 0.0) for n in om for r in res["combos"][n]["reactions_kip"].values())
        anc.update(V_Om0_kip=round(Vo, 2), T_Om0_kip=round(To, 2),
                   Om0_note="Omega_0-level seeds (2.3.6 overstrength pair) where the anchorage "
                            "must be designed with overstrength (e.g. ACI 318 17.10 "
                            "non-ductile anchors) -- agent states which applies")
    pkg["anchorage"].append(anc)
    for sched, note in (("purlin", "Z-purlins, lap/continuity + uplift R-factor basis STATED"),
                        ("girt", "wall girts, C&C wind"),
                        ("strap", "longitudinal tension-only X-straps: An*Fu vs Ag*Fy, "
                                  "connection at least strap strength%s"
                         % ("; carries the crane LONGITUDINAL force %.2f kip (4.9.5)"
                            % res["crane"]["longitudinal_kip"] if res.get("crane") else ""))):
        pkg["schedules"].append(dict(id="sched-%s" % sched, schedule=sched, rows=None,
                                     note="AGENT: " + note,
                                     limit_state=None, cited=None, capacity=None, DC=None))
    if res.get("crane"):
        pkg["crane"] = res["crane"]
        pkg["schedules"].append(dict(
            id="sched-crane", schedule="crane", rows=None,
            note="AGENT: runway / monorail beam and hangers or brackets (vertical incl. "
                 "impact, lateral, longitudinal per ASCE 7-22 4.9) and the FATIGUE scope "
                 "(AISI S100 Chapter M: stress range, number of cycles, detail category) -- "
                 "the frame demands above already include the crane as L",
            fatigue_scope=None, limit_state=None, cited=None, capacity=None, DC=None))
    if res.get("torsion_companion"):
        pkg["torsion_companion"] = res["torsion_companion"]
    sv = res["service"]
    pkg["drift_table"].append(dict(check="eave_sway", value_in=sv["eave_sway_in"],
                                   H_over=sv["H_over"], wind_case=sv.get("governing_wind_case"),
                                   basis=sv["note"], ok=None,
                                   criterion="AGENT states (e.g., H/60 metal bldg, H/240 "
                                             "w/brittle finishes) and verdicts"))
    pkg["drift_table"].append(dict(check="apex_deflection", value_in=sv.get("apex_defl_in"),
                                   basis=sv.get("apex_defl_case"),
                                   ok=None, criterion="AGENT states span criterion"))
    se = res.get("seismic")
    if se:
        pkg["seismic"] = se
        if se.get("Delta_in") is not None:
            pkg["drift_table"].append(dict(
                check="seismic_drift", value_in=se["Delta_in"], ratio=se["drift_ratio"],
                limit=se["drift_limit"], ok=se["ok"],
                basis="Delta = Cd delta_xe / Ie = %.2f x %.4f / %.2f (12.8.6, rho = 1); limit "
                      "%s" % (se["Cd"], se["delta_xe_in"], se["Ie"], se["drift_limit_basis"]),
                criterion="ASCE 7-22 Table 12.12-1 (agent confirms the row)"))
            pkg["drift_table"].append(dict(
                check="stability_theta", value=se["theta"], limit=se["theta_max"],
                ok=se["theta"] <= se["theta_max"], basis=se["theta_basis"],
                criterion="ASCE 7-22 12.8.7 Eq. 12.8-16 / 12.8-17"))
    if res.get("sbmf"):
        sb = res["sbmf"]
        pkg["sbmf"] = sb
        ve = sb.get("expected_shear") or {}
        pkg["capacity_design"] = dict(
            basis="AISI S400-20 E4.3: beams, columns and bolt bearing plates of the CFS-SBMF "
                  "are designed for the EXPECTED connection shear Ve = VS + VB (E4.3.3 -- bolt "
                  "SLIP + BEARING of the 8-bolt connection), Emh need not exceed Omega_0 Eh "
                  "(E4.3.1); the mechanism is the bolted connection, NOT Ry*Fy*Ag of a member",
            Ve_seed=ve,
            note="AGENT: confirm the bolt pattern (Table E4.3.3-1), t/Fu/Rt of the connected "
                 "parts (S400 Table A3.2-1) and propagate Ve (or Omega_0 Eh where smaller) "
                 "into the beam, column and bolt-bearing-plate (E4.3.1.2) slots")
    return pkg


def write_portal_package(name, cfg, res, outdir="."):
    import os
    pkg = build_portal_package(name, cfg, res)
    p = os.path.join(outdir, "calc_package_cfs.json")
    json.dump(pkg, open(p, "w"), indent=1)
    return p, pkg


# ---------------- Stage 3b: the merged CFS entry point ----------------

def design_and_report(name, cfg, outdir=None, do_report=True):
    """The CFS analog of pipeline.design_and_report -- ONE straight-through call:
    consistency pre-lint -> engine run (wall path or portal path, auto-detected) ->
    calc_package_cfs.json seeds -> HTML report scaffold. Computes NO capacities: the agent
    derives every capacity/D-C from the RAG and fills the package slots."""
    import os
    root = outdir or os.path.join(os.environ.get("STEEL_BUILDER_JOBS") or ".", name)
    ddir = os.path.join(root, "design")
    os.makedirs(ddir, exist_ok=True)
    out = {"name": name, "root": root, "path": "cfs"}
    try:
        import consistency as CC
        out["cfg_lint"] = (CC._geometry_issues(cfg) or []) + \
                          (CC._design_basis_issues(cfg) or [])
    except Exception as ex:
        out["cfg_lint"] = ["consistency pre-lint failed: %s" % ex]
    component = cfg.get("structure_kind") == "component"
    if component:
        # COMPONENT MODE (added 2026-07-31, Ex25 finding): Tier-0 purlin/girt/
        # component jobs on an EXISTING shell. cfg carries the shell geometry
        # (span_ft/eave_ft/... -- used only for the skeleton/report/viewer);
        # the frame runs at FIXED bases for numerical stability regardless of
        # cfg['base'], every member/connection/anchorage slot is auto-marked
        # OUT OF SCOPE with DC = 0, and the package headlines component_mode.
        # The agent's real deliverables live in the schedules + extra blocks.
        import cfs_frame as CF
        # first-order (pdelta=False): the surrogate skeleton is not a design model and a
        # second-order solve of an arbitrary shell can be sway-unstable (garbage envelopes)
        cfgc = dict(cfg, base="fixed", structure_kind="portal", pdelta=False)
        res = CF.run(cfgc)
        p, pkg = write_portal_package(name, cfgc, res, ddir)
        pkg["component_mode"] = (
            "TIER-0 COMPONENT JOB: the shell frame above is an EXISTING "
            "structure surrogate (fixed-base skeleton for the report/viewer "
            "only -- its numbers are NOT design output); deliverables are the "
            "schedules + agent blocks; new-component reactions must be handed "
            "off to the EOR of record.")
        for m in pkg.get("members", []):
            m.update(section="EXISTING SHELL -- not in scope (component mode)",
                     limit_state="n/a; component reactions handed off",
                     cited="component-mode scope rule", capacity="n/a (existing)",
                     DC=0.0)
        for c in pkg.get("connections", []):
            c.update(selection="EXISTING -- not in scope (component mode)",
                     limit_state="n/a", cited="component-mode scope rule",
                     capacity="n/a", DC=0.0)
        for a in pkg.get("anchorage", []):
            a.update(selection="EXISTING -- not in scope (component mode)",
                     limit_state="n/a", cited="component-mode scope rule",
                     capacity="n/a", DC=0.0)
        import json as _json
        with open(p, "w") as f:
            _json.dump(pkg, f, indent=1)
        res["preflight_warnings"] = [w for w in res.get("preflight_warnings", [])
                                     if "P-DELTA" not in w]
        portal = True
    else:
        portal = "span_ft" in cfg
        if portal:
            import cfs_frame as CF
            res = CF.run(cfg)
            p, pkg = write_portal_package(name, cfg, res, ddir)
        else:
            res = CE.run(cfg)
            p, pkg = write_package(name, cfg, res, ddir)
    out.update(kind="portal" if portal else "wall", package=p,
               preflight_warnings=res.get("preflight_warnings", []),
               design_dir=ddir)
    if not portal:
        out["gate_flags"] = {d: dd["gate_flags"] for d, dd in res["directions"].items()
                             if dd["gate_flags"]}
        out["drift_flags"] = {d: dd["drift_flags"] for d, dd in res["directions"].items()
                              if dd["drift_flags"]}
        out["stability_flags"] = {d: dd.get("stability_flags") for d, dd in
                                  res["directions"].items() if dd.get("stability_flags")}
    if do_report:
        try:
            import report as RPT
            out["report_html"] = RPT.build_report_cfs(name, cfg, res, pkg, root)
        except Exception as ex:
            out["report_html"] = "report failed: %s" % ex
    out["NEXT_STEP"] = (
        "MANDATORY: fill EVERY package slot (capacity, cited clause, D/C) from the RAG -- "
        "wall lines need sheathing + fastener_schedule; hold-downs are TENSION devices; "
        "resolve every gate/drift/preflight flag with a *_resolution entry or a redesign; "
        "then re-render report.build_report_cfs and run consistency.check on the package.")
    return out


def _selftest():
    print("cfs_pipeline self-test")
    segs = {k: [(20.0, 9.5), (10.0, 9.5)] for k in (1, 2, 3, 4)}
    segsB = {k: [(15.0, 9.5)] for k in (1, 2, 3, 4)}
    cfg = dict(stories=4, heights_ft=[10.67, 9.5, 9.5, 9.5], plan_ft=(160.0, 62.0),
               D_floor=35.0, D_roof=20.0, clad=15.0, snow=0.0, L_floor=40.0,
               seis=CS.seis_cfs(1.0, 0.45, 0.45, "wsp_shearwall"), system="wsp_shearwall",
               risk_cat="II", structure_kind="wall", analysis_fidelity=0,
               diaphragm="flexible", collector_lines=["reentrant-NE"],
               lines_x=[WL.WallLine("X1", 0.0, segs), WL.WallLine("X2", 31.0, segsB),
                        WL.WallLine("X3", 62.0, segs)],
               lines_y=[WL.WallLine("Y1", 0.0, segsB), WL.WallLine("Y2", 80.0, segsB),
                        WL.WallLine("Y3", 160.0, segsB)],
               wall_props=dict(chord_area_in2=1.2, Gp_kip_in=9.0, en_in=0.03,
                               k_anchor_kip_in=50.0))
    res = CE.run(cfg)
    import tempfile
    p, pkg = write_package("Ex1-like", cfg, res, tempfile.gettempdir())
    assert len(pkg["wall_lines"]) == (3 + 3) * 4, "one slot per line per story per direction"
    assert len(pkg["holddowns"]) == 6 and len(pkg["collectors"]) == 1
    hd1 = [h for h in pkg["holddowns"] if h["line"] == "X2"][0]
    assert hd1["T_cum_kip"] > 0 and hd1["device_class"] in ("strap", "bolted", "rod")
    assert all(w["sheathing"] is None for w in pkg["wall_lines"]), "capacity fields stay empty"
    st = pkg["studs"][0]["P_cum_kip_by_story"]
    assert st[1] > st[4] > 0, "stud axial must stack downward"
    assert any(not d["ok"] for d in pkg["drift_table"]), "under-walled test must flag drift"
    assert pkg["drift_flags"], "drift flags must surface in the package"
    rods = [h for h in pkg["holddowns"] if h["device_class"] == "rod"]
    print("  slots: %d wall, %d hold-down (%d rod-switch), %d collector; stud P1=%.1f kip; "
          "package -> %s" % (len(pkg["wall_lines"]), len(pkg["holddowns"]), len(rods),
                             len(pkg["collectors"]), st[1], p))
    # Stage 4: portal package
    import cfs_frame as CF
    pcfg = CF._demo_cfg()
    pres = CF.run(pcfg)
    p2, ppkg = write_portal_package("portal-demo", pcfg, pres, tempfile.gettempdir())
    assert len(ppkg["members"]) == 2 and len(ppkg["connections"]) == 2
    assert ppkg["anchorage"][0]["T_net_uplift_kip"] > 0, "uplift anchorage slot must be live"
    assert ppkg["anchorage"][0]["uplift_combo"].startswith("0.9D+1.0W")
    assert all(m["capacity"] is None for m in ppkg["members"]), "capacity fields stay empty"
    assert len(ppkg["schedules"]) == 3 and ppkg["drift_table"][0]["ok"] is None
    scfg = dict(pcfg, col_section="1000S250-97", raf_section="1000S250-97",
                structure_kind=None)
    spkg = build_portal_package("portal-single", scfg, CF.run(scfg))
    assert spkg.get("torsion_companion") and spkg["preflight_warnings"]
    print("  portal package: knee M=%.0f kip-in, net uplift %.1f kip (%s); single-channel "
          "carries torsion table + tier nudge" %
          (ppkg["connections"][0]["M_transfer_kipin"],
           ppkg["anchorage"][0]["T_net_uplift_kip"], ppkg["anchorage"][0]["uplift_combo"]))
    # Stage 3b: combos + wind screen + strap seeds + merged design_and_report
    # 170 mph: the seeded seismic side now carries rho=1.3 (SDC D), so the wind-governing
    # fixture needs more wind than the pre-rho 140 mph to exercise the wind path
    cfgw = dict(cfg, wind=dict(V=170.0, exposure="C"))
    resw = CE.run(cfgw)
    pkgw = build_package("Ex-wind", cfgw, resw)
    assert pkgw["combos"] and any("0.9D+1.0W" in c["label"] for c in pkgw["combos"])
    assert any("rho*E" in c["label"] for c in pkgw["combos"])
    ws = [w for w in pkgw["wall_lines"] if "v_wind_plf" in w]
    assert ws, "wind screen must annotate wall slots"
    assert any("wind" in (w.get("governing_basis") or "") for w in ws), \
        "170 mph must out-govern rho-amplified seismic on at least one line"
    assert any(h.get("T_wind_kip") is not None for h in pkgw["holddowns"])
    # A3/A6: rho + numeric capacity-design seeds on the WSP package
    assert pkgw["rho"] == 1.3, "SDS=1.0/SD1=0.45 -> SDC D -> rho default 1.3"
    cdw = pkgw["capacity_design"]
    # CFS-21: the under-walled fixture FAILS drift -> 12.3.1.1(3)(b) does not support the
    # light-frame flexible idealization -> footnote-b reduction withheld
    assert cdw["Om0"] == 3.0 and cdw["Om0_eff"] == 3.0, cdw["Om0_eff_basis"]
    assert not pkgw["diaphragm_basis"]["flexible_ok"]
    # 12.3.1.3 calculated flexibility supports it -> Om0_eff = Om0 - 0.5 (footnote b)
    pk13 = build_package("Ex-1313", dict(cfgw, diaphragm_MDD_ADVE=2.5), resw)
    assert pk13["capacity_design"]["Om0_eff"] == 2.5
    # concrete topping > 1.5 in. declared flexible -> no reduction, loud warning
    pkc = build_package("Ex-conc", dict(cfgw, diaphragm_topping_in=3.0), resw)
    assert pkc["capacity_design"]["Om0_eff"] == 3.0
    assert any("12.3.1.1(3)(a)" in w for w in pkc["preflight_warnings"])
    cdw = pk13["capacity_design"]
    hdw = pk13["holddowns"][0]
    assert hdw["T_cd_seed_kip"] > 1.05 * hdw["T_cum_kip"] > 0, \
        "Omega0-level seed must exceed the rho-included ELF seed"
    lncd = cdw["lines"]["X:%s" % hdw["line"]]
    assert lncd["T_cd_seed_kip"] == hdw["T_cd_seed_kip"]
    assert lncd["Ve_cap_by_story_kip"][1] > lncd["Ve_cap_by_story_kip"][4] > 0
    assert lncd["dead_relief_kip_available"] is not None      # adjacent lines -> derivable
    assert "rho=1.30" in pkgw["wall_lines"][0]["demand_basis"]
    cfgs = dict(cfgw, system="strap_braced",
                seis=CS.seis_cfs(1.0, 0.45, 0.45, "strap_braced"))
    pkgs = build_package("Ex-strap", cfgs, CE.run(cfgs))
    assert pkgs["capacity_design"]["Ry_by_Fy"] == {33: 1.5, 50: 1.1}
    assert pkgs["capacity_design"]["Om0_eff"] == 2.0, "strap Om0=2.0 < 2.5: no footnote-b cut"
    hds = [h for h in pkgs["holddowns"] if "T_bay_seed_kip" in h][0]
    assert hds["T_cd_seed_kip"] > 1.05 * hds["T_bay_seed_kip"], \
        "strap per-bay Omega0-level seed must exceed the per-bay ELF seed"
    out_w = design_and_report("t3b-wall", cfgw, outdir=tempfile.mkdtemp())
    out_p = design_and_report("t3b-portal", CF._demo_cfg(), outdir=tempfile.mkdtemp())
    import os
    assert out_w["kind"] == "wall" and out_p["kind"] == "portal"
    for o in (out_w, out_p):
        assert os.path.exists(o["package"])
        assert o["report_html"] and os.path.exists(o["report_html"]), o["report_html"]
    assert "drift_flags" in out_w                       # the under-walled fixture must flag
    print("  Stage 3b: %d combos enumerated; wind governs %d/%d wall slots; strap Ry seeds "
          "OK; merged design_and_report wrote wall+portal report.html" %
          (len(pkgw["combos"]), len([w for w in ws if "wind" in
                                     (w.get("governing_basis") or "")]), len(ws)))
    print("SELF-TEST PASS")


if __name__ == "__main__":
    _selftest()

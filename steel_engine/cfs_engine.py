"""
cfs_engine.py -- Stage 2a: the light-frame (wall-framed) analysis path.

Model: under the FLEXIBLE-diaphragm idealization each wall line is an independent vertical stack
of story shear springs, with story mass/force by tributary area (wall_line.py). Each story's
deflection is the AISI S400-20 wall deflection at the line's unit shear (Eq. E1.4.1.4-1 wood
structural panels, Eq. E2.4.1.4-1 steel sheet, E3.4.4 / E6.4.1.4 mechanics for strap-braced and
other walls -- wall_line.wall_story_response) PLUS the chord strain from the overturning of the
stories above and the rigid-body ROTATION carried up from the stories below (chord axial strain
under the cumulative overturning + hold-down/rod elongation), so story drift is the difference of
the displacements at the top and bottom of the story (ASCE 7-22 12.8.6.5) -- line_response().
The spring stiffness is the effective secant V/drift. This IS the OpenSees model: emit_opensees()
builds the identical spring stack as zeroLength elements when openseespy is available
(pipeline/user machines); the pure-python solve here is the same physics, exact for a series
chain, and keeps the validator and tests independent of the binary.

Also here: seismic weights (per level), ELF (same ASCE 7-22 12.8 formulas as engine3d, CFS Ta
defaults), the two-stage podium procedure (12.2.3.2), drift checks vs cfs_systems.drift_limit,
the P-delta stability coefficient theta per story (12.8.7, Eqs. 12.8-18/12.8-19), and the
model-vs-tributary comparison GATE. Lines may be absent at some stories (breezeway / split
level / roof step): the shear is transferred to the present lines (wall_line.distribute), and a
story with no resisting line raises wall_line.NoResistingLineError.

NO capacities. cfg schema (feet/psf/kip at this level -- brief-facing):
cfg = dict(
  stories=4, heights_ft=[10.67, 9.5, 9.5, 9.5], plan_ft=(160.0, 62.0),
  D_floor=35.0, D_roof=20.0, clad=15.0, snow=0.0, L_floor=40.0,   # psf (L_floor -> Px, 12.8.7)
  seis=cfs_systems.seis_cfs(...), system="wsp_shearwall", risk_cat="II",
  lines_x=[wall_line.WallLine(...), ...],   # lines resisting X (E-W) force, positioned in y
  lines_y=[...],                            # lines resisting Y, positioned in x
  diaphragm="flexible",                     # or "semi-rigid" (Stage 2c: coupling elements)
  # S400 deflection inputs (wall_line.wall_story_response); per line via WallLine(wall_props=)
  # and per story via wall_props=dict(by_story={k: {...}}):
  wall_props=dict(sheathing="osb", s_in=4.0, t_stud_in=0.0451, t_sheathing_in=0.4375, faces=1,
                  chord_area_in2=1.2, rod_area_in2=0.6),   # or k_anchor_kip_in=50.0 (device)
  #   steel sheet: sheathing="steel_sheet", t_sheathing_in, Fy_ksi; strap: strap_area_in2;
  #   gypsum/other: Ga_kip_in (agent-grounded). Legacy dict(Gp_kip_in, en_in) still runs but the
  #   slip term then uses a conservative ASSUMED schedule (warned in preflight_warnings).
  analysis_fidelity=0,
  # optional: partition_psf=10.0 (12.7.2 weight, floors only, W only);
  # area_sf= / perimeter_ft= (TRUE values for T/U/L plans on a bounding-box model --
  #   pair with wall_line.fit_positions for line placement);
  # per-level: level_weights_kip / area_sf_by_level / perimeter_ft_by_level /
  #   roof_area_sf_by_level; diaphragm_extent_ft={("X", 1): (0.0, 35.0)};
  # theta_beta (12.8-19 beta, default 1.0); Px_level_kip;
  # per-line drift props / partial-depth lines / stepped foundations:
  #   WallLine(..., wall_props=..., trib_scale=..., base_story=...)
)
"""
import math
import wall_line as WL
import cfs_systems as CS

try:
    import openseespy.opensees as ops
    HAVE_OPS = True
except Exception:
    ops = None
    HAVE_OPS = False


# ---------------- weights & ELF ----------------

def _by_level(cfg, key, k):
    d = cfg.get(key)
    if not d:
        return None
    v = d.get(k, d.get(str(k)))
    return None if v is None else float(v)


def story_weight(cfg, k):
    """Seismic weight (kip) at level k (1..N, N = roof). Mirrors engine3d.floor_w: area dead +
    tributary cladding + 15% flat snow at the roof ONLY where pf > 45 psf (ASCE 7-22
    12.7.2 item 4; below 45 psf snow is excluded from W).
    Optional cfg keys (irregular T/U/L plans -- pair with wall_line.fit_positions):
      area_sf       -- TRUE floor area (default Lx*Ly) so W is exact on a bounding-box model
      perimeter_ft  -- TRUE cladding perimeter (default 2*(Lx+Ly))
      partition_psf -- partition weight in W at FLOOR levels only (ASCE 7-22 12.7.2,
                       >= 10 psf where partitions occur) -- keeps D_floor = true dead for
                       member design and the gravity stud stack
    Per-level geometry (roof steps / split levels -- CFS-09; ASCE 7-22 12.7.2 weight is per
    level):
      level_weights_kip      -- {level: W} hand-computed weight per level (overrides all below)
      area_sf_by_level       -- {level: sf} plan area at that level
      perimeter_ft_by_level  -- {level: ft} cladding perimeter for the story below that level
      roof_area_sf_by_level  -- {level: sf} part of a level's area that is ROOF (D_roof, roof
                                snow) -- a lower roof at a step; the top level is all roof
                                unless given here"""
    w_over = _by_level(cfg, "level_weights_kip", k)
    if w_over is not None:
        return w_over
    Lx, Ly = cfg["plan_ft"]
    A = _by_level(cfg, "area_sf_by_level", k) or cfg.get("area_sf") or (Lx * Ly)
    N = cfg["stories"]
    roof = (k == N)
    A_roof = _by_level(cfg, "roof_area_sf_by_level", k)
    if A_roof is None:
        A_roof = A if roof else 0.0
    A_roof = min(max(A_roof, 0.0), A)
    w = (cfg["D_roof"] * A_roof +
         (cfg["D_floor"] + cfg.get("partition_psf", 0.0)) * (A - A_roof)) / 1000.0
    per = _by_level(cfg, "perimeter_ft_by_level", k) or cfg.get("perimeter_ft") or \
        (2.0 * (Lx + Ly))
    h = cfg["heights_ft"][k - 1]
    w += cfg.get("clad", 0.0) * per * (h / 2.0 if roof else h) / 1000.0
    if A_roof > 0 and cfg.get("snow", 0.0) > 45.0:
        w += 0.15 * cfg["snow"] * A_roof / 1000.0  # 12.7.2 item 4: 15% of pf where pf > 45 psf
    return w


def gravity_Px_level(cfg, k):
    """Vertical design load (kip) delivered at level k for the 12.8.7 stability coefficient:
    ASCE 7-22 12.8.6.1 expected gravity 1.0D + 0.5L with L = 0.4 L0 (0.8 L0 where L0 > 100
    psf), no factor above 1.0. D = the level's seismic weight (story_weight: dead + partitions
    + cladding [+ roof snow where included in W]); L0 = cfg['L_floor'] over the level's floor
    (non-roof) area. cfg['Px_level_kip'] = {level: kip} overrides."""
    over = _by_level(cfg, "Px_level_kip", k)
    if over is not None:
        return over
    D = story_weight(cfg, k)
    L0 = float(cfg.get("L_floor", 0.0) or 0.0)
    if L0 <= 0:
        return D
    Lx, Ly = cfg["plan_ft"]
    A = _by_level(cfg, "area_sf_by_level", k) or cfg.get("area_sf") or (Lx * Ly)
    A_roof = _by_level(cfg, "roof_area_sf_by_level", k)
    if A_roof is None:
        A_roof = A if k == cfg["stories"] else 0.0
    fL = 0.8 if L0 > 100.0 else 0.4
    return D + 0.5 * fL * L0 * max(A - A_roof, 0.0) / 1000.0


def elf(cfg):
    """ASCE 7-22 12.8 ELF. Returns dict(V, Cs, Ta, T_used, k, Fx={level: kip}, W)."""
    s = cfg["seis"]
    N = cfg["stories"]
    W = sum(story_weight(cfg, k) for k in range(1, N + 1))
    hn = sum(cfg["heights_ft"])
    Ta = s["Ct"] * hn ** s["x"]
    T = min(cfg.get("T_analytical", Ta), s.get("Cu", 1.4) * Ta) if cfg.get("T_analytical") \
        else Ta
    R, Ie = s["R"], s["Ie"]
    Cs = s["SDS"] / (R / Ie)
    TL = s.get("TL", 8.0)
    cap = s["SD1"] / (T * (R / Ie)) if T <= TL else s["SD1"] * TL / (T ** 2 * (R / Ie))
    Cs = min(Cs, cap)
    cmin = max(0.044 * s["SDS"] * Ie, 0.01)
    if s.get("S1", 0) >= 0.6:
        cmin = max(cmin, 0.5 * s["S1"] / (R / Ie))
    Cs = max(Cs, cmin)
    V = Cs * W
    kk = 1.0 if T <= 0.5 else (2.0 if T >= 2.5 else 1.0 + (T - 0.5) / 2.0)
    z = [0.0]
    for h in cfg["heights_ft"]:
        z.append(z[-1] + h)
    whk = {k: story_weight(cfg, k) * z[k] ** kk for k in range(1, N + 1)}
    ss = sum(whk.values())
    return dict(V=V, Cs=Cs, Ta=Ta, T_used=T, k=kk, W=W,
                Fx={k: V * whk[k] / ss for k in range(1, N + 1)})


# ---------------- per-line spring model (the OpenSees-equivalent stack) ----------------

def wall_props_for(cfg, line, story):
    """Deflection inputs for one line at one story: WallLine.wall_props (or cfg['wall_props']);
    dict(by_story={k: {...}}) entries are merged over the line-level keys for that story
    (chord packs, rods, fastener spacing and faces change by story)."""
    wp = getattr(line, "wall_props", None) or cfg.get("wall_props") or {}
    if "by_story" not in wp:
        return wp
    base = {k: v for k, v in wp.items() if k != "by_story"}
    st = wp["by_story"].get(story, wp["by_story"].get(str(story)))
    if st is None and not base.get("chord_area_in2"):
        raise ValueError("line %s: wall_props.by_story has no entry for story %s"
                         % (line.name, story))
    base.update({k: v for k, v in (st or {}).items() if not str(k).startswith("_")})
    return base


def line_secant_stiffness(cfg, line, story, v_plf, T_kip):
    """Single-story secant stiffness (kip/in) of one wall line at unit shear v_plf: segments
    in parallel, each per the S400 deflection (wall_line.s400_deflection) WITHOUT the rotation
    carried up from the stories below (line_response adds that). None where the line is
    absent or v <= 0."""
    segs = line.segments.get(story) or []
    if not segs or v_plf <= 0:
        return None
    wp = wall_props_for(cfg, line, story)
    K = 0.0
    for (L, hseg) in segs:
        if not (L > 0 and hseg > 0):
            continue
        d = WL.s400_deflection(v_plf, hseg, L, wp, T_kip=T_kip, system=cfg.get("system"))
        K += (v_plf * L / 1000.0) / d["total"] if d["total"] > 0 else 0.0
    return K or None


_TERMS = ("bending", "shear", "slip", "anchorage", "strap", "ot_bending")


def line_response(cfg, line, Vstory):
    """Deflection of one wall line's stack under its STORY shears {story: V_kip} (absent
    stories must carry 0). Per present story (ASCE 7-22 12.8.6.5: story drift = difference of
    the displacements at the top and bottom of the story):

      drift_k = delta_own,k + h_k * theta_base,k
      delta_own,k = S400 single-story terms at v_k = V_k/L_k (wall_line.wall_story_response:
                    Eq. E1.4.1.4-1 / E2.4.1.4-1 / E3.4.4 / E6.4.1.4) + chord strain from the
                    overturning moment of the stories above (m_top = M_top/L)
      theta_base,k = sum over the CONTIGUOUS present stories j < k of the rotation each passes up:
                    2 (m_top,j h_j + v_j h_j^2/2)/(E Ac b) + delta_v,j / b   (chord tension +
                    compression strain under the CUMULATIVE overturning, and the hold-down /
                    rod elongation + take-up of that story)
    delta_v uses the cumulative tension T_k = M_base,k / L_k (no dead relief) and the actual
    rod (rod_area_in2: T h/(E A) + take-up) or device stiffness (k_anchor_kip_in).
    Segments act in parallel (secant K_i = V_i/delta_i at the line's unit shear); line-level
    terms and rotations are K-weighted. A discontinuity (line absent below) restarts the
    rotation chain: the wall above sits on a transfer element whose deformation is NOT
    included -- 'notes' says so. Returns {story: dict(...)} for PRESENT stories only."""
    H = cfg["heights_ft"]
    system = cfg.get("system")
    ks = sorted(Vstory)
    for k in ks:
        if not line.present(k) and abs(Vstory[k]) > 1e-9:
            raise ValueError("line %s carries %.3f kip at story %s where it has NO wall -- the "
                             "shear must be transferred (wall_line.distribute does this)"
                             % (line.name, Vstory[k], k))
    M_top, M_base = {}, {}
    M = 0.0
    for k in sorted(ks, reverse=True):
        if not line.present(k):
            M = 0.0
            continue
        M_top[k] = M
        M = M + abs(Vstory[k]) * H[k - 1]
        M_base[k] = M
    out = {}
    theta = 0.0
    chain_note = None
    for k in ks:
        if not line.present(k):
            if theta > 0:
                chain_note = ("rotation chain restarted above the discontinuity at story %s "
                              "(transfer element deformation NOT included)" % k)
            theta = 0.0
            continue
        V = abs(Vstory[k])
        L = line.length(k)
        h_in = H[k - 1] * 12.0
        v_plf = V * 1000.0 / L
        T = M_base[k] / L
        m_top = M_top[k] / L
        wp = wall_props_for(cfg, line, k)
        v_eval = max(v_plf, 1.0)
        Ksum, Kth = 0.0, 0.0
        terms = dict((t, 0.0) for t in _TERMS)
        assumed, notes, eqs = [], [], []
        for (b, hs) in line.segments[k]:
            if not (b > 0 and hs > 0):
                continue                 # zero-length entry (flagged by the presence lint)
            r = WL.wall_story_response(v_eval, hs, b, wp, T_kip=T, m_top_kip=m_top,
                                       system=system)
            Ki = (v_eval * b / 1000.0) / r["total"]
            Ksum += Ki
            Kth += Ki * r["dtheta_rad"]
            for t in _TERMS:
                terms[t] += Ki * r[t]
            for a in r["assumed"]:
                if a not in assumed:
                    assumed.append(a)
            for a in r["notes"]:
                if a not in notes:
                    notes.append(a)
            if r["equation"] not in eqs:
                eqs.append(r["equation"])
        terms = dict((t, terms[t] / Ksum) for t in _TERMS)
        dtheta = Kth / Ksum
        own = V / Ksum if V > 0 else 0.0
        if V <= 0:
            terms = dict((t, 0.0) for t in _TERMS)
        rot = theta * h_in
        drift = own + rot
        if chain_note and chain_note not in notes:
            notes.append(chain_note)
        terms["rotation_from_below"] = rot
        out[k] = dict(V=V, v_unit_plf=v_plf, K_own_kip_in=Ksum,
                      K_kip_in=(V / drift) if (V > 0 and drift > 0) else Ksum,
                      drift_in=drift, drift_own_in=own, drift_rot_in=rot,
                      theta_base_rad=theta, dtheta_rad=dtheta,
                      dr_ratio=drift / h_in, dr_ratio_own=own / h_in,
                      T_kip=T, M_top_kipft=M_top[k], terms=terms,
                      equation="; ".join(eqs), assumed=assumed, notes=notes)
        theta += dtheta
    return out


def analyze_line(cfg, line, story_shears, n_iter=3):
    """Solve one wall line's stack under its per-story shear INCREMENTS {story: V_kip} (the
    wall_line.distribute 'V_shifted' encoding; story shear = running sum from the top).
    Series chain: the story spring carries the story shear; the drift of each story includes
    the rigid-body rotation carried up from the stories below (line_response -- CFS-01).
    The S400 terms are explicit in v and T, so no iteration is needed (n_iter kept for
    signature compatibility). Returns {story: dict(V, v_unit_plf, K_kip_in, drift_in, dr_ratio,
    drift_own_in, drift_rot_in, T_kip, terms, ...)} for the stories where the line is PRESENT."""
    stories = sorted(story_shears)
    Vstory = {}
    run_ = 0.0
    for k in sorted(stories, reverse=True):
        run_ += story_shears[k]
        Vstory[k] = run_ if line.present(k) else 0.0
        if not line.present(k):
            if abs(run_) > 1e-6 * max(1.0, max(abs(x) for x in story_shears.values())):
                raise ValueError("line %s: %.3f kip arrives at story %s where the line has no "
                                 "wall -- distribute() must transfer it (or declare "
                                 "WallLine(base_story=...) for a stepped foundation)"
                                 % (line.name, run_, k))
            run_ = 0.0
    return line_response(cfg, line, Vstory)


def theta_max(cfg, story=None):
    """ASCE 7-22 Eq. 12.8-19: theta_max = 0.5/(beta Cd) <= 0.25, beta = shear demand/capacity
    ratio of the story (cfg['theta_beta'] scalar or {story: beta}; conservatively 1.0), beta
    not taken less than 1.25/Omega_0; theta_max need not be taken less than 0.10 (7-22)."""
    s = cfg["seis"]
    Cd = float(s["Cd"])
    Om0 = s.get("Om0")
    if Om0 is None and cfg.get("system") in CS.SYSTEMS:
        Om0 = CS.SYSTEMS[cfg["system"]]["Om0"]
    tb = cfg.get("theta_beta", 1.0)
    beta = float(tb.get(story, tb.get(str(story), 1.0)) if isinstance(tb, dict) else tb)
    if Om0:
        beta = max(beta, 1.25 / float(Om0))
    return max(min(0.5 / (beta * Cd), 0.25), 0.10)


def run(cfg):
    """Full light-frame run for BOTH directions: ELF -> tributary distribution (per-story line
    presence, transfers) -> per-line spring solve with the S400 deflection and the rotation
    carried up the stack -> drift screen (Cd*drift/Ie, cumulative, vs cfs_systems.drift_limit)
    -> P-delta stability coefficient theta per story (ASCE 7-22 12.8.7) -> comparison gate
    (spring-model line shears vs tributary -- identical by construction under 'flexible'; the
    gate becomes meaningful when the semi-rigid/OpenSees path replaces analyze_line).

    Optional cfg keys: diaphragm_extent_ft={(dir, level): (lo_ft, hi_ft)} (a level's
    diaphragm covers part of the strip -- split level / lower roof); theta_beta (12.8-19 beta);
    Px_level_kip; level_weights_kip / area_sf_by_level / perimeter_ft_by_level /
    roof_area_sf_by_level (story_weight). Raises wall_line.NoResistingLineError when a story has
    no wall line in a direction. Returns the result dict."""
    e = elf(cfg)
    res = dict(elf=e, directions={})
    warn = list(CS.preflight_fidelity(cfg.get("structure_kind", "wall"),
                                      cfg.get("analysis_fidelity", 0)) or [])
    dl = CS.drift_limit(cfg.get("system", "wsp_shearwall"), cfg["stories"],
                        cfg.get("risk_cat", "II"))
    Cd, Ie = cfg["seis"]["Cd"], cfg["seis"]["Ie"]
    N = cfg["stories"]
    semirigid = cfg.get("diaphragm", "flexible") == "semi-rigid"
    issues = []
    for dirn, key in (("X", "lines_x"), ("Y", "lines_y")):
        issues += WL.presence_issues(cfg[key], range(1, N + 1), dirn, fatal_only=True)
    if issues:
        raise WL.NoResistingLineError("wall-line presence lint failed: " + "; ".join(issues))
    # 12.8.7: Px and Vx per story (building totals; the line-level theta uses the same P/V)
    Px, Vx = {}, {}
    accP = accV = 0.0
    for k in range(N, 0, -1):
        accP += gravity_Px_level(cfg, k)
        accV += e["Fx"][k]
        Px[k], Vx[k] = accP, accV
    ext_all = cfg.get("diaphragm_extent_ft") or {}
    assumed_lines = []
    for dirn, lines, dim in (("X", cfg["lines_x"], cfg["plan_ft"][1]),
                             ("Y", cfg["lines_y"], cfg["plan_ft"][0])):
        ext = {}
        for kk, vv in ext_all.items():
            if isinstance(kk, (tuple, list)) and len(kk) == 2 and str(kk[0]) == dirn:
                ext[int(kk[1])] = vv
        # (extent kwarg only when declared: cfgs that wrap WL.distribute keep working)
        dist = WL.distribute(e["Fx"], lines, dim, extent_by_story=ext) if ext else \
            WL.distribute(e["Fx"], lines, dim)
        if semirigid:
            depth = cfg["plan_ft"][0] if dirn == "X" else cfg["plan_ft"][1]
            dres = analyze_semirigid(cfg, lines, dist, depth)
        else:
            dres = {}
            for ln in lines:
                shears = {k: dist[k][ln.name]["V_shifted"] for k in e["Fx"]}
                dres[ln.name] = analyze_line(cfg, ln, shears)
        # spring-model shears as per-story increments (absent stories carry 0 story shear)
        model_shears = {k: {} for k in e["Fx"]}
        for ln in lines:
            lr = dres[ln.name]
            for k in e["Fx"]:
                Sk = lr[k]["V"] if k in lr else 0.0
                Sa = lr[k + 1]["V"] if (k + 1) in lr else 0.0
                model_shears[k][ln.name] = Sk - Sa
        # drift screen (cumulative drift incl. carried rotation) + 12.8.7 theta per line/story
        drift_flags, stab_flags, stab_warn = [], [], []
        stability = {}
        for name, lr in dres.items():
            for k, r in lr.items():
                h_in = cfg["heights_ft"][k - 1] * 12.0
                th = (Px[k] / Vx[k]) * r["drift_in"] / h_in if Vx[k] > 0 else 0.0
                thm = theta_max(cfg, k)
                fac = 1.0
                if th > thm:
                    stab_flags.append(
                        "%s line %s story %d: theta = %.3f > theta_max = %.3f (ASCE 7-22 12.8.7, "
                        "Eq. 12.8-18/12.8-19) -- potentially unstable, REDESIGN (stiffen)"
                        % (dirn, name, k, th, thm))
                elif th > 0.10:
                    fac = 1.0 / (1.0 - th)
                    stab_warn.append(
                        "%s line %s story %d: theta = %.3f > 0.10 -- drift multiplied by "
                        "1/(1-theta) = %.3f (12.8.7); member forces need the same factor or a "
                        "rational P-delta analysis" % (dirn, name, k, th, fac))
                amp1 = Cd * r["dr_ratio"] / Ie
                amp = amp1 * fac
                r.update(drift_amplified=amp, drift_amplified_first_order=amp1,
                         drift_amplified_single_story=Cd * r["dr_ratio_own"] / Ie,
                         theta=th, theta_max=thm, pdelta_factor=fac,
                         Px_kip=Px[k], Vx_kip=Vx[k])
                if amp > dl:
                    drift_flags.append(
                        "%s line %s story %d: Cd*drift/Ie = %.4f > %.3f (cumulative: single-story "
                        "%.4f + rotation carried from below %.4f%s)"
                        % (dirn, name, k, amp, dl, r["drift_amplified_single_story"] * fac,
                           (amp1 - r["drift_amplified_single_story"]) * fac,
                           "; incl. P-delta 1/(1-theta)" if fac > 1.0 else ""))
                st = stability.get(k)
                if st is None or th > st["theta"]:
                    stability[k] = dict(theta=th, line=name, theta_max=thm, Px_kip=Px[k],
                                        Vx_kip=Vx[k], pdelta_factor=fac)
                if r["assumed"]:
                    assumed_lines.append("%s:%s s%d (%s)" % (dirn, name, k,
                                                             ", ".join(r["assumed"])))
        gate = WL.compare_with_model(dist, model_shears)
        transfers = []
        for k, row in dist.items():
            for nm, rr in row.items():
                if rr.get("transfer_out_kip"):
                    to = {}
                    for t, rt in row.items():
                        x = (rt.get("transfer_in") or {}).get(nm, 0.0)
                        if x:
                            to[t] = round(x, 2)
                    transfers.append(dict(
                        story=k, line=nm, V_kip=rr["transfer_out_kip"], to=to,
                        note=rr.get("transfer_note"),
                        basis="discontinuous wall line: the level-%d diaphragm/collector and the "
                              "elements supporting the wall above are designed for Omega_0 "
                              "(ASCE 7-22 12.3.3.3, Table 12.3-2 Type 4)" % k))
        res["directions"][dirn] = dict(
            dist=dist, lines=dres, drift_flags=drift_flags, gate_flags=gate, drift_limit=dl,
            stability=stability, stability_flags=stab_flags, stability_warnings=stab_warn,
            transfers=transfers,
            drift_basis="story drift = S400 single-story deflection (E1.4.1.4-1 / E2.4.1.4-1 / "
                        "E3.4.4 / E6.4.1.4) + chord strain from the overturning above + h x "
                        "rotation carried from the stories below (chord axial + hold-down/rod "
                        "elongation), amplified Cd/Ie (ASCE 7-22 12.8.6), x 1/(1-theta) where "
                        "0.10 < theta <= theta_max (12.8.7)",
            diaphragm="semi-rigid" if semirigid else "flexible")
    if assumed_lines:
        warn.append("S400 deflection evaluated with an ASSUMED conservative schedule (wall_props "
                    "lack the S400 inputs sheathing/s_in/t_stud_in/t_sheathing_in[/faces/Fy_ksi"
                    "/G]) on %d line-stories, e.g. %s -- declare the SELECTED schedule per line "
                    "(wall_props, by_story) and re-run" % (len(assumed_lines),
                                                          "; ".join(assumed_lines[:4])))
    if warn:
        res["preflight_warnings"] = warn
    return res


# ---------------- semi-rigid diaphragm (Stage 2c: coupling elements) ----------------

def _gauss(A, b):
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            raise RuntimeError("singular diaphragm system")
        M[col], M[piv] = M[piv], M[col]
        for r in range(col + 1, n):
            f = M[r][col] / M[col][col]
            for cc in range(col, n + 1):
                M[r][cc] -= f * M[col][cc]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (M[i][n] - sum(M[i][j] * x[j] for j in range(i + 1, n))) / M[i][i]
    return x


def analyze_semirigid(cfg, lines, dist, depth_ft, n_iter=3):
    """Stage 2c semi-rigid path: wall-line secant springs COUPLED by story-level diaphragm
    shear springs between adjacent lines (K_d = G'_dia x depth / gap). The solved system
    REDISTRIBUTES story forces by relative stiffness -- this is where the model-vs-tributary
    gate becomes a real check instead of an identity. cfg['diaphragm_G_kip_in'] = effective
    diaphragm shear stiffness G' (kip/in per ft of depth; agent grounds the value -- panel
    type/fastening -- in S240/manufacturer data and states it in the report).
    Wall springs are the EFFECTIVE secants of line_response (S400 deflection + rotation
    carried from below); a line absent at a story has no wall spring there (its level node is
    held by the diaphragm springs, which carry the transfer). Loads = the tributary LEVEL
    forces (F_level_shifted). Returns {line_name: {story: dict(V, v_unit_plf, K_kip_in,
    drift_in, dr_ratio, T_kip, ...)}} for present stories."""
    N = cfg["stories"]
    Gp = cfg.get("diaphragm_G_kip_in", 8.0)
    order = sorted(lines, key=lambda ln: ln.pos)
    stories = list(range(1, N + 1))
    idx = {(ln.name, k): li * N + (k - 1) for li, ln in enumerate(order) for k in stories}
    n = len(order) * N
    Kw = {}
    for ln in order:
        shears = {k: dist[k][ln.name]["V_shifted"] for k in stories}
        lr = analyze_line(cfg, ln, shears)
        for k in stories:
            Kw[(ln.name, k)] = lr[k]["K_kip_in"] if k in lr else 0.0
    out = None
    for _ in range(n_iter):
        K = [[0.0] * n for _ in range(n)]
        F = [0.0] * n
        for li, ln in enumerate(order):
            for k in stories:
                i = idx[(ln.name, k)]
                kw = Kw[(ln.name, k)]
                K[i][i] += kw
                if k > 1:
                    j = idx[(ln.name, k - 1)]
                    K[j][j] += kw
                    K[i][j] -= kw
                    K[j][i] -= kw
                F[i] += dist[k][ln.name].get("F_level_shifted",
                                             dist[k][ln.name]["V_shifted"])
        for a, b in zip(order, order[1:]):
            gap = abs(b.pos - a.pos)
            if gap < 1e-6:
                continue
            kd = Gp * depth_ft / gap
            for k in stories:
                i, j = idx[(a.name, k)], idx[(b.name, k)]
                K[i][i] += kd; K[j][j] += kd
                K[i][j] -= kd; K[j][i] -= kd
        u = _gauss(K, F)
        out = {}
        for ln in order:
            Vsp = {}
            for k in stories:
                du = u[idx[(ln.name, k)]] - (u[idx[(ln.name, k - 1)]] if k > 1 else 0.0)
                Vsp[k] = Kw[(ln.name, k)] * du if ln.present(k) else 0.0
            lr = line_response(cfg, ln, Vsp)
            for k, r in lr.items():
                if r["V"] > 0:
                    Kw[(ln.name, k)] = r["K_kip_in"]
                r["V"] = Vsp[k]                  # keep the sign of the solved spring shear
            out[ln.name] = lr
    return out


# ---------------- two-stage podium (ASCE 7-22 12.2.3.2) ----------------

def two_stage_check(K_upper_kip_in, K_lower_kip_in, T_upper_s, T_combined_s):
    """Eligibility: lower portion stiffness >= 10x upper; combined period <= 1.1x upper.
    Returns (eligible, messages)."""
    msgs = []
    ok1 = K_lower_kip_in >= 10.0 * K_upper_kip_in
    ok2 = T_combined_s <= 1.1 * T_upper_s
    if not ok1:
        msgs.append("lower/upper stiffness ratio %.1f < 10 -- two-stage NOT eligible"
                    % (K_lower_kip_in / max(K_upper_kip_in, 1e-9)))
    if not ok2:
        msgs.append("combined period %.3fs > 1.1x upper %.3fs -- two-stage NOT eligible"
                    % (T_combined_s, T_upper_s))
    return ok1 and ok2, msgs


def two_stage_reactions(V_upper, R_upper, rho_upper, R_lower, rho_lower):
    """Upper-portion base reactions amplified for the lower-portion design:
    factor = (R_upper/rho_upper)/(R_lower/rho_lower), not less than 1.0."""
    f = (R_upper / rho_upper) / (R_lower / rho_lower)
    return V_upper * max(f, 1.0), max(f, 1.0)


# ---------------- OpenSees emitter (pipeline path; guarded) ----------------

def emit_opensees(cfg, result, direction="X"):
    """Build the SAME calibrated spring stacks in openseespy (zeroLength + Elastic materials),
    one 2D stick per wall line, masses by tributary. Requires openseespy (pipeline/user env).
    Returns node/element counts for the smoke check."""
    if not HAVE_OPS:
        raise RuntimeError("openseespy not available here -- emit_opensees runs in the "
                           "pipeline/user environment (Gate 2)")
    lines = cfg["lines_x"] if direction == "X" else cfg["lines_y"]
    dres = result["directions"][direction]["lines"]
    ops.wipe(); ops.model("basic", "-ndm", 1, "-ndf", 1)
    nid = mid = eid = 0
    counts = dict(nodes=0, elements=0)
    node_map = {}                                   # line name -> {story: node tag}
    for ln in lines:
        nid += 1; base = nid; ops.node(base, 0.0); ops.fix(base, 1); counts["nodes"] += 1
        prev = base
        node_map[ln.name] = {}
        for k in sorted(dres[ln.name]):
            r = dres[ln.name][k]
            nid += 1; ops.node(nid, 0.0); counts["nodes"] += 1
            mid += 1; ops.uniaxialMaterial("Elastic", mid, r["K_kip_in"])
            eid += 1; ops.element("zeroLength", eid, prev, nid, "-mat", mid, "-dir", 1)
            counts["elements"] += 1
            node_map[ln.name][k] = nid
            prev = nid
    counts["node_map"] = node_map
    return counts


# ---------------- self-test ----------------

def _selftest():
    print("cfs_engine self-test (openseespy %s)" % ("available" if HAVE_OPS else "absent -- pure-python path"))
    segs = {k: [(20.0, 9.5), (10.0, 9.5)] for k in (1, 2, 3, 4)}
    segsB = {k: [(15.0, 9.5)] for k in (1, 2, 3, 4)}
    cfg = dict(stories=4, heights_ft=[10.67, 9.5, 9.5, 9.5], plan_ft=(160.0, 62.0),
               D_floor=35.0, D_roof=20.0, clad=15.0, snow=0.0,
               seis=CS.seis_cfs(1.0, 0.45, 0.45, "wsp_shearwall"),
               system="wsp_shearwall", risk_cat="II", structure_kind="wall",
               analysis_fidelity=0, diaphragm="flexible",
               lines_x=[WL.WallLine("X1", 0.0, segs), WL.WallLine("X2", 31.0, segsB),
                        WL.WallLine("X3", 62.0, segs)],
               lines_y=[WL.WallLine("Y1", 0.0, segsB), WL.WallLine("Y2", 80.0, segsB),
                        WL.WallLine("Y3", 160.0, segsB)],
               wall_props=dict(sheathing="osb", s_in=4.0, t_stud_in=0.0451,
                               t_sheathing_in=0.4375, Gt_lb_in=77500.0, faces=1,
                               chord_area_in2=1.2, k_anchor_kip_in=50.0))
    e = elf(cfg)
    assert e["W"] > 0 and abs(sum(e["Fx"].values()) - e["V"]) < 1e-9
    print("  ELF: W=%.0f kip, Cs=%.3f, V=%.1f kip, Ta=%.3fs" % (e["W"], e["Cs"], e["V"], e["Ta"]))
    # Ex1-like hand check: Cs = SDS/(R/Ie) = 1.0/6.5 = 0.154 unless the SD1 cap bites
    assert abs(e["Cs"] - min(1.0 / 6.5, 0.45 / (e["T_used"] * 6.5))) < 1e-6
    res = run(cfg)
    assert "preflight_warnings" not in res
    dx = res["directions"]["X"]
    assert dx["gate_flags"] == [], dx["gate_flags"]
    r11 = dx["lines"]["X1"][1]
    print("  X1 story1: Vcum=%.1f kip, v=%.0f plf, K=%.1f kip/in, Cd*dr/Ie=%.4f (limit %.3f)"
          % (r11["V"], r11["v_unit_plf"], r11["K_kip_in"], r11["drift_amplified"],
             dx["drift_limit"]))
    assert r11["V"] >= dx["lines"]["X1"][4]["V"], "cumulative shear must grow downward"
    assert dx["drift_limit"] == 0.025
    # two-stage machinery
    ok, msgs = two_stage_check(100.0, 1500.0, 0.4, 0.42)
    assert ok and not msgs
    ok, msgs = two_stage_check(100.0, 500.0, 0.4, 0.42)
    assert not ok and "NOT eligible" in msgs[0]
    Vamp, f = two_stage_reactions(100.0, 6.5, 1.0, 3.0, 1.0)
    assert abs(f - 6.5 / 3.0) < 1e-9 and abs(Vamp - 216.7) < 0.1
    print("  two-stage: eligibility + reaction amplification (f=%.2f) OK" % f)
    # Stage 2c: semi-rigid diaphragm redistribution + equilibrium + gate behavior
    cfg_sr = dict(cfg, diaphragm="semi-rigid", diaphragm_G_kip_in=1e-6)
    res_sr = run(cfg_sr)                                   # nearly-flexible: matches tributary
    dxs = res_sr["directions"]["X"]
    assert dxs["diaphragm"] == "semi-rigid"
    assert dxs["gate_flags"] == [], "G'~0 must reproduce the tributary distribution: %s" \
        % dxs["gate_flags"]
    cfg_sr2 = dict(cfg, diaphragm="semi-rigid", diaphragm_G_kip_in=500.0)
    res_sr2 = run(cfg_sr2)                                 # stiff: redistribution by stiffness
    dxs2 = res_sr2["directions"]["X"]
    Vtot1 = sum(dxs2["lines"][nm][1]["V"] for nm in ("X1", "X2", "X3"))
    Vapplied = sum(dxs2["dist"][k][nm]["V_shifted"] for k in (1, 2, 3, 4)
                   for nm in ("X1", "X2", "X3"))        # shifted envelope sums to ~1.05V
    assert abs(Vtot1 - Vapplied) / Vapplied < 1e-6, \
        "semi-rigid story-1 line shears must sum to the applied (shifted) base shear"
    trib_X2 = dxs2["dist"][1]["X2"]["V_shifted"] + dxs2["dist"][2]["X2"]["V_shifted"] + \
        dxs2["dist"][3]["X2"]["V_shifted"] + dxs2["dist"][4]["X2"]["V_shifted"]
    assert dxs2["lines"]["X2"][1]["V"] < trib_X2, \
        "stiff diaphragm must shed load AWAY from the weak line X2"
    assert dxs2["gate_flags"], "strong redistribution must trip the model-vs-tributary gate"
    print("  semi-rigid: G'~0 matches tributary (gate clean); stiff G' redistributes off the "
          "weak line and TRIPS the gate (%d flags) with equilibrium held" %
          len(dxs2["gate_flags"]))
    # tier misuse trips preflight
    cfg2 = dict(cfg, structure_kind="portal_singlechannel")
    res2 = run(cfg2)
    assert res2.get("preflight_warnings"), "single-channel portal @ Tier 0 must warn"
    print("  preflight: single-channel portal @ Tier 0 warns as required")
    if HAVE_OPS:
        c = emit_opensees(cfg, res, "X")
        print("  opensees emitter: %(nodes)d nodes / %(elements)d elements" % c)
        # solve the emitted model under the tributary story shears and compare story drifts
        # against the pure-python chain -- the dual-path equivalence check
        dist = res["directions"]["X"]["dist"]
        ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
        for ln in cfg["lines_x"]:
            for k, nd in c["node_map"][ln.name].items():
                ops.load(nd, dist[k][ln.name]["V_shifted"])
        ops.system("BandGeneral"); ops.numberer("RCM"); ops.constraints("Plain")
        ops.integrator("LoadControl", 1.0); ops.algorithm("Linear"); ops.analysis("Static")
        assert ops.analyze(1) == 0, "emitted model failed to solve"
        worst = 0.0
        for ln in cfg["lines_x"]:
            nm = c["node_map"][ln.name]
            prev_d = 0.0
            for k in sorted(nm):
                d = ops.nodeDisp(nm[k], 1)
                drift_ops = d - prev_d; prev_d = d
                drift_py = res["directions"]["X"]["lines"][ln.name][k]["drift_in"]
                if drift_py > 1e-9:
                    worst = max(worst, abs(drift_ops - drift_py) / drift_py)
        assert worst < 0.01, "OpenSees vs pure-python drift mismatch %.2f%%" % (worst * 100)
        print("  dual-path equivalence: OpenSees drifts match pure-python within %.3f%%"
              % (worst * 100))
    print("SELF-TEST PASS")


if __name__ == "__main__":
    _selftest()

"""
cfs_engine.py -- Stage 2a: the light-frame (wall-framed) analysis path.

Model: under the FLEXIBLE-diaphragm idealization each wall line is an independent vertical stack
of story shear springs, with story mass/force by tributary area (wall_line.py). Spring stiffness
is the SECANT of the S400-style four-term deflection at the line's unit shear -- iterated, since
slip and anchorage terms are load-dependent. This IS the OpenSees model: emit_opensees() builds
the identical spring stack as zeroLength elements when openseespy is available (pipeline/user
machines); the pure-python solve here is the same physics, exact for a series chain, and keeps
the validator and tests independent of the binary.

Also here: seismic weights, ELF (same ASCE 7-22 12.8 formulas as engine3d, CFS Ta defaults),
the two-stage podium procedure (12.2.3.2), drift checks vs cfs_systems.drift_limit, and the
model-vs-tributary comparison GATE.

NO capacities. cfg schema (feet/psf/kip at this level -- brief-facing):
cfg = dict(
  stories=4, heights_ft=[10.67, 9.5, 9.5, 9.5], plan_ft=(160.0, 62.0),
  D_floor=35.0, D_roof=20.0, clad=15.0, snow=0.0,          # psf
  seis=cfs_systems.seis_cfs(...), system="wsp_shearwall", risk_cat="II",
  lines_x=[wall_line.WallLine(...), ...],   # lines resisting X (E-W) force, positioned in y
  lines_y=[...],                            # lines resisting Y, positioned in x
  diaphragm="flexible",                     # or "semi-rigid" (Stage 2c: coupling elements)
  wall_props=dict(chord_area_in2=1.2, Gp_kip_in=9.0, en_in=0.03, k_anchor_kip_in=50.0),
  analysis_fidelity=0,
  # optional: partition_psf=10.0 (12.7.2 weight, floors only, W only);
  # area_sf= / perimeter_ft= (TRUE values for T/U/L plans on a bounding-box model --
  #   pair with wall_line.fit_positions for line placement);
  # per-line drift props / partial-depth lines: WallLine(..., wall_props=..., trib_scale=...)
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

def story_weight(cfg, k):
    """Seismic weight (kip) at level k (1..N, N = roof). Mirrors engine3d.floor_w: area dead +
    tributary cladding + 15% flat snow at the roof ONLY where pf > 45 psf (ASCE 7-22
    12.7.2 item 4; below 45 psf snow is excluded from W).
    Optional cfg keys (irregular T/U/L plans -- pair with wall_line.fit_positions):
      area_sf       -- TRUE floor area (default Lx*Ly) so W is exact on a bounding-box model
      perimeter_ft  -- TRUE cladding perimeter (default 2*(Lx+Ly))
      partition_psf -- partition weight in W at FLOOR levels only (ASCE 7-22 12.7.2,
                       >= 10 psf where partitions occur) -- keeps D_floor = true dead for
                       member design and the gravity stud stack"""
    Lx, Ly = cfg["plan_ft"]
    A = cfg.get("area_sf") or (Lx * Ly)
    N = cfg["stories"]
    roof = (k == N)
    d = cfg["D_roof"] if roof else (cfg["D_floor"] + cfg.get("partition_psf", 0.0))
    w = d * A / 1000.0
    per = cfg.get("perimeter_ft") or (2.0 * (Lx + Ly))
    h = cfg["heights_ft"][k - 1]
    w += cfg.get("clad", 0.0) * per * (h / 2.0 if roof else h) / 1000.0
    if roof and cfg.get("snow", 0.0) > 45.0:
        w += 0.15 * cfg["snow"] * A / 1000.0     # 12.7.2 item 4: 15% of pf where pf > 45 psf
    return w


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

def line_secant_stiffness(cfg, line, story, v_plf, T_kip):
    """Secant story stiffness (kip/in) of one wall line: K = V / delta(v). Uses the S400
    four-term deflection at the story's unit shear; wall segments on the line act in parallel.
    A per-line WallLine(..., wall_props=dict(...)) overrides cfg['wall_props'] so different
    sheathing/anchorage schedules drift correctly line-by-line."""
    wp = getattr(line, "wall_props", None) or cfg["wall_props"]
    h = cfg["heights_ft"][story - 1]
    segs = line.segments.get(story, [])
    if not segs or v_plf <= 0:
        return None
    K = 0.0
    for (L, hseg) in segs:
        d = WL.s400_deflection(v_plf, hseg, L, wp["chord_area_in2"], wp["Gp_kip_in"],
                               wp["en_in"], wp["k_anchor_kip_in"], T_kip)
        Vseg = v_plf * L / 1000.0
        K += Vseg / d["total"] if d["total"] > 0 else 0.0
    return K


def analyze_line(cfg, line, story_shears, n_iter=3):
    """Solve one wall line's spring stack under its story shears {story: V_kip}. Series chain:
    story drift = V_story_cum? No -- each story spring carries the shear of that story
    (sum of forces above), drift_k = Vk_cum / K_k. Iterates K on the deflection nonlinearity.
    Returns {story: dict(V, K_kip_in, drift_in, dr_ratio)}."""
    N = cfg["stories"]
    stories = sorted(story_shears)
    Vcum = {k: sum(story_shears[j] for j in stories if j >= k) for k in stories}
    ot = None
    out = {}
    v_prev = {k: 100.0 for k in stories}                       # plf seed
    for _ in range(n_iter):
        dist_like = {k: {line.name: dict(V_shifted=story_shears[k])} for k in stories}
        ot = WL.overturning_stack(line, dist_like, {k: cfg["heights_ft"][k - 1] for k in stories})
        for k in stories:
            L = max(line.length(k), 1e-6)
            v = Vcum[k] * 1000.0 / L
            v_prev[k] = v
            K = line_secant_stiffness(cfg, line, k, v, ot[k]["T_kip"])
            h_in = cfg["heights_ft"][k - 1] * 12.0
            dr = Vcum[k] / K if K else float("inf")
            out[k] = dict(V=Vcum[k], v_unit_plf=v, K_kip_in=K, drift_in=dr,
                          dr_ratio=dr / h_in, T_kip=ot[k]["T_kip"])
    return out


def run(cfg):
    """Full light-frame run for BOTH directions: ELF -> tributary distribution -> per-line
    spring solve -> drift screen -> comparison gate (spring-model line shears vs tributary --
    identical by construction under 'flexible'; the gate becomes meaningful when the
    semi-rigid/OpenSees path replaces analyze_line). Returns the result dict."""
    e = elf(cfg)
    res = dict(elf=e, directions={})
    warn = CS.preflight_fidelity(cfg.get("structure_kind", "wall"),
                                 cfg.get("analysis_fidelity", 0))
    if warn:
        res["preflight_warnings"] = warn
    dl = CS.drift_limit(cfg.get("system", "wsp_shearwall"), cfg["stories"],
                        cfg.get("risk_cat", "II"))
    Cd, Ie = cfg["seis"]["Cd"], cfg["seis"]["Ie"]
    semirigid = cfg.get("diaphragm", "flexible") == "semi-rigid"
    for dirn, lines, dim in (("X", cfg["lines_x"], cfg["plan_ft"][1]),
                             ("Y", cfg["lines_y"], cfg["plan_ft"][0])):
        dist = WL.distribute(e["Fx"], lines, dim)
        model_shears = {k: {} for k in e["Fx"]}
        if semirigid:
            depth = cfg["plan_ft"][0] if dirn == "X" else cfg["plan_ft"][1]
            dres = analyze_semirigid(cfg, lines, dist, depth)
            for ln in lines:
                for k in e["Fx"]:
                    model_shears[k][ln.name] = dres[ln.name][k]["V"] - \
                        dres[ln.name].get(k + 1, {}).get("V", 0.0)
        else:
            dres = {}
            for ln in lines:
                shears = {k: dist[k][ln.name]["V_shifted"] for k in e["Fx"]}
                lr = analyze_line(cfg, ln, shears)
                dres[ln.name] = lr
                for k in e["Fx"]:
                    model_shears[k][ln.name] = lr[k]["V"] - (
                        lr.get(k + 1, {}).get("V", 0.0)
                        if isinstance(lr.get(k + 1), dict) else 0.0)
        # drift screen: amplified Cd/Ie vs limit, per line per story
        drift_flags = []
        for name, lr in dres.items():
            for k, r in lr.items():
                amp = Cd * r["dr_ratio"] / Ie
                r["drift_amplified"] = amp
                if amp > dl:
                    drift_flags.append("%s line %s story %d: Cd*dr/Ie = %.4f > %.3f"
                                       % (dirn, name, k, amp, dl))
        gate = WL.compare_with_model(
            dist, {k: {ln.name: dres[ln.name][k]["V"] - (dres[ln.name][k + 1]["V"]
                       if (k + 1) in dres[ln.name] else 0.0) for ln in lines}
                   for k in e["Fx"]})
        res["directions"][dirn] = dict(dist=dist, lines=dres, drift_flags=drift_flags,
                                       gate_flags=gate, drift_limit=dl,
                                       diaphragm="semi-rigid" if semirigid else "flexible")
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
    Returns {line_name: {story: dict(V, v_unit_plf, K_kip_in, drift_in, dr_ratio, T_kip)}}."""
    N = cfg["stories"]
    Gp = cfg.get("diaphragm_G_kip_in", 8.0)
    order = sorted(lines, key=lambda ln: ln.pos)
    stories = list(range(1, N + 1))
    idx = {(ln.name, k): li * N + (k - 1) for li, ln in enumerate(order) for k in stories}
    n = len(order) * N
    # initial wall spring stiffnesses from the tributary state
    Kw = {}
    ot_by_line = {}
    for ln in order:
        shears = {k: dist[k][ln.name]["V_shifted"] for k in stories}
        lr = analyze_line(cfg, ln, shears)
        ot_by_line[ln.name] = lr
        for k in stories:
            Kw[(ln.name, k)] = lr[k]["K_kip_in"] or 1e-6
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
                F[i] += dist[k][ln.name]["V_shifted"]
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
        # recover wall spring shears, update secants
        out = {}
        for ln in order:
            Vsp = {}
            for k in stories:
                du = u[idx[(ln.name, k)]] - (u[idx[(ln.name, k - 1)]] if k > 1 else 0.0)
                Vsp[k] = Kw[(ln.name, k)] * du
            # per-story force introduced at each level (for OT/tension + the gate)
            lvl = {k: Vsp[k] - Vsp.get(k + 1, 0.0) for k in stories}
            dist_like = {k: {ln.name: dict(V_shifted=lvl[k])} for k in stories}
            ot = WL.overturning_stack(ln, dist_like,
                                      {k: cfg["heights_ft"][k - 1] for k in stories})
            res_line = {}
            for k in stories:
                L = max(ln.length(k), 1e-6)
                v = max(Vsp[k], 0.0) * 1000.0 / L
                Knew = line_secant_stiffness(cfg, ln, k, max(v, 1.0), ot[k]["T_kip"])
                if Knew:
                    Kw[(ln.name, k)] = Knew
                h_in = cfg["heights_ft"][k - 1] * 12.0
                dr = abs(Vsp[k]) / Kw[(ln.name, k)]
                res_line[k] = dict(V=Vsp[k], v_unit_plf=v, K_kip_in=Kw[(ln.name, k)],
                                   drift_in=dr, dr_ratio=dr / h_in, T_kip=ot[k]["T_kip"])
            out[ln.name] = res_line
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
               wall_props=dict(chord_area_in2=1.2, Gp_kip_in=9.0, en_in=0.03,
                               k_anchor_kip_in=50.0))
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

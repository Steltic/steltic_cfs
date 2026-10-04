"""
cfs_gates.py -- framework checks the CFS pipeline ADDS to the seeded package (no capacities).

Called by pipeline.design_and_report on every CFS wall-path run (pure python, engine-side):

  independent_tributary(cfg, res)  CFS-29(a). On a FLEXIBLE diaphragm the engine's
      "model-vs-tributary" gate compares the spring model with the very distribution that loaded
      it (an identity). This recomputes every line's story shear INDEPENDENTLY of wall_line --
      simple-span diaphragm tributary widths from the line POSITIONS (half-way to the adjacent
      lines, edge strips to the end lines), collinear lines split by sheathed length, the ELF
      story forces Fx -- and compares the engine's per-line shear with it. Expected ratio
      engine/independent is 1.00-1.05 (the 5 % accidental shift). Divergence means a geometry /
      distribution problem (coincident or mis-fitted positions, a declared trib_scale, a line
      with no wall at a story still carrying shear) that the agent must fix or justify.

  rayleigh_period(cfg, res)  CFS-32. Rayleigh period per direction from the solved spring
      stacks (secant stiffness at the ELF state): T = 2 pi sqrt(sum w d^2 / (g sum F d)).
      Reported beside Ta and Cu*Ta (ASCE 7-22 12.8.2: T used for ELF <= Cu*Ta; set
      cfg['T_analytical'] to use it) and n1 = 1/T for the wind gust-effect screen (26.11).

  two_stage(cfg, res, rho_upper)  CFS-32. ASCE 7-22 12.2.3.2 two-stage procedure for a CFS
      upper portion over a rigid podium: (a) K_lower >= 10 K_upper (K = V / delta_e at the top of
      the portion under the 12.8 ELF forces, portion fixed at its base), (b) T_entire <= 1.1
      T_upper, (d) upper reactions' Eh amplified by (R/rho)_upper / (R/rho)_lower >= 1.0,
      (f) upper height measured from its own base. Inputs the framework cannot know come from
      cfg['two_stage'] = dict(K_lower_kip_in=..., R_lower=..., rho_lower=..., and either
      T_combined_s=... or W_lower_kip=... [+ podium_height_ft] for a Rayleigh estimate). Missing
      data -> status 'NOT EVALUATED' (the consistency gate fails it), never a silent pass.
"""
import math

G_IN_S2 = 386.4


def _positions(lines):
    gs, order = {}, []
    for ln in sorted(lines, key=lambda l: float(l.pos)):
        key = round(float(ln.pos), 6)
        if key not in gs:
            gs[key] = []
            order.append(key)
        gs[key].append(ln)
    return order, gs


def _simple_span_widths(order, dim):
    """Simple-span diaphragm tributary width per unique line position (ft): half-way to each
    neighbour, plus the cantilever edge strip beyond the outermost lines."""
    w = {}
    for i, x in enumerate(order):
        lo = 0.0 if i == 0 else (x + order[i - 1]) / 2.0
        hi = dim if i == len(order) - 1 else (x + order[i + 1]) / 2.0
        w[x] = max(hi - lo, 0.0)
    return w


def _dir_lines(cfg):
    return (("X", cfg["lines_x"], float(cfg["plan_ft"][1])),
            ("Y", cfg["lines_y"], float(cfg["plan_ft"][0])))


def _present(ln, k):
    return max(ln.length(k), 0.0) > 0.0


def _ext_for(cfg, dirn):
    ext = {}
    for kk, vv in (cfg.get("diaphragm_extent_ft") or {}).items():
        if isinstance(kk, (tuple, list)) and len(kk) == 2 and str(kk[0]) == dirn:
            ext[int(kk[1])] = (float(vv[0]), float(vv[1]))
    return ext


def _independent_story_shears(lines, Fx, dim, ext):
    """Independent re-derivation of the per-line STORY shears (flexible diaphragm), coded here
    without wall_line: the level-k force goes to the lines PRESENT at story k (inside the
    level's diaphragm extent, if declared) by simple-span widths between adjacent present
    positions (collinear lines split by sheathed length); a line's story shear continues down
    while the line is present; where it is absent below, the level diaphragm carries it to the
    nearest present positions on either side by the lever rule (all to one side when there is
    none on the other) -- unless the line is founded there (WallLine.base_story). Returns
    ({line: {story: V}}, {story: transferred kip}, notes)."""
    N = max(Fx)
    V = {ln.name: {} for ln in lines}
    carry = {ln.name: 0.0 for ln in lines}
    transferred, notes = {}, []
    for k in sorted(Fx, reverse=True):
        lo, hi = ext.get(k, (0.0, float(dim)))
        pres = [ln for ln in lines if _present(ln, k)]
        act = [ln for ln in pres if lo - 1e-6 <= float(ln.pos) <= hi + 1e-6]
        order, gs = _positions(act)
        new = {ln.name: 0.0 for ln in lines}
        if order:
            wid = {}
            for i, x in enumerate(order):
                a_ = lo if i == 0 else (x + order[i - 1]) / 2.0
                b_ = hi if i == len(order) - 1 else (x + order[i + 1]) / 2.0
                wid[x] = max(b_ - a_, 0.0)
            tw = sum(wid.values()) or 1.0
            for x in order:
                Ls = [max(l.length(k), 0.0) for l in gs[x]]
                for l, L in zip(gs[x], Ls):
                    new[l.name] += float(Fx[k]) * wid[x] / tw * (L / sum(Ls))
        porder, pgs = _positions(pres)
        for ln in lines:
            c = carry[ln.name]
            if c <= 1e-12 or _present(ln, k):
                continue
            bs = getattr(ln, "base_story", None)
            if bs is not None and k < bs:
                carry[ln.name] = 0.0               # founded on its stepped foundation
                continue
            if not porder:
                continue
            xd = float(ln.pos)
            left = [x for x in porder if x < xd - 1e-6]
            right = [x for x in porder if x > xd + 1e-6]
            same = [x for x in porder if abs(x - xd) <= 1e-6]
            if same:
                tg = [(same[0], 1.0)]
            elif left and right:
                xl, xr = left[-1], right[0]
                fl = (xr - xd) / (xr - xl)
                tg = [(xl, fl), (xr, 1.0 - fl)]
            else:
                tg = [((left[-1] if left else right[0]), 1.0)]
                notes.append("%s absent at story %d: cantilever transfer to one side" % (ln.name, k))
            for x, f in tg:
                Ls = [max(l.length(k), 0.0) for l in pgs[x]]
                for l, L in zip(pgs[x], Ls):
                    new[l.name] += c * f * L / sum(Ls)
            transferred[k] = transferred.get(k, 0.0) + c
            carry[ln.name] = 0.0
        for ln in lines:
            if _present(ln, k):
                carry[ln.name] += new[ln.name]
                V[ln.name][k] = carry[ln.name]
            else:
                V[ln.name][k] = 0.0
    return V, transferred, notes


def independent_tributary(cfg, res, tol=0.05, shift=0.05):
    """Returns dict(method, by_direction={dir: {line: {story: dict(V_engine, V_independent,
    ratio)}}}, flags=[...]). Only meaningful (and only flagged) on FLEXIBLE diaphragms -- the
    semi-rigid path already compares a coupled solve against tributary in the engine gate.
    Per-story line presence (CFS-09) is followed independently: shear of a line that stops
    is transferred by the lever rule at that level (or founded, base_story), declared
    diaphragm extents limit a level's tributary, and each direction uses its own ELF
    (res['elf_by_dir'], 12.2.2)."""
    out = dict(method="independent simple-span tributary recomputation from line positions "
                      "(ASCE 7-22 12.3.1.3 flexible idealization; lever-rule transfer where a line "
                      "stops, 12.3.3.4), ELF Fx (12.8.3); expected engine/independent = "
                      "1.00-%.2f (5%% accidental shift)" % (1 + shift),
               by_direction={}, flags=[])
    for dirn, lines, dim in _dir_lines(cfg):
        dd = res["directions"].get(dirn)
        if not dd:
            continue
        Fx = ((res.get("elf_by_dir") or {}).get(dirn) or res["elf"])["Fx"]
        flexible = dd.get("diaphragm", "flexible") == "flexible"
        order, gs = _positions(lines)
        Vind_all, _tr, _notes = _independent_story_shears(lines, Fx, dim, _ext_for(cfg, dirn))
        tab = {}
        for ln in lines:
            grp = gs[round(float(ln.pos), 6)]
            tab[ln.name] = {}
            for k in sorted(Fx, reverse=True):
                Vind = Vind_all[ln.name][k]
                r = (dd["lines"].get(ln.name) or {}).get(k) or {}
                Ve = float(r.get("V", 0.0))
                ratio = (Ve / Vind) if Vind > 1e-9 else None
                tab[ln.name][k] = dict(V_engine=round(Ve, 2), V_independent=round(Vind, 2),
                                       ratio=round(ratio, 3) if ratio is not None else None)
                if not flexible:
                    continue
                if ln.length(k) <= 0 and Ve > 1e-6:
                    out["flags"].append(
                        "independent tributary: %s line %s story %d has NO wall but the engine "
                        "assigns it %.1f kip -- the story has no wall on this line; redistribute "
                        "(split the line / move the shear to the lines that exist)"
                        % (dirn, ln.name, k, Ve))
                elif ratio is not None and not (1.0 - tol <= ratio <= 1.0 + shift + tol):
                    why = []
                    if abs(getattr(ln, "trib_scale", 1.0) - 1.0) > 1e-9:
                        why.append("declared trib_scale=%.2f" % ln.trib_scale)
                    if len(grp) > 1:
                        why.append("collinear group at %.2f ft" % float(ln.pos))
                    out["flags"].append(
                        "independent tributary: %s line %s story %d: engine %.1f kip vs "
                        "independent simple-span tributary %.1f kip (ratio %.2f, expected "
                        "1.00-%.2f)%s -- fix the line geometry or justify the idealization"
                        % (dirn, ln.name, k, Ve, Vind, ratio, 1 + shift,
                           (" [" + "; ".join(why) + "]") if why else ""))
                elif ratio is None and Ve > 1e-6:
                    out["flags"].append(
                        "independent tributary: %s line %s story %d carries %.1f kip but has "
                        "zero independent tributary" % (dirn, ln.name, k, Ve))
        out["by_direction"][dirn] = tab
    return out


def _story_masses(cfg):
    try:
        import cfs_engine as CE
        return {k: CE.story_weight(cfg, k) for k in range(1, cfg["stories"] + 1)}
    except Exception:
        return None


def _line_top_disp(lr):
    """{story: cumulative elastic displacement (in)} of one line's spring stack."""
    d, acc = {}, 0.0
    for k in sorted(lr):
        acc += float(lr[k].get("drift_in", 0.0) or 0.0)
        d[k] = acc
    return d


def rayleigh_period(cfg, res):
    """{dir: dict(T_rayleigh_s, n1_hz, Ta_s, CuTa_s, T_used_s, note)} -- Rayleigh period of the
    solved spring stacks (secant stiffness at the ELF state; elastic delta_e, no Cd)."""
    e = res["elf"]
    Fx = e["Fx"]
    s = cfg["seis"]
    Cu = float(s.get("Cu", 1.4))
    out = {}
    for dirn, lines, _dim in _dir_lines(cfg):
        dd = res["directions"].get(dirn)
        if not dd:
            continue
        num = den = 0.0
        for ln in lines:
            lr = dd["lines"].get(ln.name) or {}
            disp = _line_top_disp(lr)
            for k in Fx:
                if k not in disp or Fx[k] <= 0:
                    continue
                Fl = dd["dist"][k][ln.name]["V_shifted"]          # the load the stack carried
                wl = Fl / Fx[k] * _w_level(cfg, e, k)               # tributary mass share
                num += wl * disp[k] ** 2
                den += Fl * disp[k]
        if den <= 0:
            continue
        T = 2 * math.pi * math.sqrt(num / (G_IN_S2 * den))
        out[dirn] = dict(T_rayleigh_s=round(T, 3), n1_hz=round(1.0 / T, 3) if T > 0 else None,
                         Ta_s=round(e["Ta"], 3), CuTa_s=round(Cu * e["Ta"], 3),
                         T_used_s=round(e.get("T_used", e["Ta"]), 3),
                         note="ASCE 7-22 12.8.2: ELF may use T = min(T_rayleigh, Cu*Ta) -- set "
                              "cfg['T_analytical'] to adopt it (the engine applies the Cu*Ta cap); "
                              "n1 < 1 Hz -> flexible for the wind gust-effect factor (26.11)")
    return out


def _w_level(cfg, e, k):
    ws = _story_masses(cfg)
    if ws:
        return ws[k]
    return e["W"] * e["Fx"][k] / max(e["V"], 1e-9)


def _per_dir(v, dirn):
    if isinstance(v, dict):
        return v.get(dirn)
    return v


def two_stage(cfg, res, rho_upper, period=None):
    """ASCE 7-22 12.2.3.2 block for pkg['two_stage_framework'] (see module docstring)."""
    ts = cfg.get("two_stage") or {}
    s = cfg["seis"]
    e = res["elf"]
    R_u = float(s["R"])
    R_l, rho_l = ts.get("R_lower"), ts.get("rho_lower", 1.0)
    period = period if period is not None else rayleigh_period(cfg, res)
    blk = dict(procedure="ASCE 7-22 12.2.3.2 two-stage ELF (framework-evaluated)",
               R_upper=R_u, rho_upper=rho_upper, R_lower=R_l, rho_lower=rho_l,
               lower_system=ts.get("lower_system"), by_direction={}, reactions_to_podium=[],
               upper_height_ft=round(sum(cfg["heights_ft"]), 2),
               notes=["(c)/(e): upper portion designed as a separate structure with its own "
                      "R/rho by ELF (this run); lower portion by ELF with its own R/rho -- agent/"
                      "podium EOR", "(f): the height limit of Table 12.2-1 is checked on the "
                      "upper portion measured from its base (the model base = podium top)"])
    if R_l:
        f = (R_u / float(rho_upper)) / (float(R_l) / float(rho_l or 1.0))
        blk["amplification_raw"] = round(f, 4)
        blk["amplification"] = round(max(f, 1.0), 4)
        blk["amplification_basis"] = ("12.2.3.2(d): Eh of the upper-portion reactions x "
                                      "(R/rho)_upper / (R/rho)_lower = (%.2f/%.2f)/(%.2f/%.2f) = "
                                      "%.3f, not less than 1.0" % (R_u, rho_upper, float(R_l),
                                                                    float(rho_l or 1.0), f))
    else:
        blk["amplification"] = None
        blk["amplification_basis"] = "NOT EVALUATED: cfg['two_stage']['R_lower'] missing"
    if ts.get("irregular_transition"):
        blk["notes"].append("(g): Horizontal Type 4 / Vertical Type 3 irregularity at the "
                            "transition declared -- ALSO amplify per 12.3.3.4, 12.10.1.1, "
                            "12.10.3.3")
    for dirn, lines, _dim in _dir_lines(cfg):
        dd = res["directions"].get(dirn)
        if not dd:
            continue
        msgs = []
        # (a) K_upper = V / delta_e(top), base-shear-weighted mean roof displacement of the lines
        num = den = 0.0
        for ln in lines:
            lr = dd["lines"].get(ln.name) or {}
            if not lr:
                continue
            disp = _line_top_disp(lr)
            Vb = abs(float(lr[min(lr)]["V"]))
            num += Vb * disp[max(disp)]
            den += Vb
        d_top = num / den if den > 0 else None
        K_u = (e["V"] / d_top) if d_top else None
        K_l = _per_dir(ts.get("K_lower_kip_in"), dirn)
        T_u = (period.get(dirn) or {}).get("T_rayleigh_s")
        T_c = _per_dir(ts.get("T_combined_s"), dirn)
        T_c_basis = "cfg['two_stage']['T_combined_s']" if T_c is not None else None
        if T_c is None and K_l and ts.get("W_lower_kip") and T_u:
            T_c = _combined_rayleigh(cfg, res, dd, lines, float(K_l), float(ts["W_lower_kip"]),
                                     float(ts.get("podium_height_ft", 0.0) or 0.0))
            T_c_basis = ("Rayleigh estimate: upper stacks on a podium shear spring K_lower with "
                         "mass W_lower (framework)")
        d = dict(V_upper_kip=round(e["V"], 2), delta_top_in=round(d_top, 4) if d_top else None,
                 K_upper_kip_in=round(K_u, 1) if K_u else None,
                 K_lower_kip_in=K_l, T_upper_s=T_u, T_combined_s=T_c, T_combined_basis=T_c_basis)
        ok_a = ok_b = None
        if K_u and K_l:
            d["stiffness_ratio"] = round(float(K_l) / K_u, 2)
            ok_a = float(K_l) >= 10.0 * K_u
            if not ok_a:
                msgs.append("(a) K_lower/K_upper = %.1f < 10 -- NOT eligible" % (float(K_l) / K_u))
        else:
            msgs.append("(a) NOT EVALUATED: supply cfg['two_stage']['K_lower_kip_in'] (podium "
                        "V/delta_e at its top incl. the amplified upper reactions)")
        if T_u and T_c:
            d["period_ratio"] = round(float(T_c) / T_u, 3)
            ok_b = float(T_c) <= 1.1 * T_u
            if not ok_b:
                msgs.append("(b) T_entire/T_upper = %.3f > 1.1 -- NOT eligible"
                            % (float(T_c) / T_u))
        else:
            msgs.append("(b) NOT EVALUATED: supply cfg['two_stage']['T_combined_s'] (or "
                        "W_lower_kip for the framework Rayleigh estimate)")
        if blk["amplification"] is None:
            msgs.append("(d) NOT EVALUATED: R_lower missing")
        if ok_a is False or ok_b is False:
            d["status"] = "NOT ELIGIBLE"
        elif ok_a and ok_b and blk["amplification"] is not None:
            d["status"] = "ELIGIBLE"
        else:
            d["status"] = "NOT EVALUATED"
        d["messages"] = msgs
        blk["by_direction"][dirn] = d
        # reactions handed to the podium: base shear and overturning tension per line, the
        # upper portion's rho-included Eh x amplification
        amp = blk["amplification"]
        for ln in lines:
            lr = dd["lines"].get(ln.name) or {}
            if not lr:
                continue
            b = lr[min(lr)]
            V = abs(float(b["V"])) * float(rho_upper)
            T = float(b.get("T_kip", 0.0)) * float(rho_upper)
            blk["reactions_to_podium"].append(dict(
                direction=dirn, line=ln.name, V_Eh_kip=round(V, 1), T_Eh_kip=round(T, 1),
                V_Eh_amplified_kip=round(V * amp, 1) if amp else None,
                T_Eh_amplified_kip=round(T * amp, 1) if amp else None))
    return blk


def _combined_rayleigh(cfg, res, dd, lines, K_l, W_l, h_p):
    """Rayleigh period of the ENTIRE structure (one direction): every upper level displaced by
    its stack displacement plus the podium-top displacement d_p = (V_upper + F_p)/K_lower; the
    podium mass W_lower carries an ELF-pattern force F_p (k = 1) at h_p/2."""
    e = res["elf"]
    Fx = e["Fx"]
    W_u = e["W"]
    z = [h_p]
    for h in cfg["heights_ft"]:
        z.append(z[-1] + h)
    sum_wz = sum(_w_level(cfg, e, k) * z[k] for k in Fx)
    F_p = e["V"] * (W_l * (h_p / 2.0)) / max(sum_wz, 1e-9) if h_p > 0 else 0.0
    d_p = (e["V"] + F_p) / K_l
    num = W_l * (d_p / 2.0) ** 2           # podium mass at mid-height ~ half its top displacement
    den = F_p * (d_p / 2.0)
    for ln in lines:
        lr = dd["lines"].get(ln.name) or {}
        disp = _line_top_disp(lr)
        for k in Fx:
            if k not in disp or Fx[k] <= 0:
                continue
            Fl = dd["dist"][k][ln.name]["V_shifted"]
            wl = Fl / Fx[k] * _w_level(cfg, e, k)
            dk = disp[k] + d_p
            num += wl * dk ** 2
            den += Fl * dk
    if den <= 0:
        return None
    return round(2 * math.pi * math.sqrt(num / (G_IN_S2 * den)), 3)

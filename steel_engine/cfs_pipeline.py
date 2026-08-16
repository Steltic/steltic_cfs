"""
cfs_pipeline.py -- Stage 3: CFS demand package + schedule seeds (NO capacities).

Consumes cfs_engine.run() and writes the CFS calc_package: per-wall-line shear slots (unit shear
demand, sheathing/fastener selection = agent), per-line hold-down/chord slots (cumulative tension,
device class from the envelope bands, rod switch flagged), drift table, collector seeds at
declared re-entrant/step lines, and stud-schedule seed rows (cumulative axial by story = agent's
web-crippling/interaction inputs). The agent fills limit_state/cited/capacity/DC exactly as in
the hot-rolled contract. LRFD only (scope decision #3).
"""
import json, math
import wall_line as WL
import cfs_systems as CS
import cfs_engine as CE

COMBOS_NOTE = ("ASCE 7-22 2.3 LRFD; seismic cases carry (1.2+0.2SDS)D and 0.9-0.2SDS "
               "counteracting; NET-UPLIFT case 0.9D+1.0W is a REQUIRED anchorage case")

# S400-20 Table A3.2-1 expected-strength factors (ASTM A1003 sheet grades)
RY_BY_FY = {33: 1.5, 50: 1.1}
RT_BY_FY = {33: 1.2, 50: 1.1}

# R>3 wall systems (from cfs_systems.SYSTEMS) that get NUMERIC capacity-design seeds:
# chords/hold-downs/anchorage are designed for expected strength capped at Omega_0-level,
# never the raw ELF seed (S400 E1/E2/E3).
CD_WALL_SYSTEMS = ("strap_braced", "wsp_shearwall", "steelsheet_wall")


def _rho_seismic(cfg):
    """Numeric redundancy factor rho (ASCE 7-22 12.3.4): 1.3 by default in SDC D/E/F
    (canonical cfs_systems.sdc from SDS/SD1/S1 + risk category), 1.0 in SDC B/C.
    cfg['rho'] overrides (e.g. the 12.3.4.2 conditions are met -> 1.0).
    rho multiplies STRENGTH-design E only -- never drift (12.3.4.1 item 2), diaphragm
    Fpx (item 7), or Omega_0/expected-strength capacity-design quantities (item 5)."""
    if cfg.get("rho") is not None:
        return float(cfg["rho"])
    s = cfg["seis"]
    cat = CS.sdc(s.get("SDS", 0.0), s.get("SD1", 0.0), s.get("S1", 0.0),
                 cfg.get("risk_cat", "II"))
    return 1.3 if cat in ("D", "E", "F") else 1.0


def _om0_of(cfg):
    """Omega_0 from cfg['seis'], else the declared system's Table 12.2-1 value.
    NO silent fallback -- there is no generic CFS Omega_0 default."""
    s = cfg["seis"]
    if s.get("Om0") is not None:
        return float(s["Om0"])
    sysname = cfg.get("system")
    if sysname in CS.SYSTEMS:
        return float(CS.SYSTEMS[sysname]["Om0"])
    raise KeyError("cfg['seis']['Om0'] missing and cfg['system']=%r is not in the CFS "
                   "system table -- declare Omega_0 from ASCE 7-22 Table 12.2-1 "
                   "(no default exists)" % (sysname,))


def _om0_eff(cfg):
    """(Om0_eff, Om0, basis note). Table 12.2-1 footnote b: where Om0 >= 2.5, Om0 is
    permitted to be reduced by 0.5 for structures with FLEXIBLE diaphragms."""
    Om0 = _om0_of(cfg)
    flex = str(cfg.get("diaphragm", "flexible")).lower() == "flexible"
    if flex and Om0 >= 2.5:
        return Om0 - 0.5, Om0, ("Om0_eff = Om0 - 0.5 = %.2f (Table 12.2-1 footnote b: "
                                "flexible diaphragm, Om0 >= 2.5)" % (Om0 - 0.5))
    return Om0, Om0, "Om0_eff = Om0 = %.2f (footnote b reduction not applicable)" % Om0


def _chord_dead_relief(cfg, line, dirn, w_bay_ft):
    """Dead-load relief AVAILABLE at a chord/hold-down (kip) -- NOT taken in the T_cd seed:
    (0.9 - 0.2*SDS) * D_chord_trib, with the EXPLICIT chord tributary = half-bay
    (w_bay/2 along the wall) x half joist span, D summed over the stories above.
    Joist span = cfg['joist_span_ft'] where declared, else the largest spacing to an
    adjacent lateral line in the same direction group (documented surrogate).
    Returns (kip or None, note)."""
    SDS = cfg["seis"]["SDS"]
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
        D = cfg["D_roof"] if k == N else cfg["D_floor"]
        D_trib += D * (w_bay_ft / 2.0) * (span / 2.0) / 1000.0
    return round((0.9 - 0.2 * SDS) * D_trib, 2), \
        ("(0.9-0.2*SDS)*D_chord_trib; chord trib = half-bay (%.1f ft) x half joist span "
         "(%.1f ft, %s), D summed over %d stories" % (w_bay_ft / 2.0, span / 2.0, how, N))


# ---------------- Stage 3b: wall-path wind machinery + LRFD combo enumeration ----------------

def wind_story_forces(cfg, direction):
    """SEEDED MWFRS story forces (kip) on the box building for one direction, from
    cfg['wind'] = dict(V=mph, exposure=...). Net wall coefficient G*(Cp_ww - Cp_lw) =
    0.85*(0.8+0.5) on the projected face, qz stepped per story (Kz at story top). SEEDS --
    the agent verifies/replaces pressures per ASCE 7 Ch. 27/28 before capacity checks.
    Returns (forces {story: kip}, basis dict)."""
    w = cfg.get("wind")
    if not w:
        return None, None
    import cfs_frame as CF
    B = cfg["plan_ft"][1] if direction == "X" else cfg["plan_ft"][0]   # face width normal to wind
    # cfg['wind']['Cnet'] overrides the enclosed-box net wall coefficient (0.8+0.5) --
    # REQUIRED for partially enclosed / open-front wall briefs, where +/-0.55 internal
    # pressure cannot be represented by the enclosed default
    Cnet, G, Kd = w.get("Cnet", 1.3), 0.85, 0.85
    Kzt, Ke = w.get("Kzt", 1.0), w.get("Ke", 1.0)
    z = 0.0
    forces = {}
    N = cfg["stories"]
    for k in range(1, N + 1):
        h = cfg["heights_ft"][k - 1]
        trib = h / 2.0 + (cfg["heights_ft"][k] / 2.0 if k < N else 0.0)
        z += h
        # ASCE 7-22 Eq. 26.10-1: qz = 0.00256*Kz*Kzt*Ke*V^2; Kd applied at the pressure
        # equation (Eq. 27.3-1) -- folded into the same product (numerically identical)
        qz = 0.00256 * CF._kzf(z, w.get("exposure", "C")) * Kzt * Ke * Kd * w["V"] ** 2
        forces[k] = qz * G * Cnet * B * trib / 1000.0
    basis = dict(V_mph=w["V"], exposure=w.get("exposure", "C"),
                 note="SEED: MWFRS net wall coeff G*(0.8+0.5)=%.2f; qz = 0.00256*Kz*Kzt*Ke*"
                      "V^2 (Eq. 26.10-1, Kzt=%.2f Ke=%.2f) with Kd=0.85 applied at the "
                      "pressure eq. (27.3-1), qz stepped; agent verifies per ASCE 7 Ch. 27"
                      % (G * 1.3, Kzt, Ke))
    return forces, basis


def enumerate_combos(cfg):
    """The LRFD combo LIST for the wall path (data, not solves -- the spring model is linear
    per direction, so combos scale/pair the E and W distributions). Seismic carries
    (1.2+0.2SDS)/(0.9-0.2SDS) vertical pairing and rho; wind carries the 1.0W pair incl.
    the REQUIRED 0.9D+1.0W net-uplift anchorage case."""
    s = cfg["seis"]
    SDS = s["SDS"]
    rho = _rho_seismic(cfg)          # 12.3.4 via canonical SDC; cfg['rho'] overrides
    # 7-22 2.3.1: S principal carries 1.0 (was 1.6); companion S is 0.3 (was 0.5).
    # Lr KEEPS 1.6 principal / 0.5 companion -- the 7-22 factor change is snow-only.
    out = [dict(label="1.4D", D=1.4),
           dict(label="1.2D+1.6L+(0.5Lr or 0.3S)", D=1.2, L=1.6, Lr=0.5, S=0.3),
           dict(label="1.2D+(1.6Lr or 1.0S)+L", D=1.2, L=1.0, Lr=1.6, S=1.0)]
    for dirn in ("X", "Y"):
        for sgn in ("+", "-"):
            # 7-22 2.3.6 combo 6: snow companion with seismic is 0.15S (was 0.2S)
            out.append(dict(label="(1.2+0.2SDS)D+rho*E%s%s+L+0.15S" % (dirn, sgn),
                            D=1.2 + 0.2 * SDS, L=0.5, S=0.15, E=rho, dir=dirn, sign=sgn))
            out.append(dict(label="(0.9-0.2SDS)D+rho*E%s%s" % (dirn, sgn),
                            D=0.9 - 0.2 * SDS, E=rho, dir=dirn, sign=sgn,
                            role="uplift/counteracting"))
    if cfg.get("wind"):
        for dirn in ("X", "Y"):
            for sgn in ("+", "-"):
                out.append(dict(label="1.2D+1.0W%s%s+L+(0.5Lr or 0.3S)" % (dirn, sgn),
                                D=1.2, L=1.0, Lr=0.5, S=0.3, W=1.0, dir=dirn, sign=sgn))
                out.append(dict(label="0.9D+1.0W%s%s" % (dirn, sgn), D=0.9, W=1.0,
                                dir=dirn, sign=sgn, role="NET UPLIFT anchorage case"))
    Om0 = _om0_of(cfg)               # NO 2.5 fallback -- table value or explicit error
    # 2.3.6 overstrength pair (combos 6 & 7 with Emh): BOTH the additive and the
    # counteracting case are REQUIRED for collectors/transfer/anchorage (rho NOT applied
    # to Omega_0-level E, 12.3.4.1 item 5)
    out.append(dict(label="overstrength: (1.2+0.2SDS)D+Om0*E (2.3.6 combo 6; 12.10.2.1)",
                    D=1.2 + 0.2 * SDS, E=Om0, role="collectors/transfer/anchorage uplift"))
    out.append(dict(label="overstrength counteracting: (0.9-0.2SDS)D+Om0*E (2.3.6 combo 7)",
                    D=0.9 - 0.2 * SDS, E=Om0, role="collectors/transfer/anchorage uplift"))
    return out


def _wind_line_screen(cfg, res):
    """Per-direction wind distribution through the SAME tributary machinery as seismic;
    returns {dirn: {line: {story: v_wind_plf}}} + per-line wind base tension, or None."""
    if not cfg.get("wind"):
        return None
    out = {}
    for dirn, lines, dim in (("X", cfg["lines_x"], cfg["plan_ft"][1]),
                             ("Y", cfg["lines_y"], cfg["plan_ft"][0])):
        wf, basis = wind_story_forces(cfg, dirn)
        dist = WL.distribute(wf, lines, dim)
        d = {}
        for ln in lines:
            stack = {}
            Vc = 0.0
            for k in sorted(wf, reverse=True):
                Vc += dist[k][ln.name]["V_shifted"]
                L = max(ln.length(k), 1e-6)
                stack[k] = round(Vc * 1000.0 / L, 0)
            ot = WL.overturning_stack(ln, dist, {k: cfg["heights_ft"][k - 1] for k in wf})
            d[ln.name] = dict(v_plf=stack, T_base_kip=round(ot[min(ot)]["T_kip"], 1))
        basis = dict(basis, uplift_note="T_base takes NO dead-load relief (conservative "
                     "seed envelope) -- agent may refine with 0.9D in the package")
        out[dirn] = dict(lines=d, basis=basis)
    return out


def stud_axial_stack(cfg, trib_ft, spacing_in=16.0):
    """Cumulative factored axial (kip) per stud by story for a bearing wall with tributary
    trib_ft of floor each side sum. 1.2D+1.6L seed (agent refines with live reduction)."""
    N = cfg["stories"]
    out = {}
    P = 0.0
    for k in range(N, 0, -1):
        roof = (k == N)
        D = cfg["D_roof"] if roof else cfg["D_floor"]
        L = 0.0 if roof else cfg.get("L_floor", 40.0)
        w = (1.2 * D + 1.6 * L) / 1000.0 * trib_ft * (spacing_in / 12.0)
        P += w
        out[k] = round(P, 2)
    return out


def build_package(name, cfg, res):
    """The CFS calc_package dict (agent fills capacities). Seeds refuse silence: every wall
    line in every story gets a slot; hold-down slots carry the rod-switch feasibility note."""
    e = res["elf"]
    sysname = cfg.get("system", "wsp_shearwall")
    rho = _rho_seismic(cfg)
    pkg = dict(building=name, code="AISI S100-16(R2020)+S2/S3, S240-20, S400-20 -- LRFD",
               system=sysname, combos_note=COMBOS_NOTE,
               combos=enumerate_combos(cfg),
               rho=rho,
               rho_basis="ASCE 7-22 12.3.4 (canonical SDC); rho=%.2f is MULTIPLIED into the "
                         "seeded strength demands below (v_unit_plf, V_kip, T_cum/T_bay, "
                         "wind-vs-seismic comparison); NOT applied to drift (12.3.4.1 item "
                         "2), diaphragm Fpx (item 7), or Omega_0/expected-strength "
                         "capacity-design seeds (item 5)" % rho,
               elf=dict(V_kip=round(e["V"], 1), Cs=round(e["Cs"], 4), W_kip=round(e["W"], 0),
                        Ta_s=round(e["Ta"], 3),
                        note="pure ELF (no rho); per-slot seeds below already include rho"),
               wall_lines=[], holddowns=[], studs=[], collectors=[], drift_table=[],
               preflight_warnings=res.get("preflight_warnings", []))
    wind = _wind_line_screen(cfg, res)
    if wind:
        pkg["wind_basis"] = wind["X"]["basis"]
    if sysname in ("strap_braced", "sbmf"):
        pkg["capacity_design"] = dict(
            basis="S400 %s: connections, chord studs and anchorage on every braced line are "
                  "designed for the EXPECTED strength of the selected strap/beam -- "
                  "Ry*Fy*Ag (never the ELF force alone)"
                  % ("E3.3" if sysname == "strap_braced" else "E4.3"),
            Ry_by_Fy=RY_BY_FY, Rt_by_Fy=RT_BY_FY,
            note="AGENT: after selecting the strap (Ag, Fy grade), propagate Ry*Fy*Ag into "
                 "the connection, chord-stud and hold-down slots as the demand basis")
    elif sysname in ("wsp_shearwall", "steelsheet_wall"):
        pkg["capacity_design"] = dict(
            basis="%s: chord studs, hold-downs and anchorage on every shear-wall line are "
                  "designed for the EXPECTED strength of the SELECTED sheathing/fastener "
                  "assembly, capped at the Omega_0-level force -- never the ELF force alone"
                  % CS.SYSTEMS[sysname]["std"],
            note="AGENT: after selecting sheathing + fasteners, propagate the expected wall "
                 "strength (capped at Omega_0-level, see the numeric seeds below) into the "
                 "chord-stud, hold-down and anchorage slots as the demand basis")
    if sysname in CD_WALL_SYSTEMS:
        Om0e, Om0_tab, om0_note = _om0_eff(cfg)
        pkg["capacity_design"].update(
            Om0=Om0_tab, Om0_eff=Om0e, Om0_eff_basis=om0_note,
            lines={},
            seed_basis="T_cd_seed = Omega0-level stack, no dead relief; final demand = "
                       "min(Omega0-level, Omega_E*Vn_selected stack) per S400 capacity "
                       "design -- relief only as documented",
            instruction="NUMERIC: per line, Ve_cap_by_story_kip = Om0_eff x V_ELF,story "
                        "(pure ELF, NO rho -- 12.3.4.1 item 5) and T_cd_seed_kip = the "
                        "same top-down stack at Om0_eff-level shears. The FINAL hold-down/"
                        "chord/anchorage demand is min(expected-strength stack of the "
                        "SELECTED assembly (Omega_E*Vn), Ve_cap stack) -- NEVER the raw "
                        "ELF seed; consistency.check FAILS demands <= 1.05x the ELF seed. "
                        "dead_relief_kip_available is the DOCUMENTED (0.9-0.2SDS)D chord "
                        "relief -- NOT taken in the seed; take it only as documented.")
    else:
        Om0e = None
    for dirn, dd in res["directions"].items():
        for lname, lr in dd["lines"].items():
            wl = (wind or {}).get(dirn, {}).get("lines", {}).get(lname)
            for k in sorted(lr):
                r = lr[k]
                v_w = (wl["v_plf"].get(k) if wl else None)
                slot = dict(
                    id="wall-%s-%s-s%d" % (dirn, lname, k), direction=dirn, story=k,
                    v_unit_plf=round(r["v_unit_plf"] * rho, 0),
                    V_kip=round(r["V"] * rho, 1),
                    demand_basis="tributary (%s diaphragm) + 5%% shift; includes rho=%.2f"
                                 % (dd.get("diaphragm", "flexible"), rho),
                    sheathing=None, fastener_schedule=None,       # AGENT (S400 table, cited)
                    limit_state=None, cited=None, capacity=None, DC=None)
                if v_w is not None:
                    slot["v_wind_plf"] = v_w
                    slot["governing_basis"] = (
                        "wind (1.0W > rho*E at this line -- use the S400 WIND capacity "
                        "columns)" if v_w > r["v_unit_plf"] * rho
                        else "seismic (rho=%.2f included in the comparison)" % rho)
                pkg["wall_lines"].append(slot)
                pkg["drift_table"].append(dict(
                    direction=dirn, line=lname, story=k,
                    drift_amplified=round(r.get("drift_amplified", 0.0), 5),
                    limit=dd["drift_limit"],
                    ok=r.get("drift_amplified", 0.0) <= dd["drift_limit"]))
            base = lr[min(lr)]
            T_seis = base["T_kip"] * rho          # rho-included ELF strength seed
            T_wind = wl["T_base_kip"] if wl else None
            T_gov = max(T_seis, T_wind or 0.0)
            _ln = next((l for l in (cfg["lines_x"] if dirn == "X" else cfg["lines_y"])
                        if l.name == lname), None)
            _segs = _ln.segments.get(min(lr), []) if _ln is not None else []
            # STRAP lines: overturning concentrates at BAY ends -- seed the PER-BAY
            # tension too (T_line spreads M over the whole line length; per bay:
            # T_bay = M/(n_bays*w_bay) = T_line * L_line/(n_bays*w_bay))
            T_bay = None
            if sysname == "strap_braced" and _segs:
                _n, _w = len(_segs), _segs[0][0]
                T_bay = round(T_seis * _ln.length(min(lr)) / (_n * _w), 1)
                T_gov = max(T_gov, T_bay)
            dev, kdev, note = WL.pick_holddown(T_gov)
            hd = dict(
                id="hd-%s-%s" % (dirn, lname), line=lname, direction=dirn,
                T_cum_kip=round(T_seis, 1), device_class=dev,
                k_kip_in=kdev, feasibility_note=note,
                basis="cumulative overturning tension, top-down stack (includes rho=%.2f); "
                      "sized for TENSION (never the shear force); rods computed PL/AE + "
                      "take-up" % rho,
                limit_state=None, cited=None, capacity=None, DC=None)
            if T_bay is not None:
                hd["T_bay_seed_kip"] = T_bay
                hd["basis"] += ("; STRAP LINE: design anchorage for the PER-BAY tension "
                                "seed T_bay_seed_kip (includes rho; the line-level T_cum "
                                "spreads overturning over the whole line), then apply the "
                                "S400 E3.3 capacity-design amplification from the SELECTED "
                                "strap Ry*Fy*Ag")
            if T_wind is not None:
                hd["T_wind_kip"] = T_wind
                if T_wind > T_seis:
                    hd["basis"] += ("; WIND GOVERNS the tension (0.9D+1.0W net-uplift case) "
                                    "-- the anchorage chain is a wind design")
            # NUMERIC capacity-design seed (R>3 wall systems): Omega0_eff-level story
            # shears + the SAME top-down stacking as the ELF T_cum, from PURE ELF (no
            # rho -- 12.3.4.1 item 5), NO dead relief taken
            if sysname in CD_WALL_SYSTEMS and _ln is not None:
                Ve_cap = {k: round(lr[k]["V"] * Om0e, 1) for k in lr}
                ot_cd = WL.overturning_stack(
                    _ln, dd["dist"], {k: cfg["heights_ft"][k - 1] for k in dd["dist"]},
                    shear_scale=Om0e)
                T_cd = round(ot_cd[min(ot_cd)]["T_kip"], 1)
                w_bay = _segs[0][0] if _segs else None
                relief, relief_note = _chord_dead_relief(cfg, _ln, dirn, w_bay)
                cd_entry = dict(
                    direction=dirn, line=lname, Om0_eff=Om0e,
                    Ve_cap_by_story_kip=Ve_cap, T_cd_seed_kip=T_cd,
                    dead_relief_kip_available=relief, dead_relief_basis=relief_note,
                    basis="T_cd_seed = Omega0-level stack, no dead relief; final demand = "
                          "min(Omega0-level, Omega_E*Vn_selected stack) per S400 capacity "
                          "design -- relief only as documented")
                if sysname == "strap_braced" and _segs:
                    _n = len(_segs)
                    cd_entry["Ve_cap_bay_by_story_kip"] = \
                        {k: round(v / _n, 1) for k, v in Ve_cap.items()}
                    cd_entry["T_cd_bay_seed_kip"] = \
                        round(T_cd * _ln.length(min(lr)) / (_n * _segs[0][0]), 1)
                    hd["T_cd_seed_kip"] = cd_entry["T_cd_bay_seed_kip"]
                else:
                    hd["T_cd_seed_kip"] = T_cd
                pkg["capacity_design"]["lines"]["%s:%s" % (dirn, lname)] = cd_entry
                hd["basis"] += ("; CAPACITY DESIGN: final demand = min(Omega0-level "
                                "T_cd_seed_kip, expected-strength Omega_E*Vn stack of the "
                                "SELECTED assembly) -- NEVER the raw ELF T_cum/T_bay seed "
                                "(see capacity_design block)")
            pkg["holddowns"].append(hd)
        if dd.get("gate_flags"):
            pkg.setdefault("model_vs_tributary_flags", []).extend(dd["gate_flags"])
        if dd.get("drift_flags"):
            pkg.setdefault("drift_flags", []).extend(dd["drift_flags"])
    # stud schedule seed (typical bearing stud; agent adds section + checks incl. G5 at track)
    trib = cfg.get("stud_trib_ft", 2.0)
    pkg["studs"].append(dict(id="stud-typ-bearing", trib_ft=trib,
                             P_cum_kip_by_story=stud_axial_stack(cfg, trib),
                             note="AGENT: section per story group (never lighter below), "
                                  "sheathing-braced assumption STATED, web crippling G5 at "
                                  "track, interaction H1", limit_state=None, cited=None,
                             capacity=None, DC=None))
    for ln in cfg.get("collector_lines", []):
        pkg["collectors"].append(dict(id="collector-%s" % ln, line=ln,
                                      basis="SEEDED: re-entrant/step line -- design with "
                                            "overstrength (Om0) per 12.10.2.1",
                                      limit_state=None, cited=None, capacity=None, DC=None))
    return pkg


def write_package(name, cfg, res, outdir="."):
    import os
    pkg = build_package(name, cfg, res)
    p = os.path.join(outdir, "calc_package_cfs.json")
    json.dump(pkg, open(p, "w"), indent=1)
    return p, pkg


# ---------------- Stage 4: portal package ----------------

def build_portal_package(name, cfg, res):
    """calc_package for the PORTAL path (cfs_frame.run() result). Same contract discipline:
    demand slots seeded, every capacity/citation field left for the agent. Slots: frame members
    (col/raf envelope + governing combo), knee/apex connection transfer, base anchorage
    (max shear + NET UPLIFT across combos), purlin/girt/strap schedule seeds, torsion table
    (single channels), eave-drift serviceability row."""
    pkg = dict(building=name, code="AISI S100-16(R2020)+S2/S3 -- LRFD; ASCE 7-22",
               kind="cfs_portal", system="portal frame",
               structure_kind=res["structure_kind"], combos_note=COMBOS_NOTE,
               sections=res["sections"], members=[], connections=[], anchorage=[],
               schedules=[], drift_table=[],
               preflight_warnings=res.get("preflight_warnings", []),
               wind_basis=res.get("wind_basis", {}), notes=list(res.get("notes", [])))
    if "eff_stiffness" in res:
        pkg["analysis_basis"] = dict(
            tier=cfg.get("analysis_fidelity", 1),
            direct_analysis=cfg.get("direct_analysis", True),
            eff_stiffness=res["eff_stiffness"]["ratios"],
            note="Tier-1 effective-stiffness iterated (0.8E + notional + P-Delta on strength "
                 "combos); member capacities incl. distortional/global + H1 are the AGENT's")
    for mkey, lab in (("col", "col_L"), ("raf", "raf_L")):
        gname = res["governing"][mkey]
        env = res["combos"][gname]["envelope"][lab]
        pkg["members"].append(dict(
            id="frame-%s" % mkey, member=mkey, section=res["sections"][mkey],
            governing_combo=gname, P_kip=env["P_kip"], V_kip=env["V_kip"],
            M_kipin=env["M_kipin"],
            note="AGENT: EWM local+distortional+global, H1 interaction; knee-region "
                 "distortional with the UNBRACED inside flange; uplift-reversal unbraced case",
            limit_state=None, cited=None, capacity=None, DC=None))
    # knee/apex transfer = max member end moment at those joints across combos
    knee = apex = 0.0
    for cname, cd in res["combos"].items():
        for lab in ("col_L", "col_R"):
            knee = max(knee, abs(cd["envelope"][lab]["M_kipin_stations"][-1]))
        for lab in ("raf_L", "raf_R"):
            apex = max(apex, abs(cd["envelope"][lab]["M_kipin_stations"][-1]))
    for jname, M in (("knee", knee), ("apex", apex)):
        pkg["connections"].append(dict(
            id="conn-%s" % jname, joint=jname, M_transfer_kipin=round(M, 1),
            note="AGENT: bolted gusset bracket -- bolt group, bearing, net section, "
                 "gusset buckling; bracing assumption at the joint STATED",
            limit_state=None, cited=None, capacity=None, DC=None))
    Vmax = Tup = Mmax = 0.0
    up_combo = None
    for cname, cd in res["combos"].items():
        for t, r in cd["reactions_kip"].items():
            Vmax = max(Vmax, abs(r[0]))
            if len(r) > 2:
                Mmax = max(Mmax, abs(r[2]))
            if r[1] < -Tup:
                Tup, up_combo = -r[1], cname
    pkg["anchorage"].append(dict(
        id="base-anchor", V_base_kip=round(Vmax, 2), T_net_uplift_kip=round(Tup, 2),
        M_base_kipin=round(Mmax, 1), uplift_combo=up_combo,
        note="AGENT: base plate + anchor rods; NET UPLIFT is a required case (0.9D+1.0W)"
             + ("; FIXED base -- design the anchor group for M_base too" if Mmax > 1.0
                else ""),
        limit_state=None, cited=None, capacity=None, DC=None))
    for sched, note in (("purlin", "Z-purlins, lap/continuity + uplift R-factor basis STATED"),
                        ("girt", "wall girts, C&C wind"),
                        ("strap", "longitudinal tension-only X-straps: An*Fu vs Ag*Fy, "
                                  "connection at least strap strength")):
        pkg["schedules"].append(dict(id="sched-%s" % sched, schedule=sched, rows=None,
                                     note="AGENT: " + note,
                                     limit_state=None, cited=None, capacity=None, DC=None))
    if res.get("torsion_companion"):
        pkg["torsion_companion"] = res["torsion_companion"]
    sv = res["service"]
    pkg["drift_table"].append(dict(check="eave_sway", value_in=sv["eave_sway_in"],
                                   H_over=sv["H_over"], basis=sv["note"], ok=None,
                                   criterion="AGENT states (e.g., H/60 metal bldg, H/240 "
                                             "w/brittle finishes) and verdicts"))
    pkg["drift_table"].append(dict(check="apex_deflection", value_in=sv["apex_defl_in"],
                                   ok=None, criterion="AGENT states span criterion"))
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
        cfgc = dict(cfg, base="fixed", structure_kind="portal")
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
    assert ppkg["anchorage"][0]["uplift_combo"] == "0.9D+1.0W"
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
    assert cdw["Om0"] == 3.0 and cdw["Om0_eff"] == 2.5, \
        "footnote b: flexible diaphragm + Om0>=2.5 -> Om0_eff = Om0 - 0.5"
    hdw = pkgw["holddowns"][0]
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

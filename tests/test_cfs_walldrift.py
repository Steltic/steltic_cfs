"""Regression tests for the CFS wall-path drift fixes (review register CFS-01, -04, -05, -08, -09).

CFS-01  story drift includes the rigid-body rotation carried up from the stories below
        (chord strain under the cumulative overturning + hold-down/rod elongation)
CFS-04  wall_line.s400_deflection is AISI S400-20 Eq. E1.4.1.4-1 / E2.4.1.4-1 (slip ~ (v/beta)^2)
CFS-05  P-delta stability coefficient theta per ASCE 7-22 12.8.7 on the wall path, gate + package
CFS-08  fit_positions never returns coincident positions
CFS-09  wall lines absent at a story: transfer, no zero-length divisions, lint
"""
import json
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(HERE), "steel_engine")
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)

import wall_line as WL          # noqa: E402
import cfs_engine as CE         # noqa: E402
import cfs_systems as CS        # noqa: E402
import cfs_pipeline as CP       # noqa: E402
import consistency as CC        # noqa: E402

E_PSI = 29.5e6


# ------------------------------------------------------------------ CFS-04: S400 equations

def test_s400_wsp_eq_E1_4_1_4_1_matches_hand_calc():
    """Ex1 line A story 1 (15/32 plywood both faces, #8 @ 3 in., 54-mil, Ac 4.881, 2-in. rod):
    the agent's independent hand calc gave bend 0.0113 / shear 0.0301 / slip 0.1375 /
    anchorage 0.1595 in."""
    p = dict(sheathing="plywood", Gt_lb_in=37500.0, faces=2, s_in=3.0, t_stud_in=0.0566,
             chord_area_in2=4.881, rod_area_in2=2.50, takeup_in=0.05)
    h_ft, b_ft, v_plf, T = 128.0 / 12.0, 8.0, 1343.0, 40.1
    d = WL.s400_deflection(v_plf, h_ft, b_ft, p, T_kip=T)
    v, vs, h, b = v_plf / 12.0, v_plf / 24.0, 128.0, 96.0
    w1, w2, w3 = 3.0 / 6.0, 0.033 / 0.0566, math.sqrt((h / b) / 2.0)
    bend = 2 * v * h ** 3 / (3 * E_PSI * 4.881 * b)
    shear = w1 * w2 * vs * h / (1.85 * 37500.0)
    slip = w1 ** 1.25 * w2 * w3 * 1.0 * (vs / 67.5) ** 2
    dv = T * h / (29500.0 * 2.50) + 0.05
    assert d["bending"] == pytest.approx(bend, rel=1e-9)
    assert d["shear"] == pytest.approx(shear, rel=1e-9)
    assert d["slip"] == pytest.approx(slip, rel=1e-9)
    assert d["anchorage"] == pytest.approx(h / b * dv, rel=1e-9)
    # the agent's printed hand values
    assert d["bending"] == pytest.approx(0.0113, abs=6e-4)
    assert d["shear"] == pytest.approx(0.0301, abs=6e-4)
    assert d["slip"] == pytest.approx(0.1375, abs=6e-4)
    assert d["anchorage"] == pytest.approx(0.1595, abs=6e-4)
    assert d["total"] == pytest.approx(bend + shear + slip + h / b * dv, rel=1e-9)
    assert "E1.4.1.4-1" in d["equation"] and d["assumed"] == []


def test_s400_steel_sheet_eq_E2_4_1_4_1_matches_hand_calc():
    p = dict(sheathing="steel_sheet", t_sheathing_in=0.033, s_in=4.0, t_stud_in=0.0451,
             Fy_ksi=50.0, faces=1, chord_area_in2=2.0, k_anchor_kip_in=100.0)
    d = WL.s400_deflection(600.0, 9.0, 8.0, p, T_kip=10.0)
    v, h, b = 50.0, 108.0, 96.0
    beta = 29.12 * 0.033 / 0.018            # Eq. E2.4.1.4-3a
    rho = 0.075 * 0.033 / 0.018             # Eq. E2.4.1.4-4a
    w1, w2, w3, w4 = 4 / 6.0, 0.033 / 0.0451, math.sqrt((h / b) / 2), math.sqrt(33.0 / 50.0)
    exp = (2 * v * h ** 3 / (3 * E_PSI * 2.0 * b) + w1 * w2 * v * h / (rho * 11.3e6 * 0.033)
           + w1 ** 1.25 * w2 * w3 * w4 * (v / beta) ** 2 + h / b * (10.0 / 100.0))
    assert d["total"] == pytest.approx(exp, rel=1e-9)
    assert "E2.4.1.4-1" in d["equation"]


def test_s400_slip_is_quadratic_and_has_no_constant_term():
    p = dict(sheathing="osb", Gt_lb_in=77500.0, s_in=6.0, t_stud_in=0.0346, faces=1,
             chord_area_in2=1.5, k_anchor_kip_in=80.0)
    d1 = WL.s400_deflection(300.0, 9.0, 10.0, p, T_kip=0.0)
    d2 = WL.s400_deflection(600.0, 9.0, 10.0, p, T_kip=0.0)
    assert d2["slip"] == pytest.approx(4.0 * d1["slip"], rel=1e-12)
    assert d2["shear"] == pytest.approx(2.0 * d1["shear"], rel=1e-12)
    d0 = WL.s400_deflection(0.0, 9.0, 10.0, p, T_kip=0.0)
    assert d0["total"] == 0.0                 # no load-independent (constant) term


def test_legacy_props_use_assumed_schedule_and_say_so():
    legacy = dict(chord_area_in2=1.2, Gp_kip_in=9.0, en_in=0.03, k_anchor_kip_in=50.0)
    d = WL.s400_deflection(500.0, 9.5, 15.0, legacy, T_kip=5.0, system="wsp_shearwall")
    assert d["assumed"] and any("s_in" in a for a in d["assumed"])
    assert any("en_in ignored" in n for n in d["notes"])
    # positional legacy call still works (mechanics form, Gp as shear rigidity)
    d2 = WL.s400_deflection(500.0, 9.5, 15.0, 1.2, 9.0, 0.03, 50.0, 5.0)
    assert d2["total"] > 0 and d2["slip"] == 0.0


def test_strap_mechanics_E3_4_4():
    p = dict(sheathing="strap", strap_area_in2=2 * 4.0 * 0.0713, chord_area_in2=4.0,
             rod_area_in2=1.9, takeup_in=0.0)
    d = WL.s400_deflection(1000.0, 10.0, 12.0, p, T_kip=20.0)
    v, h, b = 1000 / 12.0, 120.0, 144.0
    Ld = math.hypot(b, h)
    strap = v * Ld ** 3 / (E_PSI * 0.5704 * b)
    chord = 2 * v * h ** 3 / (E_PSI * 4.0 * b)
    anch = h / b * (20.0 * 120.0 / (29500.0 * 1.9))
    assert d["total"] == pytest.approx(strap + chord + anch, rel=1e-9)


# ------------------------------------------------------------------ CFS-01: carried rotation

def _two_story_cfg(props, **kw):
    seg = {1: [(10.0, 10.0)], 2: [(10.0, 10.0)]}
    cfg = dict(stories=2, heights_ft=[10.0, 10.0], plan_ft=(40.0, 40.0),
               D_floor=40.0, D_roof=20.0, clad=0.0, snow=0.0, L_floor=0.0,
               seis=CS.seis_cfs(1.0, 0.6, 0.5, "wsp_shearwall"), system="wsp_shearwall",
               risk_cat="II", structure_kind="wall", analysis_fidelity=0, diaphragm="flexible",
               lines_x=[WL.WallLine("A", 0.0, seg), WL.WallLine("B", 40.0, seg)],
               lines_y=[WL.WallLine("1", 0.0, seg), WL.WallLine("2", 40.0, seg)],
               wall_props=props)
    cfg.update(kw)
    return cfg


def test_two_story_stack_closed_form_rotation_carried_up():
    props = dict(sheathing="gypsum", Ga_kip_in=100.0, chord_area_in2=2.0, k_anchor_kip_in=50.0)
    cfg = _two_story_cfg(props, system="gypsum_wall")
    ln = cfg["lines_x"][0]
    r = CE.analyze_line(cfg, ln, {2: 10.0, 1: 10.0})      # story shears: s2 = 10, s1 = 20 kip
    E, Ac, h, b, Ga, k = 29500.0, 2.0, 120.0, 120.0, 100.0, 50.0
    v2, v1 = 10.0 / b, 20.0 / b                              # kip/in
    T2, T1 = 10.0 * 10.0 / 10.0, (100.0 + 20.0 * 10.0) / 10.0   # kip (M/L)
    m1 = 100.0 / 10.0                                        # chord force from story 2 (kip)
    own2 = 2 * v2 * h ** 3 / (3 * E * Ac * b) + v2 * h / Ga + h / b * T2 / k
    own1 = (2 * v1 * h ** 3 / (3 * E * Ac * b) + v1 * h / Ga + m1 * h ** 2 / (E * Ac * b)
            + h / b * T1 / k)
    dth1 = 2 * (m1 * h + v1 * h * h / 2) / (E * Ac * b) + (T1 / k) / b
    assert r[1]["T_kip"] == pytest.approx(T1)
    assert r[1]["drift_in"] == pytest.approx(own1, rel=1e-9)
    assert r[2]["drift_own_in"] == pytest.approx(own2, rel=1e-9)
    assert r[2]["drift_rot_in"] == pytest.approx(h * dth1, rel=1e-9)
    assert r[2]["drift_in"] == pytest.approx(own2 + h * dth1, rel=1e-9)
    assert r[2]["K_kip_in"] == pytest.approx(10.0 / (own2 + h * dth1), rel=1e-9)


def _ex1_like_cfg():
    """Ex1 (4-story WSP) geometry, loads and line A schedule (from its design files)."""
    H = [10.0 + 8.0 / 12.0, 9.5, 9.5, 9.5]
    segs = lambda n, L: {k: [(L, H[k - 1])] * n for k in (1, 2, 3, 4)}
    EXT = dict(by_story={
        1: dict(sheathing="plywood", Gt_lb_in=37500.0, faces=2, s_in=3.0, t_stud_in=0.0566,
                chord_area_in2=4.881, rod_area_in2=2.50, takeup_in=0.05),
        2: dict(sheathing="plywood", Gt_lb_in=37500.0, faces=2, s_in=4.0, t_stud_in=0.0566,
                chord_area_in2=3.814, rod_area_in2=2.50, takeup_in=0.05),
        3: dict(sheathing="plywood", Gt_lb_in=37500.0, faces=1, s_in=2.0, t_stud_in=0.0566,
                chord_area_in2=1.68, rod_area_in2=1.405, takeup_in=0.05),
        4: dict(sheathing="plywood", Gt_lb_in=37500.0, faces=1, s_in=6.0, t_stud_in=0.0566,
                chord_area_in2=1.68, rod_area_in2=0.462, takeup_in=0.05)})
    return dict(
        stories=4, heights_ft=H, plan_ft=(160.0, 62.0),
        D_floor=35.0, D_roof=20.0, clad=15.0, snow=0.0, L_floor=55.0, partition_psf=10.0,
        seis=CS.seis_cfs(1.00, 0.45, 0.45, "wsp_shearwall", Ie=1.0), system="wsp_shearwall",
        risk_cat="II", structure_kind="wall", analysis_fidelity=0, diaphragm="flexible",
        lines_x=[WL.WallLine("A-extS", 0.0, segs(6, 8.0), wall_props=EXT),
                 WL.WallLine("B-corS", 28.0, segs(6, 8.0), wall_props=EXT),
                 WL.WallLine("C-corN", 34.0, segs(6, 8.0), wall_props=EXT),
                 WL.WallLine("D-extN", 62.0, segs(6, 8.0), wall_props=EXT)],
        lines_y=[WL.WallLine(n, x, segs(4, 10.0), wall_props=EXT)
                 for n, x in (("1", 0.0), ("2", 40.0), ("3", 80.0), ("4", 120.0), ("5", 160.0))],
        rho=1.3)


def test_ex1_line_A_upper_story_drift_includes_rotation_from_below():
    """Register CFS-01: Ex1 line A story 4 -- engine 0.0070 (story-alone spring) vs >= 0.0176
    with the inherited rotation; the agent's hand calc with the selected schedule gave 0.0243
    (incl. an extra chord-compression term)."""
    res = CE.run(_ex1_like_cfg())
    lr = res["directions"]["X"]["lines"]["A-extS"]
    r4 = lr[4]
    assert r4["drift_amplified_single_story"] == pytest.approx(0.0063, abs=0.0005)
    assert r4["drift_amplified"] == pytest.approx(0.0225, abs=0.0010)
    assert r4["drift_amplified"] > 3.0 * r4["drift_amplified_single_story"]
    # drift grows up the stack once the inherited rotation is counted
    assert lr[4]["drift_amplified"] > lr[3]["drift_amplified"] > lr[2]["drift_amplified"]
    # single-story terms unchanged vs the hand calc at story 1 (no rotation from below)
    assert lr[1]["drift_rot_in"] == 0.0
    assert lr[1]["terms"]["slip"] == pytest.approx(0.1375, abs=6e-4)


def test_drift_gate_uses_cumulative_drift():
    cfg = _ex1_like_cfg()
    res = CE.run(cfg)
    dd = res["directions"]["X"]
    dl = dd["drift_limit"]
    # every flag corresponds to cumulative > limit; with a tighter limit the cumulative trips
    cfg2 = dict(cfg, risk_cat="III")                       # 0.020 for <= 4-story light frame
    dd2 = CE.run(cfg2)["directions"]["X"]
    assert dd2["drift_limit"] == 0.020 < dl
    over = [(n, k) for n, lr in dd2["lines"].items() for k, r in lr.items()
            if r["drift_amplified"] > 0.020]
    assert over and len(dd2["drift_flags"]) == len(over)
    assert all(lr[k]["drift_amplified_single_story"] < 0.020
               for n, lr in dd2["lines"].items() for k in lr if (n, k) in over)
    pkg = CP.build_package("t", cfg2, CE.run(cfg2))
    row = next(r for r in pkg["drift_table"] if r["direction"] == "X" and r["story"] == 4)
    assert row["drift_amplified"] > row["drift_single_story"] > 0
    assert row["drift_rotation_from_below"] > 0 and not row["ok"]


# ------------------------------------------------------------------ CFS-05: theta (12.8.7)

def test_theta_max_eq_12_8_19_and_7_22_floor():
    cfg = _two_story_cfg({}, seis=dict(CS.seis_cfs(1.0, 0.6, 0.5, "wsp_shearwall")))
    assert CE.theta_max(cfg) == pytest.approx(0.5 / 4.0)                 # Cd = 4, beta = 1
    c2 = dict(cfg, seis=dict(cfg["seis"], Cd=6.5))
    assert CE.theta_max(c2) == pytest.approx(0.10)                       # 0.077 -> floor 0.10
    c3 = dict(cfg, theta_beta=0.2)                                       # beta >= 1.25/Om0
    assert CE.theta_max(c3) == pytest.approx(0.25)                       # 0.5/(0.4167*4)=0.3


def test_theta_per_story_hand_calc_and_gate():
    props = dict(sheathing="gypsum", Ga_kip_in=100.0, chord_area_in2=2.0, k_anchor_kip_in=50.0)
    cfg = _two_story_cfg(props, system="gypsum_wall", L_floor=50.0)
    res = CE.run(cfg)
    e = res["elf"]
    r1 = res["directions"]["X"]["lines"]["A"][1]
    P1 = CE.gravity_Px_level(cfg, 1) + CE.gravity_Px_level(cfg, 2)
    # 12.8.6.1 expected gravity: 1.0D + 0.5 * 0.4 * L0 over the floor (level 1 only)
    assert CE.gravity_Px_level(cfg, 1) == pytest.approx(CE.story_weight(cfg, 1)
                                                        + 0.2 * 50.0 * 1600.0 / 1000.0)
    theta = P1 * r1["drift_in"] / (e["V"] * 120.0)
    assert r1["theta"] == pytest.approx(theta, rel=1e-9)
    # soft walls -> theta > theta_max -> stability flag -> package -> consistency gate
    soft = dict(props, Ga_kip_in=0.5)
    cfg_s = _two_story_cfg(soft, system="gypsum_wall", L_floor=50.0)
    rs = CE.run(cfg_s)
    dd = rs["directions"]["X"]
    assert dd["stability_flags"] and "12.8.7" in dd["stability_flags"][0]
    pkg = CP.build_package("t", cfg_s, rs)
    assert pkg["stability_flags"]
    issues = CC._cfs_slot_issues(cfg_s, pkg)
    assert any("P-delta stability" in i for i in issues)


def test_theta_between_010_and_max_amplifies_drift():
    props = dict(sheathing="gypsum", Ga_kip_in=100.0, chord_area_in2=2.0, k_anchor_kip_in=50.0)
    for ga in (40.0, 20.0, 12.0, 8.0, 6.0, 5.0, 4.0):
        cfg = _two_story_cfg(dict(props, Ga_kip_in=ga), system="gypsum_wall", L_floor=50.0)
        res = CE.run(cfg)
        r = res["directions"]["X"]["lines"]["A"][1]
        if 0.10 < r["theta"] <= r["theta_max"]:
            assert r["pdelta_factor"] == pytest.approx(1.0 / (1.0 - r["theta"]))
            assert r["drift_amplified"] == pytest.approx(r["drift_amplified_first_order"]
                                                         * r["pdelta_factor"])
            assert res["directions"]["X"]["stability_warnings"]
            return
    pytest.fail("no case landed in 0.10 < theta <= theta_max")


# ------------------------------------------------------------------ CFS-08: fit_positions

def _shares(pos, areas, dim):
    lines = [WL.WallLine("L%d" % i, x, {1: [(10.0, 9.0)]}) for i, x in enumerate(pos)]
    d = WL.distribute({1: 100.0}, lines, dim, shift=0.0)
    return [d[1]["L%d" % i]["V"] for i in range(len(pos))]


@pytest.mark.parametrize("areas,dim", [([100, 100, 100, 100], 40.0),
                                        ([150, 200, 250, 200, 150], 95.0),
                                        ([1, 1, 1], 30.0), ([200, 300, 300, 200], 100.0)])
def test_fit_positions_strictly_ordered_and_exact(areas, dim):
    p = WL.fit_positions(areas, dim)
    assert all(b - a > 1e-6 for a, b in zip(p, p[1:])), p
    assert p[0] >= -1e-9 and p[-1] <= dim + 1e-9
    got = _shares(p, areas, dim)
    tgt = [100.0 * a / sum(areas) for a in areas]
    assert got == pytest.approx(tgt, abs=1e-6)


def test_fit_positions_legacy_ordered_result_unchanged():
    p = WL.fit_positions([930.0, 1860.0, 1860.0, 3450.0, 3060.0, 3060.0, 2520.0], 152.0)
    assert [round(x, 2) for x in p] == [0.0, 16.89, 33.78, 50.67, 96.43, 106.24, 152.0]


def test_fit_positions_infeasible_raises_and_trib_scales_alternative():
    # Ex7 Z-plan Y lines (N-bar then S-bar on one 240-ft strip): the legacy result had
    # positions 120.0, 120.0 (coincident -> merged and re-split by wall length)
    N_x = [0, 12, 25, 38, 51, 64, 77, 90, 103, 116, 129, 142, 150]
    S_x = [90, 98, 111, 124, 137, 150, 163, 176, 189, 202, 215, 228, 240]
    trib = lambda xs: [((x - xs[i - 1]) / 2 if i else 0) +
                       ((xs[i + 1] - x) / 2 if i < len(xs) - 1 else 0) for i, x in enumerate(xs)]
    areas = [t * 58.0 for t in trib(N_x) + trib(S_x)]
    with pytest.raises(ValueError, match="trib_scale"):
        WL.fit_positions(areas, 240.0)
    approx = WL.fit_positions(areas, 240.0, on_infeasible="approx")
    assert all(b > a for a, b in zip(approx, approx[1:]))
    widths = [10, 10, 3, 3, 10, 10]
    nat = [0.0, 10.0, 20.0, 23.0, 30.0, 46.0]
    sc = WL.trib_scales_for(widths, nat, 46.0)
    lines = [WL.WallLine("L%d" % i, x, {1: [(10.0, 9.0)]}, trib_scale=s)
             for i, (x, s) in enumerate(zip(nat, sc))]
    d = WL.distribute({1: 100.0}, lines, 46.0, shift=0.0)
    assert [d[1]["L%d" % i]["V"] for i in range(6)] == \
        pytest.approx([100.0 * w / sum(widths) for w in widths], abs=1e-9)


# ------------------------------------------------------------------ CFS-09: line presence

def _breezeway_cfg():
    segs = {k: [(20.0, 9.5), (10.0, 9.5)] for k in (1, 2, 3, 4)}
    segsB = {k: [(15.0, 9.5)] for k in (1, 2, 3, 4)}
    segsM = {1: [], 2: [(10.0, 9.5)], 3: [(10.0, 9.5)], 4: [(10.0, 9.5)]}
    props = dict(sheathing="osb", Gt_lb_in=77500.0, s_in=4.0, t_stud_in=0.0451, faces=1,
                 chord_area_in2=1.2, k_anchor_kip_in=50.0)
    return dict(stories=4, heights_ft=[10.67, 9.5, 9.5, 9.5], plan_ft=(160.0, 62.0),
                D_floor=35.0, D_roof=20.0, clad=15.0, snow=0.0, L_floor=40.0,
                seis=CS.seis_cfs(1.0, 0.45, 0.45, "wsp_shearwall"), system="wsp_shearwall",
                risk_cat="II", structure_kind="wall", analysis_fidelity=0, diaphragm="flexible",
                wind=dict(V=110.0, exposure="B"),
                lines_x=[WL.WallLine("X1", 0.0, segs), WL.WallLine("X2", 31.0, segsB),
                         WL.WallLine("X3", 62.0, segs)],
                lines_y=[WL.WallLine("Y1", 0.0, segsB), WL.WallLine("Y2", 80.0, segsB),
                         WL.WallLine("P5", 120.0, segsM), WL.WallLine("Y3", 160.0, segsB)],
                wall_props=props)


def test_missing_story_line_is_finite_and_transfers_by_lever_rule():
    """Register repro: a line with no story-1 wall gave v = 5.9e10 plf, T = 1.6e9 kip,
    drift = inf and Infinity in the JSON."""
    cfg = _breezeway_cfg()
    res = CE.run(cfg)
    dd = res["directions"]["Y"]
    assert 1 not in dd["lines"]["P5"]                     # no wall -> no story-1 result
    assert set(dd["lines"]["P5"]) == {2, 3, 4}
    for lr in dd["lines"].values():
        for r in lr.values():
            assert math.isfinite(r["drift_in"]) and math.isfinite(r["v_unit_plf"])
            assert r["v_unit_plf"] < 1e5 and math.isfinite(r["T_kip"])
    dist = dd["dist"]
    Vt = dist[1]["P5"]["transfer_out_kip"]
    assert Vt == pytest.approx(dist[2]["P5"]["V_story"])
    # lever rule between Y2 (x=80) and Y3 (x=160): P5 at x=120 -> 1/2 each
    assert dist[1]["Y2"]["transfer_in"]["P5"] == pytest.approx(Vt / 2)
    assert dist[1]["Y3"]["transfer_in"]["P5"] == pytest.approx(Vt / 2)
    # story-1 shear: the whole story shear lands on the present lines
    assert sum(r["V_story"] for r in dist[1].values()) == pytest.approx(res["elf"]["V"])
    assert dd["transfers"] and "12.3.3.4" in dd["transfers"][0]["basis"]
    assert dd["gate_flags"] == []
    pkg = CP.build_package("m", cfg, res)
    json.dumps(pkg, allow_nan=False, default=str)        # no Infinity / NaN anywhere
    assert pkg["discontinuity_transfers"]
    hd = next(h for h in pkg["holddowns"] if h["id"] == "hd-Y-P5")
    assert 0 < hd["T_cum_kip"] < 1000                    # base = story 2, not 1.6e9 kip


def test_story_without_any_line_raises_and_lints():
    cfg = _breezeway_cfg()
    for ln in cfg["lines_y"]:
        ln.segments[1] = []
    with pytest.raises(WL.NoResistingLineError):
        CE.run(cfg)
    lint = CC._geometry_issues(cfg)
    assert any("story 1 has NO Y-direction resisting wall line" in i for i in lint)


def test_zero_length_segment_is_linted_not_divided():
    cfg = _breezeway_cfg()
    cfg["lines_y"][2].segments[1] = [(0.0, 10.67)]
    res = CE.run(cfg)                                    # absent, transferred, finite
    assert 1 not in res["directions"]["Y"]["lines"]["P5"]
    assert any("non-positive length" in i for i in CC._geometry_issues(cfg))


def test_base_story_line_is_founded_not_transferred():
    cfg = _breezeway_cfg()
    cfg["lines_y"][2].base_story = 2                     # P5 bears on a stepped foundation
    res = CE.run(cfg)
    dist = res["directions"]["Y"]["dist"]
    assert dist[1]["P5"]["founded_kip"] == pytest.approx(dist[2]["P5"]["V_story"])
    assert not dist[1]["Y2"]["transfer_in"]
    total1 = sum(r["V_story"] for r in dist[1].values())
    assert total1 == pytest.approx(res["elf"]["V"] - dist[1]["P5"]["founded_kip"])


def test_split_level_extent_and_per_level_weights():
    cfg = _breezeway_cfg()
    cfg["level_weights_kip"] = {1: 300.0, 2: 500.0, 3: 500.0, 4: 250.0}
    assert CE.story_weight(cfg, 1) == 300.0
    cfg["diaphragm_extent_ft"] = {("X", 1): (0.0, 31.0)}
    res = CE.run(cfg)
    dist = res["directions"]["X"]["dist"]
    F1 = res["elf"]["Fx"][1]
    assert dist[1]["X3"]["F_level"] == 0.0               # outside the level-1 diaphragm
    assert dist[1]["X1"]["F_level"] + dist[1]["X2"]["F_level"] == pytest.approx(F1)


def test_semirigid_with_absent_line_is_finite():
    cfg = dict(_breezeway_cfg(), diaphragm="semi-rigid", diaphragm_G_kip_in=50.0)
    res = CE.run(cfg)
    for lr in res["directions"]["Y"]["lines"].values():
        for r in lr.values():
            assert math.isfinite(r["drift_in"]) and math.isfinite(r["K_kip_in"])
    assert 1 not in res["directions"]["Y"]["lines"]["P5"]

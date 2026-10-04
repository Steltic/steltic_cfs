"""Regression tests for the CFS wall-path load fixes (cfs-wallloads):
CFS-39/40/41 (storage weight, 0.5L, platform), CFS-06/07 (S400 capacity design incl. gypsum E6),
CFS-10/11 (wind distribution + MWFRS seed), CFS-18/19 (strap Omega_E / ductility),
CFS-20 (Type II Ca), CFS-21 (diaphragm), CFS-22 (collector Fpx), CFS-27 (drift row),
CFS-28 (per-direction systems). Hand calcs in the comments."""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "steel_engine"))

import cfs_systems as CS      # noqa: E402
import cfs_engine as CE       # noqa: E402
import cfs_pipeline as CP     # noqa: E402
import wall_line as WL        # noqa: E402


def mezz(**kw):
    """Ex29-like storage mezzanine: 60 x 32 ft, deck 12 ft, strap bays 12 ft x 10.5 ft."""
    wp = dict(chord_area_in2=1.7, Gp_kip_in=20.0, en_in=0.0, k_anchor_kip_in=50.0)
    cfg = dict(stories=1, heights_ft=[12.0], plan_ft=(60.0, 32.0),
               D_floor=12.0, D_roof=12.0, L_floor=125.0, storage=True, clad=2.5, snow=0.0,
               seis=CS.seis_cfs(0.35, 0.14, 0.10, "strap_braced"), system="strap_braced",
               risk_cat="II", structure_kind="mezzanine", analysis_fidelity=0,
               diaphragm="flexible", arch="storage mezzanine platform",
               lines_x=[WL.WallLine("A", 0.0, {1: [(12.0, 10.5)] * 2}, wall_props=wp),
                        WL.WallLine("B", 16.0, {1: [(12.0, 10.5)] * 2}, wall_props=wp),
                        WL.WallLine("C", 32.0, {1: [(12.0, 10.5)] * 2}, wall_props=wp)],
               lines_y=[WL.WallLine("1", 0.0, {1: [(16.0, 10.5)]}, wall_props=wp),
                        WL.WallLine("6", 60.0, {1: [(16.0, 10.5)]}, wall_props=wp)],
               wall_props=wp, stud_trib_ft=16.0, joist_span_ft=16.0, beam_span_ft=12.0,
               collector_lines=["X-B"])
    cfg.update(kw)
    return cfg


def box(stories=3, system="wsp_shearwall", sds=1.0, sd1=0.45, s1=0.45, **kw):
    segs = {k: [(20.0, 10.0), (10.0, 10.0)] for k in range(1, stories + 1)}
    cfg = dict(stories=stories, heights_ft=[10.0] * stories, plan_ft=(100.0, 50.0),
               D_floor=35.0, D_roof=20.0, clad=15.0, snow=0.0, L_floor=40.0,
               seis=CS.seis_cfs(sds, sd1, s1, system), system=system, risk_cat="II",
               structure_kind="wall", analysis_fidelity=0, diaphragm="flexible",
               lines_x=[WL.WallLine("X1", 0.0, segs), WL.WallLine("X2", 25.0, segs),
                        WL.WallLine("X3", 50.0, segs)],
               lines_y=[WL.WallLine("Y1", 0.0, segs), WL.WallLine("Y2", 50.0, segs),
                        WL.WallLine("Y3", 100.0, segs)],
               wall_props=dict(chord_area_in2=3.0, Gp_kip_in=60.0, en_in=0.02,
                               k_anchor_kip_in=150.0),
               stud_trib_ft=12.0, joist_span_ft=25.0)
    cfg.update(kw)
    return cfg


# ---------------- CFS-39 / 40 / 41 ----------------

def test_cfs39_storage_weight_in_W_and_V():
    # W = 12*1920 + 0.25*125*1920 + 2.5*184*6 = 23.04 + 60.00 + 2.76 = 85.80 kip
    e = CE.elf(mezz())
    assert abs(e["W"] - 85.80) < 0.01
    assert abs(e["Cs"] - 0.35 / 4.0) < 1e-9 and abs(e["V"] - 7.5075) < 1e-3
    # the pre-fix behaviour (top level = roof, no storage term) was W = 25.8 kip
    w_roof = CE.story_weight(mezz(structure_kind="wall"), 1)
    assert abs(w_roof - 25.80) < 0.01
    res = CE.run(mezz(structure_kind="wall"))
    assert any("treated as a ROOF" in w for w in res["preflight_warnings"])


def test_cfs39_storage_levels_partitions_and_5pct_exception():
    cfg = box(storage_levels=[1], L_floor=125.0, partitions=True)
    w1, bd = CE.story_weight(cfg, 1, detail=True)
    assert abs(bd["storage"] - 0.25 * 125 * 5000 / 1000.0) < 1e-9          # 156.25 kip
    assert abs(bd["partitions"] - 10.0 * 5000 / 1000.0) < 1e-9             # 12.7.2 item 2 min 10 psf
    assert "storage" not in CE.story_weight(cfg, 2, detail=True)[1]
    # exception (a): storage share > 5% -> NOT dropped even when requested
    w1x = CE.story_weight(dict(cfg, storage_5pct_exception=True), 1)
    assert abs(w1x - w1) < 1e-9
    # small storage share (5 psf live) <= 5% -> dropped only when requested
    c2 = box(storage_levels=[1], L_by_level={1: 5.0})
    assert CE.story_weight(dict(c2, storage_5pct_exception=True), 1) < CE.story_weight(c2, 1)
    # screen: heavy live with no storage declaration
    assert any("STORAGE" in w for w in CE.load_screens(box(L_floor=150.0)))


def test_cfs40_live_factor_in_seismic_and_wind_combos():
    f, b = CP.live_companion_factor(mezz())
    assert f == 1.0 and "125" in b
    combos = CP.enumerate_combos(mezz(wind=dict(V=115.0, exposure="C")))
    for c in combos:
        if c.get("E") or c.get("W"):
            if c["D"] >= 1.0:                  # additive combos carry L
                assert c.get("L") == 1.0, c["label"]
    assert any(c["label"].startswith("overstrength") and c.get("L") == 1.0 for c in combos)
    assert CP.live_companion_factor(box())[0] == 0.5
    assert CP.live_companion_factor(box(occupancy="parking garage"))[0] == 1.0
    assert CP.live_companion_factor(box(assembly=True))[0] == 1.0


def test_cfs41_platform_is_a_floor_live_kept_and_gravity_slots():
    cfg = mezz()
    st = CP.stud_axial_stack(cfg, 16.0)
    # (1.2*12 + 1.6*125) * 16 ft * 16/12 / 1000 = 4.5739 kip (pre-fix: roof -> 0.31 kip)
    assert abs(st[1] - 4.57) < 0.01
    pkg = CP.build_package("mezz", cfg, CE.run(cfg))
    gf = {g["id"]: g for g in pkg["gravity_framing"]}
    j = gf["joist-typ"]
    wu = 1.2 * 12 * 16 / 12 / 1000 + 1.6 * 125 * 16 / 12 / 1000           # 0.28587 klf
    assert abs(j["wu_klf"] - round(wu, 4)) < 1e-4
    assert abs(j["Mu_kipft"] - round(wu * 16 ** 2 / 8, 2)) < 0.01
    assert abs(j["defl_limit_live_in"] - 16 * 12 / 360.0) < 1e-3
    I_req = 5 * (0.16667 / 12) * (192.0 ** 4) / (384 * 29500 * (192 / 360.0))
    assert abs(j["I_req_in4"] - I_req) / I_req < 0.01
    assert abs(gf["post-typ"]["Pu_kip"] - (1.2 * 12 + 1.6 * 125) * 192 / 1000) < 0.01
    assert j["capacity"] is None and j["DC"] is None
    # no gravity slots on an ordinary multi-story wall building
    assert "gravity_framing" not in CP.build_package("b", box(), CE.run(box()))


# ---------------- CFS-06 / 07 ----------------

def test_cfs06_gypsum_gets_numeric_capacity_design():
    cfg = box(system="gypsum_wall", sds=0.25, sd1=0.10, s1=0.06, diaphragm_MDD_ADVE=3.0)
    pkg = CP.build_package("gyp", cfg, CE.run(cfg))
    cd = pkg["capacity_design"]
    assert cd["Omega_E"] == 1.5 and "E6" in cd["basis"]
    assert cd["Om0"] == 2.5 and cd["Om0_eff"] == 2.0            # footnote b (12.3.1.3 ratio)
    hd = [h for h in pkg["holddowns"] if h["id"] == "hd-X-X2"][0]
    assert hd["T_cd_seed_kip"] > 1.05 * hd["T_cum_kip"]
    # with the selected Vn declared, T_cd = min(Omega_E*Vn stack, Omega0 stack)
    vn = {1: 5.0, 2: 5.0, 3: 5.0}                                # small Vn -> 1.5*Vn governs
    cfg2 = dict(cfg, selected_Vn_kip={"X:X2": vn})
    pkg2 = CP.build_package("gyp2", cfg2, CE.run(cfg2))
    ln = pkg2["capacity_design"]["lines"]["X:X2"]
    # stack: 1.5*5 = 7.5 kip/story -> M = 7.5*10*3 = 225 kip-ft, L = 30 ft -> T = 7.5 kip
    assert abs(ln["T_OmegaE_Vn_stack_kip"] - 7.5) < 0.05
    assert ln["T_cd_seed_kip"] == min(ln["T_OmegaE_Vn_stack_kip"], ln["T_Om0_stack_kip"])


def test_cfs07_gypsum_is_E6_and_wind_basis_is_S240():
    assert CS.SYSTEMS["gypsum_wall"]["std"] == "AISI S400 E6"
    cfg = box(system="gypsum_wall", sds=0.25, sd1=0.10, s1=0.06,
              wind=dict(V=170.0, exposure="D"))
    pkg = CP.build_package("gyp", cfg, CE.run(cfg))
    gov = [w["governing_basis"] for w in pkg["wall_lines"] if
           (w.get("governing_basis") or "").startswith("wind")]
    assert gov and all("S240" in g and "0.65" in g for g in gov)
    txt = repr(pkg)
    assert "WIND capacity columns" not in txt and "wind columns" not in txt.lower()
    # gypsum h:w > 2:1 segments are flagged (S400 E6.3.1.1)
    segs = {k: [(4.0, 10.0), (20.0, 10.0)] for k in (1, 2, 3)}
    c3 = dict(cfg, lines_x=[WL.WallLine("X1", 0.0, segs), WL.WallLine("X2", 50.0, segs)])
    p3 = CP.build_package("gyp3", c3, CE.run(c3))
    assert any("aspect_flags" in w for w in p3["wall_lines"])


# ---------------- CFS-10 / 11 ----------------

def test_cfs10_wind_tribs_ignore_seismic_trib_scale_and_envelope_case2():
    cfg = box(wind=dict(V=115.0, exposure="C"))
    base = CP.wind_line_screen(cfg)
    cfg2 = dict(cfg)
    cfg2["lines_x"] = [WL.WallLine("X1", 0.0, cfg["lines_x"][0].segments, trib_scale=3.0),
                       cfg["lines_x"][1], cfg["lines_x"][2]]
    scaled = CP.wind_line_screen(cfg2)
    assert scaled["X"]["lines"]["X1"]["v_plf"] == base["X"]["lines"]["X1"]["v_plf"]
    assert "trib_scale_note" in scaled["X"]["basis"]
    # face B = 50: X1 trib [0, 12.5] -> Case 1 share 0.25; Case 2 block 1.2/0.3 -> 0.30
    wf, _b = CP.wind_story_forces(cfg, "X")
    V1 = sum(wf.values())
    v1 = base["X"]["lines"]["X1"]["v_plf"][1]
    assert abs(v1 - round(0.30 * V1 * 1000 / 30.0)) <= 1.0
    assert base["X"]["lines"]["X1"]["case_by_story"][1].startswith("2")


def test_cfs11_wind_seed_grade_leeward_qh_min_and_gust():
    import cfs_frame as CF
    cfg = box(wind=dict(V=115.0, exposure="C"))
    f, b = CP.wind_story_forces(cfg, "X", detail=True)
    q = lambda z: 0.00256 * CF._kzf(z, "C") * 0.85 * 115.0 ** 2
    # X wind: B = 50, L = 100 -> L/B = 2 -> Cp_lw = -0.3; rigid low-rise G = 0.85
    assert abs(b["Cp_lw"] + 0.3) < 1e-9 and b["G"] == 0.85
    p1 = 0.85 * (0.8 * q(10.0) + 0.3 * q(30.0))
    assert abs(f[1] - max(p1, 16.0) * 50 * 10 / 1000.0) < 1e-6
    # z from grade: a 15-ft podium raises every level's qz and qh
    f2, b2 = CP.wind_story_forces(dict(cfg, two_stage=dict(podium_height_ft=15.0)), "X")
    assert b2["z_base_ft"] == 15.0 and all(f2[k] > f[k] for k in f)
    # 27.1.5: 16 psf minimum governs a low wind speed
    f3, b3 = CP.wind_story_forces(dict(cfg, wind=dict(V=60.0, exposure="B")), "X")
    assert b3["min_16psf_governs_stories"] and abs(f3[1] - 16.0 * 50 * 10 / 1000) < 1e-9
    # flexible (n1 < 1 Hz) -> Gf per 26.11.5 (> 0.85 for this light damping)
    G, info = CP.gust_factor(115.0, "C", 120.0, 60.0, 100.0, n1_hz=0.4, beta=0.02)
    assert not info["rigid"] and 0.85 < G < 1.3
    assert CP.cp_leeward(1.0) == -0.5 and abs(CP.cp_leeward(3.0) + 0.25) < 1e-9


# ---------------- CFS-18 / 19 ----------------

def test_cfs18_strap_omega_E_and_selected_Vn_stack():
    cfg = box(system="strap_braced", strap_Fy_ksi=50.0, strap_Ag_in2=0.4,
              diaphragm_MDD_ADVE=3.0)
    pkg = CP.build_package("strap", cfg, CE.run(cfg))
    cd = pkg["capacity_design"]
    assert abs(cd["Omega_E"] - 1.3) < 1e-9                          # Ry 1.1 + 0.2 finish
    assert "connection" in cd["basis"].lower()                      # Ry*Fy*Ag restricted
    ln = cd["lines"]["X:X2"]
    # Vn per bay = 0.4*50*w/sqrt(h^2+w^2): 20x10 -> 17.89, 10x10 -> 14.14; line 32.03 kip
    vn = 0.4 * 50 * (20 / math.hypot(10, 20) + 10 / math.hypot(10, 10))
    assert abs(ln["Vn_selected_kip"][1] - vn) < 1e-6
    assert ln["T_cd_seed_kip"] <= ln["T_Om0_stack_kip"]


def test_cfs19_strap_ductility_gr33_fails_numerically():
    cfg = box(system="strap_braced", strap_Fy_ksi=33.0)
    pkg = CP.build_package("s33", cfg, CE.run(cfg))
    sd = pkg["capacity_design"]["strap_ductility"]
    assert sd["ok"] is False and sd["ratio_RtFu_RyFy"] < 1.2
    assert any("STRAP DUCTILITY FAILS" in w for w in pkg["preflight_warnings"])
    ok = CS.strap_ductility_check(50.0, Ag_in2=0.3, An_in2=0.26)
    assert ok["ok"] is True                    # 1.1*0.26*65 = 18.59 > 1.1*0.3*50 = 16.5


# ---------------- CFS-20 ----------------

def test_cfs20_type_ii_ca_divides_chords_and_seeds_uplift():
    cfg = box()
    pk1 = CP.build_package("t1", cfg, CE.run(cfg))
    cfg2 = dict(cfg, type_ii={"X:X2": dict(Ca=0.8)})
    pk2 = CP.build_package("t2", cfg2, CE.run(cfg2))
    h1 = [h for h in pk1["holddowns"] if h["id"] == "hd-X-X2"][0]
    h2 = [h for h in pk2["holddowns"] if h["id"] == "hd-X-X2"][0]
    assert abs(h2["T_cum_kip"] - h1["T_cum_kip"] / 0.8) <= 0.11
    assert abs(h2["T_cd_seed_kip"] - h1["T_cd_seed_kip"] / 0.8) <= 0.11
    t = h2["type_ii"]
    # uplift t = V/(Ca*Sum Li) at the capacity level, story 1
    ln = pk2["capacity_design"]["lines"]["X:X2"]
    V1 = ln["Ve_cap_by_story_kip"][1]
    assert abs(t["uplift_t_plf_by_story_capacity"][1] - V1 * 1000 / (0.8 * 30.0)) < 5.0
    # Ca from Table E1.3.1.2-1 by interpolation
    cfg3 = dict(cfg, type_ii={"X:X2": dict(pct_full_height=60, max_opening_height_ratio=0.5)})
    s = CP.type_ii_spec(cfg3, "X", cfg["lines_x"][1])
    assert abs(s["Ca"][1] - 0.83) < 1e-9


# ---------------- CFS-21 ----------------

def test_cfs21_no_om0_reduction_under_concrete_topping_or_rigid():
    cfg = box(diaphragm_MDD_ADVE=None)
    res = CE.run(cfg)
    assert not res["directions"]["X"]["drift_flags"]
    assert CP._om0_eff(cfg, "X", res)[0] == 2.5                     # WSP flexible -> 3.0 - 0.5
    assert CP._om0_eff(dict(cfg, diaphragm_topping_in=3.0), "X", res)[0] == 3.0
    assert CP._om0_eff(dict(cfg, diaphragm_material="concrete slab"), "X", res)[0] == 3.0
    assert CP._om0_eff(dict(cfg, diaphragm="semi-rigid"), "X", res)[0] == 3.0
    assert CP._om0_eff(dict(cfg, diaphragm_topping_in=1.5), "X", res)[0] == 2.5


# ---------------- CFS-22 ----------------

def test_cfs22_fpx_and_numeric_collector_seed():
    cfg = mezz()
    e = CE.elf(cfg)
    fp = CE.fpx(cfg, e)
    # one level: Fpx(12.10-1) = V; 12.10-2 = 0.2*0.35*85.8 = 6.006; 12.10-3 = 12.012
    assert abs(fp[1]["Fpx_eq1"] - e["V"]) < 1e-9 and abs(fp[1]["Fpx_min"] - 6.006) < 1e-3
    cfg2 = dict(cfg, collector_lines=[dict(name="B", direction="X", trib_fraction=0.5,
                                           transfer_kip={1: 1.0})])
    pkg = CP.build_package("m", cfg2, CE.run(cfg2))
    c = pkg["collectors"][0]
    # SDC C: 12.10.2.1 max(Om0*Fx, Om0*Fpx, Fpx_min) = 2.0*7.5075 = 15.015; 0.5x + 1.0*Om0
    assert c["by_direction"]["X"]["SDC"] == "C"
    assert abs(c["demand_kip_by_level"][1] - (0.5 * 15.015 + 2.0)) < 0.01
    # SDC B gypsum: 12.10.2.1 n/a -> S400 B3.4 basis
    g = box(system="gypsum_wall", sds=0.25, sd1=0.10, s1=0.06, collector_lines=["re-entrant"])
    pg = CP.build_package("g", g, CE.run(g))
    assert "S400 B3.4" in pg["collectors"][0]["by_direction"]["X"]["levels"][1]["basis"]


# ---------------- CFS-27 ----------------

def test_cfs27_drift_row_by_stories_and_finishes_not_light_frame():
    assert CS.drift_limit("sbmf", 2, "II", finishes_accommodate=True) == 0.025
    assert CS.drift_limit("sbmf", 2, "II", finishes_accommodate=False) == 0.020
    assert CS.drift_limit("strap_braced", 5, "II", finishes_accommodate=True) == 0.020
    assert abs(CS.drift_limit("sbmf", 2, "II", True, SDC="D", rho=1.3) - 0.025 / 1.3) < 1e-12
    # consistency's call (no flag) must not reject a correct 0.025 on a <= 4-story SBMF
    assert CS.drift_limit("sbmf", 2, "II") == 0.025
    dl, basis = CE.drift_limit_for(box(system="sbmf", sds=0.3, sd1=0.1, s1=0.05,
                                       drift_tolerant_finishes=True))
    assert dl == 0.025 and "row 1" in basis
    dl2, b2 = CE.drift_limit_for(box(system="sbmf", sds=0.3, sd1=0.1, s1=0.05))
    assert dl2 == 0.020 and "NOT declared" in b2
    dl3, _ = CE.drift_limit_for(box(drift_limit=0.015))            # tighter user value honoured
    assert dl3 == 0.015
    # footnote a: single story + finishes accommodate -> no limit (numeric 1.0 sentinel in res)
    m = mezz(drift_tolerant_finishes=True, drift_limit_no_limit_single_story=True)
    r = CE.run(m)
    assert r["directions"]["X"]["drift_limit_none"] and r["directions"]["X"]["drift_limit"] == 1.0
    pk = CP.build_package("fa", m, r)
    assert all(d["limit"] is None and d["ok"] for d in pk["drift_table"])


# ---------------- CFS-28 ----------------

def test_cfs28_per_direction_systems():
    sx = CS.seis_cfs(1.0, 0.45, 0.45, "strap_braced")
    sy = CS.seis_cfs(1.0, 0.45, 0.45, "wsp_shearwall")
    cfg = box(seis=sx, system="strap_braced", seis_by_dir=dict(X=sx, Y=sy),
              system_by_dir=dict(X="strap_braced", Y="wsp_shearwall"))
    res = CE.run(cfg)
    VX, VY = res["elf_by_dir"]["X"]["V"], res["elf_by_dir"]["Y"]["V"]
    assert abs(VX / VY - 6.5 / 4.0) < 1e-6                        # Cs = SDS/(R/Ie) both ways
    assert res["directions"]["Y"]["system"] == "wsp_shearwall"
    pkg = CP.build_package("mixed", cfg, res)
    assert pkg["system_by_direction"] == dict(X="strap_braced", Y="wsp_shearwall")
    om = {c.get("dir"): c["E"] for c in pkg["combos"] if c["label"].startswith("overstrength:")
          or c["label"].startswith("overstrength X") or c["label"].startswith("overstrength Y")}
    assert om == {"X": 2.0, "Y": 3.0}
    assert set(pkg["capacity_design"]["by_direction"]) == {"X", "Y"}


def test_cfs28_horizontal_combination_least_R_and_exception():
    lsys = {"X:X1": "strap_braced"}                                 # X1 strap, others WSP
    c3 = box(line_systems=lsys)                                     # 3 stories -> no exception
    d = CE.direction_seismic(dict(c3, use_12_2_3_3_exception=True), "X")
    assert d["system"] == "strap_braced" and d["seis"]["R"] == 4.0 and d["per_line"] is None
    assert any("REFUSED" in n for n in d["notes"])
    c2 = box(stories=2, line_systems=lsys, use_12_2_3_3_exception=True)
    d2 = CE.direction_seismic(c2, "X")
    assert d2["per_line"]["X2"][0] == "wsp_shearwall" and d2["per_line"]["X1"][0] == "strap_braced"
    res = CE.run(c2)
    # X2 (WSP, R=6.5) is loaded with its own R; X1 (strap) with R=4 -> ratio 6.5/4 per trib
    dist = res["directions"]["X"]["dist"]
    r_least = CE.elf(c2, seis=d2["per_line"]["X1"][1])["V"] / \
        CE.elf(c2, seis=d2["per_line"]["X2"][1])["V"]
    assert abs(r_least - 6.5 / 4.0) < 1e-6
    # tributaries 12.5 / 25 ft: X2/X1 = 2 * V(R=6.5)/V(R=4)
    assert abs(dist[1]["X2"]["V"] / dist[1]["X1"]["V"] - 2.0 / r_least) < 1e-6

"""CFS portal path (steel_engine/cfs_frame.py + the portal branch of cfs_pipeline.py):
regression tests for CFS-02/03/05/12/15/16/17/23/24/25/26 (review register CFS.md)."""
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ENG = os.path.join(os.path.dirname(HERE), "steel_engine")
if ENG not in sys.path:
    sys.path.insert(0, ENG)

import cfs_frame as CF          # noqa: E402
import cfs_pipeline as CP       # noqa: E402


@pytest.fixture(scope="module")
def demo():
    cfg = CF._demo_cfg()
    return cfg, CF.run(cfg)


# ---------------- CFS-03: end-force recovery with the assembled stiffness ----------------

def _cantilever(P=0.0):
    fr = CF.Frame2D()
    fr.node(1, 0.0, 0.0); fr.node(2, 0.0, 100.0); fr.node(3, 0.0, 200.0)
    fr.elem(1, 2, 29500.0 * 5, 29500.0 * 50, "c")
    fr.elem(2, 3, 29500.0 * 5, 29500.0 * 50, "c")
    fr.support(1, True, True, True)
    fr.load_node(3, 1.0, -P, 0.0)
    return fr


def test_recovery_uses_scaled_stiffness():
    # register repro: H = 1 kip at 200 in, stiff_scale 0.8 -> element end M was 250 vs 200
    fr = _cantilever()
    s = fr.solve(stiff_scale=0.8)
    assert s["end_forces"][0][2] == pytest.approx(200.0, abs=1e-6)
    assert s["reactions"][1][2] == pytest.approx(200.0, abs=1e-6)
    assert s["end_forces"][0][1] == pytest.approx(1.0, abs=1e-9)
    assert fr.equilibrium_residual(s) < 1e-8


def test_recovery_includes_pdelta_and_matches_reactions():
    fr = _cantilever(P=20.0)
    s = fr.solve(axials={0: 20.0, 1: 20.0}, stiff_scale=0.9, ei_scale={0: 0.8})
    assert fr.equilibrium_residual(s) < 1e-8
    assert s["end_forces"][0][2] == pytest.approx(s["reactions"][1][2], abs=1e-8)
    assert s["end_forces"][0][2] > 200.0          # second-order moment present


def test_every_portal_combo_in_equilibrium(demo):
    _cfg, res = demo
    assert max(c["equilibrium_residual"] for c in res["combos"].values()) < 1e-6


# ---------------- CFS-24: AISI S100-16 C1.1 direct-analysis constants ----------------

def test_direct_analysis_constants(demo):
    cfg, res = demo
    da = res["direct_analysis"]
    assert da["stiffness_EA"] == pytest.approx(0.90)
    assert da["notional_ratio"] == pytest.approx(1.0 / 240.0)
    assert "C1.1" in da["basis"]
    assert CF._tau_b(0.4, 1.0) == 1.0
    assert CF._tau_b(0.75, 1.0) == pytest.approx(4 * 0.75 * 0.25)


def test_notional_is_yi_over_240_in_gravity_combo():
    cfg = CF._demo_cfg()
    secs = dict(col=CF.frame_section(cfg["col_section"]), raf=CF.frame_section(cfg["raf_section"]))
    fr, mm, meta = CF.build_portal(cfg, secs)
    cases = {"D": CF._case_loads(fr, mm, meta, cfg, "D")}
    _f, _m, _meta, sol = CF._solve_combo(cfg, secs, {"D": 1.4}, cases, 0.9,
                                         notional=CF.DA_NOTIONAL, tau_b=True)
    Ry = sum(r[1] for r in sol["reactions"].values())
    Rx = sum(r[0] for r in sol["reactions"].values())
    assert abs(Rx) == pytest.approx(Ry / 240.0, rel=1e-6)


def test_notional_follows_lateral_resultant(demo):
    _cfg, res = demo
    for name, sgn in (("1.2D+1.0W+0.5Lr", 1.0), ("1.2D+1.0W_R+0.5Lr", -1.0)):
        Rx = sum(r[0] for r in res["combos"][name]["reactions_kip"].values())
        assert Rx * sgn < 0            # base shear opposes the (wind + notional) push


# ---------------- CFS-02 / CFS-26: both sides, signs, paired forces, apex ----------------

def test_package_envelopes_both_columns_and_rafters(demo):
    cfg, res = demo
    pkg = CP.build_portal_package("t", cfg, res)
    mem = {m["member"]: m for m in pkg["members"]}
    for g in ("col", "raf"):
        true = max(e["M_kipin"] for n, c in res["combos"].items() if c["role"] == "strength"
                   for lab, e in c["envelope"].items() if lab.startswith(g))
        assert mem[g]["M_kipin"] == pytest.approx(true)
        bases = {p["basis"] for p in mem[g]["demand_pairs"]}
        assert {"max |M|", "max compression", "max tension (uplift)"} <= bases
    # the demo's governing column is the RIGHT column (left-only seeding missed it)
    assert res["governing_label"]["col"] == "col_R"


def test_mirrored_wind_is_the_mirror_image(demo):
    _cfg, res = demo
    a = res["combos"]["0.9D+1.0W"]["envelope"]
    b = res["combos"]["0.9D+1.0W_R"]["envelope"]
    assert a["col_L"]["M_kipin"] == pytest.approx(b["col_R"]["M_kipin"], rel=0.02)
    assert a["raf_L"]["M_kipin"] == pytest.approx(b["raf_R"]["M_kipin"], rel=0.02)


def test_apex_slot_is_the_ridge_moment_and_knees_both_sides(demo):
    cfg, res = demo
    pkg = CP.build_portal_package("t", cfg, res)
    con = {c["joint"]: c for c in pkg["connections"]}
    assert set(con["knee"]["by_joint"]) == {"knee_L", "knee_R"}
    ridge = max(abs(c["joints"]["apex"]["M"]) for c in res["combos"].values()
                if c["role"] == "strength")
    assert con["apex"]["M_transfer_kipin"] == pytest.approx(ridge, abs=0.11)
    assert con["apex"]["M_transfer_kipin"] < 0.9 * con["knee"]["M_transfer_kipin"]
    assert con["knee"]["M_neg_kipin"] < 0 and con["knee"]["M_pos_kipin"] >= 0


def test_monoslope_has_no_apex_slot():
    cfg = dict(CF._demo_cfg(), monoslope=True, eave_ft=14.0, apex_ft=18.0, snow_pg=0.0)
    res = CF.run(cfg)
    pkg = CP.build_portal_package("m", cfg, res)
    assert "apex" not in {c["joint"] for c in pkg["connections"]}


# ---------------- CFS-23: wind-free branches, crane L combos ----------------

def test_wind_free_and_uplift_combos(demo):
    _cfg, res = demo
    names = set(res["combos"])
    assert {"1.2D+1.6Lr", "1.2D+1.0S_bal", "1.2D+1.6Lr+0.5W", "0.9D+1.0W",
            "0.9D+1.0W_R", "0.9D+1.0Wpar"} <= names


def _crane_cfg():
    return dict(CF._demo_cfg(), crane=dict(type="monorail", rated_kip=4.0,
                                           hoist_trolley_kip=0.6, supports=[dict(x_ft=20.0)],
                                           traction_y_ft=13.0))


def test_crane_impact_lateral_and_combos():
    cfg = _crane_cfg()
    cl = CF.crane_loads(cfg)
    assert cl["vertical"][0][2] == pytest.approx(4.6 * 1.25)      # 4.9.3 monorail 25%
    assert cl["H_lateral_kip"] == pytest.approx(0.2 * 4.6)        # 4.9.4
    assert cl["longitudinal_kip"] == pytest.approx(0.46)          # 4.9.5 (out of plane)
    names = [n for n, _c in CF.lrfd_combos(cfg)]
    for n in ("1.2D+1.6L(H+)+0.5Lr", "1.2D+1.6L(H-)+0.5Lr", "1.2D+1.6Lr+L(H+)",
              "1.2D+1.0W+L(H-)+0.5Lr"):
        assert n in names
    secs = dict(col=CF.frame_section(cfg["col_section"]), raf=CF.frame_section(cfg["raf_section"]))
    fr, mm, meta = CF.build_portal(cfg, secs)
    loads = CF._case_loads(fr, mm, meta, cfg, "L")
    assert sum(-v[1] for k, t, v in loads if k == "n") == pytest.approx(5.75)


def test_crane_type_and_impact_validated():
    with pytest.raises(CF.PortalInputError):
        CF.crane_loads(dict(_crane_cfg(), crane=dict(type="monorail", rated_kip=4.0,
                                                    supports=[dict(x_ft=20.0)], impact=0.1)))


# ---------------- CFS-25: self-weight, point loads, custom_build, pm ----------------

def test_self_weight_in_dead_case():
    cfg = CF._demo_cfg()
    secs = dict(col=CF.frame_section(cfg["col_section"]), raf=CF.frame_section(cfg["raf_section"]))
    fr, mm, meta = CF.build_portal(cfg, secs)
    D = CF._case_loads(fr, mm, meta, cfg, "D")
    CF._apply(fr, D, 1.0)
    Ry = sum(r[1] for r in fr.solve()["reactions"].values())
    raf_len = 2.0 * math.hypot(20.0, 3.0)
    roof = 4.5 * 15.0 / 1000.0 * raf_len
    sw = CF._self_weight_total(fr, mm, meta)
    exp_sw = (secs["col"]["A"] * 2 * 14.0 * 12.0 + secs["raf"]["A"] * raf_len * 12.0) * \
        CF.STEEL_KIP_PER_IN3
    assert sw == pytest.approx(exp_sw, rel=1e-6)
    assert Ry == pytest.approx(roof + sw, rel=1e-6)


def test_point_load_inserts_node_and_balances():
    cfg = dict(CF._demo_cfg(), point_loads=[dict(case="D", x_ft=13.3, P_kip=2.0)])
    secs = dict(col=CF.frame_section(cfg["col_section"]), raf=CF.frame_section(cfg["raf_section"]))
    fr, mm, meta = CF.build_portal(cfg, secs)
    assert any(abs(x - 13.3 * 12.0) < 1e-6 for x, _y in fr.nodes.values())
    loads = [l for l in CF._case_loads(fr, mm, meta, dict(cfg, self_weight=False), "D")
             if l[0] == "n"]
    assert sum(-v[1] for _k, _t, v in loads) == pytest.approx(2.0)


def test_custom_build_refused():
    with pytest.raises(CF.PortalInputError):
        CF.run(dict(CF._demo_cfg(), custom_build=lambda m: m))


def test_minimum_snow_pm_case():
    cfg = dict(CF._demo_cfg(), snow_pg=30.0)                 # ps = 21 < pm = 30 (RC II)
    names, _n = CF.snow_case_names(cfg)
    assert "S_min" in names and CF.snow_pm(cfg) == pytest.approx(30.0)
    assert CF.snow_pm(dict(cfg, risk_cat="I")) == pytest.approx(25.0)
    assert CF.snow_pm(dict(cfg, apex_ft=30.0)) == 0.0         # theta >= 15 deg


# ---------------- CFS-12: unit sanity ----------------

def test_x12_inch_cfg_refused():
    cfg = CF._demo_cfg()
    with pytest.raises(CF.PortalInputError, match="INCHES"):
        CF.check_units(dict(cfg, span_ft=480.0, eave_ft=168.0, apex_ft=204.0,
                            spacing_ft=180.0))
    with pytest.raises(CF.PortalInputError):
        CF.check_units(dict(cfg, eave_ft=168.0))
    assert CF.check_units(cfg) == []


# ---------------- CFS-15: enclosure, Cp(theta, h/L), branches, open-building CN ----------------

def test_cp_interpolation_matches_hand_values():
    ww, ww2 = CF.cp_windward_roof(11.31, 0.383)              # Ex16 hand interpolation
    assert ww == pytest.approx(-0.754, abs=1e-3)
    assert CF.cp_leeward_roof(11.31, 0.383) == pytest.approx(-0.431, abs=1e-3)
    assert CF.cp_windward_roof(14.04, 0.4375)[0] == pytest.approx(-0.688, abs=1e-3)   # Ex28
    assert CF.cp_leeward_roof(14.04, 0.4375) == pytest.approx(-0.490, abs=1e-3)
    assert CF.cp_windward_roof(14.0, 0.61)[1] == pytest.approx(-0.18)                 # Ex19 W3
    assert CF.cp_windward_roof(30.0, 0.25) == pytest.approx((-0.2, 0.3))


@pytest.mark.parametrize("enc,gcpi", [("enclosed", 0.18), ("partially_enclosed", 0.55),
                                      ("partially_open", 0.18), ("open", 0.0)])
def test_enclosure_gcpi(enc, gcpi):
    cfg = dict(CF._demo_cfg(), wind=dict(V=115.0, exposure="C", enclosure=enc))
    wc = CF.wind_cases(cfg)
    assert wc["GCpi"] == pytest.approx(gcpi)
    assert wc["qh_Kd_psf"] == pytest.approx(0.85 * wc["qh_psf"], rel=1e-3)   # qh w/o Kd


def test_closed_cases_cover_both_sides_branches_and_ridge():
    cfg = dict(CF._demo_cfg(), wind=dict(V=115.0, exposure="C", enclosure="enclosed",
                                         length_ft=120.0))
    names = {c["name"] for c in CF.wind_cases(cfg)["cases"]}
    assert {"W", "W2", "W_R", "W2_R", "Wpar", "Wpar2"} <= names
    assert any(n.endswith("_b2") for n in names)              # Fig. 27.3-1 note 3


def test_open_monoslope_uses_free_roof_cn():
    cfg = dict(CF._demo_cfg(), monoslope=True, eave_ft=14.0, apex_ft=15.0, span_ft=30.0,
               wind=dict(V=115.0, exposure="C", enclosure="open", flow="clear",
                         length_ft=80.0))
    wc = CF.wind_cases(cfg)
    c0 = wc["cases"][0]                                       # gamma = 0, case A, clear
    th = math.degrees(math.atan(1.0 / 30.0))
    assert th < 7.5
    qkG = wc["qh_Kd_psf"] * 0.85
    assert c0["roof_zones"][0][2] == pytest.approx(1.2 * qkG, rel=1e-3)    # CNW (Fig 27.3-4)
    assert c0["roof_zones"][-1][2] == pytest.approx(0.3 * qkG, rel=1e-3)   # CNL
    assert c0["walls"] == {}                                  # no wall load on an open frame
    assert any(c["name"].startswith("Wpar") for c in wc["cases"])


def test_legacy_enclosed_false_is_loud():
    cfg = dict(CF._demo_cfg(), wind=dict(V=115.0, exposure="C", enclosed=False))
    res = CF.run(cfg)
    assert any("ENCLOSURE" in w for w in res.get("preflight_warnings", []))


def test_open_side_has_no_wall_pressure():
    cfg = dict(CF._demo_cfg(), wind=dict(V=115.0, exposure="C",
                                         enclosure="partially_enclosed", open_sides=["L"]))
    for c in CF.wind_cases(cfg)["cases"]:
        assert c["walls"]["col_L"] == 0.0


# ---------------- CFS-16 / CFS-05: seismic drift, theta, rho, Omega_0, SBMF ----------------

def _seis_cfg(**kw):
    cfg = dict(CF._demo_cfg(), seis=dict(SDS=1.0, SD1=0.6, S1=0.4, R=3.0, Cd=3.0, Om0=3.0,
                                         Ie=1.0, W_frame_kip=8.0), system="not_detailed")
    cfg.update(kw)
    return cfg


def test_seismic_drift_theta_rho_om0():
    cfg = _seis_cfg()
    res = CF.run(cfg)
    se = res["seismic"]
    assert se["rho"] == 1.3                                   # SDC D default
    assert se["Delta_in"] == pytest.approx(se["Cd"] * se["delta_xe_in"] / se["Ie"], rel=1e-3)
    th = se["Px_kip"] * se["Delta_in"] * se["Ie"] / (se["V_kip"] * se["hsx_in"] * se["Cd"])
    assert se["theta"] == pytest.approx(th, abs=1e-4)
    assert se["theta_max"] == pytest.approx(min(0.5 / 3.0, 0.25), abs=1e-4)
    assert any("Om0E" in n for n in res["combos"])
    assert any("1.30E" in n for n in res["combos"])
    # not_detailed is NP in SDC D
    assert any("NOT PERMITTED" in w for w in res["preflight_warnings"])


def test_sbmf_expected_shear_matches_hand_calc():
    # Ex10 agent's independent E4.3.3 calc: Ve = 4.319 kip (VS 0.872, VB 3.447)
    r = CF.sbmf_expected_shear(21.5, 14.876, 1.711, pattern=3, N=2, t_beam_in=0.184,
                               Fu_beam=70.0, Rt_beam=1.1, t_col_in=0.5819, Fu_col=62.0,
                               Rt_col=1.2)
    assert r["VS_kip"] == pytest.approx(0.872, abs=1e-3)
    assert r["VB_max_kip"] == pytest.approx(6.722, abs=2e-3)
    assert r["Delta_B_max_in"] == pytest.approx(18.549, abs=0.01)
    assert r["Ve_kip"] == pytest.approx(4.319, abs=2e-3)


def test_sbmf_screens_fail_fixed_base_and_c_column():
    cfg = _seis_cfg(system="sbmf", base="fixed", truss_roof=True, apex_ft=14.0,
                    seis=dict(SDS=1.0, SD1=0.6, R=3.5, Cd=3.5, Om0=3.0, Ie=1.0,
                              W_frame_kip=8.0))
    res = CF.run(cfg)
    fails = [w for w in res["preflight_warnings"] if w.startswith("SBMF SCREEN FAIL")]
    assert any("E4.4.1(c)" in w for w in fails)
    assert any("E4.4.3(a)" in w for w in fails)
    assert "NOT EVALUATED" in res["sbmf"]["expected_shear"]["error"]


def test_hss_column_runs_on_portal_path():
    cfg = _seis_cfg(system="sbmf", truss_roof=True, apex_ft=14.0, col_section="HSS12X12X5/8",
                    raf_section="2x1800S350-118",
                    seis=dict(SDS=1.0, SD1=0.6, R=3.5, Cd=3.5, Om0=3.0, Ie=1.0,
                              W_frame_kip=8.0),
                    sbmf=dict(bolt_pattern=3, N=2, Fu_beam=70.0, Fu_col=62.0,
                              bolt_dia_in=1.0, n_bolts=8, beam_grade55=True,
                              column_splices=False))
    res = CF.run(cfg)
    assert res["sections"]["col"] == "HSS12X12X5/8"
    assert res["sbmf"]["expected_shear"]["Ve_kip"] > 0
    assert not [w for w in res.get("preflight_warnings", []) if "SBMF SCREEN" in w]


# ---------------- CFS-17: n-ply built-ups in the frame path ----------------

def test_nply_sections_and_per_ply_eff_iteration():
    s2 = CF.frame_section("2x1200S350-118")
    s4 = CF.frame_section("4x1200S350-118")
    assert s4["A"] == pytest.approx(2 * s2["A"]) and s4["Ix"] == pytest.approx(2 * s2["Ix"])
    with pytest.raises(ValueError):
        CF.frame_section("3x1200S350-118")
    cfg = CF._demo_cfg()
    r2 = CF.run(cfg)
    r4 = CF.run(dict(cfg, col_section="4x1200S350-118", raf_section="4x1200S350-97"))
    assert r4["eff_stiffness"]["n_ply"] == {"col": 4, "raf": 4}
    # per-ply stresses halve -> effective ratio does not drop; stiffer frame sways less
    assert r4["eff_stiffness"]["ratios"]["col"]["I_over_gross"] >= \
        r2["eff_stiffness"]["ratios"]["col"]["I_over_gross"] - 1e-9
    assert r4["service"]["eave_sway_in"] < 0.6 * r2["service"]["eave_sway_in"]


def test_knee_braces_need_explicit_geometry():
    cfg = dict(CF._demo_cfg(), knee_braces=dict(section="2x600S200-68"))
    with pytest.raises(CF.PortalInputError):
        CF.build_portal(cfg)
    cfg["knee_braces"].update(col_drop_ft=3.0, raf_run_ft=4.0)
    fr, mm, meta = CF.build_portal(cfg)
    assert len(mm["kb_L"]) == 1 and len(mm["kb_R"]) == 1


# ---------------- reference models still validate ----------------

def test_build_cfs_models_portal_gates():
    import build_cfs_models as B
    _docs, table = B.build_portals()
    assert all(st == "PASS" for _id, st, _p in table), table

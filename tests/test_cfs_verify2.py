"""Regression tests from the independent verification of the CFS review fixes (verify2).

Portal seismic block (cfs_frame._seismic_block):
  * ASCE 7-22 Eq. 12.8-18 theta uses Vx and Delta_xe of the SAME loading -- declaring a
    12.8.6.2 T_drift (which scales the drift displacement only) must not change theta.
  * Table 12.12-1 row 1 (0.025, RC II) needs DECLARED drift-tolerant finishes; undeclared ->
    'all other structures' (0.020), stated in the basis (wall-path convention, CFS-27).
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(HERE), "steel_engine")
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)

import cfs_frame as CF          # noqa: E402


def _seis_cfg(sds=1.0, sd1=0.6, s1=0.4, **kw):
    cfg = dict(CF._demo_cfg(), seis=dict(SDS=sds, SD1=sd1, S1=s1, R=3.0, Cd=3.0, Om0=3.0,
                                         Ie=1.0, W_frame_kip=8.0), system="not_detailed")
    cfg.update(kw)
    return cfg


def test_portal_theta_is_independent_of_T_drift():
    a = CF.run(_seis_cfg())["seismic"]
    cfg = _seis_cfg()
    cfg["seis"] = dict(cfg["seis"], T_drift=2.0)
    b = CF.run(cfg)["seismic"]
    # drift displacement is scaled by Cs_drift/Cs (12.8.6.2) ...
    assert b["delta_xe_in"] < 0.5 * a["delta_xe_in"]
    # ... but theta = Px Delta_xe / (Vx hsx) is a stiffness ratio of one loading (12.8.7)
    assert b["theta"] == pytest.approx(a["theta"], rel=1e-6)
    # hand: theta from the strength-level displacement and V (no T_drift)
    th = a["Px_kip"] * a["delta_xe_in"] / (a["V_kip"] * a["hsx_in"])
    assert a["theta"] == pytest.approx(th, rel=2e-3)


def test_portal_drift_limit_needs_declared_finishes():
    # SDC B (no 12.12.1.1 /rho), RC II
    lo = dict(sds=0.25, sd1=0.10, s1=0.06)
    und = CF.run(_seis_cfg(**lo))["seismic"]
    assert und["drift_limit"] == pytest.approx(0.020)
    assert "NOT declared" in und["drift_limit_basis"]
    dec = CF.run(_seis_cfg(drift_tolerant_finishes=True, **lo))["seismic"]
    assert dec["drift_limit"] == pytest.approx(0.025)
    no = CF.run(_seis_cfg(drift_tolerant_finishes=False, **lo))["seismic"]
    assert no["drift_limit"] == pytest.approx(0.020)
    # an explicitly declared limit is still used as declared
    usr = CF.run(_seis_cfg(drift_limit=0.015, **lo))["seismic"]
    assert usr["drift_limit"] == pytest.approx(0.015)


# ---------------- CFS-04: S400 omega2 takes the stud DESIGNATION thickness ----------------

import wall_line as WL          # noqa: E402
import cfs_engine as CE         # noqa: E402
import cfs_systems as CS        # noqa: E402


def test_assumed_schedule_uses_designation_thickness():
    """S400 A2.1: designation thickness = minimum base steel thickness in mils (33 mil ->
    0.033 in.), so the conservative ASSUMED (thinnest) stud has omega2 = 0.033/0.033 = 1.0."""
    legacy = dict(chord_area_in2=1.2, Gp_kip_in=9.0, en_in=0.03, k_anchor_kip_in=50.0)
    d = WL.s400_deflection(500.0, 9.5, 15.0, legacy, T_kip=5.0, system="wsp_shearwall")
    assert d["omega"]["w2"] == pytest.approx(1.0)
    assert any("t_stud_in=0.033" in a for a in d["assumed"])


def test_design_thickness_input_is_flagged_and_warned():
    p = dict(sheathing="osb", Gt_lb_in=77500.0, s_in=4.0, t_stud_in=0.0566, faces=1,
             chord_area_in2=2.0, k_anchor_kip_in=80.0)
    d = WL.s400_deflection(600.0, 9.0, 10.0, p, T_kip=10.0)
    assert any("DESIGN thickness" in n and "0.054" in n for n in d["notes"])
    ok = WL.s400_deflection(600.0, 9.0, 10.0, dict(p, t_stud_in=0.054), T_kip=10.0)
    assert not any("DESIGN thickness" in n for n in ok["notes"])
    # designation thickness -> larger omega2 -> larger shear + slip (ratio 0.0566/0.054)
    assert ok["shear"] / d["shear"] == pytest.approx(0.0566 / 0.054, rel=1e-9)
    seg = {1: [(10.0, 9.0)]}
    cfg = dict(stories=1, heights_ft=[9.0], plan_ft=(40.0, 40.0), D_floor=40.0, D_roof=20.0,
               clad=0.0, snow=0.0, L_floor=0.0,
               seis=CS.seis_cfs(1.0, 0.6, 0.5, "wsp_shearwall"), system="wsp_shearwall",
               risk_cat="II", structure_kind="wall", analysis_fidelity=0, diaphragm="flexible",
               lines_x=[WL.WallLine("A", 0.0, seg), WL.WallLine("B", 40.0, seg)],
               lines_y=[WL.WallLine("1", 0.0, seg), WL.WallLine("2", 40.0, seg)],
               wall_props=p, drift_tolerant_finishes=True)
    res = CE.run(cfg)
    assert any("DESIGN thickness" in w for w in res.get("preflight_warnings", []))

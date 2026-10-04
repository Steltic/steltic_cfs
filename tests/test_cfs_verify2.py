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

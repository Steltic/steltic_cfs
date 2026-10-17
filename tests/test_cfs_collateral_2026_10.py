"""October 2026: collateral load is not counted on to resist uplift or overturning -- the minimum-dead portal
combinations (0.9D + 1.0W, (0.9 - 0.2SDS)D + E) take it back out through the collateral-only case "Dc";
the QA annex prints the model-vs-tributary screen without the designer prompt; ' x ' in prose stays a letter.

    python -m pytest tests/test_cfs_collateral_2026_10.py -q
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ENG = os.path.join(os.path.dirname(HERE), "steel_engine")
if ENG not in sys.path:
    sys.path.insert(0, ENG)

import cfs_frame as CF          # noqa: E402


def test_minimum_dead_combinations_drop_collateral():
    cfg = dict(CF._demo_cfg(), collateral=3.0)
    combos = dict(CF.lrfd_combos(cfg))
    up = [c for n, c in combos.items() if n.startswith("0.9D+")]
    assert up and all(c.get("Dc") == pytest.approx(-0.9) for c in up)
    assert all("Dc" not in c for n, c in combos.items() if n.startswith(("1.2D", "1.4D", "(1.2")))
    e_min = [c for n, c in combos.items() if n.startswith("(0.9-0.2SDS)D")]
    assert all(c.get("Dc") == pytest.approx(-c["D"]) for c in e_min)


def test_no_collateral_no_extra_case():
    cfg = dict(CF._demo_cfg(), collateral=0.0)
    assert all("Dc" not in c for _n, c in CF.lrfd_combos(cfg))


def test_collateral_reduces_net_uplift():
    base = CF._demo_cfg()
    r0 = CF.run(dict(base, collateral=0.0))
    r1 = CF.run(dict(base, collateral=3.0))
    def uplift(res):
        c = res["combos"]["0.9D+1.0W"]
        return max(max(-r[1], 0.0) for r in c["reactions_kip"].values())
    # the 0.9D + 1.0W uplift does not benefit from collateral (it used to drop by 0.9 x collateral)
    assert uplift(r1) == pytest.approx(uplift(r0), rel=0.02, abs=0.05)


def test_times_sign_only_in_products():
    import report as R
    prev = R._CFS_PUB[0]
    R._CFS_PUB[0] = True
    try:
        assert "&times;" in R._pt("4.5 psf x 15.0 ft")
        assert "&times;" not in R._pt("the true x (east) position")
    finally:
        R._CFS_PUB[0] = prev

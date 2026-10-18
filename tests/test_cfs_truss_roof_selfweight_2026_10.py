"""October 2026: in a truss_roof frame the roof loads reach the eaves as truss reactions, but the level frame beam
(the 'rafter' members of the model) still carries its own weight in bending.

    python -m pytest tests/test_cfs_truss_roof_selfweight_2026_10.py -q
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENG = os.path.join(os.path.dirname(HERE), "steel_engine")
if ENG not in sys.path:
    sys.path.insert(0, ENG)

import cfs_frame as CF          # noqa: E402


def _loads(cfg):
    fr, members, meta = CF.build_portal(cfg)
    return members, meta, CF._case_loads(fr, members, meta, cfg, "D")


def test_truss_roof_beam_self_weight_is_a_member_load():
    cfg = dict(CF._demo_cfg(), truss_roof=True, D_roof=0.0, collateral=0.0)
    members, meta, out = _loads(cfg)
    raf = {i for lab in members if lab.startswith("raf") for i in members[lab]}
    assert raf
    member_raf = [x for x in out if x[0] == "m" and x[1] in raf]
    assert member_raf, "rafter / beam self-weight must load the member"
    assert all(abs(x[2][1]) > 0 for x in member_raf)


def test_truss_roof_roof_dead_load_still_goes_to_the_eaves():
    cfg = dict(CF._demo_cfg(), truss_roof=True, D_roof=4.5, collateral=0.0, self_weight=False)
    members, meta, out = _loads(cfg)
    raf = {i for lab in members if lab.startswith("raf") for i in members[lab]}
    assert not [x for x in out if x[0] == "m" and x[1] in raf]
    eL, eR = meta["eave_nodes"]
    assert any(x[0] == "n" and x[1] in (eL, eR) for x in out)

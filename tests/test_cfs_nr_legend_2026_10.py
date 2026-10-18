"""October 2026: in a custom / hand-model portal package the Section 4 design-model paragraph gives the
"n.r. = not reported by the design model" legend only when an n.r. cell is actually printed.

    python -m pytest tests/test_cfs_nr_legend_2026_10.py -q
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENG = os.path.join(os.path.dirname(HERE), "steel_engine")
if ENG not in sys.path:
    sys.path.insert(0, ENG)

import report as R          # noqa: E402


def _pkg(**mem):
    m = {"id": "rafter-1", "section": "2x 800S250-97", "governing_combo": "1.2D+1.6S", "P_design_kip": 4.2,
         "V_design_kip": 6.1, "M_design_kipin": 410.0, "capacity": "phiMn = 520 kip-in.", "DC": 0.79}
    m.update(mem)
    return {"design_model": {"type": "custom", "description": "hand two-span frame model"}, "members": [m]}


def test_no_nr_cell_no_legend():
    html = R._cfs_portal_design_section(_pkg(), {})
    assert "hand two-span frame model" in html
    assert "n.r. = not reported" not in html and "its values are not printed" in html


def test_nr_cell_keeps_the_legend(monkeypatch):
    # a member value identical to the outline frame's (no design value given) prints as n.r.
    monkeypatch.setattr(R, "_cfs_portal_seeds", lambda name, cfg, res: {"members": [{"id": "rafter-1", "P_kip": 4.2}]})
    html = R._cfs_portal_design_section(_pkg(P_design_kip=None, P_kip=4.2), {"x": 1}, res={"x": 1}, name="job")
    assert ">n.r.<" in html.replace(" ", "")
    assert "n.r. = not reported by the design model" in html

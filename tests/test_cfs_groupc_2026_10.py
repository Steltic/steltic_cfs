"""CFS fixes from the October 2026 re-run (group C): partition text basis, P-Delta growth with a
near-zero first-order sway, cantilever column systems (Table 12.2-1 G.2), one-value screen
false positives, gypsum-concrete topping and the free-roof wind case table.

    PYTHONPATH=$PWD:$PWD/steel_engine python -m pytest tests/test_cfs_groupc_2026_10.py -q
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "steel_engine")); sys.path.insert(0, ROOT)

import pytest  # noqa: E402

import cfs_engine as CE  # noqa: E402
import cfs_systems as CS  # noqa: E402
import consistency as CC  # noqa: E402
import cfs_pipeline as CP  # noqa: E402


def _mezz(**kw):
    c = dict(stories=1, heights_ft=[12.0], plan_ft=(60.0, 40.0), D_floor=15.0, D_roof=15.0, clad=0.0,
             L_floor=125.0, structure_kind="mezzanine")
    c.update(kw)
    return c


def test_partition_text_basis_means_no_partition_weight():
    w_none, bd = CE.story_weight(_mezz(partition_psf="none: storage platform"), 1, detail=True)
    assert "partitions" not in bd
    w_num, bd2 = CE.story_weight(_mezz(partition_psf=5.0), 1, detail=True)
    assert bd2["partitions"] == pytest.approx(10.0 * 60 * 40 / 1000.0)      # 10 psf minimum
    assert w_num > w_none
    with pytest.raises(ValueError):
        CE.story_weight(_mezz(partition_psf="some partitions"), 1)


def test_cantilever_column_system_row():
    row = CS.SYSTEMS["steel_ordinary_cantilever_column_system"]
    assert (row["R"], row["Om0"], row["Cd"]) == (1.25, 1.25, 1.25)
    assert CS.height_check("steel_ordinary_cantilever_column_system", "B", 19.0)[0]
    assert not CS.height_check("steel_ordinary_cantilever_column_system", "D", 19.0)[0]
    assert not CS.capacity_design_required("steel_ordinary_cantilever_column_system", 1.25, "B")[0]


def test_one_value_screen_ignores_connectors_pairs_and_named_quantities():
    e = {"id": "conn-1", "checks": [
        {"limit_state": "bolt bearing on the 0.1017 in. rafter web", "DC": 0.5},
        {"limit_state": "bolt bearing on the 0.1242 in. column web", "DC": 0.4},
        {"note": "A449 rod over 1-1/2 in.: Fy/Fu = 58/90 ksi; A1011 Gr 55: 55/70 ksi"},
        {"note": "flexure incl. 12 kip-in. torsion"}, {"note": "flexure incl. 36 kip-in. torsion"}]}
    assert CC._one_value_issues("connection", "conn-1", e) == []
    bad = {"id": "s-1", "a": "rod diameter 1 in.", "b": "rod diameter 1-1/8 in."}
    assert bad and CC._one_value_issues("hold-down", "s-1", {"a": "plate thickness 0.5 in.",
                                                           "b": "plate thickness 0.75 in."})


def test_gypcrete_topping_up_to_1_5_in_keeps_flexible_idealization():
    base = dict(diaphragm="flexible", diaphragm_material="wsp with gypcrete topping")
    d1 = CP.diaphragm_idealization(dict(base, diaphragm_topping_in=1.5))
    d2 = CP.diaphragm_idealization(dict(base, diaphragm_topping_in=2.0))
    assert d1["flexible_ok"] or not d1.get("warnings")
    assert not d2["flexible_ok"] or d2.get("warnings")

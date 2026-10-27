"""October 2026 final review (C7): CFS presentation items fixed in the engine.

- Model figure / viewer: optional cfg plan_outline_ft / plan_outline_by_story / line_plan_pos_ft draw L, Z, T and
  stepped plans and wall lines at their plan positions; without them an irregular plan is captioned as the analysis
  idealization.
- Portal viewer: no tool wording or file paths; combinations typeset as in the report; the eave sway is the report's
  serviceability value; no "? / ? kip" rows.
- QA annex: "Source verification ... verified", "code provision check", the S100 edition tag, the report title.
- Wind-case notation under the combinations table; lists of like records render as one table.

    PYTHONPATH=$PWD:$PWD/steel_engine python -m pytest tests/test_cfs_c7_presentation_2026_10.py -q
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "steel_engine")); sys.path.insert(0, ROOT)

import cfs_viewer3d as V  # noqa: E402
import report as R  # noqa: E402

ZPLAN = [(0, 0), (140, 0), (140, 58), (240, 58), (240, 116), (100, 116), (100, 58), (0, 58)]


def test_plan_rects_cover_the_outline_area():
    rects = V.plan_rects(ZPLAN)
    area = sum((x1 - x0) * (y1 - y0) for x0, x1, y0, y1 in rects)
    assert abs(area - (140 * 58 + 140 * 58)) < 1e-6


def test_line_extent_and_outline_fallback():
    assert V.line_extent_ft(ZPLAN, "X", 100.0) == (100.0, 240.0)
    assert V.line_extent_ft(ZPLAN, "Y", 20.0) == (0.0, 58.0)
    cfg = {"plan_ft": (240.0, 116.0)}
    assert V.plan_outline(cfg) == [(0.0, 0.0), (240.0, 0.0), (240.0, 116.0), (0.0, 116.0)]
    cfg2 = dict(cfg, plan_outline_ft=ZPLAN, plan_outline_by_story={6: [(0, 0), (100, 0), (100, 58), (0, 58)]})
    assert V.plan_outline(cfg2, 6)[1] == (100.0, 0.0) and V.plan_outline(cfg2, 2) == [tuple(map(float, p)) for p in ZPLAN]


def test_viewer_eave_sway_and_combo_text():
    pkg = {"drift_table": [{"check": "eave_sway", "value_in": "0.967 in.", "H_over": 734.0, "wind_case": "W2_b2"}]}
    assert V._pkg_eave_sway(pkg) == (0.967, 734.0, "W2_b2")
    assert V._pkg_eave_sway({"drift_table": [{"check": "eave_sway", "waived": "existing"}]}) is None
    assert V._combo_txt("?") is None
    assert "<sub>" in V._combo_txt("1.2D+1.0S_bal+0.5W2_R_b2")
    assert "agent" not in V._CAVEAT_COMMON and ".json" not in V._CAVEAT_COMMON


def test_wind_case_legend_only_for_used_subscripts():
    leg = R._cfs_wind_legend(["1.2D+1.0S_bal+0.5W2_R_b2", "0.9D+1.0Wpar"])
    assert "subscript R" in leg and "subscript b2" in leg and "par" in leg
    assert R._cfs_wind_legend(["1.2D+1.6L"]) == ""


def test_list_of_like_records_is_one_table():
    html = R._cfs_block_html({"delegated_design": [{"item": "a", "criteria": "b", "interface": "c"},
                                                   {"item": "d", "criteria": "e", "interface": "f"}]})
    assert html.count("<table") == 2 and "Criteria" in html


def test_qa_annex_wording():
    src = open(os.path.join(ROOT, "steel_engine", "report.py"), encoding="utf-8").read()
    for bad in ("<h3>Grounding verification</h3>", "library copy with Supplement 3-22", '"grounded" if ok',
                '"code provision look-up"', "confirmed against the geotechnical capacities"):
        assert bad not in src
    assert R._pretty_query("Omega0 overstrength phi_v") == "Ω₀ overstrength φv"

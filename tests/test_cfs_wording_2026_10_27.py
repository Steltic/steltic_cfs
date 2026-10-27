"""October 2026 fix-to-A campaign, CFS engine wording found while fixing the examples.

- The codes table lists AWS D1.3 only where sheet steel is welded and AWS D1.1 where the package cites it (welded
  plate washers and base gussets are D1.1 work).
- Raw keys type_ii_Ca / v_unit_over_Ca_plf print as engineering labels.
- The portal combinations note has no "[crane/point L]" or "+/-E" tokens; the portal seismic weight names one side
  wall when the other side is open.

    PYTHONPATH=$PWD:$PWD/steel_engine python -m pytest tests/test_cfs_wording_2026_10_27.py -q
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "steel_engine")); sys.path.insert(0, ROOT)

import report as R  # noqa: E402
import cfs_pipeline as CP  # noqa: E402

SRC = open(os.path.join(ROOT, "steel_engine", "report.py"), encoding="utf-8").read()
FRAME = open(os.path.join(ROOT, "steel_engine", "cfs_frame.py"), encoding="utf-8").read()


def test_raw_keys_have_labels():
    assert "C<sub>a</sub>" in R._hk("type_ii_Ca") and "type ii c a" not in R._hk("type_ii_Ca").lower()
    assert R._hk("v_unit_over_Ca_plf").endswith("(plf)")


def test_portal_combos_note_tokens():
    assert "[crane/point L]" not in CP.PORTAL_COMBOS_NOTE and "+/-E" not in CP.PORTAL_COMBOS_NOTE
    assert "E in both senses" in CP.PORTAL_COMBOS_NOTE


def test_d13_only_for_sheet_welds_and_d11_where_cited():
    assert 'rows.append(["AWS D1.3", "structural welding of sheet steel"])' in SRC
    i = SRC.index('rows.append(["AWS D1.3"')
    assert "arc[- ]spot" in SRC[i - 1200:i]
    assert 'if re.search(r"\\bweld", blob):\n        rows.append(["AWS D1.3"' not in SRC
    assert '"AWS D1.1"' in SRC


def test_one_open_side_wording():
    assert "side wall; the other side is open" in FRAME


def test_design_credit_has_no_date():
    pkg = {"design_agent": {"model": "Claude Opus 5.5", "provider": "Anthropic", "harness": "Steltic",
                            "date": "2026-10-27"}}
    assert R._cfs_design_credit(pkg) == "Designed by Claude Opus 5.5 (Anthropic) with Steltic"

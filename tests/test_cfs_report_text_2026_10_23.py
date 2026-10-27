"""October 2026 (R21i / R21k report text): the Section 7 pointer names the QA annex's "analysis screening notes" only
when the annex prints them; the grounding table counts "1 provision look-up" in the singular; the two-stage table
gives K_lower its unit; a portal package that cites AISI S240 lists S240-20 in the codes table, and a package may
state the scope of every AISI S400 section it uses ("s400_scope"), which the codes table prints in place of the
primary-system sentence and Section 6 does not repeat.

    python -m pytest tests/test_cfs_report_text_2026_10_23.py -q
"""
import html as _html
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (os.path.join(REPO, "steel_engine"), REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cfs_engine as CE        # noqa: E402
import cfs_pipeline as CP      # noqa: E402
import report as RPT           # noqa: E402
from test_cfs_wall_loads import box    # noqa: E402


def _text(html):
    return _html.unescape(re.sub(r"<[^>]+>", "", html))


def _wall():
    cfg = box(system="gypsum_wall", sds=0.25, sd1=0.10, s1=0.06, diaphragm_MDD_ADVE=3.0)
    res = CE.run(cfg)
    return cfg, res, CP.build_package("gyp", cfg, res)


def _no_screens(res, pkg):
    pkg["preflight_warnings"] = []
    res = dict(res, preflight_warnings=[])
    res["directions"] = {d: dict(dd, gate_flags=[]) for d, dd in (res.get("directions") or {}).items()}
    return res, pkg


# ---------------- 1. the Section 7 pointer names the screening notes only when the annex has them ----------------

def test_qa_pointer_omits_screening_notes_when_the_annex_has_none(tmp_path):
    cfg, res, pkg = _wall()
    res, pkg = _no_screens(res, pkg)
    assert not RPT._cfs_annex_screens(res, pkg)
    txt = _text(RPT._cfs_qa_section("gyp", str(tmp_path), cfg, res, pkg, annex="qa_annex.html"))
    assert "Design-process records (source verification of the cited standards and the design log) are in the QA annex" in txt
    assert "analysis screening notes" not in txt


def test_qa_pointer_names_screening_notes_when_the_annex_prints_them(tmp_path):
    cfg, res, pkg = _wall()
    res, pkg = _no_screens(res, pkg)
    pkg["preflight_warnings"] = ["a screen for the engineer"]
    assert RPT._cfs_annex_screens(res, pkg)
    txt = _text(RPT._cfs_qa_section("gyp", str(tmp_path), cfg, res, pkg, annex="qa_annex.html"))
    assert "analysis screening notes and the design log) are in the QA annex" in txt
    res2, pkg2 = _no_screens(res, dict(pkg, preflight_warnings=[]))
    res2["directions"] = {"X": {"gate_flags": ["X2 story 1: model 1.30 x tributary -- justify"]}}
    assert RPT._cfs_annex_screens(res2, pkg2)


# ---------------- 2. one look-up is "1 provision look-up" ----------------

def test_grounding_table_counts_one_look_up_in_the_singular(monkeypatch):
    cfg, res, pkg = _wall()
    recs = [{"tool": "search_engineering_standards", "detail": "[engineering_standards_S240] exact_section B1.2.3"},
            {"tool": "search_engineering_standards", "detail": "[engineering_standards_S100] exact_section E2"},
            {"tool": "search_engineering_standards", "detail": "[engineering_standards_S100] exact_section F2.1"}]
    monkeypatch.setattr(RPT, "_load_activity", lambda name: (recs, None))
    txt = _text(RPT._grounding_check(cfg, "gyp", pkg))
    assert "1 provision check" in txt and "1 provision checks" not in txt      # C7 (Oct 27): "check", not "look-up"
    assert "2 provision checks" in txt and "0 provision checks" in txt


# ---------------- 3. K_lower carries its unit ----------------

def test_two_stage_table_gives_K_lower_its_unit():
    pkg = {"two_stage_framework": {"by_direction": {"X": {"K_upper_kip_in": 120.0, "K_lower_kip_in": 4000.0,
                                                          "stiffness_ratio": 33.3, "T_upper_s": 0.40,
                                                          "T_combined_s": 0.41, "period_ratio": 1.02,
                                                          "status": "PASS"}},
                                   "amplification_basis": "(R/rho) upper / lower"}}
    h = RPT._cfs_framework_checks_html(pkg)
    assert "K<sub>lower</sub> (kip/in.)" in h and "K<sub>upper</sub> (kip/in.)" in h


# ---------------- 4. codes table: S240 for a portal that cites it; the stated S400 scope ----------------

def _portal_cfg():
    return dict(structure_kind="portal", span_ft=50.0, eave_ft=24.0, apex_ft=24.0, spacing_ft=12.5,
                system="sbmf", risk_cat="II", seis=dict(SDS=0.75, SD1=0.30, S1=0.30, R=3.5, Cd=3.5, Om0=3.0))


def test_portal_codes_table_lists_S240_only_where_cited():
    cfg = _portal_cfg()
    rows = RPT._cfs_codes_rows(cfg, {"members": [{"id": "frame-col", "cited": "AISI S100-16 E2"}]})
    assert not any(r[0] == "AISI S240-20" for r in rows)
    rows = RPT._cfs_codes_rows(cfg, {"members": [{"id": "truss", "cited": "AISI S240-20 B2.1 (truss chords)"}]})
    s240 = [r for r in rows if r[0] == "AISI S240-20"]
    assert s240 and "designed in this package" in s240[0][1]


def test_codes_table_prints_the_stated_S400_scope_and_section_6_does_not_repeat_it():
    cfg = _portal_cfg()
    scope = ("Section E4 (special bolted moment frames): transverse frames; Section E3 (strap-braced walls): "
             "side-wall end bays; expected-strength capacity design per B3.4")
    pkg = {"members": [{"id": "frame-col", "cited": "AISI S400-20 E4"}], "s400_scope": scope}
    row = [r for r in RPT._cfs_codes_rows(cfg, pkg) if r[0] == "AISI S400-20"][0]
    assert _text(row[1]) == scope
    assert "s400_scope" in RPT._CFS_PORTAL_SHOWN and "S400 scope" not in _text(RPT._cfs_extra_blocks(pkg))
    row = [r for r in RPT._cfs_codes_rows(cfg, {"members": []}) if r[0] == "AISI S400-20"][0]
    assert "Section E4" in _text(row[1])


# ---------------- 5. record labels and small numbers ----------------

def test_record_fields_print_readable_labels():
    assert RPT._hk("phi_vn_plf") == "&phi;v<sub>n</sub> (plf)"
    assert RPT._hk("P_cum_kip_by_story_design") == "cumulative axial by story, design (kip)"
    assert RPT._hk("story_stiffness_kip_per_inch") == "story stiffness (kip/in.)"
    assert RPT._hk("bays_below_cm") == "bays below the CM" and RPT._hk("ok") == "OK"
    assert RPT._hk("demand_kip_by_level") == "demand by level (kip)"


def test_small_numbers_print_in_fixed_notation():
    assert RPT._cn(6.0e-05) == "0.00006" and RPT._cn(9.7e-04) == "0.00097" and RPT._cn(-1.234e-07) == "-0.000000123"
    assert RPT._cn(0.0015) == "0.0015" and RPT._cn(0.25) == "0.25" and "e-" not in RPT._cn(3.3e-06)

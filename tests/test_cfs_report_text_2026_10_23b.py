"""October 2026 (figure and labels): the Section-1 model figure shows the whole building (no clipping at the square
3D drawing box, the zoom fitted to the figure), load combinations and load cases are typeset (ASCE 7 notation, the
same transform in the combinations table, the member tables and the prose; a load-case legend under the table uses
the same notation), package keys read as engineering labels
(Greek names as symbols, D/C, units in parentheses also when the unit sits inside the key), and the design log says
"clause not found in the reference library" where a look-up returned nothing. Fixed text follows the job: the analysis
method row (package text, component jobs), Section 7.1 rows that do not apply say "not applicable (reason)", the
subtitle follows the structure kind, the free-roof clause only for open roofs, the hold-down / anchorage sentence and
the QA-annex pointer list what the package holds, and Appendix A titles the portal elements by name and label.

    python -m pytest tests/test_cfs_report_text_2026_10_23b.py -q
"""
import base64
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (os.path.join(REPO, "steel_engine"), REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                  # noqa: E402
import cfs_engine as CE        # noqa: E402
import cfs_frame as CF         # noqa: E402
import cfs_pipeline as CP      # noqa: E402
import report as RPT           # noqa: E402
from test_cfs_wall_loads import box    # noqa: E402


# ---------------- 1. model figure: the whole building inside the figure ----------------

def _ink_columns(html):
    from PIL import Image
    m = re.search(r"data:image/png;base64,([A-Za-z0-9+/=]+)", html)
    assert m, "the figure is embedded"
    im = Image.open(io.BytesIO(base64.b64decode(m.group(1)))).convert("L")
    w, h = im.size
    px = im.load()
    cols = [x for x in range(w) if any(px[x, y] < 200 for y in range(0, h, 2))]
    rows = [y for y in range(h) if any(px[x, y] < 200 for x in range(0, w, 2))]
    return w, h, min(cols), max(cols), min(rows), max(rows)


@pytest.mark.parametrize("kind", ["wall", "portal"])
def test_model_figure_shows_the_whole_building(kind):
    if kind == "wall":
        cfg = box(stories=4, plan_ft=(160.0, 60.0))
        res = CE.run(cfg)
        pkg = CP.build_package("fig", cfg, res)
    else:
        cfg = CF._demo_cfg()
        res = CF.run(cfg)
        pkg = CP.build_portal_package("fig", cfg, res)
    probe = {}
    html = RPT._cfs_iso_figure(cfg, "fig", res, pkg, probe=probe)
    # every node and floor corner of the model projects inside the figure, and no 3D artist is clipped to the square
    # drawing box of the 3D axes (which spans only the middle half of the 16 x 7.5 in. figure)
    fx0, fx1, fy0, fy1 = probe["extent"]
    assert 0.0 < fx0 and fx1 < 1.0 and 0.0 < fy0 and fy1 < 1.0
    assert (fx1 - fx0) > 0.55 and not any(probe["clipped"])
    w, h, x0, x1, y0, y1 = _ink_columns(html)
    # the drawing (model and labels) spans most of the wide figure -- the old square 3D box clipped it to about half
    # of the width -- and stays off the figure edges
    assert (x1 - x0) > 0.60 * w
    assert x0 > 2 and x1 < w - 3 and y0 > 2 and y1 < h - 3


def test_model_figure_keeps_the_model_readable_with_very_long_labels():
    # the label margins are capped (30 % of the figure a side): the model keeps at least 40 % of the width
    import wall_line as WL
    segs = {k: [(20.0, 10.0), (10.0, 10.0)] for k in range(1, 4)}
    long = "-a-very-long-wall-line-name" * 6
    cfg = box(lines_x=[WL.WallLine("X%d%s" % (i, long), p, segs) for i, p in ((1, 0.0), (2, 25.0), (3, 50.0))],
              lines_y=[WL.WallLine("Y%d%s" % (i, long), p, segs) for i, p in ((1, 0.0), (2, 50.0), (3, 100.0))])
    res = CE.run(cfg)
    probe = {}
    html = RPT._cfs_iso_figure(cfg, "lng", res, CP.build_package("lng", cfg, res), probe=probe)
    assert "data:image/png" in html
    fx0, fx1, fy0, fy1 = probe["extent"]
    assert fx1 - fx0 > 0.35 and 0.0 < fx0 and fx1 < 1.0 and probe["zoom"] > 0


# ---------------- 2. load combinations and load cases ----------------

@pytest.mark.parametrize("label,out", [
    ("(1.2+0.2SDS)D+rho*EX++0.5L+0.15S", "(1.2 + 0.2S<sub>DS</sub>)D + &rho;E<sub>X</sub> + 0.5L + 0.15S"),
    ("(0.9-0.2SDS)D+rho*EY-", "(0.9 &minus; 0.2S<sub>DS</sub>)D &minus; &rho;E<sub>Y</sub>"),
    ("overstrength: (1.2+0.2SDS)D+Om0*E+1.0L+0.15S (2.3.6 combo 6; 12.10.2.1)",
     "overstrength: (1.2 + 0.2S<sub>DS</sub>)D + &Omega;<sub>0</sub>E + 1.0L + 0.15S (2.3.6 combo 6; 12.10.2.1)"),
    ("1.2D+1.0WY-+0.5L+(0.5Lr or 0.3S or 0.5R)",
     "1.2D &minus; 1.0W<sub>Y</sub> + 0.5L + (0.5L<sub>r</sub> or 0.3S or 0.5R)"),
    ("(1.2+0.2SDS)D+1.00E_neg+L(H+)+0.15S", "(1.2 + 0.2S<sub>DS</sub>)D &minus; 1.00E + L(H+) + 0.15S"),
    ("(0.9-0.2SDS)D+Om0E_neg", "(0.9 &minus; 0.2S<sub>DS</sub>)D &minus; &Omega;<sub>0</sub>E"),
    ("1.2D+1.0S_unb761_L+0.5W2_R_b2", "1.2D + 1.0S<sub>unb,L</sub> + 0.5W<sub>2,R,b2</sub>"),
    ("1.2D+1.0S_part_1+0.5Wpar_A_obs", "1.2D + 1.0S<sub>part,1</sub> + 0.5W<sub>par,A,obs</sub>"),
    ("0.9D+1.0Wlong2+1.0Qtor_R", "0.9D + 1.0W<sub>long2</sub> + 1.0Q<sub>tor,R</sub>"),
    ("1.4D", "1.4D")])
def test_combination_labels_are_typeset(label, out):
    assert RPT._cfs_combo_label(label) == out


def test_the_same_names_in_tables_cases_and_prose():
    assert RPT._cfs_case_label("W2_R_b2") == "W<sub>2,R,b2</sub>"
    assert RPT._cfs_case_label("S_bal") == "S<sub>bal</sub>"
    assert RPT._cfs_case_label("W") == "W"
    txt = RPT._pt("max compression (1.2D+1.0S_bal+0.5W2_b2): P/Pa + M/Ma; seismic rho E (rho = 1.0)")
    assert "S<sub>bal</sub>" in txt and "W<sub>2,b2</sub>" in txt and "&rho;E (&rho; = 1.0)" in txt
    assert "_" not in RPT._pt("Omega_E*Vn = 33 kip") and "&Omega;<sub>E</sub>Vn" in RPT._pt("Omega_E*Vn = 33 kip")
    cfg = box()
    pkg = CP.build_package("cmb", cfg, CE.run(cfg))
    sec = RPT._cfs_combos_section(cfg, {}, pkg)
    assert "rho*E" not in sec and "&rho;E<sub>X</sub>" in sec and "0.2S<sub>DS</sub>" in sec


def test_the_load_case_legend_uses_the_notation_of_the_table():
    # the legend a package may carry under the combinations, written for the analysis names of the cases
    pkg = dict(combos=[dict(label="1.2D+1.0S_bal+0.5W2_R_b2"), dict(label="(0.9-0.2SDS)D+Om0E_neg", role="overstrength"),
                       dict(label="(1.2+0.2SDS)D+1.00E_neg+0.15S")],
               combos_note="ASCE 7-22 2.3.1 LRFD. Load-case keys: W = wind from the left (+x) with +GCpi; W2 = the same "
                           "wind with -GCpi; the suffix _R = wind from the right (-x); the suffix _b2 = the second "
                           "windward-roof Cp branch; S_bal = balanced roof snow (7.3); E = the horizontal seismic load "
                           "effect toward +x; E_neg = the same toward -x; Om0E / Om0E_neg = the seismic load effect "
                           "with overstrength, toward +x / -x.")
    sec = RPT._cfs_combos_section({}, {}, pkg)
    assert "W<sub>2,R,b2</sub>" in sec and "&minus; &Omega;<sub>0</sub>E" in sec and "&minus; 1.00E" in sec
    note = sec[sec.index("<p class='tnote'>"):]
    assert "Load-case notation: W = wind" in note and "Load-case keys" not in note
    assert "W<sub>2</sub> = the same wind" in note and "the subscript R = wind from the right" in note
    assert "the subscript b2 = the second" in note and "S<sub>bal</sub> = balanced roof snow" in note
    assert "&minus;E = the same toward -x" in note
    assert "&Omega;<sub>0</sub>E / &minus;&Omega;<sub>0</sub>E = the seismic load effect" in note
    assert "_neg" not in note and "suffix" not in note
    # a note without a legend is printed as before
    assert RPT._cfs_case_legend("ASCE 7-22 2.3.1 LRFD; W2 = x") == "ASCE 7-22 2.3.1 LRFD; W2 = x"


# ---------------- 3. package keys as engineering labels ----------------

@pytest.mark.parametrize("key,label", [
    # an explicit _HK label wins over the generic typesetting
    ("P_cum_kip_by_story_design", "cumulative axial by story, design (kip)"),
    ("P_cum_kip_by_story_wind", "P<sub>cum</sub> by story wind (kip)"),
    ("Omega_E_story1", "&Omega;<sub>E</sub> story 1"),
    ("Omega_E_story1_bound", "&Omega;<sub>E</sub> story 1 bound"),
    ("T_cd_base_bound_kip", "T<sub>cd</sub> base bound (kip)"),
    ("vn_plf_by_story", "v<sub>n</sub> by story (plf)"),
    ("v_f_plf_by_story", "finish strength v<sub>finish</sub> by story (plf)"),
    ("v_f_plf_by_level", "v<sub>f</sub> by level (plf)"),
    ("Omega0_level_kip", "&Omega;<sub>0</sub> level (kip)"),
    ("Omega_E_Vn_by_story_kip", "&Omega;<sub>E</sub>V<sub>n</sub> by story (kip)"),
    ("Omega0_V_ELF_kip", "&Omega;<sub>0</sub>V<sub>ELF</sub> (kip)"),
    ("Omega_E_range", "&Omega;<sub>E</sub> range"),
    ("Omega0", "&Omega;<sub>0</sub>"),
    ("phiPn_c", "&phi;P<sub>n</sub>"),
    ("beam_phiMn_kipin", "beam &phi;M<sub>n</sub> (kip-in.)"),
    ("phivn_plf", "&phi;v<sub>n</sub> (plf)"),
    ("RyFyAg_kip", "R<sub>y</sub>F<sub>y</sub>A<sub>g</sub> (kip)"),
    ("corner_chord_DC", "corner chord D/C"),
    ("demand_kip_by_level", "demand by level (kip)"),
    ("story_stiffness_kip_per_inch", "story stiffness (kip/in.)"),
    ("Delta_S_in", "&Delta;<sub>S</sub> (in.)"),
    ("theta_max", "&theta;<sub>max</sub>"),
    ("line_story", "line, story"),
    ("V_mph", "V (mph)"),
    ("bolts", "bolts")])
def test_key_labels(key, label):
    assert RPT._hk(key) == label


def test_greek_labels_never_double_an_entity():
    for k in ("Delta_B_max_in", "slip_drift_Delta_S_in", "gypsum_phi_vn_wind_plf", "phi", "rho_basis", "theta_deg",
              "strap_end_screws_phiRn_kip", "Om0_eff_basis", "lambda"):
        lab = RPT._hk(k)
        assert "&&" not in lab and not re.search(r"&\w+;;", lab), (k, lab)


# ---------------- 4. design log ----------------

def test_design_log_wording():
    assert RPT._log_txt("search_engineering_standards", "0 hits", result=True) == \
        "clause not found in the reference library"
    h = RPT._activity_section("no_such_job_for_the_log_test")
    assert "corpus" not in h and "reference library" in h


# ---------------- 5. prose: combination terms spaced, phi symbols subscripted ----------------

def test_prose_combinations_are_spaced_as_in_section_2():
    assert RPT._pt("governs (1.2D+1.6Lr+0.5W2_b2): P/Pa") == \
        "governs (1.2D + 1.6L<sub>r</sub> + 0.5W<sub>2,b2</sub>): P/Pa"
    assert RPT._pt("phiMn = 1283 kip-in.; Pu/phiPn") == "&phi;M<sub>n</sub> = 1283 kip-in.; Pu/&phi;P<sub>n</sub>"
    assert RPT._pt("E = 2.9e+04 ksi") == "E = 2.9e+04 ksi"            # an exponent is not a load term
    assert RPT._pt("L(H+) and 0.9D+1.0W") == "L(H+) and 0.9D + 1.0W"
    assert RPT._cfs_combo_label("0.42 x C&C (girt deflection)") == "0.42 &times; C&amp;C (girt deflection)"


# ---------------- 6. analysis method row ----------------

def _portal():
    cfg = CF._demo_cfg()
    res = CF.run(cfg)
    return cfg, res, CP.build_portal_package("demo", cfg, res)


def _row(rows, label):
    return next(v for k, v in rows if k == label)


def test_analysis_method_row_follows_the_package_and_the_structure_kind():
    cfg, res, pkg = _portal()
    assert "planar frame analysis" in _row(RPT._cfs_basis_rows(cfg, res, pkg), "analysis method")
    pkg["analysis_method_text"] = "hand analysis of the propped frame (two-span continuous rafter)"
    assert _row(RPT._cfs_basis_rows(cfg, res, pkg), "analysis method") == \
        "hand analysis of the propped frame (two-span continuous rafter)"
    # a purlin / girt component job: the existing frames are not analysed
    cfg2 = dict(cfg, structure_kind="purlin")
    pkg2 = CP.build_portal_package("demo", cfg2, res)
    txt = _row(RPT._cfs_basis_rows(cfg2, res, pkg2), "analysis method")
    assert "planar frame analysis" not in txt and "existing frames not analysed" in txt
    pkg2["design_model"] = dict(type="custom", description="each purlin line is a continuous beam over the frames")
    assert _row(RPT._cfs_basis_rows(cfg2, res, pkg2), "analysis method") == \
        "each purlin line is a continuous beam over the frames"
    wcfg = box()
    wpkg = CP.build_package("w", wcfg, CE.run(wcfg))
    wpkg["analysis_method_text"] = "rigid-diaphragm stiffness distribution"
    assert _row(RPT._cfs_basis_rows(wcfg, {}, wpkg), "analysis method") == "rigid-diaphragm stiffness distribution"


# ---------------- 7. Section 7.1: rows that do not apply ----------------

def _qa(tmp_path, cfg, res, pkg):
    return RPT._cfs_qa_section("demo", str(tmp_path), cfg, res, pkg)


def _status(h, cat):
    m = re.search(r"<tr><td>%s</td><td>.*?</td><td>(.*?)</td></tr>" % re.escape(cat), h)
    assert m, cat
    return m.group(1)


def test_qa_rows_that_do_not_apply_say_why(tmp_path):
    cfg, res, pkg = _portal()
    h = _qa(tmp_path, cfg, res, pkg)
    for cat in ("Anchorage chain", "System and height limits", "ELF self-check"):
        assert "not applicable" not in _status(h, cat)
    # a purlin / girt job at SDC A on existing frames: bases existing, every drift row waived as existing
    cfg2 = dict(cfg, structure_kind="purlin", seis=dict(cfg["seis"], SDS=0.10, SD1=0.06, S1=0.05))
    pkg2 = CP.build_portal_package("demo", cfg2, res)
    pkg2["anchorage"] = [dict(id="base-anchor", waived="existing bases, outside this scope", waiver_scope="existing")]
    pkg2["drift_table"] = [dict(check="eave_sway", waived="existing frames", waiver_scope="existing")]
    h2 = _qa(tmp_path, cfg2, res, pkg2)
    assert "not applicable</span> (the column bases and their anchorage are existing" in _status(h2, "Anchorage chain")
    assert "SDC A" in _status(h2, "System and height limits") and "Table 12.2-1 is not used" in \
        _status(h2, "System and height limits")
    assert "SDC A" in _status(h2, "ELF self-check")
    assert "existing frames" in _status(h2, "Drift and stability")
    # no drift rows at all; and a category the package itself marks as not applicable
    pkg2["drift_table"] = []
    pkg2["qa_not_applicable"] = {"Code basis": "no screws or rods in this scope"}
    h3 = _qa(tmp_path, cfg2, res, pkg2)
    assert "no story drift or stability row" in _status(h3, "Drift and stability")
    assert _status(h3, "Code basis") == "<span class='na'>not applicable</span> (no screws or rods in this scope)"
    # a waived base anchorage on a frame designed here
    pkg["anchorage"] = [dict(id="base-anchor", waived="bases by the foundation engineer")]
    assert "the base anchorage is waived" in _status(_qa(tmp_path, cfg, res, pkg), "Anchorage chain")


def test_self_consistency_sentence_counts_only_the_applicable_checks(tmp_path, monkeypatch):
    import consistency
    monkeypatch.setattr(consistency, "check", lambda *a, **k: [])
    cfg, res, pkg = _portal()
    assert "every check listed in 7.1 passed" in _qa(tmp_path, cfg, res, pkg)
    cfg2 = dict(cfg, structure_kind="purlin", seis=dict(cfg["seis"], SDS=0.10, SD1=0.06, S1=0.05))
    assert "every applicable check listed in 7.1 passed" in _qa(tmp_path, cfg2, res, CP.build_portal_package(
        "demo", cfg2, res))


# ---------------- 8. subtitle, free-roof clause, hold-down sentence, annex pointer ----------------

def test_subtitle_follows_the_structure_kind():
    cfg, res, pkg = _portal()
    sub = lambda c, p=pkg: re.search(r"<p><b>(.*?)</b>", RPT._cfs_title_block("demo", c, res, p)).group(1)
    assert sub(cfg) == "Cold-formed steel portal frame"
    assert "purlins and girts" in sub(dict(cfg, structure_kind="purlin")) and "portal" not in \
        sub(dict(cfg, structure_kind="purlin"))
    assert sub(dict(cfg, structure_kind="canopy")) == "Cold-formed steel canopy frame"
    assert sub(dict(cfg, structure_kind="mezzanine")) == "Cold-formed steel mezzanine"
    assert sub(box()) == "Cold-formed steel light-frame building"
    assert sub(cfg, dict(pkg, report_subtitle="Cold-formed steel bus shelter")) == "Cold-formed steel bus shelter"


def test_free_roof_clause_only_for_open_roofs():
    cfg, res, pkg = _portal()
    pkg["wind_basis"]["source"] = "user"
    h = RPT._cfs_portal_loads(cfg, res, pkg)
    assert "MWFRS wind cases, psf (+ toward the surface)</h4>" in h and "free-roof" not in h
    pkg["wind_basis"]["enclosure"] = "open"
    assert "free-roof values are net pressures" in RPT._cfs_portal_loads(cfg, res, pkg)


def test_not_verified_sentence_and_annex_pointer_list_what_the_package_holds(tmp_path):
    cfg, res, pkg = _portal()
    h = _qa(tmp_path, cfg, res, pkg)
    assert "hold-down and rod products" not in h and "; concrete anchorage and delegated items are confirmed" in h
    wcfg = box()
    wres = CE.run(wcfg)
    wpkg = CP.build_package("w", wcfg, wres)
    assert "specific hold-down and rod products, concrete anchorage and delegated items" in _qa(tmp_path, wcfg, wres, wpkg)
    cfg2 = dict(cfg, structure_kind="purlin")
    pkg2 = CP.build_portal_package("demo", cfg2, res)
    assert "Appendix A; delegated items are confirmed" in _qa(tmp_path, cfg2, res, pkg2)
    # the annex pointer names the screening notes only when the package has them
    pkg["preflight_warnings"] = ["a screen"]
    a1 = RPT._cfs_qa_section("demo", str(tmp_path), cfg, res, pkg, annex="qa_annex.html")
    assert "standards, analysis screening notes and the design log) are in the QA annex" in a1
    pkg["preflight_warnings"] = []
    a2 = RPT._cfs_qa_section("demo", str(tmp_path), cfg, {}, pkg, annex="qa_annex.html")
    assert "screening notes" not in a2 and "(source verification of the cited standards and the design log)" in a2


# ---------------- 9. Appendix A titles; the 3D view keeps its D/C look-up ----------------

def test_appendix_titles_portal_elements_and_the_viewer_lookup_is_unchanged():
    import cfs_viewer3d as V
    cfg, res, pkg = _portal()
    for m in pkg["members"]:
        m["DC"] = 0.77 if m["member"] == "col" else 0.55
    h = RPT._cfs_appendix_calcs(pkg, cfg, res, "demo")
    for t in ("Frame columns (frame-col)", "Frame rafters (frame-raf)", "Column-to-rafter knee connection (conn-knee)",
              "Column-base anchorage (base-anchor)", "Purlin schedule (sched-purlin)"):
        assert "<h4>%s</h4>" % t in h, t
    assert "<td>member</td>" not in h and "<td>joint</td>" not in h
    # a schedule name with a qualifier reads 'Purlin schedule, qualifier'
    assert RPT._cfs_item_title("schedules", dict(schedule="purlin - through-fastened roof"), "sched-purlin") == \
        ("Purlin schedule, through-fastened roof (sched-purlin)", "schedule")
    assert RPT._cfs_item_title("schedules", dict(schedule="girt schedule"), "sched-girt")[0] == "Girt schedule (sched-girt)"
    assert [m["id"] for m in pkg["members"]] == ["frame-col", "frame-raf"]
    els = V.viewer_data(cfg, "demo", res, pkg)["elements"]
    assert {e["util"] for e in els if e["type"] == "column"} == {0.77}
    # wall-job items keep their element labels as titles
    wcfg = box()
    wpkg = CP.build_package("w", wcfg, CE.run(wcfg))
    wid = wpkg["wall_lines"][0]["id"]
    assert "<h4>%s</h4>" % wid in RPT._cfs_appendix_calcs(wpkg, wcfg, {}, "w")

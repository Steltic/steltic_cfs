"""October 2026 fix-to-A campaign, R24 review items in the CFS report and 3D viewer.

- The wind-case notation under the combinations names only the symbols the labels use: W_X / W_Y with the
  Fig. 27.3-8 Case 1 / Case 2 envelope on the wall path, the Fig. 27.3-1 cases on enclosed portals, the free-roof
  cases on open buildings, and nothing for a plain W.
- The viewer member panel prints typeset combinations (never the analysis key), a location row for wall-path
  members, and no "null" force rows.
- Portal longitudinal straps follow cfg["longitudinal_bracing"]; with no layout, no straps cross an open side.

    PYTHONPATH=$PWD:$PWD/steel_engine python -m pytest tests/test_cfs_r24_viewer_legend_2026_10.py -q
"""
import os, sys, re
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "steel_engine")); sys.path.insert(0, ROOT)

import report as R  # noqa: E402
import cfs_viewer3d as V  # noqa: E402

TPL = open(os.path.join(ROOT, "steel_engine", "viewer3d_template.html"), encoding="utf-8").read()
SRC_R = open(os.path.join(ROOT, "steel_engine", "report.py"), encoding="utf-8").read()


def _plain(h):
    return re.sub(r"<[^>]+>", "", h)


def test_wall_path_legend_is_shear_wall_notation():
    h = _plain(R._cfs_wind_legend(["1.2D+1.0WX++0.5L+(0.5Lr or 0.3S or 0.5R)", "0.9D+1.0WY-"]))
    assert "WX, WY" in h and "Fig. 27.3-8 Cases 1 and 2" in h
    assert "W2" not in h and "left-hand" not in h and "frames" not in h


def test_plain_w_has_no_legend():
    assert R._cfs_wind_legend(["1.2D+1.0W+0.5Lr (C&C inward)", "0.9D+1.0W (C&C net uplift)"]) == ""
    assert R._cfs_wind_legend(["1.4D", "(1.2+0.2SDS)D+rho*EX++1.0L+0.15S"]) == ""


def test_enclosed_portal_legend():
    h = _plain(R._cfs_wind_legend(["1.2D+1.6Lr+0.5W", "1.2D+1.6Lr+0.5W2_R_b2", "1.2D+1.6Lr+0.5Wpar2"]))
    assert "W, W2" in h and "subscript R" in h and "subscript b2" in h and "Wpar, Wpar2" in h
    assert "WX" not in h and "free-roof" not in h


def test_open_free_roof_legend():
    h = _plain(R._cfs_wind_legend(["1.2D+1.6Lr+0.5W_A_clr_L", "1.2D+1.6Lr+0.5W_B_obs_R", "1.2D+1.6Lr+0.5Wpar_A_clr"]))
    assert "free-roof wind across the frames" in h and "along the ridge (Fig. 27.3-7)" in h
    assert "GCpi (W)" not in h and "Wpar2" not in h and "subscript R =" not in h


def test_viewer_combo_is_typeset():
    assert V._combo_txt("1.2D+1.0S_bal+0.5W_R") == R._cfs_combo_label("1.2D+1.0S_bal+0.5W_R")
    assert "_" not in _plain(V._combo_txt("1.2D+1.6Lr+0.5W2_b2"))
    assert V._combo_txt(None) is None


def test_template_location_row_and_no_null_rows():
    assert "<td>Location</td>" in TPL
    assert "e.f[k] !== null" in TPL


def test_plan_north_in_caveat():
    assert "North = -X." in V._caveat_common({"plan_north": "-X"})
    assert "North = +Y." in V._caveat_common({})


class _Fr:
    """A two-column gable: base 1/4, eave 2/3, ridge 5 (rafter segments 2-5, 5-3)."""
    nodes = {1: (0.0, 0.0), 2: (0.0, 120.0), 5: (120.0, 160.0), 3: (240.0, 120.0), 4: (240.0, 0.0)}
    elems = [(1, 2), (2, 5), (5, 3), (3, 4)]


def _portal(monkeypatch, cfg):
    import cfs_frame as CF
    meta = {"base_nodes": [1, 4], "eave_nodes": [2, 3], "span_in": 240.0}
    members = {"col_L": [0], "raf_L": [1], "raf_R": [2], "col_R": [3]}
    monkeypatch.setattr(CF, "frame_section", lambda s: s)
    monkeypatch.setattr(CF, "build_portal", lambda c, s: (_Fr, members, meta))
    base = dict(path="portal", col_section="C", raf_section="C", spacing_ft=20.0, n_frames=6,
                span_ft=20.0, eave_ft=10.0)
    d = V._portal_data(dict(base, **cfg), "t", {"governing": {"col": "1.2D+1.0S_bal+0.5W_R"}}, {})
    return d


def _braces(d):
    return [e for e in d["elements"] if e["type"] == "brace"]


def test_default_straps_skip_open_side(monkeypatch):
    d = _portal(monkeypatch, {"wind": {"open_sides": ("L",)}})
    xs = {round(d["nodes"][str(e["n"][0])][0]) for e in _braces(d)}
    assert xs == {240}                      # only the closed right-hand side wall
    cols = [e for e in d["elements"] if e["type"] == "column"]
    assert cols and all("<sub>" in e["combo"] for e in cols)


def test_layout_header_and_roof_straps(monkeypatch):
    d = _portal(monkeypatch, {"wind": {"open_sides": ("L",)}, "longitudinal_bracing": {
        "side_bays": {"R": [1, 2, 4, 5]}, "header_bays": {"L": [1, 5]}, "header_bottom_ft": 8.0,
        "roof_bays": [1, 5]}})
    br = _braces(d)
    hdr = [e for e in br if e["sec"] == "header X-strap"]
    assert len(hdr) == 4
    zs = sorted(min(d["nodes"][str(n)][2] for n in e["n"]) for e in hdr)
    assert zs[0] == 96.0                    # header straps start at the header, not at the base
    side = [e for e in br if e["sec"] == "X-strap"]
    assert len(side) == 8 and all(d["nodes"][str(e["n"][0])][0] == 240.0 for e in side)
    assert len([e for e in br if e["sec"] == "roof X-strap"]) == 8


# ---- R24 component / portal template items (Ex25, Ex17, Ex29, QA annex) ----

def test_codes_extra_rows_and_aci_row_only_with_designed_anchorage():
    pkg = {"codes_extra": [["IEBC 2024", "existing building: alteration (Ch. 5)"]],
           "connections": [{"id": "base-anchor", "waived": "existing bases outside the scope"}]}
    rows = R._cfs_codes_rows({"span_ft": 100.0}, pkg)
    names = [r[0] for r in rows]
    assert names[1] == "IEBC 2024" and "ACI 318-19 Ch. 17" not in names
    rows2 = R._cfs_codes_rows({"span_ft": 100.0}, {"connections": [{"id": "base-anchor", "DC": 0.5}]})
    assert "ACI 318-19 Ch. 17" in [r[0] for r in rows2]
    assert "codes_extra" in SRC_R


def test_sdc_a_sfrs_row_does_not_cite_table_12_2_1_row_h():
    assert "the Table 12.2-1 coefficients are not used)" in SRC_R
    assert 'if _cfs_sdc_detail(cfg)[0] == "A" else' in SRC_R


def test_all_waived_drift_rows_read_not_applicable():
    assert "all on the existing structure outside this scope: not applicable" in SRC_R


def test_cc_wind_basis_label():
    assert '"Wind basis (components and cladding)" if _cc else "MWFRS wind basis"' in SRC_R


def test_model_key_and_qa_scope_have_no_raw_ids():
    assert "col_L/col_R" not in SRC_R.split("def _cfs_iso_figure")[1].split("Model key")[0].split('"""')[2]
    assert "frame path &mdash; S100 required" not in SRC_R
    assert "AISI S100 governs the members and connections" in SRC_R


def test_screen_text_and_statuses_not_in_capitals():
    import cfs_engine, preflight  # noqa: F401
    for mod in ("cfs_engine.py", "preflight.py", "cfs_pipeline.py"):
        src = open(os.path.join(ROOT, "steel_engine", mod), encoding="utf-8").read()
        for w in ("OCCUPIED", "ARE deliverables", "BUILDING structure", "FLEXIBLE idealization", "WITHHELD"):
            assert w not in src, (mod, w)
    assert 'str(v.get("status") or "").lower()' in SRC_R
    assert "flexible idealization NOT permitted" not in SRC_R


def test_mnlo_typeset_as_in_s100():
    assert '"phimnlo": "&phi;M<sub>n&#8467;o</sub>"' in SRC_R


def test_custom_model_viewer_drops_view_frame_combos(monkeypatch):
    d = _portal(monkeypatch, {"structure_kind": "component"})
    cols = [e for e in d["elements"] if e["type"] == "column"]
    assert cols and all(e["combo"] is None for e in cols)


# ---- R24 wall-path items left for the engine (Ex1, Ex10, Ex11, Ex14) ----

def test_mixed_system_notes_without_raw_keys():
    src = open(os.path.join(ROOT, "steel_engine", "cfs_engine.py"), encoding="utf-8").read()
    for raw in ("R = least R", "exception REFUSED", "12.2.3.3 EXCEPTION", "(line_systems) --"):
        assert raw not in src
    assert 'CS.SYSTEMS[least]["label"]' in src


def test_wind_wall_note_names_every_system():
    assert "_WIND_WALL_LABEL" in SRC_R and '"; ".join("%s: %s" % (_WIND_WALL_LABEL.get(x, x)' in SRC_R


def test_qa_sentence_r_per_direction():
    assert "{_qa_sys_txt(cfg)} (R = {_r_txt})" in SRC_R and "Special bolted moment frames" in SRC_R


def test_partition_row_is_the_w_weight():
    assert "Partition weight in the seismic weight W (&sect;12.7.2 item 2, floor levels)" in SRC_R
    assert "live load in Ch. 4; in W per" not in SRC_R


def test_codes_table_reads_cfg_materials_for_d11():
    rows = R._cfs_codes_rows({"span_ft": 60.0, "materials": [{"item": "Welds", "spec": "E70; AWS D1.1 (HSS)"}]}, {})
    assert "AWS D1.1" in [r[0] for r in rows]


def test_low_slope_roof_zones_in_surface_table():
    assert "Roof by distance from the windward edge (Fig. 27.3-1, &theta; &lt; 10&deg;)" in SRC_R


def test_no_girts_across_open_side_below_header(monkeypatch):
    d = _portal(monkeypatch, {"wind": {"open_sides": ("L",)},
                              "longitudinal_bracing": {"header_bottom_ft": 8.0, "header_bays": {"L": [1]}}})
    girts = [e for e in d["elements"] if e["sec"] == "girt"]
    for e in girts:
        x, _y, z = d["nodes"][str(e["n"][0])]
        assert not (x == 0.0 and z < 96.0)
    assert any(d["nodes"][str(e["n"][0])][0] == 240.0 for e in girts) or not girts
    assert "end-wall framing and bracing are not drawn" in d["meta"]["caveat"]


def test_r25w_engine_text_items():
    assert R._HK.get("ap_g") == "a<sub>p</sub>/g"
    assert R._qa_sys_txt({"system": "sbmf"}) == "Special bolted moment frames"
    assert R._qa_sys_txt({"system": "strap_braced", "system_by_dir": {"X": "strap_braced", "Y": "sbmf"}}).startswith("Light-frame walls and")
    pipe = open(os.path.join(ROOT, "steel_engine", "cfs_pipeline.py"), encoding="utf-8").read()
    assert "NET UPLIFT anchorage" not in pipe
    assert "before the accidental-torsion shift included in the table" in SRC_R
    assert '_pt(str(n)) for n in dict.fromkeys(map(str, notes))' in SRC_R

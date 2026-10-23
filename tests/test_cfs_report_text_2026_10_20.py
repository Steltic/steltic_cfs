"""October 2026 (report text): every demand / strength field of a limit-state check prints with its symbol and one
unit (no dash cells, no 'Ru (kip) = 3.6 kip'), readable drift-check and seismic-weight labels, the rain-basis
pointer only where the loads table has a rain row, a generic enclosure sentence in the portal wind basis, the basis
of every gravity-framing row, the retrofit wording for existing frames that are not analysed, unit suffixes for
s / sf / % / mph / mil / rad / pcf keys, headings that keep their acronyms, the diaphragm material quoted as
written, plus the two report hooks (clad_label, the analysis-screen evaluation in the QA annex, system names by
direction) and the per-citation AISC red flag.

    python -m pytest tests/test_cfs_report_text_2026_10_20.py -q
"""
import html as _html
import json
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
from test_cfs_code_basis_screens import ref_cfg    # noqa: E402


def _text(html):
    return _html.unescape(re.sub(r"<[^>]+>", "", html))


def _cells(check):
    d, s = RPT._check_dem_cap(check)
    return _text(d), _text(s)


# ---------------- 1. demand / strength cells of a limit-state check ----------------

@pytest.mark.parametrize("check,dem,cap", [
    (dict(Vu_kip=2.41, phiVsa_kip=7.57, DC=0.319), "Vu = 2.41 kip", "φVsa = 7.57 kip"),
    (dict(Vu_kip=2.41, phiVcb_kip=21.52, DC=0.112), "Vu = 2.41 kip", "φVcb = 21.5 kip"),
    (dict(Vu_kip=2.41, phiVcp_kip=32.29, DC=0.075), "Vu = 2.41 kip", "φVcp = 32.3 kip"),
    (dict(Mu_kipin=12.6, phiMnlo_kipin=64.59, DC=0.392), "Mu = 12.6 kip-in.", "φMnlo = 64.6 kip-in."),
    (dict(Pn_kip=6.73, Pu_kip=2.72, DC=0.436), "Pu = 2.72 kip", "Pn = 6.73 kip"),
    (dict(defl_in=0.044, limit_in=0.293, DC=0.15), "Δ = 0.044 in.", "limit = 0.293 in."),
    (dict(theta=0.022, theta_max=0.143, DC=0.153), "θ = 0.022", "θmax = 0.143"),
    (dict(Mu_kipin=276.06, Ms_kipin=292.248, DC=0.945), "Mu = 276.1 kip-in.", "Ms = 292.2 kip-in."),
    (dict(interaction=0.27, limit=1.0, DC=0.27), "interaction = 0.27", "limit = 1.0"),
    (dict(h_over_w=1.067, limit=2.0, DC=0.533), "h/w = 1.07", "limit = 2.0"),
    (dict(Tu_kip=2.193, Vu_kip=1.248, phiNsa_kip=9.83, phiVsa_kip=5.11, DC=0.177),
     "Tu = 2.19 kip; Vu = 1.25 kip", "φNsa = 9.83 kip; φVsa = 5.11 kip"),
    (dict(phiv_plf=1626.0, v_plf=1400.066, DC=0.861), "v = 1400 plf", "φvn = 1626 plf"),
    (dict(RtAnFu_kip=35.048, Ru_kip=31.372, DC=0.895), "Ru = 31.4 kip", "RtAnFu = 35 kip"),
    (dict(provided=1.3, required=1.2, DC=0.923), "required = 1.2", "provided = 1.3"),
    (dict(Pu_kip_per_ft=26.63, phiBn_kip_per_ft=119.34, DC=0.223), "Pu = 26.6 kip/ft", "φBn = 119.3 kip/ft"),
])
def test_check_cells_print_symbol_value_and_one_unit(check, dem, cap):
    d, s = _cells(dict(check, limit_state="x", cited="y"))
    assert (d, s) == (dem, cap)


def test_ratio_against_a_minimum_reads_the_way_the_dc_is_computed():
    # strap ductility Rt Fu / (Ry Fy) = 1.3 >= 1.2: D/C = 1.2 / 1.3, so the limit is the demand side
    assert _cells(dict(limit=1.2, ratio=1.3, DC=0.923)) == ("limit = 1.2", "ratio = 1.3")
    assert _cells(dict(ratio=0.8, limit=1.0, DC=0.8)) == ("ratio = 0.8", "limit = 1.0")


def test_no_doubled_unit_and_unsuffixed_strength_takes_the_demand_unit():
    d, s = _cells(dict(Ru_kip=3.582, phiRn_kip=4.2, DC=0.853))
    assert d == "Ru = 3.58 kip" and "(kip)" not in d
    d, s = _cells(dict(demand_in=0.12, capacity_in=0.5, DC=0.24))
    assert (d, s) == ("0.12 in.", "0.5 in.")                       # the column says which is which
    # H1.2 check: phiPn_c / phiMn_c carry no suffix -> the units of Pu_kip / Mu_kipin
    d, s = _cells(dict(Pu_kip=1.459, Mu_kipin=16.089, phiPn_c=13.078, phiMn_c=36.53, B1=1.026, DC=0.563))
    assert d == "Pu = 1.46 kip; Mu = 16.1 kip-in." and s == "φPn = 13.1 kip; φMn = 36.5 kip-in."


def test_explicit_fields_win_and_a_unitless_pair_is_not_set_against_forces():
    c = dict(interaction=0.27, limit=1.0, DC=0.27, demand="interaction = 0.270", capacity="limit = 1.00")
    assert _cells(c) == ("interaction = 0.270", "limit = 1.00")
    assert _cells(dict(demand_kip=12.3, capacity_kip=20.0, DC=0.615)) == ("12.3 kip", "20 kip")
    # component forces with an interaction value: the forces against the strengths (H1.2 terms)
    d, s = _cells(dict(Pu_kip=13.46, Mu_kipin=0.75, phiPn_kip=17.32, phiMn_kipin=49.21, interaction=0.796, DC=0.796))
    assert d == "Pu = 13.5 kip; Mu = 0.75 kip-in." and s == "φPn = 17.3 kip; φMn = 49.2 kip-in."
    # only an interaction / limit pair to compare: printed as a pair, never 'Pu = ...' against 'limit = 1.0'
    d, s = _cells(dict(Pu_kip=10.0, Mu_kipin=50.0, interaction=0.8, limit=1.0, DC=0.8))
    assert (d, s) == ("interaction = 0.8", "limit = 1.0")
    assert _cells(dict(note="x", DC=0.5)) == ("—", "—")


def test_every_check_of_a_filled_package_has_both_cells():
    """The appendix card of every designed group prints its checks without a dash cell."""
    pkg = {"anchorage": [dict(id="anch-1", DC=0.32, limit_state="sill anchor", cited="ACI 318-19 17.7",
                              checks=[dict(limit_state="steel in shear", Vu_kip=2.41, phiVsa_kip=7.57, DC=0.319),
                                      dict(limit_state="breakout", Vu_kip=2.41, phiVcb_kip=21.52, DC=0.112)])],
           "studs": [dict(id="stud-1", DC=0.15, limit_state="deflection", cited="IBC Table 1604.3",
                          checks=[dict(limit_state="out-of-plane deflection", defl_in=0.044, limit_in=0.293,
                                       DC=0.15)])]}
    html = RPT._cfs_appendix_calcs(pkg)
    rows = re.findall(r"<tr><td>([^<]*(?:shear|breakout|deflection))</td><td>(.*?)</td><td>(.*?)</td>", html)
    assert len(rows) == 3 and all("&mdash;" not in d and "&mdash;" not in s for _, d, s in rows)


# ---------------- 2. drift-table labels ----------------

def test_stability_theta_prints_as_a_readable_label():
    assert RPT._drift_check_label("stability_theta") == "Stability coefficient &theta; (&sect;12.8.7)"
    assert RPT._drift_check_label("eave_sway") == "Eave sway"
    pkg = {"drift_table": [dict(check="seismic_drift", ratio=0.006, limit=0.025, ok=True),
                           dict(check="stability_theta", value=0.0248, limit=0.167, ok=True)]}
    html = RPT._cfs_serviceability_table(pkg)
    assert "stability_theta" not in html and "stability theta" not in html
    assert "Stability coefficient &theta; (&sect;12.8.7)" in html and "Seismic drift" in html
    # the design-basis row does not repeat its own label as the location
    assert RPT._theta_where("stability_theta") == ""
    assert RPT._theta_where("stability coefficient theta, transverse frames") == "transverse frames"
    assert RPT._theta_where("X line A, story 2") == "X line A, story 2"


# ---------------- 3. rain-basis pointer in the portal combinations note ----------------

def test_portal_combos_note_points_to_the_rain_basis_only_when_there_is_one():
    assert CP.RAIN_BASIS_REF in CP.PORTAL_COMBOS_NOTE
    dry = CF._demo_cfg()
    assert not CP.has_rain_basis(dry)
    assert CP.RAIN_BASIS_REF not in CP.portal_combos_note(dry)
    assert CP.RAIN_BASIS_REF in CP.portal_combos_note(dict(dry, rain_basis="ds = 1 in., dh = 0.5 in."))
    assert CP.RAIN_BASIS_REF in CP.portal_combos_note(dict(dry, rain=15.6))
    pkg = CP.build_portal_package("p", dry, CF.run(dry))
    assert "rain basis in the loads table" not in pkg["combos_note"]
    # a package written with the old note: the report leaves the pointer out where the loads table has no rain row
    old = {"combos_note": CP.PORTAL_COMBOS_NOTE, "combos": []}
    assert "rain basis in the loads table" not in RPT._cfs_combos_section(dry, {}, old)
    wet = dict(dry, rain_basis="ds = 1 in., dh = 0.5 in.")
    assert "rain basis in the loads table" in RPT._cfs_combos_section(wet, {}, old)
    assert any(r[0].startswith("Rain load R") for r in RPT._cfs_gravity_rows(wet, {}))
    assert not any(r[0].startswith("Rain load R") for r in RPT._cfs_gravity_rows(dry, {}))


# ---------------- 4. seismic-weight breakdown labels ----------------

def test_seismic_weight_breakdown_has_readable_labels():
    cfg = ref_cfg(extra_mass_floors={4: 5.0})
    _w, bd = CE.story_weight(cfg, 4, detail=True)
    assert "equipment_bulk" in bd                          # the engine key the label covers
    pkg = CP.build_package("w", cfg, CE.run(cfg))
    rows = {r[0]: r[1] for r in RPT._cfs_basis_rows(cfg, CE.run(cfg), pkg)}
    txt = rows["seismic weight by level (&sect;12.7.2), kip"]
    assert "rooftop and suspended equipment" in txt and "equipment_bulk" not in txt
    assert "_" not in _text(txt)
    assert RPT._w_item_label("roof_mechanical") == "roof mechanical"


# ---------------- 5. enclosure sentence of the portal wind basis ----------------

@pytest.mark.parametrize("enc,phrase", [
    ("enclosed", "enclosed building (Table 26.13-1 GCpi = ±0.18)"),
    ("partially_enclosed", "partially enclosed building (Table 26.13-1 GCpi = ±0.55)"),
    ("partially_open", "partially open building (Table 26.13-1 GCpi = ±0.18)"),
    ("open", "open building (Table 26.13-1 GCpi = 0)")])
def test_wind_basis_enclosure_sentence_is_generic(enc, phrase):
    assert CF.enclosure_phrase(enc) == phrase
    basis = CF.wind_cases(dict(CF._demo_cfg(), wind=dict(V=115.0, exposure="C", enclosure=enc)))["basis"]
    assert phrase in basis and "not in qh" in basis
    assert "NOT in qh" not in basis and "enclosure %s" % enc not in basis and "BOTH" not in basis


def test_old_package_wind_basis_prints_the_new_sentence():
    old = ("ASCE 7-22 directional MWFRS. qh = 29.02 psf (Kd = 0.85 applied in the pressure equation, NOT in qh); "
           "G = 0.85; enclosure partially_enclosed (Table 26.13-1 GCpi = 0.55); theta = 0.0 deg")
    new = RPT._wind_basis_text(old)
    assert "partially enclosed building (Table 26.13-1 GCpi = ±0.55)" in new
    assert "not in qh" in new and "NOT in qh" not in new and "enclosure partially_enclosed" not in new
    assert RPT._wind_basis_text("enclosed (GCpi = +/-0.18), designer text") == \
        "enclosed (GCpi = +/-0.18), designer text"                        # designer wording untouched
    cfg = CF._demo_cfg()
    html = RPT._cfs_portal_loads(cfg, {}, {"wind_basis": {"basis": old, "qh_psf": 29.02}})
    assert "partially enclosed building" in html and "NOT in qh" not in html


# ---------------- 6. basis of every gravity-framing row ----------------

def test_gravity_framing_note_prints_every_basis():
    gf = [dict(id="joist-floor", basis="1.2D + 1.6L, L/480 live"),
          dict(id="joist-corridor", basis="1.2D + 1.6L with L = 100 psf"),
          dict(id="joist-roof", basis="1.2D + 1.6Lr; L/240"),
          dict(id="beam-roof", basis="1.2D + 1.6Lr; L/240")]
    t = _text(RPT._gravity_basis_note(gf))
    assert "joist-floor: 1.2D + 1.6L, L/480 live" in t and "joist-corridor: 1.2D + 1.6L with L = 100 psf" in t
    assert "joist-roof, beam-roof: 1.2D + 1.6Lr; L/240" in t and t.count("1.2D + 1.6Lr; L/240") == 1
    # one shared basis: one note, as before
    one = RPT._gravity_basis_note([dict(id="a", basis="1.2D + 1.6L"), dict(id="b", basis="1.2D + 1.6L")])
    assert one == "<p class='tnote'>1.2D + 1.6L</p>"
    # a basis already written out inside another row's basis is not printed twice
    merged = RPT._gravity_basis_note([dict(id="joist-typ", basis="floor joist: 1.2D + 1.6L; corridor joist: 1.2D + 1.6L, 6 ft"),
                                      dict(id="joist-corridor", basis="1.2D + 1.6L, 6 ft")])
    assert _text(merged).count("1.2D + 1.6L, 6 ft") == 1
    assert RPT._gravity_basis_note([dict(id="a"), dict(id="b", basis=None)]) == ""
    html = RPT._cfs_seed_blocks({"gravity_framing": gf})
    assert "joist-corridor:" in _text(html) and "L = 100 psf" in html


# ---------------- 7. retrofit with existing frames that are not analysed ----------------

def test_existing_frames_wording(tmp_path):
    cfg = dict(CF._demo_cfg(), structure_kind="purlin")
    rows = {r[0]: r[1] for r in RPT._cfs_building_rows(cfg, {}, {})}
    fm = rows["Frame members"]
    assert "not analysed here; the purlin and girt reactions are handed to the engineer of record of the frames" in fm
    assert "stand-in" not in fm and "outline" not in fm
    pkg = {"design_model": {"type": "custom", "description": "continuous purlin lines over the existing frames"},
           "members": [dict(id="purlin-1", section="1000Z300-97", capacity="phiMn = 120 kip-in.", DC=0.8,
                            M_design_kipin=96.0, P_design_kip=0.0, V_design_kip=2.0, cited="S100 F2")]}
    html = RPT._cfs_portal_design_section(pkg, {})
    assert "The frame drawn in the 3D model view is not used for design and its values are not printed." in html
    assert "outline frame" not in html and "bookkeeping" not in html and "engine" not in html


# ---------------- 8. unit suffixes ----------------

@pytest.mark.parametrize("key,label", [
    ("T_used_s", "T used (s)"), ("A_sf", "A (sf)"), ("strength_loss_pct", "strength loss (%)"),
    ("V_mph", "V (mph)"), ("t_min_mil", "t min (mil)"), ("slip_rotation_rad", "slip rotation (rad)"),
    ("unit_weight_pcf", "unit weight (pcf)"), ("K_kipin_rad", "K (kip-in./rad)"),
    ("k_rot_min_kipin_per_rad", "k rot min (kip-in./rad)"), ("Pu_kip_per_ft", "P<sub>u</sub> (kip/ft)"),
    ("Ta (s)", "T<sub>a</sub> (s)"), ("bolts", "bolts")])
def test_unit_suffixes(key, label):
    assert RPT._hk(key) == label


# ---------------- 9. headings keep their acronyms ----------------

def test_headings_capitalise_only_the_first_letter():
    assert RPT._cap1("line demand EW basis") == "Line demand EW basis" and RPT._cap1("") == ""
    pkg = {"wind_cc": {"a": 1.0}, "wind_mwfrs": {"a": 1.0}, "cc_zone_table": [{"zone": "1", "p_psf": 30.0}],
           "sdc_determination": "SDC B", "line_demand_EW_basis": "x"}
    html = RPT._cfs_extra_blocks(pkg)
    heads = re.findall(r"<h3>(.*?)</h3>", html)
    assert heads == ["Wind C&amp;C", "Wind MWFRS", "C&amp;C zone table", "SDC determination", "Line demand EW basis"]
    assert "Wind cc" not in html and "Cc zone" not in html


# ---------------- 10. diaphragm material quoted as written ----------------

MAT = "23/32 in. Structural I wood structural panel (Sturd-I-Floor 24 o.c.) on CFS joists"


def test_diaphragm_note_quotes_the_material_as_written():
    cfg = ref_cfg(diaphragm_material=MAT, diaphragm_topping_in=1.5)
    di = CP.diaphragm_idealization(cfg)
    assert di["flexible_ok"] and MAT in di["note"] and MAT.lower() not in di["note"]
    conc = CP.diaphragm_idealization(ref_cfg(diaphragm_material="Composite Steel Deck with Concrete Fill"))
    assert not conc["flexible_ok"] and "material Composite Steel Deck with Concrete Fill" in conc["warnings"][0]


def test_old_package_prints_the_material_as_written():
    cfg = ref_cfg(diaphragm_material=MAT)
    low = "flexible per 12.3.1.1 (%s; topping 1.50 in. <= 1.5 in.; lines meet Table 12.12-1)" % MAT.lower()
    pkg = {"diaphragm_basis": {"note": low, "warnings": []}, "capacity_design": {"Om0_basis": "Om0 >= 2.5; " + low}}
    out = RPT._cfs_quoted_as_written(pkg, cfg)
    assert MAT in out["diaphragm_basis"]["note"] and MAT in out["capacity_design"]["Om0_basis"]
    assert pkg["diaphragm_basis"]["note"] == low                    # the caller's package is not changed
    assert RPT._cfs_quoted_as_written(pkg, ref_cfg()) is pkg         # nothing to restore
    assert RPT._cfs_quoted_as_written(pkg, ref_cfg(diaphragm_material="osb")) is pkg


# ---------------- report hooks: clad_label, analysis-screen evaluation, system names ----------------

def test_clad_label_names_the_cladding_row():
    lab = "Exterior wall weight (cladding + CFS stud wall, per unit wall area)"
    rows = RPT._cfs_gravity_rows(ref_cfg(clad_label=lab), {})
    assert [r for r in rows if r[0] == lab] and not [r for r in rows if r[0].startswith("Exterior cladding")]
    assert any(r[0] == "Exterior cladding (per unit wall area)" for r in RPT._cfs_gravity_rows(ref_cfg(), {}))


def test_analysis_screen_evaluation_goes_to_the_qa_annex(tmp_path):
    pkg = {"preflight_warnings": ["line drift screen: line XW0 at 0.024 hsx"],
           "analysis_screen_evaluation": "the drift screen uses the strength-level forces; the design drift is 0.019",
           "system_by_direction": {"X": "strap_braced", "Y": "wsp_shearwall"}}
    fn = RPT._cfs_write_qa_annex("job", {}, {}, pkg, str(tmp_path))
    annex = open(os.path.join(str(tmp_path), fn), encoding="utf-8").read()
    i, j = annex.index("line drift screen"), annex.index("<b>Evaluation:</b>")
    assert i < j and "the design drift is 0.019" in annex[j:]
    body = RPT._cfs_extra_blocks(pkg)
    assert "Analysis screen evaluation" not in body and "System by direction" not in body


def test_system_names_by_direction_and_in_appendix_a():
    cfg = ref_cfg()
    res = CE.run(cfg)
    pkg = CP.build_package("w", cfg, res)
    pkg["elf_by_direction"] = {"X": dict(system="strap_braced", V_kip=35.0, R=4.0, Cd=3.5, Om0=2.0),
                               "Y": dict(system="wsp_shearwall", V_kip=22.0, R=6.5, Cd=4.0, Om0=3.0)}
    rows = {r[0]: r[1] for r in RPT._cfs_basis_rows(cfg, res, pkg)}
    elf = rows["ELF per direction (&sect;12.2.2)"]
    assert "strap-braced walls" in elf and "wood-structural-panel shear walls" in elf and "_" not in elf
    card = RPT._cfs_appendix_calcs({"wall_lines": [dict(id="w1", system="wsp_shearwall", DC=0.5, cited="S400",
                                                        limit_state="in-plane shear")]})
    assert "wood-structural-panel shear walls" in card and "wsp_shearwall" not in card


# ---------------- citation red flag: each citation on its own ----------------

FLAG = "cites AISC 341/360/358 for CFS members"


def _flags(*cites):
    pkg = {"connections": [dict(id="c%d" % i, cited=c, DC=0.5) for i, c in enumerate(cites)]}
    return RPT._grounding_check({"system": "wsp_shearwall", "structure_kind": "wall"}, "no-such-job", pkg)


def test_aisc_flag_reads_each_citation_on_its_own():
    # anchor rods to AISC 360 next to a CFS stud citation of ANOTHER item: no flag
    assert FLAG not in _flags("AISC 360-22 J3.6 (A449 anchor rods)", "AISI S100-16 E2, 600S162-54 studs")
    # AISC 360 cited for a CFS member in the same citation, or AISC 341 anywhere: flagged
    assert FLAG in _flags("AISC 360-16 F2 for the 600S162-54 header")
    assert FLAG in _flags("AISC 341-22 F2 for the braced frame")

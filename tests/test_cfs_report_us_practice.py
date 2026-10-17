"""CFS report template, US-practice rules (review CFS_batch1-3 + the HR template audit, 2026-10):
publication mode (no harness / agent / RAG text in the body; grounding + activity log in
qa_annex.html), one number formatter, a complete design basis (SD1 never 'n/a', SDC A, portal
R/Cd/Omega0, materials), demand columns that print the demand the D/C uses, blocks only where they
apply, the custom-model portal rule, the ELF self-check, citations once per table, sample
calculations, the design credit and an honest QA chapter."""
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
import cfs_systems as CS       # noqa: E402
import consistency as CC       # noqa: E402
import report as RPT           # noqa: E402
from test_cfs_code_basis_screens import ref_cfg    # noqa: E402

AGENT = {"model": "Claude Opus 5.5", "provider": "Anthropic", "harness": "Steltic", "date": "2026-10-17"}
CREDIT = "Designed by Claude Opus 5.5 (Anthropic) with Steltic, 2026-10-17"
CITE = ("AISI S400-20 Table E1.3-1 (7/16 in. OSB, No. 8 screws); phi_v = 0.60 (E1.3.2) "
        "[RAG chunk E1.3.1.1.3 part 2/3] (retrieved verbatim)")


def _text(html):
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<img[^>]*>", " ", html)
    return _html.unescape(re.sub(r"<[^>]+>", " ", html))


FORBIDDEN = re.compile(r"\bRAG\b|retrieved|SEEDED|seeded|scaffold|cfg\[|calc_package|\bomitted\b|"
                       r"on request|0 queries|grounded|re-render|verbatim", re.I)


def _filled_wall(cfg, name="w"):
    res = CE.run(cfg)
    pkg = CP.build_package(name, cfg, res)
    for w in pkg["wall_lines"]:
        v = max(w["v_unit_plf"], w.get("v_wind_plf") or 0.0)
        cap = 1692.0 if w["story"] <= 2 else 1056.0
        w.update(sheathing="7/16 in. OSB, two sides" if w["story"] <= 2 else "7/16 in. OSB, one side",
                 fastener_schedule="No. 8 @ 4 in. edges / 12 in. field", capacity=cap,
                 DC=round(v / cap, 3), limit_state="in-plane shear S400 E1.3", cited=CITE)
    for h in pkg["holddowns"]:
        T = h["T_cd_seed_kip"]
        h.update(device_class="rod", T_design_kip=T, capacity=76.3, DC=round(T / 76.3, 3),
                 selection="continuous rod, 1-1/4 in. ASTM A449", limit_state="rod tension D2/D3",
                 cited="AISI S100-16 D2/D3 (retrieved verbatim); S400 B3.4")
    pkg["studs"][0].update(section="600S162-54 @ 16 in.", DC=0.4, limit_state="E2/E4/H1.2", cited="S100 E2",
                           capacity="phi*Pn = 7.4 kip")
    for a in pkg["anchorage"]:
        v = a["v_cd_seed_plf"]
        a.update(selection="5/8 in. anchor bolts @ 12 in. + 3x3x1/4 plate washers; rod to embed",
                 v_design_plf=v, T_design_kip=a["T_cd_seed_kip"], capacity=2550.0, DC=round(v / 2550.0, 3),
                 limit_state="bolt bearing J3.3.1; anchors ACI 318-19 17.7", cited="S100 J3.3.1; ACI 318-19 Ch. 17")
    pkg["connections"] = [dict(id="conn-track-anchorage", selection="5/8 in. anchor bolts @ 16 in.",
                               demand="v = 2.23 klf (Omega0-capped story-1 shear)", DC=0.87,
                               limit_state="bolt bearing J3.3.1", capacity="phi*Rn = 2.55 klf",
                               cited="S100 J3.3.1")]
    pkg["design_agent"] = AGENT
    pkg["rag_grounding"] = "S400 Table E1.3-1 retrieved verbatim; cfs_design_examples probed once: empty"
    pkg["solution_notes"] = "RAG-GROUNDED SOLUTION. Table values CORRECTED vs my offline memory."
    pkg["corpus_defects"] = ["chunk E2.3 truncated"]
    return res, pkg


# ---------------- small helpers ----------------

def test_number_formatter_and_scrub():
    assert RPT._cn(None) == "&mdash;" and RPT._cn(float("nan")) == "&mdash;"
    assert RPT._cn(1060.2) == "1060" and RPT._cn(163.1) == "163.1" and RPT._cn(892.0) == "892"
    assert RPT._cn(1.0) == "1.0" and RPT._cn(6.5) == "6.5" and RPT._cn(0.15384) == "0.154"
    d = RPT._cn({"T_cd_kip": 60.5, "x": None, "ok": True})
    assert "{" not in d and "None" not in d and "60.5" in d and "yes" in d
    s = RPT._pub_scrub("S100 J4.3.1 (phi = 0.50, retrieved verbatim) [RAG chunk J4 part 1/2]; "
                       "AGENT: see cfg['wall_props'] in design/calc_package_cfs.json")
    assert "retrieved" not in s and "RAG" not in s and "AGENT" not in s and "cfg[" not in s
    assert "(phi = 0.50)" in s and "wall props" in s


def test_design_credit_format():
    assert RPT._cfs_design_credit({"design_agent": AGENT}) == CREDIT
    assert RPT._cfs_design_credit({}) == ""
    assert RPT._cfs_design_credit({"design_agent": {"model": "Claude Opus 5.5", "provider": "Anthropic"}}) == \
        "Designed by Claude Opus 5.5 (Anthropic) with Steltic"
    assert RPT._cfs_design_credit({"design_agent": "Claude Opus 5.5"}) == "Designed by Claude Opus 5.5 with Steltic"
    assert RPT._cfs_design_credit({"design_agent": {"provider": "Anthropic"}}) == ""      # no model recorded


# ---------------- whole report: publication mode ----------------

@pytest.fixture(scope="module")
def wall_report(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("wsp"))
    cfg = ref_cfg(wind=dict(V=105.0, exposure="B"))
    res, pkg = _filled_wall(cfg)
    path = RPT.build_report_cfs("WSP", cfg, res, pkg, root)
    return root, cfg, res, pkg, open(path, encoding="utf-8").read()


def test_wall_report_body_has_no_harness_text(wall_report):
    root, _cfg, _res, _pkg, html = wall_report
    txt = _text(html)
    assert not FORBIDDEN.search(txt), FORBIDDEN.search(txt).group(0)
    assert re.search(r"\bNone\b|\bAGENT\b|\bSEED\b", txt) is None
    # no 'agent' anywhere in the body; the design credit names the model
    assert "agent" not in txt.lower()
    assert CREDIT in txt
    assert "AISI S100-16 (2020) w/S2-20 (as referenced by IBC 2024)" in txt
    # the design-process records went to the QA annex, linked from the QA chapter
    assert "href='qa_annex.html'" in html
    annex = open(os.path.join(root, "qa_annex.html"), encoding="utf-8").read()
    assert "Grounding verification" in annex and "CORRECTED vs my offline memory" in annex
    assert "chunk E2.3 truncated" in annex and "Design log" in annex


def test_design_basis_tables_are_complete(wall_report):
    _root, _cfg, _res, _pkg, html = wall_report
    txt = _text(html)
    for item in ("Occupancy / use", "Risk Category", "Site Class", "Importance factor",
                 "Seismic Design Category", "Response coefficients", "Directionality factor",
                 "Enclosure classification", "Internal pressure coefficient", "Ground snow load",
                 "Thermal factor", "Floor dead load", "Floor live load", "Roof live load",
                 "Collateral load", "Materials", "Screws", "Threaded rods"):
        assert item in txt, item
    assert "not stated" in txt                       # occupancy / site class / pg are absent here
    assert "Table 11.6-1" in txt and "Table 11.6-2" in txt
    assert "1-1/4 in.: F y = 81 ksi, F u = 105 ksi" in re.sub(r"\s+", " ", txt)


def test_holddown_and_anchorage_tables_print_the_capacity_design_demand(wall_report):
    _root, _cfg, _res, _pkg, html = wall_report
    hd = html[html.index("Hold-down / continuous rod schedule"):html.index("Stud and chord-stud schedule")]
    assert "T<sub>cd</sub> (kip)" in hd and "ELF&middot;&rho; T (kip)" in hd
    assert "capacity design, T<sub>cd</sub> (S400 B3.4)" in hd and "<b>60.5</b>" in hd
    con = html[html.index("Connections and anchorage"):]
    assert "capacity design &le; 2230 plf" in con and "ELF&middot;&rho; &le; 1160 plf" in con
    anc = html[html.index("Anchorage schedule (per wall line)"):html.index("Stud and chord-stud schedule")]
    assert "Capacity-design v<sub>cd</sub> (plf)" in anc and "anch-X-B" in anc and "2230" in anc
    assert "capacity design (S400 B3.4)" in anc and "<b>2230</b>" in anc


def test_sheathing_table_demand_basis_and_citation_once(wall_report):
    _root, _cfg, _res, _pkg, html = wall_report
    sch = html[html.index("sheathing and fastener schedule"):html.index("Hold-down / continuous rod schedule")]
    assert "Demand basis" in sch and "seismic (&rho;E)" in sch
    assert sch.count("Table E1.3-1 (7/16 in. OSB, No. 8 screws)") == 1      # once, under the table
    assert "Reference (all rows)" in sch


def test_self_check_matches_section3(wall_report):
    _root, cfg, res, pkg, _html = wall_report
    h, ok = RPT._cfs_elf_selfcheck(cfg, res, pkg)
    assert ok and "matches" in h
    bad = dict(pkg, elf_spot_check="W 3876 / V 536.7 kip -- matches engine")
    h2, ok2 = RPT._cfs_elf_selfcheck(cfg, res, bad)
    assert not ok2 and "does not match Section 3" in h2
    good = dict(pkg, elf_spot_check="V = 0.1538 x %.1f = %.1f kip" % (res["elf"]["W"], res["elf"]["V"]))
    assert RPT._cfs_elf_selfcheck(cfg, res, good)[1]


def test_qa_chapter_states_what_was_verified(wall_report):
    _root, _cfg, _res, _pkg, html = wall_report
    qa = html[html.index("Quality assurance</h2>"):]
    for row in ("Completeness", "Arithmetic", "Capacity design (S400 B3.4)", "Code basis",
                "Drift and stability", "ELF hand check", "Not verified by this report"):
        assert row in qa, row


def test_sample_calcs_block():
    pkg = {"sample_calcs": [dict(title="Shear wall X-B, story 1", item="wall-X-B-s1",
                                 steps=[dict(step="Design strength", expression="phi vn = 0.60 x 2820",
                                             value=1692.0, ref="S400 E1.3.2")], result="OK")]}
    h = RPT._cfs_sample_calcs(pkg)
    assert "Sample calculations" in h and "&phi; vn = 0.60 &times; 2820" in h and "1692" in h
    assert RPT._cfs_sample_calcs({}) == ""


# ---------------- design basis details ----------------

def test_sd1_never_na_and_sdc_a_recognised():
    pcfg = CF._demo_cfg()                     # seis: SDS 0.25, R 3, no SD1 / Cd / Om0
    rows = RPT._cfs_basis_rows(pcfg, CF.run(pcfg), {})
    txt = " ".join(" ".join(r) for r in rows)
    assert "n/a" not in txt
    assert "S<sub>D1</sub> not stated" in txt or "<b>not stated</b>" in txt
    assert "Table 11.6-2 not evaluated" in txt
    assert "C<sub>d</sub> = 3.0" in txt and "&Omega;<sub>0</sub> = 3.0" in txt and "Table 12.2-1" in txt
    c, t = RPT._cfs_sdc_detail(dict(seis=dict(SDS=0.10, SD1=0.04), risk_cat="II"))
    assert c == "A" and "&sect;1.4" in t
    c, _t = RPT._cfs_sdc_detail(dict(seis=dict(SDS=0.48, SD1=0.20), risk_cat="II"))
    assert c == "D"                            # Table 11.6-2 governs (SD1 = 0.20)


# ---------------- blocks only where they apply ----------------

def test_strap_report_has_no_sheathing_column_and_its_own_drift_method():
    cfg = ref_cfg(system="strap_braced", seis=CS.seis_cfs(0.45, 0.19, 0.17, "strap_braced"),
                  strap_Fy_ksi=50.0, strap_Ag_in2=0.428,
                  wall_props=dict(sheathing="strap", strap_area_in2=0.856, chord_area_in2=2.4,
                                  k_anchor_kip_in=250.0))
    pkg = CP.build_package("s", cfg, CE.run(cfg))
    for w in pkg["wall_lines"]:
        w.update(sheathing="NONE -- X-strap bay", fastener_schedule="6 in. x 68 mil strap, 18 No. 12 each end",
                 capacity=3000.0, DC=0.3, limit_state="strap yield", cited="S400 E3.3.1")
    html = RPT._cfs_schedules_section(pkg, cfg)
    sch = html[:html.index("Hold-down")]
    assert "Strap bracing (per bay)" in sch and "Sheathing" not in sch
    drift = html[html.index("Story drift"):]
    assert "E3.4.4" in drift and "E1.4.1.4-1" not in drift
    cap = RPT._cfs_capacity_chapter(cfg, pkg)
    assert "Strap ductility" in cap and "Type II" not in cap


def test_type_ii_row_only_with_type_ii_walls():
    cfg = ref_cfg()
    pkg = CP.build_package("t", cfg, CE.run(cfg))
    assert "Type II" not in RPT._cfs_capacity_chapter(cfg, pkg)
    pkg["holddowns"][0]["type_ii"] = {"Ca_by_story": {1: 0.8}}
    assert "Type II" in RPT._cfs_capacity_chapter(cfg, pkg)


def test_portal_r3_has_no_wall_boilerplate():
    pcfg = dict(CF._demo_cfg(), system="not_detailed")
    res = CF.run(pcfg)
    pkg = CP.build_portal_package("p", pcfg, res)
    cap = RPT._cfs_capacity_chapter(pcfg, pkg)
    assert "S400 A1.2.3" in cap
    for bad in ("hold-down", "Hold-down", "Wind design of the lateral walls", "wind columns", "four-term"):
        assert bad not in cap, bad
    serv = RPT._cfs_serviceability_table(pkg)
    assert "four-term" not in serv and "Serviceability" in serv


def test_portal_basis_prints_the_12_7_2_frame_weight(tmp_path):
    pcfg = dict(CF._demo_cfg(), system="not_detailed", collateral=3.0, clad=2.5,
                seis=dict(SDS=0.30, SD1=0.12, S1=0.08, R=3.0, Cd=3.0, Om0=3.0, Ie=1.0, W_frame_kip=None))
    res = CF.run(pcfg)
    pkg = CP.build_portal_package("p", pcfg, res)
    rows = RPT._cfs_basis_rows(pcfg, res, pkg)
    lab = {r[0]: r[1] for r in rows}
    w = lab["Seismic weight per frame (&sect;12.7.2)"]
    assert ("W = %s kip" % RPT._cn(res["seismic"]["W_frame_kip"])) in w and "roof dead" in w and "collateral" in w
    assert "V = C<sub>s</sub>W" in lab["ELF (&sect;12.8), per frame"]
    assert "4.5 psf &times; " in w and " &rarr; W = " in w
    assert "all other structures" in lab["drift-limit basis"] and "cfg[" not in lab["drift-limit basis"]
    html = open(RPT.build_report_cfs("P", pcfg, res, pkg, str(tmp_path)), encoding="utf-8").read()
    m = re.search(r"worst drift / limit = ([\d.]+)", html)           # the portal seismic drift row counts
    sd = next(d for d in pkg["drift_table"] if d["check"] == "seismic_drift")
    assert m and float(m.group(1)) == pytest.approx(sd["ratio"] / sd["limit"], abs=0.01)
    grav = {r[0]: r[1] for r in RPT._cfs_gravity_rows(pcfg, pkg)}
    assert grav["Wall cladding (in the seismic weight, &sect;12.7.2)"] == "2.5 psf"


# ---------------- custom / hand-model portal ----------------

def test_custom_model_portal_does_not_print_engine_skeleton(tmp_path):
    pcfg = dict(CF._demo_cfg(), system="not_detailed")
    res = CF.run(pcfg)
    pkg = CP.build_portal_package("p", pcfg, res)
    knee_eng = pkg["connections"][0]["M_transfer_kipin"]
    up_eng = pkg["anchorage"][0]["T_net_uplift_kip"]
    pkg["design_model"] = {"type": "custom", "description": "two-span hand model with an interior column"}
    for m in pkg["members"]:
        m.update(P_kip=15.2, V_kip=0.0, M_kipin=637.0, capacity="phi*Mn = 1924 kip-in; phi*Pn = 300 kip",
                 DC=0.38, limit_state="H1.2", cited="S100 H1.2")
    for c in pkg["connections"]:
        c.update(selection="bolted gusset", limit_state="bolt bearing", capacity="phi*M = 2290 kip-in", DC=0.5,
                 cited="S100 J3.3.2")
    pkg["connections"][0]["demand"] = "M = 1623 kip-in (design model)"
    pkg["anchorage"][0].update(selection="4 rods", limit_state="tension", capacity="20 kip", DC=0.2, cited="ACI 318")
    pkg["drift_table"][0].update(criterion="H/60", ok=True)
    html = RPT._cfs_portal_design_section(pkg, pcfg, res, "p")
    assert "Design model" in html and "two-span hand model" in html
    assert "M = %s kip-in." % RPT._cn(knee_eng) not in html            # engine knee moment not the demand
    assert "M = 1623 kip-in (design model)" in html
    assert "not reported" in html                                       # every member V = 0
    anch = html[html.index("Base anchorage"):]
    assert "n.r." in anch and ("%.1f" % up_eng) not in anch.split("Limit state")[0]
    assert "outline frame of the 3D view, not used for design" in html      # eave sway left at the seed
    appx = RPT._cfs_appendix_calcs(pkg, pcfg, res, "p")
    assert "outline frame of the 3D view, not used for design" in appx


def test_custom_model_detection():
    assert RPT._cfs_custom_model({}, {"design_model": {"type": "hand", "description": "x"}})[0]
    assert RPT._cfs_custom_model({"structure_kind": "component"}, {})[0]
    assert not RPT._cfs_custom_model({}, {"design_model": {"type": "engine"}})[0]


# ---------------- D/C display ----------------

def test_delegated_zero_dc_and_numeric_drift_status():
    assert RPT._cdc_e(dict(DC=0.0, limit_state="delegated to the truss manufacturer")) == "delegated"
    assert RPT._cdc_e(dict(DC=0.0, selection="EXISTING -- not in scope")) == "existing (not in scope)"
    rows = RPT._cfs_drift_rows([dict(check="apex_deflection", value_in=2.7, criterion="L/180", DC=1.02, ok=True)])
    assert rows[0][-1] == "<b>NG</b>"                                   # the numbers win over 'ok'

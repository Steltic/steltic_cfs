"""consistency.py code-basis screens added for the US-practice review of the published CFS reports
(2026-10): screw phi/Omega per S100-16 (2020) w/S2-20, the screw's own shear strength, ASTM A449
strength by diameter, the interaction lower bound (AISC H1-1b aware), ASCE 7-16 snow factors,
framework seed labels left as the design basis, hold-down / chord / anchorage demands below the
capacity-design seed, shear anchorage at ELF, ELF self-checks that do not match the analysis, and
the strap-bay completeness rule. Hand values in the comments."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (os.path.join(REPO, "steel_engine"), REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                  # noqa: E402
import cfs_engine as CE        # noqa: E402
import cfs_pipeline as CP      # noqa: E402
import cfs_systems as CS       # noqa: E402
import consistency as CC       # noqa: E402
import wall_line as WL         # noqa: E402

H = {1: 10.0, 2: 9.5, 3: 9.5, 4: 9.5}


def _segs(n, L):
    return {k: [(L, H[k])] * n for k in H}


def ref_cfg(**kw):
    """contract/CFS_REFERENCE.md building (SDC D, WSP, Omega0_eff 2.5 -> T_cd = 60.5 kip)."""
    cfg = dict(stories=4, heights_ft=[10.0, 9.5, 9.5, 9.5], plan_ft=(120.0, 60.0),
               D_floor=35.0, D_roof=22.0, clad=12.0, snow=25.0, L_floor=40.0,
               seis=CS.seis_cfs(1.0, 0.5, 0.4, "wsp_shearwall"), system="wsp_shearwall",
               risk_cat="II", structure_kind="wall", analysis_fidelity=0, diaphragm="flexible",
               lines_x=[WL.WallLine("A", 0.0, _segs(2, 24.0)), WL.WallLine("B", 30.0, _segs(4, 24.0)),
                        WL.WallLine("C", 60.0, _segs(2, 24.0))],
               lines_y=[WL.WallLine("1", 0.0, _segs(3, 16.0)), WL.WallLine("2", 60.0, _segs(6, 16.0)),
                        WL.WallLine("3", 120.0, _segs(3, 16.0))],
               wall_props=dict(sheathing="osb", s_in=4.0, t_stud_in=0.0538, t_sheathing_in=0.4375,
                               Gt_lb_in=77500.0, chord_area_in2=2.4, k_anchor_kip_in=250.0,
                               by_story={1: dict(faces=2), 2: dict(faces=2), 3: dict(faces=1),
                                         4: dict(faces=1)}))
    cfg.update(kw)
    return cfg


@pytest.fixture(scope="module")
def ref_pkg():
    cfg = ref_cfg()
    return cfg, CP.build_package("ref", cfg, CE.run(cfg))


# ---------------- screws: phi 0.50 / Omega 3.00 (S100-16 (2020) w/S2-20 J4) ----------------

def test_screw_phi_055_is_flagged_and_050_is_not():
    bad = {"connections": [dict(id="c1", limit_state="screw shear J4.3.1 (phi = 0.55)", DC=0.9,
                                cited="AISI S100-16 J4.3.1 (Eqs. J4.3.1-1..3, phi=0.55 retrieved verbatim)")]}
    assert any("phi = 0.50 / Omega = 3.00" in i for i in CC._screw_phi_issues(bad))
    asd = {"collectors": [dict(id="k", cited="J4.3.1 screws, Omega = 2.80", DC=0.5)]}
    assert CC._screw_phi_issues(asd)
    ok = {"connections": [dict(id="c1", limit_state="screw shear J4.3.1 (phi = 0.50)", DC=0.9)]}
    assert CC._screw_phi_issues(ok) == []
    # a weld check with phi 0.55 in a screwed connection is not a screw factor
    weld = {"connections": [dict(id="c2", limit_state="arc spot weld J2.2 (phi = 0.55)", DC=0.4)]}
    assert CC._screw_phi_issues(weld) == []


def test_screw_connection_needs_the_screw_shear_strength():
    c = dict(id="strap-end", limit_state="18 No. 12 screws, tilting J4.3.1", DC=0.9, cited="S100 J4.3.1")
    assert CC._screw_shear_strength_issues({"connections": [c]})
    c2 = dict(c, limit_state="18 No. 12 screws, tilting J4.3.1 and screw shear J4.3.2 (Pss = 2.1 kip)")
    assert CC._screw_shear_strength_issues({"connections": [c2]}) == []


# ---------------- ASTM A449 strength by diameter ----------------

def test_a449_grades_areas_and_bounds():
    assert CC.a449_strength(1.0) == (92.0, 120.0)
    assert CC.a449_strength(1.25) == (81.0, 105.0)
    assert CC.a449_strength(1.75) == (58.0, 90.0)
    # 1-1/4 in.: min(0.9 x 81 x 1.227, 0.75 x 105 x 0.969) = 76.3 kip (review table)
    assert CC.a449_phiTn_max(1.25) == pytest.approx(76.3, abs=0.2)
    # 1-3/4 in.: 0.9 x 58 x 2.405 = 125.5 kip governs
    assert CC.a449_phiTn_max(1.75) == pytest.approx(125.5, abs=0.3)
    assert CC.parse_rod_diameter("continuous rod 1-1/2 in. ASTM A449, plate 6x6x3/4 in.") == 1.5
    assert CC.parse_rod_diameter("7/8 in. A449 threaded rod") == 0.875
    assert CC.parse_rod_diameter("bearing plate 3/4 in. thick") is None


def test_a449_rod_over_1in_with_fu_120_is_flagged():
    hd = dict(id="hd-X-N", selection="continuous rod 1-1/2 in. ASTM A449", DC=0.71,
              capacity="phi_t*Tn = 126.5 kip (min of 0.9*81*1.767 yield, 0.75*120*1.405 rupture)",
              cited="S100 D2/D3")
    iss = CC._rod_grade_issues({"holddowns": [hd]})
    assert any("Fu = 120 ksi" in i for i in iss)
    assert any("exceeds the ASTM A449 strength" in i and "110.7" in i for i in iss)
    # 1-3/4 in. computed with the 81/105 grade
    hd2 = dict(hd, selection="1-3/4 in. A449 rod", capacity="phi*Tn = 171.0 kip (0.9 x 81 x 2.405)")
    assert any("58/90" in i for i in CC._rod_grade_issues({"holddowns": [hd2]}))
    # correct grade and strength: no issue; a 3/4 in. rod at Fu = 120 is correct
    ok = dict(hd, capacity="phi*Tn = 110.6 kip (0.75 x 105 x 1.405)")
    assert CC._rod_grade_issues({"holddowns": [ok]}) == []
    small = dict(hd, selection="3/4 in. A449 rod", capacity="phi*Tn = 30.0 kip (0.75 x 120 x 0.334)")
    assert CC._rod_grade_issues({"holddowns": [small]}) == []


# ---------------- interaction lower bound (H1-1b aware) ----------------

def test_interaction_floor_respects_h1_1b():
    def mem(name, P, Pc, dc):
        return {"members": [dict(id="m", DC=dc, limit_state=name, cited="x",
                                 checks=[dict(limit_state=name, Pu_kip=P, phiPn_kip=Pc, DC=dc)])]}
    # S100 H1.2 is linear: D/C 0.30 < P/Pc 0.50 is impossible
    assert CC._interaction_issues(mem("H1.2 interaction", 50.0, 100.0, 0.30))
    assert CC._interaction_issues(mem("H1.2 interaction", 50.0, 100.0, 0.80)) == []
    # AISC H1-1b: axial term Pr/(2Pc) = 0.075 -> a correct D/C 0.10 (< Pr/Pc 0.15) is NOT flagged
    assert CC._interaction_issues(mem("H1-1b interaction", 15.0, 100.0, 0.10)) == []
    assert CC._interaction_issues(mem("H1-1b interaction", 15.0, 100.0, 0.05))
    # H1-1b used with Pr/Pc >= 0.2
    assert any("H1-1a governs" in i for i in CC._interaction_issues(mem("H1-1b", 30.0, 100.0, 0.40)))
    # headline: portal member slot P_kip vs 'phi*Pn = ...' in the capacity string
    e = dict(id="frame-col", P_kip=150.0, M_kipin=100.0, DC=0.40, capacity="phi*Mn = 1924 kip-in; phi*Pn = 300 kip",
             limit_state="H1.2 interaction", cited="S100 H1.2")
    assert CC._interaction_issues({"members": [e]})


def test_snow_factor_716_combo_is_flagged():
    pkg = {"members": [dict(id="frame-raf", governing_combo="1.2D+1.6S_unb1", DC=0.9)],
           "connections": [dict(id="k", checks=[dict(combo="1.2D+1.0W+0.5S bal", DC=0.2)])]}
    iss = CC._snow_factor_issues(pkg)
    assert len(iss) == 2 and all("1.0S" in i for i in iss)
    ok = {"members": [dict(id="m", governing_combo="1.2D+1.0S_bal+0.5W", DC=0.9),
                      dict(id="n", governing_combo="(1.2+0.2SDS)D+rhoE+0.5L+0.15S", DC=0.5)]}
    assert CC._snow_factor_issues(ok) == []


def test_seeded_collector_label_left_in_place():
    c = dict(id="collector-CN", line="CN", DC=0.83, cited="x",
             basis="SEEDED: re-entrant/step line -- design with overstrength (Om0) per 12.10.2.1")
    assert CC._seed_label_issues({"collectors": [c]})
    assert CC._seed_label_issues({"collectors": [dict(c, design_basis="S400 expected strength, Omega0 cap")]}) == []
    assert CC._seed_label_issues({"collectors": [dict(c, DC=None)]}) == []        # not designed yet


# ---------------- capacity-design floors (S400 B3.4) ----------------

def test_chord_demand_below_its_lines_tcd_fails(ref_pkg):
    cfg, pkg = ref_pkg
    pkg = dict(pkg, studs=[dict(id="chord-X-B", line="B", direction="X", P_design_kip=40.0, DC=0.5,
                                capacity="phi*Pn = 80 kip", limit_state="E2", cited="S100 E2")])
    iss = CC._capacity_design_numeric_issues(cfg, pkg)
    assert any("[chord chord-X-B]" in i and "60.5" in i for i in iss)       # T_cd 60.5 - relief 16


def test_anchorage_slot_below_tcd_fails(ref_pkg):
    cfg, pkg = ref_pkg
    pkg = dict(pkg, anchorage=[dict(id="anc-1", T_cd_seed_kip=60.5, T_design_kip=30.0, DC=0.5,
                                    limit_state="anchor tension", cited="ACI 318")])
    assert any("[anchorage anc-1]" in i for i in CC._capacity_design_numeric_issues(cfg, pkg))


def test_shear_anchorage_at_elf_is_flagged(ref_pkg):
    cfg, pkg = ref_pkg
    ref = CC.capacity_design_unit_shear(pkg)
    # line B, story 1: rho-ELF 1160 plf; Omega0-capped 2.5 x 85.6 kip / 96 ft = 2231 plf
    assert ref[("X", "B")][0] == pytest.approx(1160, abs=1)
    assert ref[("X", "B")][1] == pytest.approx(2230, abs=3)
    elf = dict(id="conn-track-anchorage", selection="5/8 in. anchor bolts @ 24 in. through 54 mil track",
               demand="v = 1.16 klf max -> 2.32 kip/bolt", limit_state="bolt bearing", DC=0.6,
               capacity="phi*Rn = 1.93 klf", cited="S100 J3.3.1")
    assert CC._anchorage_shear_cd_issues(cfg, dict(pkg, connections=[elf]))
    cd = dict(elf, demand="v = 2.23 klf (Omega0-capped story-1 shear)")
    assert CC._anchorage_shear_cd_issues(cfg, dict(pkg, connections=[cd])) == []


def test_per_line_anchorage_slots_are_seeded(ref_pkg):
    cfg, pkg = ref_pkg
    anc = {a["id"]: a for a in pkg["anchorage"]}
    assert len(anc) == len(cfg["lines_x"]) + len(cfg["lines_y"])
    lines = pkg["capacity_design"]["lines"]
    hds = {(h["direction"], h["line"]): h for h in pkg["holddowns"]}
    for a in anc.values():
        assert a["framework_slot"] and a["DC"] is None and a["capacity"] is None
        ve = lines["%s:%s" % (a["direction"], a["line"])]["Ve_cap_by_story_kip"][a["story"]]
        assert a["v_cd_seed_plf"] == pytest.approx(ve * 1000.0 / a["L_ft"], abs=1.0)
        assert a["v_cd_seed_plf"] >= a["v_elf_plf"]
        assert a["T_cd_seed_kip"] == hds[(a["direction"], a["line"])]["T_cd_seed_kip"]
    b = anc["anch-X-B"]
    assert b["L_ft"] == 96.0 and b["v_elf_plf"] == pytest.approx(1160, abs=1)
    assert b["v_cd_seed_plf"] == pytest.approx(2230, abs=3) and b["T_cd_seed_kip"] == pytest.approx(60.5, abs=0.1)
    # the report / checks take the reference unit shears from the slots
    assert CC.capacity_design_unit_shear(pkg)[("X", "B")] == (b["v_elf_plf"], b["v_cd_seed_plf"])


def test_anchorage_slot_shear_checked_against_its_own_line(ref_pkg):
    cfg, pkg = ref_pkg
    slots = [dict(a) for a in pkg["anchorage"]]
    for a in slots:
        a.update(v_design_plf=a["v_cd_seed_plf"], T_design_kip=a["T_cd_seed_kip"], capacity=2600.0,
                 DC=round(a["v_cd_seed_plf"] / 2600.0, 3), selection="5/8 in. anchor bolts @ 12 in.",
                 limit_state="bolt bearing J3.3.1", cited="S100 J3.3.1")
    assert CC._anchorage_shear_cd_issues(cfg, dict(pkg, anchorage=slots)) == []
    a = next(x for x in slots if x["id"] == "anch-X-A")
    a["v_design_plf"] = a["v_elf_plf"]                        # designed at rho-ELF
    iss = CC._anchorage_shear_cd_issues(cfg, dict(pkg, anchorage=slots))
    assert len(iss) == 1 and "[anchorage anch-X-A]" in iss[0] and "capacity-design unit shear v_cd" in iss[0]
    # its own Omega_E*Vn (numbers) is an accepted basis
    a["design_basis"] = "own expected strength: Omega_E*Vn = 1.0 x 1160 plf x 48 ft = 55.7 kip (Omega_E = 1.0)"
    assert CC._anchorage_shear_cd_issues(cfg, dict(pkg, anchorage=slots)) == []


def test_anchorage_tension_check_never_reads_a_shear_capacity_as_tension(ref_pkg):
    cfg, pkg = ref_pkg
    a = dict(pkg["anchorage"][0], v_design_plf=2300.0, capacity=2550.0, DC=0.9,
             selection="5/8 in. anchor bolts @ 12 in.", limit_state="bolt bearing", cited="S100 J3.3.1")
    iss = CC._capacity_design_numeric_issues(cfg, dict(pkg, anchorage=[a]))
    assert not any("[anchorage %s]" % a["id"] in i for i in iss)
    a["T_design_kip"] = 20.0                                   # a stated design tension IS checked
    iss = CC._capacity_design_numeric_issues(cfg, dict(pkg, anchorage=[a]))
    assert any("[anchorage %s]" % a["id"] in i for i in iss)


# ---------------- ELF self-check vs the analysis ----------------

def test_parse_and_compare_elf_spot_check():
    assert CC.parse_W_V("Cs = 1.00/6.5 = 0.1538; V = 0.1538 x 1767 = 271.8 kip -- matches engine") == \
        (1767.0, 271.8)
    assert CC.parse_W_V("W 3876 / V 536.7 kip -- matches engine") == (3876.0, 536.7)
    pkg = {"elf": {"W_kip": 3829.0, "V_kip": 530.2}, "elf_spot_check": "W 3876 / V 536.7 kip -- matches engine"}
    iss = CC._elf_spot_check_issues(pkg)
    assert iss and "3829" in iss[0] and "536.7" in iss[0]
    pkg["elf_spot_check"] = "V = 0.1385 x 3829 = 530.2 kip -- matches engine"
    assert CC._elf_spot_check_issues(pkg) == []


# ---------------- strap bays: strap selection + connection, not sheathing ----------------

def test_strap_bay_completeness_rule():
    cfg = dict(system="strap_braced")
    pkg = {"wall_lines": [dict(id="wall-X-N-s1", direction="X", story=1, DC=0.5,
                               selection="6 in. x 68 mil strap each face", connection="20 No. 12 screws")]}
    assert not [i for i in CC._cfs_slot_issues(cfg, pkg) if "wall-X-N-s1" in i]
    bare = {"wall_lines": [dict(id="wall-X-N-s1", direction="X", story=1, DC=0.5,
                                sheathing="NONE -- X-strap bay")]}
    assert any("strap bay" in i for i in CC._cfs_slot_issues(cfg, bare))
    # the app gate's shape (sheathing NONE + strap data in fastener_schedule) is accepted
    gate = {"wall_lines": [dict(id="wall-X-N-s1", direction="X", story=1, DC=0.5, sheathing="NONE -- X-strap bay",
                                fastener_schedule="6 in. x 68 mil strap, 18 No. 12 screws each end")]}
    assert not [i for i in CC._cfs_slot_issues(cfg, gate) if "wall-X-N-s1" in i]
    wsp = {"wall_lines": [dict(id="wall-X-A-s1", direction="X", story=1, DC=0.5)]}
    assert any("no sheathing + fastener_schedule" in i for i in CC._cfs_slot_issues({"system": "wsp_shearwall"}, wsp))


def test_code_basis_issues_is_wired_into_check(tmp_path):
    pkg = {"connections": [dict(id="c1", limit_state="screw shear J4.3.1 (phi = 0.55) and J4.3.2 Pss",
                                DC=0.5, cited="S100 J4")]}
    iss = CC.check("x", root=str(tmp_path), pkg=pkg, verbose=False)
    assert any("phi = 0.50 / Omega = 3.00" in i for i in iss)


def test_rho_one_in_sdc_d_needs_the_12_3_4_2_showing():
    cfg = ref_cfg(rho=1.0)
    pkg = {"rho": 1.0, "rho_basis": "ASCE 7-22 12.3.4 (canonical SDC); rho=1.00 is MULTIPLIED into ..."}
    assert CC._rho_basis_issues(cfg, pkg)
    assert CC._rho_basis_issues(cfg, dict(pkg, solution_notes="rho = 1.0 per 12.3.4.2")), "assertion only"
    ok = dict(pkg, rho_justification="12.3.4.2(b): regular plan; 4 bays of 24-ft walls per side >= 2L/h = 2 x 24/10")
    assert CC._rho_basis_issues(cfg, ok) == []
    assert CC._rho_basis_issues(ref_cfg(seis=CS.seis_cfs(0.45, 0.19, 0.17, "wsp_shearwall"), rho=1.0), pkg) == []


def test_h11_for_members_in_compression_and_716_snow_equations():
    m = dict(id="frame-raf", Pc_paired_kip=5.2, DC=0.86, limit_state="H1.1 interaction", cited="S100 H1.1")
    assert CC._h11_compression_issues({"members": [m]})
    assert CC._h11_compression_issues({"members": [dict(m, cited="S100 H1.2")]}) == []
    pkg = {"snow_map": {"step": "hd = 0.43*(180)^(1/3)*(30)^(1/4) - 1.5 = 4.19 ft", "ros": "5-psf rain-on-snow"}}
    iss = CC._snow_basis_issues(pkg)
    assert len(iss) == 2 and "W2" in iss[0] and "8 psf" in iss[1]

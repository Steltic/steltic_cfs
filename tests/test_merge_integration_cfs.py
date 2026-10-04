"""Integration tests for the review-fixes merge of steltic_cfs: the seams between fix/cfs-walldrift,
fix/cfs-wallloads, fix/cfs-portal, fix/cfs-gates, the synced hot-rolled engine and the HR ports.
Hand values in the comments."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (os.path.join(REPO, "steel_engine"), REPO, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cfs_systems as CS       # noqa: E402
import cfs_engine as CE        # noqa: E402
import cfs_pipeline as CP      # noqa: E402
import cfs_gates as G          # noqa: E402
import consistency as CC       # noqa: E402
import wall_line as WL         # noqa: E402
from test_cfs_wall_loads import box, mezz   # noqa: E402

S400_WP = dict(sheathing="osb", s_in=4.0, t_stud_in=0.0538, t_sheathing_in=0.4375, faces=2,
               Gt_lb_in=77500.0, chord_area_in2=3.0, k_anchor_kip_in=150.0)


# ---------------- story_weight: walldrift per-level geometry x wallloads 12.7.2 terms ----------------

def test_story_weight_storage_on_floor_part_and_roof_snow_on_roof_part():
    # 2 stories, 100 x 50 = 5000 sf; level 2 is half lower roof (2500 sf) half storage floor
    cfg = box(stories=2, snow=50.0, storage_levels=[2], L_by_level={2: 125.0},
              roof_area_sf_by_level={2: 2500.0}, partition_psf=5.0, clad=0.0)
    w, bd = CE.story_weight(cfg, 2, detail=True)
    # dead 20*2500 + 35*2500 = 137.5; partitions max(5,10)=10 psf on the floor part = 25.0;
    # storage 0.25*125*2500 = 78.125; snow 0.15*50*2500 = 18.75 (pf > 45, roof part only)
    assert abs(bd["dead"] - 137.5) < 1e-6 and abs(bd["partitions"] - 25.0) < 1e-6
    assert abs(bd["storage"] - 78.125) < 1e-6 and abs(bd["snow"] - 18.75) < 1e-6
    assert abs(w - (137.5 + 25.0 + 78.125 + 18.75)) < 1e-6
    # level_weights_kip overrides everything (walldrift CFS-09) and works with detail=True
    w2, bd2 = CE.story_weight(dict(cfg, level_weights_kip={2: 300.0}), 2, detail=True)
    assert w2 == 300.0 and bd2["level_weights_kip"] == 300.0


def test_theta_px_does_not_double_count_storage():
    # Ex29-like mezzanine: W = 23.04 dead + 60.0 storage + 2.76 cladding = 85.80 kip;
    # 12.8.7 Px = D + 0.5*(0.8*125)*1920/1000 = (85.80 - 60.0) + 96.0 = 121.8 kip (storage is
    # live load -- it enters through L0, not also through W)
    cfg = mezz()
    assert abs(CE.story_weight(cfg, 1) - 85.80) < 0.01
    assert abs(CE.gravity_Px_level(cfg, 1) - 121.80) < 0.01


def test_theta_and_drift_use_each_directions_system():
    # X strap (Cd 3.5, Om0 2.0), Y WSP (Cd 4.0, Om0 3.0): theta_max = 0.5/(beta Cd), beta >= 1.25/Om0
    sx = CS.seis_cfs(1.0, 0.45, 0.45, "strap_braced")
    sy = CS.seis_cfs(1.0, 0.45, 0.45, "wsp_shearwall")
    cfg = box(seis_by_dir={"X": sx, "Y": sy}, system_by_dir={"X": "strap_braced", "Y": "wsp_shearwall"},
              wall_props=S400_WP)
    r = CE.run(cfg)
    tx = r["directions"]["X"]["lines"]["X1"][1]["theta_max"]
    ty = r["directions"]["Y"]["lines"]["Y1"][1]["theta_max"]
    assert abs(tx - min(0.5 / (1.0 * sx["Cd"]), 0.25)) < 1e-9
    assert abs(ty - min(0.5 / (1.0 * sy["Cd"]), 0.25)) < 1e-9 and tx != ty
    # Vx for theta is the direction's own ELF story shear (R differs -> V differs)
    assert abs(r["directions"]["X"]["lines"]["X1"][1]["Vx_kip"] - r["elf_by_dir"]["X"]["V"]) < 1e-6
    assert abs(r["directions"]["Y"]["lines"]["Y1"][1]["Vx_kip"] - r["elf_by_dir"]["Y"]["V"]) < 1e-6
    # drift amplified with the direction's Cd
    x = r["directions"]["X"]["lines"]["X1"][1]
    assert abs(x["drift_amplified_first_order"] - sx["Cd"] * x["dr_ratio"]) < 1e-12


def test_footnote_a_no_drift_limit_keeps_rows_numeric_and_ok():
    cfg = box(stories=1, drift_limit_no_limit_single_story=True, drift_tolerant_finishes=True)
    r = CE.run(cfg)
    assert r["directions"]["X"]["drift_limit_none"] and not r["directions"]["X"]["drift_flags"]
    pkg = CP.build_package("fa", cfg, r)
    assert all(d["ok"] and d["limit"] is None and "theta" in d for d in pkg["drift_table"])


def test_package_drift_rows_carry_both_branches_columns():
    cfg = box(wall_props=S400_WP)
    pkg = CP.build_package("cols", cfg, CE.run(cfg))
    d = [x for x in pkg["drift_table"] if x["story"] == 2][0]
    for k in ("drift_amplified", "drift_single_story", "drift_rotation_from_below", "theta",
              "theta_max", "limit", "ok"):
        assert k in d, k
    assert d["drift_rotation_from_below"] > 0             # story 2 carries story-1 rotation
    assert pkg["drift_limit_basis"]["X"] and pkg["drift_basis"]


# ---------------- independent tributary follows the transfer ----------------

def test_independent_tributary_follows_base_story_founding():
    cfg = box(stories=2, wall_props=S400_WP)
    segs = {1: [], 2: [(20.0, 10.0), (10.0, 10.0)]}
    cfg["lines_x"][1] = WL.WallLine("X2", 25.0, segs, base_story=2)
    r = CE.run(cfg)
    it = G.independent_tributary(cfg, r)
    assert not it["flags"], it["flags"]
    # X2 story 1 has no wall and is founded: 0; X1 story 1 = F1 * 25/50 (no transfer from X2)
    F = r["elf"]["Fx"]
    assert it["by_direction"]["X"]["X2"][1]["V_independent"] == 0.0
    assert abs(it["by_direction"]["X"]["X1"][1]["V_independent"]
               - round(F[2] * 12.5 / 50.0 + F[1] * 25.0 / 50.0, 2)) < 0.02


# ---------------- consistency: cfs-wallloads requests ----------------

def test_gypsum_is_capacity_design_gated_and_not_called_not_detailed():
    cfg = box(system="gypsum_wall", sds=1.0, sd1=0.45, s1=0.45)
    # SDC D: gypsum is an S400 E6 system -> no 'not specifically detailed' note
    assert not [i for i in CC._design_basis_issues(cfg, "g", {}) if "S400 does not apply" in i]
    pkg = dict(system="gypsum_wall", holddowns=[dict(id="hd-1", T_cum_kip=10.0, T_cd_seed_kip=20.0,
                                                     T_design_kip=10.0)])
    out = CC._capacity_design_numeric_issues(cfg, pkg)
    assert out and "below the capacity-design seed" in out[0]
    # 0.9D relief may lower it, and the agent's own computed Omega_E*Vn clears it
    pkg["holddowns"][0]["dead_relief_kip_available"] = 10.5
    assert CC._capacity_design_numeric_issues(cfg, pkg) == []
    pkg["holddowns"][0].update(dead_relief_kip_available=0.0, T_OmegaE_Vn_agent_kip=10.0)
    assert CC._capacity_design_numeric_issues(cfg, pkg) == []
    # S400 A1.2.3: R = 3 in SDC B -> no capacity-design chain, gate silent
    cfgB = box(system="not_detailed", sds=0.3, sd1=0.1, s1=0.05)
    assert CC._capacity_design_numeric_issues(cfgB, dict(pkg, system="not_detailed")) == []


def test_not_detailed_note_needs_numeric_governing_hazard():
    cfg = box(system="not_detailed", sds=0.3, sd1=0.1, s1=0.05)
    note = lambda pkg: [i for i in CC._design_basis_issues(cfg, "n", pkg) if "S400 does not apply" in i]
    assert note({})
    assert note({"governing_hazard": {"statement": "wind governs"}})             # prose only
    assert not note({"governing_hazard": {"X": "WIND governs: 34.2 kip vs seismic 7.9 kip"}})
    assert not note({"governing_hazard": {"X": {"wind_kip": 34.2, "seismic_kip": 7.9}}})


def test_flexible_diaphragm_with_concrete_topping_fails():
    cfg = box(diaphragm_topping_in=3.0)
    assert [i for i in CC._design_basis_issues(cfg, "t", {}) if "12.3.1.1" in i and "topping" in i]
    assert not [i for i in CC._design_basis_issues(dict(cfg, diaphragm_MDD_ADVE=2.5), "t", {})
                if "12.3.1.1" in i and "topping" in i]


def test_strap_ductility_verdict_is_the_evidence():
    cfg = box(system="strap_braced")
    base = dict(wall_lines=[], capacity_design={})
    fail = dict(base, capacity_design=dict(strap_ductility=dict(ok=False, message="Gr 33: 1.09 < 1.2")))
    ok = dict(base, capacity_design=dict(strap_ductility=dict(ok=True, message="1.30 >= 1.2")))
    assert any("STRAP DUCTILITY FAILS" in i for i in CC._system_checks_issues(cfg, fail))
    assert not any("strap ductility" in i.lower() for i in CC._system_checks_issues(cfg, ok))


def test_raw_wallloads_seed_does_not_trip_r9():
    for cfg in (box(), box(system="strap_braced"), mezz()):
        pkg = CP.build_package("r9", cfg, CE.run(cfg))
        assert CC._named_not_computed_issues(pkg) == []


# ---------------- report renders the new seeds ----------------

def test_report_renders_wallloads_and_walldrift_blocks():
    import report as RPT
    cfg = mezz()
    pkg = CP.build_package("m", cfg, CE.run(cfg))
    html = RPT._cfs_schedules_section(pkg)
    assert "Gravity framing" in html and "Collector design levels" in html
    assert "Strap ductility" in html and "Single-story / rotation from below" in html
    rows = RPT._cfs_basis_rows(cfg, CE.run(cfg), pkg)
    labels = " ".join(r[0] for r in rows)
    assert "seismic weight by level" in labels and "drift-limit basis" in labels
    assert "diaphragm idealization" in labels and "S400 capacity design" in labels


# ---------------- hot-rolled route ports (HR-14 / HR-15 / HR-20 / HR-22) ----------------

def _hr_cfg(**kw):
    cfg = dict(heights=[180.0] * 5, NX=4, NY=3, SX=360.0, SY=360.0, system="OCBF",
               seis=dict(SDS=1.0, SD1=0.6, S1=0.5, R=3.25, Cd=3.25, Om0=2.0, Ie=1.0,
                         Ct=0.02, x=0.75))
    cfg.update(kw)
    return cfg


def test_hr_route_sdc_override_and_height_limit():
    import preflight as PF
    # declared SDC B on an SDS = 1.0 site: overridden to the derived D (HR-15)
    assert PF.sdc_of_cfg(_hr_cfg(sdc="B")) == "D"
    # OCBF, 75 ft in SDC D > 35 ft (Table 12.2-1) -> consistency ERROR via preflight (HR-20)
    iss = CC._height_limit_issues(_hr_cfg())
    assert any("35" in i for i in iss), iss
    # the CFS route keeps the CFS system table
    assert CC._height_limit_issues(box(stories=8)) and \
        not CC._height_limit_issues_hr is CC._height_limit_issues


def test_hr_route_rbs_declaration():
    pkg = {"connections": [{"id": "c1", "type": "RBS moment connection"}]}
    assert CC._rbs_declaration_issues(_hr_cfg(system="SMF"), pkg)
    assert CC._rbs_declaration_issues(box(), pkg) == []          # CFS cfgs: not applicable


def test_hr_route_completion_gate_reads_sanity_results(tmp_path):
    from steltic import agent as A
    d = tmp_path / "design"
    d.mkdir()
    (tmp_path / "cfg.py").write_text("cfg = {}\n")
    import hashlib
    sha = hashlib.sha256((tmp_path / "cfg.py").read_bytes()).hexdigest()
    rec = dict(cfg_sha256=sha, checks=dict(drift_X=False, model_complete=False, period=True),
               extra=dict(theta=dict(max=0.05, theta_max=0.10)))
    (d / "pipeline_results.json").write_text(json.dumps(rec))
    probs = A._hr_gate_sanity(tmp_path, {})
    assert any("drift_X FAILED" in p and "not waivable" in p for p in probs)
    assert any("model_complete FAILED" in p for p in probs)
    probs = A._hr_gate_sanity(tmp_path, {"gate_waivers": {"model_complete": "x" * 25}})
    assert not any("model_complete" in p for p in probs)
    (tmp_path / "cfg.py").write_text("cfg = {'changed': 1}\n")
    assert any("STALE" in p for p in A._hr_gate_sanity(tmp_path, {}))


def test_engine_sync_api_used_by_cfs_report():
    import engine3d as E
    for fn in ("theta_rows", "stability_theta", "seismic_drift", "rbs_drift_factor", "sdc_of"):
        assert hasattr(E, fn), fn


def test_portal_theta_max_uses_eq_12_8_19_floor_and_beta():
    import cfs_frame as CF
    cfg = CF._demo_cfg()
    cfg["seis"] = dict(SDS=0.25, SD1=0.10, S1=0.05, R=3.0, Cd=6.0, Om0=3.0, Ie=1.0, W_frame_kip=4.0)
    se = [r for r in CP.build_portal_package("t", cfg, CF.run(cfg))["drift_table"]
          if r.get("check") == "stability_theta"][0]
    # 0.5/(1.0*6.0) = 0.083 < 0.10 -> theta_max = 0.10 (7-22 floor); basis cites Eq. 12.8-18/-19
    assert abs(se["limit"] - 0.10) < 1e-9 and "12.8-19" in se["basis"]
    cfg["seis"] = dict(cfg["seis"], Cd=3.0, Om0=1.0)       # beta >= 1.25/Om0 = 1.25
    se = [r for r in CP.build_portal_package("t", cfg, CF.run(cfg))["drift_table"]
          if r.get("check") == "stability_theta"][0]
    assert abs(se["limit"] - 0.5 / (1.25 * 3.0)) < 1e-4


def test_recheck_after_rerun_is_not_silent():
    # merge_fills clears the top-level D/C when a seeded demand moved, but the carried per-check
    # D/Cs were computed at the OLD demand -- consistency must say so (no silent pass)
    pkg = dict(members=[dict(id="frame-col", M_kipin=1036.0, DC=None, DC_before_rerun=0.9,
                             recheck_after_rerun="seeded demand changed on re-run: M_kipin 936.7 -> 1036.0",
                             limit_state="H1.2", cited="S100 H1.2",
                             checks=[dict(limit_state="F2", DC=0.9, cited="S100 F2")])],
               connections=[dict(id="c", DC=0.5, cited="S100 J4", limit_state="J4")])
    iss = CC.check("x", pkg=pkg, verbose=False)
    assert any("OLD demand" in i and "frame-col" in i for i in iss), iss


def test_type_ii_screen_ignores_the_seeds_own_instruction_text():
    cfg = mezz()
    pkg = CP.build_package("m", cfg, CE.run(cfg))
    assert "Type II" in json.dumps(pkg.get("capacity_design"))           # wallloads seed text
    assert not [i for i in CC._cfs_slot_issues(cfg, pkg) if "Type II" in i]


def test_merge_fills_keeps_fresh_seeded_drift_and_flags_stale_design(tmp_path, monkeypatch):
    import pipeline as PL
    monkeypatch.setenv("STEEL_BUILDER_JOBS", str(tmp_path))
    d = tmp_path / "job" / "design"
    d.mkdir(parents=True)
    fresh = dict(drift_table=[dict(direction="Y", line="1", story=5, drift_amplified=0.0298, theta=0.05,
                                   limit=0.02, ok=False),
                              dict(direction="Y", line="1", story=6, drift_amplified=0.0100, limit=0.02,
                                   ok=True)],
                 holddowns=[dict(id="hd-1", T_cum_kip=12.0, k_kip_in=80.0, device_class="rod",
                                 DC=None, cited=None)])
    old = dict(drift_table=[dict(direction="Y", line="1", story=5, drift_amplified=0.0048, limit=0.02,
                                 ok=True, drift_design=0.006),
                            dict(direction="Y", line="1", story=6, drift_amplified=0.0100, limit=0.02,
                                 ok=True, drift_design=0.009)],
               holddowns=[dict(id="hd-1", T_cum_kip=12.0, k_kip_in=50.0, device_class="rod",
                               DC=0.8, cited="S400 E1", selection="1-in. rod")])
    (d / "calc_package_cfs.json").write_text(json.dumps(fresh))
    (d / "calc_package_cfs.json.filled.bak").write_text(json.dumps(old))
    PL.merge_fills("job")
    m = json.loads((d / "calc_package_cfs.json").read_text())
    r5, r6 = m["drift_table"]
    assert r5["drift_amplified"] == 0.0298 and r5["ok"] is False and r5["theta"] == 0.05
    assert "re-derive drift_design" in r5["recheck_after_rerun"]
    assert r6["ok"] is True and r6["drift_design"] == 0.009 and "recheck_after_rerun" not in r6
    assert any("re-derive drift_design" in i for i in CC._drift_table_issues(m))
    h = m["holddowns"][0]
    assert h["k_kip_in"] == 80.0 and h["DC"] == 0.8 and h["selection"] == "1-in. rod"

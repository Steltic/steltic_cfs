"""CFS gates, package naming, report dispatch, backups, two-stage and preflight (fix/cfs-gates).

Covers CFS-13/14/29/30/31/32/35/39(preflight)/HR-14 port. Each test states the review repro it
pins (consolidated/CFS.md)."""
import json
import math
import os
import shutil
import sys
import time
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ENG = os.path.join(REPO, "steel_engine")
for _p in (ENG, REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import consistency as CC          # noqa: E402
import cfs_systems as CS          # noqa: E402
import wall_line as WL            # noqa: E402
import cfs_engine as CE           # noqa: E402
import cfs_pipeline as CP         # noqa: E402
import cfs_gates as G             # noqa: E402
import preflight as PF            # noqa: E402


def _wall_cfg(**kw):
    segs = {k: [(20.0, 9.5), (10.0, 9.5)] for k in (1, 2, 3, 4)}
    segsB = {k: [(15.0, 9.5)] for k in (1, 2, 3, 4)}
    cfg = dict(stories=4, heights_ft=[10.67, 9.5, 9.5, 9.5], plan_ft=(160.0, 62.0),
               D_floor=35.0, D_roof=20.0, clad=15.0, snow=0.0, L_floor=40.0,
               seis=CS.seis_cfs(1.0, 0.45, 0.45, "wsp_shearwall"), system="wsp_shearwall",
               risk_cat="II", structure_kind="wall", analysis_fidelity=0,
               diaphragm="flexible",
               lines_x=[WL.WallLine("X1", 0.0, segs), WL.WallLine("X2", 31.0, segsB),
                        WL.WallLine("X3", 62.0, segs)],
               lines_y=[WL.WallLine("Y1", 0.0, segsB), WL.WallLine("Y2", 80.0, segsB),
                        WL.WallLine("Y3", 160.0, segsB)],
               wall_props=dict(chord_area_in2=1.2, Gp_kip_in=9.0, en_in=0.03,
                               k_anchor_kip_in=50.0))
    cfg.update(kw)
    return cfg


_CFG_SRC = '''import cfs_systems as CS, wall_line as WL
segs = {k: [(20.0, 9.5), (10.0, 9.5)] for k in (1, 2, 3, 4)}
segsB = {k: [(15.0, 9.5)] for k in (1, 2, 3, 4)}
cfg = dict(stories=4, heights_ft=[10.67, 9.5, 9.5, 9.5], plan_ft=(160.0, 62.0),
           D_floor=35.0, D_roof=20.0, clad=15.0, snow=0.0, L_floor=40.0,
           seis=CS.seis_cfs(1.0, 0.45, 0.45, "wsp_shearwall"), system="wsp_shearwall",
           risk_cat="II", structure_kind="wall", analysis_fidelity=0, diaphragm="flexible",
           wind=dict(V=100.0, exposure="C"),
           lines_x=[WL.WallLine("X1", 0.0, segs), WL.WallLine("X2", 31.0, segsB),
                    WL.WallLine("X3", 62.0, segs)],
           lines_y=[WL.WallLine("Y1", 0.0, segsB), WL.WallLine("Y2", 80.0, segsB),
                    WL.WallLine("Y3", 160.0, segsB)],
           wall_props=dict(chord_area_in2=1.2, Gp_kip_in=9.0, en_in=0.03, k_anchor_kip_in=50.0))
'''


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("STEEL_BUILDER_JOBS", str(tmp_path))
    return tmp_path


def _make_job(jobs, name="T_cfs", src=_CFG_SRC):
    root = jobs / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "cfg.py").write_text(src)
    m = types.ModuleType("c")
    exec(compile(src, "cfg.py", "exec"), m.__dict__)
    return root, m.cfg


# ------------------------------------------------------------------ CFS-30: exact keys / tolerance
def test_phiPn_is_not_read_as_both_demand_and_capacity():
    """Repro: {phiPn_kip: 50, Pu_kip: 40, DC: 0.8} was flagged '50/50 = 1.000'."""
    e = dict(id="s", limit_state="E2", cited="S100 E2", DC=0.8,
             checks=[dict(limit_state="E2 global", phiPn_kip=50.0, Pu_kip=40.0, DC=0.8)])
    assert CC._entry_issues("stud", e) == []
    bad = dict(e, checks=[dict(limit_state="E2", phiPn_kip=50.0, Pu_kip=40.0, DC=0.5)], DC=0.5)
    iss = CC._entry_issues("stud", bad)
    assert any("40/50 = 0.800" in i for i in iss), iss


def test_interaction_rows_are_not_recomputed_and_abs_floor():
    e = dict(id="s", limit_state="H1", cited="S100 H1.2", DC=0.91,
             checks=[dict(limit_state="H1.2 interaction", Pu_kip=10.0, phiPn_kip=20.0, DC=0.91),
                     dict(limit_state="G2 shear", Vu_kip=0.12, phiVn_kip=10.0, DC=0.02)])
    assert CC._entry_issues("stud", e) == []          # 0.012 vs 0.020: inside the 0.01 floor


def test_schedule_rows_are_recomputed_and_counted():
    s = dict(id="sched-strap", limit_state="D2", cited="S100 D2", DC=0.93,
             rows=[dict(panel="a", V_u_kip=34.37, phiVn_kip=36.94, DC=0.93),
                   dict(panel="b", V_u_kip=10.0, phiVn_kip=10.0, DC=1.0),
                   dict(panel="c", V_u_kip=5.0, phiVn_kip=10.0, DC=0.2)])
    iss = CC._entry_issues("schedule", s)
    assert any("row 'c'" in i for i in iss), iss                     # 0.2 != 5/10
    assert any("headline D/C 0.930 != worst" in i for i in iss), iss  # row b governs (1.00)
    assert not any("row 'a'" in i or "row 'b'" in i for i in iss), iss


# ------------------------------------------------------------------ CFS-29(b)/30: seed text
def _seed_pkg(**kw):
    cfg = _wall_cfg(**kw)
    return cfg, CP.build_package("T", cfg, CE.run(cfg))


def test_raw_seed_does_not_pass_its_own_system_checks_nor_fail_r9():
    """Repro: the raw WSP seed returned _system_checks_issues == [] (seed text satisfied it) and
    its own capacity_design.instruction tripped the named-not-computed screen."""
    cfg, pkg = _seed_pkg()
    sysi = CC._system_checks_issues(cfg, pkg)
    assert any("expected wall strength" in i for i in sysi), sysi
    assert any("FASTENER" in i for i in sysi), sysi
    assert CC._named_not_computed_issues(pkg) == []
    # computed agent evidence clears it
    pkg["capacity_design"]["expected_strength"] = dict(Omega_E=1.3, T_expected_kip=52.0)
    for w in pkg["wall_lines"]:
        w["fastener_schedule"] = "#8 @ 4/12"
    assert CC._system_checks_issues(cfg, pkg) == []
    # an agent symbolic inequality is still caught
    pkg["capacity_design"]["check"] = "track thickness t >= h / 200"
    assert CC._named_not_computed_issues(pkg)


def test_uplift_screen_ignores_seed_combos():
    cfg, pkg = _seed_pkg(wind=dict(V=140.0, exposure="C"))
    assert any("0.9D+1.0W" in c["label"] for c in pkg["combos"])      # the seed has it ...
    assert any("NET-UPLIFT" in i for i in CC._cfs_slot_issues(cfg, pkg))   # ... it does not count
    pkg["connections"] = [dict(id="uplift-clip", limit_state="0.9D+1.0W net uplift clip",
                               cited="S100 J4", DC=0.6)]
    assert not any("NET-UPLIFT" in i for i in CC._cfs_slot_issues(cfg, pkg))


def test_type_ii_screen_ignores_negations():
    pkg = dict(wall_lines=[], notes_agent="No Type II walls; all Type I segmented, no perforated walls")
    assert not any("Type II" in i for i in CC._cfs_slot_issues({}, pkg))
    pkg = dict(wall_lines=[], wall_type="Type II perforated on line A")
    assert any("Type II" in i for i in CC._cfs_slot_issues({}, pkg))


def test_holddown_band_uses_design_or_capacity_design_tension():
    """Repro (CFS-29e): 'bolted' at T_cum 17.1 kip passed while T_cd was 49.5 kip."""
    hd = dict(id="hd-X-N", T_cum_kip=17.1, T_cd_seed_kip=49.5, device_class="bolted",
              basis="cumulative overturning tension")
    iss = CC._cfs_slot_issues({}, dict(holddowns=[hd]))
    assert any("49.5" in i and "bolted" in i for i in iss), iss
    hd2 = dict(hd, T_design_kip=18.0)
    assert not any("envelope" in i for i in CC._cfs_slot_issues({}, dict(holddowns=[hd2])))


# ------------------------------------------------------------------ CFS-29(c)(d): drift/theta/waivers
def test_failing_drift_row_fails_even_with_resolution():
    """Repro: an Ex10 drift row at D/C 1.6 gave 0 issues; drift_flags were cleared by any text."""
    pkg = dict(drift_table=[dict(check="SBMF drift", ratio=0.04, limit=0.025, ok=False, DC=1.6)],
               drift_flags=["x"], drift_flags_resolution="accepted")
    assert any("FAILS" in i for i in CC._drift_table_issues(pkg))
    ok = dict(drift_table=[dict(direction="X", line="A", story=1, drift_amplified=0.031,
                                limit=0.025, ok=True, drift_design=0.022)])
    assert CC._drift_table_issues(ok) == []                      # designed value overrides screen
    nov = dict(drift_table=[dict(check="eave_sway", value_in=1.0, ok=None)])
    assert any("no verdict" in i for i in CC._drift_table_issues(nov))
    w = dict(drift_table=[dict(direction="X", line="S", story=2, drift_amplified=0.05, limit=0.025,
                               ok=False, waived="declared split-level inter-diaphragm offset; "
                                                "step ties designed (conn-step)")])
    assert CC._drift_table_issues(w) == []


def test_theta_over_limit_fails():
    assert CC._theta_issues(dict(stability=dict(theta=0.12, theta_max=0.10)))
    assert CC._theta_issues(dict(stability=dict(theta=0.30)))          # absolute 0.25 ceiling
    assert CC._theta_issues(dict(stability=dict(theta=0.05, theta_max=0.10))) == []


def test_waiver_with_dc_over_one_needs_scope():
    """Repro: {DC: 2.0, waived: 'x'} gave []."""
    iss = CC._waiver_issues("member", dict(id="m", DC=2.0, waived="x"))
    assert len(iss) == 2
    good = dict(id="m", DC=2.0, waived="EXISTING joist retained in the retrofit; strengthening "
                                       "by others", waiver_scope="existing")
    assert CC._waiver_issues("member", good) == []
    assert CC._waiver_issues("member", dict(id="m", DC=2.0, waived="this new beam is fine I think")) \
        and len(CC._waiver_issues("member", dict(id="m", DC=2.0, waived="this new beam is fine I think"))) == 1
    assert CC.waived_ng_items(dict(members=[good])) == [("member", "m", 2.0, good["waived"])]


def test_check_runs_waivers_anchorage_and_schedules(jobs):
    root, cfg = _make_job(jobs)
    (root / "design").mkdir()
    pkg = dict(connections=[dict(id="c", limit_state="J4", cited="S100 J4", DC=0.5)],
               members=[dict(id="m", DC=2.0, waived="x")],
               anchorage=[dict(id="base-anchor", T_net_uplift_kip=3.0, limit_state=None,
                               cited=None, capacity=None, DC=None)],
               schedules=[dict(id="sched-girt", schedule="girt", rows=None, DC=None)])
    (root / "design" / "calc_package_cfs.json").write_text(json.dumps(pkg))
    iss = CC.check("T_cfs", root=str(root), verbose=False)
    blob = "\n".join(iss)
    assert "[anchorage base-anchor] no D/C" in blob and "[schedule sched-girt] no D/C" in blob
    assert "WAIVED with D/C = 2.000" in blob
    st = json.loads((root / "design" / "consistency_result.json").read_text())
    assert st["package"] == "calc_package_cfs.json" and st["result"] == "FAIL"


# ------------------------------------------------------------------ CFS-13: one package file
def test_package_path_rules(tmp_path):
    d = tmp_path / "design"
    d.mkdir()
    cfg = _wall_cfg()
    assert CC.package_path(str(tmp_path), cfg)[0] is None
    (d / "calc_package.json").write_text("{}")
    p, notes, issues = CC.package_path(str(tmp_path), cfg)
    assert p.endswith("calc_package.json") and notes and not issues        # legacy, compat read
    (d / "calc_package_cfs.json").write_text("{}")
    p, notes, issues = CC.package_path(str(tmp_path), cfg)
    assert p.endswith("calc_package_cfs.json") and issues                  # duplicate = issue
    p, notes, issues = CC.package_path(str(tmp_path), {"heights": [156.0]})  # hot-rolled job
    assert p.endswith("calc_package_cfs.json")      # _cfs present -> treated as CFS job


def test_pipeline_backup_merge_and_build_report_dispatch(jobs):
    """CFS-13 repro: report.build_report(name) on a CFS job raised KeyError 'heights'.
    CFS-14 repro: design_and_report silently overwrote a filled calc_package_cfs.json."""
    import pipeline as PL
    import report as RPT
    root, cfg = _make_job(jobs)
    out = PL.design_and_report("T_cfs", cfg)
    pkgp = root / "design" / "calc_package_cfs.json"
    assert out["package"].endswith("calc_package_cfs.json") and pkgp.exists()
    assert out["package_backup"] == []
    assert os.path.exists(out["report_html"])
    assert "calc_package_cfs.json" in out["NEXT_STEP"]
    # fill one slot, re-render through build_report: the fill survives and the CFS report renders
    pkg = json.loads(pkgp.read_text())
    hd = pkg["holddowns"][0]
    hd.update(T_design_kip=77.7, DC=0.88, capacity=88.3, limit_state="rod tension",
              cited="S100 J3.4", selection="7/8 in. A193 B7 rod")
    pkgp.write_text(json.dumps(pkg))
    html_path = RPT.build_report("T_cfs")
    html = open(html_path, encoding="utf-8").read()
    assert "77.7" in html and "7/8 in. A193 B7 rod" in html          # DESIGN values rendered
    assert "seed band" in html                                        # unfilled ones say seed
    # re-run: the filled package is backed up + warned, then merge_fills carries the fill back
    out2 = PL.design_and_report("T_cfs", cfg)
    assert out2["package_backup"] and "filled.bak" in out2["package_backup"][0]
    assert (root / "design" / "calc_package_cfs.json.filled.bak").exists()
    fresh = json.loads(pkgp.read_text())
    assert fresh["holddowns"][0]["DC"] is None
    summ = PL.merge_fills("T_cfs")
    merged = json.loads(pkgp.read_text())
    assert merged["holddowns"][0]["T_design_kip"] == 77.7 and merged["holddowns"][0]["DC"] == 0.88
    assert summ["carried"] >= 1 and summ["rechecks"] == []
    # a legacy calc_package.json beside it is moved aside on the next run (one file remains)
    (root / "design" / "calc_package.json").write_text(json.dumps(merged))
    out3 = PL.design_and_report("T_cfs", cfg, keep_fills=True)
    assert not (root / "design" / "calc_package.json").exists()
    assert any("legacy" in m for m in out3["package_backup"])
    assert json.loads(pkgp.read_text())["holddowns"][0]["T_design_kip"] == 77.7   # keep_fills


# ------------------------------------------------------------------ CFS-29(a): independent tributary
def test_independent_tributary_is_not_an_identity():
    cfg = _wall_cfg()
    res = CE.run(cfg)
    it = G.independent_tributary(cfg, res)
    assert it["flags"] == []                                   # clean geometry: 1.00-1.05
    r = it["by_direction"]["X"]["X2"][1]["ratio"]
    assert 1.0 <= r <= 1.051, r
    # a declared trib_scale (or a patched distribution) now FLAGS -- the engine gate cannot
    cfg2 = _wall_cfg()
    cfg2["lines_x"][1] = WL.WallLine("X2", 31.0, cfg2["lines_x"][1].segments, trib_scale=0.5)
    res2 = CE.run(cfg2)
    assert res2["directions"]["X"]["gate_flags"] == []        # the identity gate stays silent
    fl = G.independent_tributary(cfg2, res2)["flags"]
    assert any("X2" in f and "trib_scale=0.50" in f for f in fl), fl
    # hand check: X2 at story 1 = sum Fx * (31/62) (simple span half-way to each neighbour)
    V_ind = sum(res["elf"]["Fx"].values()) * 31.0 / 62.0
    assert abs(it["by_direction"]["X"]["X2"][1]["V_independent"] - round(V_ind, 2)) < 0.02


def test_independent_tributary_flags_line_without_wall_at_story():
    cfg = _wall_cfg()
    segs = {k: [(15.0, 9.5)] for k in (1, 2, 3)}
    segs[4] = []
    cfg["lines_x"][1] = WL.WallLine("X2", 31.0, segs)
    res = CE.run(cfg)
    # after the CFS-09 merge the engine transfers the missing story by the lever rule and the
    # independent recomputation follows the same mechanics -> agreement, no flags
    it = G.independent_tributary(cfg, res)
    assert not it["flags"], it["flags"]
    F4 = res["elf"]["Fx"][4]
    # X1 at story 4 = F4 x (simple-span half of the 62-ft strip, X2 absent) = F4 / 2
    assert abs(it["by_direction"]["X"]["X1"][4]["V_independent"] - round(F4 / 2.0, 2)) < 0.02
    assert it["by_direction"]["X"]["X2"][4]["V_independent"] == 0.0
    # an engine result that still loads the absent story is flagged
    res["directions"]["X"]["lines"]["X2"][4] = dict(V=5.0)
    fl = G.independent_tributary(cfg, res)["flags"]
    assert any("X2 story 4 has NO wall" in f for f in fl), fl


def test_rayleigh_period_single_spring_hand_check():
    """One story, one line per direction: T = 2 pi sqrt(W / (g K_secant))."""
    segs = {1: [(20.0, 10.0)]}
    cfg = dict(stories=1, heights_ft=[10.0], plan_ft=(40.0, 20.0), D_floor=0.0, D_roof=20.0,
               clad=0.0, snow=0.0, seis=CS.seis_cfs(1.0, 0.45, 0.4, "wsp_shearwall"),
               system="wsp_shearwall", diaphragm="flexible", structure_kind="wall",
               lines_x=[WL.WallLine("A", 10.0, segs)], lines_y=[WL.WallLine("1", 20.0, segs)],
               wall_props=dict(chord_area_in2=1.2, Gp_kip_in=9.0, en_in=0.03,
                               k_anchor_kip_in=50.0))
    res = CE.run(cfg)
    K = res["directions"]["X"]["lines"]["A"][1]["K_kip_in"]
    W = res["elf"]["W"]
    T = 2 * math.pi * math.sqrt(W / (386.4 * K))
    assert abs(G.rayleigh_period(cfg, res)["X"]["T_rayleigh_s"] - T) < 2e-3


# ------------------------------------------------------------------ CFS-32: two-stage podium
def test_two_stage_eligibility_and_amplification():
    cfg = _wall_cfg(structure_kind="podium",
                    two_stage=dict(R_lower=4.0, rho_lower=1.0, K_lower_kip_in=1.0e6,
                                   T_combined_s=0.01))
    res = CE.run(cfg)
    rho = CP._rho_seismic(cfg)
    blk = G.two_stage(cfg, res, rho)
    assert abs(blk["amplification"] - (6.5 / rho) / 4.0) < 1e-3       # (R/rho)u / (R/rho)l
    # T_combined 0.01 s is below 1.1 T_upper -> both items pass
    assert all(d["status"] == "ELIGIBLE" for d in blk["by_direction"].values())
    # K_upper hand definition: V / (base-shear-weighted roof displacement)
    dx = blk["by_direction"]["X"]
    assert abs(dx["K_upper_kip_in"] - res["elf"]["V"] / dx["delta_top_in"]) < 0.2
    r0 = blk["reactions_to_podium"][0]
    assert abs(r0["V_Eh_amplified_kip"] - r0["V_Eh_kip"] * blk["amplification"]) < 0.11
    soft = G.two_stage(dict(cfg, two_stage=dict(cfg["two_stage"], K_lower_kip_in=1.0)), res, rho)
    assert all(d["status"] == "NOT ELIGIBLE" for d in soft["by_direction"].values())
    none = G.two_stage(dict(cfg, two_stage=dict(R_lower=4.0)), res, rho)
    assert all(d["status"] == "NOT EVALUATED" for d in none["by_direction"].values())
    iss = CC._two_stage_issues(cfg, dict(two_stage_framework=none))
    assert len(iss) == 2 and "NOT EVALUATED" in iss[0]
    assert CC._two_stage_issues(cfg, dict(two_stage_framework=blk)) == []
    assert CC._two_stage_issues(cfg, {})                              # claimed, no block
    # amplification floor 1.0 (12.2.3.2(d))
    lo = G.two_stage(dict(cfg, two_stage=dict(R_lower=8.0, rho_lower=1.0)), res, 1.0)
    assert lo["amplification"] == 1.0 and lo["amplification_raw"] < 1.0


def test_two_stage_rayleigh_estimate_for_entire_structure():
    cfg = _wall_cfg(structure_kind="podium",
                    two_stage=dict(R_lower=4.0, K_lower_kip_in=1.0e5, W_lower_kip=800.0,
                                   podium_height_ft=15.0))
    res = CE.run(cfg)
    blk = G.two_stage(cfg, res, 1.0)
    d = blk["by_direction"]["X"]
    assert d["T_combined_s"] >= d["T_upper_s"] - 1e-3                # podium only lengthens T
    assert d["period_ratio"] < 1.1 and d["status"] == "ELIGIBLE"


# ------------------------------------------------------------------ preflight on the CFS path
def test_cfs_preflight_runs_storage_platform_rack_and_units():
    base = dict(lines_x=[], seis=dict(SDS=1, SD1=.5, R=4.0, Cd=3.5, Ie=1), system="strap_braced",
                heights_ft=[10.0], plan_ft=(40.0, 20.0), structure_kind="wall",
                wind=dict(V=100))
    f = PF.check(dict(base, L_floor=125.0, arch="storage mezzanine"))
    msgs = " ".join(m for _s, m in f)
    assert "12.7.2" in msgs and "15.1.1" in msgs
    assert not any(s == "ERROR" for s, _m in f)
    assert not any("12.7.2" in m for _s, m in PF.check(dict(base, L_floor=125.0, storage=True)))
    rack = PF.check(dict(base, arch="selective pallet racks"))
    assert any(s == "ERROR" and "OUT OF SCOPE" in m for s, m in rack)
    portal = PF.check(dict(span_ft=576.0, eave_ft=20.0, apex_ft=24.0, spacing_ft=25.0,
                           seis=dict(SDS=1, SD1=.5, R=3, Cd=3, Ie=1), system="not_detailed",
                           wind=dict(V=100)))
    assert any(s == "ERROR" and "INCHES" in m for s, m in portal)
    hr = PF.check(dict(heights=[156.0], seis=dict(SDS=1, SD1=.5, R=8, Cd=5.5, Ie=1),
                       system="smf"))               # hot-rolled cfg keeps the HR linter
    assert any("model" in m for _s, m in hr)


# ------------------------------------------------------------------ HR-14 port: completion gate
class _WS:
    def __init__(self, d):
        self.d = d

    def _job_dir(self):
        return self.d


def _clean_pkg():
    return dict(connections=[dict(id="c1", limit_state="J4", cited="S100 J4", DC="0.80 (governs)")],
                wall_lines=[dict(id="w", sheathing="7/16 OSB", fastener_schedule="#8@4/12",
                                 cited="S400 E1", DC=0.9)],
                holddowns=[dict(id="h", cited="S100 J3", DC=0.7)],
                drift_table=[dict(direction="X", line="A", story=1, drift_amplified=0.01,
                                  limit=0.025, ok=True)])


def _write(jd, pkg, stamp=True, issues=(), report_after=True):
    import hashlib
    d = jd / "design"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "calc_package_cfs.json"
    p.write_text(json.dumps(pkg))
    if not stamp and (d / "consistency_result.json").exists():
        (d / "consistency_result.json").unlink()
    if stamp:
        (d / "consistency_result.json").write_text(json.dumps(dict(
            package=p.name, package_sha1=hashlib.sha1(p.read_bytes()).hexdigest(),
            n_issues=len(issues), issues=list(issues), result="FAIL" if issues else "PASS")))
    rep = jd / "report.html"
    rep.write_text("x")
    t = time.time()
    os.utime(p, (t, t))
    os.utime(rep, (t + 5, t + 5) if report_after else (t - 5, t - 5))
    return p


def test_completion_gate_requires_consistency_drift_and_fresh_report(tmp_path):
    from steltic.agent import _completion_gate
    jd = tmp_path / "job"
    _write(jd, _clean_pkg())
    assert _completion_gate(_WS(jd)) == []
    _write(jd, _clean_pkg(), stamp=False)
    assert any("consistency.check" in p for p in _completion_gate(_WS(jd)))
    _write(jd, _clean_pkg(), issues=["x"])
    assert any("FAILS with 1" in p for p in _completion_gate(_WS(jd)))
    _write(jd, _clean_pkg(), report_after=False)
    assert any("older than the package" in p for p in _completion_gate(_WS(jd)))
    bad = _clean_pkg()
    bad["drift_table"][0].update(drift_amplified=0.04, ok=False)
    bad["drift_flags_resolution"] = "accepted"
    _write(jd, bad)
    assert any("FAILS" in p for p in _completion_gate(_WS(jd)))
    th = dict(_clean_pkg(), stability=dict(theta=0.2, theta_max=0.1))
    _write(jd, th)
    assert any("theta" in p for p in _completion_gate(_WS(jd)))
    wv = _clean_pkg()
    wv["connections"].append(dict(id="c2", DC=1.4, waived="x"))
    _write(jd, wv)
    probs = _completion_gate(_WS(jd))
    assert any("without an engineering justification" in p for p in probs)
    wv["connections"][-1]["waived"] = "new clip, accepted by judgement"
    _write(jd, wv)
    assert any("WAIVED at D/C = 1.400" in p for p in _completion_gate(_WS(jd)))
    ts = dict(_clean_pkg(), two_stage_framework=dict(by_direction=dict(X=dict(status="NOT EVALUATED"))))
    _write(jd, ts)
    assert any("two-stage" in p for p in _completion_gate(_WS(jd)))
    # a stale stamp (package edited after the check) is rejected
    p = _write(jd, _clean_pkg())
    p.write_text(json.dumps(dict(_clean_pkg(), note2="edited")))
    os.utime(jd / "report.html", (time.time() + 10, time.time() + 10))
    assert any("changed after the last consistency.check" in q for q in _completion_gate(_WS(jd)))


def test_completion_gate_two_package_files(tmp_path):
    from steltic.agent import _completion_gate
    jd = tmp_path / "job"
    _write(jd, _clean_pkg())
    (jd / "design" / "calc_package.json").write_text("{}")
    assert any("TWO package files" in p for p in _completion_gate(_WS(jd)))

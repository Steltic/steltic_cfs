"""October 2026 (capacity table and wind wording): the second tension column of the S400 B3.4 capacity-design table
carries the full expected-strength stack (sum of Omega_E Vn h / L over the stories, no Omega_0 cap) and says so; a
package whose line records carry the per-story min chain under that key prints it as the chain. A structure with no
wind exposure gets no 0.9D+1.0W wording (combinations note, hold-down / anchorage intros, wall wind note, QA row), and
cfg['wind_h_basis'] is appended to the velocity-pressure row.

    python -m pytest tests/test_cfs_report_text_2026_10_21.py -q
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


def _gyp(**kw):
    cfg = box(system="gypsum_wall", sds=0.25, sd1=0.10, s1=0.06, diaphragm_MDD_ADVE=3.0)
    cfg.update(kw)
    return cfg


def _pkg_with_vn():
    # Vn chosen so that Omega_E Vn governs at the top story and the Omega_0 level below it
    cfg = _gyp(selected_Vn_kip={"X:X2": {1: 40.0, 2: 40.0, 3: 1.0}})
    return cfg, CP.build_package("gyp", cfg, CE.run(cfg))


# ---------------- 1. the expected-strength stack is the full stack; T_cd stays the per-story chain ----------------

def test_expected_strength_stack_is_the_full_stack_and_T_cd_the_chain():
    cfg, pkg = _pkg_with_vn()
    ln = pkg["capacity_design"]["lines"]["X:X2"]
    OmE = ln["Omega_E"]
    full = OmE * (40.0 + 40.0 + 1.0) * 10.0 / 30.0                  # every story at Omega_E Vn, L = 30 ft
    assert abs(ln["T_OmegaE_Vn_stack_kip"] - round(full, 1)) < 0.051
    # the chain caps the two lower stories at the Omega_0 level, so T_cd is below both full stacks
    assert ln["T_cd_seed_kip"] < ln["T_OmegaE_Vn_stack_kip"]
    assert ln["T_cd_seed_kip"] <= ln["T_Om0_stack_kip"]
    hd = [h for h in pkg["holddowns"] if h["id"] == "hd-X-X2"][0]
    assert hd["T_cd_seed_kip"] == ln["T_cd_seed_kip"]
    assert "per-story chain" in ln["basis"]


# ---------------- 2. the capacity table labels the column by what it carries ----------------

def test_capacity_table_prints_the_full_stack_under_its_label():
    cfg, pkg = _pkg_with_vn()
    txt = _text(RPT._cfs_capacity_chapter(cfg, pkg))
    assert "T, ΩEVn stack (kip)" in txt and "T, Ω0 stack (kip)" in txt
    assert "per-story min chain" not in txt and "T at ΩEVn" not in txt
    assert "carry the full Ω0V and the full ΩEVn of every story" in txt
    assert not RPT._cfs_cd_chain_stored(pkg["capacity_design"]["lines"]["X:X2"])


def test_capacity_table_labels_a_stored_chain_as_the_chain():
    cfg, pkg = _pkg_with_vn()
    ln = pkg["capacity_design"]["lines"]["X:X2"]
    ln["T_OmegaE_Vn_stack_kip"] = ln["T_cd_seed_kip"]              # a record that carries the chain under the key
    assert RPT._cfs_cd_chain_stored(ln)
    txt = _text(RPT._cfs_capacity_chapter(cfg, pkg))
    assert "T, per-story min chain (kip)" in txt and "T, ΩEVn stack (kip)" not in txt
    assert "the chain column carries the per-story sum above, which is Tcd" in txt


def test_a_record_equal_to_T_cd_with_Omega_E_governing_everywhere_is_the_full_stack():
    ln = dict(T_OmegaE_Vn_stack_kip=6.0, T_cd_seed_kip=6.0, Omega_E=1.5, Vn_selected_kip={"1": 4.0},
              Ve_cap_by_story_kip={"1": 8.0})                   # 1.5 x 4.0 = 6.0 < 8.0: chain = full stack
    assert not RPT._cfs_cd_chain_stored(ln)
    ln["Ve_cap_by_story_kip"] = {"1": 5.0}                        # 6.0 > 5.0: the Omega_0 level governs
    assert RPT._cfs_cd_chain_stored(ln)


# ---------------- 3. no wind exposure: no 0.9D+1.0W wording ----------------

def test_no_wind_exposure_has_no_wind_case_wording(tmp_path):
    cfg, pkg = _pkg_with_vn()
    assert "wind" not in cfg
    assert pkg["combos_note"] == CP.COMBOS_NOTE_NO_WIND and "0.9D+1.0W case" in pkg["combos_note"]
    cap = _text(RPT._cfs_capacity_chapter(cfg, pkg))
    assert "Wind design of the lateral walls" not in cap
    hd = _text(RPT._cfs_holddown_schedule(pkg, cfg))
    assert "wind = 0.9D+1.0W" not in hd and "no wind case (no wind exposure)" in hd
    an = _text(RPT._cfs_anchorage_schedule(pkg, cfg))
    assert "and wind unit shears" not in an and "ρ-ELF unit shears (no wind exposure)" in an
    qa = _text(RPT._cfs_qa_section("gyp", str(tmp_path), cfg, {}, pkg))
    assert "0.9D+1.0W net-uplift path" not in qa and "no wind net-uplift case (no wind exposure)" in qa


def test_wind_exposure_keeps_the_wind_case_wording():
    cfg = _gyp(wind=dict(V=115.0, exposure="C"))
    pkg = CP.build_package("gypw", cfg, CE.run(cfg))
    assert pkg["combos_note"] == CP.COMBOS_NOTE
    assert "wind = 0.9D+1.0W" in _text(RPT._cfs_holddown_schedule(pkg, cfg))
    assert "and wind unit shears" in _text(RPT._cfs_anchorage_schedule(pkg, cfg))


# ---------------- 4. wind_h_basis on the velocity-pressure row ----------------

def test_wind_h_basis_is_appended_to_the_velocity_pressure_row():
    cfg = _gyp(wind=dict(V=115.0, exposure="C"))
    pkg = CP.build_package("gypw", cfg, CE.run(cfg))
    base = RPT._cfs_wind_rows(cfg, pkg)
    q = [r for r in base if r[0].startswith("Velocity pressure")]
    assert q, "the package carries qh"
    note = "the bar wing has a mean roof height of 53 ft, qh = 22.6 psf"
    rows = RPT._cfs_wind_rows(dict(cfg, wind_h_basis=note), pkg)
    q2 = [r for r in rows if r[0].startswith("Velocity pressure")][0]
    assert q2[1].endswith("; " + note) and q2[1].startswith(q[0][1])

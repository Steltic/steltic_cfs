"""Native physical model builders (steel_engine/cfs_physical*.py) on completed-job fixtures.

Each fixture is the resolved cfg and the filled calculation package (with its physical_design block) of a
finished job: a two-story strap-braced wall building, a single-bay special bolted moment frame shop and a
four-bay special bolted moment frame high bay with beam splices and an interior bracing line.
"""
import json
import os
import sys
import zipfile
from collections import Counter
from pathlib import Path

import pytest

ENGINE = os.path.join(os.path.dirname(__file__), "..", "steel_engine")
sys.path.insert(0, ENGINE)

import cfs_frame          # noqa: E402
import cfs_physical       # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "cfs_physical_jobs.zip"


def job(tmp_path, name, edit_cfg=None, edit_pkg=None):
    root = tmp_path / name
    with zipfile.ZipFile(FIXTURE) as z:
        for n in z.namelist():
            if n.startswith(name + "/"):
                z.extract(n, tmp_path)
    cfg = json.loads((root / "cfg.json").read_text(encoding="utf-8"))
    if edit_cfg:
        edit_cfg(cfg)
        (root / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    if edit_pkg:
        p = root / "design" / "calc_package_cfs.json"
        pkg = json.loads(p.read_text(encoding="utf-8"))
        edit_pkg(pkg)
        p.write_text(json.dumps(pkg), encoding="utf-8")
    (root / "cfg.py").write_text("import json\ncfg = json.load(open('cfg.json', encoding='utf-8'))\n", encoding="utf-8")
    return root


def built(tmp_path, name, **kw):
    root = job(tmp_path, name, **kw)
    summary = cfs_physical.build(root)
    return summary, json.loads((root / "physical_inputs.json").read_text(encoding="utf-8"))["declarations"]


def closed(d):
    """Every member end sits in a joint, every joint has a connection or a disposition, and every binding names a declared entity."""
    members = {m["id"] for m in d["members"]}
    known = members | {p["id"] for p in d.get("panels", [])} | {i["id"] for i in d.get("interfaces", [])}
    ends = set()
    for j in d["joints"]:
        for e in j.get("member_ends", []):
            ends.add((e["member_id"], e["end"]))
    missing = [(m, e) for m in members for e in ("start", "end") if (m, e) not in ends]
    joints = {j["id"] for j in d["joints"]}
    covered = {c["joint_id"] for c in d["connections"]} | {x["joint_id"] for x in d.get("joint_dispositions", [])}
    templates = {a["id"] for a in d["assemblies"]}
    bad = []
    for c in d["connections"]:
        inst = c.get("assembly_instance")
        if inst:
            if inst["template_id"] not in templates:
                bad.append(c["id"])
            bad += [c["id"] for v in inst.get("bindings", {}).values() if v not in known]
    return missing, sorted(joints - covered), bad


def test_wall_building(tmp_path):
    summary, d = built(tmp_path, "wall_strap")
    assert summary["members"] == len(d["members"]) > 500 and summary["panels"] == 2
    kinds = Counter(m["kind"] for m in d["members"])
    assert kinds["strap"] == 2 * 8 * 2            # one X pair in each of 8 braced bays per story, 2 stories
    assert kinds["tie_rod"] == 32 and all(k in kinds for k in ("stud", "top_track", "bottom_track", "joist"))
    assert closed(d) == ([], [], [])
    assert d["reference_system"]["levels"][-1]["name"] == "Lid"
    # an interior lid has no wind uplift connection: nothing may point at a record the package does not hold
    records = {"%s/%s" % (t, r["id"]) for t in ("connections", "framing", "holddowns", "anchorage", "studs", "wall_lines", "collectors", "diaphragms")
               for r in json.loads((tmp_path / "wall_strap" / "design" / "calc_package_cfs.json").read_text(encoding="utf-8")).get(t, [])}
    assert "connections/conn-roof-uplift" not in records
    assert not [a["id"] for a in d["assemblies"] if "connections/conn-roof-uplift" in a.get("source_record_ids", [])]


def test_single_bay_bolted_moment_frames(tmp_path):
    summary, d = built(tmp_path, "sbmf_single")
    profiles = {p["id"]: p for p in d["profiles"]}
    assert profiles["HSS12X12X1/2"]["family"] == "rhs" and profiles["HSS12X12X1/2"]["dimensions"] == {"h": 12.0, "b": 12.0, "t": 0.465}
    cols = [m for m in d["members"] if m["kind"] == "column"]
    beams = [m for m in d["members"] if m["kind"] == "rafter"]
    assert len(cols) == 58 and len(beams) == 29 and {m["profile_id"] for m in cols} == {"HSS12X12X1/2"}
    beam = profiles[beams[0]["profile_id"]]
    assert beam["family"] == "built_up" and len(beam["components"]) == 2
    assert abs(beam["components"][1]["offset"][0] - beam["components"][0]["offset"][0]) == pytest.approx(12.0 + 0.1242)       # webs on the two column faces
    families = Counter(c.get("family") for c in d["connections"])
    assert families["bolted moment connection"] == 58 and families["column base"] == 58 and families["beam splice"] == 0
    joint = next(a for a in d["assemblies"] if a["id"].startswith("SBMF-JOINT"))
    bolts = [p for p in joint["parts"] if p["kind"] == "bolt"]
    assert len(bolts) == 8 and {p["parameters"]["diameter_in"] for p in bolts} == {1.0}
    base = next(a for a in d["assemblies"] if a["id"].startswith("BASE-PINNED-HSS"))
    assert Counter(p["kind"] for p in base["parts"]) == {"base_plate": 1, "anchor_rod": 4, "fillet_weld": 4}
    assert closed(d) == ([], [], [])


def test_multi_bay_bolted_moment_frames_with_splices(tmp_path):
    summary, d = built(tmp_path, "sbmf_multibay")
    cols = [m for m in d["members"] if m["kind"] == "column"]
    beams = [m for m in d["members"] if m["kind"] == "rafter"]
    assert len(cols) == 9 * 5 and len(beams) == 9 * 3          # five columns and three shipping pieces per frame
    lengths = sorted({round(abs(m["axis"][1][0] - m["axis"][0][0])) for m in beams})
    assert lengths == [240, 360]
    families = Counter(c.get("family") for c in d["connections"])
    assert families["bolted moment connection"] == 45 and families["beam splice"] == 18
    ids = {a["id"].rsplit("-", 1)[0] for a in d["assemblies"]}
    assert {"SBMF-JOINT", "SBMF-JOINT-I", "SBMF-JOINT-R", "BEAM-SPLICE", "BASE-PINNED-HSS"} <= ids
    interior = next(a for a in d["assemblies"] if a["id"].startswith("SBMF-JOINT-I"))
    end = next(a for a in d["assemblies"] if a["id"].startswith("SBMF-JOINT-") and not a["id"].startswith("SBMF-JOINT-I") and not a["id"].startswith("SBMF-JOINT-R"))
    assert interior["source_record_ids"] != end["source_record_ids"]       # interior joints carry their own design record
    straps = Counter((m.get("plane"), m.get("host_line")) for m in d["members"] if m["kind"] == "strap")
    assert straps[("wall", 5)] == 4 * 2 and straps[("wall", 2)] == 5 * 2 and not straps[("wall", 1)]        # nothing on the open storefront line
    assert closed(d) == ([], [], [])


def test_bolted_frame_beam_must_be_straight(tmp_path):
    def kink(cfg):
        cfg["spans"][1]["apex_ft"] = 26.0
    root = job(tmp_path, "sbmf_multibay", edit_cfg=kink)
    with pytest.raises(cfs_physical.PhysicalDesignError, match="not straight"):
        cfs_physical.build(root, save=False)


def test_splice_must_stand_at_an_analysis_node(tmp_path):
    def move(pkg):
        pkg["physical_design"]["frame"]["splice"]["at_ft"] = [33.0, 50.0]
    root = job(tmp_path, "sbmf_multibay", edit_pkg=move)
    with pytest.raises(cfs_physical.PhysicalDesignError, match="splice"):
        cfs_physical.build(root, save=False)


def test_package_without_physical_design_is_refused(tmp_path):
    root = job(tmp_path, "sbmf_single", edit_pkg=lambda pkg: pkg.pop("physical_design"))
    with pytest.raises(cfs_physical.PhysicalDesignError, match="physical_design"):
        cfs_physical.build(root, save=False)


def test_sbmf_screens_accept_multi_bay_frames(tmp_path):
    cfg = json.loads((job(tmp_path, "sbmf_multibay") / "cfg.json").read_text(encoding="utf-8"))
    res = cfs_frame.run(cfg)
    screens = res["sbmf"]["screens"]
    assert all(s["ok"] for s in screens), [s for s in screens if not s["ok"]]
    assert any("multi-bay" in s["check"] and s["clause"] == "S400 E4.4.1(b)" for s in screens)
    assert next(s for s in screens if s["check"].startswith("one story"))["ok"] is True
    cfg["base"] = "fixed"
    res = cfs_frame.run(cfg)
    assert next(s for s in res["sbmf"]["screens"] if s["check"] == "pin-based columns")["ok"] is False

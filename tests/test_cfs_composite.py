"""Composite CFS projects (steel_engine/cfs_composite.py): binding finished part jobs into one project report and manifest."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "steel_engine"))

import cfs_composite as CC          # noqa: E402

HEAD = "<meta charset='utf-8'><title>%s &mdash; CFS design report</title><style>body{font-family:Arial}</style>"


def part(jobs, name, kind, body, extra=None, dc=0.5):
    root = jobs / name
    (root / "design").mkdir(parents=True)
    (root / "report.html").write_text("<!doctype html><html><head>%s</head><body><h1>%s</h1>%s<p><a href='viewer_3d.html'>View model</a></p></body></html>" % (HEAD % name, name, body), encoding="utf-8")
    (root / "viewer_3d.html").write_text("<html><body>viewer of %s</body></html>" % name, encoding="utf-8")
    pkg = dict(kind=kind, system="strap_braced" if kind == "wall" else "sbmf", members=[dict(id="m1", DC=dc, checks=[dict(DC=dc - 0.1)])], physical_design=dict(big=[dict(DC=9.9)]))
    pkg.update(extra or {})
    (root / "design" / "calc_package_cfs.json").write_text(json.dumps(pkg), encoding="utf-8")
    (root / "design" / "bim_package.json").write_text(json.dumps(dict(module="CFS", building_id=name)), encoding="utf-8")
    return root


def project(tmp_path, stated=12.0, **spec_edit):
    jobs = tmp_path / "jobs"
    part(jobs, "P_A", "portal", "<p>frame text ALPHA</p>", dc=0.91)
    record = dict(basis="ASCE 7-22 12.12.2", rows=[dict(level="floor", separation_in=2.5), dict(level="roof", separation_in=6.25)])
    part(jobs, "P_B", "wall", "<p>wall text BETA</p>", extra=dict(joint_record=record), dc=0.72)
    root = jobs / "P"
    root.mkdir()
    spec = dict(schema="steltic.cfs_composite", version=1, name="P", title="Two structures",
                parts=[dict(id="A", job="P_A", title="Frames", origin_in=[0.0, 0.0, 0.0]), dict(id="B", job="P_B", title="Mezzanine", origin_in=[21.0, 1803.0, 0.0])],
                interface=dict(kind="seismic_joint", statement="Joint all round.", record=dict(part="B", key="joint_record"),
                               clear_gaps=[dict(label="walls to columns", a=dict(part="A", kinds=["column"]), b=dict(part="B"), axis="x", stated_in=stated, required_key="separation_in")]))
    spec.update(spec_edit)
    (root / "compose.json").write_text(json.dumps(spec), encoding="utf-8")
    return root, jobs


def test_build_binds_parts_into_one_report_and_manifest(tmp_path):
    root, jobs = project(tmp_path)
    m = CC.build(root, jobs)
    text = (root / "report.html").read_text(encoding="utf-8")
    assert "frame text ALPHA" in text and "wall text BETA" in text and text.index("ALPHA") < text.index("BETA")
    assert "PART A &mdash; Frames" in text and "PART B &mdash; Mezzanine" in text
    assert "href='viewer_3d_A.html'" in text and "href='viewer_3d_B.html'" in text and text.count("<title>") == 1 and "<title>P &mdash;" in text
    assert "x = 21 in., y = 1803 in., z = 0 in." in text and "<td>6.25</td><td>12.00</td>" in text and "ASCE 7-22 12.12.2" in text
    assert (root / "viewer_3d_A.html").read_text(encoding="utf-8") == (jobs / "P_A" / "viewer_3d.html").read_text(encoding="utf-8")
    assert "viewer_3d_B.html" in (root / "viewer_3d.html").read_text(encoding="utf-8")
    saved = json.loads((root / "design" / "composite.json").read_text(encoding="utf-8"))
    assert saved == m and m["schema"] == "steltic.cfs_composite" and m["report_sha256"] == CC.sha(root / "report.html")
    a, b = m["parts"]
    assert (a["worst_DC"], a["worst_DC_row"], a["kind"]) == (0.91, "m1", "portal") and b["worst_DC"] == 0.72       # the physical_design block is not a design table
    assert a["report_sha256"] == CC.sha(jobs / "P_A" / "report.html") and b["calc_sha256"] == CC.sha(jobs / "P_B" / "design" / "calc_package_cfs.json")
    assert m["interface"]["clear_gaps"][0]["required_in"] == 6.25 and m["interface_record"]["rows"][1]["separation_in"] == 6.25


def test_part_qa_annex_travels_under_the_part_letter(tmp_path):
    root, jobs = project(tmp_path)
    rep = jobs / "P_A" / "report.html"
    rep.write_text(rep.read_text(encoding="utf-8").replace("</body>", "<p><a href='qa_annex.html'>qa_annex.html</a></p></body>"), encoding="utf-8")
    (jobs / "P_A" / "qa_annex.html").write_text("<html><body>annex A</body></html>", encoding="utf-8")
    CC.build(root, jobs)
    text = (root / "report.html").read_text(encoding="utf-8")
    assert "href='qa_annex_A.html'>qa_annex.html</a>" in text and "href='qa_annex.html'" not in text
    assert "annex A" in (root / "qa_annex_A.html").read_text(encoding="utf-8") and not (root / "qa_annex_B.html").exists()


def test_part_reports_are_not_altered(tmp_path):
    root, jobs = project(tmp_path)
    before = {n: (jobs / n / "report.html").read_bytes() for n in ("P_A", "P_B")}
    CC.build(root, jobs)
    assert before == {n: (jobs / n / "report.html").read_bytes() for n in ("P_A", "P_B")}


def test_gap_smaller_than_the_record_requires_is_refused(tmp_path):
    root, jobs = project(tmp_path, stated=6.0)
    with pytest.raises(CC.CompositeError, match="less than the 6.25 in. required"):
        CC.build(root, jobs)


def test_unfinished_part_is_refused(tmp_path):
    root, jobs = project(tmp_path)
    (jobs / "P_B" / "design" / "bim_package.json").unlink()
    with pytest.raises(CC.CompositeError, match="bim_package.json is missing"):
        CC.build(root, jobs)


def test_part_with_another_buildings_model_is_refused(tmp_path):
    root, jobs = project(tmp_path)
    (jobs / "P_B" / "design" / "bim_package.json").write_text(json.dumps(dict(module="CFS", building_id="OTHER")), encoding="utf-8")
    with pytest.raises(CC.CompositeError, match="belongs to 'OTHER'"):
        CC.build(root, jobs)


@pytest.mark.parametrize("edit, message", [
    (dict(schema="other"), "schema"),
    (dict(parts=[dict(id="A", job="P_A", title="Frames", origin_in=[0, 0, 0])]), "at least two parts"),
    (dict(parts=[dict(id="A", job="P_A", title="Frames", origin_in=[0, 0, 0]), dict(id="A", job="P_B", title="Mezzanine", origin_in=[0, 0, 0])]), "distinct"),
    (dict(parts=[dict(id="A", job="P_A", title="Frames", origin_in=[0, 0, 0]), dict(id="B", job="P_B", title="Mezzanine", origin_in=[0, 0])]), "origin_in"),
])
def test_declaration_errors(tmp_path, edit, message):
    root, jobs = project(tmp_path, **edit)
    with pytest.raises(CC.CompositeError, match=message):
        CC.build(root, jobs)


def test_interface_record_must_exist_in_the_named_part(tmp_path):
    root, jobs = project(tmp_path)
    spec = json.loads((root / "compose.json").read_text(encoding="utf-8"))
    spec["interface"]["record"]["key"] = "no_such_record"
    (root / "compose.json").write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(CC.CompositeError, match="not in the calculation package of part B"):
        CC.build(root, jobs)

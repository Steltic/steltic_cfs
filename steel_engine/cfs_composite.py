"""Composite CFS design packages: one project made of structurally separate parts.

Some buildings are two structures standing side by side (or one inside the other) with a
seismic joint between them -- a moment-frame shop with a free-standing braced mezzanine, a
high bay with an office wing.  Each part is a complete Steltic CFS job of its own kind
(``wall`` or ``portal``) with its own calculation package, report and physical model.  This
module binds the finished parts into ONE project deliverable without re-designing anything:

    <root>/compose.json           the engineer's declaration (parts, set-out, interface)
    <root>/report.html            cover (parts, set-out, interface record) + every part report
    <root>/viewer_3d.html         index of the part viewers (viewer_3d_<id>.html; qa_annex_<id>.html where a part has one)
    <root>/design/composite.json  manifest: part hashes, set-out, interface (read by openBIM)

``compose.json``::

    {"schema": "steltic.cfs_composite", "version": 1, "name": "CFS_Ex18", "title": "...",
     "parts": [{"id": "A", "job": "CFS_Ex18_A", "title": "...", "origin_in": [0, 0, 0]},
               {"id": "B", "job": "CFS_Ex18_B", "title": "...", "origin_in": [21, 1803, 0]}],
     "interface": {"kind": "seismic_joint", "statement": "...",
                   "record": {"part": "B", "key": "seismic_joint_at_shop_frames"},
                   "clear_gaps": [{"label": "...", "a": {"part": "A", "kinds": ["column"]},
                                   "b": {"part": "B"}, "axis": "x", "stated_in": 12.0,
                                   "required_key": "separation_across_shop_in"}]}}

``origin_in`` is the plan position of the part's own origin in the project system (inches);
parts are never rotated.  The interface record is a supplementary record of one of the part
packages: the cover repeats it verbatim so the project report, the part report and the
openBIM composite check all read the same numbers.  Nothing here changes a part.
"""
import hashlib
import html
import json
import re
import shutil
from pathlib import Path

SCHEMA = "steltic.cfs_composite"


class CompositeError(ValueError):
    pass


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_spec(root):
    spec = json.loads((Path(root) / "compose.json").read_text(encoding="utf-8"))
    if spec.get("schema") != SCHEMA or spec.get("version") != 1:
        raise CompositeError("compose.json must declare schema %s version 1" % SCHEMA)
    ids = [p.get("id") for p in spec.get("parts") or []]
    if len(ids) < 2 or len(set(ids)) != len(ids) or not all(isinstance(i, str) and re.fullmatch(r"[A-Z]", i) for i in ids):
        raise CompositeError("compose.json needs at least two parts with distinct single-letter ids")
    for p in spec["parts"]:
        o = p.get("origin_in")
        if not (isinstance(o, list) and len(o) == 3 and all(isinstance(v, (int, float)) for v in o)):
            raise CompositeError("part %s needs origin_in = [x, y, z] in inches" % p["id"])
        if not p.get("job") or not p.get("title"):
            raise CompositeError("part %s needs 'job' and 'title'" % p["id"])
    return spec


def worst_dc(pkg):
    """Largest D/C of any row of any table of a calculation package, with the row id."""
    best = (None, None)

    def walk(o):
        nonlocal best
        if isinstance(o, dict):
            dc = o.get("DC")
            if isinstance(dc, (int, float)) and not isinstance(dc, bool) and (best[0] is None or dc > best[0]):
                best = (float(dc), o.get("id"))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for k, v in pkg.items():
        if k not in ("physical_design", "bim"):
            walk(v)
    return best


def esc(v):
    return html.escape("%g" % v if isinstance(v, float) else str(v))


def render(value):
    """A supplementary record as HTML: text as paragraphs, lists of dicts as tables."""
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            label = esc(str(k).replace("_", " "))
            if isinstance(v, (dict, list)):
                out.append("<p><b>%s</b></p>%s" % (label, render(v)))
            else:
                out.append("<p><b>%s:</b> %s</p>" % (label, esc(v)))
        return "".join(out)
    if isinstance(value, list) and value and all(isinstance(r, dict) for r in value):
        cols = []
        for r in value:
            for k in r:
                if k not in cols:
                    cols.append(k)
        head = "".join("<th>%s</th>" % esc(c.replace("_", " ")) for c in cols)
        body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % esc(r.get(c, "")) for c in cols) for r in value)
        return "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (head, body)
    if isinstance(value, list):
        return "<ul>%s</ul>" % "".join("<li>%s</li>" % esc(v) for v in value)
    return "<p>%s</p>" % esc(value)


def split_report(text, where):
    m = re.search(r"<head>(.*?)</head>\s*<body>(.*)</body>\s*</html>\s*$", text, re.S)
    if not m:
        raise CompositeError("%s is not a Steltic report (head/body not found)" % where)
    return m.group(1), m.group(2)


def build(root, jobs_dir=None):
    """Bind the finished part jobs under ``root``; returns the manifest written to design/composite.json."""
    root = Path(root)
    jobs_dir = Path(jobs_dir) if jobs_dir else root.parent
    spec = load_spec(root)
    parts, bodies, head = [], [], None
    for p in spec["parts"]:
        pr = jobs_dir / p["job"]
        need = dict(report=pr / "report.html", calc=pr / "design" / "calc_package_cfs.json", bim=pr / "design" / "bim_package.json", viewer=pr / "viewer_3d.html")
        for k, f in need.items():
            if not f.is_file():
                raise CompositeError("part %s (%s): %s is missing; finish the part job first" % (p["id"], p["job"], f.name))
        pkg = json.loads(need["calc"].read_text(encoding="utf-8"))
        bim = json.loads(need["bim"].read_text(encoding="utf-8-sig"))
        if bim.get("building_id") != p["job"]:
            raise CompositeError("part %s: the physical package belongs to %r" % (p["id"], bim.get("building_id")))
        h, b = split_report(need["report"].read_text(encoding="utf-8"), "%s/report.html" % p["job"])
        head = head or h
        b = b.replace("href='viewer_3d.html'", "href='viewer_3d_%s.html'" % p["id"])
        if (pr / "qa_annex.html").is_file():                 # the part's QA annex travels with the project report under the part letter
            b = b.replace("href='qa_annex.html'", "href='qa_annex_%s.html'" % p["id"])
            shutil.copyfile(pr / "qa_annex.html", root / ("qa_annex_%s.html" % p["id"]))
        bodies.append("<hr class='part'><p class='part'>PART %s &mdash; %s</p>%s" % (p["id"], esc(p["title"]), b))
        shutil.copyfile(need["viewer"], root / ("viewer_3d_%s.html" % p["id"]))
        dc, rid = worst_dc(pkg)
        seis = pkg.get("seismic") if isinstance(pkg.get("seismic"), dict) else {}
        parts.append(dict(id=p["id"], job=p["job"], title=p["title"], origin_in=[float(v) for v in p["origin_in"]], kind=pkg.get("kind"), system=pkg.get("system"),
                          worst_DC=dc, worst_DC_row=rid, SDC=seis.get("SDC") or (pkg.get("elf") or {}).get("SDC"),
                          report_sha256=sha(need["report"]), calc_sha256=sha(need["calc"]), bim_sha256=sha(need["bim"])))
    iface = dict(spec.get("interface") or {})
    rec = iface.get("record")
    record = None
    if rec:
        src = next((p for p in spec["parts"] if p["id"] == rec.get("part")), None)
        if src is None:
            raise CompositeError("interface record names part %r, which is not declared" % rec.get("part"))
        record = json.loads((jobs_dir / src["job"] / "design" / "calc_package_cfs.json").read_text(encoding="utf-8")).get(rec.get("key"))
        if not isinstance(record, dict):
            raise CompositeError("interface record %r is not in the calculation package of part %s" % (rec.get("key"), rec.get("part")))
        for g in iface.get("clear_gaps") or []:
            vals = [float(r[g["required_key"]]) for r in record.get("rows") or [] if isinstance(r.get(g["required_key"]), (int, float))]
            if not vals:
                raise CompositeError("interface gap %r: the record has no rows with %r" % (g.get("label"), g.get("required_key")))
            g["required_in"] = max(vals)
            if float(g["stated_in"]) + 1e-9 < g["required_in"]:
                raise CompositeError("interface gap %r: %.2f in. provided is less than the %.2f in. required by the record" % (g.get("label"), g["stated_in"], g["required_in"]))
    rows = "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                   % (p["id"], esc(p["title"]), esc(p["job"]), esc(p["kind"]), "x = %g in., y = %g in., z = %g in." % tuple(p["origin_in"]),
                      "%.3f (%s)" % (p["worst_DC"], esc(p["worst_DC_row"])) if p["worst_DC"] is not None else "") for p in parts)
    gaps = "".join("<tr><td>%s</td><td>%s</td><td>%.2f</td><td>%.2f</td></tr>" % (esc(g["label"]), esc(g["axis"]), g["required_in"], float(g["stated_in"])) for g in iface.get("clear_gaps") or [])
    cover = ["<h1>%s &mdash; cold-formed steel structural design</h1>" % esc(spec["name"]),
             "<p><b>%s</b></p>" % esc(spec.get("title") or ""),
             "<p><a href='viewer_3d.html' target='_blank' rel='noopener' style=\"display:inline-block;background:#ff8a3d;color:#fff;font-weight:600;padding:9px 22px;border-radius:8px;text-decoration:none\">View model</a></p>",
             "<h2>Project composition</h2>",
             "<p>This project is %d structurally separate structures. Each is a complete design of its own, reported in full below as a part; the parts share a site and a set-out but no structural member, connection or load path. "
             "The drawing set and the IFC model contain every part at the set-out given here.</p>" % len(parts),
             "<table><thead><tr><th>Part</th><th>Structure</th><th>Design job</th><th>Design path</th><th>Origin of the part in the project system</th><th>Largest demand/capacity ratio (row)</th></tr></thead><tbody>%s</tbody></table>" % rows]
    if iface:
        cover.append("<h2>Interface between the parts</h2>")
        if iface.get("statement"):
            cover.append("<p>%s</p>" % esc(iface["statement"]))
        if gaps:
            cover.append("<table><thead><tr><th>Clear gap</th><th>Project axis</th><th>Required separation, in.</th><th>Gap provided, in.</th></tr></thead><tbody>%s</tbody></table>" % gaps)
        if record is not None:
            cover.append("<p class='note'>Interface record of part %s (%s), repeated from that part's report:</p>%s" % (esc(rec["part"]), esc(rec["key"].replace("_", " ")), render(record)))
    style = "<style>hr.part{margin:56px 0 0;border:0;border-top:4px solid #2c5aa0}p.part{font-size:1.25em;font-weight:700;color:#2c5aa0;margin:6px 0 0}</style>"
    head = re.sub(r"<title>.*?</title>", "<title>%s &mdash; CFS design report</title>" % esc(spec["name"]), head, count=1, flags=re.S)
    (root / "report.html").write_text("<!doctype html><html><head>%s%s</head><body>%s%s</body></html>" % (head, style, "".join(cover), "".join(bodies)), encoding="utf-8")
    links = "".join("<li><a href='viewer_3d_%s.html'>Part %s &mdash; %s</a></li>" % (p["id"], p["id"], esc(p["title"])) for p in parts)
    frames = "".join("<h2>Part %s &mdash; %s</h2><iframe src='viewer_3d_%s.html' style='width:100%%;height:82vh;border:1px solid #ccc'></iframe>" % (p["id"], esc(p["title"]), p["id"]) for p in parts)
    (root / "viewer_3d.html").write_text("<!doctype html><html><head><meta charset='utf-8'><title>%s &mdash; 3D models</title><style>body{font-family:Segoe UI,Arial,sans-serif;margin:16px;color:#222}h2{color:#2c5aa0}</style></head>"
                                         "<body><h1>%s &mdash; 3D models of the parts</h1><ul>%s</ul>%s</body></html>" % (esc(spec["name"]), esc(spec["name"]), links, frames), encoding="utf-8")
    manifest = dict(schema=SCHEMA, version=1, name=spec["name"], title=spec.get("title"), parts=parts, interface=iface, interface_record=record, report_sha256=sha(root / "report.html"))
    (root / "design").mkdir(exist_ok=True)
    (root / "design" / "composite.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    import sys
    m = build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print("composite %s: %d parts %s; report %s" % (m["name"], len(m["parts"]), [(p["id"], p["job"], p["worst_DC"]) for p in m["parts"]], m["report_sha256"][:12]))

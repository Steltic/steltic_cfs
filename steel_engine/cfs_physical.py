"""Native physical model of a completed CFS design, for drawings and IFC (Steltic openBIM).

``build(job)`` turns the structured selections of a COMPLETED, consistency-checked job into the
full physical declaration set (``physical_inputs.json``): every stud, chord ply, track, joist,
header, tie-down rod, sheathing panel and opening as a located occurrence, with the selected
fasteners and anchors as located solids in versioned assembly templates, and every occurrence
linked to the calculation record that selected it.

It is a recorder, not a designer.  It reads ``calc_package_cfs.json#/physical_design`` (the
engineer's structured selections, schema below) and the geometry in ``cfg.py``; it never sizes
a member, chooses a spacing, calculates a capacity or reads a selection out of prose.  A missing
selection raises ``PhysicalDesignError`` naming the field -- nothing is defaulted silently.

    physical_design = {
      "schema": "steltic.cfs_physical_design", "version": 1, "kind": "wall",
      "floor_zone_in": 8.75,           # floor datum down to the wall top-track web (joist + deck)
      "wall_depth_in": 6.0,
      "materials": {id: {"specification": str, "record": "materials/@i" (optional), ...}},
      "fasteners": {"frame_screw": {"size": "No. 10", "d_in": 0.19, "material": id, "record": "connections/<id>"}},
      "wall_lines": {"X:S": {"direction": "X", "pos_ft": 0, "start_ft": 0, "end_ft": 160,
          "stories": {"1": {
              "stud": {"section", "material", "spacing_in", "record", "n": 1 | 2 (back-to-back)},
              "segment_stud": {"section", "material"},                 # framing inside sheathed segments
              "track": {"section", "material"}, "top_track_record": "collectors/<id>" (optional),
              "segments_ft": [[a, b], ...],
              "through_segments_ft": [[a, b], ...],                 # interrupted wall: chord packs and rods of the segments above pass through (no sheathing)
              "sheathing": {"thickness_in", "material", "faces", "edge_in", "field_in", "screw", "screw_d_in",
                            "screw_material", "record"},                # sheathed segments (wood panel, steel sheet, gypsum), or
              "strap": {"width_in", "t_in", "faces", "material", "record", "gusset": {"t_in", "material"}, "screw", "screw_d_in",
                        "screw_material", "n_strap", "rows", "per_row", "n_gusset", "connection_record"},   # strap-braced bays
              "chord": {"section", "material", "n", "record", "stitch_record"},
              "rod": {"d_in", "material", "record"},
              "anchor": {"d_in", "spacing_in", "material", "embed_in", "record"},     # story 1
              "uplift_tie": {"width_in", "t_in", "material", "lap_in", "n_screws", "screw", "screw_d_in", "screw_material", "record"},  # optional: strap on every stud
              "floor_shear": {"rows", "spacing_in", "screw_d_in", "screw_material", "record"}}}}},   # stories >= 2
      "openings": [{"id", "line": "X:S", "story": 1, "start_ft", "width_ft", "sill_ft", "head_ft",
                    "header": {"section", "material", "n", "record"}, "jamb": {"section", "material", "n"},
                    "clip_record": "connections/<id>"}],
      "floors": [{"level": 1, "kind": "floor" | "roof",
                  "sheathing": {"thickness_in", "material", "records": [...]},
                  "zones": [{"id", "x_ft": [x0, x1], "y_ft": [y0, y1], "span": "X" | "Y",
                             "joist": {"section", "material", "spacing_in", "record"}      # C-joist, or an open-web truss as
                                      | {"envelope": {"width_in", "depth_in"}, "designation", "material", "spacing_in", "record"}
                                      | {"section" (bottom chord), "top_section", "rise_ft", "truss_supplier", ...}      # gable truss: chords located, webs delegated
                             "supports": ["X:S", "X:M1"], "bearing_record": "connections/<id>",
                             "uplift_record": "connections/<id>" (optional)}]}],
      "foundation": {"responsible_party": str, "detail_ref": {"id", "revision"}},
      "coverage": {"primary_members": str, "secondary_members": str, "openings": str, "connections": str}
    }

Coordinates are inches, Z up; X-direction wall lines run along +X at y = pos, Y-direction lines
along +Y at x = pos.  The model is design-coordination geometry: member worklines, profile
envelopes and fastener locations as selected -- not shop cut lengths.
"""
import copy
import json
import math
import runpy
from pathlib import Path

try:
    from . import cfs_sections, physical_inputs, bim_assemblies
except ImportError:                                   # engine modules are also imported flat
    import cfs_sections, physical_inputs, bim_assemblies

SCHEMA = "steltic.cfs_physical_design"
PACKAGE = "calc_package_cfs.json"
Z = [0.0, 0.0, 1.0]


class PhysicalDesignError(ValueError):
    pass


def need(d, key, where):
    if not isinstance(d, dict) or d.get(key) is None:
        raise PhysicalDesignError("physical_design: %s needs '%s'" % (where, key))
    return d[key]


def add(a, b, s=1.0):
    return [a[i] + s * b[i] for i in range(3)]


def r6(p):
    return [round(float(v), 6) for v in p]


def neg(v):
    return [-x for x in v]


def r12(v):
    """Unit axes keep 12 decimals: 6 would leave an inclined axis off unit length by more than the frame tolerance."""
    return [round(float(x), 12) for x in v]


def cyl(pid, kind, material, origin, axis, length, radius, **parameters):
    geometry = dict(type="cylinder", origin=r6(origin), axis=r12(axis), length=float(length), radius=float(radius))
    if radius <= 0.15:
        geometry["segments"] = 6            # screws are drawn as hexagonal prisms: tens of thousands of them per building
    return dict(id=pid, kind=kind, material_id=material, parameters=parameters, geometry=geometry)


def box(pid, kind, material, origin, size, x_axis=(1.0, 0.0, 0.0), y_axis=(0.0, 1.0, 0.0), **parameters):
    return dict(id=pid, kind=kind, material_id=material, parameters=parameters,
                geometry=dict(type="box", origin=r6(origin), size=[float(v) for v in size], x_axis=r12(x_axis), y_axis=r12(y_axis)))


class Recorder(object):
    """Collects declarations and writes them through physical_inputs.PhysicalModel."""

    def __init__(self, model):
        self.m = model
        self.decl = model.value["declarations"]
        for k in ("joint_dispositions", "record_dispositions", "assemblies", "candidate_panel_replacements"):
            self.decl.setdefault(k, [])
        self.profiles, self.materials, self.templates = set(), set(), {}
        self.linked = set()

    def link(self, refs):
        refs = [r for r in (refs or []) if r]
        self.linked.update(refs)
        return refs

    def material(self, mid, spec):
        if mid in self.materials:
            return mid
        row = dict(spec)
        rec = row.pop("record", None)
        self.m.material(mid, need(row, "specification", "material " + mid), **{k: v for k, v in row.items() if k != "specification"},
                        design_record_ids=self.link([rec]))
        self.materials.add(mid)
        return mid

    def channel(self, name):
        if name in self.profiles:
            return name
        p = cfs_sections.parse_designator(name)
        t = p["t"]
        track = p.get("style") == "T"
        dims = dict(d=p["depth"] + (2.0 * t if track else 0.0), b=p["flange"], lip=p["lip"], t=t,
                    r=cfs_sections.RADIUS_BY_MIL[p["mil"]])
        self.m.profile(name, "channel", dims, designation=name, lip_inside=True,
                       source=dict(kind="engine_section_catalog", name=name,
                                   basis="SFIA designation; track depth taken as the stud depth plus two track thicknesses so studs seat inside" if track
                                   else "SFIA designation, design thickness and inside bend radius"))
        self.profiles.add(name)
        return name

    def flat(self, width, t):
        name = "STRAP-%gx%g" % (width, round(t * 1000))
        if name not in self.profiles:
            self.m.profile(name, "flat", dict(width=float(width), thickness=float(t)), designation="%g in. x %.4f in. flat strap" % (width, t))
            self.profiles.add(name)
        return name

    def built_up(self, base, n, arrangement="box", gap=0.0):
        """n channels as n/2 toe-to-toe boxes (or back-to-back pairs) side by side; returns (profile id, parsed base section)."""
        base = self.channel(base); p = cfs_sections.parse_designator(base)
        if int(n) < 2:
            return base, p
        boxed = arrangement == "box"
        name = "%dx%s%s" % (n, base, "/box" if boxed else "")
        if name not in self.profiles:
            t, B = p["t"], p["flange"]; m = int(n) // 2
            comps = []
            for k in range(m):
                u = (k - (m - 1) / 2.0) * (2.0 * B + gap)
                if boxed:
                    comps += [dict(profile_id=base, rotation_deg=0.0, offset=[round(u - gap / 2.0 - B + t / 2.0, 6), 0.0]), dict(profile_id=base, rotation_deg=180.0, offset=[round(u + gap / 2.0 + B - t / 2.0, 6), 0.0])]
                else:
                    comps += [dict(profile_id=base, rotation_deg=180.0, offset=[round(u - gap / 2.0 - t / 2.0, 6), 0.0]), dict(profile_id=base, rotation_deg=0.0, offset=[round(u + gap / 2.0 + t / 2.0, 6), 0.0])]
            self.m.profile(name, "built_up", {}, components=comps, designation="%d x %s in %d %s" % (n, base, m, "toe-to-toe box(es)" if boxed else "back-to-back pair(s)"),
                           source=dict(kind="engine_section_catalog", name=base, basis="Built-up of identical channels; strong-axis properties are n times the single channel"))
            self.profiles.add(name)
        return name, p

    def envelope(self, designation, width, depth):
        name = "ENV-%gx%g" % (width, depth)
        if name not in self.profiles:
            self.m.profile(name, "box_envelope", dict(width=float(width), depth=float(depth)), designation=designation, scope="delegated",
                           basis="Overall envelope of an open-web truss whose chord and web members are scheduled on its design record; fabrication geometry by the truss supplier",
                           source=dict(kind="design_envelope", name=designation, basis="Overall envelope of the open-web truss; chord and web members are scheduled on the truss record"))
            self.profiles.add(name)
        return name

    def round(self, d):
        name = "ROD-%g" % d
        if name not in self.profiles:
            self.m.profile(name, "round", dict(d=float(d)), designation="%g in. dia. threaded rod" % d)
            self.profiles.add(name)
        return name

    def member(self, mid, kind, axis, profile, material, local_y, level, records=(), **props):
        self.m.member(mid, kind, [r6(axis[0]), r6(axis[1])], profile, material, r12(local_y), reference_line=props.pop("reference_line", "web mid-depth centre"),
                      end_offsets=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]], level=level, design_record_ids=self.link(records), **props)
        return mid

    def joint(self, jid, point, level, ends=(), attachments=()):
        row = dict(id=jid, point=r6(point), level=level, member_ends=[dict(member_id=m, end=e) for m, e in ends])
        if attachments:
            row["attachments"] = [dict(member_id=m, station=round(float(s), 12), offset=r6(o)) for m, s, o in attachments]
        self.m.record("joints", row)
        return jid

    def shared(self, jid, connection_ids, reason):
        self.decl["joint_dispositions"].append(dict(joint_id=jid, kind="shared_connection", connection_ids=list(connection_ids), reason=reason))

    def template(self, key, parts, receivers, records, basis):
        """One versioned template per distinct local geometry; identical occurrences share it."""
        token = bim_assemblies.fingerprint(dict(parts=parts, receivers=receivers, records=sorted(records), basis=basis))
        if token not in self.templates:
            tid = "%s-%s" % (key, token[:10])
            value = dict(id=tid, parts=copy.deepcopy(parts), source_record_ids=list(records), receivers=copy.deepcopy(receivers), basis=basis)
            value["revision"] = bim_assemblies.fingerprint(value)
            self.decl["assemblies"].append(value)
            self.templates[token] = (tid, value["revision"])
        return self.templates[token]

    def instance(self, cid, joint_id, template, origin, x_axis, y_axis, bindings, records, level, **data):
        tid, rev = template
        self.m.record("connections", dict(id=cid, joint_id=joint_id, scope="designed", design_record_ids=self.link(records), parts=[], level=level,
                                          assembly_instance=dict(template_id=tid, revision=rev, origin=r6(origin), x_axis=r12(x_axis), y_axis=r12(y_axis), bindings=bindings), **data))
        return cid


# --------------------------------------------------------------------------------------------
# wall-framed buildings
# --------------------------------------------------------------------------------------------

class WallModel(object):
    def __init__(self, cfg, pkg, pd, rec):
        self.cfg, self.pkg, self.pd, self.r = cfg, pkg, pd, rec
        base = float(cfg.get("base_elevation_ft", 0.0)) * 12.0
        self.L = [base]
        for h in cfg["heights_ft"]:
            self.L.append(self.L[-1] + float(h) * 12.0)
        self.N = len(cfg["heights_ft"])
        self.fz = float(need(pd, "floor_zone_in", "root"))
        self.rz = float(pd.get("roof_zone_in") or self.fz)
        self.dw = float(need(pd, "wall_depth_in", "root"))
        self.tracks = {}            # (line, story, 'B'|'T') -> dict(id, P0, u, n, length, t)
        self.track_studs = {}       # track id -> list of (station, flange_dir, section)
        self.frame_screw = need(need(pd, "fasteners", "root"), "frame_screw", "fasteners")
        for mid, spec in need(pd, "materials", "root").items():
            rec.material(mid, spec)

    # ---- geometry of a line ----
    def frame(self, line):
        d = need(line, "direction", "wall line")
        a, b, pos = float(need(line, "start_ft", "wall line")) * 12.0, float(need(line, "end_ft", "wall line")) * 12.0, float(need(line, "pos_ft", "wall line")) * 12.0
        if d == "X":
            return [a, pos, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], b - a
        return [pos, a, 0.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], b - a

    def pt(self, P0, u, s, z, n=None, off=0.0):
        p = [P0[0] + u[0] * s, P0[1] + u[1] * s, z]
        return add(p, n, off) if n else p

    # ---- one storey of one wall line ----
    def wall_story(self, key, line, k, st, openings):
        r = self.r
        P0, u, n, Lw = self.frame(line)
        lvl = k - 1
        tr = need(st, "track", key)
        tname = r.channel(need(tr, "section", key + " track")); tmat = need(tr, "material", key + " track")
        tt = cfs_sections.parse_designator(tname)["t"]
        zb, zt = self.L[k - 1], self.L[k] - self.zone(k) - float(st.get("top_drop_in", 0.0))       # top_drop_in: a panel that stands under a beam
        base = "W:%s:S%d" % (key, k)
        tracks = {}
        top = st.get("top_track") or tr                     # the collector selection where the top track is the collector (one or two nested plies)
        topname = r.channel(need(top, "section", key + " top track")); topmat = need(top, "material", key + " top track")
        ttop = cfs_sections.parse_designator(topname)["t"]
        for tag, z, ly, recs in (("BT", zb, Z, []), ("TT", zt, neg(Z), [st.get("top_track_record")])):
            sec_, mat_, th_ = (tname, tmat, tt) if tag == "BT" else (topname, topmat, ttop)
            tid = r.member(base + ":" + tag, "bottom_track" if tag == "BT" else "top_track", [self.pt(P0, u, 0.0, z), self.pt(P0, u, Lw, z)], sec_, mat_, ly, lvl,
                           recs, storey=k, wall_line=key, role="bottom track" if tag == "BT" else "top track")
            tracks[tag] = dict(id=tid, z=z, P0=P0, u=u, n=n, length=Lw, t=th_)
            self.tracks[(key, k, tag)] = tracks[tag]
            self.track_studs[tid] = []
        if int(top.get("n", 1)) == 2:
            tracks["TT"]["ply2"] = r.member(base + ":TT2", "top_track", [self.pt(P0, u, 0.0, zt + ttop), self.pt(P0, u, Lw, zt + ttop)], topname, topmat, neg(Z), lvl,
                                            [st.get("top_track_record")], storey=k, wall_line=key, role="top track (second ply)")
        z0, z1 = zb + tt / 2.0, zt - ttop / 2.0
        stud = need(st, "stud", key)
        segs = [(float(a) * 12.0 - self.s0(line), float(b) * 12.0 - self.s0(line)) for a, b in st.get("segments_ft", [])]
        seg_stud = st.get("segment_stud") or stud
        # A wall interrupted at this storey (pass-through under the sheathed segments above): the chord packs and tie-down
        # rods of the segments above continue through to their base, with no sheathing, shear anchorage or braced studs here.
        thru = [(float(a) * 12.0 - self.s0(line), float(b) * 12.0 - self.s0(line)) for a, b in st.get("through_segments_ft", [])]
        if thru and segs:
            raise PhysicalDesignError("%s storey %d: 'through_segments_ft' and 'segments_ft' are exclusive" % (key, k))
        taken = []                                  # (lo, hi) stations occupied by packs / openings
        verticals = []                              # (station, flange_dir, section, material, role, records)

        def pack(s_lo, sel, n_ply, role, records):
            b = cfs_sections.parse_designator(sel["section"])["flange"]
            for i in range(n_ply):
                lo = s_lo + i * b
                if i % 2 == 0:
                    verticals.append((lo, 1.0, sel["section"], sel["material"], role, records))
                else:
                    verticals.append((lo + b, -1.0, sel["section"], sel["material"], role, records))
            taken.append((s_lo - 0.01, s_lo + n_ply * b + 0.01))
            return n_ply * b

        rods = []
        t2 = bool(st.get("type_ii"))            # Type II (perforated) wall: chord packs and tie-down rods at the two ends of the wall only
        for si, (a, e) in enumerate(segs or thru, 1):
            ch = need(st, "chord", key + " (sheathed segment)")
            npl = int(need(ch, "n", key + " chord"))
            first, last = (not t2) or si == 1, (not t2) or si == len(segs)
            w = npl * cfs_sections.parse_designator(ch["section"])["flange"]
            if first:
                pack(a, ch, npl, "chord stud", [ch.get("record")])
            if last:
                pack(e - w, ch, npl, "chord stud", [ch.get("record")])
            rd = st.get("rod")
            if rd and first:
                rods.append((1 if t2 else si, "A", a + w + 2.0, rd)); taken.append((a + w + 0.5, a + w + 3.5))
            if rd and last:
                rods.append((1 if t2 else si, "B", e - w - 2.0, rd)); taken.append((e - w - 3.5, e - w - 0.5))
        for op in openings:
            a = float(op["start_ft"]) * 12.0 - self.s0(line); e = a + float(op["width_ft"]) * 12.0
            jm = need(op, "jamb", "opening " + op["id"]); nj = int(need(jm, "n", "jamb"))
            bj = cfs_sections.parse_designator(jm["section"])["flange"]
            pack(a - nj * bj, jm, nj, "jamb stud", [need(op, "header", "opening")["record"]])
            pack(e, jm, nj, "jamb stud", [op["header"]["record"]])
            taken.append((a - 0.01, e + 0.01))
        sp = float(need(stud, "spacing_in", key + " stud"))
        stations = [i * sp for i in range(int(math.floor(Lw / sp + 1e-9)) + 1)]
        bs = cfs_sections.parse_designator(stud["section"])["flange"]
        if Lw - stations[-1] > bs + 0.5:
            stations.append(Lw - bs)                 # closing stud at the end of the line
        elif stations[-1] > Lw - bs:
            stations[-1] = Lw - bs
        # Inside a sheathed segment or braced bay the studs are set out from the segment start, so equal segments are framed
        # (and fastened) identically wherever they sit on the line; sheathing panel joints (48 in. sheets) land on framing.
        stations = [x for x in stations if not any(a_ - 0.5 <= x <= e_ + 0.5 for a_, e_ in segs)]
        for a, e in segs:
            x = a + sp
            while x < e - 1.0:
                stations.append(x); x += sp
        for a, e in ([] if st.get("strap") else segs):
            x = a + 48.0
            while x < e - 6.0:
                stations.append(x); x += 48.0
        seen = []
        for s in sorted(stations):
            if any(abs(s - q) < 1.0 for q in seen) or any(lo - 1.6 <= s <= hi for lo, hi in taken):
                continue
            seen.append(s)
            inseg = any(a <= s <= e for a, e in segs)
            sel = seg_stud if inseg else stud
            role = ("braced-bay stud" if st.get("strap") else "shear-wall stud") if inseg else "bearing stud"
            if int(stud.get("n", 1)) == 2 and not inseg and s >= cfs_sections.parse_designator(sel["section"])["flange"]:
                verticals.append((s, -1.0, sel["section"], sel["material"], role, [stud.get("record")]))      # back-to-back pair
                verticals.append((s + cfs_sections.parse_designator(sel["section"])["t"], 1.0, sel["section"], sel["material"], role, [stud.get("record")]))
            else:
                verticals.append((s, 1.0, sel["section"], sel["material"], role, [stud.get("record")]))
        ids = {"BT": [], "TT": []}
        for i, (s, fd, sec, mat, role, records) in enumerate(sorted(verticals, key=lambda v: v[0]), 1):
            r.channel(sec)
            mid = r.member("%s:V%03d" % (base, i), "stud", [self.pt(P0, u, s, z0), self.pt(P0, u, s, z1)], sec, mat, [fd * u[0], fd * u[1], 0.0], lvl,
                           records, storey=k, wall_line=key, role=role, station_in=round(s, 4))
            for tag, end, zz, off in (("BT", "start", z0, tt / 2.0), ("TT", "end", z1, -ttop / 2.0)):
                jid = r.joint("%s:%s" % (mid, "B" if tag == "BT" else "T"), self.pt(P0, u, s, zz), lvl, ends=[(mid, end)],
                              attachments=[(tracks[tag]["id"], s / Lw, [0.0, 0.0, off])])
                ids[tag].append(jid)
            self.track_studs[tracks["BT"]["id"]].append((s, fd, sec)); self.track_studs[tracks["TT"]["id"]].append((s, fd, sec))
        self.track_fastening(key, k, tracks, ids, lvl)
        if st.get("uplift_tie"):
            self.uplift_ties(key, k, st, [(s_, fd_, sec_) for s_, fd_, sec_, _m, role_, _r in sorted(verticals, key=lambda v: v[0]) if role_ not in ("chord stud", "jamb stud")], tracks["BT"], lvl)
        numbers = st.get("segment_numbers") or list(range(1, len(segs) + 1))
        aline = line.get("analysis_line") or key
        for si, (a, e) in enumerate(segs, 1):
            if st.get("strap"):
                self.straps(key, k, si, a, e, st, tracks, P0, u, n, lvl, aline, numbers[si - 1])
            else:
                self.sheathing(key, k, si, a, e, st, tracks, P0, u, n, lvl, aline, numbers[si - 1])
            if k == 1 or st.get("anchor"):                  # base of the line: storey 1, or the first storey of a line on a stepped foundation
                self.anchors(key, si, a, e, st, tracks["BT"], lvl)
            else:
                self.floor_shear(key, k, si, a, e, st, tracks["BT"], lvl)
            ch = st["chord"]
            if ch.get("stitch_record"):
                self.stitch(key, k, si, st, base, verticals, a, e, z0, z1, P0, u, lvl)
        for si, (a, e) in enumerate(thru, 1):
            if st["chord"].get("stitch_record"):
                self.stitch(key, k, si, st, base, verticals, a, e, z0, z1, P0, u, lvl)
        for op in openings:
            self.opening(key, k, op, line, st, tracks, P0, u, n, lvl, base)
        return rods

    def zone(self, k):
        return self.rz if k == self.N else self.fz

    def s0(self, line):
        return float(line["start_ft"]) * 12.0

    # ---- stud-to-track screws for one storey of a line (both tracks) ----
    def track_fastening(self, key, k, tracks, joint_ids, lvl):
        r = self.r; fs = self.frame_screw
        rad = float(need(fs, "d_in", "frame_screw")) / 2.0
        for tag in ("BT", "TT"):
            tr = tracks[tag]
            parts = []
            for i, (s, fd, sec) in enumerate(sorted(self.track_studs[tr["id"]]), 1):
                p = cfs_sections.parse_designator(sec)
                x = s + fd * p["flange"] / 2.0
                for side, sg in (("a", 1.0), ("b", -1.0)):
                    parts.append(cyl("scr-%03d%s" % (i, side), "screw", fs["material"], [x, sg * (p["depth"] / 2.0 + tr["t"] + 0.05), 1.0], [0.0, -sg, 0.0], 0.55, rad,
                                     size=fs["size"], role="stud flange to track flange"))
            receivers = [dict(id="track", kind="member", axis=[[0.0, 0.0, 0.0], [tr["length"], 0.0, 0.0]])]
            tpl = r.template("STUD-TRACK", parts, receivers, [fs["record"]], "One %s screw through each track flange into each stud flange, at every stud and chord ply on this track" % fs["size"])
            origin = self.pt(tr["P0"], tr["u"], 0.0, tr["z"])
            jid = r.joint(tr["id"] + ":A", origin, lvl, ends=[(tr["id"], "start")])
            cid = r.instance("C:%s:S%d:%s:studs" % (key, k, tag), jid, tpl, origin, tr["u"], tr["n"] if tag == "BT" else neg(tr["n"]), dict(track=tr["id"]),
                             [fs["record"]], lvl, family="stud-to-track fastening")
            je = r.joint(tr["id"] + ":Z", self.pt(tr["P0"], tr["u"], tr["length"], tr["z"]), lvl, ends=[(tr["id"], "end")])
            r.shared(je, [cid], "Track end is fastened to the end stud of the wall by the stud-to-track screws of this track")
            if tr.get("ply2"):
                for end, s_ in (("start", 0.0), ("end", tr["length"])):
                    j2 = r.joint("%s:%s" % (tr["ply2"], "A" if end == "start" else "Z"), self.pt(tr["P0"], tr["u"], s_, tr["z"] + tr["t"]), lvl, ends=[(tr["ply2"], end)])
                    r.shared(j2, [cid], "Second top-track ply nested on the first and fastened to it through the stud-to-track screws of this track")
            for j in joint_ids[tag]:
                r.shared(j, [cid], "Stud end seated in the track and fastened by the located stud-to-track screws of this track")

    # ---- sheathing panels and their screws ----
    def sheathing(self, key, k, si, a, e, st, tracks, P0, u, n, lvl, aline, number):
        r = self.r
        sh = need(st, "sheathing", key + " segment")
        t = float(need(sh, "thickness_in", "sheathing")); faces = int(need(sh, "faces", "sheathing"))
        edge, field = float(need(sh, "edge_in", "sheathing")), float(need(sh, "field_in", "sheathing"))
        rad = float(need(sh, "screw_d_in", "sheathing")) / 2.0
        zb, zt = tracks["BT"]["z"], tracks["TT"]["z"]
        h, Ls = zt - zb, e - a
        Lw = tracks["BT"]["length"]
        studs = sorted(s - a for s, fd, sec in self.track_studs[tracks["BT"]["id"]] if a + 1.0 < s < e - 1.0)

        def run(x0, z0, x1, z1, pitch):
            length = math.hypot(x1 - x0, z1 - z0)
            m = max(1, int(math.floor(length / pitch + 1e-9)))
            return [(x0 + (x1 - x0) * i / m, z0 + (z1 - z0) * i / m) for i in range(m + 1)]

        def template(mirror):
            pts = []
            pts += run(0.75, 1.0, Ls - 0.75, 1.0, edge) + run(0.75, h - 1.0, Ls - 0.75, h - 1.0, edge)
            pts += run(0.75, 1.0 + edge, 0.75, h - 1.0 - edge, edge) + run(Ls - 0.75, 1.0 + edge, Ls - 0.75, h - 1.0 - edge, edge)
            joints = [x for x in (48.0 * i for i in range(1, int(Ls // 48.0) + 1)) if x < Ls - 6.0]
            for x in joints:
                pts += run(x, 1.0 + edge, x, h - 1.0 - edge, edge)
            if h > 96.0 + 6.0:
                pts += run(0.75 + edge, 96.0, Ls - 0.75 - edge, 96.0, edge)
            for s in studs:
                x = (Ls - s) if mirror else s
                if all(abs(x - j) > 1.0 for j in joints):
                    pts += run(x + 0.8, 1.0 + field, x + 0.8, h - 1.0 - field, field)
            uniq = sorted({(round(x, 3), round(z, 3)) for x, z in pts})
            parts = [cyl("scr-%04d" % i, "screw", sh["screw_material"], [x, t + 0.05, z], [0.0, -1.0, 0.0], t + 0.6, rad, size=sh["screw"], role="sheathing to framing")
                     for i, (x, z) in enumerate(uniq, 1)]
            receivers = [dict(id="track", kind="member", axis=[[0.0, -self.dw / 2.0, 0.0], [Ls, -self.dw / 2.0, 0.0]]), dict(id="panel", kind="panel")]
            return r.template("SHEATHING", parts, receivers, [sh["record"]],
                              "%s screws at %g in. o.c. on all panel edges (blocked, 48 in. sheets) and %g in. o.c. in the field, %g in. sheathing" % (sh["screw"], edge, field, t))

        for f in range(faces):
            sg = 1.0 if f == 0 else -1.0
            off = sg * (self.dw / 2.0 + t / 2.0)
            v = [self.pt(P0, u, a, zb, n, off), self.pt(P0, u, e, zb, n, off), self.pt(P0, u, e, zt, n, off), self.pt(P0, u, a, zt, n, off)]
            pid = "P:%s:S%d:G%d:F%d" % (key, k, si, f + 1)
            r.m.panel(pid, "wall_sheathing", [r6(p) for p in v], t, sh["material"], level=lvl, storey=k, wall_line=key, face="+" if f == 0 else "-",
                      design_record_ids=r.link([sh["record"]]), segment=si)
            if f == 0:
                r.decl["candidate_panel_replacements"].append(dict(candidate_id="cfs:wall:%s:%s:story:%d:segment:%d" % (aline.split(":")[0], aline.split(":")[1], k, number),
                                                                    entity_ids=[pid], basis="Sheathed segment declared as located sheathing panels with their selected fastening"))
            else:
                r.decl["candidate_panel_replacements"][-1]["entity_ids"].append(pid)
            if f == 0:
                origin = self.pt(P0, u, a, zb, n, self.dw / 2.0); xa, ya, s_at = u, n, a
            else:
                origin = self.pt(P0, u, e, zb, n, -self.dw / 2.0); xa, ya, s_at = neg(u), neg(n), e
            jid = r.joint("J:%s" % pid, origin, lvl, attachments=[(tracks["BT"]["id"], s_at / Lw, [sg * n[0] * self.dw / 2.0, sg * n[1] * self.dw / 2.0, 0.0])])
            r.instance("C:%s" % pid, jid, template(f == 1), origin, xa, ya, dict(track=tracks["BT"]["id"], panel=pid), [sh["record"]], lvl, family="sheathing fastening")

    # ---- flat-strap X bracing of one bay with its gusset connections ----
    def straps(self, key, k, si, a, e, st, tracks, P0, u, n, lvl, aline, number):
        r = self.r
        sp_ = need(st, "strap", key + " braced bay")
        width, t, faces = float(need(sp_, "width_in", "strap")), float(need(sp_, "t_in", "strap")), int(need(sp_, "faces", "strap"))
        gus = need(sp_, "gusset", "strap"); tg = float(need(gus, "t_in", "gusset"))
        ch = need(st, "chord", key + " (braced bay)")
        cw = int(ch["n"]) * cfs_sections.parse_designator(ch["section"])["flange"]
        zb, zt = tracks["BT"]["z"], tracks["TT"]["z"]
        xa, xe = a + cw, e - cw
        wb, hb = xe - xa, zt - zb
        Ld = math.hypot(wb, hb); cx, cz = wb / Ld, hb / Ld
        prof = r.flat(width, t)
        rad = float(need(sp_, "screw_d_in", "strap")) / 2.0
        nst, per_row, ngu = int(need(sp_, "n_strap", "strap")), int(need(sp_, "per_row", "strap")), int(need(sp_, "n_gusset", "strap"))
        rows = int(math.ceil(nst / float(per_row)))
        gsize = max(12.0, math.ceil(cw + (3.0 + rows * 1.0 + 1.0) * max(cx, cz) + width / 2.0))
        rec, crec = need(sp_, "record", "strap"), need(sp_, "connection_record", "strap")
        Lw = tracks["BT"]["length"]

        def template(s_):
            ys0 = s_ * (self.dw / 2.0 + tg + t / 2.0)                 # strap plane from the wall centreline, local y
            yg = (self.dw / 2.0 if s_ > 0 else -self.dw / 2.0 - tg) - ys0
            ys = 0.0
            parts = [box("gusset", "gusset_plate", gus["material"], [-cw, yg, 0.0], [gsize, tg, gsize], role="strap gusset plate", thickness_in=tg)]
            for i in range(rows):
                for j in range(per_row):
                    al = 3.0 + i * 1.0
                    lat = (j - (per_row - 1) / 2.0) * min(1.25, (width - 1.0) / max(1, per_row - 1))
                    parts.append(cyl("scr-s-%02d-%d" % (i + 1, j + 1), "screw", sp_["screw_material"], [al * cx - lat * cz, ys + s_ * (t / 2.0 + 0.05), al * cz + lat * cx], [0.0, -s_, 0.0], tg + t + 0.5, rad,
                                     size=sp_["screw"], role="strap to gusset"))
            half = ngu // 2
            for i in range(half):
                parts.append(cyl("scr-c-%02d" % (i + 1), "screw", sp_["screw_material"], [-cw / 2.0, yg + (tg + 0.05 if s_ > 0 else -0.05), 1.5 + i * (gsize - 3.0) / max(1, half - 1)], [0.0, -s_, 0.0], tg + 0.6, rad,
                                 size=sp_["screw"], role="gusset to chord stud"))
            for i in range(ngu - half):
                parts.append(cyl("scr-t-%02d" % (i + 1), "screw", sp_["screw_material"], [-cw + 1.5 + i * (gsize - 3.0) / max(1, ngu - half - 1), yg + (tg + 0.05 if s_ > 0 else -0.05), 0.6], [0.0, -s_, 0.0], tg + 0.6, rad,
                                 size=sp_["screw"], role="gusset to track"))
            receivers = [dict(id="strap", kind="member", axis=[[0.0, ys, 0.0], [12.0 * cx, ys, 12.0 * cz]]), dict(id="track", kind="member", axis=[[0.0, -ys0, 0.0], [12.0, -ys0, 0.0]])]
            return r.template("STRAP-END", parts, receivers, [crec],
                              "%g in. x %.4f in. strap fastened to a %.4f in. gusset plate with %d %s screws; gusset fastened to the chord studs and track with %d %s screws" % (width, t, tg, nst, sp_["screw"], ngu, sp_["screw"]))

        ids = []
        for f in range(faces):
            sf = 1.0 if f == 0 else -1.0
            off = sf * (self.dw / 2.0 + tg + t / 2.0)
            for tag, (x0, z0, x1, z1) in (("U", (xa, zb, xe, zt)), ("D", (xa, zt, xe, zb))):
                A, B = self.pt(P0, u, x0, z0, n, off), self.pt(P0, u, x1, z1, n, off)
                ax = [(B[i] - A[i]) / Ld for i in range(3)]
                ly = [n[1] * ax[2] - n[2] * ax[1], n[2] * ax[0] - n[0] * ax[2], n[0] * ax[1] - n[1] * ax[0]]          # n x axis: strap width in the wall plane
                mid = r.member("B:%s:S%d:G%d:F%d%s" % (key, k, si, f + 1, tag), "strap", [A, B], prof, sp_["material"], ly, lvl, [rec], storey=k, wall_line=key, role="tension strap",
                               reference_line="strap centreline", face="+" if f == 0 else "-", segment=si)
                ids.append(mid)
                for end, (xs, zs) in (("start", (x0, z0)), ("end", (x1, z1))):
                    sx = 1.0 if abs(xs - xa) < 1e-6 else -1.0
                    szz = 1.0 if abs(zs - zb) < 1e-6 else -1.0
                    sy = sx * szz
                    tr = tracks["BT"] if szz > 0 else tracks["TT"]
                    pnt = self.pt(P0, u, xs, zs, n, off)
                    origin = pnt
                    jid = r.joint("%s:%s" % (mid, "A" if end == "start" else "B"), pnt, lvl, ends=[(mid, end)], attachments=[(tr["id"], xs / Lw, [n[0] * off, n[1] * off, 0.0])])
                    r.instance("C:%s:%s" % (mid, "A" if end == "start" else "B"), jid, template(sf * sy), origin, [sx * u[0], sx * u[1], 0.0], [sy * n[0], sy * n[1], 0.0],
                               dict(strap=mid, track=tr["id"]), [crec, rec], lvl, family="strap end connection")
        r.decl["candidate_panel_replacements"].append(dict(candidate_id="cfs:wall:%s:%s:story:%d:segment:%d" % (aline.split(":")[0], aline.split(":")[1], k, number),
                                                            entity_ids=ids, basis="Braced bay declared as located tension straps with their gusset connections"))

    def anchors(self, key, si, a, e, st, bt, lvl):
        r = self.r
        an = need(st, "anchor", key + " base storey")
        d, sp, emb = float(need(an, "d_in", "anchor")), float(need(an, "spacing_in", "anchor")), float(need(an, "embed_in", "anchor"))
        Ls = e - a
        m = max(1, int(math.floor((Ls - 12.0) / sp + 1e-9)))
        xs = [6.0 + (Ls - 12.0) * i / m for i in range(m + 1)]
        parts = []
        for i, x in enumerate(xs, 1):
            parts.append(cyl("anchor-%02d" % i, "anchor_rod", an["material"], [x, 0.0, -emb], Z, emb + 2.0, d / 2.0, diameter_in=d, role="track shear anchor"))
            parts.append(box("washer-%02d" % i, "plate_washer", an["material"], [x - 1.5, -1.5, bt["t"] / 2.0], [3.0, 3.0, 0.25], role="plate washer"))
        fid = self.foundation()
        receivers = [dict(id="track", kind="member", axis=[[0.0, 0.0, 0.0], [Ls, 0.0, 0.0]]), dict(id="foundation", kind="interface")]
        tpl = r.template("TRACK-ANCHORS", parts, receivers, [an["record"]], "%g in. dia. anchors at %g in. o.c. (maximum) with plate washers through the bottom track of the sheathed segment" % (d, sp))
        origin = self.pt(bt["P0"], bt["u"], a, bt["z"])
        jid = r.joint("J:%s:S%d:G%d:anchors" % (key, lvl + 1, si), origin, lvl, attachments=[(bt["id"], a / bt["length"], [0.0, 0.0, 0.0])])
        r.instance("C:%s:S%d:G%d:anchors" % (key, lvl + 1, si), jid, tpl, origin, bt["u"], bt["n"], dict(track=bt["id"], foundation=fid), [an["record"]], lvl, family="track shear anchorage")

    def uplift_ties(self, key, k, st, studs, bt, lvl):
        """Wind uplift straps on the wall studs: one strap on the outside flange of every stud that is not a chord or jamb ply,
        lapped across the floor onto the stud below (upper storeys) or cast into the foundation (base storey)."""
        r = self.r; ut = st["uplift_tie"]
        w, t, lap = float(need(ut, "width_in", "uplift_tie")), float(need(ut, "t_in", "uplift_tie")), float(ut.get("lap_in", 10.0))
        ns, d = int(need(ut, "n_screws", "uplift_tie")), float(need(ut, "screw_d_in", "uplift_tie"))
        at_base = k == 1 or bool(st.get("anchor"))
        drop = float(ut.get("embed_in", 6.0)) if at_base else self.zone(k - 1) + lap
        y0 = self.dw / 2.0
        zs = [1.0 + (lap - 2.0) * j / max(1, ns - 1) for j in range(ns)]
        parts = []
        for i, (s_, fd, sec) in enumerate(studs, 1):
            xc = s_ + fd * cfs_sections.parse_designator(sec)["flange"] / 2.0
            parts.append(box("strap-%03d" % i, "strap", ut["material"], [xc - w / 2.0, y0, -drop], [w, t, drop + lap], role="uplift strap", width_in=w, thickness_in=t))
            for j, z in enumerate(zs, 1):
                parts.append(cyl("su-%03d-%02d" % (i, j), "screw", ut["screw_material"], [xc, y0 + t + 0.05, z], [0.0, -1.0, 0.0], 1.0, d / 2.0, size=ut.get("screw"), role="uplift strap to the stud above the floor"))
                if not at_base:
                    parts.append(cyl("sl-%03d-%02d" % (i, j), "screw", ut["screw_material"], [xc, y0 + t + 0.05, -drop + z - 0.5], [0.0, -1.0, 0.0], 1.0, d / 2.0, size=ut.get("screw"), role="uplift strap to the stud below the floor"))
        if not parts:
            return
        receivers = [dict(id="track", kind="member", axis=[[0.0, 0.0, 0.0], [bt["length"], 0.0, 0.0]])]
        bind = dict(track=bt["id"])
        if at_base:
            receivers.append(dict(id="foundation", kind="interface")); bind["foundation"] = self.foundation()
        rec = need(ut, "record", "uplift_tie")
        tpl = r.template("STUD-UPLIFT-TIE", parts, receivers, [rec], "%g in. x %g in. uplift strap on every stud with %d %s screws to the stud%s" % (
            w, t, ns, ut.get("screw", "screws"), ", cast into the foundation" if at_base else " above and %d to the stud below the floor" % ns))
        origin = self.pt(bt["P0"], bt["u"], 0.0, bt["z"])
        jid = r.joint("J:%s:S%d:uplift" % (key, k), origin, lvl, attachments=[(bt["id"], 0.0, [0.0, 0.0, 0.0])])
        r.instance("C:%s:S%d:uplift" % (key, k), jid, tpl, origin, bt["u"], bt["n"], bind, [rec], lvl, family="stud uplift tie")

    def floor_shear(self, key, k, si, a, e, st, bt, lvl):
        r = self.r
        fs = need(st, "floor_shear", "%s storey %d" % (key, k))
        rows, sp = int(need(fs, "rows", "floor_shear")), float(need(fs, "spacing_in", "floor_shear"))
        Ls = e - a
        m = max(1, int(math.floor((Ls - 2.0) / sp + 1e-9)))
        ys = [0.0] if rows == 1 else [-1.5 + 3.0 * j / (rows - 1) for j in range(rows)]
        pk = fs.get("part_kind", "screw"); pl = float(fs.get("length_in", 1.3))
        parts = [cyl("%s-%04d-%d" % ("anchor" if pk != "screw" else "scr", i, j), pk, fs["screw_material"], [1.0 + (Ls - 2.0) * i / m, y, bt["t"] / 2.0 + 0.05], neg(Z), pl, float(fs["screw_d_in"]) / 2.0,
                     size=fs.get("screw"), role="bottom track to floor framing")
                 for i in range(m + 1) for j, y in enumerate(ys, 1)]
        recs = [fs["record"]]
        if fs.get("uplift_rows"):                      # Type II wall: dedicated uplift row(s) beside the shear rows
            mu = max(1, int(math.floor((Ls - 2.0) / float(fs["uplift_spacing_in"]) + 1e-9)))
            for j in range(int(fs["uplift_rows"])):
                yu = 2.4 * (1.0 if j == 0 else -1.0)
                parts += [cyl("up-%04d-%d" % (i, j + 1), "screw", fs["screw_material"], [1.0 + (Ls - 2.0) * i / mu, yu, bt["t"] / 2.0 + 0.05], neg(Z), pl, float(fs["screw_d_in"]) / 2.0,
                              size=fs.get("screw"), role="bottom track uplift tie") for i in range(mu + 1)]
            recs.append(need(fs, "uplift_record", "floor_shear"))
        receivers = [dict(id="track", kind="member", axis=[[0.0, 0.0, 0.0], [Ls, 0.0, 0.0]])]
        tpl = r.template("FLOOR-SHEAR", parts, receivers, recs, "%d row(s) of %s at %g in. o.c. through the bottom track web into the floor below the wall segment%s" % (
            rows, "screws" if pk == "screw" else "anchors", sp, ("; %d uplift row(s) at %g in. o.c." % (fs["uplift_rows"], fs["uplift_spacing_in"])) if fs.get("uplift_rows") else ""))
        origin = self.pt(bt["P0"], bt["u"], a, bt["z"])
        jid = r.joint("J:%s:S%d:G%d:shear" % (key, k, si), origin, lvl, attachments=[(bt["id"], a / bt["length"], [0.0, 0.0, 0.0])])
        r.instance("C:%s:S%d:G%d:shear" % (key, k, si), jid, tpl, origin, bt["u"], bt["n"], dict(track=bt["id"]), recs, lvl, family="wall-to-floor shear transfer")

    def stitch(self, key, k, si, st, base, verticals, a, e, z0, z1, P0, u, lvl):
        """Screws interconnecting the chord plies (one template per pack end)."""
        r = self.r; ch = st["chord"]; fs = self.frame_screw
        b = cfs_sections.parse_designator(ch["section"])["flange"]; npl = int(ch["n"])
        h = z1 - z0
        for tag, s_lo in (("A", a), ("B", e - npl * b)):
            first = None
            for i, (s, fd, sec, mat, role, records) in enumerate(sorted(verticals, key=lambda v: v[0]), 1):
                if role == "chord stud" and s_lo - 0.01 <= s <= s_lo + npl * b + 0.01:
                    first = "%s:V%03d" % (base, i); s_first = s
                    break
            if first is None:
                continue
            zs = [6.0 + 12.0 * i for i in range(int((h - 12.0) // 12.0) + 1)]
            parts = [cyl("scr-%02d-%03d" % (j, i), "screw", fs["material"], [j * b - (s_first - s_lo), -0.6, z], [0.0, 1.0, 0.0], 1.2, float(fs["d_in"]) / 2.0, role="chord ply interconnection")
                     for j in range(1, npl) for i, z in enumerate(zs, 1)]
            if not parts:
                continue
            receivers = [dict(id="ply", kind="member", axis=[[0.0, 0.0, 0.0], [0.0, 0.0, h]])]
            tpl = r.template("CHORD-STITCH", parts, receivers, [ch["stitch_record"]], "Screws at 12 in. o.c. interconnecting adjacent chord plies over the storey height")
            origin = self.pt(P0, u, s_first, z0)
            jid = r.joint("J:%s:S%d:G%d:%s:stitch" % (key, k, si, tag), origin, lvl, attachments=[(first, 0.0, [0.0, 0.0, 0.0])])
            r.instance("C:%s:S%d:G%d:%s:stitch" % (key, k, si, tag), jid, tpl, origin, u, [-u[1], u[0], 0.0], dict(ply=first), [ch["stitch_record"]], lvl, family="chord interconnection")

    def opening(self, key, k, op, line, st, tracks, P0, u, n, lvl, base):
        r = self.r
        a = float(op["start_ft"]) * 12.0 - self.s0(line); e = a + float(op["width_ft"]) * 12.0
        zb = tracks["BT"]["z"]
        sill, head = float(need(op, "sill_ft", "opening")) * 12.0, float(need(op, "head_ft", "opening")) * 12.0
        hd = need(op, "header", "opening " + op["id"])
        p = cfs_sections.parse_designator(r.channel(hd["section"]))
        zc = zb + head + p["depth"] / 2.0
        plies = []
        for i in range(int(need(hd, "n", "header"))):
            sg = 1.0 if i % 2 == 0 else -1.0
            off = sg * p["t"] / 2.0
            mid = r.member("%s:H:%s:%d" % (base, op["id"], i + 1), "header", [self.pt(P0, u, a, zc, n, off), self.pt(P0, u, e, zc, n, off)], hd["section"], hd["material"],
                           [sg * n[0], sg * n[1], 0.0], lvl, [hd["record"]], storey=k, wall_line=key, role="header ply")
            plies.append(mid)
        jm = op["jamb"]; fs = self.frame_screw
        clip = op.get("clip_record") or hd["record"]
        nscr = max(2, int(op.get("clip_screws", 4)))
        for tag, s, end in (("A", a, "start"), ("B", e, "end")):
            origin = self.pt(P0, u, s, zc, n, p["t"] / 2.0)                 # end of the first ply
            xdir = u if tag == "A" else neg(u)
            ydir = n if tag == "A" else neg(n)
            sgn = 1.0 if tag == "A" else -1.0                              # local +y points to the first ply's flange side at end A
            parts = [box("clip-1", "clip_angle", jm["material"], [0.0, sgn * p["t"] / 2.0 if sgn > 0 else p["t"] / 2.0, -p["depth"] / 2.0 + 0.5], [1.5, 0.0566, p["depth"] - 1.0], role="header clip angle"),
                     box("clip-2", "clip_angle", jm["material"], [0.0, -p["t"] * 1.5 - 0.0566 if sgn > 0 else -p["t"] * 0.5 - 0.0566 - p["t"], -p["depth"] / 2.0 + 0.5], [1.5, 0.0566, p["depth"] - 1.0], role="header clip angle")]
            for j in range(nscr):
                z = -p["depth"] / 2.0 + 1.0 + (p["depth"] - 2.0) * j / max(1, nscr - 1)
                parts.append(cyl("scr-h-%d" % (j + 1), "screw", fs["material"], [0.75, 0.7, z], [0.0, -1.0, 0.0], 1.6, float(fs["d_in"]) / 2.0, size=fs["size"], role="clip to header webs"))
                parts.append(cyl("scr-j-%d" % (j + 1), "screw", fs["material"], [0.4, 1.2, z + 0.3], [-1.0, 0.0, 0.0], 0.8, float(fs["d_in"]) / 2.0, size=fs["size"], role="clip to jamb"))
            receivers = [dict(id="header", kind="member", axis=[[0.0, 0.0, 0.0], [6.0, 0.0, 0.0]])]
            tpl = r.template("HEADER-CLIP", parts, receivers, [clip], "Clip angle each side of the header with %d screws to the header webs and %d screws to the jamb" % (nscr, nscr))
            jid = r.joint("%s:%s" % (plies[0], tag), origin, lvl, ends=[(plies[0], end)])
            cid = r.instance("C:%s:H:%s:%s" % (base, op["id"], tag), jid, tpl, origin, xdir, ydir, dict(header=plies[0]), [clip], lvl, family="header to jamb")
            for i, mid in enumerate(plies[1:], 1):
                sg = 1.0 if i % 2 == 0 else -1.0
                je = r.joint("%s:%s" % (mid, tag), self.pt(P0, u, s, zc, n, sg * p["t"] / 2.0), lvl, ends=[(mid, end)])
                r.shared(je, [cid], "Header ply end carried by the located clip angles and screws at this jamb")
        v = [self.pt(P0, u, a, zb + sill), self.pt(P0, u, e, zb + sill), self.pt(P0, u, e, zb + head), self.pt(P0, u, a, zb + head)]
        r.m.opening("O:%s:S%d:%s" % (key, k, op["id"]), plies[0], [r6(q) for q in v], level=lvl, storey=k, wall_line=key, kind=op.get("kind", "opening"),
                    design_record_ids=r.link([hd["record"]]))

    # ---- tie-down rods ----
    def rods(self, key, line, by_story):
        r = self.r
        P0, u, n, Lw = self.frame(line)
        marks = sorted({(si, tag) for k in by_story for si, tag, s, rd in by_story[k]})
        fid = self.foundation()
        for si, tag in marks:
            prev = None
            for k in sorted(by_story):
                hit = [(s, rd) for sj, tj, s, rd in by_story[k] if (sj, tj) == (si, tag)]
                if not hit:
                    prev = None
                    continue
                s, rd = hit[0]
                d = float(need(rd, "d_in", "rod"))
                z0 = self.L[k - 1] + (6.0 if (k > 1 and prev is not None) else 0.0)       # a rod starts at the base of its own line (stepped foundations)
                top = (k == max(by_story)) or not any((sj, tj) == (si, tag) for sj, tj, _s, _r in by_story.get(k + 1, []))
                z1 = (self.tracks[(key, k, "TT")]["z"] + 3.0) if top else (self.L[k] + 6.0)        # above the top track of the line (a panel under a beam stops below it)
                mid = r.member("R:%s:G%d%s:S%d" % (key, si, tag, k), "tie_rod", [self.pt(P0, u, s, z0), self.pt(P0, u, s, z1)], r.round(d), rd["material"], [u[0], u[1], 0.0], k - 1,
                               [rd["record"]], storey=k, wall_line=key, role="continuous tie-down rod", reference_line="rod centreline")
                bt = self.tracks[(key, k, "BT")]
                if prev is None:
                    jid = r.joint(mid + ":B", self.pt(P0, u, s, z0), k - 1, ends=[(mid, "start")])
                    recs = [rd["record"]] + ([rd["anchorage_record"]] if rd.get("anchorage_record") else [])
                    fd = need(self.pd, "foundation", "root")
                    r.m.record("connections", dict(id="C:" + mid + ":foundation", joint_id=jid, scope="delegated", design_record_ids=r.link(recs), parts=[], level=k - 1,
                                                   responsible_party=need(fd, "responsible_party", "foundation"), detail_ref=need(fd, "detail_ref", "foundation"),
                                                   representation="Rod embedment, anchor plate and concrete anchorage by the foundation design for the tabulated rod tension; the rod itself is modelled to the top of the foundation",
                                                   interface_id=fid, family="tie-down rod foundation anchorage"))
                else:
                    pm, pz1 = prev
                    origin = self.pt(P0, u, s, z0)
                    parts = [cyl("coupler", "coupler", rd["material"], [0.0, 0.0, -2.0], Z, 4.0, d * 0.8, role="rod coupler"),
                             box("bearing-plate", "bearing_plate", rd["material"], [-2.5, -2.5, -6.0 + bt["t"] / 2.0], [5.0, 5.0, 0.5], role="bearing plate on the bottom track"),
                             cyl("take-up", "take_up_device", rd["material"], [0.0, 0.0, -5.5 + bt["t"] / 2.0], Z, 2.0, d * 0.9, role="shrinkage take-up device"),
                             cyl("nut", "nut", rd["material"], [0.0, 0.0, -3.5 + bt["t"] / 2.0], Z, d, d * 0.85, role="heavy hex nut")]
                    receivers = [dict(id="rod_below", kind="member", axis=[[0.0, 0.0, -6.0], [0.0, 0.0, 0.0]]), dict(id="rod_above", kind="member", axis=[[0.0, 0.0, 0.0], [0.0, 0.0, 6.0]])]
                    tpl = r.template("ROD-COUPLER", parts, receivers, [rd["record"]], "Rod coupler above the floor with bearing plate, take-up device and nut on the bottom track, %g in. rod" % d)
                    jid = r.joint(mid + ":B", origin, k - 1, ends=[(pm, "end"), (mid, "start")])
                    r.instance("C:" + mid + ":coupler", jid, tpl, origin, u, n, dict(rod_below=pm, rod_above=mid), [rd["record"]], k - 1, family="tie-down coupler and take-up")
                if top:
                    tt = self.tracks[(key, k, "TT")]
                    origin = self.pt(P0, u, s, z1)
                    parts = [box("bearing-plate", "bearing_plate", rd["material"], [-2.5, -2.5, -3.0 + tt["t"] / 2.0], [5.0, 5.0, 0.5], role="bearing plate on the top track"),
                             cyl("nut", "nut", rd["material"], [0.0, 0.0, -2.5 + tt["t"] / 2.0], Z, d, d * 0.85, role="heavy hex nut")]
                    receivers = [dict(id="rod", kind="member", axis=[[0.0, 0.0, -6.0], [0.0, 0.0, 0.0]]), dict(id="track", kind="member", axis=[[-2.0, 0.0, -3.0], [2.0, 0.0, -3.0]])]
                    tpl = r.template("ROD-TOP", parts, receivers, [rd["record"]], "Rod termination: bearing plate and nut on the top track, %g in. rod" % d)
                    jid = r.joint(mid + ":T", origin, k - 1, ends=[(mid, "end")])
                    r.instance("C:" + mid + ":top", jid, tpl, origin, u, n, dict(rod=mid, track=tt["id"]), [rd["record"]], k - 1, family="tie-down termination")
                    prev = None
                else:
                    prev = (mid, z1)

    def foundation(self):
        fid = "IF:foundation"
        if not getattr(self, "_fdn", False):
            fd = need(self.pd, "foundation", "root")
            self.r.m.record("interfaces", dict(id=fid, scope="delegated", responsible_party=need(fd, "responsible_party", "foundation"),
                                               representation="Foundation and concrete anchorage outside the cold-formed steel design; demands are tabulated in the anchorage schedule",
                                               detail_ref=need(fd, "detail_ref", "foundation")))
            self._fdn = True
        return fid

    # ---- floors and roof ----
    def floor(self, fl):
        r = self.r
        lvl = int(need(fl, "level", "floor")); zL = self.L[lvl]
        sh = need(fl, "sheathing", "floor %d" % lvl)
        tsh = float(need(sh, "thickness_in", "floor sheathing"))
        fs = self.frame_screw
        for zn in need(fl, "zones", "floor %d" % lvl):
            x0, x1 = [float(v) * 12.0 for v in need(zn, "x_ft", "zone")]; y0, y1 = [float(v) * 12.0 for v in need(zn, "y_ft", "zone")]
            jo = need(zn, "joist", "zone " + zn["id"])
            if jo.get("envelope"):
                env = jo["envelope"]
                jname = r.envelope(need(jo, "designation", "truss"), float(need(env, "width_in", "truss envelope")), float(need(env, "depth_in", "truss envelope")))
                p = dict(depth=float(env["depth_in"]))
            else:
                jname = r.channel(jo["section"])
                p = cfs_sections.parse_designator(jname)
            sp = float(need(jo, "spacing_in", "joist"))
            span = need(zn, "span", "zone")
            sup = need(zn, "supports", "zone")
            tA, tB = self.tracks[(sup[0], lvl, "TT")], self.tracks[(sup[1], lvl, "TT")]
            zc = tA["z"] + tA["t"] / 2.0 + p["depth"] / 2.0
            if span == "Y":
                lo, hi = x0, x1; ya, yb = y0, y1
                mk = lambda q: ([q, ya, zc], [q, yb, zc]); ly = [1.0, 0.0, 0.0]
            else:
                lo, hi = y0, y1; ya, yb = x0, x1
                mk = lambda q: ([ya, q, zc], [yb, q, zc]); ly = [0.0, 1.0, 0.0]
            m = int(math.floor((hi - lo) / sp + 1e-9))
            qs = [lo + sp * i for i in range(1, m + 1) if lo + sp * i < hi - 1.0]
            ends = {0: [], 1: []}
            for i, q in enumerate(qs, 1):
                A, B = mk(q)
                what = "truss" if jo.get("envelope") else ("truss bottom chord" if jo.get("rise_ft") else "joist")
                mid = r.member("F:L%d:%s:J%03d" % (lvl, zn["id"], i), "joist", [A, B], jname, jo["material"], ly, lvl, [jo["record"]], zone=zn["id"], role=("floor " if fl.get("kind") != "roof" else "roof ") + what,
                               **({"reference_line": "truss envelope centre"} if jo.get("envelope") else {}))
                tops = []
                if jo.get("rise_ft"):                      # gable truss: the two top chords are located members; webs are the truss supplier's
                    ridge = [(A[0] + B[0]) / 2.0, (A[1] + B[1]) / 2.0, zc + float(jo["rise_ft"]) * 12.0]
                    tname = r.channel(need(jo, "top_section", "gable truss"))
                    for tag, P in (("TA", A), ("TB", B)):
                        tops.append(r.member("%s:%s" % (mid, tag), "joist", [P, ridge], tname, jo["material"], ly, lvl, [jo["record"]], zone=zn["id"], role="roof truss top chord"))
                    jr = r.joint(mid + ":R", ridge, lvl, ends=[(tops[0], "end"), (tops[1], "end")])
                    r.m.record("connections", dict(id="C:" + mid + ":ridge", joint_id=jr, scope="delegated", design_record_ids=r.link([jo["record"]]), parts=[], level=lvl,
                                                   responsible_party=need(jo, "truss_supplier", "gable truss"), detail_ref=dict(id="TRUSS-PANEL-POINTS", revision="1"),
                                                   representation="Ridge joint, web members and panel-point connections of the roof truss by the truss supplier for the chord forces and reactions on the truss record",
                                                   family="roof truss ridge joint"))
                for e_i, (pnt, tr, endname) in enumerate(((A, tA, "start"), (B, tB, "end"))):
                    s = self.station(tr, pnt)
                    off = [pnt[0] - (tr["P0"][0] + tr["u"][0] * s), pnt[1] - (tr["P0"][1] + tr["u"][1] * s), pnt[2] - tr["z"]]
                    jid = r.joint("%s:%s" % (mid, "A" if e_i == 0 else "B"), pnt, lvl, ends=[(mid, endname)] + ([(tops[e_i], "start")] if tops else []), attachments=[(tr["id"], s / tr["length"], off)])
                    ends[e_i].append((jid, s, off))
            for e_i, tr in ((0, tA), (1, tB)):
                if not ends[e_i]:
                    continue
                recs = [zn["bearing_record"]] + ([zn["uplift_record"]] if zn.get("uplift_record") else [])
                s0 = min(s for _j, s, _o in ends[e_i])
                parts = []
                for i, (_j, s, off) in enumerate(sorted(ends[e_i], key=lambda v: v[1]), 1):
                    lx = s - s0
                    for j, dy in enumerate((-0.6, 0.6), 1):
                        parts.append(cyl("scr-%03d-%d" % (i, j), "screw", fs["material"], [lx + 0.8, dy, tr["t"] / 2.0 + 0.2], neg(Z), 0.6, float(fs["d_in"]) / 2.0, size=fs["size"], role="joist flange to top track"))
                    if zn.get("uplift_record"):
                        uc = zn.get("uplift_clip") or {}
                        parts.append(box("clip-%03d" % i, "clip_angle", uc.get("material", jo["material"]), [lx - 0.05, -float(uc.get("width_in", 2.0)) / 2.0, tr["t"] / 2.0],
                                         [float(uc.get("t_in", 0.0451)), float(uc.get("width_in", 2.0)), float(uc.get("height_in", 3.0))], role=uc.get("role", "uplift clip angle")))
                receivers = [dict(id="track", kind="member", axis=[[0.0, 0.0, 0.0], [max(1.0, max(s for _j, s, _o in ends[e_i]) - s0), 0.0, 0.0]])]
                tpl = r.template("JOIST-BEARING", parts, receivers, recs, "Two %s screws through each joist bottom flange into the wall top track%s" % (fs["size"], ("; " + (zn.get("uplift_clip") or {}).get("text", "uplift clip angle at each joist")) if zn.get("uplift_record") else ""))
                origin = self.pt(tr["P0"], tr["u"], s0, tr["z"])
                jid = r.joint("J:L%d:%s:%s:bearing" % (lvl, zn["id"], "A" if e_i == 0 else "B"), origin, lvl, attachments=[(tr["id"], s0 / tr["length"], [0.0, 0.0, 0.0])])
                cid = r.instance("C:L%d:%s:%s:bearing" % (lvl, zn["id"], "A" if e_i == 0 else "B"), jid, tpl, origin, tr["u"], tr["n"], dict(track=tr["id"]), recs, lvl, family="joist bearing")
                for j, _s, _o in ends[e_i]:
                    r.shared(j, [cid], "Joist end seated on the wall top track and fastened by the located screws of this bearing line")
        bx = [float(v) * 12.0 for v in fl.get("x_ft", [0.0, self.cfg["plan_ft"][0]])]; by = [float(v) * 12.0 for v in fl.get("y_ft", [0.0, self.cfg["plan_ft"][1]])]
        ring = [[float(x) * 12.0, float(y) * 12.0, zL] for x, y in fl["outline_ft"]] if fl.get("outline_ft") else [[bx[0], by[0], zL], [bx[1], by[0], zL], [bx[1], by[1], zL], [bx[0], by[1], zL]]
        if fl.get("panels"):                              # explicit diaphragm panels (wings, sloped roof planes): vertices x, y in ft, z offset from the level in ft
            for pn in fl["panels"]:
                r.m.panel("D:L%d:%s" % (lvl, pn["id"]), "roof" if fl.get("kind") == "roof" else "floor", [[float(x) * 12.0, float(y) * 12.0, zL + float(dz) * 12.0] for x, y, dz in pn["ring_ft"]],
                          float(pn.get("thickness_in", tsh)), pn.get("material", sh["material"]), level=lvl, design_record_ids=r.link(sh.get("records", [])), role="diaphragm sheathing")
            return
        r.m.panel("D:L%d" % lvl, "roof" if fl.get("kind") == "roof" else "floor", ring, tsh, sh["material"], level=lvl,
                  design_record_ids=r.link(sh.get("records", [])), role="diaphragm sheathing")

    def station(self, tr, pnt):
        return (pnt[0] - tr["P0"][0]) * tr["u"][0] + (pnt[1] - tr["P0"][1]) * tr["u"][1]

    # ---- whole building ----
    def build(self):
        pd = self.pd
        ops = {}
        for op in pd.get("openings", []):
            ops.setdefault((op["line"], int(op["story"])), []).append(op)
        for key, line in need(pd, "wall_lines", "root").items():
            rods = {}
            for ks, st in sorted(need(line, "stories", key).items(), key=lambda kv: int(kv[0])):
                k = int(ks)
                got = self.wall_story(key, line, k, st, ops.get((key, k), []))
                if got:
                    rods[k] = got
            if rods:
                self.rods(key, line, rods)
        for pl in pd.get("post_lines", []):
            self.post_line(pl)
        for fl in pd.get("floors", []):
            self.floor(fl)
        grids = []
        for key, line in pd["wall_lines"].items():
            P0, u, n, Lw = self.frame(line)
            if line.get("grid", True):
                grids.append(dict(id=key, label=key.split(":")[1], direction="V" if line["direction"] == "X" else "U", points_in=[r6(P0), r6(self.pt(P0, u, Lw, 0.0))]))
        top_name = pd.get("top_level_name") or "Roof"            # a platform's top level is a floor deck, not a roof
        levels = [dict(level=i, name=("Foundation" if i == 0 else top_name if i == self.N else "Level %d" % (i + 1)), elevation_in=round(z, 6)) for i, z in enumerate(self.L)]
        self.r.decl["reference_system"] = dict(basis="Wall-line worklines and floor datums of the completed design; wall studs are centred on the line", levels=levels, grids=grids)
        cov = need(pd, "coverage", "root")
        self.r.decl["coverage"] = {k: dict(complete=True, basis=need(cov, k, "coverage")) for k in ("primary_members", "secondary_members", "openings", "connections")}

    # ---- open post-and-beam line under a floor (platforms): posts on base plates, one beam per bay on the post caps ----
    def post_line(self, pl):
        """{"id", "direction", "pos_ft", "level", "posts_ft": [...], "post": {"section", "n", "arrangement", "material", "record"},
            "beam": {"section", "n", "arrangement", "material", "record"},
            "base": {"plate_in": [a, b], "plate_t_in", "plate_material", "anchor_d_in", "n_anchors", "anchor_material", "embed_in", "record"},
            "cap": {"plate_in": [a, b], "plate_t_in", "plate_material", "n_bolts", "bolt_d_in", "bolt_material", "record"}}
        Each beam is registered as a joist support under the key "<id>/<bay>" (bays numbered from 1)."""
        r = self.r
        key = need(pl, "id", "post line"); k = int(need(pl, "level", "post line")); lvl = k - 1
        xs = [float(v) * 12.0 for v in need(pl, "posts_ft", key)]
        line = dict(direction=need(pl, "direction", key), pos_ft=need(pl, "pos_ft", key), start_ft=xs[0] / 12.0, end_ft=xs[-1] / 12.0)
        P0, u, n, Lw = self.frame(line)
        ps, bm, bs, cap = need(pl, "post", key), need(pl, "beam", key), need(pl, "base", key), need(pl, "cap", key)
        pprof, pp = r.built_up(need(ps, "section", "post"), int(ps.get("n", 2)), ps.get("arrangement", "box"))
        bprof, bp = r.built_up(need(bm, "section", "beam"), int(bm.get("n", 2)), bm.get("arrangement", "box"))
        ztop = self.L[k] - self.zone(k)                       # top of the beams = underside of the joists
        zb = ztop - bp["depth"]
        tc = float(need(cap, "plate_t_in", "cap")); cw, cl = [float(v) for v in need(cap, "plate_in", "cap")]
        nb, db = int(need(cap, "n_bolts", "cap")), float(need(cap, "bolt_d_in", "cap"))
        tp = float(need(bs, "plate_t_in", "base")); pw, plen = [float(v) for v in need(bs, "plate_in", "base")]
        na, da, emb = int(need(bs, "n_anchors", "base")), float(need(bs, "anchor_d_in", "base")), float(need(bs, "embed_in", "base"))
        fid = self.foundation()
        # templates (local x along the line, y across it, z up)
        bparts = [box("base-plate", "base_plate", bs["plate_material"], [-pw / 2.0, -plen / 2.0, -tp], [pw, plen, tp], role="post base plate", thickness_in=tp)]
        for i in range(na):
            x = (-1.0 if i % 2 == 0 else 1.0) * (pw / 2.0 - 1.75)
            y = 0.0 if na <= 2 else (-1.0 if i < 2 else 1.0) * (plen / 2.0 - 1.75)
            bparts.append(cyl("anchor-%d" % (i + 1), "anchor_rod", bs["anchor_material"], [x, y, -tp - emb], Z, emb + tp + 2.0, da / 2.0, diameter_in=da, role="post base anchor"))
        nsb, nsc = int(bs.get("n_screws", 0)), int(cap.get("n_screws", 0))
        hw = pp["depth"] / 2.0
        for j in range(nsb):
            bparts.append(cyl("scr-%d" % (j + 1), "screw", bs["screw_material"], [(-1.0 if j % 2 == 0 else 1.0) * (hw + 0.2), 0.0, 1.0 + 1.0 * (j // 2)], [1.0 if j % 2 == 0 else -1.0, 0.0, 0.0], 0.8, float(bs["screw_d_in"]) / 2.0, role="base shoe to post web"))
        btpl = r.template("POST-BASE", bparts, [dict(id="post", kind="member", axis=[[0.0, 0.0, 0.0], [0.0, 0.0, 12.0]]), dict(id="foundation", kind="interface")], [bs["record"]],
                          "Post base: %g in. base plate with a formed shoe fastened to the post with %d screws, %d x %g in. anchors into the slab" % (tp, nsb, na, da))
        posts = []
        for i, x in enumerate(xs, 1):
            A, B = self.pt(P0, u, x - xs[0], self.L[lvl]), self.pt(P0, u, x - xs[0], zb - tc)
            mid = r.member("%s:P%02d" % (key, i), "post", [A, B], pprof, ps["material"], u, lvl, [need(ps, "record", "post")], storey=k, post_line=key, role="platform post", reference_line="section centroid")
            posts.append((mid, A, B))
            jb = r.joint("%s:B" % mid, A, lvl, ends=[(mid, "start")])
            r.instance("C:%s:base" % mid, jb, btpl, A, u, n, dict(post=mid, foundation=fid), [bs["record"]], lvl, family="post base")
        beams = []
        for i in range(len(xs) - 1):
            A, B = self.pt(P0, u, xs[i] - xs[0], zb + bp["depth"] / 2.0), self.pt(P0, u, xs[i + 1] - xs[0], zb + bp["depth"] / 2.0)
            mid = r.member("%s:B%02d" % (key, i + 1), "beam", [A, B], bprof, bm["material"], n, lvl, [need(bm, "record", "beam")], storey=k, post_line=key, bay=i + 1, role="platform beam", reference_line="section centroid")
            beams.append((mid, A, B))
            self.tracks[("%s/%d" % (key, i + 1), k, "TT")] = dict(id=mid, z=zb + bp["depth"] / 2.0, P0=[A[0], A[1], 0.0], u=u, n=n, length=xs[i + 1] - xs[i], t=bp["depth"])
        for i, (pid, A, B) in enumerate(posts):
            left = beams[i - 1] if i > 0 else None; right = beams[i] if i < len(beams) else None
            parts = [box("cap-plate", "cap_plate", cap["plate_material"], [-cw / 2.0, -cl / 2.0, 0.0], [cw, cl, tc], role="post cap plate under the beam ends", thickness_in=tc)]
            sides = ([-1.0] if left else []) + ([1.0] if right else [])
            per = max(1, nb // max(1, 2 * len(sides)))
            for sg in sides:
                for j in range(per):
                    for q, sy in enumerate((-1.0, 1.0), 1):
                        parts.append(cyl("bolt-%s-%d-%d" % ("L" if sg < 0 else "R", j + 1, q), "bolt", cap["bolt_material"], [sg * (1.5 + 2.5 * j), sy * (cl / 2.0 - 1.25), -0.5], Z, tc + 1.5, db / 2.0, diameter_in=db, role="beam bottom flange to cap plate"))
            for j in range(nsc):
                parts.append(cyl("scr-%d" % (j + 1), "screw", cap["screw_material"], [(-1.0 if j % 2 == 0 else 1.0) * (hw + 0.2), 0.0, -1.0 - 1.0 * (j // 2)], [1.0 if j % 2 == 0 else -1.0, 0.0, 0.0], 0.8, float(cap["screw_d_in"]) / 2.0, role="cap sleeve to post web"))
            receivers = [dict(id="post", kind="member", axis=[[0.0, 0.0, -12.0], [0.0, 0.0, 0.0]])]; bind = dict(post=pid)
            hz = tc + bp["depth"] / 2.0
            if left:
                receivers.append(dict(id="beam_left", kind="member", axis=[[-12.0, 0.0, hz], [0.0, 0.0, hz]])); bind["beam_left"] = left[0]
            if right:
                receivers.append(dict(id="beam_right", kind="member", axis=[[0.0, 0.0, hz], [12.0, 0.0, hz]])); bind["beam_right"] = right[0]
            tpl = r.template("POST-CAP", parts, receivers, [cap["record"]], "Post cap: %g in. cap plate with a formed sleeve fastened to the post with %d screws, %d x %g in. bolts through the bottom flanges of each beam end" % (tc, nsc, 2 * per, db))
            jt = r.joint("%s:T" % pid, B, lvl, ends=[(pid, "end")])
            cid = r.instance("C:%s:cap" % pid, jt, tpl, B, u, n, bind, [cap["record"]], lvl, family="post cap")
            ends = ([(left[0], "end")] if left else []) + ([(right[0], "start")] if right else [])
            Pj = left[2] if left else right[1]
            jb_ = r.joint("%s:beams" % pid, Pj, lvl, ends=ends)
            r.shared(jb_, [cid], "Beam end(s) seated on the post cap plate and bolted by the located bolts of this cap assembly")


# --------------------------------------------------------------------------------------------

def dispose_unlinked(rec, pkg):
    """Records the physical model does not carry need an explicit, structured disposition from the engineer."""
    listed = {d["record_id"]: d for d in (pkg.get("physical_design") or {}).get("record_dispositions", [])}
    for rid, d in listed.items():
        if rid not in rec.linked:
            rec.decl["record_dispositions"].append(copy.deepcopy(d))


def build(job, save=True):
    """Record the native physical model of a completed job. Returns a summary dict."""
    root = Path(job)
    pkg = json.loads((root / "design" / PACKAGE).read_text(encoding="utf-8"))
    pd = pkg.get("physical_design")
    if not isinstance(pd, dict) or pd.get("schema") != SCHEMA or pd.get("version") != 1:
        raise PhysicalDesignError("calculation package needs physical_design (schema %s, version 1); see contract/BIM_EXPORT.md" % SCHEMA)
    import os
    cwd = os.getcwd()
    os.chdir(root)
    try:
        cfg = runpy.run_path(str(root / "cfg.py"))["cfg"]
    finally:
        os.chdir(cwd)
    current = physical_inputs.read(root, "CFS")
    value = copy.deepcopy(current) if current else physical_inputs.empty("CFS")
    for k in list(value["declarations"]):
        if isinstance(value["declarations"][k], list):
            value["declarations"][k] = []
        elif k in ("coverage", "reference_system"):
            value["declarations"].pop(k)
    model = physical_inputs.PhysicalModel("CFS", value)
    model._loaded_sha = physical_inputs.fingerprint(current) if current else None
    rec = Recorder(model)
    kind = need(pd, "kind", "root")
    if kind == "wall":
        WallModel(cfg, pkg, pd, rec).build()
    elif kind == "portal":
        try:
            from . import cfs_physical_portal
        except ImportError:
            import cfs_physical_portal
        cfs_physical_portal.PortalModel(cfg, pkg, pd, rec).build()
    else:
        raise PhysicalDesignError("physical_design.kind must be 'wall' or 'portal'")
    dispose_unlinked(rec, pkg)
    d = model.value["declarations"]
    summary = {k: len(d.get(k, [])) for k in ("members", "panels", "openings", "joints", "connections", "assemblies", "profiles", "materials")}
    if save:
        summary.update(model.save(root))
    return summary


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Record the native physical model of a completed CFS job")
    ap.add_argument("job", type=Path)
    a = ap.parse_args(argv)
    print(json.dumps(build(a.job)))


if __name__ == "__main__":
    main()

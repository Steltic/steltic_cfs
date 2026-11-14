"""Native physical model of a completed CFS portal-frame design (see cfs_physical for the recorder and the wall path).

``PortalModel`` turns the structured selections in ``calc_package_cfs.json#/physical_design`` (kind "portal") into located
members and assemblies.  The frame members take their axes and analysis identities from the planar frame the analysis
solved (``bim_adapter.portal``), so the physical columns and rafters are the analysed ones, frame by frame.

    physical_design = {
      "schema": "steltic.cfs_physical_design", "version": 1, "kind": "portal",
      "materials": {id: {"specification": str, "record": "materials/@i"}},
      "frame": {
        "column": {"section": "1200S250-118", "n": 8, "material": id, "record": "members/frame-col"},
        "rafter": {"section", "n", "material", "record": "members/frame-raf"},
        "knee":  {"plate_t_in", "plate_material", "bolt_d_in", "bolt_material", "rows", "gauge_in", "pitch_in", "record": "connections/conn-knee"},
        "apex":  {... "record": "connections/conn-apex"},                               # omitted where the frame has no ridge joint
        "base":  {"plate_t_in", "plate_in": [w, l], "plate_material", "anchor_d_in", "n_anchors", "anchor_material", "embed_in",
                  "cleat_t_in", "bolt_d_in", "bolts_per_pair", "bolt_material", "record": "anchorage/base-anchor"}},
      "purlins": {"section", "material", "spacing_ft", "bolt_d_in", "bolt_material", "flange_brace": {"leg_in", "t_in", "material"}, "record": "schedules/sched-purlin"},
      "girts":   {"section", "material", "spacing_ft", "bolt_d_in", "bolt_material", "record": "schedules/sched-girt"},
      "bracing": {"wall": {"width_in", "t_in", "material", "bays": [1, 11, 22], "n_screws", "screw_d_in", "screw_material", "record": "schedules/sched-strap"},
                  "roof": {..., "panels": 2}},
      "foundation": {"responsible_party": str, "detail_ref": {"id", "revision"}},
      "coverage": {"primary_members", "secondary_members", "openings", "connections"},
      "record_dispositions": [...]
    }

Other frame forms (field lists in contract/BIM_EXPORT.md and in the builders below):

    "frame": {"existing": {...}}                    existing shell of a component design: context members, delegated joints
    "frame": {"canopy": true, "knee_brace", "head", "kb_end", "base": {fixed base ...}}      box columns, continuous rafter, knee braces
    "frame": {"sbmf": true, "column": {"hss": {"h", "b", "t"}}, "rafter" (two channels, "interconnect": {"gap_in": column width}),
              "joint": {"bolt_d_in", "a_in", "b_in", "c_in", "record", "record_interior"},
              "splice": {"at_ft": [...], plate and bolt set-out, "record"},                  # optional shipping splices at analysis nodes
              "base": {pinned plate, anchors on the column centreline, "weld_leg_in", "weld_material"}}
                                                    cold-formed steel special bolted moment frames (AISI S400 E4), one or more bays

Coordinates are inches, Z up; frames stand in X-Z planes at y = frame position.  Bay k lies between frames k and k + 1.
"""
import math
from collections import defaultdict

try:
    from . import cfs_sections, bim_adapter
    from .cfs_physical import need, add, r6, neg, cyl, box, Z, PhysicalDesignError
except ImportError:                                   # engine modules are also imported flat
    import cfs_sections, bim_adapter
    from cfs_physical import need, add, r6, neg, cyl, box, Z, PhysicalDesignError

Y = [0.0, 1.0, 0.0]


def unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def lerp(a, b, t):
    return [a[i] + (b[i] - a[i]) * t for i in range(3)]


class PortalModel(object):
    def __init__(self, cfg, pkg, pd, rec):
        self.cfg, self.pkg, self.pd, self.r = cfg, pkg, pd, rec
        for mid, spec in need(pd, "materials", "root").items():
            rec.material(mid, spec)
        self.fr = need(pd, "frame", "root")
        self.rafters = defaultdict(list)       # frame index -> [(member id, A, B)]
        self.columns = defaultdict(list)
        self.fid = None
        self.axis = {}

    # ---- profiles ----
    def built_up(self, sel):
        base = self.r.channel(need(sel, "section", "frame member")); n = int(need(sel, "n", "frame member"))
        p = cfs_sections.parse_designator(base)
        if n == 1:
            return base, p
        gap = float((sel.get("interconnect") or {}).get("gap_in", 0.0))
        boxed = sel.get("arrangement") == "box"
        sp = sel.get("pair_spacing_in")
        if sp:
            p["pair_spacing"] = float(sp)
        name = "%dx%s%s%s" % (n, base, "/box" if boxed else "", "@%g" % float(sp) if sp else "")
        if name not in self.r.profiles:
            t = p["t"]; B = p["flange"]
            comps = []
            for u in self.pair_offsets(n, B, gap, sp):
                if boxed:                       # toe to toe: the webs are the outer faces, the lips meet at the seam plane u
                    comps.append(dict(profile_id=base, rotation_deg=0.0, offset=[round(u - gap / 2.0 - B + t / 2.0, 6), 0.0]))
                    comps.append(dict(profile_id=base, rotation_deg=180.0, offset=[round(u + gap / 2.0 + B - t / 2.0, 6), 0.0]))
                else:
                    comps.append(dict(profile_id=base, rotation_deg=180.0, offset=[round(u - gap / 2.0 - t / 2.0, 6), 0.0]))
                    comps.append(dict(profile_id=base, rotation_deg=0.0, offset=[round(u + gap / 2.0 + t / 2.0, 6), 0.0]))
            if boxed:
                des = "%d x %s in %d toe-to-toe box(es)%s%s" % (n, base, n // 2, " side by side" if n > 2 else "", ", lips %g in. apart on batten plates" % gap if gap else "")
                basis = "Toe-to-toe boxes; in-plane properties are n times the single channel, each box acts on its own out of plane"
            else:
                des = "%d x %s in %d back-to-back pairs side by side%s%s" % (n, base, n // 2, ", webs of each pair %g in. apart" % gap if gap else "", ", pairs at %g in. centres" % float(sp) if sp else "")
                basis = "Back-to-back pairs side by side%s; in-plane properties are n times the single channel" % ("" if sp else " with flange tips touching")
            self.r.m.profile(name, "built_up", {}, components=comps, designation=des, source=dict(kind="engine_section_catalog", name=base, basis=basis))
            self.r.profiles.add(name)
        return name, p

    def existing_profile(self, ex):
        name = "EXISTING-%gx%g" % (float(ex["width_in"]), float(ex["depth_in"]))
        if name not in self.r.profiles:
            self.r.m.profile(name, "box_envelope", dict(width=float(ex["width_in"]), depth=float(ex["depth_in"])), designation=need(ex, "designation", "existing frame"), scope="delegated",
                             basis="Outline of the existing frame member for context; the section is not surveyed and is not designed here",
                             source=dict(kind="design_envelope", name=ex["designation"], basis="Outline of the existing frame member; flange width as assumed for purlin bearing"))
            self.r.profiles.add(name)
        return name

    def zee(self, name):
        if name not in self.r.profiles:
            p = cfs_sections.parse_designator(name)
            self.r.m.profile(name, "z", dict(d=p["depth"], b=p["flange"], lip=p["lip"], t=p["t"], r=cfs_sections.RADIUS_BY_MIL[p["mil"]]), designation=name, lip_inside=True,
                             lip_angle_deg=float(p.get("lip_angle_deg") or 50.0), source=dict(kind="engine_section_catalog", name=name, basis="SFIA designation, design thickness and inside bend radius"))
            self.r.profiles.add(name)
        return name, cfs_sections.parse_designator(name)

    def foundation(self):
        if self.fid is None:
            fd = need(self.pd, "foundation", "root")
            self.fid = "IF:foundation"
            self.r.m.record("interfaces", dict(id=self.fid, scope="delegated", responsible_party=need(fd, "responsible_party", "foundation"),
                                               representation="Foundations and concrete anchorage outside the cold-formed steel design; base reactions are tabulated in the anchorage schedule",
                                               detail_ref=need(fd, "detail_ref", "foundation")))
        return self.fid

    # ---- frames ----
    def frames(self, cands):
        r = self.r
        by_frame = defaultdict(list)
        for c in cands:
            by_frame[int(c["id"].split(":")[2])].append(c)
        if self.fr.get("canopy"):
            return self.canopy_frames(by_frame)
        if self.fr.get("sbmf"):
            return self.sbmf_frames(by_frame)
        ex = self.fr.get("existing")
        if ex:
            # existing shell of a component design: the frame members are context, shown as outline envelopes; nothing of them is designed here
            cprof = rprof = self.existing_profile(ex)
            cp = rp = dict(depth=float(ex["depth_in"]), flange=float(ex["width_in"]), t=0.0, lip=0.0)
            self.cp, self.rp = cp, rp
            self.ncol = self.nraf = 0
            self.gcol = self.graf = 0.0
            iid = "IF:%s" % ex["interface"]["id"]
            declared = {i_.get("id") for i_ in (self.pkg.get("bim") or {}).get("interfaces", [])}        # the calculation package may already declare it
            if iid not in declared:
                r.m.record("interfaces", dict(id=iid, scope="existing", responsible_party=need(ex["interface"], "responsible_party", "existing interface"),
                                              representation=need(ex["interface"], "representation", "existing interface"), detail_ref=need(ex["interface"], "detail_ref", "existing interface")))
        else:
            cprof, cp = self.built_up(need(self.fr, "column", "frame")); rprof, rp = self.built_up(need(self.fr, "rafter", "frame"))
            self.cp, self.rp = cp, rp
            self.ncol, self.nraf = int(self.fr["column"]["n"]), int(self.fr["rafter"]["n"])
            self.gcol, self.graf = [float((self.fr[k].get("interconnect") or {}).get("gap_in", 0.0)) for k in ("column", "rafter")]
        for fi in sorted(by_frame):
            ends = defaultdict(list)             # rounded point -> [(member id, end, kind, other point)]
            nlab = defaultdict(int)
            for c in by_frame[fi]:
                nlab[c["id"].split(":")[4]] += 1
            for c in by_frame[fi]:
                label = c["id"].split(":")[4]
                if nlab[label] > 1:
                    label = "%s%s" % (label, c["id"].split(":")[5])
                col = c["kind"] == "column"
                sel = (dict(material=ex["material"], record=ex["column_record"] if col else ex["rafter_record"])) if ex else (self.fr["column"] if col else self.fr["rafter"])
                A, B = [float(v) for v in c["axis"][0]], [float(v) for v in c["axis"][1]]
                mid = "FR%02d:%s" % (fi, label)
                r.member(mid, "column" if col else "rafter", [A, B], cprof if col else rprof, sel["material"], Y, 0 if col else 1, [need(sel, "record", "frame member")],
                         analysis_ids=c["analysis_ids"], frame=fi, role=("existing column" if col else "existing rafter") if ex else ("portal column" if col else "portal rafter"), reference_line="section centroid",
                         **({"scope": "existing_context"} if ex else {}))
                lo, hi = (A, B) if A[2] <= B[2] else (B, A)          # hosts are set out from their lower end
                (self.columns if col else self.rafters)[fi].append((mid, lo, hi))
                self.axis[mid] = (A, B)
                if not ex:
                    self.interconnect(mid, (A, B), sel, cp if col else rp, fi, col)
                for end, P, Q in (("start", A, B), ("end", B, A)):
                    ends[tuple(round(v, 3) for v in P)].append((mid, end, "column" if col else "rafter", Q, P))
            for pt, es in sorted(ends.items()):
                P = list(es[0][4])
                kinds = sorted(e[2] for e in es)
                jid = r.joint("J:FR%02d:%d:%d" % (fi, int(round(P[0])), int(round(P[2]))), P, 0 if P[2] < 1.0 else 1, ends=[(e[0], e[1]) for e in es])
                if ex:
                    what = "base" if (kinds == ["column"] and P[2] < 1.0) else ("knee" if "column" in kinds else "apex")
                    r.m.record("connections", dict(id="C:FR%02d:existing:%d:%d" % (fi, int(round(P[0])), int(round(P[2]))), joint_id=jid, scope="delegated", design_record_ids=r.link([need(ex["records"], what, "existing records")]), parts=[],
                                                   level=0 if what == "base" else 1, responsible_party=ex["interface"]["responsible_party"], detail_ref=ex["interface"]["detail_ref"],
                                                   representation="Existing %s of the existing frame; not surveyed and not part of this component design" % ("column base and anchorage" if what == "base" else what + " connection"),
                                                   interface_id=iid, family="existing frame %s" % ("base" if what == "base" else "joint")))
                elif kinds == ["column"] and P[2] < 1.0:
                    self.base(fi, jid, P, es[0])
                elif "column" in kinds and "rafter" in kinds:
                    self.knee(fi, jid, P, es, "valley" if (kinds.count("rafter") == 2 and "valley" in self.fr) else "knee")
                elif kinds and all(k == "rafter" for k in kinds) and len(es) == 2:
                    self.apex(fi, jid, P, es)
                else:
                    raise PhysicalDesignError("portal frame %d: joint at %s joins %s; no connection family covers it" % (fi, P, kinds))

    # ---- special bolted moment frames: one-piece hollow columns between the webs of two beam channels, eight bolts per joint, pinned bases ----
    def sbmf_frames(self, by_frame):
        r = self.r; fr = self.fr
        cs_ = need(fr, "column", "frame"); h = need(cs_, "hss", "column")
        cprof = need(cs_, "section", "column")
        if cprof not in r.profiles:
            r.m.profile(cprof, "rhs", dict(h=float(h["h"]), b=float(h["b"]), t=float(h["t"])), designation=cprof,
                        source=dict(kind="engine_section_catalog", name=cprof, basis="AISC shapes database, ASTM A500 design wall thickness (0.93 times nominal)"))
            r.profiles.add(cprof)
        cp = dict(depth=float(h["h"]), flange=float(h["b"]), t=float(h["t"]), lip=0.0, half_width=float(h["b"]) / 2.0)
        rprof, rp = self.built_up(need(fr, "rafter", "frame"))
        self.cp, self.rp = cp, rp
        self.ncol, self.nraf = 1, int(fr["rafter"]["n"])
        self.gcol, self.graf = 0.0, float((fr["rafter"].get("interconnect") or {}).get("gap_in", 0.0))
        for fi in sorted(by_frame):
            cs = by_frame[fi]
            rafs = [c for c in cs if c["kind"] == "rafter"]
            pts = sorted([[float(v) for v in q] for c in rafs for q in c["axis"]], key=lambda q: q[0])
            A, B = pts[0], pts[-1]
            L = math.dist(A, B)
            for q in pts:                                        # one beam, one piece in the model: every analysis node must lie on the line A-B
                t_ = sum((q[i] - A[i]) * (B[i] - A[i]) for i in range(3)) / L ** 2
                if math.dist(q, lerp(A, B, t_)) > 0.05:
                    raise PhysicalDesignError("bolted moment frame %d: the beam is not straight between its ends (node at %s); use a flat or single-slope beam" % (fi, q))
            dr = unit([B[0] - A[0], 0.0, B[2] - A[2]])
            sp = fr.get("splice")
            cuts = sorted(float(v) * 12.0 for v in (sp or {}).get("at_ft") or [])
            nodes = [A] + [lerp(A, B, (x - A[0]) / (B[0] - A[0])) for x in cuts] + [B]
            pieces = []
            for c in rafs:                                      # a splice inside an analysis element would split its design record between two pieces
                x0, x1 = min(q[0] for q in c["axis"]), max(q[0] for q in c["axis"])
                if any(x0 + 0.05 < float(n_[0]) < x1 - 0.05 for n_ in nodes[1:-1]):
                    raise PhysicalDesignError("bolted moment frame %d: a beam splice must stand at an analysis node (mid-bay or column), not inside the element %s" % (fi, c["id"]))
            for k in range(len(nodes) - 1):
                Pa, Pb = nodes[k], nodes[k + 1]
                pid = "FR%02d:raf" % fi if len(nodes) == 2 else "FR%02d:raf%d" % (fi, k + 1)
                aids = sorted({a for c in rafs for a in c["analysis_ids"] if min(q[0] for q in c["axis"]) >= Pa[0] - 0.05 and max(q[0] for q in c["axis"]) <= Pb[0] + 0.05})
                if not aids:
                    raise PhysicalDesignError("bolted moment frame %d: a beam splice must stand at an analysis node (mid-bay or column)" % fi)
                r.member(pid, "rafter", [Pa, Pb], rprof, fr["rafter"]["material"], Y, 1, [need(fr["rafter"], "record", "rafter")], analysis_ids=aids, frame=fi,
                         role="moment frame beam: two channels, one each side of the columns", reference_line="section centroid")
                self.rafters[fi].append((pid, Pa, Pb)); self.axis[pid] = (Pa, Pb)
                pieces.append((pid, Pa, Pb))
            for k in range(len(pieces) - 1):
                Pj = pieces[k][2]
                js_ = r.joint("J:FR%02d:%d:%d" % (fi, int(round(Pj[0])), int(round(Pj[2]))), Pj, 1, ends=[(pieces[k][0], "end"), (pieces[k + 1][0], "start")])
                self.beam_splice(fi, js_, Pj, pieces[k][0], pieces[k + 1][0], dr)
            used = set()
            for c in sorted((c for c in cs if c["kind"] == "column"), key=lambda c: float(c["axis"][0][0])):
                label = c["id"].split(":")[4]
                P0, P1 = [float(v) for v in c["axis"][0]], [float(v) for v in c["axis"][1]]
                mid = "FR%02d:%s" % (fi, label)
                if mid in self.axis:
                    mid = "%s%s" % (mid, c["id"].split(":")[5])
                r.member(mid, "column", [P0, P1], cprof, cs_["material"], Y, 0, [need(cs_, "record", "column")], analysis_ids=c["analysis_ids"], frame=fi, role="moment frame column, pinned base, no splice", reference_line="section centroid")
                lo, hi = (P0, P1) if P0[2] <= P1[2] else (P1, P0)
                self.columns[fi].append((mid, lo, hi)); self.axis[mid] = (P0, P1)
                e_lo, e_hi = ("start", "end") if P0[2] <= P1[2] else ("end", "start")
                jb = r.joint("J:FR%02d:%d:0" % (fi, int(round(lo[0]))), lo, 0, ends=[(mid, e_lo)])
                self.base_sbmf(fi, jb, lo, mid)
                end = "start" if math.dist(hi, A) < 0.05 else ("end" if math.dist(hi, B) < 0.05 else None)
                if end:
                    used.add(end)
                    rid = pieces[0][0] if end == "start" else pieces[-1][0]
                    jh = r.joint("J:FR%02d:%d:%d" % (fi, int(round(hi[0])), int(round(hi[2]))), hi, 1, ends=[(mid, e_hi), (rid, end)])
                    self.sbmf_joint(fi, jh, hi, mid, rid, dr, dr if end == "start" else neg(dr), False)
                else:
                    rid, Pa, Pb = next(q for q in pieces if q[1][0] - 0.05 <= hi[0] <= q[2][0] + 0.05)
                    t_, o_ = self.att((Pa, Pb), hi)
                    jh = r.joint("J:FR%02d:%d:%d" % (fi, int(round(hi[0])), int(round(hi[2]))), hi, 1, ends=[(mid, e_hi)], attachments=[(rid, t_, o_)])
                    self.sbmf_joint(fi, jh, hi, mid, rid, dr, dr, True)
            for end, P, q in (("start", A, pieces[0]), ("end", B, pieces[-1])):
                if end not in used:
                    self.free_end(q[0], end, P, 1, "Beam end beyond the last column")

    def base_sbmf(self, fi, jid, P, mid):
        r = self.r; bs = need(self.fr, "base", "frame"); cp = self.cp
        tp = float(need(bs, "plate_t_in", "base")); pw, pl = [float(v) for v in need(bs, "plate_in", "base")]
        d, na, emb = float(need(bs, "anchor_d_in", "base")), int(need(bs, "n_anchors", "base")), float(need(bs, "embed_in", "base"))
        leg = float(need(bs, "weld_leg_in", "base")); gx = float(bs.get("anchor_gauge_in", 4.0))
        h, b = cp["depth"], cp["flange"]
        parts = [box("base-plate", "base_plate", bs["plate_material"], [-pw / 2.0, -pl / 2.0, -tp], [pw, pl, tp], role="base plate", thickness_in=tp)]
        ya = (b / 2.0 + pl / 2.0) / 2.0
        per = max(1, na // 2)
        for i in range(na):
            sy = -1.0 if i % 2 == 0 else 1.0
            x = 0.0 if per == 1 else (-gx / 2.0 + (i // 2) * gx / (per - 1))
            parts.append(cyl("anchor-%d" % (i + 1), "anchor_rod", bs["anchor_material"], [x, sy * ya, -tp - emb], Z, emb + tp + 3.0, d / 2.0, diameter_in=d, role="anchor rod"))
        for j, (ox, oy, lx, ly) in enumerate(((-h / 2.0 - leg, -b / 2.0, leg, b), (h / 2.0, -b / 2.0, leg, b), (-h / 2.0, -b / 2.0 - leg, h, leg), (-h / 2.0, b / 2.0, h, leg)), 1):
            parts.append(self.weld("weld-base-%d" % j, bs["weld_material"], [ox, oy, 0.0], [lx, ly, leg], leg, "column to base plate, all round"))
        receivers = [dict(id="column", kind="member", axis=[[0.0, 0.0, 0.0], [0.0, 0.0, 24.0]]), dict(id="foundation", kind="interface")]
        tpl = r.template("BASE-PINNED-HSS", parts, receivers, [bs["record"]],
                         "Pinned column base: %g in. base plate %g in. x %g in. on %d x %g in. anchor rods set on the column centreline across the frame (%g in. apart in the plane of the frame), column welded to the plate all round (%g in. fillet)"
                         % (tp, pw, pl, na, d, gx, leg))
        r.instance("C:FR%02d:base:%d" % (fi, int(round(P[0]))), jid, tpl, P, [1.0, 0.0, 0.0], Y, dict(column=mid, foundation=self.foundation()), [bs["record"]], 0, family="column base")

    def beam_splice(self, fi, jid, P, ra, rb, dr):
        """Web splice of the two beam channels: one plate on the outside of each web, bolts each side of the splice line."""
        r = self.r; sp = need(self.fr, "splice", "frame"); rp = self.rp
        tp, d = float(need(sp, "plate_t_in", "splice")), float(need(sp, "bolt_d_in", "splice"))
        Lp, dp = [float(v) for v in need(sp, "plate_in", "splice")]
        rows, gauge, pitch = int(need(sp, "rows", "splice")), float(need(sp, "gauge_in", "splice")), float(need(sp, "pitch_in", "splice"))
        nrm = [-dr[2], 0.0, dr[0]]
        parts = []
        for k, sy in enumerate((-1.0, 1.0), 1):
            yw = sy * (self.graf / 2.0 + rp["t"])                 # outside face of the web of this channel (the flanges turn outward)
            y0 = yw if sy > 0 else yw - tp
            o = [-Lp / 2.0 * dr[0] - dp / 2.0 * nrm[0], y0, -Lp / 2.0 * dr[2] - dp / 2.0 * nrm[2]]
            parts.append(box("splice-plate-%d" % k, "splice_plate", sp["plate_material"], o, [Lp, tp, dp], x_axis=dr, y_axis=Y, role="web splice plate", thickness_in=tp))
            for side, sg in (("a", -1.0), ("b", 1.0)):
                for i, gx in enumerate((-gauge / 2.0, gauge / 2.0), 1):
                    for j in range(rows):
                        u, v = sg * Lp / 4.0 + gx, (j - (rows - 1) / 2.0) * pitch
                        yb = min(yw, yw - sy * rp["t"], yw + sy * tp) - 0.75
                        parts.append(cyl("bolt-%d%s-%d-%d" % (k, side, i, j + 1), "bolt", sp["bolt_material"], [u * dr[0] + v * nrm[0], yb, u * dr[2] + v * nrm[2]], Y, rp["t"] + tp + 1.5, d / 2.0, diameter_in=d,
                                         role="beam web to splice plate"))
        receivers = [dict(id="rafter1", kind="member", axis=[[-24.0 * dr[0], 0.0, -24.0 * dr[2]], [0.0, 0.0, 0.0]]), dict(id="rafter2", kind="member", axis=[[0.0, 0.0, 0.0], [24.0 * dr[0], 0.0, 24.0 * dr[2]]])]
        tpl = r.template("BEAM-SPLICE", parts, receivers, [sp["record"]], "Web splice: one %g in. plate %g in. x %g in. on each channel web, %d x %g in. bolts each side of the splice in each web (2 lines at %g in., %d rows at %g in.)"
                         % (tp, dp, Lp, 2 * rows, d, gauge, rows, pitch))
        r.instance("C:FR%02d:splice:%d" % (fi, int(round(P[0]))), jid, tpl, P, [1.0, 0.0, 0.0], Y, dict(rafter1=ra, rafter2=rb), [sp["record"]], 1, family="beam splice")

    def sbmf_joint(self, fi, jid, P, mid, rid, dr, into, interior=False):
        """Eight bolts through the two beam webs and the two walls of the column; the bolt group centre is the joint point."""
        r = self.r; js = need(self.fr, "joint", "frame"); cp, rp = self.cp, self.rp
        rec = js.get("record_interior") if (interior and js.get("record_interior")) else js["record"]
        d, a, b, c = [float(need(js, k, "joint")) for k in ("bolt_d_in", "a_in", "b_in", "c_in")]
        nrm = [-dr[2], 0.0, dr[0]]
        Lb = cp["flange"] + 2.0 * rp["t"] + 2.5
        parts = []
        for i, sx in enumerate((-1.0, 1.0), 1):
            for j, v in enumerate((-(b / 2.0 + a), -b / 2.0, b / 2.0, b / 2.0 + a), 1):
                o = [sx * c / 2.0 * dr[0] + v * nrm[0], -Lb / 2.0, sx * c / 2.0 * dr[2] + v * nrm[2]]
                parts.append(cyl("bolt-%d-%d" % (i, j), "bolt", js["bolt_material"], o, Y, Lb, d / 2.0, diameter_in=d, role="beam webs to column walls, snug-tight"))
        receivers = [dict(id="column", kind="member", axis=[[0.0, 0.0, -24.0], [0.0, 0.0, 0.0]]), dict(id="rafter", kind="member", axis=[[0.0, 0.0, 0.0], [24.0 * into[0], 0.0, 24.0 * into[2]]])]
        tpl = r.template(("SBMF-JOINT-I" if interior else "SBMF-JOINT") if into is dr else "SBMF-JOINT-R", parts, receivers, [rec],
                         "8 x %g in. snug-tight high-strength bolts through both beam webs and both walls of the column: two vertical lines %g in. apart, four bolts in each line at %g in., %g in. and %g in." % (d, c, a, b, a))
        r.instance("C:FR%02d:joint:%d" % (fi, int(round(P[0]))), jid, tpl, P, [1.0, 0.0, 0.0], Y, dict(column=mid, rafter=rid), [rec], 1, family="bolted moment connection")

    # ---- open canopy: box columns on fixed bases, continuous rafter over the column heads, knee braces ----
    def free_end(self, mid, end, P, level, reason):
        jid = self.r.joint("J:%s:%s" % (mid, "tipA" if end == "start" else "tipB"), P, level, ends=[(mid, end)])
        self.r.decl["joint_dispositions"].append(dict(joint_id=jid, kind="free_end", reason=reason))

    def weld(self, pid, material, origin, size, leg, role, **kw):
        return box(pid, "fillet_weld", material, origin, size, role=role, leg_in=leg, **kw)

    def canopy_frames(self, by_frame):
        r = self.r; fr = self.fr
        cprof, cp = self.built_up(need(fr, "column", "frame")); rprof, rp = self.built_up(need(fr, "rafter", "frame")); kprof, kp = self.built_up(need(fr, "knee_brace", "frame"))
        self.cp, self.rp, self.kp = cp, rp, kp
        self.ncol, self.nraf, self.nkb = int(fr["column"]["n"]), int(fr["rafter"]["n"]), int(fr["knee_brace"]["n"])
        self.gcol, self.graf, self.gkb = [float((fr[k].get("interconnect") or {}).get("gap_in", 0.0)) for k in ("column", "rafter", "knee_brace")]
        self.box_u = self.pair_offsets(self.ncol, cp["flange"], self.gcol)          # seam planes of the column boxes
        for fi in sorted(by_frame):
            cs = by_frame[fi]
            rafs = [c for c in cs if c["kind"] == "rafter"]
            pts = sorted([[float(v) for v in q] for c in rafs for q in c["axis"]], key=lambda q: q[0])
            A, B = pts[0], pts[-1]
            rid = "FR%02d:raf" % fi
            r.member(rid, "rafter", [A, B], rprof, fr["rafter"]["material"], Y, 1, [need(fr["rafter"], "record", "rafter")], analysis_ids=sorted({a for c in rafs for a in c["analysis_ids"]}), frame=fi, role="canopy rafter, continuous over the column heads",
                     reference_line="section centroid")
            self.rafters[fi].append((rid, A, B)); self.axis[rid] = (A, B)
            self.interconnect(rid, (A, B), fr["rafter"], rp, fi, False)
            for end, P in (("start", A), ("end", B)):
                self.free_end(rid, end, P, 1, "Rafter overhang tip: free end of the cantilever beyond the column")
            dr = unit([B[0] - A[0], 0.0, B[2] - A[2]])
            for c in cs:
                if c["kind"] != "column":
                    continue
                label = c["id"].split(":")[4]
                P0, P1 = [float(v) for v in c["axis"][0]], [float(v) for v in c["axis"][1]]
                mid = "FR%02d:%s" % (fi, label)
                r.member(mid, "column", [P0, P1], cprof, fr["column"]["material"], Y, 0, [need(fr["column"], "record", "column")], analysis_ids=c["analysis_ids"], frame=fi, role="canopy column (box), fixed base", reference_line="section centroid")
                lo, hi = (P0, P1) if P0[2] <= P1[2] else (P1, P0)
                self.columns[fi].append((mid, lo, hi)); self.axis[mid] = (P0, P1)
                e_lo, e_hi = ("start", "end") if P0[2] <= P1[2] else ("end", "start")
                self.battens(mid, lo, hi)
                jb = r.joint("J:FR%02d:%d:0" % (fi, int(round(lo[0]))), lo, 0, ends=[(mid, e_lo)])
                self.base_fixed(fi, jb, lo, mid)
                t_, o_ = self.att((A, B), hi)
                jh = r.joint("J:FR%02d:%d:%d" % (fi, int(round(hi[0])), int(round(hi[2]))), hi, 1, ends=[(mid, e_hi)], attachments=[(rid, t_, o_)])
                self.head(fi, jh, hi, mid, rid, dr)
            for c in cs:
                if c["kind"] != "knee_brace":
                    continue
                label = c["id"].split(":")[4]
                P0, P1 = [float(v) for v in c["axis"][0]], [float(v) for v in c["axis"][1]]
                kid = "FR%02d:%s" % (fi, label)
                r.member(kid, "knee_brace", [P0, P1], kprof, fr["knee_brace"]["material"], Y, 1, [need(fr["knee_brace"], "record", "knee brace")], analysis_ids=c["analysis_ids"], frame=fi, role="knee brace", reference_line="section centroid")
                self.axis[kid] = (P0, P1)
                self.interconnect(kid, (P0, P1), fr["knee_brace"], kp, fi, False)
                for end, P, Q in (("start", P0, P1), ("end", P1, P0)):
                    dk = unit([Q[0] - P[0], 0.0, Q[2] - P[2]])
                    col = next((m_ for m_, lo, hi in self.columns[fi] if abs(lo[0] - P[0]) < 0.5 and lo[2] - 0.5 <= P[2] <= hi[2] + 0.5), None)
                    host = col or rid
                    t_, o_ = self.att(self.axis[host], P)
                    jid = r.joint("J:%s:%s" % (kid, "A" if end == "start" else "B"), P, 1, ends=[(kid, end)], attachments=[(host, t_, o_)])
                    self.kb_end(fi, jid, P, kid, end, host, bool(col), dk, dr)

    def battens(self, mid, lo, hi):
        """Batten plates between the lips of each column box, welded to both lips, at both seams."""
        r = self.r; sel = self.fr["column"]; bt = need(sel, "batten", "column"); cp = self.cp
        L = math.dist(lo, hi); sp = float(need(bt, "spacing_in", "batten")); Lb = float(bt.get("length_in", 4.0)); tp = float(need(bt, "plate_t_in", "batten")); leg = float(bt.get("weld_leg_in", 0.125))
        m = max(1, int(math.ceil((L - 48.0) / sp - 1e-9)))
        xs = [24.0 + (L - 48.0) * i / m for i in range(m + 1)]
        zc = cp["depth"] / 2.0 - cp["t"] - 0.45
        parts = []
        for i, x in enumerate(xs, 1):
            for k, u in enumerate(self.box_u, 1):
                for j, sg in enumerate((-1.0, 1.0), 1):
                    parts.append(box("batten-%02d-%d-%d" % (i, k, j), "spacer_plate", bt["plate_material"], [x - Lb / 2.0, u - tp / 2.0, sg * zc - 0.6], [Lb, tp, 1.2], role="batten plate between the lips of a column box", thickness_in=tp))
                    for w_, sy in enumerate((-1.0, 1.0), 1):
                        parts.append(self.weld("weld-%02d-%d-%d-%d" % (i, k, j, w_), bt["weld_material"], [x - Lb / 2.0, u + sy * tp / 2.0 - (leg if sy < 0 else 0.0), sg * zc - leg / 2.0], [Lb, leg, leg], leg, "batten plate to channel lip"))
        receivers = [dict(id="member", kind="member", axis=[[0.0, 0.0, 0.0], [L, 0.0, 0.0]])]
        tpl = r.template("BOX-BATTEN", parts, receivers, [sel["record"]], "%g in. batten plates between the lips of each box at both seams, %g in. long at %g in. o.c. maximum (%d stations), %g in. fillet welds to both lips" % (tp, Lb, sp, len(xs), leg))
        jid = r.joint("J:%s:battens" % mid, lo, 0, attachments=[(mid, self.att(self.axis[mid], lo)[0], [0.0, 0.0, 0.0])])
        r.instance("C:%s:battens" % mid, jid, tpl, lo, unit([hi[i] - lo[i] for i in range(3)]), Y, dict(member=mid), [sel["record"]], 0, family="built-up member interconnection")

    def base_fixed(self, fi, jid, P, mid):
        r = self.r; bs = need(self.fr, "base", "frame"); cp = self.cp
        tp = float(need(bs, "plate_t_in", "base")); pw, pl = [float(v) for v in need(bs, "plate_in", "base")]
        d, na, emb = float(need(bs, "anchor_d_in", "base")), int(need(bs, "n_anchors", "base")), float(need(bs, "embed_in", "base"))
        sx, sy = [float(v) for v in need(bs, "anchor_pattern_in", "base")]
        tf, hf, leg = float(need(bs, "fin_t_in", "base")), float(need(bs, "fin_h_in", "base")), float(need(bs, "weld_leg_in", "base"))
        legp = float(bs.get("perimeter_weld_leg_in", leg))
        depth, B = cp["depth"], cp["flange"]
        parts = [box("base-plate", "base_plate", bs["plate_material"], [-pw / 2.0, -pl / 2.0, -tp], [pw, pl, tp], role="base plate", thickness_in=tp)]
        cols = max(1, na // 2)
        for i in range(na):
            x = (-1.0 if i % 2 == 0 else 1.0) * sx / 2.0
            y = 0.0 if cols == 1 else (-sy / 2.0 + (i // 2) * sy / (cols - 1))
            parts.append(cyl("anchor-%d" % (i + 1), "anchor_rod", bs["anchor_material"], [x, y, -tp - emb], Z, emb + tp + 3.0, d / 2.0, diameter_in=d, role="anchor rod"))
        xw = depth / 2.0 - cp["t"] - 0.45
        for k, u in enumerate(self.box_u, 1):
            parts.append(box("fin-%d" % k, "fin_plate", bs["plate_material"], [-depth / 2.0 - 1.0, u - tf / 2.0, 0.0], [depth + 2.0, tf, hf], role="base fin plate between the lips of a column box", thickness_in=tf))
            for j, sg in enumerate((-1.0, 1.0), 1):
                for w_, s2 in enumerate((-1.0, 1.0), 1):
                    parts.append(self.weld("weld-fin-%d-%d-%d" % (k, j, w_), bs["weld_material"], [sg * xw - leg / 2.0, u + s2 * tf / 2.0 - (leg if s2 < 0 else 0.0), 1.0], [leg, leg, hf - 1.0], leg, "fin plate to channel lip"))
            w1 = B + self.gcol / 2.0
            for j, (ox, oy, lx, ly) in enumerate(((-depth / 2.0 - legp, u - w1, legp, 2.0 * w1), (depth / 2.0, u - w1, legp, 2.0 * w1), (-depth / 2.0, u - w1 - legp, depth, legp), (-depth / 2.0, u + w1, depth, legp)), 1):
                parts.append(self.weld("weld-base-%d-%d" % (k, j), bs["weld_material"], [ox, oy, 0.0], [lx, ly, legp], legp, "column box to base plate, all round"))
        receivers = [dict(id="column", kind="member", axis=[[0.0, 0.0, 0.0], [0.0, 0.0, 24.0]]), dict(id="foundation", kind="interface")]
        tpl = r.template("BASE-FIXED", parts, receivers, [bs["record"]], "Fixed column base: %g in. base plate on %d x %g in. anchor rods, one %g in. fin plate per column box welded to the four lips over %g in., column welded to the base plate all round" % (tp, na, d, tf, hf))
        r.instance("C:FR%02d:base:%d" % (fi, int(round(P[0]))), jid, tpl, P, [1.0, 0.0, 0.0], Y, dict(column=mid, foundation=self.foundation()), [bs["record"]], 0, family="column base")

    def head(self, fi, jid, P, mid, rid, dr):
        """Head plate of each column box: welded between the lips of the box, bolted between the webs of a rafter pair."""
        r = self.r; js = need(self.fr, "head", "frame"); cp, rp = self.cp, self.rp
        tg, d = float(need(js, "plate_t_in", "head")), float(need(js, "bolt_d_in", "head"))
        rows, gauge, pitch = int(need(js, "rows", "head")), float(need(js, "gauge_in", "head")), float(need(js, "pitch_in", "head"))
        leg, Lw = float(need(js, "weld_leg_in", "head")), float(need(js, "weld_len_in", "head"))
        nrm = [-dr[2], 0.0, dr[0]]
        a0 = cp["depth"] / 2.0 + 3.0
        Lleg = a0 + (rows - 1) * pitch + 2.0
        xw = cp["depth"] / 2.0 - cp["t"] - 0.45
        parts = []
        for k, u in enumerate(self.box_u, 1):
            o = [-Lleg * dr[0] - rp["depth"] / 2.0 * nrm[0], u - tg / 2.0, -Lleg * dr[2] - rp["depth"] / 2.0 * nrm[2]]
            parts.append(box("head-rafter-%d" % k, "gusset_plate", js["plate_material"], o, [2.0 * Lleg, tg, rp["depth"]], x_axis=dr, y_axis=Y, role="head plate between the webs of a rafter pair", thickness_in=tg))
            parts.append(box("head-column-%d" % k, "gusset_plate", js["plate_material"], [-cp["depth"] / 2.0 - 1.0, u - tg / 2.0, 0.0], [Lw + 2.0, tg, cp["depth"] + 2.0], x_axis=[0.0, 0.0, -1.0], y_axis=Y,
                             role="head plate between the lips of a column box", thickness_in=tg))
            for side, sg in (("a", 1.0), ("b", -1.0)):
                parts += self.leg_bolts("%s%d" % (side, k), u, [sg * v for v in dr], nrm, a0, rows, pitch, gauge, d, js["bolt_material"], "rafter webs to head plate")
            for j, sx_ in enumerate((-1.0, 1.0), 1):
                for w_, s2 in enumerate((-1.0, 1.0), 1):
                    parts.append(self.weld("weld-%d-%d-%d" % (k, j, w_), js["weld_material"], [sx_ * xw - leg / 2.0, u + s2 * tg / 2.0 - (leg if s2 < 0 else 0.0), -1.0 - Lw], [leg, leg, Lw], leg, "head plate to channel lip"))
        receivers = [dict(id="column", kind="member", axis=[[0.0, 0.0, -24.0], [0.0, 0.0, 0.0]]), dict(id="rafter", kind="member", axis=[[-12.0 * dr[0], 0.0, -12.0 * dr[2]], [12.0 * dr[0], 0.0, 12.0 * dr[2]]])]
        tpl = r.template("HEAD", parts, receivers, [js["record"]], "One %g in. head plate per column box welded to the four lips over %g in. (%g in. fillet welds), %d x %g in. bolts through the webs of each rafter pair each side of the column (2 lines at %g in., %d rows at %g in.)" % (
            tg, Lw, leg, 2 * rows, d, gauge, rows, pitch))
        r.instance("C:FR%02d:head:%d" % (fi, int(round(P[0]))), jid, tpl, P, [1.0, 0.0, 0.0], Y, dict(column=mid, rafter=rid), [js["record"]], 1, family="column head connection")

    def kb_end(self, fi, jid, P, kid, end, host, at_column, dk, dr):
        r = self.r; ks = need(self.fr, "kb_end", "frame"); cp, rp, kp = self.cp, self.rp, self.kp
        tg, d = float(need(ks, "plate_t_in", "kb_end")), float(need(ks, "bolt_d_in", "kb_end"))
        nb, nr = int(need(ks, "n_bolts_brace", "kb_end")), int(need(ks, "n_bolts_rafter", "kb_end"))
        leg, Lw = float(need(ks, "weld_leg_in", "kb_end")), float(need(ks, "weld_len_in", "kb_end"))
        nk = [-dk[2], 0.0, dk[0]]
        gauge = max(2.5, kp["depth"] - 3.0); rows = max(1, nb // 2); pitch = 3.0
        if at_column:
            a0 = (cp["depth"] / 2.0) / max(abs(dk[0]), 0.2) + 2.5
        else:
            a0 = (rp["depth"] / 2.0) / max(abs(dk[0] * (-dr[2]) + dk[2] * dr[0]), 0.2) + 2.5
        Lg = a0 + (rows - 1) * pitch + 2.0
        xw = cp["depth"] / 2.0 - cp["t"] - 0.45
        parts = []
        for k, u in enumerate(self.box_u, 1):
            o = [-(kp["depth"] / 2.0 + 1.0) * nk[0], u - tg / 2.0, -(kp["depth"] / 2.0 + 1.0) * nk[2]]
            parts.append(box("gusset-brace-%d" % k, "gusset_plate", ks["plate_material"], o, [Lg, tg, kp["depth"] + 2.0], x_axis=dk, y_axis=Y, role="knee brace gusset between the webs of a brace pair", thickness_in=tg))
            parts += self.leg_bolts("k%d" % k, u, dk, nk, a0, rows, pitch, gauge, d, ks["bolt_material"], "brace webs to gusset plate")
            if at_column:
                parts.append(box("fin-%d" % k, "fin_plate", ks["plate_material"], [cp["depth"] / 2.0 + 1.0, u - tg / 2.0, -Lw / 2.0], [Lw, tg, cp["depth"] + 2.0], x_axis=[0.0, 0.0, 1.0], y_axis=Y, role="fin plate between the lips of a column box", thickness_in=tg))
                for j, sx_ in enumerate((-1.0, 1.0), 1):
                    for w_, s2 in enumerate((-1.0, 1.0), 1):
                        parts.append(self.weld("weld-%d-%d-%d" % (k, j, w_), ks["weld_material"], [sx_ * xw - leg / 2.0, u + s2 * tg / 2.0 - (leg if s2 < 0 else 0.0), -Lw / 2.0], [leg, leg, Lw], leg, "fin plate to channel lip"))
            else:
                nrm = [-dr[2], 0.0, dr[0]]
                rr = max(1, nr // 2); L2 = (rr - 1) * pitch / 2.0 + 2.5
                o2 = [-L2 * dr[0] - rp["depth"] / 2.0 * nrm[0], u - tg / 2.0, -L2 * dr[2] - rp["depth"] / 2.0 * nrm[2]]
                parts.append(box("gusset-rafter-%d" % k, "gusset_plate", ks["plate_material"], o2, [2.0 * L2, tg, rp["depth"]], x_axis=dr, y_axis=Y, role="knee brace gusset between the webs of a rafter pair", thickness_in=tg))
                parts += self.leg_bolts("r%d" % k, u, dr, nrm, -(rr - 1) * pitch / 2.0, rr, pitch, max(2.5, rp["depth"] - 5.0), d, ks["bolt_material"], "rafter webs to gusset plate")
        hax = [[0.0, 0.0, -6.0], [0.0, 0.0, 6.0]] if at_column else [[-6.0 * dr[0], 0.0, -6.0 * dr[2]], [6.0 * dr[0], 0.0, 6.0 * dr[2]]]
        receivers = [dict(id="brace", kind="member", axis=[[0.0, 0.0, 0.0], [24.0 * dk[0], 0.0, 24.0 * dk[2]]]), dict(id="host", kind="member", axis=hax)]
        if at_column:
            basis = "Knee brace to column: one %g in. fin plate per column box welded to the four lips over %g in. (%g in. fillet welds), %d x %g in. bolts through the webs of each brace pair" % (tg, Lw, leg, nb, d)
        else:
            basis = "Knee brace to rafter: one %g in. gusset per rafter pair with %d x %g in. bolts through the rafter webs and %d x %g in. bolts through the webs of the brace pair" % (tg, nr, d, nb, d)
        rec = ks.get("record_column" if at_column else "record_rafter") or need(ks, "record", "kb_end")
        tpl = r.template("KB-END-COLUMN" if at_column else "KB-END-RAFTER", parts, receivers, [rec], basis)
        r.instance("C:%s:%s" % (kid, "A" if end == "start" else "B"), jid, tpl, P, [1.0, 0.0, 0.0], Y, dict(brace=kid, host=host), [rec], 1, family="knee brace end connection")

    @staticmethod
    def pair_offsets(n, b, gap=0.0, sp=None):
        """Out-of-plane positions of the mid-plane between the two webs of each back-to-back pair (or of the seam plane of each toe-to-toe box).
        sp: centre-to-centre spacing of the pairs where they are set apart to line up with another member (default: flange tips touching)."""
        m = max(1, n // 2)
        return [(k - (m - 1) / 2.0) * (sp or (2.0 * b + gap)) for k in range(m)]

    def half_width(self, n, p, gap):
        if p.get("half_width") is not None:
            return p["half_width"]
        if n < 2:
            return p["flange"]
        m = n // 2
        return (m - 1) * (p.get("pair_spacing") or (2.0 * p["flange"] + gap)) / 2.0 + p["flange"] + gap / 2.0

    def interconnect(self, mid, axis, sel, p, fi, col):
        """Spacer plates and bolts that make each back-to-back pair act as one I-section."""
        ic = sel.get("interconnect")
        n = int(sel["n"])
        if not ic or n < 2:
            return
        r = self.r
        A, B = axis; L = math.dist(A, B)
        gap, sp, d, gauge = float(ic["gap_in"]), float(need(ic, "spacing_in", "interconnect")), float(need(ic, "bolt_d_in", "interconnect")), float(need(ic, "gauge_in", "interconnect"))
        m = max(1, int(math.ceil((L - 36.0) / sp - 1e-9)))
        xs = [18.0 + (L - 36.0) * i / m for i in range(m + 1)]
        parts = []
        for i, x in enumerate(xs, 1):
            for k, u in enumerate(self.pair_offsets(n, p["flange"], gap, p.get("pair_spacing")), 1):
                if gap > 0:
                    parts.append(box("spacer-%02d-%d" % (i, k), "spacer_plate", ic["plate_material"], [x - 2.0, u - gap / 2.0, -(gauge + 3.0) / 2.0], [4.0, gap, gauge + 3.0], role="spacer plate between the webs of a pair", thickness_in=gap))
                for j, sg in enumerate((-1.0, 1.0), 1):
                    parts.append(cyl("bolt-%02d-%d-%d" % (i, k, j), "bolt", ic["bolt_material"], [x, u - gap / 2.0 - 0.75, sg * gauge / 2.0], Y, gap + 1.5, d / 2.0, diameter_in=d, role="pair interconnection"))
        receivers = [dict(id="member", kind="member", axis=[[0.0, 0.0, 0.0], [L, 0.0, 0.0]])]
        tpl = r.template("PAIR-INTERCONNECT", parts, receivers, [sel["record"]],
                         "%g in. spacer plates between the webs of each back-to-back pair with 2 x %g in. bolts at %g in. o.c. maximum (%d stations)" % (gap, d, sp, len(xs)))
        d_ = unit([B[i] - A[i] for i in range(3)])
        jid = r.joint("J:%s:interconnect" % mid, A, 0 if col else 1, attachments=[(mid, 0.0, [0.0, 0.0, 0.0])])
        r.instance("C:%s:interconnect" % mid, jid, tpl, A, d_, Y, dict(member=mid), [sel["record"]], 0 if col else 1, family="built-up member interconnection")

    def leg_bolts(self, tag, u, direction, normal, a0, rows, pitch, gauge, d, material, role):
        """Two lines of bolts along a member leg in the frame plane, through the web pair at out-of-plane offset u."""
        parts = []
        for i in range(rows):
            for j, sg in enumerate((-1.0, 1.0)):
                a = a0 + i * pitch
                p = [a * direction[0] + sg * gauge / 2.0 * normal[0], u - 1.25, a * direction[2] + sg * gauge / 2.0 * normal[2]]
                parts.append(cyl("bolt-%s-%d-%d" % (tag, i + 1, j + 1), "bolt", material, p, Y, 2.5, d / 2.0, diameter_in=d, role=role))
        return parts

    def base(self, fi, jid, P, e):
        r = self.r; bs = need(self.fr, "base", "frame")
        tp = float(need(bs, "plate_t_in", "base")); pw, pl = [float(v) for v in need(bs, "plate_in", "base")]
        d, na, emb = float(need(bs, "anchor_d_in", "base")), int(need(bs, "n_anchors", "base")), float(need(bs, "embed_in", "base"))
        tc, db, nb = float(need(bs, "cleat_t_in", "base")), float(need(bs, "bolt_d_in", "base")), int(need(bs, "bolts_per_pair", "base"))
        depth, b = self.cp["depth"], self.cp["flange"]
        parts = [box("base-plate", "base_plate", bs["plate_material"], [-pw / 2.0, -pl / 2.0, -tp], [pw, pl, tp], role="base plate", thickness_in=tp)]
        cols = max(1, na // 2)
        for i in range(na):
            x = (-1.0 if i % 2 == 0 else 1.0) * (pw / 2.0 - 2.0)
            y = 0.0 if cols == 1 else (-pl / 2.0 + 2.0 + (i // 2) * (pl - 4.0) / (cols - 1))
            parts.append(cyl("anchor-%d" % (i + 1), "anchor_rod", bs["anchor_material"], [x, y, -tp - emb], Z, emb + tp + 2.5, d / 2.0, diameter_in=d, role="anchor rod"))
        for k, u in enumerate(self.pair_offsets(self.ncol, b, self.gcol), 1):
            parts.append(box("cleat-%d" % k, "cleat_plate", bs["plate_material"], [-(depth - 2.0) / 2.0, u - tc / 2.0, 0.0], [depth - 2.0, tc, 4.0 + 3.0 * max(1, nb // 2)], role="base cleat between the webs of a column pair", thickness_in=tc))
            parts += self.leg_bolts("c%d" % k, u, [0.0, 0.0, 1.0], [1.0, 0.0, 0.0], 2.5, max(1, nb // 2), 3.0, depth - 5.0, db, bs["bolt_material"], "column webs to base cleat")
        receivers = [dict(id="column", kind="member", axis=[[0.0, 0.0, 0.0], [0.0, 0.0, 24.0]]), dict(id="foundation", kind="interface")]
        tpl = r.template("BASE", parts, receivers, [bs["record"]], "Pinned column base: base plate on %d anchor rods, one cleat plate between the webs of each column pair bolted through the webs" % na)
        r.instance("C:FR%02d:base:%d" % (fi, int(round(P[0]))), jid, tpl, P, [1.0, 0.0, 0.0], Y, dict(column=e[0], foundation=self.foundation()), [bs["record"]], 0, family="column base")

    def bracket(self, key, legs, family, fi, jid, P, bindings):
        """Gusset plate between the webs of every back-to-back pair, bolted to each member leg. legs: [(receiver id, unit direction away from the joint, n plies, depth)]."""
        r = self.r; js = need(self.fr, key, "frame")
        tg, d = float(need(js, "plate_t_in", key)), float(need(js, "bolt_d_in", key))
        rows, gauge, pitch = int(need(js, "rows", key)), float(need(js, "gauge_in", key)), float(need(js, "pitch_in", key))
        npl = min(l[2] for l in legs)
        b = self.rp["flange"]
        parts, receivers = [], []
        for li, (rid, direction, _n, depth) in enumerate(legs, 1):
            normal = [-direction[2], 0.0, direction[0]]
            a0 = depth / 2.0 + 3.0
            Lleg = a0 + (rows - 1) * pitch + 3.0
            for k, u in enumerate(self.pair_offsets(npl, b, self.graf), 1):
                o = [-depth / 2.0 * direction[0] - depth / 2.0 * normal[0], u - tg / 2.0, -depth / 2.0 * direction[2] - depth / 2.0 * normal[2]]
                parts.append(box("gusset-%d-%d" % (li, k), "gusset_plate", js["plate_material"], o, [Lleg + depth / 2.0, tg, depth], x_axis=direction, y_axis=Y, role="leg of the gusset plate between the webs of a pair", thickness_in=tg, leg=li))
                parts += self.leg_bolts("%d-%d" % (li, k), u, direction, normal, a0, rows, pitch, gauge, d, js["bolt_material"], "member webs to gusset plate")
            receivers.append(dict(id=rid, kind="member", axis=[[0.0, 0.0, 0.0], [24.0 * direction[0], 0.0, 24.0 * direction[2]]]))
        tpl = r.template(key.upper(), parts, receivers, [js["record"]], "One gusset plate between the webs of each back-to-back pair, %d bolts to each member leg of each plate (2 lines at %g in., %d rows at %g in.)" % (2 * rows, gauge, rows, pitch))
        r.instance("C:FR%02d:%s:%d" % (fi, key, int(round(P[0]))), jid, tpl, P, [1.0, 0.0, 0.0], Y, bindings, [js["record"]], 1, family=family)

    def knee(self, fi, jid, P, es, key="knee"):
        legs, bind = [], {}
        for i, (mid, end, kind, Q, _P) in enumerate(es, 1):
            direction = unit([Q[0] - P[0], 0.0, Q[2] - P[2]])
            rid = "column" if kind == "column" else "rafter%d" % i
            legs.append((rid, direction, self.ncol if kind == "column" else self.nraf, self.cp["depth"] if kind == "column" else self.rp["depth"]))
            bind[rid] = mid
        self.bracket(key, legs, "%s bracket" % key, fi, jid, P, bind)

    def apex(self, fi, jid, P, es):
        legs, bind = [], {}
        for i, (mid, end, kind, Q, _P) in enumerate(es, 1):
            legs.append(("rafter%d" % i, unit([Q[0] - P[0], 0.0, Q[2] - P[2]]), self.nraf, self.rp["depth"])); bind["rafter%d" % i] = mid
        self.bracket("apex", legs, "apex bracket", fi, jid, P, bind)

    # ---- purlins and girts: one member per bay on each line, lapped and cleated at every frame ----
    @staticmethod
    def att(axis, P):
        """Station and offset of point P relative to a member axis."""
        A, B, P = r6(axis[0]), r6(axis[1]), r6(P)          # the recorded (6-decimal) geometry, so the declared station and offset reproduce the joint point
        d = [B[i] - A[i] for i in range(3)]; L2 = sum(v * v for v in d)
        t = sum((P[i] - A[i]) * d[i] for i in range(3)) / L2
        t = round(min(1.0, max(0.0, t)), 12)
        return t, [P[i] - (A[i] + t * d[i]) for i in range(3)]

    def stations(self, L, sp, first, last):
        n = max(1, int(math.ceil((L - first - last) / sp - 1e-9)))
        return [first + (L - first - last) * i / n for i in range(n + 1)]

    def secondary(self, key, kind, hosts_by_frame, normal_of, host_p, host_n, first, last):
        """hosts_by_frame: {fi: [(host id, A, B)]} with the same host order in every frame."""
        r = self.r; sel = need(self.pd, key, "root")
        prof, p = self.zee(need(sel, "section", key)); dp = p["depth"]
        lap = float(sel.get("lap_ft", 0.0)) * 12.0
        sp = float(need(sel, "spacing_ft", key)) * 12.0
        ovh = float(sel.get("overhang_ft", 0.0)) * 12.0         # the lines run on as cantilevers beyond the end frames
        frames = sorted(hosts_by_frame)
        tag0 = "PU" if kind == "purlin" else "GI"
        lines = []
        geo = {}
        for hi in range(len(hosts_by_frame[frames[0]])):
            _m0, A0, B0 = hosts_by_frame[frames[0]][hi]
            Lh = math.dist(A0, B0)
            nrm = normal_of(A0, B0, hi)
            off = host_p["depth"] / 2.0 + dp / 2.0
            ly = cross(nrm, Y)                              # Y x ly = nrm: the web of the Z stands along the outward normal
            for si, s in enumerate(self.stations(Lh, sp, first, last), 1):
                line = "%s:H%d:L%02d" % (tag0, hi + 1, si)
                Pf = {fi: add(lerp(hosts_by_frame[fi][hi][1], hosts_by_frame[fi][hi][2], s / Lh), nrm, off) for fi in frames}
                zline = Pf[frames[0]][2]
                skip = set()
                for om in sel.get("omit") or []:            # door / opening bays: no member on the lines below the opening head
                    if abs(self.columns[frames[0]][int(om["line"]) - 1][1][0] - A0[0]) < 1.0 and zline < float(om.get("below_ft", 1e9)) * 12.0:
                        skip.update(int(b) for b in om["bays"])
                line_mids = {}
                runs, cur = [], []
                for bi in range(1, len(frames)):
                    if bi in skip:
                        if cur:
                            runs.append(cur); cur = []
                    else:
                        cur.append(bi)
                if cur:
                    runs.append(cur)
                for run in runs:
                    mids, axes = {}, {}
                    for bi in run:
                        fa, fb = frames[bi - 1], frames[bi]
                        A = add(Pf[fa], Y, -lap if bi > run[0] else (-ovh if bi == 1 else 0.0))
                        B = add(Pf[fb], Y, lap if bi < run[-1] else (ovh if bi == len(frames) - 1 else 0.0))
                        mid = r.member("%s:B%02d" % (line, bi), kind, [A, B], prof, sel["material"], ly, 1, [need(sel, "record", key)], bay=bi, line=si, role="roof purlin" if kind == "purlin" else "wall girt",
                                       reference_line="section centroid", host_line=hi + 1)
                        mids[bi] = mid; axes[mid] = (A, B); line_mids[bi] = (mid, A, B)
                    for k in range(run[0] - 1, run[-1] + 1):       # frame index k (0-based) borders bays k and k + 1
                        fi = frames[k]
                        before, after = mids.get(k), mids.get(k + 1)
                        host = hosts_by_frame[fi][hi][0]
                        P = Pf[fi]
                        th, oh = self.att(self.axis[host], P)
                        ends, atts, bind = [], [(host, th, oh)], dict(host=host)
                        if before and after:
                            for m_, rid in ((before, "before"), (after, "after")):
                                t_, o_ = self.att(axes[m_], P); atts.append((m_, t_, o_)); bind[rid] = m_
                        elif after:
                            if ovh and k == 0:
                                t_, o_ = self.att(axes[after], P); atts.append((after, t_, o_)); self.free_end(after, "start", axes[after][0], 1, "Cantilever tip of the %s beyond the end frame" % kind)
                            else:
                                ends.append((after, "start"))
                            bind["after"] = after
                        else:
                            if ovh and k == len(frames) - 1:
                                t_, o_ = self.att(axes[before], P); atts.append((before, t_, o_)); self.free_end(before, "end", axes[before][1], 1, "Cantilever tip of the %s beyond the end frame" % kind)
                            else:
                                ends.append((before, "end"))
                            bind["before"] = before
                        tpl = self.support_template(key, kind, sel, bool(before), bool(after), lap, off, dp, p["t"], host_p, host_n)
                        jid = r.joint("J:%s:F%02d" % (line, fi), P, 1, ends=ends, attachments=atts)
                        cid = r.instance("C:%s:F%02d" % (line, fi), jid, tpl, P, Y, nrm, bind, [sel["record"]], 1, family="%s support" % kind)
                        if before and after and lap > 0:
                            for m_, end, other, sg in ((before, "end", after, 1.0), (after, "start", before, -1.0)):
                                Q = add(P, Y, sg * lap)
                                t_, o_ = self.att(axes[other], Q)
                                jl = r.joint("J:%s:F%02d:lap%s" % (line, fi, "B" if sg > 0 else "A"), Q, 1, ends=[(m_, end)], attachments=[(other, t_, o_)])
                                r.shared(jl, [cid], "Lap end bolted through both nested webs by the lap bolts of this support assembly")
                lines.append(line)
                geo.setdefault(hi, []).append(dict(si=si, Pf=Pf, mids=line_mids, nrm=nrm))
        if sel.get("bridging"):
            for hi, glines in sorted(geo.items()):
                self.bridging(kind, sel, hi, glines, frames, dp, need(sel["bridging"], "record", "bridging"))
        return lines

    def bridging(self, kind, sel, hi, glines, frames, dp, rec):
        """Discrete brace lines: a flat strap on each flange crossing every purlin / girt of a host line, in every bay."""
        r = self.r; bg = sel["bridging"]
        w, t = float(need(bg, "width_in", "bridging")), float(need(bg, "t_in", "bridging"))
        prof = r.flat(w, t)
        nl, ns, rad = int(need(bg, "n_lines", "bridging")), int(need(bg, "n_screws", "bridging")), float(need(bg, "screw_d_in", "bridging")) / 2.0
        glines = sorted(glines, key=lambda g_: g_["si"])
        nrm = glines[0]["nrm"]
        for bi in range(1, len(frames)):
            fa, fb = frames[bi - 1], frames[bi]
            present = [g_ for g_ in glines if bi in g_["mids"]]
            if len(present) < 2:
                continue
            for j in range(1, nl + 1):
                f = j / (nl + 1.0)
                for flange, sg in (("O", 1.0), ("I", -1.0)):
                    zoff = sg * (dp / 2.0 + t / 2.0)
                    pts = [add(lerp(g_["Pf"][fa], g_["Pf"][fb], f), nrm, zoff) for g_ in present]
                    A, B = pts[0], pts[-1]
                    xa = unit([B[i] - A[i] for i in range(3)])
                    ya = unit(cross(nrm, xa))                 # strap width direction; local z = xa x ya = nrm
                    mid = r.member("BG:%s:H%d:B%02d:L%d%s" % ("PU" if kind == "purlin" else "GI", hi + 1, bi, j, flange), "bridging", [A, B], prof, bg["material"], ya, 1, [rec], bay=bi, host_line=hi + 1,
                                   role="%s brace strap" % kind, reference_line="strap centreline", plane="roof" if kind == "purlin" else "wall", flange="outer" if sg > 0 else "inner")
                    parts, receivers, bind = [], [dict(id="strap", kind="member", axis=[[0.0, 0.0, 0.0], [math.dist(A, B), 0.0, 0.0]])], dict(strap=mid)
                    for i, (g_, P) in enumerate(zip(present, pts), 1):
                        x = math.dist(A, P)
                        for k in range(ns):
                            parts.append(cyl("scr-%02d-%d" % (i, k + 1), "screw", bg["screw_material"], [x + (k - (ns - 1) / 2.0) * 0.75, 0.0, sg * 0.2], [0.0, 0.0, -sg], 0.6, rad, role="brace strap to %s flange" % kind))
                        mm, MA, MB = g_["mids"][bi]
                        hy = sum((MB[q] - MA[q]) * ya[q] for q in range(3)); hy = 1.0 if hy >= 0 else -1.0
                        receivers.append(dict(id="m%d" % i, kind="member", axis=[[r6([x])[0], -6.0 * hy, -zoff], [r6([x])[0], 6.0 * hy, -zoff]])); bind["m%d" % i] = mm
                    tpl = r.template("%s-BRIDGING" % kind.upper(), parts, receivers, [rec], "%g in. x %.4f in. flat strap across the %s flanges of %d %ss, %d screws to each flange" % (w, t, "outer" if sg > 0 else "inner", len(present), kind, ns))
                    ja = jb = None
                    for end, P, g_ in (("start", A, present[0]), ("end", B, present[-1])):
                        mm, MA, MB = g_["mids"][bi]
                        t_, o_ = self.att((MA, MB), P)
                        jid = r.joint("J:%s:%s" % (mid, "A" if end == "start" else "B"), P, 1, ends=[(mid, end)], attachments=[(mm, t_, o_)])
                        if end == "start":
                            ja = jid
                        else:
                            jb = jid
                    cid = r.instance("C:%s" % mid, ja, tpl, A, xa, ya, bind, [rec], 1, family="%s brace line" % kind)
                    r.shared(jb, [cid], "Far end of the brace strap, fastened by the last screw group of this brace-line assembly")


    def support_template(self, key, kind, sel, before, after, lap, off, dp, tw, host_p, host_n):
        """Local frame: x along the purlin / girt, y along the outward normal of the frame member, z along the frame member."""
        d = float(need(sel, "bolt_d_in", key)); fb = sel.get("flange_brace")
        tc = float(sel.get("cleat_t_in", 0.25))
        parts = [box("cleat", "cleat_plate", need(sel, "cleat_material", key), [-3.0, -dp / 2.0, tw / 2.0], [6.0, dp - 0.5, tc], role="support cleat on the frame member", thickness_in=tc)]
        for j, yy in enumerate((-dp / 4.0, dp / 4.0), 1):
            parts.append(cyl("bolt-c-%d" % j, "bolt", sel["bolt_material"], [0.0, yy, -0.75], Z, 1.75, d / 2.0, diameter_in=d, role="%s web%s to cleat" % (kind, "s" if before and after else "")))
        if before and after and lap > 0:
            for i, x in enumerate((-(lap - 1.5), lap - 1.5), 1):
                for j, yy in enumerate((-dp / 4.0, dp / 4.0), 1):
                    parts.append(cyl("bolt-l-%d-%d" % (i, j), "bolt", sel["bolt_material"], [x, yy, -0.75], Z, 1.5, d / 2.0, diameter_in=d, role="lap end, through both nested webs"))
        if host_n == 0:                                     # existing frame member: new cleat bolted to the existing flange, nothing else added to it
            for j, xx in enumerate((-2.0, 2.0), 1):
                parts.append(cyl("bolt-h-%d" % j, "bolt", sel["bolt_material"], [xx, -dp / 2.0 - 0.75, 1.5], [0.0, 1.0, 0.0], 1.5, d / 2.0, diameter_in=d, role="cleat to the existing flange"))
            receivers = [dict(id="host", kind="member", axis=[[0.0, -off, -3.0], [0.0, -off, 3.0]])]
            if before:
                receivers.append(dict(id="before", kind="member", axis=[[-12.0, 0.0, 0.0], [lap if after else 0.0, 0.0, 0.0]]))
            if after:
                receivers.append(dict(id="after", kind="member", axis=[[-lap if before else 0.0, 0.0, 0.0], [12.0, 0.0, 0.0]]))
            basis = "%s support on the existing frame: %g in. cleat bolted to the existing flange with 2 x %g in. bolts, 2 x %g in. bolts through the %s web%s and the cleat%s" % (
                kind.capitalize(), tc, d, d, kind, "s" if before and after else "", "; %ss lapped %g in. each side of the frame and bolted through both nested webs with 2 bolts at each lap end" % (kind, lap) if (before and after and lap > 0) else "")
            return self.r.template("%s-SUPPORT-EXISTING" % kind.upper(), parts, receivers, [sel["record"]], basis)
        gap = self.gcol if kind == "girt" else self.graf
        hw = self.half_width(host_n, host_p, gap)
        rad_s = float(sel.get("screw_d_in", 0.216)) / 2.0
        flanges = []
        for u in (self.pair_offsets(host_n, host_p["flange"], gap, host_p.get("pair_spacing")) if host_n > 1 else [0.0]):
            flanges += [u - gap / 2.0 - host_p["flange"] / 2.0, u + gap / 2.0 + host_p["flange"] / 2.0] if host_n > 1 else [host_p["flange"] / 2.0]
        parts.append(box("sole", "sole_plate", sel["cleat_material"], [-hw, -dp / 2.0, -3.0], [2.0 * hw, tc, 6.0], role="cleat sole plate across the outside flanges of the frame member", thickness_in=tc))
        for i, x in enumerate(flanges, 1):
            for j, zz in enumerate((-2.0, 2.0), 1):
                parts.append(cyl("scr-sole-%d-%d" % (i, j), "screw", sel["screw_material"], [x, -dp / 2.0 + tc + 0.1, zz], [0.0, -1.0, 0.0], 0.8, rad_s, role="sole plate to outside flange"))
        if fb:
            drop = host_p["depth"] + dp / 2.0
            tie = fb.get("tie")
            if tie and host_n > 1:
                tw_, tt = float(tie["width_in"]), float(tie["t_in"])
                parts.append(box("flange-tie", "tie_strap", tie["material"], [-hw, -drop - tt, -tw_ / 2.0], [2.0 * hw, tt, tw_], role="tie strap across the inside flanges of all pairs", thickness_in=tt, width_in=tw_))
                for i, x in enumerate(flanges, 1):
                    for j, zz in enumerate((-tw_ / 4.0, tw_ / 4.0), 1):
                        parts.append(cyl("scr-tie-%d-%d" % (i, j), "screw", sel["screw_material"], [x, -drop - tt - 0.1, zz], [0.0, 1.0, 0.0], 0.8, rad_s, role="tie strap to inside flange"))
            bw, bt = float(need(fb, "width_in", "flange_brace")), float(need(fb, "depth_in", "flange_brace"))
            sg = 1.0 if after else -1.0
            Q1 = [sg * (hw + drop), 0.0, -bw / 2.0]
            xa = unit([-sg * drop, -drop, 0.0])
            parts.append(box("flange-brace", "flange_brace", fb["material"], Q1, [math.hypot(drop, drop), bw, bt], x_axis=xa, y_axis=Z, role="flange brace from the %s web to the inside flange of the frame member" % kind,
                             section=fb["section"], length_in=round(math.hypot(drop, drop), 2)))
            ns, rad = int(need(fb, "n_screws", "flange_brace")), float(need(fb, "screw_d_in", "flange_brace")) / 2.0
            for e_, Q in (("p", [sg * (hw + drop - 1.0), -1.0, 0.0]), ("f", [sg * (hw + 1.0), -drop + 1.0, 0.0])):
                for i in range(ns):
                    parts.append(cyl("scr-%s-%d" % (e_, i + 1), "screw", fb["screw_material"], [Q[0] + sg * i * 0.75, Q[1] + i * 0.75, -0.4], Z, 0.8, rad, role="flange brace end"))
        receivers = [dict(id="host", kind="member", axis=[[0.0, -off, -3.0], [0.0, -off, 3.0]])]
        if before:
            receivers.append(dict(id="before", kind="member", axis=[[-12.0, 0.0, 0.0], [lap if after else 0.0, 0.0, 0.0]]))
        if after:
            receivers.append(dict(id="after", kind="member", axis=[[-lap if before else 0.0, 0.0, 0.0], [12.0, 0.0, 0.0]]))
        basis = "%s support: %g in. cleat plate on the frame member, 2 x %g in. bolts through the %s web%s and the cleat%s%s" % (
            kind.capitalize(), tc, d, kind, "s" if before and after else "",
            "; %ss lapped %g in. each side of the frame and bolted through both nested webs with 2 bolts at each lap end" % (kind, lap) if (before and after and lap > 0) else "",
            "; sole plate across the outside flanges with 2 screws to each flange" + ("; %s flange brace to the inside flange of the frame member with %d screws each end%s" % (fb["section"], int(fb["n_screws"]), "; tie strap across the inside flanges with 2 screws to each flange" if (fb.get("tie") and host_n > 1) else "") if fb else ""))
        return self.r.template("%s-SUPPORT" % kind.upper(), parts, receivers, [sel["record"]], basis)

    # ---- tension straps in the braced bays ----
    def strap_end(self, sel, rec, mid, end, P, host, haxis, out, zoff, direction):
        r = self.r
        n = int(need(sel, "n_screws", "bracing")); rad = float(need(sel, "screw_d_in", "bracing")) / 2.0
        w = float(sel["width_in"])
        xa = unit(direction)
        ya = unit(cross(out, xa))                          # local z = xa x ya = out
        h = unit([haxis[1][i] - haxis[0][i] for i in range(3)])
        hx, hy = sum(h[i] * xa[i] for i in range(3)), sum(h[i] * ya[i] for i in range(3))
        per = 2 if w < 3.0 else (3 if w < 6.0 else 4)
        parts = []
        for i in range(n):
            row, col = i // per, i % per
            parts.append(cyl("scr-%02d" % (i + 1), "screw", sel["screw_material"], [1.5 + row * 0.75, (col - (per - 1) / 2.0) * (w - 1.0) / max(1, per - 1), 0.2], [0.0, 0.0, -1.0], 0.8, rad, role="strap end to frame member"))
        receivers = [dict(id="strap", kind="member", axis=[[0.0, 0.0, 0.0], [12.0, 0.0, 0.0]]),
                     dict(id="host", kind="member", axis=[[r6([-3.0 * hx])[0], r6([-3.0 * hy])[0], -zoff], [r6([3.0 * hx])[0], r6([3.0 * hy])[0], -zoff]])]
        tpl = r.template("STRAP-END", parts, receivers, [rec], "%d screws through the strap end into the %s" % (n, "face of the frame member" if self.fr.get("sbmf") else "outside flanges of the frame member"))
        t_, o_ = self.att(self.axis[host], P)
        jid = r.joint("%s:%s" % (mid, "A" if end == "start" else "B"), P, 1, ends=[(mid, end)], attachments=[(host, t_, o_)])
        r.instance("C:%s:%s" % (mid, "A" if end == "start" else "B"), jid, tpl, P, xa, ya, dict(strap=mid, host=host), [rec], 1, family="strap end connection")

    def straps(self):
        r = self.r; br = need(self.pd, "bracing", "root")
        frames = sorted(self.columns)
        for where in ("wall", "roof"):
            entries = br.get(where)
            entries = entries if isinstance(entries, list) else ([entries] if entries else [])
            for sel in entries:
                w, t = float(need(sel, "width_in", where)), float(need(sel, "t_in", where))
                prof = r.flat(w, t); rec = need(sel, "record", where)
                hosts = self.columns if where == "wall" else self.rafters
                n_h = len(hosts[frames[0]])
                host_lines = sel.get("lines") or ([1, n_h] if where == "wall" else list(range(1, n_h + 1)))
                depth = (self.cp if where == "wall" else self.rp)["depth"]
                for bay in need(sel, "bays", where):
                    fa, fb = frames[int(bay) - 1], frames[int(bay)]
                    for hl in host_lines:
                        hi = int(hl) - 1
                        ha, A0, B0 = hosts[fa][hi]; hb, A1, B1 = hosts[fb][hi]
                        Lh = math.dist(A0, B0)
                        out = self.wall_normal(A0, B0, hi) if where == "wall" else self.roof_normal(A0, B0, hi)
                        zoff = depth / 2.0 + t / 2.0
                        offv = [out[i] * zoff for i in range(3)]
                        npan = int(sel.get("panels", 1))
                        for pi in range(npan):
                            t0, t1 = (12.0 + (Lh - 24.0) * pi / npan) / Lh, (12.0 + (Lh - 24.0) * (pi + 1) / npan) / Lh
                            for tag, (ta, tb) in (("U", (t0, t1)), ("D", (t1, t0))):
                                Pa, Pb = add(lerp(A0, B0, ta), offv), add(lerp(A1, B1, tb), offv)
                                ax = unit([Pb[i] - Pa[i] for i in range(3)])
                                mid = r.member("BR:%s:L%d:B%02d:P%d%s" % (where, hi + 1, int(bay), pi + 1, tag), "strap", [Pa, Pb], prof, sel["material"], cross(out, ax), 1, [rec],
                                               bay=int(bay), role=sel.get("role") or ("%s tension strap" % ("side-wall" if where == "wall" else "roof-plane")), reference_line="strap centreline",
                                               host_line=hi + 1, plane=where, schedule_row=sel.get("row") or "strap-%s" % where)
                                self.strap_end(sel, rec, mid, "start", Pa, ha, (A0, B0), out, zoff, ax)
                                self.strap_end(sel, rec, mid, "end", Pb, hb, (A1, B1), out, zoff, neg(ax))

    # ---- longitudinal struts on a column line (discrete bracing of the columns) ----
    def struts(self):
        r = self.r
        frames = sorted(self.columns)
        for sel in self.pd.get("struts") or []:
            prof, p = self.built_up(dict(section=need(sel, "section", "strut"), n=int(sel.get("n", 1))))
            hi = int(need(sel, "line", "strut")) - 1
            z = float(need(sel, "height_ft", "strut")) * 12.0
            d = float(need(sel, "bolt_d_in", "strut"))
            hw = self.half_width(self.ncol, self.cp, self.gcol)
            rec = need(sel, "record", "strut")
            nbt = int(sel.get("n_bolts", 2)); ncol_b = max(1, (nbt + 1) // 2)
            parts = [box("clip", "clip_angle", sel["clip_material"], [0.0, -p["depth"] / 2.0 + 0.5, p["t"] / 2.0], [1.5 + 2.5 * ncol_b, p["depth"] - 1.0, 0.25], role="strut end clip on the column", thickness_in=0.25)]
            for k_ in range(ncol_b):
                for j, yy in enumerate((-p["depth"] / 4.0, p["depth"] / 4.0), 1):
                    if 2 * k_ + j <= nbt:
                        parts.append(cyl("bolt-%d-%d" % (k_ + 1, j), "bolt", sel["bolt_material"], [1.5 + 2.5 * k_, yy, -0.5], Z, 1.25, d / 2.0, diameter_in=d, role="strut web to clip"))
            receivers = [dict(id="strut", kind="member", axis=[[0.0, 0.0, 0.0], [12.0, 0.0, 0.0]]), dict(id="column", kind="member", axis=[[-hw, -3.0, 0.0], [-hw, 3.0, 0.0]])]
            tpl = r.template("STRUT-END", parts, receivers, [rec], "Strut end: clip on the column pack with %d x %g in. bolts through the strut web" % (nbt, d))
            bays = sel.get("bays") or list(range(1, len(frames)))
            for bay in bays:
                fa, fb = frames[int(bay) - 1], frames[int(bay)]
                ca, cb = self.columns[fa][hi], self.columns[fb][hi]
                A = [ca[1][0], ca[1][1] + hw, z]; B = [cb[1][0], cb[1][1] - hw, z]
                mid = r.member("ST:L%d:B%02d" % (hi + 1, int(bay)), "strut", [A, B], prof, sel["material"], [1.0, 0.0, 0.0], 1, [rec], bay=int(bay), role=sel.get("role") or "column-line strut",
                               reference_line="web mid-depth centre", host_line=hi + 1)
                for end, P, host, sg in (("start", A, ca[0], 1.0), ("end", B, cb[0], -1.0)):
                    t_, o_ = self.att(self.axis[host], P)
                    jid = r.joint("J:%s:%s" % (mid, "A" if end == "start" else "B"), P, 1, ends=[(mid, end)], attachments=[(host, t_, o_)])
                    r.instance("C:%s:%s" % (mid, "A" if end == "start" else "B"), jid, tpl, P, [0.0, sg, 0.0], [0.0, 0.0, 1.0], dict(strut=mid, column=host), [rec], 1, family="strut end connection")

    # ---- hanger posts under the rafters (monorail / equipment supports carried by the frames) ----
    def hangers(self):
        r = self.r
        for hp in self.pd.get("hanger_posts") or []:
            x = float(need(hp, "x_ft", "hanger post")) * 12.0
            L = float(need(hp, "drop_ft", "hanger post")) * 12.0
            cn = need(hp, "connection", "hanger post"); rec = need(hp, "record", "hanger post")
            tp = float(need(cn, "plate_t_in", "hanger connection"))
            prof, p = self.built_up(dict(section=need(hp, "section", "hanger post"), n=int(hp.get("n", 2)), interconnect=dict(gap_in=tp)))
            d = p["depth"]; dr = self.rp["depth"]
            it = need(hp, "interface", "hanger post")
            iid = "IF:%s" % it["id"]
            if iid not in getattr(self, "_ifaces", set()):
                r.m.record("interfaces", dict(id=iid, scope="delegated", responsible_party=need(it, "responsible_party", "interface"), representation=need(it, "representation", "interface"), detail_ref=need(it, "detail_ref", "interface")))
                self._ifaces = getattr(self, "_ifaces", set()) | {iid}
            npb, db, gauge, pitch = int(need(cn, "n_post_bolts", "hanger connection")), float(need(cn, "bolt_d_in", "hanger connection")), float(need(cn, "gauge_in", "hanger connection")), float(need(cn, "pitch_in", "hanger connection"))
            nrb = int(need(cn, "n_rafter_bolts", "hanger connection")); ncb, dcb = int(need(cn, "n_clamp_bolts", "hanger connection")), float(need(cn, "clamp_bolt_d_in", "hanger connection"))
            for fi in sorted(self.rafters):
                host = next((q for q in self.rafters[fi] if min(q[1][0], q[2][0]) - 1e-6 <= x <= max(q[1][0], q[2][0]) + 1e-6), None)
                if host is None:
                    raise PhysicalDesignError("hanger post at x = %g ft is not under a rafter of frame %d" % (x / 12.0, fi))
                hid, lo, hi_ = host
                f = (x - lo[0]) / (hi_[0] - lo[0])
                Pt = lerp(lo, hi_, f); Pb = add(Pt, Z, -L)
                mid = r.member("HP%02d:%d" % (fi, int(round(x))), "hanger_post", [Pb, Pt], prof, hp["material"], Y, 1, [rec], frame=fi, role=hp.get("role") or "hanger post", reference_line="section centroid")
                # top: hanger plate between the post webs and the webs of the rafter pair, bolted to both
                away = lo if math.dist(lo, Pt) > 1.0 else hi_
                dv = unit([away[0] - Pt[0], 0.0, away[2] - Pt[2]])
                rows = max(1, npb // 2)
                Lp = dr / 2.0 + 2.0 + (rows - 1) * pitch + 2.0
                parts = [box("hanger-plate", "hanger_plate", cn["plate_material"], [-d / 2.0, -tp / 2.0, -Lp], [d, tp, Lp + dr / 2.0], role="hanger plate between the webs", thickness_in=tp)]
                for i in range(rows):
                    for j, sg in enumerate((-1.0, 1.0), 1):
                        parts.append(cyl("bolt-p-%d-%d" % (i + 1, j), "bolt", cn["bolt_material"], [sg * gauge / 2.0, -tp / 2.0 - 0.75, -(dr / 2.0 + 2.0 + i * pitch)], Y, tp + 1.5, db / 2.0, diameter_in=db, role="post webs to hanger plate"))
                for i in range(nrb):
                    a = 3.0 + (i // 2) * 3.0
                    nx, nz = -dv[2], dv[0]
                    sgn = -1.0 if i % 2 == 0 else 1.0
                    parts.append(cyl("bolt-r-%d" % (i + 1), "bolt", cn["bolt_material"], [a * dv[0] + sgn * 2.5 * nx, -tp / 2.0 - 0.75, a * dv[2] + sgn * 2.5 * nz], Y, tp + 1.5, db / 2.0, diameter_in=db, role="hanger plate to rafter webs"))
                receivers = [dict(id="post", kind="member", axis=[[0.0, 0.0, -24.0], [0.0, 0.0, 0.0]]), dict(id="rafter", kind="member", axis=[[0.0, 0.0, 0.0], r6([12.0 * dv[0], 0.0, 12.0 * dv[2]])])]
                tpl = r.template("HANGER-TOP", parts, receivers, [rec], "%g in. hanger plate between the webs: %d x %g in. bolts to the post, %d x %g in. bolts to the rafter webs" % (tp, 2 * rows, db, nrb, db))
                t_, o_ = self.att(self.axis[hid], Pt)
                jt = r.joint("J:%s:top" % mid, Pt, 1, ends=[(mid, "end")], attachments=[(hid, t_, o_)])
                r.instance("C:%s:top" % mid, jt, tpl, Pt, [1.0, 0.0, 0.0], Y, dict(post=mid, rafter=hid), [rec], 1, family="hanger post top connection")
                # bottom: clamp plate and bolts to the supported runway (by others)
                parts = [box("clamp-plate", "clamp_plate", cn["plate_material"], [-4.0, -3.0, -tp], [8.0, 6.0, tp], role="runway clamp plate", thickness_in=tp)]
                for i in range(ncb):
                    parts.append(cyl("bolt-c-%d" % (i + 1), "bolt", cn["bolt_material"], [(-2.5 if i % 2 == 0 else 2.5), (-1.5 if i < 2 else 1.5), -tp - 1.5], Z, tp + 2.0, dcb / 2.0, diameter_in=dcb, role="clamp bolts to the runway beam flange"))
                receivers = [dict(id="post", kind="member", axis=[[0.0, 0.0, 0.0], [0.0, 0.0, 24.0]]), dict(id="runway", kind="interface")]
                tpl = r.template("HANGER-BOTTOM", parts, receivers, [rec], "Clamp plate with %d x %g in. bolts to the top flange of the runway beam" % (ncb, dcb))
                jb = r.joint("J:%s:bottom" % mid, Pb, 1, ends=[(mid, "start")])
                r.instance("C:%s:bottom" % mid, jb, tpl, Pb, [1.0, 0.0, 0.0], Y, dict(post=mid, runway=iid), [rec], 1, family="hanger post runway connection")

    def wall_normal(self, A, B, hi):
        return [-1.0, 0.0, 0.0] if A[0] <= self.xmid else [1.0, 0.0, 0.0]

    def roof_normal(self, A, B, hi=0):
        d = unit([B[0] - A[0], 0.0, B[2] - A[2]])
        n = [-d[2], 0.0, d[0]]
        return n if n[2] > 0 else neg(n)

    # ---- whole building ----
    def build(self):
        snap = bim_adapter.portal(self.cfg, self.pkg)
        if snap.get("findings"):
            raise PhysicalDesignError("portal frame layout: %s" % snap["findings"][0])
        ys = snap["layout_evidence"]["frame_positions_in"]
        self.frames(snap["physical"]["members"])
        frames = sorted(self.columns)
        for fi in frames:                                   # host lines are numbered across the frame, left to right
            self.columns[fi].sort(key=lambda c: c[1][0])
            self.rafters[fi].sort(key=lambda c: min(c[1][0], c[2][0]))
        xs = sorted({round(c[1][0], 3) for c in self.columns[frames[0]]})
        self.xmid = (xs[0] + xs[-1]) / 2.0
        if self.pd.get("purlins"):
            self.secondary("purlins", "purlin", self.rafters, self.roof_normal, self.rp, self.nraf, 6.0, 6.0)
        if self.pd.get("girts"):
            want = [int(v) for v in (self.pd["girts"].get("lines") or [1, len(xs)])]         # column lines (numbered across the frame) that carry girts
            ext = {fi: [c for i_, c in enumerate(cols, 1) if i_ in want] for fi, cols in self.columns.items()}
            self.secondary("girts", "girt", ext, self.wall_normal, self.cp, self.ncol, 12.0, 6.0)
        if self.pd.get("bracing"):
            self.straps()
        if self.pd.get("struts"):
            self.struts()
        if self.pd.get("hanger_posts"):
            self.hangers()
        eaves = sorted({round(c[2][2], 3) for fi in frames for c in self.columns[fi]})
        top = max(round(q[2][2], 3) for fi in frames for q in self.rafters[fi])
        tops = [z for z in eaves if z < top - 1e-6] + [top]
        names = ["Eave" if len(tops) == 2 else "Eave %d" % i for i in range(1, len(tops))] + ["Apex" if len(tops) > 1 else "Roof"]
        if len(tops) == 2 and abs(tops[0] - tops[1]) < 1e-6:
            tops, names = tops[:1], ["Roof"]
        if self.fr.get("canopy") and len(tops) == 3:
            names = ["Low eave", "High eave", "Roof edge"]
        levels = [dict(level=0, name="Base", elevation_in=0.0)] + [dict(level=i, name=names[i - 1], elevation_in=z) for i, z in enumerate(tops, 1)]
        grids = [dict(id="F%d" % fi, label=str(fi), direction="V", points_in=[[xs[0], ys[fi - 1], 0.0], [xs[-1], ys[fi - 1], 0.0]]) for fi in frames] + \
                [dict(id="C%s" % chr(65 + i), label=chr(65 + i), direction="U", points_in=[[x, ys[0], 0.0], [x, ys[-1], 0.0]]) for i, x in enumerate(xs)]
        self.r.decl["reference_system"] = dict(basis="Frame lines and column lines of the analysed portal frames; member worklines are section centroids", levels=levels, grids=grids)
        cov = need(self.pd, "coverage", "root")
        self.r.decl["coverage"] = {k: dict(complete=True, basis=need(cov, k, "coverage")) for k in ("primary_members", "secondary_members", "openings", "connections")}

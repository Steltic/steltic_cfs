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
        name = "%dx%s" % (n, base)
        if name not in self.r.profiles:
            t = p["t"]
            comps = []
            for u in self.pair_offsets(n, p["flange"], gap):
                comps.append(dict(profile_id=base, rotation_deg=180.0, offset=[round(u - gap / 2.0 - t / 2.0, 6), 0.0]))
                comps.append(dict(profile_id=base, rotation_deg=0.0, offset=[round(u + gap / 2.0 + t / 2.0, 6), 0.0]))
            self.r.m.profile(name, "built_up", {}, components=comps,
                             designation="%d x %s in %d back-to-back pairs side by side%s" % (n, base, n // 2, ", webs of each pair %g in. apart" % gap if gap else ""),
                             source=dict(kind="engine_section_catalog", name=base, basis="Back-to-back pairs side by side with flange tips touching; in-plane properties are n times the single channel"))
            self.r.profiles.add(name)
        return name, p

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
        cprof, cp = self.built_up(need(self.fr, "column", "frame")); rprof, rp = self.built_up(need(self.fr, "rafter", "frame"))
        self.cp, self.rp = cp, rp
        self.ncol, self.nraf = int(self.fr["column"]["n"]), int(self.fr["rafter"]["n"])
        self.gcol, self.graf = [float((self.fr[k].get("interconnect") or {}).get("gap_in", 0.0)) for k in ("column", "rafter")]
        for fi in sorted(by_frame):
            ends = defaultdict(list)             # rounded point -> [(member id, end, kind, other point)]
            for c in by_frame[fi]:
                label = c["id"].split(":")[4]
                col = c["kind"] == "column"
                sel = self.fr["column"] if col else self.fr["rafter"]
                A, B = [float(v) for v in c["axis"][0]], [float(v) for v in c["axis"][1]]
                mid = "FR%02d:%s" % (fi, label)
                r.member(mid, "column" if col else "rafter", [A, B], cprof if col else rprof, sel["material"], Y, 0 if col else 1, [need(sel, "record", "frame member")],
                         analysis_ids=c["analysis_ids"], frame=fi, role="portal column" if col else "portal rafter", reference_line="section centroid")
                lo, hi = (A, B) if A[2] <= B[2] else (B, A)          # hosts are set out from their lower end
                (self.columns if col else self.rafters)[fi].append((mid, lo, hi))
                self.axis[mid] = (A, B)
                self.interconnect(mid, (A, B), sel, cp if col else rp, fi, col)
                for end, P, Q in (("start", A, B), ("end", B, A)):
                    ends[tuple(round(v, 4) for v in P)].append((mid, end, "column" if col else "rafter", Q))
            for pt, es in sorted(ends.items()):
                P = list(pt)
                kinds = sorted(e[2] for e in es)
                jid = r.joint("J:FR%02d:%d:%d" % (fi, int(round(P[0])), int(round(P[2]))), P, 0 if P[2] < 1.0 else 1, ends=[(e[0], e[1]) for e in es])
                if kinds == ["column"] and P[2] < 1.0:
                    self.base(fi, jid, P, es[0])
                elif "column" in kinds and "rafter" in kinds:
                    self.knee(fi, jid, P, es)
                elif kinds and all(k == "rafter" for k in kinds) and len(es) == 2:
                    self.apex(fi, jid, P, es)
                else:
                    raise PhysicalDesignError("portal frame %d: joint at %s joins %s; no connection family covers it" % (fi, P, kinds))

    @staticmethod
    def pair_offsets(n, b, gap=0.0):
        """Out-of-plane positions of the mid-plane between the two webs of each back-to-back pair."""
        m = max(1, n // 2)
        return [(k - (m - 1) / 2.0) * (2.0 * b + gap) for k in range(m)]

    def half_width(self, n, p, gap):
        return p["flange"] if n < 2 else (n // 2) * (2.0 * p["flange"] + gap) / 2.0

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
            for k, u in enumerate(self.pair_offsets(n, p["flange"], gap), 1):
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

    def knee(self, fi, jid, P, es):
        legs, bind = [], {}
        for i, (mid, end, kind, Q) in enumerate(es, 1):
            direction = unit([Q[0] - P[0], 0.0, Q[2] - P[2]])
            rid = "column" if kind == "column" else "rafter%d" % i
            legs.append((rid, direction, self.ncol if kind == "column" else self.nraf, self.cp["depth"] if kind == "column" else self.rp["depth"]))
            bind[rid] = mid
        self.bracket("knee", legs, "knee bracket", fi, jid, P, bind)

    def apex(self, fi, jid, P, es):
        legs, bind = [], {}
        for i, (mid, end, kind, Q) in enumerate(es, 1):
            legs.append(("rafter%d" % i, unit([Q[0] - P[0], 0.0, Q[2] - P[2]]), self.nraf, self.rp["depth"])); bind["rafter%d" % i] = mid
        self.bracket("apex", legs, "apex bracket", fi, jid, P, bind)

    # ---- purlins and girts: one member per bay on each line, lapped and cleated at every frame ----
    @staticmethod
    def att(axis, P):
        """Station and offset of point P relative to a member axis."""
        A, B = axis
        d = [B[i] - A[i] for i in range(3)]; L2 = sum(v * v for v in d)
        t = sum((P[i] - A[i]) * d[i] for i in range(3)) / L2
        t = min(1.0, max(0.0, t))
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
        frames = sorted(hosts_by_frame)
        tag0 = "PU" if kind == "purlin" else "GI"
        lines = []
        for hi in range(len(hosts_by_frame[frames[0]])):
            _m0, A0, B0 = hosts_by_frame[frames[0]][hi]
            Lh = math.dist(A0, B0)
            nrm = normal_of(A0, B0, hi)
            off = host_p["depth"] / 2.0 + dp / 2.0
            ly = cross(nrm, Y)                              # Y x ly = nrm: the web of the Z stands along the outward normal
            for si, s in enumerate(self.stations(Lh, sp, first, last), 1):
                line = "%s:H%d:L%02d" % (tag0, hi + 1, si)
                Pf = {fi: add(lerp(hosts_by_frame[fi][hi][1], hosts_by_frame[fi][hi][2], s / Lh), nrm, off) for fi in frames}
                mids, axes = [], {}
                for bi, (fa, fb) in enumerate(zip(frames, frames[1:]), 1):
                    A = add(Pf[fa], Y, -lap if bi > 1 else 0.0)
                    B = add(Pf[fb], Y, lap if bi < len(frames) - 1 else 0.0)
                    mid = r.member("%s:B%02d" % (line, bi), kind, [A, B], prof, sel["material"], ly, 1, [need(sel, "record", key)], bay=bi, line=si, role="roof purlin" if kind == "purlin" else "wall girt",
                                   reference_line="section centroid", host_line=hi + 1)
                    mids.append(mid); axes[mid] = (A, B)
                for k, fi in enumerate(frames):
                    before = mids[k - 1] if k > 0 else None
                    after = mids[k] if k < len(mids) else None
                    host = hosts_by_frame[fi][hi][0]
                    P = Pf[fi]
                    th, oh = self.att(self.axis[host], P)
                    ends, atts, bind = [], [(host, th, oh)], dict(host=host)
                    if before and after:
                        for m_, rid in ((before, "before"), (after, "after")):
                            t_, o_ = self.att(axes[m_], P); atts.append((m_, t_, o_)); bind[rid] = m_
                    elif after:
                        ends.append((after, "start")); bind["after"] = after
                    else:
                        ends.append((before, "end")); bind["before"] = before
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
        return lines

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
        gap = self.gcol if kind == "girt" else self.graf
        hw = self.half_width(host_n, host_p, gap)
        rad_s = float(sel.get("screw_d_in", 0.216)) / 2.0
        flanges = []
        for u in (self.pair_offsets(host_n, host_p["flange"], gap) if host_n > 1 else [0.0]):
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
        tpl = r.template("STRAP-END", parts, receivers, [rec], "%d screws through the strap end into the outside flanges of the frame member" % n)
        t_, o_ = self.att(self.axis[host], P)
        jid = r.joint("%s:%s" % (mid, "A" if end == "start" else "B"), P, 1, ends=[(mid, end)], attachments=[(host, t_, o_)])
        r.instance("C:%s:%s" % (mid, "A" if end == "start" else "B"), jid, tpl, P, xa, ya, dict(strap=mid, host=host), [rec], 1, family="strap end connection")

    def straps(self):
        r = self.r; br = need(self.pd, "bracing", "root")
        frames = sorted(self.columns)
        for where in ("wall", "roof"):
            sel = br.get(where)
            if not sel:
                continue
            w, t = float(need(sel, "width_in", where)), float(need(sel, "t_in", where))
            prof = r.flat(w, t); rec = need(sel, "record", where)
            hosts = self.columns if where == "wall" else self.rafters
            n_h = len(hosts[frames[0]])
            host_lines = sel.get("host_lines") or ([1, n_h] if where == "wall" else list(range(1, n_h + 1)))
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
                            mid = r.member("BR:%s:B%02d:H%d:P%d%s" % (where, int(bay), hi + 1, pi + 1, tag), "strap", [Pa, Pb], prof, sel["material"], cross(out, ax), 1, [rec],
                                           bay=int(bay), role="%s tension strap" % ("side-wall" if where == "wall" else "roof-plane"), reference_line="strap centreline", host_line=hi + 1, plane=where)
                            self.strap_end(sel, rec, mid, "start", Pa, ha, (A0, B0), out, zoff, ax)
                            self.strap_end(sel, rec, mid, "end", Pb, hb, (A1, B1), out, zoff, neg(ax))

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
        xs = sorted({round(c[1][0], 3) for c in self.columns[frames[0]]})
        self.xmid = (xs[0] + xs[-1]) / 2.0
        if self.pd.get("purlins"):
            self.secondary("purlins", "purlin", self.rafters, self.roof_normal, self.rp, self.nraf, 6.0, 6.0)
        if self.pd.get("girts"):
            ext = {fi: [c for c in cols if abs(c[1][0] - xs[0]) < 1.0 or abs(c[1][0] - xs[-1]) < 1.0] for fi, cols in self.columns.items()}
            self.secondary("girts", "girt", ext, self.wall_normal, self.cp, self.ncol, 12.0, 6.0)
        if self.pd.get("bracing"):
            self.straps()
        eaves = sorted({round(c[2][2], 3) for fi in frames for c in self.columns[fi]})
        top = max(round(q[2][2], 3) for fi in frames for q in self.rafters[fi])
        tops = [z for z in eaves if z < top - 1e-6] + [top]
        names = ["Eave" if len(tops) == 2 else "Eave %d" % i for i in range(1, len(tops))] + ["Apex" if len(tops) > 1 else "Roof"]
        if len(tops) == 2 and abs(tops[0] - tops[1]) < 1e-6:
            tops, names = tops[:1], ["Roof"]
        levels = [dict(level=0, name="Base", elevation_in=0.0)] + [dict(level=i, name=names[i - 1], elevation_in=z) for i, z in enumerate(tops, 1)]
        grids = [dict(id="F%d" % fi, label=str(fi), direction="V", points_in=[[xs[0], ys[fi - 1], 0.0], [xs[-1], ys[fi - 1], 0.0]]) for fi in frames] + \
                [dict(id="C%s" % chr(65 + i), label=chr(65 + i), direction="U", points_in=[[x, ys[0], 0.0], [x, ys[-1], 0.0]]) for i, x in enumerate(xs)]
        self.r.decl["reference_system"] = dict(basis="Frame lines and column lines of the analysed portal frames; member worklines are section centroids", levels=levels, grids=grids)
        cov = need(self.pd, "coverage", "root")
        self.r.decl["coverage"] = {k: dict(complete=True, basis=need(cov, k, "coverage")) for k in ("primary_members", "secondary_members", "openings", "connections")}

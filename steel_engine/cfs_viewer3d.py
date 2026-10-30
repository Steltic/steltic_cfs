"""cfs_viewer3d.py -- interactive 3D viewer for the CFS paths (three.js), reusing
viewer3d_template.html (the template is data-driven; only the DATA builder is CFS-specific).

  standalone_html(cfg, name, res, pkg, root) -> full-page viewer HTML string
  report_section(cfg, name, res, pkg, root)  -> writes <root>/viewer_3d.html, returns the
                                                orange "View model" button HTML

WALL PATH  -- schematic wall model built from the cfg: chord studs (columns) + top tracks
(beams) + panel diagonals (braces; literal for strap walls, sheathing stand-in otherwise),
floor plates as slabs, segments laid out evenly along each line. D/C colors come from the
FILLED design/calc_package_cfs.json (the check of record) -- wall slots color the
diagonals, chord entries color the chords. "Mode" shapes are the engine's per-story DRIFT
profiles per direction (static patterns, not eigenmodes -- labelled in the caveat).

PORTAL PATH -- real build_portal() geometry replicated at each frame line, purlins/girts
between frames, side-wall X-straps in the end bays; deflected shapes from actual solves
(governing wind sway + gravity). Frame count from cfg['n_frames'] or ['length_ft'] (else 4
representative frames).

Units: kip-inch model space (matches the template's z-up inch convention).
"""
import os, json, math, html as _html

_DIR = os.path.dirname(os.path.abspath(__file__))
ORANGE = "#ff8a3d"

_CAVEAT_COMMON = (
    "D/C colors are the governing D/C of each member's design record in the calculation package "
    "(the check of record); grey members have no design record. Animated shapes are static deflection "
    "patterns (per-story drift profiles / combination deflections), not eigenmodes. "
    "North = +Y. Drag = orbit, scroll = zoom, right-drag = pan, click a member for details.")


def _caveat_common(cfg, solved_shapes=False):
    """The common caveat with the plan north direction of this building (default +Y). solved_shapes: the viewer
    animates combination deflections from real solves (portal frames), so the static-pattern sentence is left out."""
    north = str((cfg or {}).get("plan_north") or "+Y")
    txt = _CAVEAT_COMMON.replace("North = +Y.", "North = %s." % north)
    if solved_shapes:
        txt = txt.replace("Animated shapes are static deflection patterns (per-story drift profiles / combination "
                          "deflections), not eigenmodes. ", "")
    return txt


def _combo_txt(label):
    """A combination as the report prints it (typeset ASCE 7 notation), never the analysis key."""
    if not label or label == "?":
        return None
    try:
        import report as _R
        return _R._cfs_combo_label(label)
    except Exception:
        return _html.escape(str(label))


def _num(v):
    try:
        return float(str(v).replace("in.", "").replace("in", "").strip())
    except (TypeError, ValueError):
        return None


def _pkg_eave_sway(pkg):
    """(sway in., H/x, case) of the report's serviceability row (drift_table 'eave_sway'), or None when the package
    has none or carries it as outside the scope (existing frames)."""
    for r in (pkg or {}).get("drift_table") or []:
        if isinstance(r, dict) and str(r.get("check", "")).lower() == "eave_sway":
            if r.get("waived"):
                return None
            v = _num(r.get("value_in", r.get("sway_in")))
            if v is not None:
                return v, r.get("H_over"), r.get("wind_case")
    return None


def _pkg_wall_dc(pkg):
    """(dirn, line, story) -> (DC, sheathing/schedule string)."""
    out = {}
    for w in (pkg or {}).get("wall_lines") or []:
        try:
            _, d, ln, s = w["id"].split("-")
            dc = w.get("DC")
            sec = (w.get("sheathing") or "").split(",")[0][:26] or "wall panel"
            out[(d, ln, int(s[1:]))] = (dc if isinstance(dc, (int, float)) else None, sec)
        except Exception:
            continue
    return out


def _pkg_chord(pkg):
    for s in (pkg or {}).get("studs") or []:
        if "chord" in str(s.get("id", "")):
            dc = s.get("DC")
            sec = str(s.get("section", "chord")).split(";")[0][:26]
            return (dc if isinstance(dc, (int, float)) else None, sec)
    return (None, "chord studs")


# =============================================================== plan geometry (optional cfg keys)
#   cfg['plan_outline_ft']        [(x, y), ...] ft, the plan outline (L, T, U, Z, cross ...), counter-clockwise or not
#   cfg['plan_outline_by_story']  {story: [(x, y), ...]} the plate on top of that story where it differs (set-backs)
#   cfg['line_plan_pos_ft']       {"X-<line>" or "<line>": ft} the drawn position of a wall line where the analysis
#                                 position (WallLine.pos, tributary fit) differs from the plan position
# Without them the plates are the bounding plan_ft rectangle and the lines sit at their analysis positions.
def plan_outline(cfg, story=None):
    """Plan outline [(x, y) ft] of the plate on top of `story` (1..N)."""
    bys = cfg.get("plan_outline_by_story") or {}
    if story is not None and (bys.get(story) or bys.get(str(story))):
        return [tuple(map(float, v)) for v in (bys.get(story) or bys.get(str(story)))]
    if cfg.get("plan_outline_ft"):
        return [tuple(map(float, v)) for v in cfg["plan_outline_ft"]]
    Lx, Ly = float(cfg["plan_ft"][0]), float(cfg["plan_ft"][1])
    return [(0.0, 0.0), (Lx, 0.0), (Lx, Ly), (0.0, Ly)]


def has_outline(cfg):
    return bool(cfg.get("plan_outline_ft") or cfg.get("plan_outline_by_story"))


def _inside(poly, x, y):
    c = False
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            c = not c
    return c


def plan_rects(poly):
    """Rectangles (x0, x1, y0, y1) ft covering a rectilinear outline (grid cells whose centre is inside)."""
    xs = sorted({p[0] for p in poly}); ys = sorted({p[1] for p in poly})
    out = []
    for a in range(len(xs) - 1):
        for b in range(len(ys) - 1):
            if _inside(poly, (xs[a] + xs[a + 1]) / 2.0, (ys[b] + ys[b + 1]) / 2.0):
                out.append((xs[a], xs[a + 1], ys[b], ys[b + 1]))
    return out


def line_pos_ft(cfg, dirn, ln):
    m = cfg.get("line_plan_pos_ft") or {}
    v = m.get("%s-%s" % (dirn, ln.name), m.get(ln.name))
    return float(v) if v is not None else float(ln.pos)


def line_extent_ft(poly, dirn, pos):
    """(lo, hi) ft of a wall line at `pos` within the outline: an X line runs along x at y = pos, a Y line along y."""
    hits = []
    for eps in (0.05, -0.05):
        c = pos + eps
        cuts = []
        n = len(poly)
        for i in range(n):
            (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
            if dirn == "X" and (y1 > c) != (y2 > c):
                cuts.append(x1 + (c - y1) * (x2 - x1) / (y2 - y1))
            if dirn == "Y" and (x1 > c) != (x2 > c):
                cuts.append(y1 + (c - x1) * (y2 - y1) / (x2 - x1))
        hits += cuts
    if len(hits) < 2:
        xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
        return (min(xs), max(xs)) if dirn == "X" else (min(ys), max(ys))
    return min(hits), max(hits)


# =============================================================== wall path
def _wall_data(cfg, name, res, pkg):
    z = [0.0]
    for h in cfg["heights_ft"]:
        z.append(z[-1] + h * 12.0)
    Lx, Ly = cfg["plan_ft"][0] * 12.0, cfg["plan_ft"][1] * 12.0
    wall_dc = _pkg_wall_dc(pkg)
    chord_dc, chord_sec = _pkg_chord(pkg)

    nodes, elements, fixed = {}, [], []
    tag = [0]

    def nd(x, y, zz):
        tag[0] += 1
        nodes[str(tag[0])] = [round(x, 1), round(y, 1), round(zz, 1)]
        if zz < 1e-6:
            fixed.append(tag[0])
        return tag[0]

    etag = [0]

    def el(t1, t2, typ, sec, util=None, combo=None, f=None, where=None):
        etag[0] += 1
        c1, c2 = nodes[str(t1)], nodes[str(t2)]
        L = math.dist(c1, c2)
        elements.append({"tag": etag[0], "type": typ, "sec": sec, "n": [t1, t2],
                         "L": round(L, 1), "util": util, "combo": combo, "f": f or {},
                         "where": where})

    sysname = str(cfg.get("system", ""))
    strap = sysname == "strap_braced"

    base_poly = plan_outline(cfg, 1)
    for dirn, lines in (("X", cfg["lines_x"]), ("Y", cfg["lines_y"])):
        for ln in lines:
            # layout from the story with the most segments (stacked walls; upper stories
            # with fewer segments drop the LAST positions -- matches stepped briefs)
            base_segs = max((ln.segments.get(k, []) for k in range(1, cfg["stories"] + 1)),
                            key=len)
            pos_ft = line_pos_ft(cfg, dirn, ln)
            lo_ft, hi_ft = line_extent_ft(base_poly, dirn, pos_ft)
            dim = (hi_ft - lo_ft) * 12.0
            tot = sum(L for (L, _h) in base_segs) * 12.0
            gap = max((dim - tot) / (len(base_segs) + 1), 0.0)
            starts = []
            cur = lo_ft * 12.0 + gap
            for (L, _h) in base_segs:
                starts.append(cur)
                cur += L * 12.0 + gap
            pos = pos_ft * 12.0
            # persistent segment-end node grids per story level
            grid = {}     # (segidx, end, level) -> tag

            def gnode(si, end, lev, a, b):
                key = (si, end, lev)
                if key not in grid:
                    if dirn == "X":
                        grid[key] = nd(a, pos, z[lev])
                    else:
                        grid[key] = nd(pos, a, z[lev])
                return grid[key]

            for k in range(1, cfg["stories"] + 1):
                segs = ln.segments.get(k, [])
                dc, sec = wall_dc.get((dirn, ln.name, k), (None, sysname or "wall"))
                for si, (Lft, _h) in enumerate(segs):
                    if si >= len(starts):
                        break
                    xa = starts[si]
                    xb = xa + Lft * 12.0
                    nba = gnode(si, 0, k - 1, xa, pos)
                    nbb = gnode(si, 1, k - 1, xb, pos)
                    nta = gnode(si, 0, k, xa, pos)
                    ntb = gnode(si, 1, k, xb, pos)
                    cu = chord_dc if chord_dc is not None else None
                    where = "line %s, story %d" % (ln.name, k)
                    el(nba, nta, "column", chord_sec, cu, where=where)
                    el(nbb, ntb, "column", chord_sec, cu, where=where)
                    el(nta, ntb, "beam", "track", None, None, where=where)
                    el(nba, ntb, "brace", sec, dc, where=where)
                    el(nbb, nta, "brace", sec, dc, where=where)

    slabs = [[x0 * 12.0, x1 * 12.0, y0 * 12.0, y1 * 12.0, z[k]]
             for k in range(1, len(z)) for (x0, x1, y0, y1) in plan_rects(plan_outline(cfg, k))]

    # drift-profile "modes" per direction (governing line cumulative story drifts)
    modes = []
    Ta = (res.get("elf") or {}).get("Ta", 0.3)
    for dirn in ("X", "Y"):
        dd = (res.get("directions") or {}).get(dirn)
        if not dd:
            continue
        gname, best = None, -1.0
        for lname, lr in dd["lines"].items():
            V1 = lr[min(lr)]["V"]
            if V1 > best:
                best, gname = V1, lname
        lr = dd["lines"][gname]
        cum, prof = 0.0, {0: 0.0}
        for k in sorted(lr):
            cum += lr[k].get("drift_in", 0.0)
            prof[k] = cum
        mx = max(prof.values()) or 1.0
        shape = {}
        for t, c in nodes.items():
            lev = min(range(len(z)), key=lambda i: abs(z[i] - c[2]))
            d = prof.get(lev, prof[max(prof)]) / mx
            shape[t] = [round(d, 4), 0.0, 0.0] if dirn == "X" else [0.0, round(d, 4), 0.0]
        modes.append({"T": round(Ta, 3), "shape": shape})

    # loads -- feed EVERY template toggle so no checkbox is dead:
    # DLdyn/LLdyn = per-level gravity totals as down-arrows on a plan grid;
    # wind/seis = lateral story-force arrows. ("static model" ribbons stay empty --
    # the wall path has no beam tributary model; the toggle is a hot-rolled feature.)
    loads = {}
    e = res.get("elf") or {}
    N = cfg["stories"]
    area = cfg.get("area_sf") or (cfg["plan_ft"][0] * cfg["plan_ft"][1])
    try:
        import cfs_engine as CEE
        ptsD, ptsL, totD, totL = [], [], 0.0, 0.0
        gx = [Lx * (i + 0.5) / 6.0 for i in range(6)]
        gy = [Ly * (j + 0.5) / 4.0 for j in range(4)]
        npts = len(gx) * len(gy)
        for k in range(1, N + 1):
            Dk = CEE.story_weight(cfg, k)
            Lk = ((cfg.get("Lr", 20.0) if k == N else cfg.get("L_floor", 40.0))
                  * area / 1000.0)
            totD += Dk
            totL += Lk
            for xx in gx:
                for yy in gy:
                    ptsD.append([round(xx, 1), round(yy, 1), round(z[k], 1),
                                 round(Dk / npts, 2)])
                    ptsL.append([round(xx, 1), round(yy, 1), round(z[k], 1),
                                 round(Lk / npts, 2)])
        loads["DLdyn"] = {"pts": ptsD, "total": round(totD, 0)}
        loads["LLdyn"] = {"pts": ptsL, "total": round(totL, 0)}
    except Exception:
        pass
    if e:
        loads["seis"] = {"z": [round(v, 1) for v in z[1:]],
                         "F": [round(e["Fx"][k], 1) for k in sorted(e["Fx"])],
                         "V": round(e["V"], 1), "Cs": round(e["Cs"], 4)}
    try:
        import cfs_pipeline as CP
        if cfg.get("wind"):
            FX, _b = CP.wind_story_forces(cfg, "X")
            FY, _b = CP.wind_story_forces(cfg, "Y")
            loads["wind"] = {"z": [round(v, 1) for v in z[1:]],
                             "X": [round(FX[k], 1) for k in sorted(FX)],
                             "Y": [round(FY[k], 1) for k in sorted(FY)]}
    except Exception:
        pass

    dcs = [u[0] for u in wall_dc.values() if u[0] is not None]
    worst_dr = max((r.get("drift_amplified", 0.0) for dd in res["directions"].values()
                    for lr in dd["lines"].values() for r in lr.values()), default=0.0)
    stats = [["Wall lines X / Y", "%d / %d" % (len(cfg["lines_x"]), len(cfg["lines_y"]))],
             ["W / V (ELF)", "%.0f k / %.1f k (Cs=%.4f)" % (e.get("W", 0), e.get("V", 0),
                                                            e.get("Cs", 0))],
             ["Ta", "%.3f s" % e.get("Ta", 0)],
             ["Worst amplified drift", "%.4f" % worst_dr]]
    if dcs:
        stats.append(["Max wall D/C (package)", "%.2f" % max(dcs)])

    return {
        "meta": {
            "title": "%s — CFS wall model" % name,
            "subtitle": "%d nodes · %d elements · schematic wall-line model · units: kip, in"
                        % (len(nodes), len(elements)),
            "model_line": "system: %s · diaphragm: %s" % (sysname,
                                                          cfg.get("diaphragm", "flexible")),
            "support_label": "Supports (hold-downs/rods at chords; track anchors between)",
            "levels": [round(v, 1) for v in z],
            "stats": stats,
            "demand_src": "D/C: wall-line and chord design records of the calculation package",
            "load_labels": {
                "lDLdyn": "Dead load (story totals, incl. cladding/partitions in W)",
                "lLLdyn": "Live load (story totals, unreduced)",
                "lWind": "Wind story forces (MWFRS screen)",
                "lSeis": "Seismic ELF story forces"},
            "caveat": ("SCHEMATIC wall model: segments laid out evenly along each line "
                       "(their exact plan positions are on the wall-line schedules); "
                       "diagonals represent sheathing panels (literal straps on strap-braced "
                       "systems); " + ("slabs follow the floor plan. " if has_outline(cfg) else
                                       "slabs show the bounding plan. ") + _caveat_common(cfg)),
        },
        "nodes": nodes, "fixed": fixed, "elements": elements,
        "modes": modes, "slabs": slabs, "loads": loads,
    }


# =============================================================== portal path
def _portal_data(cfg, name, res, pkg):
    import cfs_frame as CF
    secs = dict(col=CF.frame_section(cfg["col_section"]),
                raf=CF.frame_section(cfg["raf_section"]))
    fr, members, meta = CF.build_portal(cfg, secs)
    sp = cfg["spacing_ft"] * 12.0
    if cfg.get("n_frames"):
        nf = int(cfg["n_frames"])
    elif cfg.get("length_ft"):
        nf = int(round(cfg["length_ft"] / cfg["spacing_ft"])) + 1
    else:
        nf = 4                                    # representative slice, noted in caveat
    nf = max(2, min(nf, 24))

    mem_dc = {}
    for m in (pkg or {}).get("members") or []:
        dc = m.get("DC")
        mem_dc[m.get("member")] = (dc if isinstance(dc, (int, float)) else None,
                                   str(m.get("section", ""))[:28] or m.get("section"))
    sched_dc = {}
    for s in (pkg or {}).get("schedules") or []:
        dc = s.get("DC")
        key = str(s.get("schedule") or s.get("id", "")).replace("sched-", "")
        sched_dc[key] = dc if isinstance(dc, (int, float)) else None

    nodes, elements, fixed = {}, [], []

    def nid(j, t):
        return j * 100000 + t

    for j in range(nf):
        for t, (x, y) in fr.nodes.items():
            nodes[str(nid(j, t))] = [round(x, 1), round(j * sp, 1), round(y, 1)]
            if y < 1e-6:
                fixed.append(nid(j, t))

    etag = [0]

    def el(t1, t2, typ, sec, util=None, combo=None, f=None, where=None):
        etag[0] += 1
        c1, c2 = nodes[str(t1)], nodes[str(t2)]
        elements.append({"tag": etag[0], "type": typ, "sec": sec, "n": [t1, t2],
                         "L": round(math.dist(c1, c2), 1), "util": util,
                         "combo": combo, "f": f or {}, "where": where})

    try:                                          # a custom / hand design model: the view frame's own results
        import report as _R                       # (governing combinations) are not the design values
        custom = bool(_R._cfs_custom_model(cfg, pkg)[0])
    except Exception:
        custom = False
    gov = {} if custom else ((res or {}).get("governing") or {})
    for j in range(nf):
        for lab, idxs in members.items():
            mkey = "col" if lab.startswith("col") else "raf"
            dc, secl = mem_dc.get(mkey, (None, None))
            secl = secl or cfg["%s_section" % mkey]
            for idx in idxs:
                n1, n2 = fr.elems[idx][0], fr.elems[idx][1]
                el(nid(j, n1), nid(j, n2), "column" if mkey == "col" else "beam",
                   secl, dc, _combo_txt(gov.get(mkey)))

    # purlins (rafter station nodes) + girts (column station nodes) between frames
    raf_nodes, col_nodes = set(), set()
    for lab, idxs in members.items():
        tgt = raf_nodes if lab.startswith("raf") else col_nodes
        for idx in idxs:
            tgt.update((fr.elems[idx][0], fr.elems[idx][1]))
    # girts on the exterior column lines only (interior columns carry no wall); on an open side only above the
    # header (cfg["longitudinal_bracing"]["header_bottom_ft"]), none where no header is given
    _xs = [fr.nodes[t][0] for t in meta["base_nodes"]]
    _xL, _xR = min(_xs), max(_xs)
    _open = set((cfg.get("wind") or {}).get("open_sides") or ())
    _zh = float((cfg.get("longitudinal_bracing") or {}).get("header_bottom_ft") or 0.0) * 12.0

    def _girt_ok(t):
        x, z = fr.nodes[t]
        side = "L" if abs(x - _xL) < 1e-6 else "R" if abs(x - _xR) < 1e-6 else None
        if side is None:
            return False
        return side not in _open or (_zh > 0.0 and z >= _zh - 1e-6)
    for j in range(nf - 1):
        for t in sorted(raf_nodes):
            el(nid(j, t), nid(j + 1, t), "beam", "purlin", sched_dc.get("purlin"))
        for t in sorted(t for t in col_nodes - set(meta["base_nodes"]) if _girt_ok(t)):
            el(nid(j, t), nid(j + 1, t), "beam", "girt", sched_dc.get("girt"))
    # longitudinal X-straps (exterior column lines; a multi-span frame's interior columns carry
    # no side wall). Default: both side walls in the two end bays, none across an open side.
    # cfg["longitudinal_bracing"] gives the actual layout: side_bays {"L"/"R": [bay, ...]},
    # header_bays {"L"/"R": [...]} with header_bottom_ft (straps from the header to the eave on
    # an open side) and roof_bays; bay k lies between frames k and k + 1 (frames numbered from 1).
    lb = cfg.get("longitudinal_bracing") or {}
    n_all = int(cfg.get("n_frames") or nf)
    open_sides = set((cfg.get("wind") or {}).get("open_sides") or ())

    def bay_idx(k):
        """1-based bay of the building -> 0-based bay of the drawn slice (end bays kept at the ends)."""
        b = int(k) - 1
        if nf == n_all or b < (n_all - 1) / 2.0:
            return min(max(b, 0), nf - 2)
        return max(nf - 2 - (n_all - 2 - b), 0)

    bL, bR = meta["base_nodes"][0], meta["base_nodes"][-1]
    eL, eR = meta["eave_nodes"][0], meta["eave_nodes"][-1]
    side_bays = lb.get("side_bays") or {s_: [] if s_ in open_sides else [1, n_all - 1] for s_ in ("L", "R")}
    for s_, (b, e_) in (("L", (bL, eL)), ("R", (bR, eR))):
        for j in sorted({bay_idx(k) for k in side_bays.get(s_) or ()}):
            el(nid(j, b), nid(j + 1, e_), "brace", "X-strap", sched_dc.get("strap"),
               where="side wall %s, bay %d" % (s_, j + 1))
            el(nid(j + 1, b), nid(j, e_), "brace", "X-strap", sched_dc.get("strap"),
               where="side wall %s, bay %d" % (s_, j + 1))
    zh = float(lb.get("header_bottom_ft") or 0.0) * 12.0
    for s_, (b, e_) in (("L", (bL, eL)), ("R", (bR, eR))):
        bays = sorted({bay_idx(k) for k in (lb.get("header_bays") or {}).get(s_) or ()})
        if not bays or zh <= 0.0:
            continue
        for j in range(nf):                       # header strut node on the open column line
            x0, y0, _z = nodes[str(nid(j, b))]
            nodes[str(nid(j, 90000 + (0 if s_ == "L" else 1)))] = [x0, y0, round(zh, 1)]
        h_ = 90000 + (0 if s_ == "L" else 1)
        for j in range(nf - 1):
            el(nid(j, h_), nid(j + 1, h_), "beam", "header strut", None, where="open side %s" % s_)
        for j in bays:
            el(nid(j, h_), nid(j + 1, e_), "brace", "header X-strap", sched_dc.get("strap"),
               where="open side %s header, bay %d" % (s_, j + 1))
            el(nid(j + 1, h_), nid(j, e_), "brace", "header X-strap", sched_dc.get("strap"),
               where="open side %s header, bay %d" % (s_, j + 1))
    raf_elems = [fr.elems[i][:2] for lab_, idxs in members.items() if lab_.startswith("raf") for i in idxs]
    for j in sorted({bay_idx(k) for k in lb.get("roof_bays") or ()}):
        for (n1, n2) in raf_elems:
            el(nid(j, n1), nid(j + 1, n2), "brace", "roof X-strap", None, where="roof, bay %d" % (j + 1))
            el(nid(j + 1, n1), nid(j, n2), "brace", "roof X-strap", None, where="roof, bay %d" % (j + 1))

    # deflected shapes from real solves (replicated across frames)
    modes = []
    try:
        cases = {c: CF._case_loads(fr, members, meta, cfg, c)
                 for c in ("D", "Lr", "S_bal", "W", "W2", "E")}
        shapes = [("wind sway (1.0W)", {"W": 1.0})]
        if cfg.get("snow_pg", 0.0) > 0:
            shapes.append(("gravity (1.2D+1.6S)", {"D": 1.2, "S_bal": 1.6}))
        else:
            shapes.append(("gravity (1.2D+1.6Lr)", {"D": 1.2, "Lr": 1.6}))
        Tlab = (cfg.get("seis") or {}).get("Ta", 0.4) or 0.4
        for _nm, combo in shapes:
            _f, _m, _meta, sol = CF._solve_combo(cfg, secs, combo, cases, 1.0,
                                                 pdelta_iters=0)
            mx = max(max(abs(u[0]), abs(u[1])) for u in sol["u"].values()) or 1.0
            shp = {}
            for j in range(nf):
                for t, u in sol["u"].items():
                    shp[str(nid(j, t))] = [round(u[0] / mx, 4), 0.0, round(u[1] / mx, 4)]
            modes.append({"T": round(float(Tlab), 3), "shape": shp})
    except Exception:
        pass

    # loads: gravity ribbons on the rafters (static-model toggle), roof-uplift ribbons,
    # and lateral wind/seismic arrows at the eave -- so every load checkbox does something
    loads = {}
    try:
        sp_ft = cfg["spacing_ft"]
        wD = (cfg.get("D_roof", 0.0) + cfg.get("collateral", 0.0)) * sp_ft / 1000.0  # klf
        ps = cfg.get("snow_ps", 0.7 * cfg.get("snow_pg", 0.0))
        wL = max(ps, cfg.get("Lr", 0.0)) * sp_ft / 1000.0
        beamsD, beamsL = [], []
        for j in range(nf):
            for lab in [k for k in members if k.startswith("raf")]:
                for idx in members[lab]:
                    n1, n2 = fr.elems[idx][0], fr.elems[idx][1]
                    (x1, y1), (x2, y2) = fr.nodes[n1], fr.nodes[n2]
                    seg = [round(x1, 1), round(j * sp, 1), round(y1, 1),
                           round(x2, 1), round(j * sp, 1), round(y2, 1)]
                    beamsD.append(seg + [[round(wD, 3)] * 4])
                    beamsL.append(seg + [[round(wL, 3)] * 4])
        span_ft = meta["span_in"] / 12.0                 # total width (every span)
        loads["stat"] = {
            "D": {"beams": beamsD, "total": round(wD * span_ft * nf, 1)},
            "L": {"beams": beamsL, "total": round(wL * span_ft * nf, 1)}}
        pr = CF.wind_surface_pressures(cfg)
        Hw = (max(pr.get("wall_wind", 0.0), 0.0) + max(-pr.get("wall_lee", 0.0), 0.0)) \
            * cfg["eave_ft"] * sp_ft / 1000.0
        if Hw > 0.01:
            loads["wind"] = {"z": [round(cfg["eave_ft"] * 12.0, 1)],
                             "X": [round(Hw, 2)], "Y": [0.0]}
        s = cfg.get("seis") or {}
        se = (res or {}).get("seismic") or {}
        if se.get("V_kip") is not None and se.get("Cs") is not None:
            # the analysis' ELF (W per frame stated or computed per 12.7.2)
            loads["seis"] = {"z": [round(cfg["eave_ft"] * 12.0, 1)],
                             "F": [round(se["V_kip"], 2)], "V": round(se["V_kip"], 2),
                             "Cs": round(se["Cs"], 4)}
        elif s.get("W_frame_kip"):
            Ve = s["SDS"] / (s.get("R", 3.0) / s.get("Ie", 1.0)) * s["W_frame_kip"]
            loads["seis"] = {"z": [round(cfg["eave_ft"] * 12.0, 1)],
                             "F": [round(Ve, 2)], "V": round(Ve, 2),
                             "Cs": round(s["SDS"] / (s.get("R", 3.0) / s.get("Ie", 1.0)), 4)}
    except Exception:
        loads = {}

    a = ((pkg or {}).get("anchorage") or [{}])[0]
    if cfg.get("spans"):
        geo = ["Spans / eave / apex", "%s ft / %.0f ft / %s ft" % (
            " + ".join("%.0f" % float(x["span_ft"]) for x in cfg["spans"]), cfg["eave_ft"],
            " / ".join("%.0f" % float(x["apex_ft"]) for x in cfg["spans"]))]
    else:
        geo = ["Span / eave / apex", "%.0f / %.0f / %.0f ft" % (
            meta["span_in"] / 12.0, cfg["eave_ft"], cfg.get("apex_ft", cfg["eave_ft"]))]
    stats = [["Frames shown", "%d @ %.1f ft o.c." % (nf, cfg["spacing_ft"])], geo]
    gc, gr = _combo_txt(gov.get("col")), _combo_txt(gov.get("raf"))
    if gc or gr:
        stats.append(["Governing combinations", "column: %s · rafter: %s" % (gc or "&mdash;", gr or "&mdash;")])
    vb = _num(a.get("V_design_kip", None if custom else a.get("V_base_kip")))
    tb = _num(a.get("T_design_kip", None if custom else a.get("T_net_uplift_kip")))
    if vb is not None or tb is not None:
        stats.append(["Base V / net uplift", "%s / %s kip" % ("%.2f" % vb if vb is not None else "&mdash;",
                                                              "%.2f" % tb if tb is not None else "&mdash;")])
    if isinstance(a.get("M_base_kipin"), (int, float)) and a["M_base_kipin"] > 1:
        stats.append(["Base moment (fixed)", "%.0f kip-in" % a["M_base_kipin"]])
    if (res or {}).get("seismic", {}).get("W_frame_kip"):
        stats.append(["Seismic W / V per frame", "%.1f / %.2f kip" % (
            res["seismic"]["W_frame_kip"], res["seismic"].get("V_kip") or 0.0)])
    ps = _pkg_eave_sway(pkg)                           # the report's serviceability value (design model)
    sv = (res or {}).get("service") or {}
    if ps:
        stats.append(["Eave sway (service wind)", "%.2f in%s" % (ps[0], (" (H/%d)" % int(ps[1])) if ps[1] else "")])
    elif sv and _num(sv.get("eave_sway_in")) is not None and not (pkg or {}).get("drift_table"):
        stats.append(["Eave sway (service wind)", "%.2f in (H/%d)" % (_num(sv["eave_sway_in"]),
                                                                       int(sv.get("H_over", 0) or 0))])
    dcs = [v[0] for v in mem_dc.values() if v[0] is not None]
    if dcs:
        stats.append(["Max member D/C (package)", "%.2f" % max(dcs)])

    note_nf = ("" if (cfg.get("n_frames") or cfg.get("length_ft")) else
               " Only a representative %d-frame slice of the building is drawn." % nf)
    return {
        "meta": {
            "title": "%s — CFS portal model" % name,
            "subtitle": "%d nodes · %d elements · %d frames · units: kip, in"
                        % (len(nodes), len(elements), nf),
            "model_line": "sections: col %s · raf %s · base %s" % (
                cfg["col_section"], cfg["raf_section"], cfg.get("base", "pinned")),
            "support_label": "Supports (%s)" % cfg.get("base", "pinned"),
            "levels": [0.0, round(cfg["eave_ft"] * 12.0, 1)],
            "stats": stats,
            "demand_src": "D/C: member and schedule design records of the calculation package",
            "load_labels": {
                "lDLstat": "Roof dead load (ribbons on rafters)",
                "lLLstat": "Roof snow/Lr (ribbons on rafters)",
                "lWind": "Wind (per-frame transverse force at eave)",
                "lSeis": "Seismic E (per-frame force at eave)"},
            "caveat": ("Portal geometry is the analysis model; purlins/girts/straps are "
                       "drawn schematically between frames at their model stations; end-wall framing and "
                       "bracing are not drawn."
                       + note_nf + " Animated shapes are combo deflections from real "
                       "solves (mode 1 = wind sway, mode 2 = gravity), not eigenmodes. " + _caveat_common(cfg, True)),
        },
        "nodes": nodes, "fixed": fixed, "elements": elements,
        "modes": modes, "slabs": [], "loads": loads,
    }


# =============================================================== assembly
def viewer_data(cfg, name, res, pkg):
    if "span_ft" in cfg or cfg.get("spans"):
        return _portal_data(cfg, name, res, pkg)
    return _wall_data(cfg, name, res, pkg)


def standalone_html(cfg, name, res, pkg, root=None):
    data = viewer_data(cfg, name, res, pkg)
    tpl = open(os.path.join(_DIR, "viewer3d_template.html"), encoding="utf-8").read()
    three = open(os.path.join(_DIR, "vendor", "three.min.js"), encoding="utf-8").read()
    return (tpl.replace("__TITLE__", _html.escape(str(name)))
               .replace("__THREE__", three)
               .replace("__DATA__", json.dumps(data, separators=(",", ":"))))


def report_section(cfg, name, res, pkg, root):
    """Write <root>/viewer_3d.html and return the orange 'View model' button HTML."""
    os.makedirs(root, exist_ok=True)
    doc = standalone_html(cfg, name, res, pkg, root)
    with open(os.path.join(root, "viewer_3d.html"), "w", encoding="utf-8") as f:
        f.write(doc)
    return ("<p><a href='viewer_3d.html' target='_blank' rel='noopener' style=\""
            "display:inline-block;background:%s;color:#fff;font-weight:600;"
            "padding:9px 22px;border-radius:8px;text-decoration:none\">"
            "View model</a> <span class='note' style='margin-left:8px'>interactive 3D "
            "viewer (walls/frames, package D&#8725;C colors, drift/deflection shapes, "
            "story forces) &mdash; opens in a new tab</span></p>" % ORANGE)

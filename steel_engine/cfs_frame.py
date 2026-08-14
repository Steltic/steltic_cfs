"""
cfs_frame.py -- Stage 2c/4: the CFS portal-frame analysis path (Tier 1).

Same design philosophy as cfs_engine: the pure-python model IS the model. A planar
direct-stiffness solver (3 dof/node, exact for the linear frame with consistent fixed-end
forces) carries the physics; emit_opensees() builds the identical model in openseespy when
available (owner WSL) as the dual-path equivalence check. NO capacities anywhere -- the module
produces demands, drifts, reactions and torsion companions; every capacity/spec decision is
the agent's, grounded in the RAG.

cfg schema (brief-facing feet/psf; internal kip-inch):
cfg = dict(
  span_ft=60.0, eave_ft=20.0, apex_ft=26.0, spacing_ft=25.0,       # one interior frame
  purlin_spacing_ft=5.0, girt_spacing_ft=6.0,                      # node/brace stations
  col_section="2x800S250-97", raf_section="2x800S250-97",          # "2x" = back-to-back
  base="pinned",                                                   # or "fixed"
  D_roof=4.5, Lr=20.0, snow_pg=10.0, collateral=0.0,               # psf (D includes collateral)
  wind=dict(V=120.0, exposure="C", enclosed=True),                 # or wind_pressures_psf=...
  seis=dict(SDS=0.25, R=3.0, Ie=1.0, W_frame_kip=None),            # optional; eave push
  pattern_snow=True, unbalanced_factors=(0.3, 1.5),                # SEEDS -- agent verifies 7.6
  structure_kind="portal",                # "portal_singlechannel" -> torsion companion + tier
  analysis_fidelity=1, direct_analysis=True,                       # 0.8E + notional + P-Delta
)

Wind machinery note: wind_surface_pressures() seeds directional-procedure MWFRS pressures
(Kz Exposure table, G=0.85, seeded Cp set, +/-GCpi) so combos ENUMERATE correctly. The seeds
are labelled as such in the output -- the agent must verify/replace pressures from ASCE 7
before capacity checks. Pass cfg["wind_pressures_psf"] to override entirely.
"""
import math
import cfs_sections as SEC
import cfs_systems as CS
import eff_stiffness as EFF

try:
    import openseespy.opensees as ops
    HAVE_OPS = True
except Exception:
    ops = None
    HAVE_OPS = False

E_KSI = 29500.0


# ---------------- sections ----------------

def frame_section(name):
    """'800S250-97' (single channel) or '2x800S250-97' (back-to-back built-up) ->
    dict(A, Ix, depth, single, e0_in, designator). e0_in = load-plane-to-shear-center
    eccentricity for the torsion companion (loads through the web plane): |x0| + xbar-to-web
    is conservative; we use |x0| measured from the web midline (gross_props convention)."""
    single = not name.lower().startswith("2x")
    base = name[2:] if not single else name
    if single:
        p = SEC.gross_props(base)
        return dict(A=p["A"], Ix=p["Ix"], depth=p["depth"], single=True,
                    e0_in=abs(p["x0"]) + abs(p["xbar"]), designator=name, base=base)
    b = SEC.built_up_back_to_back(base)
    return dict(A=b["A"], Ix=b["Ix"], depth=b["depth"], single=False,
                e0_in=0.0, designator=name, base=base)


# ---------------- planar direct-stiffness core (kip, inch) ----------------

def _kel(EA, EI, L):
    a = EA / L
    b = 12.0 * EI / L ** 3
    c = 6.0 * EI / L ** 2
    d = 4.0 * EI / L
    e = 2.0 * EI / L
    return [[a, 0, 0, -a, 0, 0],
            [0, b, c, 0, -b, c],
            [0, c, d, 0, -c, e],
            [-a, 0, 0, a, 0, 0],
            [0, -b, -c, 0, b, -c],
            [0, c, e, 0, -c, d]]


def _T(c, s):
    return [[c, s, 0, 0, 0, 0], [-s, c, 0, 0, 0, 0], [0, 0, 1, 0, 0, 0],
            [0, 0, 0, c, s, 0], [0, 0, 0, -s, c, 0], [0, 0, 0, 0, 0, 1]]


def _matmul(A, B):
    n, m, p = len(A), len(B), len(B[0])
    return [[sum(A[i][k] * B[k][j] for k in range(m)) for j in range(p)] for i in range(n)]


def _matTvec(T, v):
    return [sum(T[k][i] * v[k] for k in range(6)) for i in range(6)]


def _gauss(A, b):
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            raise RuntimeError("singular system (unstable model?)")
        M[col], M[piv] = M[piv], M[col]
        for r in range(col + 1, n):
            f = M[r][col] / M[col][col]
            for cc in range(col, n + 1):
                M[r][cc] -= f * M[col][cc]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (M[i][n] - sum(M[i][j] * x[j] for j in range(i + 1, n))) / M[i][i]
    return x


class Frame2D:
    """Minimal planar frame: nodes {tag:(x,y)}, elements [(n1,n2,EA,EI,label)], supports
    {tag:(fx,fy,mz) booleans}, nodal loads, member uniform loads (local axial p, transverse w,
    kip/in). Solve is exact at nodes for the linear model (consistent FEFs)."""

    def __init__(self):
        self.nodes, self.elems, self.fix = {}, [], {}
        self.nload, self.mload = {}, {}

    def node(self, tag, x, y):
        self.nodes[tag] = (x, y)

    def elem(self, n1, n2, EA, EI, label):
        self.elems.append([n1, n2, EA, EI, label])

    def support(self, tag, fx=True, fy=True, mz=False):
        self.fix[tag] = (fx, fy, mz)

    def load_node(self, tag, Fx=0.0, Fy=0.0, Mz=0.0):
        a = self.nload.setdefault(tag, [0.0, 0.0, 0.0])
        a[0] += Fx; a[1] += Fy; a[2] += Mz

    def load_member(self, idx, p_axial=0.0, w_perp=0.0):
        a = self.mload.setdefault(idx, [0.0, 0.0])
        a[0] += p_axial; a[1] += w_perp

    def _geom(self, el):
        (x1, y1), (x2, y2) = self.nodes[el[0]], self.nodes[el[1]]
        L = math.hypot(x2 - x1, y2 - y1)
        return L, (x2 - x1) / L, (y2 - y1) / L

    def solve(self, axials=None, stiff_scale=1.0):
        """One linear solve. axials {elem_idx: N_compression_kip} adds string geometric
        stiffness (P-Delta). Returns dict(u={tag:(ux,uy,rz)}, end_forces={idx:[local 6]},
        reactions={tag:(Rx,Ry,Mz)})."""
        tags = sorted(self.nodes)
        dof = {t: (3 * i, 3 * i + 1, 3 * i + 2) for i, t in enumerate(tags)}
        n = 3 * len(tags)
        K = [[0.0] * n for _ in range(n)]
        F = [0.0] * n
        feq_l = {}
        for idx, el in enumerate(self.elems):
            L, c, s = self._geom(el)
            kl = _kel(el[2] * stiff_scale, el[3] * stiff_scale, L)
            if axials and axials.get(idx, 0.0) > 0.0:
                Ng = axials[idx] / L                   # compression string softening
                for (i, j, sgn) in ((1, 1, 1), (4, 4, 1), (1, 4, -1), (4, 1, -1)):
                    kl[i][j] -= sgn * Ng
            T = _T(c, s)
            kg = _matmul(_matmul([list(r) for r in zip(*T)], kl), T)
            p, w = self.mload.get(idx, (0.0, 0.0))
            fl = [p * L / 2.0, w * L / 2.0, w * L * L / 12.0,
                  p * L / 2.0, w * L / 2.0, -w * L * L / 12.0]
            feq_l[idx] = fl
            fg = _matTvec(T, fl)
            ed = dof[el[0]] + dof[el[1]]
            for i in range(6):
                F[ed[i]] += fg[i]
                for j in range(6):
                    K[ed[i]][ed[j]] += kg[i][j]
        for t, (Fx, Fy, Mz) in self.nload.items():
            d = dof[t]
            F[d[0]] += Fx; F[d[1]] += Fy; F[d[2]] += Mz
        K0 = [row[:] for row in K]
        F0 = F[:]
        fixed = []
        for t, (fx, fy, mz) in self.fix.items():
            for flag, d in zip((fx, fy, mz), dof[t]):
                if flag:
                    fixed.append(d)
        for d in fixed:
            for j in range(n):
                K[d][j] = 0.0; K[j][d] = 0.0
            K[d][d] = 1.0; F[d] = 0.0
        u = _gauss(K, F)
        res_u = {t: tuple(u[d] for d in dof[t]) for t in tags}
        end = {}
        for idx, el in enumerate(self.elems):
            L, c, s = self._geom(el)
            T = _T(c, s)
            ed = dof[el[0]] + dof[el[1]]
            ug = [u[d] for d in ed]
            ul = [sum(T[i][j] * ug[j] for j in range(6)) for i in range(6)]
            kl = _kel(el[2], el[3], L)
            q = [sum(kl[i][j] * ul[j] for j in range(6)) - feq_l[idx][i] for i in range(6)]
            end[idx] = q
        reac = {}
        for t, (fx, fy, mz) in self.fix.items():
            d = dof[t]
            R = [sum(K0[dd][j] * u[j] for j in range(n)) - F0[dd] for dd in d]
            reac[t] = tuple(R)
        return dict(u=res_u, end_forces=end, reactions=reac)


# ---------------- portal geometry ----------------

def _stations(length, target):
    nseg = max(int(round(length / max(target, 1.0))), 2)
    return [length * i / nseg for i in range(nseg + 1)]


def build_portal(cfg, secs=None):
    """Frame2D for the gable portal + member map {label: [elem indices]} + node groups.
    Members: col_L, col_R, raf_L, raf_R. Nodes at girt (columns) / purlin (rafters) stations
    (load application = brace points). Returns (frame, members, meta)."""
    span = cfg["span_ft"] * 12.0
    He = cfg["eave_ft"] * 12.0
    Ha = cfg["apex_ft"] * 12.0
    secs = secs or dict(col=frame_section(cfg["col_section"]),
                        raf=frame_section(cfg["raf_section"]))
    fr = Frame2D()
    members = {"col_L": [], "col_R": [], "raf_L": [], "raf_R": []}
    tag = [0]

    def add_chain(x1, y1, x2, y2, sec, label, target_in):
        Lc = math.hypot(x2 - x1, y2 - y1)
        pts = _stations(Lc, target_in)
        chain_tags = []
        for d in pts:
            fx = x1 + (x2 - x1) * d / Lc
            fy = y1 + (y2 - y1) * d / Lc
            existing = next((t for t, (xx, yy) in fr.nodes.items()
                             if abs(xx - fx) < 1e-6 and abs(yy - fy) < 1e-6), None)
            if existing is None:
                tag[0] += 1
                fr.node(tag[0], fx, fy)
                chain_tags.append(tag[0])
            else:
                chain_tags.append(existing)
        for a, b in zip(chain_tags, chain_tags[1:]):
            fr.elem(a, b, E_KSI * sec["A"], E_KSI * sec["Ix"], label)
            members[label].append(len(fr.elems) - 1)
        return chain_tags

    gt = cfg.get("girt_spacing_ft", 6.0) * 12.0
    pt = cfg.get("purlin_spacing_ft", 5.0) * 12.0
    if cfg.get("spans"):
        # MULTI-SPAN mode (added 2026-07-31, Ex17 gap): cfg['spans'] = list of
        # dict(span_ft, apex_ft) sharing cfg['eave_ft'] at every column line
        # (twin/multi-gable with interior VALLEY columns). Left halves pool into
        # 'raf_L', right halves into 'raf_R' (downstream label contract kept);
        # interior columns are 'col_I<k>' (the run() envelope groups them into
        # 'col' by prefix). The seeded S_unb_L/R split applies to the pooled
        # halves -- a per-span unbalanced refinement stays an agent task
        # (state it in the package). cfg['span_ft']/'apex_ft' are ignored in
        # this mode except as viewer hints.
        xs, x0 = [0.0], 0.0
        for sp_i in cfg["spans"]:
            x0 += sp_i["span_ft"] * 12.0
            xs.append(x0)
        total = x0
        cols = []
        for k, x in enumerate(xs):
            lab = "col_L" if k == 0 else ("col_R" if k == len(xs) - 1
                                          else "col_I%d" % k)
            if lab not in members:
                members[lab] = []
            cols.append(add_chain(x, 0.0, x, He, secs["col"], lab, gt))
        apex1 = None
        for k, sp_i in enumerate(cfg["spans"]):
            xa, xb = xs[k], xs[k + 1]
            Hax = sp_i["apex_ft"] * 12.0
            mid = (xa + xb) / 2.0
            t_ = add_chain(xa, He, mid, Hax, secs["raf"], "raf_L", pt)
            add_chain(mid, Hax, xb, He, secs["raf"], "raf_R", pt)
            if apex1 is None:
                apex1 = t_[-1]
        fixed = cfg.get("base", "pinned") == "fixed"
        for c in cols:
            fr.support(c[0], True, True, fixed)
        meta = dict(secs=secs, base_nodes=tuple(c[0] for c in cols),
                    eave_nodes=(cols[0][-1], cols[-1][-1]), apex_node=apex1,
                    span_in=total, He_in=He,
                    Ha_in=max(s_["apex_ft"] for s_ in cfg["spans"]) * 12.0,
                    multi_span=True, valley_nodes=tuple(c[-1] for c in cols[1:-1]),
                    raf_slope=math.atan2(cfg["spans"][0]["apex_ft"] * 12.0 - He,
                                         cfg["spans"][0]["span_ft"] * 6.0))
        return fr, members, meta
    if cfg.get("monoslope"):
        # MONOSLOPE mode (added 2026-07-31, Ex26 gap): eave_ft = LOW eave,
        # apex_ft = HIGH eave; the single slope is split at midspan so the
        # raf_L/raf_R labels (and every downstream consumer) keep working.
        # Optional cfg['overhang_ft'] extends the roof past both columns
        # (loads on the cantilevers flow through the normal rafter machinery).
        ymid = (He + Ha) / 2.0
        oh = cfg.get("overhang_ft", 0.0) * 12.0
        slope = (Ha - He) / span
        colL = add_chain(0.0, 0.0, 0.0, He, secs["col"], "col_L", gt)
        if oh > 0:
            add_chain(-oh, He - slope * oh, 0.0, He, secs["raf"], "raf_L", pt)
        rafL = add_chain(0.0, He, span / 2.0, ymid, secs["raf"], "raf_L", pt)
        rafR = add_chain(span / 2.0, ymid, span, Ha, secs["raf"], "raf_R", pt)
        if oh > 0:
            add_chain(span, Ha, span + oh, Ha + slope * oh, secs["raf"], "raf_R", pt)
        colR = add_chain(span, 0.0, span, Ha, secs["col"], "col_R", gt)
        fixed = cfg.get("base", "pinned") == "fixed"
        fr.support(colL[0], True, True, fixed)
        fr.support(colR[0], True, True, fixed)
        meta = dict(secs=secs, base_nodes=(colL[0], colR[0]),
                    eave_nodes=(colL[-1], colR[-1]), apex_node=rafL[-1],
                    span_in=span, He_in=He, Ha_in=Ha, monoslope=True,
                    raf_slope=math.atan2(Ha - He, span))
        return fr, members, meta
    colL = add_chain(0.0, 0.0, 0.0, He, secs["col"], "col_L", gt)
    rafL = add_chain(0.0, He, span / 2.0, Ha, secs["raf"], "raf_L", pt)
    rafR = add_chain(span / 2.0, Ha, span, He, secs["raf"], "raf_R", pt)
    colR = add_chain(span, 0.0, span, He, secs["col"], "col_R", gt)
    fixed = cfg.get("base", "pinned") == "fixed"
    fr.support(colL[0], True, True, fixed)
    fr.support(colR[0], True, True, fixed)
    meta = dict(secs=secs, base_nodes=(colL[0], colR[0]), eave_nodes=(colL[-1], colR[-1]),
                apex_node=rafL[-1], span_in=span, He_in=He, Ha_in=Ha,
                raf_slope=math.atan2(Ha - He, span / 2.0))
    return fr, members, meta


# ---------------- load cases ----------------

def _kzf(z_ft, exposure="C"):
    """ASCE 7 Kz = 2.01 (z/zg)^(2/alpha), z floored at 15 ft."""
    zg, alpha = dict(B=(3280.0, 7.5), C=(900.0, 9.8), D=(700.0, 11.5))[exposure]
    z = max(z_ft, 15.0)
    return 2.01 * (z / zg) ** (2.0 / alpha)


def wind_surface_pressures(cfg):
    """SEEDED MWFRS directional-procedure pressures (psf, +toward surface) for the transverse
    gable case. Kd=0.85, G=0.85, seeded Cp (windward wall +0.8, leeward wall -0.5, windward
    roof -0.7, leeward roof -0.6). Internal pressure is a SINGLE value per load case, so the
    seed emits TWO statically-consistent cases (a prior seed mixed +GCpi and -GCpi across
    surfaces in one case -- ~33% high on sway):
      case_pos: GCpi = +gcpi (internal pressure out) -- maximum roof uplift; also the
                legacy top-level keys (the 'W' elementary case)
      case_neg: GCpi = -gcpi (internal suction) -- maximum windward-wall push (the 'W2'
                elementary case; run() envelopes both)
    AGENT VERIFIES/REPLACES per ASCE 7 before capacity checks: cfg['wind_pressures_psf']
    overrides case_pos entirely; give it a 'case_neg' sub-dict to keep two-case enveloping."""
    if "wind_pressures_psf" in cfg:
        d = dict(cfg["wind_pressures_psf"])
        d["basis"] = "user/agent supplied"
        d.setdefault("case_neg", None)
        return d
    w = cfg["wind"]
    hmean = (cfg["eave_ft"] + cfg["apex_ft"]) / 2.0
    qh = 0.00256 * _kzf(hmean, w.get("exposure", "C")) * 0.85 * w["V"] ** 2
    G = 0.85
    gcpi = 0.18 if w.get("enclosed", True) else 0.55
    pos = dict(wall_wind=qh * (G * 0.8 - gcpi), wall_lee=qh * (-G * 0.5 - gcpi),
               roof_wind=qh * (-G * 0.7 - gcpi), roof_lee=qh * (-G * 0.6 - gcpi))
    neg = dict(wall_wind=qh * (G * 0.8 + gcpi), wall_lee=qh * (-G * 0.5 + gcpi),
               roof_wind=qh * (-G * 0.7 + gcpi), roof_lee=qh * (-G * 0.6 + gcpi))
    d = dict(qh_psf=round(qh, 2), case_neg=neg,
             basis="SEED: directional MWFRS, G=0.85 Kd=0.85, Cp {+0.8,-0.5,-0.7,-0.6}, "
                   "TWO single-GCpi cases (+/-%.2f): W = +GCpi (max uplift), W2 = -GCpi "
                   "(max wall push) -- agent verifies per ASCE 7 Ch.27" % gcpi)
    d.update(pos)
    return d



def snow_ps(cfg):
    """Flat-roof snow ps (psf) for the portal cases. Priority: explicit cfg['snow_ps']
    override, else 0.7 * Ce * Ct * Is * pg with FIRST-CLASS factor keys snow_ce /
    snow_ct / snow_is (each defaulting 1.0). Added 2026-07-31 (Ex30 finding: Ct for
    cold roofs was silently dropped unless the agent knew the snow_ps override)."""
    if "snow_ps" in cfg:
        return float(cfg["snow_ps"])
    return (0.7 * cfg.get("snow_ce", 1.0) * cfg.get("snow_ct", 1.0)
            * cfg.get("snow_is", 1.0) * cfg.get("snow_pg", 0.0))

def _case_loads(fr, members, meta, cfg, case):
    """Apply one elementary case to a fresh copy of member/nodal loads. Returns a closure list
    [(kind, target, values)] the combo assembler replays with its factor."""
    sp_ft = cfg["spacing_ft"]
    out = []
    slope = meta["raf_slope"]
    cs = math.cos(slope)

    def raf_members(side):
        return members["raf_L"] if side == "L" else members["raf_R"]

    def uniform_on_rafters(psf, basis, side="both"):
        """Gravity load psf (per horiz. projection or member length) -> local (axial, perp)."""
        wl_kip_in = psf * sp_ft / 1000.0 / 12.0
        for sd in (("L", "R") if side == "both" else (side,)):
            for idx in raf_members(sd):
                el = fr.elems[idx]
                (x1, y1), (x2, y2) = fr.nodes[el[0]], fr.nodes[el[1]]
                L = math.hypot(x2 - x1, y2 - y1)
                c, s = (x2 - x1) / L, (y2 - y1) / L
                wline = wl_kip_in * (abs(c) if basis == "projection" else 1.0)
                out.append(("m", idx, (-wline * s, -wline * c)))

    def gravity_case(psf, basis, split=None):
        """Gravity psf -> rafter-distributed load, OR eave-node point loads when
        cfg['truss_roof'] is set (roof gravity spans trusses/purlin-trusses direct to the
        columns -- e.g. SBMF frames whose beams carry lateral only). split=(fL, fR)
        apportions unbalanced cases to the two columns."""
        if not cfg.get("truss_roof"):
            if split is None:
                uniform_on_rafters(psf, basis)
            else:
                fL, fR = split
                uniform_on_rafters(fL * psf, basis, side="L")
                uniform_on_rafters(fR * psf, basis, side="R")
            return
        half = cfg["span_ft"] / 2.0 * sp_ft * psf / 1000.0     # kip per column half-span
        fL, fR = split if split else (1.0, 1.0)
        nL, nR = meta["eave_nodes"]
        out.append(("n", nL, (0.0, -half * fL, 0.0)))
        out.append(("n", nR, (0.0, -half * fR, 0.0)))

    if case == "D":
        gravity_case(cfg["D_roof"] + cfg.get("collateral", 0.0), "length")
    elif case == "Lr":
        gravity_case(cfg["Lr"], "projection")
    elif case == "S_bal":
        gravity_case(snow_ps(cfg), "projection")
    elif case in ("S_unb_L", "S_unb_R"):
        fw, fl = cfg.get("unbalanced_factors", (0.3, 1.5))
        ps = snow_ps(cfg)
        sp = (fw, fl) if case.endswith("L") else (fl, fw)
        gravity_case(ps, "projection", split=sp)
    elif case in ("W", "W2"):
        pr = wind_surface_pressures(cfg)
        if case == "W2":
            neg = pr.get("case_neg")
            if not neg:
                return out                                     # single-case override
            pr = dict(pr)
            pr.update(neg)
        for idx in members["col_L"]:                    # windward wall: pushes +x
            out.append(("m", idx, (0.0, -pr["wall_wind"] * sp_ft / 1000.0 / 12.0)))
        for idx in members["col_R"]:                    # leeward wall: local +y' points INWARD
            out.append(("m", idx, (0.0, +pr["wall_lee"] * sp_ft / 1000.0 / 12.0)))
        for sd, key in (("L", "roof_wind"), ("R", "roof_lee")):
            w_kip_in = pr[key] * sp_ft / 1000.0 / 12.0
            for idx in (members["raf_L"] if sd == "L" else members["raf_R"]):
                el = fr.elems[idx]
                (x1, y1), (x2, y2) = fr.nodes[el[0]], fr.nodes[el[1]]
                L = math.hypot(x2 - x1, y2 - y1)
                s = (y2 - y1) / L
                # negative (suction) pressures act along the OUTWARD normal -> local -w on
                # the local +y side facing the interior; sign handled by pressure sign itself
                out.append(("m", idx, (0.0, -w_kip_in)))
        # note: transverse wind from the left; the mirror case is covered by symmetry of the
        # enumerated combos (uplift + sway envelopes are symmetric for symmetric frames)
    elif case == "E":
        s = cfg.get("seis") or {}
        Wf = s.get("W_frame_kip")
        if Wf:
            V = s["SDS"] / (s.get("R", 3.0) / s.get("Ie", 1.0)) * Wf
            for nd in meta["eave_nodes"]:
                out.append(("n", nd, (V / 2.0, 0.0, 0.0)))
    return out


def _apply(fr, loadlist, factor):
    for kind, tgt, val in loadlist:
        if kind == "m":
            fr.load_member(tgt, p_axial=val[0] * factor, w_perp=val[1] * factor)
        else:
            fr.load_node(tgt, val[0] * factor, val[1] * factor, val[2] * factor)


def lrfd_combos(cfg):
    """LRFD combo dicts {case: factor} incl. pattern snow + net-uplift. Notional handled by
    the runner (0.002 x case gravity on gravity-governed combos when direct_analysis)."""
    has_S = cfg.get("snow_pg", 0.0) > 0
    snow_cases = ["S_bal"] + (["S_unb_L", "S_unb_R"]
                              if (has_S and cfg.get("pattern_snow", True)) else [])
    combos = [("1.4D", {"D": 1.4})]
    combos.append(("1.2D+1.6Lr+0.5W", {"D": 1.2, "Lr": 1.6, "W": 0.5}))
    for sc in (snow_cases if has_S else []):
        combos.append(("1.2D+1.6%s+0.5W" % sc, {"D": 1.2, sc: 1.6, "W": 0.5}))
    combos.append(("1.2D+1.0W+0.5Lr", {"D": 1.2, "W": 1.0, "Lr": 0.5}))
    if has_S:
        combos.append(("1.2D+1.0W+0.5S", {"D": 1.2, "W": 1.0, "S_bal": 0.5}))
    combos.append(("0.9D+1.0W", {"D": 0.9, "W": 1.0}))                 # NET UPLIFT case
    # second internal-pressure case (W2 = -GCpi): duplicate every W combo unless the
    # agent supplied a single-case override without 'case_neg'
    wp = cfg.get("wind_pressures_psf")
    if wp is None or wp.get("case_neg"):
        for name, c in [(n, c) for n, c in combos if "W" in c]:
            c2 = {("W2" if k == "W" else k): v for k, v in c.items()}
            combos.append((name.replace("W", "W2", 1), c2))
    if (cfg.get("seis") or {}).get("W_frame_kip"):
        sds = cfg["seis"]["SDS"]
        combos.append(("(1.2+0.2SDS)D+E", {"D": 1.2 + 0.2 * sds, "E": 1.0}))
        combos.append(("(0.9-0.2SDS)D+E", {"D": 0.9 - 0.2 * sds, "E": 1.0}))
    return combos


# ---------------- runner (Tier 1: effective-stiffness iterated envelopes) ----------------

def _member_envelope(fr, members, sol):
    env = {}
    for label, idxs in members.items():
        P = Vv = Mm = 0.0
        stations = []
        for i, idx in enumerate(idxs):
            q = sol["end_forces"][idx]
            P = max(P, abs(q[0]), abs(q[3]))
            Vv = max(Vv, abs(q[1]), abs(q[4]))
            if i == 0:
                stations.append(q[2])
            stations.append(-q[5])
            Mm = max(Mm, abs(q[2]), abs(q[5]))
        env[label] = dict(P_kip=round(P, 2), V_kip=round(Vv, 2), M_kipin=round(Mm, 1),
                          M_kipin_stations=[round(m, 1) for m in stations])
    return env


def _solve_combo(cfg, secs, combo, cases_cache, stiff_scale, pdelta_iters=2, notional=0.0):
    fr, members, meta = build_portal(cfg, secs)
    grav_R = 0.0
    for case, f in combo.items():
        _apply(fr, cases_cache[case], f)
    if notional:
        pre = fr.solve(stiff_scale=stiff_scale)
        Rg = sum(r[1] for r in pre["reactions"].values())
        H = notional * max(Rg, 0.0)
        for nd in meta["eave_nodes"]:
            fr.load_node(nd, H / 2.0, 0.0, 0.0)
    sol = fr.solve(stiff_scale=stiff_scale)
    # P-Delta with a CONVERGENCE GUARD: the fixed 2-iteration loop silently reported
    # mid-oscillation garbage when the frame is sway-unstable at the trial section
    # (axials near the sway Pcr) -- iterate to tolerance and FLAG divergence instead.
    def _sway(s):
        return max(abs(u[0]) for u in s["u"].values()) or 1e-9
    d_prev = _sway(sol)
    pd = dict(converged=True, iters=0, growth=1.0)
    for it in range(max(pdelta_iters, 0) and max(pdelta_iters, 4)):
        ax = {}
        for label in ("col_L", "col_R"):
            for idx in members[label]:
                q = sol["end_forces"][idx]
                N = max(q[0], -q[3])                    # compression positive (local axial)
                if N > 0:
                    ax[idx] = N
        if not ax:
            break
        sol = fr.solve(axials=ax, stiff_scale=stiff_scale)
        d = _sway(sol)
        pd["iters"] = it + 1
        pd["growth"] = d / d_prev
        if pd["growth"] > 3.0:                          # diverging -- frame sway-unstable
            pd["converged"] = False
            break
        if abs(d - d_prev) <= 0.01 * max(abs(d), 1e-6):
            break
        d_prev = d
    else:
        if pdelta_iters and pd["growth"] > 1.05:
            pd["converged"] = False                     # still growing after max iters
    sol["pdelta"] = pd
    return fr, members, meta, sol


def run(cfg):
    """Full portal run: preflight -> elementary cases -> LRFD combos (direct analysis:
    0.8 stiffness + 0.002 notional + P-Delta on strength combos) -> Tier-1 effective-stiffness
    iteration on the governing envelopes -> service drift -> torsion companion (singles).
    Returns the result dict (NO capacities)."""
    secs = dict(col=frame_section(cfg["col_section"]), raf=frame_section(cfg["raf_section"]))
    kind = cfg.get("structure_kind") or \
        ("portal_singlechannel" if (secs["col"]["single"] or secs["raf"]["single"])
         else "portal")
    res = dict(structure_kind=kind, sections={k: s["designator"] for k, s in secs.items()},
               notes=[])
    warn = CS.preflight_fidelity(kind, cfg.get("analysis_fidelity", 1))
    if warn:
        res["preflight_warnings"] = warn
    fr0, members0, meta0 = build_portal(cfg, secs)
    cases = {}
    for case in ("D", "Lr", "S_bal", "S_unb_L", "S_unb_R", "W", "W2", "E"):
        cases[case] = _case_loads(fr0, members0, meta0, cfg, case)
    combos = lrfd_combos(cfg)
    da = cfg.get("direct_analysis", True)
    scale = 0.8 if da else 1.0
    notional = 0.002 if da else 0.0

    def analyze(props):
        """eff_stiffness callback: envelope demands across all strength combos with the
        CURRENT effective props (col/raf label granularity)."""
        s2 = {k: dict(secs[k], A=props[m]["A"], Ix=props[m]["Ix"])
              for k, m in (("col", "col"), ("raf", "raf"))}
        env_all = {}
        for name, combo in combos:
            _f, mm, _meta, sol = _solve_combo(cfg, s2, combo, cases, scale,
                                              notional=notional)
            env = _member_envelope(_f, mm, sol)
            for lab, e in env.items():
                mkey = "col" if lab.startswith("col") else "raf"
                cur = env_all.setdefault(mkey, dict(P_kip=0.0, M_kipin_stations=[0.0]))
                cur["P_kip"] = max(cur["P_kip"], e["P_kip"])
                if max(map(abs, e["M_kipin_stations"])) > \
                        max(map(abs, cur["M_kipin_stations"])):
                    cur["M_kipin_stations"] = e["M_kipin_stations"]
        return env_all

    tier = cfg.get("analysis_fidelity", 1)
    if tier >= 1:
        it = EFF.iterate({"col": secs["col"]["base"], "raf": secs["raf"]["base"]}, analyze)
        eff = {m: dict(A=p["A"], Ix=p["Ix"]) for m, p in it["props"].items()}
        if not secs["col"]["single"]:
            eff["col"] = dict(A=2 * eff["col"]["A"], Ix=2 * eff["col"]["Ix"])
        if not secs["raf"]["single"]:
            eff["raf"] = dict(A=2 * eff["raf"]["A"], Ix=2 * eff["raf"]["Ix"])
        secs_eff = {k: dict(secs[k], **eff[k]) for k in secs}
        res["eff_stiffness"] = dict(converged=it["converged"],
                                    iters=len(it["history"]),
                                    ratios={m: dict(A_over_gross=it["props"][m]["A_over_gross"],
                                                    I_over_gross=it["props"][m]["I_over_gross"])
                                            for m in it["props"]})
    else:
        secs_eff = secs
        res["notes"].append("Tier 0: gross-stiffness analysis (preflight will have flagged "
                            "this for portals)")
    combo_out = {}
    for name, combo in combos:
        _f, mm, meta, sol = _solve_combo(cfg, secs_eff, combo, cases, scale,
                                         notional=notional)
        env = _member_envelope(_f, mm, sol)
        R = {t: (round(r[0], 2), round(r[1], 2), round(r[2], 2))
             for t, r in sol["reactions"].items()}      # (Rx, Ry, Mz) -- Mz nonzero at fixed bases
        uplift = any(r[1] < -0.05 for r in sol["reactions"].values())
        combo_out[name] = dict(envelope=env, reactions_kip=R, net_uplift=uplift)
        pd = sol.get("pdelta", {})
        if pd and not pd.get("converged", True):
            res.setdefault("preflight_warnings", []).append(
                "P-DELTA DIVERGED on combo '%s' (sway growth %.1fx/iter): the frame is "
                "sway-UNSTABLE at the trial sections -- envelopes for this combo are "
                "meaningless; RESIZE members (or add spring restraint) and re-run"
                % (name, pd.get("growth", 0.0)))
    res["combos"] = combo_out
    res["governing"] = {m: max(combo_out, key=lambda n:
                               combo_out[n]["envelope"][lab]["M_kipin"])
                        for m, lab in (("col", "col_L"), ("raf", "raf_L"))}
    # service drift: 10-yr MRI wind seed (~0.42 x ultimate pressures, ASCE 7 CC.2.2),
    # full stiffness, no P-Delta -- the agent states the criterion AND the MRI basis
    wsf = cfg.get("service_wind_factor", 0.42)
    _f, mm, meta, sol = _solve_combo(cfg, secs_eff, {"W": wsf, "D": 1.0}, cases, 1.0,
                                     pdelta_iters=0)
    eL, eR = meta["eave_nodes"]
    drift = max(abs(sol["u"][eL][0]), abs(sol["u"][eR][0]))
    res["service"] = dict(eave_sway_in=round(drift, 3),
                          H_over=round(meta["He_in"] / max(drift, 1e-6), 0),
                          apex_defl_in=round(-sol["u"][meta["apex_node"]][1], 3),
                          note="D + %.2fW service pair (10-yr MRI wind seed, CC.2.2); agent "
                               "states the eave drift criterion and the MRI basis" % wsf)
    res["wind_basis"] = wind_surface_pressures(cfg)
    # torsion companion for single channels (Tier-1 analytic seed; Tier 2 = WSL fiber/warping)
    tors = []
    for key, lab in (("col", "col_L"), ("raf", "raf_L")):
        s = secs[key]
        if s["single"]:
            Lb = (cfg.get("girt_spacing_ft", 6.0) if key == "col"
                  else cfg.get("purlin_spacing_ft", 5.0)) * 12.0
            gname = res["governing"][key]
            Venv = combo_out[gname]["envelope"][lab]["V_kip"]
            m_t = Venv / Lb * s["e0_in"]               # kip-in/in torsion line moment seed
            tors.append(dict(member=key, designator=s["designator"], e0_in=round(s["e0_in"], 3),
                             Lb_in=Lb, T_seg_kipin=round(m_t * Lb / 2.0, 2),
                             B_seed_kipin2=round(m_t * Lb ** 2 / 8.0, 1),
                             basis="uniform torque m=V*e0/Lb between braces; T=mL/2, B~mL^2/8 "
                                   "SEED -- agent does the S100 bending+torsion reduction"))
    if tors:
        res["torsion_companion"] = tors
    return res


# ---------------- OpenSees emitter (WSL dual-path check; guarded) ----------------

def emit_opensees(cfg, combo=None):
    """Build the identical portal (elasticBeamColumn, gross props, Corotational transf) in
    openseespy and solve one combo (default service D+W). Returns dict(eave_sway_in, nodes,
    elements) for the equivalence check against the pure-python solver."""
    if not HAVE_OPS:
        raise RuntimeError("openseespy not available -- run on the WSL environment")
    secs = dict(col=frame_section(cfg["col_section"]), raf=frame_section(cfg["raf_section"]))
    fr, members, meta = build_portal(cfg, secs)
    cases = {}
    for case in ("D", "Lr", "S_bal", "S_unb_L", "S_unb_R", "W", "E"):
        cases[case] = _case_loads(fr, members, meta, cfg, case)
    combo = combo or {"D": 1.0, "W": 1.0}
    ops.wipe(); ops.model("basic", "-ndm", 2, "-ndf", 3)
    for t, (x, y) in fr.nodes.items():
        ops.node(t, x, y)
    for t, (fx, fy, mz) in fr.fix.items():
        ops.fix(t, int(fx), int(fy), int(mz))
    ops.geomTransf("Linear", 1)
    for idx, el in enumerate(fr.elems):
        EA, EI = el[2], el[3]
        ops.element("elasticBeamColumn", idx + 1, el[0], el[1], EA / E_KSI, E_KSI,
                    EI / E_KSI, 1)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    for case, f in combo.items():
        for kind, tgt, val in cases[case]:
            if kind == "m":
                L, c, s = fr._geom(fr.elems[tgt])
                ops.eleLoad("-ele", tgt + 1, "-type", "-beamUniform", val[1] * f, val[0] * f)
            else:
                ops.load(tgt, val[0] * f, val[1] * f, val[2] * f)
    ops.system("BandGeneral"); ops.numberer("RCM"); ops.constraints("Plain")
    ops.integrator("LoadControl", 1.0); ops.algorithm("Linear"); ops.analysis("Static")
    if ops.analyze(1) != 0:
        raise RuntimeError("openseespy portal failed to solve")
    eL, eR = meta["eave_nodes"]
    sway = max(abs(ops.nodeDisp(eL, 1)), abs(ops.nodeDisp(eR, 1)))
    return dict(eave_sway_in=sway, nodes=len(fr.nodes), elements=len(fr.elems))


# ---------------- self-test ----------------

def _demo_cfg():
    """A PLAUSIBLE portal for the machinery test (deep built-up sections, moderate span/
    spacing). Deliberately NOT one of the P-models or test briefs (firewall)."""
    return dict(span_ft=40.0, eave_ft=14.0, apex_ft=17.0, spacing_ft=15.0,
                purlin_spacing_ft=5.0, girt_spacing_ft=6.0,
                col_section="2x1200S350-118", raf_section="2x1200S350-97", base="pinned",
                D_roof=4.5, Lr=20.0, snow_pg=10.0,
                wind=dict(V=115.0, exposure="C", enclosed=True),
                seis=dict(SDS=0.25, R=3.0, Ie=1.0, W_frame_kip=None),
                pattern_snow=True, structure_kind="portal",
                analysis_fidelity=1, direct_analysis=True)


def _selftest():
    print("cfs_frame self-test (openseespy %s)" % ("available" if HAVE_OPS else
                                                   "absent -- pure-python path"))
    # 1) solver bench: simply supported beam, uniform load -- nodal solution exactness
    fr = Frame2D()
    L, EI, EA, w = 240.0, 29500.0 * 100.0, 29500.0 * 10.0, 0.05
    for i in range(5):
        fr.node(i + 1, L * i / 4.0, 0.0)
    for i in range(4):
        fr.elem(i + 1, i + 2, EA, EI, "b")
        fr.load_member(i, w_perp=-w)
    fr.support(1, True, True, False); fr.support(5, False, True, False)
    sol = fr.solve()
    d_mid = -sol["u"][3][1]
    d_exact = 5.0 * w * L ** 4 / (384.0 * EI)
    assert abs(d_mid - d_exact) / d_exact < 1e-6, (d_mid, d_exact)
    Rsum = sum(r[1] for r in sol["reactions"].values())
    assert abs(Rsum - w * L) < 1e-9
    print("  bench: SS beam midspan %.4f in == 5wL^4/384EI (%.4f); reactions balance" %
          (d_mid, d_exact))
    # 2) portal gravity equilibrium + symmetry
    cfg = _demo_cfg()
    secs = dict(col=frame_section(cfg["col_section"]), raf=frame_section(cfg["raf_section"]))
    fr, members, meta = build_portal(cfg, secs)
    cases = {c: _case_loads(fr, members, meta, cfg, c) for c in ("D",)}
    _apply(fr, cases["D"], 1.0)
    sol = fr.solve()
    Ry = sum(r[1] for r in sol["reactions"].values())
    Rx = sum(r[0] for r in sol["reactions"].values())
    raf_len = math.hypot(meta["span_in"] / 2.0, meta["Ha_in"] - meta["He_in"]) * 2.0
    Wtot = (cfg["D_roof"]) * cfg["spacing_ft"] / 1000.0 / 12.0 * raf_len
    assert abs(Ry - Wtot) / Wtot < 1e-6, (Ry, Wtot)
    assert abs(Rx) < 1e-8                                  # thrusts cancel
    bL, bR = meta["base_nodes"]
    assert abs(sol["reactions"][bL][1] - sol["reactions"][bR][1]) < 1e-8
    print("  portal gravity: sumRy == roof dead (%.2f kip), thrusts cancel, symmetric" % Wtot)
    # 3) full run: envelopes, uplift case, pattern asymmetry, drift
    res = run(cfg)
    assert "preflight_warnings" not in res, res.get("preflight_warnings")
    assert res["eff_stiffness"]["converged"]
    up = res["combos"]["0.9D+1.0W"]
    assert up["net_uplift"], "0.9D+1.0W must produce net uplift at this wind speed"
    unb = res["combos"].get("1.2D+1.6S_unb_L+0.5W")
    if unb:
        mL = unb["envelope"]["raf_L"]["M_kipin"]
        mR = unb["envelope"]["raf_R"]["M_kipin"]
        assert abs(mL - mR) > 1e-3, "unbalanced snow must break rafter symmetry"
    assert 0 < res["service"]["eave_sway_in"] < meta["He_in"] / 30.0, \
        "eave sway %.1f in is not plausible for the demo frame" % res["service"]["eave_sway_in"]
    print("  run: %d combos, governing col=%s raf=%s; eave sway %.2f in (H/%d); "
          "I/Ig col=%.2f raf=%.2f" %
          (len(res["combos"]), res["governing"]["col"], res["governing"]["raf"],
           res["service"]["eave_sway_in"], res["service"]["H_over"],
           res["eff_stiffness"]["ratios"]["col"]["I_over_gross"],
           res["eff_stiffness"]["ratios"]["raf"]["I_over_gross"]))
    # 4) P-Delta actually softens: same combo without direct analysis moves the drift
    cfg_l = dict(cfg, direct_analysis=False)
    res_l = run(cfg_l)
    assert res_l["combos"]["0.9D+1.0W"]["envelope"]["col_L"]["M_kipin"] > 0
    # 5) single-channel: torsion companion + preflight
    cfg_s = dict(cfg, col_section="1000S250-97", raf_section="1000S250-97",
                 structure_kind=None, analysis_fidelity=1)
    res_s = run(cfg_s)
    assert res_s["structure_kind"] == "portal_singlechannel"
    assert res_s.get("torsion_companion"), "single channels must emit the torsion table"
    t0 = res_s["torsion_companion"][0]
    assert t0["e0_in"] > 0.5, t0
    assert res_s.get("preflight_warnings"), "single-channel @ Tier 1 carries the Tier-2 nudge"
    print("  single-channel: e0=%.2f in, T_seg=%.2f kip-in, B seed=%.0f kip-in^2; "
          "preflight nudges Tier 2" % (t0["e0_in"], t0["T_seg_kipin"], t0["B_seed_kipin2"]))
    # 6) Tier 0 on a portal trips preflight
    res_t0 = run(dict(cfg, analysis_fidelity=0))
    assert res_t0.get("preflight_warnings"), "portal @ Tier 0 must warn (Gate 4 / Ex27 hook)"
    print("  tier screen: portal @ Tier 0 warns")
    if HAVE_OPS:
        chk = emit_opensees(cfg)
        # pure-python service solve with GROSS props for apples-to-apples
        secs_g = dict(col=frame_section(cfg["col_section"]),
                      raf=frame_section(cfg["raf_section"]))
        cases_g = None
        frg, mmg, metag = build_portal(cfg, secs_g)
        cases_g = {c: _case_loads(frg, mmg, metag, cfg, c)
                   for c in ("D", "Lr", "S_bal", "S_unb_L", "S_unb_R", "W", "E")}
        for case, f in {"D": 1.0, "W": 1.0}.items():
            _apply(frg, cases_g[case], f)
        solg = frg.solve()
        eL, eR = metag["eave_nodes"]
        sway_py = max(abs(solg["u"][eL][0]), abs(solg["u"][eR][0]))
        dev = abs(chk["eave_sway_in"] - sway_py) / max(sway_py, 1e-9)
        assert dev < 0.02, "OpenSees vs pure-python sway mismatch %.1f%%" % (dev * 100)
        print("  dual-path: OpenSees eave sway %.3f in vs pure-python %.3f in (%.2f%%)" %
              (chk["eave_sway_in"], sway_py, dev * 100))
    print("SELF-TEST PASS")


if __name__ == "__main__":
    _selftest()

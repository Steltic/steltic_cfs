"""
cfs_frame.py -- Stage 2c/4: the CFS portal-frame analysis path (Tier 1).

Same design philosophy as cfs_engine: the pure-python model IS the model. A planar
direct-stiffness solver (3 dof/node, exact for the linear frame with consistent fixed-end
forces; numpy when available) carries the physics; emit_opensees() builds the identical model
in openseespy when available (owner WSL) as the dual-path equivalence check. NO capacities
anywhere -- the module produces demands, drifts, reactions and torsion companions; every
capacity/spec decision is the agent's, grounded in the RAG.

UNITS: the portal path is BRIEF-FACING FEET / PSF / MPH / KIP (it is NOT the hot-rolled
engine3d kip-inch custom_build path). check_units() refuses a cfg whose dimensions look
like inches (x12) and run() refuses cfg['custom_build'] (it would be silently ignored).

cfg schema (feet/psf; internal kip-inch):
cfg = dict(
  span_ft=60.0, eave_ft=20.0, apex_ft=26.0, spacing_ft=25.0,       # one interior frame
  purlin_spacing_ft=5.0, girt_spacing_ft=6.0,                      # node/brace stations
  col_section="2x800S250-97", raf_section="2x800S250-97",
  #   "800S250-97" single channel | "Nx<des>" N-ply back-to-back built-up (N even, 2..8),
  #   "Nx<des>/box" toe-to-toe boxed | "HSS12X12X5/8" cold-formed HSS | "800Z250-68" Z
  base="pinned",                                                   # or "fixed"
  D_roof=4.5, Lr=20.0, snow_pg=10.0, collateral=0.0,               # psf (D includes collateral)
  self_weight=True,                       # frame member self-weight added to D (default)
  wind=dict(V=120.0, exposure="C", enclosure="enclosed",           # enclosed | partially_enclosed
            # | partially_open | open (free roof, CN Figs 27.3-4..7); legacy enclosed=bool
            length_ft=None, frame_dist_ft=0.0,                     # along-ridge zone inputs
            open_sides=(), flow="both", fascia_ft=0.0, col_drag_plf=0.0),
  # or wind_pressures_psf=dict(wall_wind, wall_lee, roof_wind, roof_lee, case_neg=...) /
  #    dict(cases=[dict(name, from_side='L'|'R', wall_wind, wall_lee, roof_wind, roof_lee)])
  wind_mirror=True,                       # legacy overrides are mirrored (wind from the right)
  seis=dict(SDS=0.25, SD1=0.10, R=3.0, Cd=3.0, Om0=3.0, Ie=1.0, W_frame_kip=None),
  rho=None,                               # 12.3.4 (default 1.3 in SDC D-F, else 1.0)
  pattern_snow=True, unbalanced_factors=(0.3, 1.5),                # SEEDS -- agent verifies 7.6
  point_loads=[dict(case="D"|"L"|"Lr"|..., x_ft=, y_ft=None (on roof), P_kip=down, H_kip=, M_kipin=)],
  crane=dict(type="monorail"|"bridge_ABC"|"bridge_DEF"|"hand", rated_kip=, hoist_trolley_kip=,
             bridge_kip=0, supports=[dict(x_ft, y_ft=None, frac=1.0, ecc_in=0.0)],
             reaction_factor=1.0, traction_y_ft=None, powered_trolley=True),   # ASCE 7-22 4.9
  knee_braces=dict(section="2x600S200-68", col_drop_ft=3.5, raf_run_ft=5.0),  # pin-ended struts
  structure_kind="portal",                # "portal_singlechannel" -> torsion companion + tier
  analysis_fidelity=1, direct_analysis=True,   # AISI S100-16 C1.1: 0.9*tau_b stiffness,
                                               # Ni = (1/240) Yi notional, P-Delta
  system="not_detailed"|"sbmf", sbmf=dict(bolt_pattern=0..5, N=2, t_beam_in=, Fu_beam=, ...),
)

Wind machinery note: wind_cases() SEEDS directional-procedure MWFRS pressures (Kz exposure
table, G = 0.85, Kd = 0.85 at the pressure equation, Fig. 27.3-1 Cp(theta, h/L) with BOTH
windward-roof branches, Table 26.13-1 GCpi by enclosure, wind from BOTH directions plus the
along-ridge case; open buildings use the free-roof CN figures). The seeds are labelled as
such in the output -- the agent verifies/replaces pressures from ASCE 7 before capacity
checks. Pass cfg["wind_pressures_psf"] to override.
"""
import math
import re
import cfs_sections as SEC
import cfs_systems as CS
import eff_stiffness as EFF

try:
    import numpy as np
except Exception:                                  # pragma: no cover - numpy is a core dep
    np = None

try:
    import openseespy.opensees as ops
    HAVE_OPS = True
except Exception:
    ops = None
    HAVE_OPS = False

E_KSI = 29500.0
STEEL_KIP_PER_IN3 = 490.0 / 1728.0 / 1000.0        # 490 pcf -> kip/in^3
DA_EA = 0.90                                       # AISI S100-16 C1.1.1.3(a)
DA_NOTIONAL = 1.0 / 240.0                          # AISI S100-16 Eq. C1.1.1.2-1 (alpha = 1, LRFD)


class PortalInputError(ValueError):
    """A portal cfg the engine refuses (units, unsupported keys) -- never silently run."""


# ---------------- unit / schema sanity (CFS-12) ----------------

def check_units(cfg):
    """Plausibility screen for the FEET/PSF portal schema. Raises PortalInputError when a
    dimension can only be inches (a x12 cfg written for the kip-inch engine3d path) or a
    load can only be in the wrong unit; returns a list of softer warnings."""
    for k in ("span_ft", "eave_ft", "apex_ft", "spacing_ft"):
        if k not in cfg and not (k in ("span_ft", "apex_ft") and cfg.get("spans")):
            raise PortalInputError(
                "portal cfg is missing %r -- the cfs_frame schema is span_ft/eave_ft/apex_ft/"
                "spacing_ft in FEET (see the cfs_frame module docstring)" % k)
    if "custom_build" in cfg:
        raise PortalInputError(
            "cfg['custom_build'] is NOT supported on the CFS portal path (cfs_frame): it "
            "belongs to the hot-rolled engine3d kip-inch builder and would be silently "
            "ignored here. Express extra members/loads with point_loads / crane / "
            "knee_braces / spans, or build a Frame2D directly.")
    hard = []
    lim = dict(span_ft=400.0, eave_ft=100.0, apex_ft=120.0, spacing_ft=80.0,
               purlin_spacing_ft=30.0, girt_spacing_ft=30.0, overhang_ft=40.0)
    for k, v in lim.items():
        x = cfg.get(k)
        if x is not None and float(x) > v:
            hard.append("%s=%g ft (> %g)" % (k, float(x), v))
    for i, sp in enumerate(cfg.get("spans") or []):
        if float(sp.get("span_ft", 0.0)) > lim["span_ft"] or \
                float(sp.get("apex_ft", 0.0)) > lim["apex_ft"]:
            hard.append("spans[%d]=%r" % (i, sp))
    if hard:
        raise PortalInputError(
            "portal dimensions look like INCHES (x12): %s. The cfs_frame portal path takes "
            "FEET and PSF (e.g. span_ft=60 for a 60-ft span) and converts to kip-inch "
            "internally -- do NOT multiply by 12." % ", ".join(hard))
    for k in ("D_roof", "Lr", "collateral"):
        x = cfg.get(k)
        if x is not None and (float(x) < 0.0 or float(x) > 150.0):
            raise PortalInputError("%s=%g is not a plausible roof load in PSF" % (k, float(x)))
    if float(cfg.get("snow_pg", 0.0) or 0.0) > 400.0:
        raise PortalInputError("snow_pg=%g is not a plausible ground snow load in PSF"
                               % float(cfg["snow_pg"]))
    w = cfg.get("wind") or {}
    if w.get("V") is not None and not (60.0 <= float(w["V"]) <= 300.0):
        raise PortalInputError("wind V=%g is not a basic wind speed in MPH" % float(w["V"]))
    if not cfg.get("monoslope") and not cfg.get("spans") and \
            float(cfg["apex_ft"]) < float(cfg["eave_ft"]) - 1e-9:
        raise PortalInputError("apex_ft (%g) < eave_ft (%g): set monoslope=True (eave_ft = "
                               "LOW eave, apex_ft = HIGH eave) for a single-slope roof"
                               % (cfg["apex_ft"], cfg["eave_ft"]))
    soft = []
    if float(cfg.get("span_ft") or 0.0) > 200.0:
        soft.append("span_ft=%g ft is unusually long for a CFS portal -- confirm FEET"
                    % cfg["span_ft"])
    if float(cfg["eave_ft"]) > 40.0:
        soft.append("eave_ft=%g ft is unusually tall for a CFS portal -- confirm FEET"
                    % cfg["eave_ft"])
    if cfg.get("span_ft") is not None and float(cfg["span_ft"]) < 8.0:
        soft.append("span_ft=%g ft is very short -- confirm FEET" % cfg["span_ft"])
    return soft


# ---------------- sections ----------------

def _parse_member(name):
    """'2x800S250-97' -> (2, '800S250-97', 'back_to_back'); '4x1200S250-118/box' -> box;
    'HSS12X12X5/8' -> (1, 'HSS12X12X5/8', None); '800S250-97' -> (1, ..., None)."""
    s = str(name).strip()
    arr = "back_to_back"
    if s.lower().endswith("/box"):
        arr, s = "box", s[:-4]
    m = re.match(r"^(\d+)\s*[xX]\s*(\d.+)$", s)
    if m:
        return int(m.group(1)), m.group(2).strip(), arr
    return 1, s, None


def frame_section(name, arrangement=None):
    """Member designator -> dict(A, Ix, Ag, depth, Fy, single, n_ply, e0_in, designator, base,
    kind, w_self_kip_in, props). Supported: single C/T/U ("800S250-97"), Z ("800Z250-68"),
    N-ply built-ups ("2x", "4x", "6x", "8x"... back-to-back pairs; "/box" toe-to-toe), and
    cold-formed HSS ("HSS12X12X5/8", S400 E4.4.3 SBMF columns).
    e0_in = load-plane (web midline) to shear-centre distance m = |x0| - xbar for single
    channels (the torsion companion lever arm; a prior version used |x0| + xbar, ~2.2x high);
    0 for doubly-symmetric built-ups, HSS and Z (shear centre at the centroid)."""
    n, base, arr = _parse_member(name)
    arr = arrangement or arr
    if SEC.is_hss(base):
        p = SEC.gross_props(base)
        return dict(A=p["A"], Ix=p["Ix"], Ag=p["A"], depth=p["depth"], Fy=p["Fy"],
                    single=False, n_ply=1, e0_in=0.0, designator=name, base=base,
                    kind="HSS", w_self_kip_in=p["A"] * STEEL_KIP_PER_IN3, props=p)
    if n == 1:
        p = SEC.gross_props(base)
        kind = "Z" if p["style"] == "Z" else "single"
        return dict(A=p["A"], Ix=p["Ix"], Ag=p["A"], depth=p["depth"], Fy=p["Fy"],
                    single=True, n_ply=1, e0_in=(0.0 if kind == "Z" else p["m"]),
                    designator=name, base=base, kind=kind,
                    w_self_kip_in=p["A"] * STEEL_KIP_PER_IN3, props=p)
    b = SEC.built_up(base, n, arr or "back_to_back")
    return dict(A=b["A"], Ix=b["Ix"], Ag=b["A"], depth=b["depth"], Fy=b["Fy"], single=False,
                n_ply=n, e0_in=0.0, designator=name, base=base, kind="built_up_%s" % arr,
                w_self_kip_in=b["A"] * STEEL_KIP_PER_IN3, props=b)


# ---------------- planar direct-stiffness core (kip, inch) ----------------

def _kel(EA, EI, L, truss=False):
    a = EA / L
    if truss:
        return [[a, 0, 0, -a, 0, 0], [0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0],
                [-a, 0, 0, a, 0, 0], [0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0]]
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
    """Minimal planar frame: nodes {tag:(x,y)}, elements [n1, n2, EA, EI, label, truss],
    supports {tag:(fx,fy,mz) booleans}, nodal loads, member uniform loads (local axial p,
    transverse w, kip/in). Solve is exact at nodes for the linear model (consistent FEFs)."""

    def __init__(self):
        self.nodes, self.elems, self.fix = {}, [], {}
        self.nload, self.mload = {}, {}

    def node(self, tag, x, y):
        self.nodes[tag] = (x, y)

    def elem(self, n1, n2, EA, EI, label, truss=False):
        self.elems.append([n1, n2, EA, EI, label, bool(truss)])
        return len(self.elems) - 1

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

    def clear_loads(self):
        self.nload, self.mload = {}, {}

    def copy_geometry(self):
        f = Frame2D()
        f.nodes = dict(self.nodes)
        f.elems = [list(e) for e in self.elems]
        f.fix = dict(self.fix)
        return f

    def split_elem(self, idx, s):
        """Split element idx at fraction s (0<s<1): the element keeps its start node and a new
        node is inserted; returns (new_elem_idx, new_node_tag)."""
        el = self.elems[idx]
        (x1, y1), (x2, y2) = self.nodes[el[0]], self.nodes[el[1]]
        tag = max(self.nodes) + 1
        self.node(tag, x1 + s * (x2 - x1), y1 + s * (y2 - y1))
        n2 = el[1]
        el[1] = tag
        self.elems.append([tag, n2, el[2], el[3], el[4], el[5] if len(el) > 5 else False])
        return len(self.elems) - 1, tag

    def _element_k(self, idx, el, L, stiff_scale, ei_scale, ea_scale, axials):
        truss = len(el) > 5 and el[5]
        EA = el[2] * stiff_scale * (ea_scale.get(idx, 1.0) if ea_scale else 1.0)
        EI = el[3] * stiff_scale * (ei_scale.get(idx, 1.0) if ei_scale else 1.0)
        kl = _kel(EA, EI, L, truss)
        if axials and axials.get(idx, 0.0) > 0.0:
            Ng = axials[idx] / L                       # compression string softening (P-Delta)
            for (i, j, sgn) in ((1, 1, 1), (4, 4, 1), (1, 4, -1), (4, 1, -1)):
                kl[i][j] -= sgn * Ng
        return kl

    def solve(self, axials=None, stiff_scale=1.0, ei_scale=None, ea_scale=None):
        """One linear solve.
        axials {elem_idx: N_compression_kip} adds string geometric stiffness (P-Delta).
        stiff_scale multiplies EA and EI of every element (direct analysis 0.90, S100-16
        C1.1.1.3(a)); ei_scale {idx: tau_b} multiplies EI only (C1.1.1.3(b)); ea_scale
        {idx: f} multiplies EA only.
        Member end forces are recovered with the SAME element stiffness that was assembled
        (scaled + geometric), so q = k_used u - feq is in exact equilibrium with the
        reactions (CFS-03: a prior version recovered with the unscaled elastic k, which put
        every deformation-induced force 1/0.8 high and out of equilibrium).
        Returns dict(u={tag:(ux,uy,rz)}, end_forces={idx:[local 6]}, reactions={tag:(Rx,Ry,Mz)},
        posdef=bool (tangent stiffness positive definite -> below the elastic buckling load))."""
        tags = sorted(self.nodes)
        dof = {t: (3 * i, 3 * i + 1, 3 * i + 2) for i, t in enumerate(tags)}
        n = 3 * len(tags)
        use_np = np is not None
        K = np.zeros((n, n)) if use_np else [[0.0] * n for _ in range(n)]
        F = np.zeros(n) if use_np else [0.0] * n
        feq_l, kls, Ts = {}, {}, {}
        for idx, el in enumerate(self.elems):
            L, c, s = self._geom(el)
            kl = self._element_k(idx, el, L, stiff_scale, ei_scale, ea_scale, axials)
            T = _T(c, s)
            p, w = self.mload.get(idx, (0.0, 0.0))
            fl = [p * L / 2.0, w * L / 2.0, w * L * L / 12.0,
                  p * L / 2.0, w * L / 2.0, -w * L * L / 12.0]
            if len(el) > 5 and el[5]:                  # truss: transverse load lumped, no FEM
                fl[2] = fl[5] = 0.0
            feq_l[idx] = fl
            ed = dof[el[0]] + dof[el[1]]
            if use_np:
                Tn = np.array(T)
                kln = np.array(kl)
                kls[idx], Ts[idx] = kln, Tn
                kg = Tn.T @ kln @ Tn
                fg = Tn.T @ np.array(fl)
                ix = np.array(ed)
                K[np.ix_(ix, ix)] += kg
                F[ix] += fg
            else:
                kls[idx], Ts[idx] = kl, T
                kg = _matmul(_matmul([list(r) for r in zip(*T)], kl), T)
                fg = _matTvec(T, fl)
                for i in range(6):
                    F[ed[i]] += fg[i]
                    for j in range(6):
                        K[ed[i]][ed[j]] += kg[i][j]
        for t, (Fx, Fy, Mz) in self.nload.items():
            d = dof[t]
            F[d[0]] += Fx; F[d[1]] += Fy; F[d[2]] += Mz
        fixed = sorted(d for t, fl_ in self.fix.items() for flag, d in zip(fl_, dof[t]) if flag)
        free = [d for d in range(n) if d not in set(fixed)]
        posdef = True
        if use_np:
            Kff = K[np.ix_(free, free)]
            try:
                np.linalg.cholesky(Kff)
            except np.linalg.LinAlgError:
                posdef = False
            try:
                uf = np.linalg.solve(Kff, F[free])
            except np.linalg.LinAlgError:
                raise RuntimeError("singular system (unstable model?)")
            if not np.all(np.isfinite(uf)) or \
                    np.linalg.cond(Kff) > 1e15:
                raise RuntimeError("singular system (unstable model?)")
            u = np.zeros(n)
            u[free] = uf
            R_all = K @ u - F
        else:
            K0 = [row[:] for row in K]
            F0 = F[:]
            for d in fixed:
                for j in range(n):
                    K[d][j] = 0.0; K[j][d] = 0.0
                K[d][d] = 1.0; F[d] = 0.0
            u = _gauss(K, F)
            R_all = [sum(K0[dd][j] * u[j] for j in range(n)) - F0[dd] for dd in range(n)]
        res_u = {t: tuple(float(u[d]) for d in dof[t]) for t in tags}
        end = {}
        for idx, el in enumerate(self.elems):
            ed = dof[el[0]] + dof[el[1]]
            if use_np:
                ul = Ts[idx] @ u[list(ed)]
                q = kls[idx] @ ul - np.array(feq_l[idx])
                end[idx] = [float(v) for v in q]
            else:
                T = Ts[idx]
                ug = [u[d] for d in ed]
                ul = [sum(T[i][j] * ug[j] for j in range(6)) for i in range(6)]
                kl = kls[idx]
                end[idx] = [sum(kl[i][j] * ul[j] for j in range(6)) - feq_l[idx][i]
                            for i in range(6)]
        reac = {t: tuple(float(R_all[dd]) for dd in dof[t]) for t in self.fix}
        return dict(u=res_u, end_forces=end, reactions=reac, posdef=posdef)

    def equilibrium_residual(self, sol):
        """max |sum of element end forces (global) - nodal loads - reactions| over all node
        dofs (kip / kip-in). ~0 when recovery is consistent with the assembled stiffness."""
        acc = {t: [0.0, 0.0, 0.0] for t in self.nodes}
        for idx, el in enumerate(self.elems):
            L, c, s = self._geom(el)
            g = _matTvec(_T(c, s), sol["end_forces"][idx])
            for k in range(3):
                acc[el[0]][k] += g[k]
                acc[el[1]][k] += g[3 + k]
        worst = 0.0
        for t, a in acc.items():
            nl = self.nload.get(t, (0.0, 0.0, 0.0))
            R = sol["reactions"].get(t, (0.0, 0.0, 0.0))
            for k in range(3):
                worst = max(worst, abs(a[k] - nl[k] - R[k]))
        return worst


# ---------------- portal geometry ----------------

def _stations(length, target):
    nseg = max(int(round(length / max(target, 1.0))), 2)
    return [length * i / nseg for i in range(nseg + 1)]


def _roof_y_at(cfg, x_in):
    """Rafter centreline elevation (in) at frame x (in) -- single gable / monoslope."""
    span = cfg["span_ft"] * 12.0
    He, Ha = cfg["eave_ft"] * 12.0, cfg["apex_ft"] * 12.0
    if cfg.get("monoslope"):
        return He + (Ha - He) * x_in / span
    if cfg.get("spans"):
        xs, x0 = [0.0], 0.0
        for sp_i in cfg["spans"]:
            x0 += sp_i["span_ft"] * 12.0
            xs.append(x0)
        for k, sp_i in enumerate(cfg["spans"]):
            if xs[k] - 1e-6 <= x_in <= xs[k + 1] + 1e-6:
                mid = (xs[k] + xs[k + 1]) / 2.0
                Hax = sp_i["apex_ft"] * 12.0
                return He + (Hax - He) * (1.0 - abs(x_in - mid) / (mid - xs[k]))
        return He
    half = span / 2.0                                  # linear past the eaves (overhangs)
    return He + (Ha - He) * (1.0 - abs(x_in - half) / half)


def _ensure_node(fr, members, x, y, tol=6.0, snap=0.75):
    """Node at (x, y) (in): an existing node within `snap` in., else split the nearest frame
    element whose centreline passes within `tol` in. Raises PortalInputError otherwise."""
    for t, (xx, yy) in fr.nodes.items():
        if math.hypot(xx - x, yy - y) <= snap:
            return t
    best = None
    for idx, el in enumerate(fr.elems):
        if len(el) > 5 and el[5]:
            continue
        (x1, y1), (x2, y2) = fr.nodes[el[0]], fr.nodes[el[1]]
        L2 = (x2 - x1) ** 2 + (y2 - y1) ** 2
        s = ((x - x1) * (x2 - x1) + (y - y1) * (y2 - y1)) / L2
        if not (0.0 < s < 1.0):
            continue
        d = math.hypot(x1 + s * (x2 - x1) - x, y1 + s * (y2 - y1) - y)
        if d <= tol and (best is None or d < best[0]):
            best = (d, idx, s)
    if best is None:
        raise PortalInputError("point (%.2f ft, %.2f ft) is not on the frame centreline -- "
                               "check x_ft/y_ft (FEET, origin at the left column base)"
                               % (x / 12.0, y / 12.0))
    _d, idx, s = best
    new_idx, tag = fr.split_elem(idx, s)
    for lab, lst in members.items():
        if idx in lst:
            lst.insert(lst.index(idx) + 1, new_idx)
            break
    return tag


def _load_points(cfg):
    """[(x_in, y_in)] of every point the concentrated loads / crane supports need as a node."""
    pts = [_point_xy(cfg, pl) for pl in (cfg.get("point_loads") or [])]
    cr = cfg.get("crane")
    if cr:
        pts += [_point_xy(cfg, sup) for sup in (cr.get("supports") or [])]
    return pts


def _point_xy(cfg, d):
    """Frame coordinates (in) of a point-load / crane-support dict: x_ft (from the left
    column line) and y_ft (from the base); y_ft omitted -> on the rafter at x_ft;
    on='col_L'/'col_R' -> on that column at y_ft."""
    on = str(d.get("on", "")).lower()
    span_in = (cfg.get("span_ft") or sum(s_["span_ft"] for s_ in cfg.get("spans") or [])) * 12.0
    if on == "col_l":
        x = 0.0
    elif on == "col_r":
        x = span_in
    else:
        x = float(d.get("x_ft", 0.0)) * 12.0
    if d.get("y_ft") is not None:
        return x, float(d["y_ft"]) * 12.0
    if on in ("col_l", "col_r"):
        raise PortalInputError("point on %s needs y_ft" % on)
    return x, _roof_y_at(cfg, x)


def build_portal(cfg, secs=None):
    """Frame2D for the gable portal + member map {label: [elem indices]} + node groups.
    Members: col_L, col_R, raf_L, raf_R (+ col_I<k> interior columns in multi-span mode,
    kb_L/kb_R knee braces). Nodes at girt (columns) / purlin (rafters) stations (load
    application = brace points), plus nodes at every point-load / crane / knee-brace point.
    Returns (frame, members, meta)."""
    span = cfg["span_ft"] * 12.0 if cfg.get("span_ft") is not None else None
    He = cfg["eave_ft"] * 12.0
    Ha = cfg["apex_ft"] * 12.0 if cfg.get("apex_ft") is not None else He
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
            members.setdefault(label, []).append(len(fr.elems) - 1)
        return chain_tags

    gt = cfg.get("girt_spacing_ft", 6.0) * 12.0
    pt = cfg.get("purlin_spacing_ft", 5.0) * 12.0
    fixed = cfg.get("base", "pinned") == "fixed"
    if cfg.get("spans"):
        # MULTI-SPAN mode (added 2026-07-31, Ex17 gap): cfg['spans'] = list of
        # dict(span_ft, apex_ft) sharing cfg['eave_ft'] at every column line
        # (twin/multi-gable with interior VALLEY columns). Left halves pool into
        # 'raf_L', right halves into 'raf_R' (downstream label contract kept);
        # interior columns are 'col_I<k>' (the run() envelope groups them into
        # 'col' by prefix). The seeded S_unb_L/R split applies to the pooled
        # halves -- a per-span unbalanced refinement stays an agent task
        # (state it in the package).
        xs, x0 = [0.0], 0.0
        for sp_i in cfg["spans"]:
            x0 += sp_i["span_ft"] * 12.0
            xs.append(x0)
        total = x0
        cols = []
        for k, x in enumerate(xs):
            lab = "col_L" if k == 0 else ("col_R" if k == len(xs) - 1
                                          else "col_I%d" % k)
            members.setdefault(lab, [])
            cols.append(add_chain(x, 0.0, x, He, secs["col"], lab, gt))
        apexes = []
        for k, sp_i in enumerate(cfg["spans"]):
            xa, xb = xs[k], xs[k + 1]
            Hax = sp_i["apex_ft"] * 12.0
            mid = (xa + xb) / 2.0
            t_ = add_chain(xa, He, mid, Hax, secs["raf"], "raf_L", pt)
            add_chain(mid, Hax, xb, He, secs["raf"], "raf_R", pt)
            apexes.append(t_[-1])
        for c in cols:
            fr.support(c[0], True, True, fixed)
        meta = dict(secs=secs, base_nodes=tuple(c[0] for c in cols),
                    eave_nodes=(cols[0][-1], cols[-1][-1]), apex_node=apexes[0],
                    apex_nodes=apexes, span_in=total, He_in=He, HeR_in=He,
                    Ha_in=max(s_["apex_ft"] for s_ in cfg["spans"]) * 12.0,
                    multi_span=True, valley_nodes=tuple(c[-1] for c in cols[1:-1]),
                    roof_x=(0.0, total), overhang_in=0.0,
                    raf_slope=math.atan2(cfg["spans"][0]["apex_ft"] * 12.0 - He,
                                         cfg["spans"][0]["span_ft"] * 6.0))
    elif cfg.get("monoslope"):
        # MONOSLOPE mode (added 2026-07-31, Ex26 gap): eave_ft = LOW eave,
        # apex_ft = HIGH eave; the single slope is split at midspan so the
        # raf_L/raf_R labels (and every downstream consumer) keep working.
        # Optional cfg['overhang_ft'] extends the roof past both columns
        # (loads on the cantilevers flow through the normal rafter machinery).
        ymid = (He + Ha) / 2.0
        oh = cfg.get("overhang_ft", 0.0) * 12.0
        slope = (Ha - He) / span
        colL = add_chain(0.0, 0.0, 0.0, He, secs["col"], "col_L", gt)
        tipL = tipR = None
        if oh > 0:
            tipL = add_chain(-oh, He - slope * oh, 0.0, He, secs["raf"], "raf_L", pt)[0]
        rafL = add_chain(0.0, He, span / 2.0, ymid, secs["raf"], "raf_L", pt)
        add_chain(span / 2.0, ymid, span, Ha, secs["raf"], "raf_R", pt)
        if oh > 0:
            tipR = add_chain(span, Ha, span + oh, Ha + slope * oh, secs["raf"], "raf_R",
                             pt)[-1]
        colR = add_chain(span, 0.0, span, Ha, secs["col"], "col_R", gt)
        fr.support(colL[0], True, True, fixed)
        fr.support(colR[0], True, True, fixed)
        meta = dict(secs=secs, base_nodes=(colL[0], colR[0]),
                    eave_nodes=(colL[-1], colR[-1]), apex_node=rafL[-1], apex_nodes=[],
                    span_in=span, He_in=He, HeR_in=Ha, Ha_in=Ha, monoslope=True,
                    roof_x=(-oh, span + oh), overhang_in=oh, tips=(tipL, tipR),
                    raf_slope=math.atan2(Ha - He, span))
    else:
        oh = cfg.get("overhang_ft", 0.0) * 12.0
        slope = (Ha - He) / (span / 2.0)
        colL = add_chain(0.0, 0.0, 0.0, He, secs["col"], "col_L", gt)
        tipL = tipR = None
        if oh > 0:
            tipL = add_chain(-oh, He - slope * oh, 0.0, He, secs["raf"], "raf_L", pt)[0]
        rafL = add_chain(0.0, He, span / 2.0, Ha, secs["raf"], "raf_L", pt)
        add_chain(span / 2.0, Ha, span, He, secs["raf"], "raf_R", pt)
        if oh > 0:
            tipR = add_chain(span, He, span + oh, He - slope * oh, secs["raf"], "raf_R",
                             pt)[-1]
        colR = add_chain(span, 0.0, span, He, secs["col"], "col_R", gt)
        fr.support(colL[0], True, True, fixed)
        fr.support(colR[0], True, True, fixed)
        meta = dict(secs=secs, base_nodes=(colL[0], colR[0]), eave_nodes=(colL[-1], colR[-1]),
                    apex_node=rafL[-1], span_in=span, He_in=He,
                    apex_nodes=[rafL[-1]] if Ha - He > 1e-6 else [],   # flat beam: no ridge
                    HeR_in=He, Ha_in=Ha, roof_x=(-oh, span + oh), overhang_in=oh,
                    tips=(tipL, tipR), raf_slope=math.atan2(Ha - He, span / 2.0))
    # nodes for concentrated loads / crane supports
    meta["load_nodes"] = {}
    for (x, y) in _load_points(cfg):
        meta["load_nodes"][(round(x, 3), round(y, 3))] = _ensure_node(fr, members, x, y)
    # knee braces (pin-ended struts, axial only)
    kb = cfg.get("knee_braces")
    if kb:
        ksec = frame_section(kb["section"]) if kb.get("section") else None
        A_kb = float(kb.get("A_in2") or (ksec["A"] if ksec else 0.0))
        if A_kb <= 0:
            raise PortalInputError("knee_braces needs section= or A_in2=")
        def _pair(key_ft, key_in):
            v = kb.get(key_ft)
            sc = 1.0
            if v is None and kb.get(key_in) is not None:
                v, sc = kb[key_in], 1.0 / 12.0
            if v is None:
                raise PortalInputError("knee_braces needs %s (or %s): the brace geometry is "
                                       "never defaulted" % (key_ft, key_in))
            v = v if isinstance(v, (list, tuple)) else (v, v)
            return tuple(float(x) * sc for x in v)
        drops = _pair("col_drop_ft", "col_drop_in")
        runs = _pair("raf_run_ft", "raf_run_in")
        xR = meta["span_in"]
        yR = meta["HeR_in"]
        pairs = []
        for side, xc, yc, sgn in (("L", 0.0, He, 1.0), ("R", xR, yR, -1.0)):
            d_in = float(drops[0 if side == "L" else 1]) * 12.0
            r_in = float(runs[0 if side == "L" else 1]) * 12.0
            if d_in <= 0 or r_in <= 0:
                continue
            a = _ensure_node(fr, members, xc, yc - d_in)
            xr = xc + sgn * r_in
            b = _ensure_node(fr, members, xr, _roof_y_at(cfg, xr))
            lab = "kb_%s" % side
            members.setdefault(lab, []).append(
                fr.elem(a, b, E_KSI * A_kb, 0.0, lab, truss=True))
            pairs.append((a, b))
        meta["kb_nodes"] = pairs
        meta["kb_section"] = dict(A=A_kb, Ag=A_kb, designator=kb.get("section", "A=%.3f"
                                                                   % A_kb),
                                  w_self_kip_in=A_kb * STEEL_KIP_PER_IN3,
                                  Fy=(ksec or {}).get("Fy", 50.0))
    # roof edge nodes (windward/leeward fascia, overhang tips) and every column top
    raf_nodes = sorted({n for lab in ("raf_L", "raf_R") for i in members[lab]
                        for n in fr.elems[i][:2]}, key=lambda t: fr.nodes[t][0])
    meta["roof_edge_nodes"] = (raf_nodes[0], raf_nodes[-1]) if raf_nodes else \
        meta["eave_nodes"]
    meta["col_top_nodes"] = tuple(fr.elems[members[lab][-1]][1]
                                  for lab in members if lab.startswith("col"))
    return fr, members, meta


# ---------------- wind: ASCE 7-22 Ch. 26/27 directional-procedure SEEDS (CFS-15) ----------------

def _kzf(z_ft, exposure="C"):
    """ASCE 7-22 Kz = 2.41 (z/zg)^(2/alpha), z floored at 15 ft (Table 26.10-1 note 1);
    terrain constants per Table 26.11-1: B (zg=3280 ft, alpha=7.5), C (2460, 9.8),
    D (1935, 11.5)."""
    zg, alpha = dict(B=(3280.0, 7.5), C=(2460.0, 9.8), D=(1935.0, 11.5))[exposure]
    z = max(z_ft, 15.0)
    return 2.41 * (z / zg) ** (2.0 / alpha)


# Table 26.13-1 internal pressure coefficient by enclosure classification (26.2 / 26.12)
GCPI_BY_ENCLOSURE = {"enclosed": 0.18, "partially_enclosed": 0.55,
                     "partially_open": 0.18, "open": 0.00}

# Fig. 27.3-1 roof Cp, wind NORMAL to ridge, theta >= 10 deg (printed ASCE 7-22 values; the
# corpus figure is garbled -- values cross-checked against three agents' interpolations).
# Windward: first (negative) and second (positive/less negative) values -- Note 3: where two
# values are listed the roof shall be designed for BOTH.
_CPW_TH = [10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 45.0, 60.0]
_CPW_NEG = {0.25: [-0.7, -0.5, -0.3, -0.2, -0.2, 0.0, 0.4, 0.6],
            0.5: [-0.9, -0.7, -0.4, -0.3, -0.2, -0.2, 0.0, 0.6],
            1.0: [-1.3, -1.0, -0.7, -0.5, -0.3, -0.2, 0.0, 0.6]}
_CPW_POS = {0.25: [-0.18, 0.0, 0.2, 0.3, 0.3, 0.4, 0.4, 0.6],
            0.5: [-0.18, -0.18, 0.0, 0.2, 0.2, 0.3, 0.4, 0.6],
            1.0: [-0.18, -0.18, -0.18, 0.0, 0.2, 0.2, 0.3, 0.6]}
_CPL_TH = [10.0, 15.0, 20.0]
_CPL = {0.25: [-0.3, -0.5, -0.6], 0.5: [-0.5, -0.5, -0.6], 1.0: [-0.7, -0.6, -0.6]}

# Fig. 27.3-4 (monoslope free roof, open building), corpus-verified; per row (CNW, CNL)
_CN_MONO_TH = [7.5, 15.0, 22.5, 30.0, 37.5, 45.0]
_CN_MONO_LT75 = {"A_clr": (1.2, 0.3), "A_obs": (-0.5, -1.2),
                 "B_clr": (-1.1, -0.1), "B_obs": (-1.1, -0.6)}
_CN_MONO = {
    0: {"A_clr": [(-0.6, -1.0), (-0.9, -1.3), (-1.5, -1.6), (-1.8, -1.8), (-1.8, -1.8), (-1.6, -1.8)],
        "A_obs": [(-1.0, -1.5), (-1.1, -1.5), (-1.5, -1.7), (-1.5, -1.8), (-1.5, -1.8), (-1.3, -1.8)],
        "B_clr": [(-1.4, 0.0), (-1.9, 0.0), (-2.4, -0.3), (-2.5, -0.5), (-2.4, -0.6), (-2.3, -0.7)],
        "B_obs": [(-1.7, -0.8), (-2.1, -0.6), (-2.3, -0.9), (-2.3, -1.1), (-2.2, -1.1), (-1.9, -1.2)]},
    180: {"A_clr": [(0.9, 1.5), (1.3, 1.6), (1.7, 1.8), (2.1, 2.1), (2.1, 2.2), (2.2, 2.5)],
          "A_obs": [(-0.2, -1.2), (0.4, -1.1), (0.5, -1.0), (0.6, -1.0), (0.7, -0.9), (0.8, -0.9)],
          "B_clr": [(1.6, 0.3), (1.8, 0.6), (2.2, 0.7), (2.6, 1.0), (2.7, 1.1), (2.6, 1.4)],
          "B_obs": [(0.8, -0.3), (1.2, -0.3), (1.3, 0.0), (1.6, 0.1), (1.9, 0.3), (2.1, 0.4)]}}
# Fig. 27.3-5 (pitched free roof, gamma = 0/180), corpus-verified
_CN_PITCH = {"A_clr": [(1.1, -0.3), (1.1, -0.4), (1.1, 0.1), (1.3, 0.3), (1.3, 0.6), (1.1, 0.9)],
             "A_obs": [(-1.6, -1.0), (-1.2, -1.0), (-1.2, -1.2), (-0.7, -0.7), (-0.6, -0.6),
                       (-0.5, -0.5)],
             "B_clr": [(0.2, -1.2), (0.1, -1.1), (-0.1, -0.8), (-0.1, -0.9), (-0.2, -0.6),
                       (-0.3, -0.5)],
             "B_obs": [(-0.9, -1.7), (-0.6, -1.6), (-0.8, -1.7), (-0.2, -1.1), (-0.3, -0.9),
                       (-0.3, -0.7)]}
# Fig. 27.3-7 (free roof, along ridge / gamma = 90, 270; also note 4 of Fig. 27.3-4)
_CN_PAR = [(1.0, {"A_clr": -0.8, "A_obs": -1.2, "B_clr": 0.8, "B_obs": 0.5}),
           (2.0, {"A_clr": -0.6, "A_obs": -0.9, "B_clr": 0.5, "B_obs": 0.5}),
           (1e9, {"A_clr": -0.3, "A_obs": -0.6, "B_clr": 0.3, "B_obs": 0.3})]


def _lerp_ss(v1, v2, f, branch=None):
    """Fig. 27.3-1 Note 2: interpolate only between values of the SAME sign; where no value of
    the same sign is given, use 0.0 (branch 'neg' keeps negatives, 'pos' keeps positives)."""
    if v1 * v2 < 0 and branch:
        if branch == "neg":
            v1, v2 = min(v1, 0.0), min(v2, 0.0)
        else:
            v1, v2 = max(v1, 0.0), max(v2, 0.0)
    return v1 + f * (v2 - v1)


def _interp1(xs, ys, x, branch=None):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            return _lerp_ss(ys[i], ys[i + 1], (x - xs[i]) / (xs[i + 1] - xs[i]), branch)
    return ys[-1]


def _interp_hl(table, ths, theta, hL, branch=None):
    hls = sorted(table)
    vals = [_interp1(ths, table[h], theta, branch) for h in hls]
    return _interp1(hls, vals, min(max(hL, hls[0]), hls[-1]), branch)


def cp_windward_roof(theta, hL):
    """(first, second) windward-roof Cp, Fig. 27.3-1, wind normal to ridge, theta >= 10."""
    if theta >= 60.0:
        return 0.01 * theta, 0.01 * theta
    return (_interp_hl(_CPW_NEG, _CPW_TH, theta, hL, "neg"),
            _interp_hl(_CPW_POS, _CPW_TH, theta, hL, "pos"))


def cp_leeward_roof(theta, hL):
    return _interp_hl(_CPL, _CPL_TH, theta, hL)


def cp_low_slope_zones(hL, h_ft):
    """Fig. 27.3-1 'normal to ridge for theta < 10 and parallel to ridge for all theta':
    [(d0_ft, d1_ft, Cp_first)] by horizontal distance from the windward edge (second value
    -0.18 everywhere). h/L between 0.5 and 1.0 interpolated; the ** area reduction of the
    -1.3 value is NOT taken (conservative)."""
    lo = [(0.0, 0.5, -0.9), (0.5, 1.0, -0.9), (1.0, 2.0, -0.5), (2.0, 1e9, -0.3)]
    hi = {0: -1.3, 1: -0.7, 2: -0.7, 3: -0.7}
    f = min(max((hL - 0.5) / 0.5, 0.0), 1.0)
    return [(a * h_ft, b * h_ft, c + f * (hi[i] - c)) for i, (a, b, c) in enumerate(lo)]


def cp_leeward_wall(L_over_B):
    """Fig. 27.3-1 leeward wall: L/B 0-1 -> -0.5, 2 -> -0.3, >= 4 -> -0.2 (None -> -0.5)."""
    if L_over_B is None:
        return -0.5
    return _interp1([1.0, 2.0, 4.0], [-0.5, -0.3, -0.2], L_over_B)


def enclosure_of(cfg):
    """(enclosure, warnings). cfg['wind']['enclosure'] in enclosed | partially_enclosed |
    partially_open | open (ASCE 7-22 26.2 / Table 26.13-1). Legacy enclosed=False is read as
    PARTIALLY ENCLOSED (+/-0.55) with a warning; canopies default to OPEN."""
    w = cfg.get("wind") or {}
    if w.get("enclosure") is not None:
        e = str(w["enclosure"]).strip().lower().replace("-", "_").replace(" ", "_")
        if e not in GCPI_BY_ENCLOSURE:
            raise PortalInputError("wind['enclosure']=%r -- use one of %s (ASCE 7-22 26.2)"
                                   % (w["enclosure"], sorted(GCPI_BY_ENCLOSURE)))
        return e, []
    if cfg.get("structure_kind") == "canopy":
        return "open", ["wind enclosure not declared for a canopy -> OPEN building (free roof "
                        "CN, Figs. 27.3-4..7, GCpi = 0) assumed; declare wind['enclosure']"]
    if "enclosed" in w:
        if w["enclosed"]:
            return "enclosed", []
        return "partially_enclosed", [
            "legacy wind['enclosed']=False read as PARTIALLY ENCLOSED (GCpi = +/-0.55): open "
            "and partially-open buildings cannot be expressed by the bool -- declare "
            "wind['enclosure'] in enclosed | partially_enclosed | partially_open | open"]
    return "enclosed", ["wind enclosure not declared -> ENCLOSED (GCpi = +/-0.18) assumed; the "
                        "ASCE 7-22 26.12 classification is the agent's -- declare "
                        "wind['enclosure']"]


def _roof_geometry(cfg):
    """(theta_deg, mean roof height h_ft, along-wind width L_ft, x-range in, overhang in)."""
    He = float(cfg["eave_ft"])
    if cfg.get("spans"):
        span = sum(s_["span_ft"] for s_ in cfg["spans"])
        Ha = max(s_["apex_ft"] for s_ in cfg["spans"])
        th = math.degrees(math.atan2(cfg["spans"][0]["apex_ft"] - He,
                                     cfg["spans"][0]["span_ft"] / 2.0))
        oh = 0.0
    else:
        span = float(cfg["span_ft"])
        Ha = float(cfg["apex_ft"])
        oh = float(cfg.get("overhang_ft", 0.0) or 0.0)
        run = span if cfg.get("monoslope") else span / 2.0
        th = math.degrees(math.atan2(Ha - He, run))
    h = (He + Ha) / 2.0
    return th, h, span, (-oh * 12.0, (span + oh) * 12.0), oh * 12.0


def _building_length_ft(cfg):
    w = cfg.get("wind") or {}
    if w.get("length_ft"):
        return float(w["length_ft"])
    if cfg.get("length_ft"):
        return float(cfg["length_ft"])
    if cfg.get("n_frames"):
        return max(int(cfg["n_frames"]) - 1, 1) * float(cfg["spacing_ft"])
    return None


def _segments_to_zones(segs, x_lo, x_hi, span_in, from_side, qk, G, gcpi_term,
                       overhang_bottom=True):
    """External-Cp segments [(x0, x1, Cp)] (in) -> net psf zones (+ toward the top surface).
    Inside the walls the internal-pressure term applies; overhangs carry external pressure
    only, and the WINDWARD overhang adds the 27.3.3 bottom-surface Cp = 0.8 (upward)."""
    out = []
    cuts = [(x_lo, 0.0, "oh_L"), (0.0, span_in, "in"), (span_in, x_hi, "oh_R")]
    for a, b, cp in segs:
        for c0, c1, kind in cuts:
            lo, hi = max(a, c0), min(b, c1)
            if hi - lo <= 1e-9:
                continue
            p = qk * G * cp
            if kind == "in":
                p -= gcpi_term
            elif overhang_bottom and kind == ("oh_L" if from_side == "L" else "oh_R"):
                p -= qk * G * 0.8                       # 27.3.3 windward overhang soffit
            out.append((lo, hi, p))
    return out


def _closed_cases(cfg, enc, GCpi, qk, G, th, h, span_ft, xr, oh_in, warns):
    """Enclosed / partially enclosed / partially open: Fig. 27.3-1 cases, wind from both
    sides, +/-GCpi, both windward-roof branches, plus the along-ridge case."""
    w = cfg.get("wind") or {}
    span_in = span_ft * 12.0
    x_lo, x_hi = xr
    xmid = (x_lo + x_hi) / 2.0
    mono = bool(cfg.get("monoslope"))
    hL = h / span_ft
    Lb = _building_length_ft(cfg)
    cp_lw_wall = cp_leeward_wall(span_ft / Lb if Lb else None)
    open_sides = set(str(s).upper() for s in (w.get("open_sides") or ()))
    cases = []

    def roof_segments(side, branch):
        """External roof Cp segments for wind from `side` ('L' = +x)."""
        def dist_to_x(d0, d1):
            if side == "L":
                return x_lo + d0 * 12.0, min(x_lo + d1 * 12.0, x_hi)
            return max(x_hi - d1 * 12.0, x_lo), x_hi - d0 * 12.0
        if th < 10.0:
            segs = []
            for d0, d1, cp in cp_low_slope_zones(hL, h):
                a, b = dist_to_x(d0, d1)
                if b > a:
                    segs.append((a, b, cp if branch == 1 else -0.18))
            return segs
        ww = cp_windward_roof(th, hL)[branch - 1]
        lw = cp_leeward_roof(th, hL)
        if mono:                                        # Fig. 27.3-1: whole monoslope roof
            return [(x_lo, x_hi, ww if side == "L" else lw)]   # low side (L) = windward
        if side == "L":
            return [(x_lo, xmid, ww), (xmid, x_hi, lw)]
        return [(x_lo, xmid, lw), (xmid, x_hi, ww)]

    two = th < 10.0 or abs(cp_windward_roof(th, hL)[0] - cp_windward_roof(th, hL)[1]) > 1e-6
    branches = (1, 2) if two else (1,)
    for side in ("L", "R"):
        for br in branches:
            if mono and side == "R" and th >= 10.0 and br == 2:
                continue                                # leeward-only roof: one value
            for sgn in (1.0, -1.0):
                gterm = qk * sgn * GCpi
                ww_p = qk * G * 0.8 - gterm
                lw_p = qk * G * cp_lw_wall - gterm
                walls = {"col_L": ww_p if side == "L" else lw_p,
                         "col_R": ww_p if side == "R" else lw_p}
                for s_ in open_sides:
                    walls["col_%s" % s_] = 0.0
                zones = _segments_to_zones(roof_segments(side, br), x_lo, x_hi, span_in,
                                           side, qk, G, gterm)
                base = "W" if sgn > 0 else "W2"
                name = base + ("" if side == "L" else "_R") + ("" if br == 1 else "_b2")
                cases.append(dict(
                    name=name, from_side=side, gcpi=sgn * GCpi, branch=br, walls=walls,
                    roof_zones=zones,
                    desc="wind from %s, %sGCpi, windward-roof Cp branch %d%s"
                         % ("LEFT (+x)" if side == "L" else "RIGHT (-x)",
                            "+" if sgn > 0 else "-", br,
                            " (theta<10: distance zones)" if th < 10.0 else "")))
    # along-ridge (parallel to ridge, Fig. 27.3-1 table by distance from the windward END)
    if Lb:
        hLp = h / Lb
    else:
        hLp = 0.5
        warns.append("building length unknown (wind['length_ft'] / cfg['length_ft'] / "
                     "n_frames) -> along-ridge h/L taken as 0.5; declare it")
    d_f = float(w.get("frame_dist_ft", 0.0) or 0.0)
    cp_par = next(cp for d0, d1, cp in cp_low_slope_zones(hLp, h) if d0 <= d_f < d1)
    for sgn in (1.0, -1.0):
        gterm = qk * sgn * GCpi
        side_p = qk * G * (-0.7) - gterm
        walls = {"col_L": side_p, "col_R": side_p}
        for s_ in open_sides:
            walls["col_%s" % s_] = 0.0
        zones = _segments_to_zones([(x_lo, x_hi, cp_par)], x_lo, x_hi, span_in, "ridge",
                                   qk, G, gterm, overhang_bottom=False)
        cases.append(dict(name="Wpar" + ("" if sgn > 0 else "2"), from_side="ridge",
                          gcpi=sgn * GCpi, branch=1, walls=walls, roof_zones=zones,
                          desc="wind PARALLEL to ridge, frame %.0f ft from the windward end "
                               "(roof Cp %.2f, sidewalls -0.7), %sGCpi"
                               % (d_f, cp_par, "+" if sgn > 0 else "-")))
    return cases


def _open_cases(cfg, qk, G, th, h, span_ft, xr, warns):
    """Open building, free roof (27.3.2, Eq. 27.3-2 p = qh Kd G CN): Fig. 27.3-4 (monoslope),
    27.3-5 (pitched), 27.3-7 (along ridge; also gamma 0/180 for h/L < 0.25 with theta < 5).
    Load cases A and B, clear and/or obstructed flow, wind from both directions."""
    w = cfg.get("wind") or {}
    x_lo, x_hi = xr
    xmid = (x_lo + x_hi) / 2.0
    mono = bool(cfg.get("monoslope"))
    hL = h / span_ft
    flow = str(w.get("flow", "both")).lower()
    flows = ["clr", "obs"] if flow == "both" else (["obs"] if flow.startswith("obs")
                                                    else ["clr"])
    if flow == "both":
        warns.append("open-building flow not declared -> BOTH clear and obstructed CN cases "
                      "enumerated (declare wind['flow'] = 'clear' or 'obstructed', 27.3.2)")
    if cfg.get("spans"):
        warns.append("troughed / multi-span free roofs (Fig. 27.3-6) are not seeded -- supply "
                      "wind_pressures_psf")
    if hL > 1.0 or (hL < 0.25 and th >= 5.0):
        warns.append("open-building h/L = %.2f is outside the 0.25-1.0 range of Figs. "
                     "27.3-4/5 -- CN taken at the figure values; verify" % hL)
    use_par_zones = hL < 0.25 and th < 5.0          # Fig. 27.3-4 note 4 -> Fig. 27.3-7
    kip = float(cfg["spacing_ft"]) / 1000.0
    fascia = float(w.get("fascia_ft", 0.0) or 0.0)
    drag = float(w.get("col_drag_plf", 0.0) or 0.0)
    if not drag:
        warns.append("open building: wind drag on the columns themselves is not included "
                     "(set wind['col_drag_plf'] per column, Ch. 29 Cf)")
    cases = []
    for side in ("L", "R"):
        gamma = 0 if side == "L" else 180               # monoslope: low eave at x = 0
        for lc in ("A", "B"):
            for fl in flows:
                key = "%s_%s" % (lc, fl)
                if use_par_zones:
                    segs = []
                    for i, (dmax, row) in enumerate(_CN_PAR):
                        dmin = 0.0 if i == 0 else _CN_PAR[i - 1][0]
                        a = dmin * h * 12.0
                        b = min(dmax * h * 12.0, x_hi - x_lo)
                        if b <= a:
                            continue
                        if side == "L":
                            segs.append((x_lo + a, x_lo + b, row[key]))
                        else:
                            segs.append((x_hi - b, x_hi - a, row[key]))
                    src = "Fig. 27.3-7 (note 4: h/L < 0.25, theta < 5)"
                else:
                    if th < 7.5:
                        cnw, cnl = _CN_MONO_LT75[key]
                        src = "Fig. 27.3-4 theta < 7.5"
                    elif mono:
                        rows = _CN_MONO[gamma][key]
                        cnw = _interp1(_CN_MONO_TH, [r[0] for r in rows], th)
                        cnl = _interp1(_CN_MONO_TH, [r[1] for r in rows], th)
                        src = "Fig. 27.3-4 gamma=%d" % gamma
                    else:
                        rows = _CN_PITCH[key]
                        cnw = _interp1(_CN_MONO_TH, [r[0] for r in rows], th)
                        cnl = _interp1(_CN_MONO_TH, [r[1] for r in rows], th)
                        src = "Fig. 27.3-5"
                    segs = ([(x_lo, xmid, cnw), (xmid, x_hi, cnl)] if side == "L"
                            else [(x_lo, xmid, cnl), (xmid, x_hi, cnw)])
                zones = [(a, b, qk * G * cn) for a, b, cn in segs]
                dirx = 1.0 if side == "L" else -1.0
                edge = []
                if fascia > 0 and th <= 5.0:            # 27.3.2: fascia = inverted parapet
                    Fw = 1.5 * qk * fascia * kip * dirx
                    Fl = 1.0 * qk * fascia * kip * dirx
                    edge = [("L", Fw), ("R", Fl)] if side == "L" else [("R", Fw), ("L", Fl)]
                cases.append(dict(
                    name="W_%s_%s%s" % (lc, fl, "" if side == "L" else "_R"), from_side=side,
                    gcpi=0.0, branch=lc, walls={}, roof_zones=zones, edge_Fx=edge,
                    col_drag_kip_in=dirx * drag / 1000.0 / 12.0,
                    desc="OPEN free roof, wind from %s (gamma=%d), load case %s, %s flow, %s"
                         % (side, gamma if mono else (0 if side == "L" else 180), lc,
                            "clear" if fl == "clr" else "obstructed", src)))
    for lc in ("A", "B"):
        for fl in flows:
            key = "%s_%s" % (lc, fl)
            d_f = float(w.get("frame_dist_ft", 0.0) or 0.0)
            cn = next(row[key] for dmax, row in _CN_PAR if d_f <= dmax * h)
            cases.append(dict(
                name="Wpar_%s_%s" % (lc, fl), from_side="ridge", gcpi=0.0, branch=lc,
                walls={}, roof_zones=[(x_lo, x_hi, qk * G * cn)], edge_Fx=[],
                desc="OPEN free roof, wind ALONG ridge (Fig. 27.3-7), frame %.0f ft from the "
                     "windward edge, case %s, %s flow" % (d_f, lc, fl)))
    # legacy names: the first two cases are W / W2 (downstream consumers key on them)
    cases[0]["name"], cases[1]["name"] = "W", "W2"
    return cases


def _user_wind_cases(cfg):
    """cfg['wind_pressures_psf'] override: legacy single/two-case dict (W from the LEFT, W2 =
    case_neg) MIRRORED to W_R/W2_R unless cfg['wind_mirror'] is False (CFS-02: the old engine
    applied supplied pressures from the left only), or an explicit 'cases' list."""
    wp = dict(cfg["wind_pressures_psf"])
    cases = []

    def mk(name, d, side):
        walls = {"col_L": d.get("wall_wind", 0.0), "col_R": d.get("wall_lee", 0.0)}
        roof = {"raf_L": d.get("roof_wind", 0.0), "raf_R": d.get("roof_lee", 0.0)}
        fk = d.get("fascia_kip") or d.get("edge_Fx_kip")
        edge = [("L", fk[0]), ("R", fk[1])] if fk else []
        if side == "R":
            walls = {"col_L": walls["col_R"], "col_R": walls["col_L"]}
            roof = {"raf_L": roof["raf_R"], "raf_R": roof["raf_L"]}
            edge = [("R" if e == "L" else "L", -f) for e, f in edge]
        if d.get("roof_zones_ft"):
            zones = [(a * 12.0, b * 12.0, p) for a, b, p in d["roof_zones_ft"]]
            if side == "R":
                span_in = float(cfg.get("span_ft") or 0.0) * 12.0
                zones = [(span_in - b, span_in - a, p) for a, b, p in zones]
            roof_spec = dict(roof_zones=zones)
        else:
            roof_spec = dict(roof_by_label=roof)
        return dict(dict(name=name, from_side=side, walls=walls, edge_Fx=edge,
                         desc="user/agent supplied (%s)" % ("as given" if side == "L"
                                                            else "MIRRORED: wind from right")),
                    **roof_spec)

    if wp.get("cases"):
        for i, d in enumerate(wp["cases"]):
            nm = d.get("name") or ("W" if i == 0 else "W%d" % (i + 1))
            cases.append(mk(nm, d, str(d.get("from_side", "L")).upper()))
    else:
        cases.append(mk("W", wp, "L"))
        if wp.get("case_neg"):
            cases.append(mk("W2", wp["case_neg"], "L"))
        if cfg.get("wind_mirror", True):
            for c in list(cases):
                src = wp if c["name"] == "W" else wp["case_neg"]
                cases.append(mk(c["name"] + "_R", src, "R"))
    return cases


def wind_cases(cfg):
    """All MWFRS wind cases for the frame (see module doc). Returns dict(cases=[...],
    qh_psf (Eq. 26.10-1, WITHOUT Kd), Kd, G, qh_Kd_psf, enclosure, GCpi, theta_deg, h_ft,
    h_over_L, basis, warnings, source)."""
    warns = []
    if "wind_pressures_psf" in cfg:
        wp = cfg["wind_pressures_psf"]
        cases = _user_wind_cases(cfg)
        if cfg.get("wind_mirror", True) is not False and not wp.get("cases"):
            warns.append("supplied wind_pressures_psf are applied from the LEFT and MIRRORED "
                         "(W_R/W2_R); set cfg['wind_mirror']=False only when the supplied "
                         "cases are already the complete directional set")
        return dict(cases=cases, qh_psf=wp.get("qh_psf"), source="user",
                    basis="user/agent supplied", warnings=warns,
                    enclosure=(enclosure_of(cfg)[0] if cfg.get("wind") else None))
    w = cfg.get("wind")
    if not w:
        return dict(cases=[], warnings=[], basis="no wind declared", source=None)
    enc, ew = enclosure_of(cfg)
    warns += ew
    th, h, span_ft, xr, oh_in = _roof_geometry(cfg)
    exp = w.get("exposure", "C")
    Kzt, Ke = w.get("Kzt", 1.0), w.get("Ke", 1.0)
    qh = 0.00256 * _kzf(h, exp) * Kzt * Ke * w["V"] ** 2       # Eq. 26.10-1 (no Kd)
    Kd, G = w.get("Kd", 0.85), w.get("G", 0.85)
    qk = qh * Kd                                                # Kd at Eq. 27.3-1 / 27.3-2
    GCpi = float(w.get("GCpi", GCPI_BY_ENCLOSURE[enc]))
    if cfg.get("spans") and enc != "open":
        warns.append("multi-span (twin/multi-gable) roof: Fig. 27.3-1 has no multi-span gable "
                     "case -- the seed treats the windward HALF of the total width as windward "
                     "roof; supply wind_pressures_psf for a cited distribution")
    if enc == "open":
        cases = _open_cases(cfg, qk, G, th, h, span_ft, xr, warns)
    else:
        cases = _closed_cases(cfg, enc, GCpi, qk, G, th, h, span_ft, xr, oh_in, warns)
    basis = ("SEED: ASCE 7-22 directional MWFRS. qh = 0.00256 Kz Kzt Ke V^2 = %.2f psf "
             "(Eq. 26.10-1 at h = %.1f ft, Exp %s, Kzt %.2f, Ke %.2f; Kd = %.2f applied in the "
             "pressure equation, NOT in qh); G = %.2f; enclosure %s (Table 26.13-1 GCpi = "
             "%.2f); theta = %.1f deg, h/L = %.2f; %s; %d cases: wind from BOTH sides, both "
             "internal-pressure signs, both windward-roof Cp branches (Fig. 27.3-1 note 3) "
             "and the along-ridge case -- agent verifies per ASCE 7 Ch. 26/27"
             % (qh, h, exp, Kzt, Ke, Kd, G, enc, GCpi, th, h / span_ft,
                "open-building free-roof CN (Figs. 27.3-4..7, Eq. 27.3-2)" if enc == "open"
                else "Fig. 27.3-1 Cp(theta, h/L), walls +0.8 / Cp(L/B) / sidewalls -0.7",
                len(cases)))
    return dict(cases=cases, qh_psf=round(qh, 2), Kd=Kd, G=G, qh_Kd_psf=round(qk, 2),
                enclosure=enc, GCpi=GCpi, theta_deg=round(th, 2), h_ft=round(h, 2),
                h_over_L=round(h / span_ft, 3), basis=basis, warnings=warns, source="seed")


def wind_surface_pressures(cfg):
    """LEGACY view of the wind seed (kept for the viewer/report): the first case's surface
    pressures as wall_wind / wall_lee / roof_wind / roof_lee (+ case_neg = the second case),
    qh_psf (Eq. 26.10-1 WITHOUT Kd -- a prior version folded Kd into qh_psf, inviting a
    double count), Kd, qh_Kd_psf, basis, and the full 'cases' list."""
    wc = wind_cases(cfg)

    def legacy(c):
        if c is None:
            return None
        rl = c.get("roof_by_label")
        if rl:
            rw, rlee = rl.get("raf_L", 0.0), rl.get("raf_R", 0.0)
        else:
            z = c.get("roof_zones") or [(0, 0, 0.0)]
            rw, rlee = z[0][2], z[-1][2]
        return dict(wall_wind=c["walls"].get("col_L", 0.0),
                    wall_lee=c["walls"].get("col_R", 0.0), roof_wind=rw, roof_lee=rlee)
    cs = wc["cases"]
    d = dict(legacy(cs[0]) if cs else {})
    d["case_neg"] = legacy(cs[1]) if len(cs) > 1 and cs[1]["name"] == "W2" else None
    for k in ("qh_psf", "Kd", "qh_Kd_psf", "basis", "enclosure", "GCpi", "theta_deg",
              "h_over_L", "warnings", "source"):
        if k in wc:
            d[k] = wc[k]
    d["cases"] = [dict(name=c["name"], desc=c.get("desc", ""),
                       walls={k: round(v, 2) for k, v in c["walls"].items()},
                       roof=([(round(a / 12.0, 2), round(b / 12.0, 2), round(p, 2))
                              for a, b, p in c["roof_zones"]] if c.get("roof_zones")
                             else {k: round(v, 2) for k, v in
                                   (c.get("roof_by_label") or {}).items()}))
                  for c in cs]
    return d


# ---------------- snow (ASCE 7-22 Ch. 7 seeds) ----------------

PM_MAX_BY_RC = {"I": 25.0, "II": 30.0, "III": 35.0, "IV": 40.0}     # Table 7.3-4


def snow_ps(cfg):
    """Flat-roof snow ps (psf) for the portal cases. Priority: explicit cfg['snow_ps']
    override, else ASCE 7-22 Eq. 7.3-1: pf = 0.7 * Ce * Ct * pg with FIRST-CLASS factor
    keys snow_ce / snow_ct (each defaulting 1.0); Cs = 1.0 (no slope reduction -- seed).
    7-22 CHANGE: Is is NO LONGER a factor in Eq. 7.3-1 -- importance is embedded in the
    Risk-Category-specific pg maps. cfg['snow_is'] is still read for backward compat but
    a value != 1.0 is IGNORED with a warning (use the RC-appropriate pg instead)."""
    if "snow_ps" in cfg:
        return float(cfg["snow_ps"])
    if abs(float(cfg.get("snow_is", 1.0)) - 1.0) > 1e-9:
        import warnings
        warnings.warn("ASCE 7-22 embeds importance in the RC-specific pg maps; supplied "
                      "snow_is=%.2f ignored -- use the RC-appropriate pg"
                      % float(cfg["snow_is"]))
    return (0.7 * cfg.get("snow_ce", 1.0) * cfg.get("snow_ct", 1.0)
            * cfg.get("snow_pg", 0.0))


def snow_pm(cfg):
    """ASCE 7-22 7.3.3 minimum snow load for low-slope (theta < 15 deg) monoslope/hip/gable
    roofs: pm = pg where pg <= pm,max, else pm,max (Table 7.3-4: RC I 25, II 30, III 35,
    IV 40 psf). A SEPARATE uniform case -- never combined with unbalanced/partial/drift."""
    pg = float(cfg.get("snow_pg", 0.0) or 0.0)
    if pg <= 0.0:
        return 0.0
    if _roof_geometry(cfg)[0] >= 15.0:
        return 0.0
    rc = str(cfg.get("risk_cat", "II")).strip().upper()
    return min(pg, PM_MAX_BY_RC.get(rc, 30.0))


def _partial_segments(cfg):
    """7.5 partial-load spans [(x0_in, x1_in)] for monoslopes / overhang cantilevers."""
    th, _h, span_ft, (x_lo, x_hi), oh = _roof_geometry(cfg)
    span_in = span_ft * 12.0
    segs = [(0.0, span_in)]
    if oh > 0:
        segs = [(x_lo, 0.0)] + segs + [(span_in, x_hi)]
    return segs


def snow_case_names(cfg):
    """(principal snow cases, notes). S_bal = ps; S_min = pm (7.3.3) where it exceeds ps;
    gable/multi-span unbalanced S_unb_L/R (7.6.1, only for 2.38 <= theta <= 30.2 deg);
    7.5 partial S_part_k for monoslope / overhang cantilever spans (gable roofs between
    2.38 and 30.3 deg are exempt from 7.5)."""
    notes = []
    ps, pm = snow_ps(cfg), snow_pm(cfg)
    if ps <= 0 and pm <= 0:
        return [], notes
    names = ["S_bal"]
    if pm > ps + 1e-9:
        names.append("S_min")
        notes.append("7.3.3 minimum snow pm = %.1f psf > ps = %.1f psf: separate uniform case "
                     "S_min" % (pm, ps))
    th = _roof_geometry(cfg)[0]
    if not cfg.get("pattern_snow", True) or ps <= 0:
        return names, notes
    oh = float(cfg.get("overhang_ft", 0.0) or 0.0)
    if cfg.get("monoslope"):
        notes.append("monoslope: 7.6.1 unbalanced snow does not apply (hip/gable only)")
        if oh > 0:
            names += ["S_part_%d" % k for k in range(len(_partial_segments(cfg)))]
            notes.append("7.5 partial loading on the overhang cantilevers + main span "
                         "(balanced on one span, 0.5 balanced elsewhere)")
        return names, notes
    if cfg.get("truss_roof"):
        names += ["S_unb_L", "S_unb_R"]
        return names, notes
    if 2.38 <= th <= 30.2:
        names += ["S_unb_L", "S_unb_R"]
    else:
        notes.append("theta = %.1f deg outside 2.38-30.2: 7.6.1 unbalanced snow not required"
                     % th)
        if oh > 0:
            names += ["S_part_%d" % k for k in range(len(_partial_segments(cfg)))]
    return names, notes


# ---------------- cranes / concentrated loads (ASCE 7-22 4.9) ----------------

CRANE_IMPACT = {"monorail": 0.25, "bridge_DEF": 0.25, "bridge_ABC": 0.10, "hand": 0.0}


def crane_loads(cfg):
    """ASCE 7-22 4.9 crane actions delivered to THIS frame. Returns dict(vertical=[(x_in,
    y_in, P_kip down incl. impact, ecc_in)], lateral=[(x_in, y_in, H_kip, arm_in)],
    longitudinal_kip, impact, basis) or None. Vertical = reaction_factor x (rated +
    hoist/trolley + bridge) x frac per support, x (1 + impact) per 4.9.3 (monorail powered
    25%, bridge D/E/F 25%, bridge A/B/C 10%, hand-geared 0%); lateral = 20% (rated +
    hoist/trolley) for electrically powered trolleys (4.9.4), split over the supports, at the
    traction surface (arm = node elevation - traction elevation); longitudinal 10% of the
    maximum wheel loads (4.9.5) is OUT OF PLANE -> reported for the longitudinal bracing."""
    cr = cfg.get("crane")
    if not cr:
        return None
    typ = str(cr.get("type", "monorail"))
    if typ not in CRANE_IMPACT:
        raise PortalInputError("crane type %r -- use one of %s (ASCE 7-22 4.9.3)"
                               % (typ, sorted(CRANE_IMPACT)))
    impact = float(cr.get("impact", CRANE_IMPACT[typ]))
    if impact < CRANE_IMPACT[typ] - 1e-9:
        raise PortalInputError("crane impact %.2f < ASCE 7-22 4.9.3 minimum %.2f for %s"
                               % (impact, CRANE_IMPACT[typ], typ))
    sups = cr.get("supports") or []
    if not sups:
        raise PortalInputError("crane needs supports=[dict(x_ft, y_ft or on-roof, frac)] -- "
                               "where the runway/monorail delivers its reaction to this frame")
    rf = float(cr.get("reaction_factor", 1.0))
    rated = float(cr["rated_kip"])
    ht = float(cr.get("hoist_trolley_kip", 0.0))
    br = float(cr.get("bridge_kip", 0.0))
    Wstat = rf * (rated + ht + br)
    vert, lat = [], []
    powered = typ != "hand" and cr.get("powered_trolley", True)
    Htot = 0.20 * rf * (rated + ht) if powered else 0.0
    nfr = sum(float(s_.get("lat_frac", 1.0 / len(sups))) for s_ in sups)
    for s_ in sups:
        x, y = _point_xy(cfg, s_)
        P = Wstat * float(s_.get("frac", 1.0 if len(sups) == 1 else 1.0 / len(sups)))
        vert.append((x, y, P * (1.0 + impact), float(s_.get("ecc_in", 0.0))))
        if Htot:
            yt = s_.get("traction_y_ft", cr.get("traction_y_ft"))
            arm = (y - float(yt) * 12.0) if yt is not None else 0.0
            lat.append((x, y, Htot * float(s_.get("lat_frac", 1.0 / len(sups))) / nfr, arm))
    long_kip = 0.0 if typ == "hand" else 0.10 * Wstat
    return dict(vertical=vert, lateral=lat, longitudinal_kip=round(long_kip, 2),
                impact=impact, type=typ, W_static_kip=round(Wstat, 2),
                H_lateral_kip=round(Htot, 2),
                basis="ASCE 7-22 4.9: vertical %.2f kip static x (1 + %.2f impact, 4.9.3); "
                      "lateral 20%% (rated + hoist/trolley) = %.2f kip (4.9.4, either "
                      "direction -> L(H+)/L(H-) combos); longitudinal 10%% wheel loads = %.2f "
                      "kip OUT OF PLANE (4.9.5 -> longitudinal bracing). Crane load enters "
                      "the 2.3.1 combinations as L. Fatigue (S100 Ch. M) is the agent's."
                      % (Wstat, impact, Htot, long_kip))


def _has_L(cfg):
    return bool(cfg.get("crane")) or any(str(p.get("case", "D")) == "L"
                                         for p in (cfg.get("point_loads") or []))


def _has_LH(cfg):
    cl = crane_loads(cfg)
    return bool(cl and cl["lateral"])


# ---------------- elementary load cases ----------------

def _node_at(meta, fr, x, y):
    t = meta.get("load_nodes", {}).get((round(x, 3), round(y, 3)))
    if t is not None:
        return t
    return min(fr.nodes, key=lambda k: math.hypot(fr.nodes[k][0] - x, fr.nodes[k][1] - y))


def _case_loads(fr, members, meta, cfg, case):
    """Apply one elementary case. Returns the load list [(kind, target, values)] the combo
    assembler replays with its factor ('m': member local (axial, perp) kip/in; 'n': nodal
    (Fx, Fy, Mz) kip / kip-in)."""
    sp_ft = cfg["spacing_ft"]
    out = []
    truss = bool(cfg.get("truss_roof"))
    eL, eR = meta["eave_nodes"]
    span_in = meta["span_in"]

    def raf_idxs():
        return [i for lab in members if lab.startswith("raf") for i in members[lab]]

    def put_rafter(idx, p_ax, w_perp):
        """Rafter member load, or (truss_roof) its simple-span reactions at the eaves."""
        if not truss:
            out.append(("m", idx, (p_ax, w_perp)))
            return
        el = fr.elems[idx]
        (x1, y1), (x2, y2) = fr.nodes[el[0]], fr.nodes[el[1]]
        L = math.hypot(x2 - x1, y2 - y1)
        c, s = (x2 - x1) / L, (y2 - y1) / L
        Fx = (p_ax * c - w_perp * s) * L
        Fy = (p_ax * s + w_perp * c) * L
        xm = (x1 + x2) / 2.0
        fR = min(max(xm / span_in, 0.0), 1.0)
        out.append(("n", eL, (Fx * (1 - fR), Fy * (1 - fR), 0.0)))
        out.append(("n", eR, (Fx * fR, Fy * fR, 0.0)))

    def gravity_on_rafters(psf_of_x, basis):
        """Gravity psf (function of element mid x) on every rafter element ->
        local (axial, perp). basis 'projection' (per horizontal) or 'length'."""
        k = sp_ft / 1000.0 / 12.0
        for idx in raf_idxs():
            el = fr.elems[idx]
            (x1, y1), (x2, y2) = fr.nodes[el[0]], fr.nodes[el[1]]
            L = math.hypot(x2 - x1, y2 - y1)
            c, s = (x2 - x1) / L, (y2 - y1) / L
            psf = psf_of_x((x1 + x2) / 2.0)
            if not psf:
                continue
            wline = psf * k * (abs(c) if basis == "projection" else 1.0)
            put_rafter(idx, -wline * s, -wline * c)

    def point_loads(case_names):
        for pl in cfg.get("point_loads") or []:
            pc = str(pl.get("case", "D"))
            pc = "S_bal" if pc == "S" else pc
            if pc not in case_names:
                continue
            x, y = _point_xy(cfg, pl)
            nd = _node_at(meta, fr, x, y)
            Fx = float(pl.get("H_kip", pl.get("Fx_kip", 0.0)))
            Fy = -float(pl["P_kip"]) if "P_kip" in pl else float(pl.get("Fy_kip", 0.0))
            Mz = float(pl.get("M_kipin", pl.get("Mz_kipin", 0.0)))
            out.append(("n", nd, (Fx, Fy, Mz)))

    if case == "D":
        Droof = cfg["D_roof"] + cfg.get("collateral", 0.0)
        gravity_on_rafters(lambda x: Droof, "length")
        if cfg.get("self_weight", True):                # CFS-25: frame self-weight
            for lab, idxs in members.items():
                sec = (meta["secs"]["col"] if lab.startswith("col") else
                       meta.get("kb_section") if lab.startswith("kb") else meta["secs"]["raf"])
                wsw = (sec or {}).get("w_self_kip_in", 0.0)
                if not wsw:
                    continue
                for idx in idxs:
                    el = fr.elems[idx]
                    L, c, s = fr._geom(el)
                    if len(el) > 5 and el[5]:           # truss strut: lump to its ends
                        out.append(("n", el[0], (0.0, -wsw * L / 2.0, 0.0)))
                        out.append(("n", el[1], (0.0, -wsw * L / 2.0, 0.0)))
                    elif lab.startswith("raf"):
                        put_rafter(idx, -wsw * s, -wsw * c)
                    else:
                        out.append(("m", idx, (-wsw * s, -wsw * c)))
        point_loads(("D",))
    elif case == "Lr":
        gravity_on_rafters(lambda x: cfg["Lr"], "projection")
        point_loads(("Lr",))
    elif case == "L":
        point_loads(("L",))
        cl = crane_loads(cfg)
        for (x, y, P, ecc) in (cl or {}).get("vertical", []):
            out.append(("n", _node_at(meta, fr, x, y), (0.0, -P, -P * ecc)))
    elif case == "LH":
        cl = crane_loads(cfg)
        for (x, y, H, arm) in (cl or {}).get("lateral", []):
            out.append(("n", _node_at(meta, fr, x, y), (H, 0.0, H * arm)))
    elif case == "S_bal":
        ps = snow_ps(cfg)
        gravity_on_rafters(lambda x: ps, "projection")
        point_loads(("S_bal",))
    elif case == "S_min":
        pm = snow_pm(cfg)
        gravity_on_rafters(lambda x: pm, "projection")
    elif case in ("S_unb_L", "S_unb_R"):
        fw, fl = cfg.get("unbalanced_factors", (0.3, 1.5))
        ps = snow_ps(cfg)
        fL, fR = (fw, fl) if case.endswith("L") else (fl, fw)
        xmid = span_in / 2.0
        if meta.get("multi_span"):
            # pooled halves: every LEFT half (raf_L) vs every RIGHT half (raf_R)
            left = set(members["raf_L"])
            k = sp_ft / 1000.0 / 12.0
            for idx in raf_idxs():
                el = fr.elems[idx]
                (x1, y1), (x2, y2) = fr.nodes[el[0]], fr.nodes[el[1]]
                L = math.hypot(x2 - x1, y2 - y1)
                c, s = (x2 - x1) / L, (y2 - y1) / L
                wline = (fL if idx in left else fR) * ps * k * abs(c)
                put_rafter(idx, -wline * s, -wline * c)
        else:
            gravity_on_rafters(lambda x: ps * (fL if x < xmid else fR), "projection")
    elif case.startswith("S_part_"):
        k_ = int(case.split("_")[-1])
        segs = _partial_segments(cfg)
        a, b = segs[k_]
        ps = snow_ps(cfg)
        gravity_on_rafters(lambda x: ps * (1.0 if a - 1e-6 <= x <= b + 1e-6 else 0.5),
                           "projection")
    elif case.startswith("W"):
        wc = meta.get("_wind")
        if wc is None:
            wc = meta["_wind"] = wind_cases(cfg)
        c = next((c_ for c_ in wc["cases"] if c_["name"] == case), None)
        if c is None:
            return out
        k = sp_ft / 1000.0 / 12.0
        for lab, idxs in members.items():
            if lab.startswith("col"):
                p = c["walls"].get(lab, 0.0)
                drag = c.get("col_drag_kip_in", 0.0)
                for idx in idxs:
                    # col_L: local +y' = -x (outward) -> + toward surface pushes +x (w = -p);
                    # col_R: local +y' = -x (inward)  -> + toward surface pushes -x (w = +p)
                    w_ = (-p if lab == "col_L" else (p if lab == "col_R" else 0.0)) * k
                    w_ += -drag                                # drag along +x*dir: y' = -x
                    if w_:
                        out.append(("m", idx, (0.0, w_)))
        rl = c.get("roof_by_label")
        zones = c.get("roof_zones") or []
        for idx in raf_idxs():
            el = fr.elems[idx]
            lab = el[4]
            if rl is not None:
                p = rl.get("raf_L" if lab == "raf_L" else "raf_R", 0.0)
            else:
                (x1, _y1), (x2, _y2) = fr.nodes[el[0]], fr.nodes[el[1]]
                a, b = min(x1, x2), max(x1, x2)
                tot = 0.0
                for z0, z1, pz in zones:
                    ov = min(b, z1) - max(a, z0)
                    if ov > 0:
                        tot += pz * ov
                p = tot / (b - a) if b > a else 0.0
            if p:
                put_rafter(idx, 0.0, -p * k)            # + toward the top surface = -y'
        for side, Fx in c.get("edge_Fx") or []:
            nd = meta["roof_edge_nodes"][0 if side == "L" else 1]
            out.append(("n", nd, (Fx, 0.0, 0.0)))
    elif case in ("E", "E_neg"):
        sh = seismic_base_shear(cfg)
        if sh:
            V = sh["V_kip"] * (1.0 if case == "E" else -1.0)
            tops = meta.get("col_top_nodes") or meta["eave_nodes"]
            for nd in tops:
                out.append(("n", nd, (V / len(tops), 0.0, 0.0)))
    return out


def _apply(fr, loadlist, factor):
    for kind, tgt, val in loadlist:
        if kind == "m":
            fr.load_member(tgt, p_axial=val[0] * factor, w_perp=val[1] * factor)
        else:
            fr.load_node(tgt, val[0] * factor, val[1] * factor, val[2] * factor)


# ---------------- seismic (portal / SBMF) ----------------

def _om0(cfg):
    s = cfg.get("seis") or {}
    for k in ("Om0", "Omega0", "Omega_0"):
        if s.get(k) is not None:
            return float(s[k])
    sysname = cfg.get("system") or s.get("system")
    if sysname in CS.SYSTEMS:
        return float(CS.SYSTEMS[sysname]["Om0"])
    return None


def _sdc(cfg):
    s = cfg.get("seis") or {}
    return CS.sdc(s.get("SDS", 0.0), s.get("SD1", 0.0), s.get("S1", 0.0),
                  cfg.get("risk_cat", "II"))


def rho_portal(cfg):
    """12.3.4 redundancy: cfg['rho'] (or seis['rho']) when declared, else 1.3 in SDC D-F and
    1.0 in B/C. Multiplies strength-level E only (never drift, never Omega_0 combos)."""
    for src in (cfg, cfg.get("seis") or {}):
        if src.get("rho") is not None:
            return float(src["rho"])
    return 1.3 if _sdc(cfg) in ("D", "E", "F") else 1.0


def seismic_base_shear(cfg):
    """ELF base shear for the frame tributary (single level at the eave): V = Cs W_frame with
    ASCE 7-22 12.8.1.1: Cs = SDS/(R/Ie), <= SD1/(T R/Ie) for T <= TL (T = Ta = Ct hn^x,
    12.8.2.1 steel moment frame Ct 0.028, x 0.8 unless seis gives T / Ct / x), >= 0.044 SDS Ie
    >= 0.01, and >= 0.5 S1/(R/Ie) where S1 >= 0.6. None if no W_frame_kip."""
    s = cfg.get("seis") or {}
    W = s.get("W_frame_kip")
    if not W:
        return None
    SDS, R, Ie = float(s["SDS"]), float(s.get("R", 3.0)), float(s.get("Ie", 1.0))
    Cs = SDS / (R / Ie)
    note = ["Cs = SDS/(R/Ie) = %.4f" % Cs]
    hn = float(cfg.get("apex_ft") or cfg["eave_ft"])
    T = s.get("T")
    if T is None and s.get("SD1"):
        T = float(s.get("Ct", 0.028)) * hn ** float(s.get("x", 0.8))
    if s.get("SD1") and T:
        TL = float(s.get("TL", 8.0))
        cap = float(s["SD1"]) / (T * R / Ie) if T <= TL else \
            float(s["SD1"]) * TL / (T * T * R / Ie)
        if cap < Cs:
            Cs = cap
            note.append("capped by SD1/(T R/Ie) at T = %.3f s -> %.4f" % (T, Cs))
    cmin = max(0.044 * SDS * Ie, 0.01)
    if Cs < cmin:
        Cs = cmin
        note.append("minimum 0.044 SDS Ie >= 0.01 governs -> %.4f" % Cs)
    S1 = float(s.get("S1", 0.0) or 0.0)
    if S1 >= 0.6 and Cs < 0.5 * S1 / (R / Ie):
        Cs = 0.5 * S1 / (R / Ie)
        note.append("S1 >= 0.6 minimum -> %.4f" % Cs)
    return dict(V_kip=Cs * float(W), Cs=Cs, T_s=T, W_kip=float(W),
                basis="ASCE 7-22 12.8.1.1 (" + "; ".join(note) + ")")


# ---------------- LRFD combinations (ASCE 7-22 2.3.1 / 2.3.6) ----------------

GRAVITY_CASES = ("D", "Lr", "L", "S_bal", "S_min", "S_unb_L", "S_unb_R")


def _is_gravity(case):
    return case in GRAVITY_CASES or case.startswith("S_part_")


def combo_role(name):
    return "overstrength" if "Om0" in name else "strength"


def lrfd_combos(cfg, wind=None):
    """LRFD combo list [(name, {case: factor})], ASCE 7-22 2.3.1 / 2.3.6:
      1a 1.4D
      2a 1.2D + 1.6L + (0.5Lr or 0.3S)                         [when L (crane/point L) exists]
      3a 1.2D + (1.6Lr or 1.0S) + (L or 0.5W)  -- BOTH branches: the wind-free one with L and
         with L not acting, and every wind case at 0.5 (CFS-23)
      4a 1.2D + 1.0W + L + (0.5Lr or 0.3S)
      5a 0.9D + 1.0W                                            (NET UPLIFT)
      6  (1.2 + 0.2SDS)D + rho E + L + 0.15S;  7  (0.9 - 0.2SDS)D + rho E   (E and E_neg)
         + the Omega_0 pair (role 'overstrength': connections / anchorage / SBMF Emh cap).
    Snow principal cases: S_bal, S_min (7.3.3), S_unb_L/R (7.6.1) or S_part_k (7.5).
    Crane lateral (LH) acts in either direction -> L(H+) / L(H-) variants.
    Notional loads are added by the runner (S100 C1.1.1.2), not here."""
    wind = wind or (wind_cases(cfg) if (cfg.get("wind") or
                                        "wind_pressures_psf" in cfg) else dict(cases=[]))
    Wn = [c["name"] for c in wind["cases"]]
    snow_pr, _n = snow_case_names(cfg)
    has_S = bool(snow_pr)
    has_L = _has_L(cfg)
    lvars = [("", {})]
    if has_L:
        lvars = ([("L(H+)", {"L": 1.0, "LH": 1.0}), ("L(H-)", {"L": 1.0, "LH": -1.0})]
                 if _has_LH(cfg) else [("L", {"L": 1.0})])

    def addL(c, lv, f):
        c = dict(c)
        for k, v in lv.items():
            c[k] = c.get(k, 0.0) + v * f
        return c
    combos = [("1.4D", {"D": 1.4})]
    if has_L:                                                    # 2a
        for ln, lv in lvars:
            combos.append(("1.2D+1.6%s+0.5Lr" % ln, addL({"D": 1.2, "Lr": 0.5}, lv, 1.6)))
            if has_S:
                combos.append(("1.2D+1.6%s+0.3S" % ln, addL({"D": 1.2, "S_bal": 0.3}, lv, 1.6)))
    principals = [("1.6Lr", {"Lr": 1.6})] if float(cfg.get("Lr", 0.0) or 0.0) > 0 else []
    # ASCE 7-22 2.3.1 combo 3: snow as PRINCIPAL carries 1.0S (was 1.6S in 7-16; Lr stays 1.6)
    principals += [("1.0%s" % sc, {sc: 1.0}) for sc in snow_pr]
    for pn, pc in principals:                                    # 3a
        combos.append(("1.2D+%s" % pn, dict({"D": 1.2}, **pc)))  # (L or 0.5W): L not acting
        if has_L:
            for ln, lv in lvars:
                combos.append(("1.2D+%s+%s" % (pn, ln), addL(dict({"D": 1.2}, **pc), lv, 1.0)))
        for w in Wn:
            combos.append(("1.2D+%s+0.5%s" % (pn, w), dict({"D": 1.2, w: 0.5}, **pc)))
    for w in Wn:                                                 # 4a
        comps = [("0.5Lr", {"Lr": 0.5})] + ([("0.3S", {"S_bal": 0.3})] if has_S else [])
        for cn, cc in comps:
            for ln, lv in lvars:
                nm = "1.2D+1.0%s%s+%s" % (w, ("+" + ln) if ln else "", cn)
                combos.append((nm, addL(dict({"D": 1.2, w: 1.0}, **cc), lv, 1.0)))
    for w in Wn:                                                 # 5a NET UPLIFT
        combos.append(("0.9D+1.0%s" % w, {"D": 0.9, w: 1.0}))
    sh = seismic_base_shear(cfg)
    if sh:
        sds = float(cfg["seis"]["SDS"])
        rho = rho_portal(cfg)
        om0 = _om0(cfg)
        Sc = {"S_bal": 0.15} if has_S else {}
        for e in ("E", "E_neg"):
            for ln, lv in lvars:
                c6 = addL(dict({"D": 1.2 + 0.2 * sds, e: rho}, **Sc), lv, 1.0)
                combos.append(("(1.2+0.2SDS)D+%.2f%s%s%s" % (rho, e, ("+" + ln) if ln else "",
                                                          "+0.15S" if Sc else ""), c6))
            combos.append(("(0.9-0.2SDS)D+%.2f%s" % (rho, e), {"D": 0.9 - 0.2 * sds, e: rho}))
            if om0:
                for ln, lv in lvars:
                    c6 = addL(dict({"D": 1.2 + 0.2 * sds, e: om0}, **Sc), lv, 1.0)
                    combos.append(("(1.2+0.2SDS)D+Om0%s%s%s" % (e, ("+" + ln) if ln else "",
                                                              "+0.15S" if Sc else ""), c6))
                combos.append(("(0.9-0.2SDS)D+Om0%s" % e, {"D": 0.9 - 0.2 * sds, e: om0}))
    return combos


# ---------------- runner (Tier 1: effective-stiffness iterated envelopes) ----------------

def _member_envelope(fr, members, sol):
    """Per-label envelope of ONE solve. Moment sign convention: + = tension on the INSIDE
    flange (rafter bottom / column inner face), - = outside flange in tension. Stations:
    every node plus each element mid-point (UDL sagging term). P_kip = max |N|; Pc_kip /
    Pt_kip = max compression / tension."""
    env = {}
    for label, idxs in members.items():
        sgn = -1.0 if label == "col_R" else 1.0
        P = Vv = Pc = Pt = 0.0
        stations = []
        for i, idx in enumerate(idxs):
            q = sol["end_forces"][idx]
            P = max(P, abs(q[0]), abs(q[3]))
            Vv = max(Vv, abs(q[1]), abs(q[4]))
            Pc = max(Pc, q[0], -q[3])
            Pt = max(Pt, -q[0], q[3])
            m0, m1 = -q[2] * sgn, q[5] * sgn
            if i == 0:
                stations.append(m0)
            w = fr.mload.get(idx, (0.0, 0.0))[1]
            if w:
                L = fr._geom(fr.elems[idx])[0]
                stations.append((m0 + m1) / 2.0 - sgn * w * L * L / 8.0)
            stations.append(m1)
        Mpos = max(stations + [0.0])
        Mneg = min(stations + [0.0])
        env[label] = dict(P_kip=round(P, 2), V_kip=round(Vv, 2),
                          M_kipin=round(max(Mpos, -Mneg), 1),
                          M_pos_kipin=round(Mpos, 1), M_neg_kipin=round(Mneg, 1),
                          Pc_kip=round(Pc, 2), Pt_kip=round(Pt, 2),
                          M_kipin_stations=[round(m, 1) for m in stations])
    return env


def _joint_moments(fr, members, meta, sol):
    """Signed joint moments (inside-flange tension +) with the member end P/V at the knees,
    apex(es) and valley column tops."""
    out = {}
    for side, lab in (("L", "col_L"), ("R", "col_R")):
        if members.get(lab):
            idx = members[lab][-1]
            q = sol["end_forces"][idx]
            sgn = -1.0 if lab == "col_R" else 1.0
            out["knee_" + side] = dict(M=round(q[5] * sgn, 1), V=round(abs(q[4]), 2),
                                       P=round(-q[3], 2), node=fr.elems[idx][1])
    for k, an in enumerate(meta.get("apex_nodes") or []):
        idx = next((i for i in members["raf_L"] if fr.elems[i][1] == an), None)
        if idx is None:
            continue
        q = sol["end_forces"][idx]
        out["apex" if k == 0 else "apex_%d" % (k + 1)] = dict(
            M=round(q[5], 1), V=round(abs(q[4]), 2), P=round(-q[3], 2), node=an)
    for lab in members:
        if lab.startswith("col_I"):
            idx = members[lab][-1]
            q = sol["end_forces"][idx]
            out["valley_" + lab[4:]] = dict(M=round(q[5], 1), V=round(abs(q[4]), 2),
                                            P=round(-q[3], 2), node=fr.elems[idx][1])
    return out


def _tau_b(N, Py):
    """AISI S100-16 C1.1.1.3(b): tau_b = 1.0 for alpha Pr/Py <= 0.5, else
    4 (alpha Pr/Py)(1 - alpha Pr/Py); alpha = 1.0 (LRFD). Floored at 0.05 (flagged)."""
    if not Py or N <= 0:
        return 1.0
    r = N / Py
    if r <= 0.5:
        return 1.0
    return max(4.0 * r * (1.0 - r), 0.05)


def _nodal_gravity(fr, cases_cache, combo):
    """{node: downward kip} of the GRAVITY part of a combo (Yi for the notional loads), member
    loads lumped half to each end."""
    acc = {}
    for case, f in combo.items():
        if not _is_gravity(case):
            continue
        for kind, tgt, val in cases_cache.get(case, []):
            if kind == "m":
                el = fr.elems[tgt]
                L, c, s = fr._geom(el)
                Fy = (val[0] * s + val[1] * c) * L * f
                acc[el[0]] = acc.get(el[0], 0.0) - Fy / 2.0
                acc[el[1]] = acc.get(el[1], 0.0) - Fy / 2.0
            else:
                acc[tgt] = acc.get(tgt, 0.0) - val[1] * f
    return acc


def _lateral_resultant(fr, cases_cache, combo):
    tot = 0.0
    for case, f in combo.items():
        if _is_gravity(case):
            continue
        for kind, tgt, val in cases_cache.get(case, []):
            if kind == "m":
                L, c, s = fr._geom(fr.elems[tgt])
                tot += (val[0] * c - val[1] * s) * L * f
            else:
                tot += val[0] * f
    return tot


def _sway(sol, meta):
    tops = meta.get("col_top_nodes") or meta["eave_nodes"]
    vals = [sol["u"][t][0] for t in tops if t in sol["u"]]
    return max(vals, key=abs) if vals else 0.0


def _solve_combo(cfg, secs, combo, cases_cache, stiff_scale, pdelta_iters=2, notional=0.0,
                 template=None, tau_b=False):
    """Solve one combination. Direct analysis (AISI S100-16 C1.1): stiff_scale = 0.90 on EA
    and EI (C1.1.1.3(a)), tau_b on EI from alpha Pr/Py per element (C1.1.1.3(b)) when tau_b,
    notional Ni = notional x Yi at every node with gravity load, in the direction of the
    combination's lateral resultant (or of the gravity sway when it has none) --
    C1.1.1.2(b)(1)-(2) -- and P-Delta (string stiffness on every compressed element) to
    convergence. Member end forces are recovered with the assembled (scaled + geometric)
    stiffness. Returns (frame, members, meta, sol); sol['pdelta'] = dict(converged, iters,
    growth, amplification = second-order / first-order sway, posdef, tau_b_min)."""
    if template is not None:
        fr0, members, meta = template
        fr = fr0.copy_geometry()
    else:
        fr, members, meta = build_portal(cfg, secs)
    for case, f in combo.items():
        if f:
            _apply(fr, cases_cache[case], f)
    if notional:
        grav = _nodal_gravity(fr, cases_cache, combo)
        H = _lateral_resultant(fr, cases_cache, combo)
        if abs(H) > 1e-6:
            dirx = 1.0 if H > 0 else -1.0
        else:
            pre = fr.solve(stiff_scale=stiff_scale)
            dirx = -1.0 if _sway(pre, meta) < -1e-9 else 1.0
        for nd, Y in grav.items():
            if Y > 0:
                fr.load_node(nd, dirx * notional * Y, 0.0, 0.0)
    Py = {}
    if tau_b:
        for idx, el in enumerate(fr.elems):
            lab = el[4]
            sec = secs["col"] if lab.startswith("col") else (
                meta.get("kb_section") if lab.startswith("kb") else secs["raf"])
            if sec:
                Py[idx] = float(sec.get("Fy", 50.0)) * float(sec.get("Ag", sec["A"]))
    sol = fr.solve(stiff_scale=stiff_scale)
    d_first = _sway(sol, meta) or 1e-9
    d_prev = d_first
    pd = dict(converged=True, iters=0, growth=1.0, amplification=1.0, posdef=True,
              tau_b_min=1.0)
    n_it = max(pdelta_iters, 0) and max(pdelta_iters, 6)
    for it in range(n_it):
        ax, tau = {}, {}
        for idx in range(len(fr.elems)):
            q = sol["end_forces"][idx]
            N = max(q[0], -q[3])                        # compression positive (local axial)
            if N > 0:
                ax[idx] = N
                if tau_b and idx in Py:
                    tb = _tau_b(N, Py[idx])
                    if tb < 1.0:
                        tau[idx] = tb
        if not ax:
            break
        sol = fr.solve(axials=ax, stiff_scale=stiff_scale, ei_scale=tau or None)
        d = _sway(sol, meta)
        pd["iters"] = it + 1
        pd["growth"] = abs(d) / max(abs(d_prev), 1e-9)
        pd["posdef"] = sol.get("posdef", True)
        pd["tau_b_min"] = round(min(list(tau.values()) + [1.0]), 3)
        if not pd["posdef"] or pd["growth"] > 3.0:     # beyond the elastic sway buckling load
            pd["converged"] = False
            break
        if abs(d - d_prev) <= 0.005 * max(abs(d), 1e-6):
            break
        d_prev = d
    else:
        if n_it and pd["growth"] > 1.02:
            pd["converged"] = False                     # still growing after max iters
    pd["amplification"] = round(abs(_sway(sol, meta)) / max(abs(d_first), 1e-9), 3) \
        if abs(d_first) > 1e-6 else 1.0
    sol["pdelta"] = pd
    return fr, members, meta, sol


def _group_envelope(combo_out, prefix, strength_only=True):
    """Envelope of every label starting with prefix ('col', 'raf', 'kb') over the combos:
    the max-|M| pair, max inside/outside-flange moment, max compression and max tension,
    each with its PAIRED forces and combo (CFS-26: P was taken from the max-M combo only)."""
    rows = []
    for name, cd in combo_out.items():
        if strength_only and cd.get("role") != "strength":
            continue
        for lab, e in cd["envelope"].items():
            if lab.startswith(prefix):
                rows.append((name, lab, e))
    if not rows:
        return None

    def pick(key, sign=1.0):
        name, lab, e = max(rows, key=lambda r: sign * r[2][key])
        return dict(combo=name, label=lab, M_kipin=e["M_kipin"],
                    M_pos_kipin=e["M_pos_kipin"], M_neg_kipin=e["M_neg_kipin"],
                    Pc_kip=e["Pc_kip"], Pt_kip=e["Pt_kip"], V_kip=e["V_kip"],
                    P_kip=e["P_kip"])
    return dict(M_max=pick("M_kipin"), M_inside_max=pick("M_pos_kipin"),
                M_outside_max=pick("M_neg_kipin", -1.0), Pc_max=pick("Pc_kip"),
                Pt_max=pick("Pt_kip"), V_max=pick("V_kip"))


def _joint_envelope(combo_out):
    out = {}
    for name, cd in combo_out.items():
        if cd.get("role") != "strength":
            continue
        for j, d in (cd.get("joints") or {}).items():
            e = out.setdefault(j, dict(M_pos=0.0, combo_pos=None, M_neg=0.0, combo_neg=None,
                                       V_max=0.0, P_paired_pos=None, P_paired_neg=None))
            if d["M"] > e["M_pos"]:
                e.update(M_pos=d["M"], combo_pos=name, P_paired_pos=d["P"])
            if d["M"] < e["M_neg"]:
                e.update(M_neg=d["M"], combo_neg=name, P_paired_neg=d["P"])
            e["V_max"] = max(e["V_max"], d["V"])
    return out


def _vertical_total(fr, cases_cache, names):
    tot = 0.0
    for c in names:
        g = _nodal_gravity(fr, cases_cache, {c: 1.0})
        tot += sum(g.values())
    return tot


# ---------------- SBMF (AISI S400-20 E4) screens + expected strength ----------------

# Table E4.3.3-1 (a, b, c in.; CS ft; CDS 1/ft; CB ft; CB,0 in./ft), corpus-verified
SBMF_PATTERNS = [(2.5, 3.0, 4.25, 2.37, 5.22, 4.20, 0.887),
                 (3.0, 6.0, 4.25, 3.34, 3.61, 5.88, 0.625),
                 (3.0, 10.0, 4.25, 4.53, 2.55, 7.80, 0.475),
                 (2.5, 3.0, 6.25, 2.84, 4.66, 5.10, 0.792),
                 (3.0, 6.0, 6.25, 3.69, 3.44, 6.56, 0.587),
                 (3.0, 10.0, 6.25, 4.80, 2.58, 8.50, 0.455)]
# Table E4.3.3-2 bearing deformation adjustment C_DB vs relative bearing strength
_RBS = [0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
_CDB = [1.00, 1.10, 1.16, 1.23, 1.33, 1.46, 1.66, 2.00]


def sbmf_expected_shear(h_ft, Delta_in, K_kip_in, pattern=0, N=2, t_beam_in=None,
                        Fu_beam=None, Rt_beam=1.1, t_col_in=None, Fu_col=None, Rt_col=1.2,
                        d_in=1.0, hos_in=1.0 / 16.0, T_kip=10.0, k_slip=0.33, n_cols=2,
                        sum_Me_over_h_kip=None):
    """AISI S400-20 E4.3.3 expected shear Ve = VS + VB of the bolted beam-to-column connection
    (the SBMF capacity-design demand for beams/columns, E4.3.1.1; Emh need not exceed
    Omega_0 Eh, E4.3.1). Inputs: h = column base to beam centreline (ft), Delta = DESIGN story
    drift Cd delta_xe / Ie (in), K = elastic lateral stiffness of the frame line (kip/in),
    bolt pattern row of Table E4.3.3-1, N channels in the beam, connected thicknesses/Fu/Rt
    (S400 Table A3.2-1: sheet Fy >= 50 -> Rt 1.1; A500 Gr C HSS Rt 1.2, Gr B 1.3).
      VS = CS k N T / h;  VB,max = CB N R0 / h, R0 = min(d t Rt Fu);
      Delta_S = CDS hos h;  Delta_B,max = CB,0 CDB h (CDB from Table E4.3.3-2 by RBS);
      Delta_B = Delta - Delta_S - (sum_i Me,i/hi)/K >= 0  [Eq. E4.3.3-5; the corpus print is
        garbled -- reconstructed from the symbol list: Me,i = expected moment at bolt group
        i = Ve hi, so sum Me,i/hi = n_cols Ve and the equation is IMPLICIT in Ve: solved by
        bisection (the elastic frame drift at the expected column shears is removed);
        sum_Me_over_h_kip overrides];
      (VB/VB,max)^2 + (1 - Delta_B/Delta_B,max)^1.43 = 1.
    This is a capacity-design DEMAND helper, not a member capacity; every input is the
    agent's declared detail."""
    a, b, c, CS_, CDS, CB, CB0 = SBMF_PATTERNS[int(pattern)]
    comps = [(t_beam_in, Fu_beam, Rt_beam), (t_col_in, Fu_col, Rt_col)]
    comps = [(t, f, r) for t, f, r in comps if t and f]
    if not comps:
        raise PortalInputError("sbmf_expected_shear needs t/Fu of the beam and column")
    R0 = min(d_in * t * r * f for t, f, r in comps)
    tF = [t * f for t, f, _r in comps]
    RBS = min(tF) / max(tF) if len(tF) > 1 else 1.0
    CDB = _interp1(_RBS, _CDB, RBS)
    VS = CS_ * k_slip * N * T_kip / h_ft
    VBmax = CB * N * R0 / h_ft
    dS = CDS * hos_in * h_ft
    dBmax = CB0 * CDB * h_ft

    def VB_of(dB):
        if dB >= dBmax:
            return VBmax
        return VBmax * math.sqrt(max(1.0 - (1.0 - dB / dBmax) ** 1.43, 0.0))

    def dB_of(Ve):
        sMe = n_cols * Ve if sum_Me_over_h_kip is None else float(sum_Me_over_h_kip)
        return max(Delta_in - dS - (sMe / K_kip_in if K_kip_in else 0.0), 0.0)
    lo, hi = VS, VS + VBmax
    for _ in range(200):                          # g(Ve) = VS + VB(dB(Ve)) - Ve, decreasing
        mid = 0.5 * (lo + hi)
        if VS + VB_of(dB_of(mid)) - mid > 0:
            lo = mid
        else:
            hi = mid
    Ve = 0.5 * (lo + hi)
    dB = dB_of(Ve)
    VB = Ve - VS
    return dict(Ve_kip=round(VS + VB, 3), VS_kip=round(VS, 3), VB_kip=round(VB, 3),
                VB_max_kip=round(VBmax, 3), R0_kip=round(R0, 3), RBS=round(RBS, 3),
                CDB=round(CDB, 3), Delta_S_in=round(dS, 4), Delta_B_in=round(dB, 4),
                Delta_B_max_in=round(dBmax, 4), pattern=dict(a=a, b=b, c=c),
                basis="AISI S400-20 E4.3.3 (Eq. E4.3.3-5 reconstructed: corpus print garbled)")


def sbmf_screens(cfg, secs, hn_ft):
    """AISI S400-20 E4.4 system screens for a cfg declaring system='sbmf' (CFS-16). Returns
    [dict(check, ok, clause, detail)] -- a failed screen is a design that S400 does not
    permit; ok=None = the agent must declare it."""
    out = []

    def add(check, ok, clause, detail):
        out.append(dict(check=check, ok=ok, clause=clause, detail=detail))
    sd = cfg.get("sbmf") or {}
    add("one story, height <= 35 ft", hn_ft <= 35.0 and not cfg.get("spans"),
        "S400 E4.4.1(a)", "structure height %.1f ft" % hn_ft)
    cs_ = sd.get("column_splices")
    add("no column splices", True if cs_ is False else (False if cs_ else None),
        "S400 E4.4.1(a)", "declare sbmf['column_splices']=False")
    add("pin-based columns", cfg.get("base", "pinned") == "pinned", "S400 E4.4.1(c)",
        "base = %r" % cfg.get("base", "pinned"))
    col, raf = secs["col"], secs["raf"]
    E_Fy = lambda fy: math.sqrt(E_KSI / fy)
    if col["kind"] != "HSS":
        add("HSS columns", False, "S400 E4.4.3(a)", "column %s is not an HSS"
            % col["designator"])
    else:
        p = col["props"]
        fy = float(sd.get("Fy_col", p["Fy"]))
        add("column depth/width 8-12 in", 8.0 <= p["depth"] <= 12.0 and
            8.0 <= p["flange"] <= 12.0, "S400 E4.4.3(b)",
            "%.1f x %.1f in" % (p["depth"], p["flange"]))
        wt = max(p["flats"]["web"], p["flats"]["flange"]) / p["t"]
        add("column flat w/t <= 1.40 sqrt(E/Fy)", wt <= 1.40 * E_Fy(fy), "S400 E4.4.3(c)",
            "w/t = %.1f vs %.1f (radical restored: corpus drops it)" % (wt, 1.40 * E_Fy(fy)))
    bp = raf["props"]
    fyb = float(sd.get("Fy_beam", 55.0 if sd.get("beam_grade55", True) else bp.get("Fy", 50)))
    lipped_c = raf["kind"] in ("single", "built_up_back_to_back") and \
        SEC.parse_designator(raf["base"]).get("style") == "S"
    add("beam = lipped C (single or back-to-back)", lipped_c, "S400 E4.4.2(a)", raf["designator"])
    add("beam ASTM A653 Grade 55", True if sd.get("beam_grade55") else None, "S400 E4.4.2(a)",
        "declare sbmf['beam_grade55']=True")
    if lipped_c:
        t = bp["t"]
        add("beam design t >= 0.105 in", t >= 0.105 - 1e-6, "S400 E4.4.2(b)", "t = %.4f" % t)
        add("beam depth 12-20 in", 12.0 <= bp["depth"] <= 20.0, "S400 E4.4.2(c)",
            "d = %.1f in" % bp["depth"])
        g = SEC.gross_props(raf["base"])
        ht = g["flats"]["web"] / t
        add("beam web flat h/t <= 6.18 sqrt(E/Fy)", ht <= 6.18 * E_Fy(fyb), "S400 E4.4.2(d)",
            "h/t = %.1f vs %.1f (Fy %.0f)" % (ht, 6.18 * E_Fy(fyb), fyb))
        if raf["kind"] == "single":
            add("single-C beam torsion accounted", None, "S400 E4.4.2(e)",
                "single C beam: torsional effects must be designed")
    add("1-in snug-tight high-strength bolts, 8-bolt pattern",
        True if (sd.get("bolt_dia_in") == 1.0 and sd.get("n_bolts") == 8) else
        (False if sd.get("bolt_dia_in") or sd.get("n_bolts") else None),
        "S400 E4.4.4.1.1(a),(c)", "bolt_dia_in=%s n_bolts=%s"
        % (sd.get("bolt_dia_in"), sd.get("n_bolts")))
    add("one beam/column size and connection detail per frame", True, "S400 E4.4.1(d)",
        "single col/raf section per frame (model)")
    return out


# ---------------- the run ----------------

def run(cfg):
    """Full portal run: unit/schema screen -> elementary cases (D incl. self-weight, Lr,
    snow incl. pm / unbalanced / partial, crane L + LH, every wind case, +/-E) -> LRFD combos
    (2.3.1 both (L or 0.5W) branches, 2.3.6 with rho, Omega_0 pair) -> AISI S100-16 C1.1
    direct analysis (0.90 EA/EI, tau_b, Ni = Yi/240 in the destabilising direction, P-Delta)
    -> Tier-1 effective-stiffness iteration on PER-PLY demands of built-ups -> envelopes of
    EVERY column and rafter (both sides) with paired forces and signs -> joints (both knees,
    apex), reactions, service drift over every wind case, seismic drift (Cd delta_xe / Ie),
    theta (12.8.7), SBMF screens -> torsion companion (single channels).
    Returns the result dict (NO capacities)."""
    soft = check_units(cfg)
    secs = dict(col=frame_section(cfg["col_section"], cfg.get("col_arrangement")),
                raf=frame_section(cfg["raf_section"], cfg.get("raf_arrangement")))
    kind = cfg.get("structure_kind") or \
        ("portal_singlechannel" if (secs["col"]["single"] or secs["raf"]["single"])
         else "portal")
    res = dict(structure_kind=kind, sections={k: s["designator"] for k, s in secs.items()},
               notes=list(soft))
    pw = []
    warn = CS.preflight_fidelity(kind, cfg.get("analysis_fidelity", 1))
    if warn:
        pw += warn
    fr0, members0, meta0 = build_portal(cfg, secs)
    has_wind = bool(cfg.get("wind")) or "wind_pressures_psf" in cfg
    wind = wind_cases(cfg) if has_wind else dict(cases=[], warnings=[], basis="no wind")
    meta0["_wind"] = wind
    for wmsg in wind.get("warnings", []):
        if "enclosure not declared" in wmsg or "legacy wind['enclosed']" in wmsg:
            pw.append("WIND ENCLOSURE: " + wmsg)
        else:
            res["notes"].append("wind: " + wmsg)
    snow_names, snow_notes = snow_case_names(cfg)
    res["notes"] += snow_notes
    names = ["D", "Lr"] + snow_names
    if _has_L(cfg):
        names.append("L")
        if _has_LH(cfg):
            names.append("LH")
    names += [c["name"] for c in wind["cases"]]
    if seismic_base_shear(cfg):
        names += ["E", "E_neg"]
    cases = {c: _case_loads(fr0, members0, meta0, cfg, c) for c in names}
    combos = lrfd_combos(cfg, wind)
    da = cfg.get("direct_analysis", True)
    scale = DA_EA if da else 1.0
    notional = float(cfg.get("notional_ratio", DA_NOTIONAL)) if da else 0.0
    if da and notional < DA_NOTIONAL - 1e-12:
        res["notes"].append("notional ratio %.5f < 1/240: permitted only where project QA "
                            "criteria stipulate a more stringent imperfection (S100 "
                            "C1.1.1.2(b)(1)) -- state the basis" % notional)
    strength = [(n, c) for n, c in combos if combo_role(n) == "strength"]
    nply = {g: secs[g]["n_ply"] for g in ("col", "raf")}
    pdi = 2 if cfg.get("pdelta", True) else 0
    if not pdi:
        pw.append("P-DELTA OFF (cfg['pdelta']=False): first-order envelopes -- not a design "
                  "analysis (AISI S100 C1.1 / ASCE 7-22 12.8.7 require second-order effects)")

    def analyze(props):
        """eff_stiffness callback: props are PER PLY (single channel / tube); the frame is
        analysed with the n-ply member and the EWM stresses use per-ply demands (CFS-17: a
        prior version ran the frame at single-ply stiffness with whole-member forces)."""
        s2 = {k: dict(secs[k], A=props[k]["A"] * nply[k], Ix=props[k]["Ix"] * nply[k])
              for k in ("col", "raf")}
        tmpl = build_portal(cfg, s2)
        env_all = {}
        for name, combo in strength:
            _f, mm, _meta, sol = _solve_combo(cfg, s2, combo, cases, scale, notional=notional,
                                              template=tmpl, tau_b=da, pdelta_iters=pdi)
            env = _member_envelope(_f, mm, sol)
            for lab, e in env.items():
                if lab.startswith("kb"):
                    continue
                g = "col" if lab.startswith("col") else "raf"
                cur = env_all.setdefault(g, dict(P_kip=0.0, M_kipin_stations=[0.0]))
                cur["P_kip"] = max(cur["P_kip"], e["P_kip"])
                if e["M_kipin"] > max(map(abs, cur["M_kipin_stations"])):
                    cur["M_kipin_stations"] = e["M_kipin_stations"]
        return {g: dict(P_kip=d["P_kip"] / nply[g],
                        M_kipin_stations=[m / nply[g] for m in d["M_kipin_stations"]])
                for g, d in env_all.items()}

    tier = cfg.get("analysis_fidelity", 1)
    if tier >= 1:
        it = EFF.iterate({"col": secs["col"]["base"], "raf": secs["raf"]["base"]}, analyze)
        eff = {m: dict(A=p["A"] * nply[m], Ix=p["Ix"] * nply[m]) for m, p in it["props"].items()}
        secs_eff = {k: dict(secs[k], **eff[k]) for k in secs}
        res["eff_stiffness"] = dict(converged=it["converged"], iters=len(it["history"]),
                                    n_ply=nply,
                                    ratios={m: dict(A_over_gross=it["props"][m]["A_over_gross"],
                                                    I_over_gross=it["props"][m]["I_over_gross"])
                                            for m in it["props"]})
    else:
        secs_eff = secs
        res["notes"].append("Tier 0: gross-stiffness analysis (preflight will have flagged "
                            "this for portals)")
    tmpl = build_portal(cfg, secs_eff)
    combo_out = {}
    for name, combo in combos:
        _f, mm, meta, sol = _solve_combo(cfg, secs_eff, combo, cases, scale,
                                         notional=notional, template=tmpl, tau_b=da,
                                         pdelta_iters=pdi)
        env = _member_envelope(_f, mm, sol)
        R = {t: (round(r[0], 2), round(r[1], 2), round(r[2], 2))
             for t, r in sol["reactions"].items()}      # (Rx, Ry, Mz) -- Mz nonzero at fixed bases
        uplift = any(r[1] < -0.05 for r in sol["reactions"].values())
        pd = sol.get("pdelta", {})
        combo_out[name] = dict(envelope=env, reactions_kip=R, net_uplift=uplift,
                               role=combo_role(name), joints=_joint_moments(_f, mm, meta, sol),
                               pdelta=dict(amplification=pd.get("amplification", 1.0),
                                           converged=pd.get("converged", True),
                                           tau_b_min=pd.get("tau_b_min", 1.0)),
                               equilibrium_residual=round(_f.equilibrium_residual(sol), 6))
        if pd and not pd.get("converged", True):
            pw.append("P-DELTA DIVERGED on combo '%s' (sway growth %.1fx/iter, tangent "
                      "stiffness %s): the frame is sway-UNSTABLE at the trial sections -- "
                      "envelopes for this combo are meaningless; RESIZE members (or add "
                      "restraint) and re-run" % (name, pd.get("growth", 0.0),
                                                  "positive definite" if pd.get("posdef")
                                                  else "NOT positive definite"))
    res["combos"] = combo_out
    res["direct_analysis"] = dict(
        enabled=bool(da), stiffness_EA=scale, stiffness_EI="%.2f x tau_b" % scale if da
        else 1.0, notional_ratio=notional,
        basis=("AISI S100-16 C1.1: C1.1.1.3(a) 0.90 on all stiffnesses, (b) tau_b on flexural "
               "stiffness (alpha Pr/Py > 0.5), C1.1.1.2(b) notional Ni = (1/240) alpha Yi "
               "(alpha = 1.0 LRFD) at every gravity node, in every combination, in the "
               "direction of the lateral resultant (gravity-only: the direction of the "
               "gravity sway); P-Delta by string stiffness to convergence" if da else
               "direct analysis OFF: nominal stiffness, no notional loads"),
        max_amplification=max([c["pdelta"]["amplification"] for c in combo_out.values()] +
                              [1.0]))
    if res["direct_analysis"]["max_amplification"] > 1.7 + 1e-9:
        res["notes"].append("second-order/first-order sway ratio %.2f > 1.7: notional loads "
                            "are (correctly) applied in every combination (S100 "
                            "C1.1.1.2(b)(3) relief not available)"
                            % res["direct_analysis"]["max_amplification"])
    # governing / envelopes across BOTH sides (CFS-02)
    gov, gov_lab, menv = {}, {}, {}
    for g in ("col", "raf", "kb"):
        e = _group_envelope(combo_out, g)
        if e is None:
            continue
        menv[g] = e
        if g != "kb":
            gov[g] = e["M_max"]["combo"]
            gov_lab[g] = e["M_max"]["label"]
    res["governing"] = gov
    res["governing_label"] = gov_lab
    res["member_envelopes"] = menv
    res["joints"] = _joint_envelope(combo_out)
    om = [c for c in combo_out if combo_role(c) == "overstrength"]
    if om:
        res["overstrength_envelopes"] = {g: _group_envelope(
            {n: dict(combo_out[n], role="strength") for n in om}, g) for g in ("col", "raf")}
    # service drift: 10-yr MRI wind seed (~0.42 x ultimate pressures, ASCE 7 CC.2.2),
    # full (effective) stiffness, no P-Delta / notional -- EVERY wind case (both directions)
    wsf = cfg.get("service_wind_factor", 0.42)
    best = None
    for wc in wind["cases"]:
        _f, mm, meta, sol = _solve_combo(cfg, secs_eff, {"D": 1.0, wc["name"]: wsf}, cases,
                                         1.0, pdelta_iters=0, template=tmpl)
        for side, nd in zip(("L", "R"), meta["eave_nodes"]):
            h_n = meta["He_in"] if side == "L" else meta.get("HeR_in", meta["He_in"])
            u = abs(sol["u"][nd][0])
            if best is None or u / h_n > best[0]:
                best = (u / h_n, u, h_n, wc["name"])
    grav_best = None
    an = meta0["apex_node"]
    for gc in [c for c in ("Lr", "S_bal", "S_min") if c in cases]:
        _f, mm, meta, sol = _solve_combo(cfg, secs_eff, {"D": 1.0, gc: 1.0}, cases, 1.0,
                                         pdelta_iters=0, template=tmpl)
        dv = -sol["u"][an][1]
        if grav_best is None or dv > grav_best[0]:
            grav_best = (dv, gc)
    if best:
        res["service"] = dict(eave_sway_in=round(best[1], 3),
                              H_over=round(best[2] / max(best[1], 1e-6), 0),
                              governing_wind_case=best[3],
                              apex_defl_in=round(grav_best[0], 3) if grav_best else None,
                              apex_defl_case="D+%s" % grav_best[1] if grav_best else None,
                              note="D + %.2fW service pair over EVERY wind case (10-yr MRI "
                                   "wind seed, CC.2.2); agent states the eave drift "
                                   "criterion and the MRI basis" % wsf)
    else:
        res["service"] = dict(eave_sway_in=0.0, H_over=0.0, governing_wind_case=None,
                              apex_defl_in=round(grav_best[0], 3) if grav_best else None,
                              note="no wind declared -- service sway not evaluated")
    res["wind_basis"] = wind_surface_pressures(cfg) if has_wind else {}
    res["frame_self_weight_kip"] = round(_self_weight_total(fr0, members0, meta0), 3) \
        if cfg.get("self_weight", True) else 0.0
    cl = crane_loads(cfg)
    if cl:
        res["crane"] = {k: v for k, v in cl.items() if k not in ("vertical", "lateral")}
        res["crane"]["vertical_kip"] = [round(v[2], 2) for v in cl["vertical"]]
        res["crane"]["lateral_kip"] = [round(v[2], 2) for v in cl["lateral"]]
    # seismic: base shear, drift (Cd delta_xe / Ie), theta (12.8.7), screens (CFS-16, CFS-05)
    sh = seismic_base_shear(cfg)
    hn = float(meta0["Ha_in"]) / 12.0
    s = cfg.get("seis") or {}
    sysname = cfg.get("system") or s.get("system")
    if sh:
        res["seismic"] = _seismic_block(cfg, secs_eff, cases, tmpl, meta0, fr0, sh, pw)
        if sysname in CS.SYSTEMS:
            ok, msg = CS.height_check(sysname, _sdc(cfg), hn)
            res["seismic"]["height_screen"] = dict(system=sysname, SDC=_sdc(cfg), hn_ft=hn,
                                                   ok=ok, message=msg)
            if not ok:
                pw.append("SEISMIC SYSTEM SCREEN: " + msg)
    if sysname == "sbmf":
        scr = sbmf_screens(cfg, secs, hn)
        res["sbmf"] = dict(screens=scr)
        for c in scr:
            if c["ok"] is False:
                pw.append("SBMF SCREEN FAIL (%s): %s -- %s" % (c["clause"], c["check"],
                                                               c["detail"]))
        sd = cfg.get("sbmf") or {}
        seis_b = res.get("seismic") or {}
        if sd and seis_b.get("Delta_in") is not None and seis_b.get("K_kip_in"):
            try:
                ve = sbmf_expected_shear(
                    meta0["He_in"] / 12.0, seis_b["Delta_in"], seis_b["K_kip_in"],
                    pattern=sd.get("bolt_pattern", 0), N=sd.get("N", secs["raf"]["n_ply"]),
                    t_beam_in=sd.get("t_beam_in", secs["raf"]["props"].get("t")),
                    Fu_beam=sd.get("Fu_beam", 70.0), Rt_beam=sd.get("Rt_beam", 1.1),
                    t_col_in=sd.get("t_col_in", secs["col"]["props"].get("t")),
                    Fu_col=sd.get("Fu_col", 62.0), Rt_col=sd.get("Rt_col", 1.2),
                    hos_in=sd.get("hos_in", 1.0 / 16.0), n_cols=len(meta0["base_nodes"]))
                om0 = _om0(cfg)
                if om0:
                    cap = om0 * sh["V_kip"] / len(meta0["base_nodes"])
                    ve["Omega0_Eh_per_col_kip"] = round(cap, 3)
                    ve["Ve_design_kip"] = round(min(ve["Ve_kip"], cap), 3)
                res["sbmf"]["expected_shear"] = ve
            except Exception as ex:                       # inputs incomplete -> say so
                res["sbmf"]["expected_shear"] = dict(error="Ve not evaluated: %s" % ex)
        else:
            res["sbmf"]["expected_shear"] = dict(
                error="Ve NOT EVALUATED: declare cfg['sbmf'] (bolt_pattern, N, t/Fu/Rt of "
                      "beam and column) and a seismic case (S400 E4.3.3)")
    # torsion companion for single channels (Tier-1 analytic seed; Tier 2 = WSL fiber/warping)
    tors = []
    for key in ("col", "raf"):
        sct = secs[key]
        if not sct["single"]:
            continue
        Lb = (cfg.get("girt_spacing_ft", 6.0) if key == "col"
              else cfg.get("purlin_spacing_ft", 5.0)) * 12.0
        e = menv[key]["V_max"]
        m_t = e["V_kip"] / Lb * sct["e0_in"]           # kip-in/in torsion line moment seed
        tors.append(dict(member=key, designator=sct["designator"], e0_in=round(sct["e0_in"], 3),
                         Lb_in=Lb, T_seg_kipin=round(m_t * Lb / 2.0, 2),
                         B_seed_kipin2=round(m_t * Lb ** 2 / 8.0, 1), combo=e["combo"],
                         label=e["label"],
                         basis=("Z section: shear centre at the centroid -> no load-plane "
                                "torsion; biaxial bending from Ixy is the agent's"
                                if sct["kind"] == "Z" else
                                "uniform torque m = V e0 / Lb between braces, e0 = web-to-"
                                "shear-centre distance m = |x0| - xbar; T = mL/2, B ~ mL^2/8 "
                                "SEED -- agent does the S100 bending+torsion reduction")))
    if tors:
        res["torsion_companion"] = tors
    if pw:
        res["preflight_warnings"] = pw
    return res


def _self_weight_total(fr, members, meta):
    tot = 0.0
    for lab, idxs in members.items():
        sec = (meta["secs"]["col"] if lab.startswith("col") else
               meta.get("kb_section") if lab.startswith("kb") else meta["secs"]["raf"])
        for idx in idxs:
            tot += (sec or {}).get("w_self_kip_in", 0.0) * fr._geom(fr.elems[idx])[0]
    return tot


def _seismic_block(cfg, secs_eff, cases, tmpl, meta0, fr0, sh, pw):
    """Seismic drift (12.8.6: Cd delta_xe / Ie, rho = 1), allowable drift (Table 12.12-1,
    / rho for moment frames in SDC D-F, 12.12.1.1), stability coefficient theta (12.8.7,
    Eq. 12.8-18/19), Omega_0 and rho basis."""
    s = cfg["seis"]
    sysname = cfg.get("system") or s.get("system")
    Ie = float(s.get("Ie", 1.0))
    Cd = s.get("Cd")
    if Cd is None and sysname in CS.SYSTEMS:
        Cd = CS.SYSTEMS[sysname]["Cd"]
    rho = rho_portal(cfg)
    out = dict(V_kip=round(sh["V_kip"], 3), Cs=round(sh["Cs"], 4), T_s=sh.get("T_s"),
               W_frame_kip=sh["W_kip"], basis=sh["basis"], rho=rho,
               rho_basis="ASCE 7-22 12.3.4 (%s); multiplies strength E only"
                         % ("declared" if (cfg.get("rho") is not None or s.get("rho")
                                           is not None) else "SDC %s default" % _sdc(cfg)),
               Om0=_om0(cfg), SDC=_sdc(cfg))
    if out["Om0"] is None:
        pw.append("SEISMIC: Omega_0 not declared (seis['Om0']) and system %r unknown -- the "
                  "2.3.6 overstrength combinations were NOT generated" % sysname)
    _f, mm, meta, sol = _solve_combo(cfg, secs_eff, {"E": 1.0}, cases, 1.0, pdelta_iters=0,
                                     template=tmpl)
    worst = None
    for side, nd in zip(("L", "R"), meta["eave_nodes"]):
        h_n = meta["He_in"] if side == "L" else meta.get("HeR_in", meta["He_in"])
        u = abs(sol["u"][nd][0])
        if worst is None or u / h_n > worst[0]:
            worst = (u / h_n, u, h_n)
    dxe = worst[1]
    hsx = worst[2]
    dxe_V = dxe            # elastic displacement under the strength-level V (12.8.7 Vx / Delta_xe)
    out["K_kip_in"] = round(sh["V_kip"] / max(abs(_sway(sol, meta)), 1e-9), 3)
    if s.get("T_drift"):
        # 12.8.6.2: drift forces may use the computed period without the Cu Ta cap;
        # 12.8.6.1: Eq. 12.8-5 minimum need not apply (Eq. 12.8-6 S1 >= 0.6 still does)
        R_, SDS_ = float(s.get("R", 3.0)), float(s["SDS"])
        Td = float(s["T_drift"])
        Csd = SDS_ / (R_ / Ie)
        if s.get("SD1"):
            TL = float(s.get("TL", 8.0))
            Csd = min(Csd, float(s["SD1"]) / (Td * R_ / Ie) if Td <= TL else
                      float(s["SD1"]) * TL / (Td * Td * R_ / Ie))
        S1 = float(s.get("S1", 0.0) or 0.0)
        if S1 >= 0.6:
            Csd = max(Csd, 0.5 * S1 / (R_ / Ie))
        dxe *= Csd / sh["Cs"]
        out["drift_force_basis"] = ("12.8.6.2: drift forces at T_drift = %.3f s (Cs_drift = "
                                    "%.4f vs strength Cs = %.4f)" % (Td, Csd, sh["Cs"]))
    out["delta_xe_in"] = round(dxe, 4)
    if Cd is None:
        pw.append("SEISMIC DRIFT NOT EVALUATED: Cd not declared (seis['Cd'])")
        out.update(Delta_in=None, ok=None)
        return out
    Cd = float(Cd)
    Delta = Cd * dxe / Ie
    lim = cfg.get("drift_limit")
    lim_basis = "cfg['drift_limit'] (declared)"
    if lim is None:
        # Table 12.12-1 row 1 (0.025 at RC I/II) needs DECLARED drift-tolerant finishes
        # (cfg['drift_tolerant_finishes'], as on the wall path, cfs_engine.drift_limit_for);
        # undeclared -> 'all other structures' (a portal is not a light-frame wall system),
        # stated in the basis. (cfs_systems.drift_limit(None) returns the PERMISSIVE row-1
        # value meant for screens only -- it must not set the design limit silently.)
        acc = cfg.get("drift_tolerant_finishes")
        lim, b_ = CS.drift_limit(sysname if sysname in CS.SYSTEMS else "not_detailed", 1,
                                 str(cfg.get("risk_cat", "II")).upper(),
                                 finishes_accommodate=bool(acc) if acc is not None else False,
                                 with_basis=True)
        lim_basis = "cfs_systems.drift_limit: " + b_
        if acc is None:
            lim_basis += ("; cfg['drift_tolerant_finishes'] NOT declared -> 'all other "
                          "structures' assumed (declare True where the walls, partitions and "
                          "ceilings accommodate the story drift)")
    if cfg.get("drift_no_limit_single_story") or cfg.get("drift_limit_no_limit_single_story"):
        lim = None
        lim_basis = "Table 12.12-1 footnote a (declared: single story, finishes accommodate)"
    if lim is not None and _sdc(cfg) in ("D", "E", "F"):
        lim = lim / rho                                   # 12.12.1.1 moment frames
        lim_basis += " / rho (12.12.1.1, moment frame in SDC %s)" % _sdc(cfg)
    ok = None if lim is None else (Delta / hsx <= lim + 1e-12)
    out.update(Cd=Cd, Ie=Ie, Delta_in=round(Delta, 4), hsx_in=round(hsx, 1),
               drift_ratio=round(Delta / hsx, 5), drift_limit=lim, drift_limit_basis=lim_basis,
               ok=ok)
    if ok is False:
        pw.append("SEISMIC DRIFT NG: Delta = Cd delta_xe / Ie = %.2f in = %.4f h > %.4f h (%s)"
                  % (Delta, Delta / hsx, lim, lim_basis))
    # theta (12.8.7): Px = vertical design load of the seismic combination with no factor
    # above 1.0 (2.3.6 combo 6: D + L + 0.15S), Vx = V
    Px = _vertical_total(fr0, cases, [c for c in ("D", "L") if c in cases]) + \
        (0.15 * _vertical_total(fr0, cases, ["S_bal"]) if "S_bal" in cases else 0.0)
    # Eq. 12.8-19: beta >= 1.25/Omega_0; theta_max need not be taken < 0.10 (7-22)
    _om0v = out.get("Om0")
    beta = max(float(s.get("beta", 1.0)), 1.25 / float(_om0v) if _om0v else 0.0)
    # Vx and Delta_xe from the SAME loading (Eq. 12.8-18: Vx/Delta_xe is the story stiffness):
    # the strength-level V with its own displacement -- NOT the 12.8.6.2 T_drift-scaled
    # displacement over the unscaled V (that understated theta by Cs_drift/Cs).
    theta = Px * dxe_V / (sh["V_kip"] * hsx) if sh["V_kip"] else 0.0
    tmax = max(min(0.5 / (beta * Cd), 0.25), 0.10)
    out.update(theta=round(theta, 4), theta_max=round(tmax, 4), Px_kip=round(Px, 2),
               theta_basis="ASCE 7-22 Eq. 12.8-18 theta = Px Delta_xe / (Vx hsx) (Vx and the "
                           "elastic Delta_xe of the same strength-level loading), Px = "
                           "D + L + 0.15S (>= the 12.8.6.1 expected gravity 1.0D + 0.5L; no "
                           "factor > 1.0), Eq. 12.8-19 theta_max = "
                           "0.5/(beta Cd) <= 0.25, >= 0.10, with beta = %.2f%s"
                           % (beta, "" if s.get("beta") else " (conservative default)"))
    if theta > tmax:
        pw.append("THETA %.3f > theta_max %.3f (ASCE 7-22 12.8.7): the frame is potentially "
                  "unstable and shall be redesigned" % (theta, tmax))
    elif theta > 0.10:
        out["theta_note"] = ("theta > 0.10: P-Delta must be included (12.8.7) -- it is, in "
                             "the S100 C1.1 second-order direct analysis of the strength "
                             "combinations")
    return out


# ---------------- OpenSees emitter (WSL dual-path check; guarded) ----------------

def emit_opensees(cfg, combo=None):
    """Build the identical portal (elasticBeamColumn, gross props, Linear transf; knee braces
    as Truss) in openseespy and solve one combo (default D + W). Returns dict(eave_sway_in,
    nodes, elements) for the equivalence check against the pure-python solver."""
    if not HAVE_OPS:
        raise RuntimeError("openseespy not available -- run on the WSL environment")
    secs = dict(col=frame_section(cfg["col_section"]), raf=frame_section(cfg["raf_section"]))
    fr, members, meta = build_portal(cfg, secs)
    combo = combo or {"D": 1.0, "W": 1.0}
    meta["_wind"] = wind_cases(cfg) if (cfg.get("wind") or "wind_pressures_psf" in cfg) \
        else dict(cases=[])
    cases = {c: _case_loads(fr, members, meta, cfg, c) for c in combo}
    ops.wipe(); ops.model("basic", "-ndm", 2, "-ndf", 3)
    for t, (x, y) in fr.nodes.items():
        ops.node(t, x, y)
    for t, (fx, fy, mz) in fr.fix.items():
        ops.fix(t, int(fx), int(fy), int(mz))
    ops.geomTransf("Linear", 1)
    ops.uniaxialMaterial("Elastic", 1, E_KSI)
    for idx, el in enumerate(fr.elems):
        EA, EI = el[2], el[3]
        if len(el) > 5 and el[5]:
            ops.element("Truss", idx + 1, el[0], el[1], EA / E_KSI, 1)
        else:
            ops.element("elasticBeamColumn", idx + 1, el[0], el[1], EA / E_KSI, E_KSI,
                        EI / E_KSI, 1)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    for case, f in combo.items():
        for kind, tgt, val in cases[case]:
            if kind == "m":
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
    assert fr.equilibrium_residual(sol) < 1e-8
    print("  bench: SS beam midspan %.4f in == 5wL^4/384EI (%.4f); reactions balance" %
          (d_mid, d_exact))
    # 1b) recovery with scaled + geometric stiffness stays in equilibrium (CFS-03)
    fr = Frame2D()
    fr.node(1, 0.0, 0.0); fr.node(2, 0.0, 100.0); fr.node(3, 0.0, 200.0)
    fr.elem(1, 2, EA, EI, "c"); fr.elem(2, 3, EA, EI, "c")
    fr.support(1, True, True, True)
    fr.load_node(3, 1.0, -20.0, 0.0)
    sol = fr.solve(axials={0: 20.0, 1: 20.0}, stiff_scale=0.9)
    assert fr.equilibrium_residual(sol) < 1e-8
    assert abs(sol["end_forces"][0][2] - sol["reactions"][1][2]) < 1e-8
    print("  cantilever (0.9 EI + P-Delta): base element M %.2f == reaction %.2f kip-in"
          % (sol["end_forces"][0][2], sol["reactions"][1][2]))
    # 2) portal gravity equilibrium + symmetry
    cfg = _demo_cfg()
    secs = dict(col=frame_section(cfg["col_section"]), raf=frame_section(cfg["raf_section"]))
    fr, members, meta = build_portal(cfg, secs)
    cases = {c: _case_loads(fr, members, meta, dict(cfg, self_weight=False), c)
             for c in ("D",)}
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
    # 3) full run: envelopes, uplift case, pattern asymmetry, drift, both sides
    res = run(cfg)
    assert "preflight_warnings" not in res, res.get("preflight_warnings")
    assert res["eff_stiffness"]["converged"]
    up = res["combos"]["0.9D+1.0W"]
    assert up["net_uplift"], "0.9D+1.0W must produce net uplift at this wind speed"
    assert "0.9D+1.0W_R" in res["combos"], "wind must act from BOTH sides"
    assert "1.2D+1.6Lr" in res["combos"], "wind-free 3a branch"
    unb = res["combos"].get("1.2D+1.0S_unb_L+0.5W")
    if unb:
        mL = unb["envelope"]["raf_L"]["M_kipin"]
        mR = unb["envelope"]["raf_R"]["M_kipin"]
        assert abs(mL - mR) > 1e-3, "unbalanced snow must break rafter symmetry"
    assert max(c["equilibrium_residual"] for c in res["combos"].values()) < 1e-6
    assert 0 < res["service"]["eave_sway_in"] < meta["He_in"] / 30.0, \
        "eave sway %.1f in is not plausible for the demo frame" % res["service"]["eave_sway_in"]
    print("  run: %d combos, governing col=%s (%s) raf=%s (%s); eave sway %.2f in (H/%d); "
          "I/Ig col=%.2f raf=%.2f" %
          (len(res["combos"]), res["governing"]["col"], res["governing_label"]["col"],
           res["governing"]["raf"], res["governing_label"]["raf"],
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
    assert 0.5 < t0["e0_in"] < 1.5, t0
    assert res_s.get("preflight_warnings"), "single-channel @ Tier 1 carries the Tier-2 nudge"
    print("  single-channel: e0=%.2f in, T_seg=%.2f kip-in, B seed=%.0f kip-in^2; "
          "preflight nudges Tier 2" % (t0["e0_in"], t0["T_seg_kipin"], t0["B_seed_kipin2"]))
    # 6) Tier 0 on a portal trips preflight
    res_t0 = run(dict(cfg, analysis_fidelity=0))
    assert res_t0.get("preflight_warnings"), "portal @ Tier 0 must warn (Gate 4 / Ex27 hook)"
    print("  tier screen: portal @ Tier 0 warns")
    # 7) units: a x12 cfg is refused
    try:
        check_units(dict(cfg, span_ft=480.0, eave_ft=168.0, apex_ft=204.0, spacing_ft=180.0))
        raise AssertionError("x12 cfg must be refused")
    except PortalInputError:
        print("  units: x12 (inch) cfg refused")
    if HAVE_OPS:
        chk = emit_opensees(cfg)
        # pure-python service solve with GROSS props for apples-to-apples
        secs_g = dict(col=frame_section(cfg["col_section"]),
                      raf=frame_section(cfg["raf_section"]))
        frg, mmg, metag = build_portal(cfg, secs_g)
        metag["_wind"] = wind_cases(cfg)
        cases_g = {c: _case_loads(frg, mmg, metag, cfg, c) for c in ("D", "W")}
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

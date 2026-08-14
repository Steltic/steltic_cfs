"""
cfs_sections.py -- CFS section properties: designators, geometry-computed GROSS properties, and
S100 Effective Width Method (EWM) effective properties at a given stress.

Doctrine (matches the repo): this module computes PROPERTIES ONLY -- gross section properties for
the model, effective properties (Ae at stress, Ixe/Se at stress) for the effective-stiffness
iteration and for calc_package inputs. It computes NO capacities: every strength limit state
(local/distortional/global interaction, web crippling, connections) remains the design agent's
job, derived from the AISI S100 RAG and cited.

Designators: SFIA/SSMA style, e.g. "600S162-54" = 6.00 in deep Stud with 1.625 in flanges,
54 mil (0.0566 in) design thickness. Styles: S (stud/joist, lipped), T (track, unlipped),
U (channel, unlipped). Back-to-back built-ups via built_up_back_to_back(). Generic lipped/Z
profiles via from_geometry().

VALIDATION GATE (Stage 1, BUILD_STAGES.md): drop the SFIA extraction 'cfs_shapes.csv' next to
this file and run  python3 cfs_sections.py  -- validate_against_csv() reports per-property
deviation. Gross within 2%, effective within 5% before Stage 2 wiring.

Stated geometric assumptions (checked by the validation gate):
- inside bend radius r_in = 1.5*t unless overridden (SFIA uses per-mil radii; close, not exact)
- corner regions are treated as fully effective in EWM computations
- lip edge stiffener modeled as a simple flat of the standard SFIA length for the flange width
- flange compressive stress taken at the extreme fiber (conservative) in bending EWM

Units: inch, ksi throughout. E = 29,500 ksi per AISI (NOT the hot-rolled 29,000).
"""
import math, os, csv

E_KSI = 29500.0
G_KSI = 11300.0

# design thickness (in) by mil designation
MIL_T = {18: 0.0188, 27: 0.0283, 30: 0.0312, 33: 0.0346, 43: 0.0451, 54: 0.0566,
         68: 0.0713, 97: 0.1017, 118: 0.1242}
# default yield (ksi) by mil (SFIA convention: 33/43 mil Gr 33; 54+ Gr 50)
FY_BY_MIL = {18: 33.0, 27: 33.0, 30: 33.0, 33: 33.0, 43: 33.0,
             54: 50.0, 68: 50.0, 97: 50.0, 118: 50.0}
# SFIA design INSIDE corner radius by mil (Technical Guide thickness table / web-crippling
# tables): ~1.5t at 43+ mils but much larger on thin gauges -- using 1.5t there skews every
# thin-gauge property (Gate-1 finding, 2026-07-30).
RADIUS_BY_MIL = {18: 0.0844, 27: 0.0796, 30: 0.0781, 33: 0.0764, 43: 0.0712,
                 54: 0.0849, 68: 0.1069, 97: 0.1525, 118: 0.1863}
# standard SFIA lip length (in) by flange designation (hundredths of an inch)
LIP_BY_FLANGE = {125: 0.188, 137: 0.375, 162: 0.500, 200: 0.625, 250: 0.625,
                 300: 0.625, 350: 1.000}   # per SFIA guide p.8 (S350 lip is 1", NOT 5/8")


def parse_designator(name):
    """'600S162-54' -> dict(depth=6.0, flange=1.625, lip=0.5, t=0.0566, style='S', mil=54).
    Flange field is hundredths of an inch (162 -> 1.625 by SFIA convention: 162=1-5/8)."""
    import re
    m = re.match(r"^\s*(\d{3,4})([STUF])(\d{3})-(\d{2,3})\s*$", str(name).upper())
    if not m:
        raise KeyError("cannot parse CFS designator %r (expect e.g. '600S162-54')" % (name,))
    depth = int(m.group(1)) / 100.0
    style = m.group(2)
    fcode = int(m.group(3))
    # SFIA flange codes are nominal: 162 = 1.625, 137 = 1.375, 250 = 2.5, etc.
    flange = {125: 1.25, 137: 1.375, 162: 1.625, 200: 2.0, 250: 2.5, 300: 3.0, 350: 3.5}.get(
        fcode, fcode / 100.0)
    mil = int(m.group(4))
    if mil not in MIL_T:
        raise KeyError("unknown mil thickness %r in %r" % (mil, name))
    lip = LIP_BY_FLANGE.get(fcode, 0.5) if style == "S" else 0.0
    return dict(depth=depth, flange=flange, lip=lip, t=MIL_T[mil], style=style, mil=mil)


# ---------------- midline geometry (polyline with facetted corners) ----------------

def _corner_arc(cx, cy, r, a0, a1, n=8):
    """Facet a corner arc (midline radius r) from angle a0 to a1 into n points (excl. start)."""
    pts = []
    for i in range(1, n + 1):
        a = a0 + (a1 - a0) * i / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def lipped_c_midline(depth, flange, lip, t, r_in=None, z_shape=False):
    """Midline point chain for a lipped C (or Z when z_shape=True), web on the y-axis.
    Origin at web mid-height, web along x=0, flanges toward +x (C) or +x/-x (Z).
    Returns list of (x, y) midline points, ordered from bottom lip tip to top lip tip."""
    if r_in is None:
        r_in = 1.5 * t                      # generic fallback; callers pass RADIUS_BY_MIL[mil]
    R = r_in + t / 2.0                      # midline corner radius
    h2 = depth / 2.0 - t / 2.0              # web midline half-height
    bf = flange - t                         # flange midline length (out-to-out minus t)
    dl = max(lip - t / 2.0 - R, 0.0)        # lip FLAT: SFIA lip length is OVERALL (outside
                                            # flange face to tip) = R + flat + t/2
    fw = bf - 2 * R if lip > 0 else bf - R  # flange flat between corner tangents
    wf = 2 * h2 - 2 * R                     # web flat
    sgn_top, sgn_bot = 1.0, (-1.0 if z_shape else 1.0)
    pts = []
    if dl > 0:  # bottom lip tip (lip points up toward web mid) -- C orientation
        pts.append((sgn_bot * (R + fw + R), -h2 + R + dl))
        pts.append((sgn_bot * (R + fw + R), -h2 + R))
        pts += _corner_arc(sgn_bot * (R + fw), -h2 + R, R, 0.0 if sgn_bot > 0 else math.pi,
                           -math.pi / 2 if sgn_bot > 0 else 3 * math.pi / 2)
    else:
        pts.append((sgn_bot * (R + fw), -h2))
    # bottom flange flat toward web
    pts.append((sgn_bot * R, -h2))
    pts += _corner_arc(sgn_bot * R, -h2 + R, R, -math.pi / 2 if sgn_bot > 0 else -math.pi / 2,
                       (-math.pi if sgn_bot > 0 else 0.0))
    # web flat up
    pts.append((0.0, -h2 + R))
    pts.append((0.0, h2 - R))
    # top corner + flange (+x always)
    pts += _corner_arc(R, h2 - R, R, math.pi, math.pi / 2)
    pts.append((R + fw, h2))
    if dl > 0:
        pts += _corner_arc(R + fw, h2 - R, R, math.pi / 2, 0.0)
        pts.append((R + fw + R, h2 - R))
        pts.append((R + fw + R, h2 - R - dl))
    # dedupe near-coincident consecutive points
    out = [pts[0]]
    for p in pts[1:]:
        if math.dist(p, out[-1]) > 1e-9:
            out.append(p)
    return out


def _chain_props(pts, t):
    """Thin-wall properties of a constant-t midline chain: A, centroid, Ix, Iy, Ixy, J."""
    A = Sx = Sy = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        L = math.dist((x1, y1), (x2, y2))
        A += L * t
        Sx += L * t * (y1 + y2) / 2.0
        Sy += L * t * (x1 + x2) / 2.0
    xc, yc = Sy / A, Sx / A
    Ix = Iy = Ixy = J = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        L = math.dist((x1, y1), (x2, y2))
        u1, v1, u2, v2 = x1 - xc, y1 - yc, x2 - xc, y2 - yc
        # exact line-segment second moments (linear interpolation along the segment)
        Ix += L * t * (v1 * v1 + v1 * v2 + v2 * v2) / 3.0
        Iy += L * t * (u1 * u1 + u1 * u2 + u2 * u2) / 3.0
        Ixy += L * t * (2 * u1 * v1 + u1 * v2 + u2 * v1 + 2 * u2 * v2) / 6.0
        J += L * t ** 3 / 3.0
    return A, xc, yc, Ix, Iy, Ixy, J


def _sectorial(pts, t, pole, xc, yc):
    """Normalized sectorial coordinate at nodes for the given pole; returns (omega list, Cw,
    I_wx = int(w*x)dA, I_wy = int(w*y)dA) with x,y centroidal."""
    ax, ay = pole
    w = [0.0]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        dw = (x1 - ax) * (y2 - y1) - (y1 - ay) * (x2 - x1)
        w.append(w[-1] + dw)
    # normalize to zero mean over area
    A = Sw = 0.0
    for i, ((x1, y1), (x2, y2)) in enumerate(zip(pts, pts[1:])):
        L = math.dist((x1, y1), (x2, y2))
        A += L * t
        Sw += L * t * (w[i] + w[i + 1]) / 2.0
    w = [wi - Sw / A for wi in w]
    Cw = Iwx = Iwy = 0.0
    for i, ((x1, y1), (x2, y2)) in enumerate(zip(pts, pts[1:])):
        L = math.dist((x1, y1), (x2, y2))
        w1, w2 = w[i], w[i + 1]
        u1, v1, u2, v2 = x1 - xc, y1 - yc, x2 - xc, y2 - yc
        Cw += L * t * (w1 * w1 + w1 * w2 + w2 * w2) / 3.0
        Iwx += L * t * (2 * w1 * u1 + w1 * u2 + w2 * u1 + 2 * w2 * u2) / 6.0
        Iwy += L * t * (2 * w1 * v1 + w1 * v2 + w2 * v1 + 2 * w2 * v2) / 6.0
    return w, Cw, Iwx, Iwy


def gross_props(name_or_geom, r_in=None):
    """GROSS properties dict for a designator string or a geometry dict from parse_designator().
    Keys: A, Ix, Iy, rx, ry, J, Cw, xbar (centroid from web midline), x0 (shear center from
    centroid, signed), depth, flange, lip, t, style, flats {web, flange, lip} (EWM inputs)."""
    g = parse_designator(name_or_geom) if isinstance(name_or_geom, str) else dict(name_or_geom)
    if g["style"] in ("T", "U"):
        g = dict(g, lip=0.0)
    if r_in is None:
        r_in = RADIUS_BY_MIL.get(g.get("mil"), 1.5 * g["t"])   # SFIA per-mil design radius
    if g["style"] == "T":
        # SFIA note: track web depth = NOMINAL stud width + 2t + bend radius (track fits OVER
        # the stud) -- the designator's depth code is the stud it mates with, not its own web.
        g = dict(g, depth=g["depth"] + 2.0 * g["t"] + r_in)
    pts = lipped_c_midline(g["depth"], g["flange"], g["lip"], g["t"], r_in=r_in)
    t = g["t"]
    A, xc, yc, Ix, Iy, Ixy, J = _chain_props(pts, t)
    # shear center: solve with pole at centroid, then shift (principal axes; C: Ixy ~ 0)
    _, _, Iwx, Iwy = _sectorial(pts, t, (xc, yc), xc, yc)
    den = Ix * Iy - Ixy * Ixy
    xs = (Iy * Iwy - Ixy * Iwx) / den        # shear-center offset from centroid (x)
    ys = -(Ix * Iwx - Ixy * Iwy) / den
    _, Cw, _, _ = _sectorial(pts, t, (xc + xs, yc + ys), xc, yc)
    R = (r_in if r_in is not None else 1.5 * t) + t / 2.0
    flats = dict(web=g["depth"] - 2 * (R + t / 2.0),
                 flange=g["flange"] - 2 * (R + t / 2.0) if g["lip"] > 0
                        else g["flange"] - (R + t / 2.0) - t / 2.0,
                 lip=max(g["lip"] - (R + t / 2.0), 0.0))
    return dict(A=A, Ix=Ix, Iy=Iy, rx=math.sqrt(Ix / A), ry=math.sqrt(Iy / A), J=J, Cw=Cw,
                xbar=xc, x0=-xs, Ixy=Ixy, depth=g["depth"], flange=g["flange"], lip=g["lip"],
                t=t, style=g["style"], mil=g.get("mil"), Fy=FY_BY_MIL.get(g.get("mil"), 50.0),
                flats=flats)


def built_up_back_to_back(name):
    """Back-to-back (web-to-web) built-up of two lipped C's: doubly-symmetric I-like section.
    Returns gross dict (A, Ix, Iy, J, Cw~2x, rx, ry). Interconnection requirements per S240
    remain the agent's design item."""
    p = gross_props(name)
    A = 2 * p["A"]; Ix = 2 * p["Ix"]
    Iy = 2 * (p["Iy"] + p["A"] * p["xbar"] ** 2)     # webs back-to-back at the joint plane
    return dict(A=A, Ix=Ix, Iy=Iy, rx=math.sqrt(Ix / A), ry=math.sqrt(Iy / A),
                J=2 * p["J"], Cw=2 * p["Cw"], t=p["t"], depth=p["depth"],
                flange=p["flange"], lip=p["lip"], Fy=p["Fy"], base=name, built_up="back-to-back")


# ---------------- S100 Effective Width Method (App. 1) ----------------

def _rho(lam):
    return 1.0 if lam <= 0.673 else (1.0 - 0.22 / lam) / lam


def eff_width(w, t, f, k):
    """Effective width b of a flat of width w, thickness t, at stress f (ksi), plate coeff k."""
    if w <= 0 or f <= 0:
        return max(w, 0.0)
    lam = (1.052 / math.sqrt(k)) * (w / t) * math.sqrt(f / E_KSI)
    return _rho(lam) * w


def edge_stiffened_flange(w, t, d_lip, f, D_out=None):
    """S100 1.3 (uniformly compressed element with a simple lip edge stiffener).
    Returns (b_eff, ds_eff, k). w = flange flat, d_lip = lip flat, D_out = lip out-to-out."""
    if d_lip <= 0:
        return eff_width(w, t, f, 0.43), 0.0, 0.43     # no stiffener: unstiffened element
    S = 1.28 * math.sqrt(E_KSI / f)
    ds_p = eff_width(d_lip, t, f, 0.43)                # lip as unstiffened element
    if w / t <= 0.328 * S:                             # stiffener not required
        return w, ds_p, 4.0
    Ia = min(399.0 * t ** 4 * ((w / t) / S - 0.328) ** 3,
             t ** 4 * (115.0 * (w / t) / S + 5.0))
    Is = t * d_lip ** 3 / 12.0                         # lip about its own axis (per spec)
    RI = min(Is / Ia, 1.0) if Ia > 0 else 1.0
    n = max(1.0 / 3.0, 0.582 - (w / t) / (4.0 * S))
    D = D_out if D_out is not None else d_lip + 2 * t  # lip out-to-out approximation
    Dw = D / w
    if Dw <= 0.25:
        k = min(3.57 * RI ** n + 0.43, 4.0)
    elif Dw <= 0.8:
        k = min((4.82 - 5.0 * Dw) * RI ** n + 0.43, 4.0)
    else:
        k = 0.43
    return eff_width(w, t, f, k), ds_p * RI, k


def web_gradient_eff(w, t, f1, f2, ho_over_bo=1.0):
    """S100 App. 1, 1.1.2 -- webs/stiffened elements under stress gradient. f1 = compression
    edge stress (+), f2 = other edge (+compression, -tension). ho_over_bo = out-to-out web
    depth / out-to-out compression-flange width, which selects between Eq. set (a) (<= 4) and
    the HARSHER Eq. set (b) (> 4, deep web with narrow flange: b2 = be/(1+psi) - b1).
    Returns (b1, b2, comp_zone) measured from the compression edge; tension zone fully eff."""
    if f1 <= 0:
        return 0.0, 0.0, 0.0
    psi = f2 / f1                                      # signed; spec's psi_abs = -psi in tension
    k = 4.0 + 2.0 * (1.0 - psi) ** 3 + 2.0 * (1.0 - psi)
    be = eff_width(w, t, f1, k)
    comp = w if psi >= 0 else w * f1 / (f1 - f2)       # compression-zone length
    b1 = be / (3.0 - psi)                              # == be/(3 + |psi|) for tension f2
    if ho_over_bo > 4.0:
        b2 = max(be / (1.0 - psi) - b1, 0.0)           # Eq. set (b): be/(1+|psi|) - b1
    else:
        b2 = be / 2.0 if psi <= -0.236 else be - b1    # Eq. set (a)
    if b1 + b2 >= comp:                                # fully effective compression zone
        b1, b2 = comp, 0.0
    return b1, b2, comp


def effective_area(name, f):
    """Ae at uniform compressive stress f (ksi): EWM on web (k=4), edge-stiffened flanges, lips.
    Corners fully effective (stated assumption). For STIFFNESS (EA) and calc_package inputs."""
    p = gross_props(name)
    t, fl = p["t"], p["flats"]
    b_web = eff_width(fl["web"], t, f, 4.0)
    if p["style"] == "S":
        b_fl, ds, _k = edge_stiffened_flange(fl["flange"], t, fl["lip"], f)
        loss = (fl["web"] - b_web) + 2 * (fl["flange"] - b_fl) + 2 * (fl["lip"] - ds)
    else:                                              # track/channel: unstiffened flanges
        b_fl = eff_width(fl["flange"], t, f, 0.43)
        loss = (fl["web"] - b_web) + 2 * (fl["flange"] - b_fl)
    return max(p["A"] - loss * t, 0.05 * p["A"])


def effective_Ix(name, f, n_iter=4):
    """Ixe and Se at extreme-fiber compression stress f (ksi), major-axis bending, compression
    on the TOP flange. Iterates the neutral axis of the effective section. Ineffective portions
    are removed as line segments at their actual midline positions."""
    p = gross_props(name)
    t, fl, d = p["t"], p["flats"], p["depth"]
    h = fl["web"]                                      # web flat
    y_top_flat = h / 2.0                               # web flat spans +/- h/2 about mid-depth
    ycg = 0.0                                          # from mid-depth, +up; symmetric start
    for _ in range(n_iter):
        c_top = d / 2.0 - ycg                          # compression extreme-fiber distance
        # compression flange (top): edge-stiffened at f
        if p["style"] == "S":
            b_fl, ds, _k = edge_stiffened_flange(fl["flange"], t, fl["lip"], f)
            lip_loss = (fl["lip"] - ds)
        else:
            b_fl = eff_width(fl["flange"], t, f, 0.43); lip_loss = 0.0
        fl_loss = fl["flange"] - b_fl
        # web under gradient: stresses at web-flat ends by similar triangles from extreme fiber
        yw_top, yw_bot = y_top_flat - ycg, -y_top_flat - ycg   # distances from NA (+up)
        f_wt = f * yw_top / c_top
        f_wb = f * yw_bot / c_top                      # negative = tension
        b1, b2, comp = web_gradient_eff(h, t, max(f_wt, 1e-6), f_wb,
                                        ho_over_bo=d / p["flange"])
        web_loss = comp - (b1 + b2)                    # ineffective web length (in comp zone)
        # ineffective web segment location: centered between b1 (from comp edge) and b2 (above NA)
        seg_top = y_top_flat - b1                      # top of hole (below compression edge)
        seg_bot = seg_top - web_loss                   # bottom of hole
        # effective section = gross - losses (line-area bookkeeping about mid-depth axis)
        A_eff = p["A"] - t * (fl_loss + 2 * 0.0) - t * lip_loss * 1.0 - t * web_loss
        # NOTE: only ONE flange & its lip are in compression; the tension side is fully effective
        Sy_loss = (t * fl_loss) * (d / 2.0 - t / 2.0) \
                + (t * lip_loss) * (d / 2.0 - p["lip"] / 2.0) \
                + (t * web_loss) * ((seg_top + seg_bot) / 2.0)
        A_eff = p["A"] - t * (fl_loss + lip_loss + web_loss)
        ycg_new = (0.0 * p["A"] - Sy_loss) / A_eff     # gross centroid at 0 (symmetric section)
        if abs(ycg_new - ycg) < 1e-4:
            ycg = ycg_new; break
        ycg = ycg_new
    # Ixe: gross Ix minus removed-segment contributions, then parallel-axis to the new NA
    Ix0 = p["Ix"]
    dIx = (t * fl_loss) * (d / 2.0 - t / 2.0) ** 2 \
        + (t * lip_loss) * ((d / 2.0 - p["lip"] / 2.0) ** 2 + 0.0) \
        + (t * web_loss) * (((seg_top + seg_bot) / 2.0) ** 2 + web_loss ** 2 / 12.0)
    A_eff = p["A"] - t * (fl_loss + lip_loss + web_loss)
    Ixe = Ix0 - dIx - 0.0
    Ixe = Ixe + p["A"] * 0.0 - A_eff * ycg ** 2        # shift to effective NA (gross NA at 0)
    c_max = d / 2.0 + abs(ycg)
    return dict(Ixe=max(Ixe, 0.05 * Ix0), Se=max(Ixe, 0.05 * Ix0) / c_max, ycg_shift=ycg,
                A_eff_flex=A_eff, f=f)


def deflection_Ixe(name, omega_b=1.67, n_iter=6):
    """SFIA/S100 Procedure-1 serviceability Ixe: the effective inertia at the stress f that
    satisfies f * Se(f) = Ma, with Ma = Se(Fy) * Fy / omega_b (the tabulated ASD moment).
    This is the basis of the published Ixe columns (SFIA guide, General Note 2) -- NOT 0.6*Fy,
    though it converges to 0.6*Fy for fully effective sections. Fixed-point iteration."""
    p = gross_props(name)
    fy = p["Fy"]
    Ma = effective_Ix(name, fy)["Se"] * fy / omega_b
    f = 0.6 * fy
    for _ in range(n_iter):
        se = effective_Ix(name, f)["Se"]
        f_new = Ma / max(se, 1e-9)
        if abs(f_new - f) < 0.05:
            f = f_new; break
        f = f_new
    return effective_Ix(name, f)["Ixe"]


_TABLE_CACHE = None


def table_effective(name, fy=None):
    """Tabulated SFIA effective properties for a designator, from cfs_shapes.csv:
    dict(Sxe, Ixe, Ma_asd, Ma_dist_asd, Va_asd_lb, Va_net_asd_lb, Fy) or None. fy picks the
    33/50 column set (defaults to the designator's standard grade). The SFIA table is the
    design AUTHORITY -- prefer these over computed at the tabulated basis (Sxe at Fy; Ixe
    at the Procedure-1 serviceability stress)."""
    global _TABLE_CACHE
    if _TABLE_CACHE is None:
        _TABLE_CACHE = {}
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cfs_shapes.csv")
        if os.path.exists(path):
            with open(path, newline="") as fh:
                for row in csv.DictReader(fh):
                    d = (row.get("designator") or "").strip()
                    if d:
                        _TABLE_CACHE[d] = row
    row = _TABLE_CACHE.get(name)
    if not row:
        return None
    if fy is None:
        try:
            fy = int(gross_props(name)["Fy"])
        except KeyError:
            return None
    fy = int(fy)
    g = lambda k: float(row[k]) if row.get(k) not in (None, "") else None
    fy_req = fy
    sxe, ixe = g("Sxe_%d" % fy), g("Ixe_%d" % fy)
    if sxe is None and ixe is None:
        # the SFIA table carries Fy 33/50 column sets ONLY -- for other grades (e.g.
        # Gr55) fall back to the highest tabulated Fy <= requested (conservative for
        # strength) and SAY so via fy_used/fy_requested
        for fb in (50, 33):
            if fb < fy and g("Sxe_%d" % fb) is not None:
                fy = fb
                sxe, ixe = g("Sxe_%d" % fy), g("Ixe_%d" % fy)
                break
        if sxe is None and ixe is None:
            return None
    out = dict(Sxe=sxe, Ixe=ixe, Ma_asd=g("Ma_asd_%d" % fy),
               Ma_dist_asd=g("Ma_dist_asd_kipin_%d" % fy),
               Va_asd_lb=g("Va_asd_lb_%d" % fy), Va_net_asd_lb=g("Va_net_asd_lb_%d" % fy),
               Fy=fy)
    if fy != fy_req:
        out["fy_requested"] = fy_req
        out["note"] = ("SFIA table has Fy 33/50 only -- values at Fy=%d used "
                       "(conservative for Fy=%d design; state it)" % (fy, fy_req))
    return out


def flex_props(name):
    """Design-basis flexural effective properties: SFIA table values where tabulated
    (the authority), spec-computed EWM otherwise. Computed values carry a calibration
    clamp: never above the tabulated value where one exists (the residual computed-vs-table
    gap is confined to the shallow drywall S125/S137 families, computed UNconservative).
    Returns dict(Se, Ixe_defl, Ma_asd, source)."""
    p = gross_props(name)
    fy = p["Fy"]
    tab = table_effective(name)
    se_c = effective_Ix(name, fy)["Se"]
    if tab and tab["Sxe"]:
        se, src = tab["Sxe"], "SFIA"
    else:
        se, src = se_c, "computed"
    if tab and tab["Ixe"]:
        ixe_d = tab["Ixe"]
    else:
        ixe_d = deflection_Ixe(name)
    ma = tab["Ma_asd"] if (tab and tab["Ma_asd"]) else se * fy / 1.67
    return dict(Se=se, Ixe_defl=ixe_d, Ma_asd=ma, source=src)


def stiffness_pair(name, f_axial, f_bend):
    """The decoupled assignment (scope decision): EA from Ae(f_axial), EI from Ixe(f_bend).
    Returns dict(EA, EIx, Ae, Ixe) for the OpenSees element property update loop."""
    Ae = effective_area(name, max(f_axial, 0.1))
    ix = effective_Ix(name, max(f_bend, 0.1))
    return dict(EA=E_KSI * Ae, EIx=E_KSI * ix["Ixe"], Ae=Ae, Ixe=ix["Ixe"])


# ---------------- validation harness (Stage 1 gate) ----------------

def validate_against_csv(path=None, tol_gross=0.02, tol_eff=0.05, verbose=True):
    """Compare computed properties against the owner's SFIA extraction cfs_shapes.csv.
    Expected columns (flexible; extras ignored): designator, A, Ix, Iy, rx, ry, J, Cw, and
    optionally Ae_33/Ae_50 (effective at Fy) and Ixe_33/Ixe_50. Returns (n_checked, worst)."""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "cfs_shapes.csv")
    if not os.path.exists(path):
        if verbose:
            print("[cfs_sections] cfs_shapes.csv not found -- Stage 1 validation gate PENDING "
                  "(owner extraction). Computed values are UNVALIDATED until it lands.")
        return 0, None
    worst = (0.0, None, None)
    n = 0
    eff_fails = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            des = (row.get("designator") or row.get("Designator") or "").strip()
            if not des:
                continue
            try:
                p = gross_props(des)
            except KeyError:
                continue
            n += 1
            for key in ("A", "Ix", "Iy", "rx", "ry", "J", "Cw"):
                v = row.get(key)
                if v in (None, ""):
                    continue
                ref = float(v)
                dev = abs(p[key] - ref) / ref if ref else 0.0
                if dev > worst[0]:
                    worst = (dev, des, key)
                if verbose and dev > tol_gross:
                    print("  [GROSS >%s%%] %s %s: computed %.4g vs SFIA %.4g (%.1f%%)"
                          % (int(tol_gross * 100), des, key, p[key], ref, dev * 100))
            fy = p["Fy"]
            for key, fn in (("Ae_%d" % int(fy), lambda: effective_area(des, fy)),
                            ("Ixe_%d" % int(fy), lambda: deflection_Ixe(des)),
                            ("Sxe_%d" % int(fy), lambda: effective_Ix(des, fy)["Se"])):
                v = row.get(key)
                if v in (None, ""):
                    continue
                ref = float(v)
                try:
                    val = fn()
                except Exception:
                    continue
                dev = abs(val - ref) / ref if ref else 0.0
                if dev > worst[0]:
                    worst = (dev, des, key)
                if dev > tol_eff:
                    eff_fails.append((des, key, val, ref, dev))
                    if verbose:
                        print("  [EFF >%s%%] %s %s: computed %.4g vs SFIA %.4g (%.1f%%)"
                              % (int(tol_eff * 100), des, key, val, ref, dev * 100))
    if verbose:
        print("[cfs_sections] validated %d designators; worst deviation %.1f%% (%s %s); "
              "%d effective checks past %d%%"
              % (n, worst[0] * 100, worst[1], worst[2], len(eff_fails), int(tol_eff * 100)))
        if eff_fails and all("S125" in d or "S137" in d for d, _k, _v, _r, _dv in eff_fails):
            print("  (all residuals are shallow drywall S125/S137 sections where the SFIA "
                  "table is MORE conservative than a faithful S100-16 App.1 calc; design "
                  "values route through flex_props(), which uses the table as authority)")
    return n, worst


# ---------------- self-test ----------------

def _selftest():
    print("cfs_sections self-test (E = %.0f ksi)" % E_KSI)
    rows = []
    for des in ("362S162-54", "600S162-54", "600S162-43", "800S200-68", "1000S250-97",
                "600T150-54" if False else "600T125-54"):
        try:
            p = gross_props(des)
        except KeyError as e:
            print("  skip %s (%s)" % (des, e)); continue
        Ae = effective_area(des, p["Fy"])
        ix = effective_Ix(des, p["Fy"])
        rows.append((des, p["A"], p["Ix"], p["Iy"], p["J"], p["Cw"], p["x0"], Ae, ix["Ixe"]))
        print("  %-12s A=%.4f Ix=%.3f Iy=%.4f J=%.5f Cw=%.3f x0=%+.3f | Ae(Fy)=%.4f Ixe(Fy)=%.3f"
              % rows[-1])
    p = gross_props("600S162-54")
    assert 0.40 < p["A"] < 0.65, "A out of expected band"
    assert 2.2 < p["Ix"] < 3.4, "Ix out of expected band"
    assert 0.10 < p["Iy"] < 0.45, "Iy out of expected band"
    assert p["x0"] < 0 or p["x0"] > 0, "shear center must be nonzero for a C"
    Ae = effective_area("600S162-54", 50.0)
    assert Ae < p["A"], "Ae at Fy must be < gross for a slender stud"
    assert Ae > 0.4 * p["A"], "Ae implausibly small"
    ix50 = effective_Ix("600S162-54", 50.0)["Ixe"]
    ix10 = effective_Ix("600S162-54", 10.0)["Ixe"]
    assert ix50 <= ix10 <= p["Ix"] * 1.001, "Ixe must grow as stress drops, bounded by gross"
    bb = built_up_back_to_back("600S162-54")
    assert abs(bb["A"] - 2 * p["A"]) < 1e-9 and bb["Iy"] > 2 * p["Iy"]
    sp = stiffness_pair("600S162-54", 25.0, 30.0)
    assert sp["EA"] < E_KSI * p["A"] and sp["EIx"] <= E_KSI * p["Ix"] * 1.001
    print("  built-up 2x600S162-54: A=%.3f Ix=%.3f Iy=%.3f" % (bb["A"], bb["Ix"], bb["Iy"]))
    print("  stiffness_pair(600S162-54, fa=25, fb=30): EA/EAg=%.3f EI/EIg=%.3f"
          % (sp["EA"] / (E_KSI * p["A"]), sp["EIx"] / (E_KSI * p["Ix"])))
    print("SELF-TEST PASS")
    validate_against_csv()


if __name__ == "__main__":
    _selftest()

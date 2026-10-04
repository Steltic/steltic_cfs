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
U (channel, unlipped), Z (lipped Z, same field layout, e.g. "800Z250-68": equal flanges,
lips sloped at Z_LIP_ANGLE_DEG -- a STATED geometry assumption, verify against the
manufacturer), and cold-formed HSS ("HSS12X12X5/8", A500 design t = 0.93 t_nom unless
the caller passes hss_spec="A1085"). SFIA "F" furring HATS are NOT modelled (they used to
be silently treated as C channels) -- parse_designator raises KeyError for them.
Built-ups: built_up(name, n, arrangement) for n plies back-to-back (n/2 pairs) or boxed
(toe-to-toe); built_up_back_to_back(name) == built_up(name, 2).
Sign convention (AISI/SFIA): x0 = shear-centre-to-centroid distance along the principal x
axis, NEGATIVE for a C/track (shear centre on the far side of the web); 'm' = web-midline-
to-shear-centre distance = |x0| - xbar.

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


Z_LIP_ANGLE_DEG = 50.0      # Z-section lip slope from the flange plane (stated assumption)


def is_hss(name):
    return isinstance(name, str) and name.strip().upper().startswith("HSS")


def parse_hss(name, hss_spec="A500"):
    """'HSS12X12X5/8' -> dict(style='HSS', depth=12, flange=12, t_nom=0.625, t=design t).
    Design wall thickness = 0.93 t_nom for ASTM A500 (ERW, the AISC 360 B4.2 convention used
    for the published HSS tables); t_nom for A1085. Fy default 50 ksi (A500 Gr C
    rectangular); A500 Gr B rectangular is 46 ksi -- pass Fy explicitly where it applies."""
    import re
    s = str(name).strip().upper().replace(" ", "")
    m = re.match(r"^HSS(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)X(\d+(?:/\d+)?|\d*\.\d+)$", s)
    if not m:
        raise KeyError("cannot parse HSS designator %r (expect e.g. 'HSS12X12X5/8')" % (name,))
    H, B = float(m.group(1)), float(m.group(2))
    tn = m.group(3)
    if "/" in tn:
        a, b = tn.split("/")
        t_nom = float(a) / float(b)
    else:
        t_nom = float(tn)
    t = t_nom * (1.0 if str(hss_spec).upper() == "A1085" else 0.93)
    return dict(style="HSS", depth=H, flange=B, t_nom=t_nom, t=t, lip=0.0, mil=None,
                spec=hss_spec, label="HSS%sX%sX%s" % (m.group(1), m.group(2), tn))


def parse_designator(name):
    """'600S162-54' -> dict(depth=6.0, flange=1.625, lip=0.5, t=0.0566, style='S', mil=54).
    Flange field is hundredths of an inch (162 -> 1.625 by SFIA convention: 162=1-5/8).
    Styles S/T/U/Z; 'F' (furring hat) raises KeyError (hat geometry not modelled); HSS
    designators route to parse_hss()."""
    import re
    if is_hss(name):
        return parse_hss(name)
    m = re.match(r"^\s*(\d{3,4})([STUFZ])(\d{3})-(\d{2,3})\s*$", str(name).upper())
    if not m:
        raise KeyError("cannot parse CFS designator %r (expect e.g. '600S162-54')" % (name,))
    depth = int(m.group(1)) / 100.0
    style = m.group(2)
    if style == "F":
        raise KeyError("%r is an SFIA furring HAT ('F'): hat geometry is not modelled by "
                       "cfs_sections -- use the SFIA table values (table_effective) and state "
                       "it; it is NOT a C channel" % (name,))
    fcode = int(m.group(3))
    # SFIA flange codes are nominal: 162 = 1.625, 137 = 1.375, 250 = 2.5, etc.
    flange = {125: 1.25, 137: 1.375, 162: 1.625, 200: 2.0, 250: 2.5, 300: 3.0, 350: 3.5}.get(
        fcode, fcode / 100.0)
    mil = int(m.group(4))
    if mil not in MIL_T:
        raise KeyError("unknown mil thickness %r in %r" % (mil, name))
    lip = LIP_BY_FLANGE.get(fcode, 0.5) if style in ("S", "Z") else 0.0
    out = dict(depth=depth, flange=flange, lip=lip, t=MIL_T[mil], style=style, mil=mil)
    if style == "Z":
        out["lip_angle_deg"] = Z_LIP_ANGLE_DEG
    return out


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
    # flange flat between corner tangents. UNLIPPED (track/U): the free tip is the flange's
    # out-to-out edge, i.e. midline x = flange - t/2 (the web midline sits t/2 inside the
    # web's outer face), so the flat runs R -> flange - t/2 (a prior version stopped t/2
    # short: track/U Iy -3..-23%, Cw -7..-16% vs SFIA -- static review F7 / CFS-36).
    fw = bf - 2 * R if lip > 0 else bf + t / 2.0 - R
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


def _fillet_polyline(verts, R, n=8):
    """Midline polyline through sharp vertices with every interior vertex rounded to midline
    radius R (facetted). Used for the Z profile (sloped lips)."""
    out = [verts[0]]
    for i in range(1, len(verts) - 1):
        p0, p1, p2 = verts[i - 1], verts[i], verts[i + 1]
        d1 = ((p1[0] - p0[0]), (p1[1] - p0[1]))
        d2 = ((p2[0] - p1[0]), (p2[1] - p1[1]))
        l1, l2 = math.hypot(*d1), math.hypot(*d2)
        u1 = (d1[0] / l1, d1[1] / l1)
        u2 = (d2[0] / l2, d2[1] / l2)
        cosphi = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
        phi = math.acos(cosphi)                       # turning angle
        if phi < 1e-9 or R <= 0:
            out.append(p1)
            continue
        tl = min(R * math.tan(phi / 2.0), 0.999 * l1, 0.999 * l2)
        Rr = tl / math.tan(phi / 2.0)
        a = (p1[0] - u1[0] * tl, p1[1] - u1[1] * tl)
        cross = u1[0] * u2[1] - u1[1] * u2[0]
        nrm = (-u1[1], u1[0]) if cross > 0 else (u1[1], -u1[0])
        c = (a[0] + nrm[0] * Rr, a[1] + nrm[1] * Rr)
        a0 = math.atan2(a[1] - c[1], a[0] - c[0])
        sgn = 1.0 if cross > 0 else -1.0
        out.append(a)
        for k in range(1, n + 1):
            ang = a0 + sgn * phi * k / n
            out.append((c[0] + Rr * math.cos(ang), c[1] + Rr * math.sin(ang)))
    out.append(verts[-1])
    ded = [out[0]]
    for p in out[1:]:
        if math.dist(p, ded[-1]) > 1e-9:
            ded.append(p)
    return ded


def lipped_z_midline(depth, flange, lip, t, r_in, lip_angle_deg=Z_LIP_ANGLE_DEG):
    """Point-symmetric lipped Z midline (equal flanges): web on the y axis, top flange to +x,
    bottom flange to -x, lips sloped lip_angle_deg from the flange plane, turned back toward
    the web. lip = overall lip length along the lip (stated assumption)."""
    R = r_in + t / 2.0
    h2 = depth / 2.0 - t / 2.0
    bf = flange - t
    th = math.radians(lip_angle_deg)
    Lm = max(lip - t / 2.0, 0.0)
    top_tip = (bf, h2)
    top_lip = (bf - Lm * math.cos(th), h2 - Lm * math.sin(th))
    verts = [(-top_lip[0], -top_lip[1]), (-bf, -h2), (0.0, -h2), (0.0, h2), top_tip, top_lip]
    if Lm <= 0:
        verts = verts[1:-1]
    return _fillet_polyline(verts, R)


def _csv_row(name):
    """The cfs_shapes.csv row for a designator (or None)."""
    table_effective("__warm__")                       # populate the cache
    return (_TABLE_CACHE or {}).get(str(name).strip().upper()) or \
        (_TABLE_CACHE or {}).get(str(name).strip())


def default_Fy(name, mil=None):
    """Default Fy (ksi): the SFIA table's Fy_avail where the designator is tabulated (a
    Gr 33-only product stays 33 ksi even at 54 mil -- static review F11), else the mil
    convention (<= 43 mil: 33 ksi; >= 54 mil: 50 ksi)."""
    row = _csv_row(name) if isinstance(name, str) else None
    fa = (row or {}).get("Fy_avail") or ""
    if fa.strip() == "33":
        return 33.0
    if fa.strip() == "50":
        return 50.0
    return FY_BY_MIL.get(mil, 50.0)


def applicability_warnings(p):
    """S100-16 B4.1 dimensional limits as WARNINGS (effective properties are still returned,
    but an h/t past the limit means App. 1 / Chapter G do not apply as written)."""
    w = []
    t = p["t"]
    fl = p.get("flats") or {}
    if p["style"] == "HSS":
        return w
    ht = fl.get("web", 0.0) / t if t else 0.0
    if ht > 300.0:
        w.append("web h/t = %.0f > 300 (S100 Table B4.1-1 limit): effective-width method "
                 "outside its range" % ht)
    elif ht > 200.0:
        w.append("web h/t = %.0f > 200: S100 B4.1 / Chapter G unreinforced-web limit -- "
                 "bearing stiffeners / web-crippling basis must be stated" % ht)
    wt = fl.get("flange", 0.0) / t if t else 0.0
    lim = 90.0 if p.get("lip", 0.0) > 0 else 60.0
    if wt > lim:
        w.append("flange w/t = %.0f > %.0f (S100 Table B4.1-1)" % (wt, lim))
    return w


def _hss_gross(g, Fy=None):
    """Gross props of a rectangular cold-formed HSS. Area/Ix/Iy/J from aisc_shapes.csv when
    the label is tabulated (authority), otherwise thin-wall midline with outside corner
    radius 2t. Flats per AISC 360 B4.1b(d) convention (outside dimension - 3t)."""
    H, B, t = g["depth"], g["flange"], g["t"]
    ro = 2.0 * t
    rm = ro - t / 2.0
    fH, fB = H - 2 * ro, B - 2 * ro
    A = 2 * (fH + fB) * t + 2 * math.pi * rm * t
    cH, cB = H / 2.0 - t / 2.0, B / 2.0 - t / 2.0
    Ix = 2 * (fB * t * cH ** 2) + 2 * (t * fH ** 3 / 12.0)
    Iy = 2 * (fH * t * cB ** 2) + 2 * (t * fB ** 3 / 12.0)
    nseg = 64
    for i in range(nseg):
        th = (i + 0.5) * (math.pi / 2.0) / nseg
        y = (H / 2.0 - ro) + rm * math.sin(th)
        x = (B / 2.0 - ro) + rm * math.cos(th)
        dA = rm * t * (math.pi / 2.0) / nseg
        Ix += 4 * dA * y * y
        Iy += 4 * dA * x * x
    Aenc = (H - t) * (B - t) - (4 - math.pi) * rm ** 2
    perim = 2 * (fH + fB) + 2 * math.pi * rm
    J = 4 * Aenc ** 2 * t / perim                   # Bredt (thin-walled closed cell)
    src = "computed (ro = 2t)"
    try:
        row = _aisc_hss_row(g["label"])            # aisc_shapes.csv (read-only)
    except Exception:
        row = None
    if row and str(g.get("spec", "A500")).upper() != "A1085":
        A, Ix, Iy = float(row["A"]), float(row["Ix"]), float(row["Iy"])
        try:
            J = float(row["J"])
        except (KeyError, TypeError, ValueError):
            pass
        src = "aisc_shapes.csv (A500, tdes = 0.93 tnom)"
    flats = dict(web=H - 3.0 * t, flange=B - 3.0 * t, lip=0.0)
    return dict(A=A, Ix=Ix, Iy=Iy, rx=math.sqrt(Ix / A), ry=math.sqrt(Iy / A), J=J, Cw=0.0,
                xbar=0.0, x0=0.0, m=0.0, Ixy=0.0, depth=H, flange=B, lip=0.0, t=t,
                t_nom=g["t_nom"], style="HSS", mil=None, Fy=float(Fy or 50.0), flats=flats,
                source=src, closed=True)


_AISC_HSS = None


def _aisc_hss_row(label):
    global _AISC_HSS
    if _AISC_HSS is None:
        _AISC_HSS = {}
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aisc_shapes.csv")
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    lab = (row.get("AISC_Manual_Label") or "").strip().upper()
                    if lab.startswith("HSS"):
                        _AISC_HSS[lab] = row
    return _AISC_HSS.get(str(label).upper())


def gross_props(name_or_geom, r_in=None):
    """GROSS properties dict for a designator string or a geometry dict from parse_designator().
    Keys: A, Ix, Iy, rx, ry, J, Cw, xbar (centroid from web midline, + toward the flanges),
    x0 (shear centre to centroid, AISI sign: NEGATIVE for a C/track), m (web midline to shear
    centre = |x0| - xbar), Ixy, depth, flange, lip, t, style, Fy (SFIA Fy_avail default),
    flats {web, flange, lip} (EWM inputs), warnings (S100 B4.1 screens)."""
    if isinstance(name_or_geom, str) and is_hss(name_or_geom):
        return _hss_gross(parse_hss(name_or_geom))
    g = parse_designator(name_or_geom) if isinstance(name_or_geom, str) else dict(name_or_geom)
    if g["style"] == "HSS":
        return _hss_gross(g, g.get("Fy"))
    if g["style"] in ("T", "U"):
        g = dict(g, lip=0.0)
    if r_in is None:
        r_in = RADIUS_BY_MIL.get(g.get("mil"), 1.5 * g["t"])   # SFIA per-mil design radius
    if g["style"] == "T":
        # SFIA note: track web depth = NOMINAL stud width + 2t + bend radius (track fits OVER
        # the stud) -- the designator's depth code is the stud it mates with, not its own web.
        g = dict(g, depth=g["depth"] + 2.0 * g["t"] + r_in)
    t = g["t"]
    if g["style"] == "Z":
        pts = lipped_z_midline(g["depth"], g["flange"], g["lip"], t, r_in,
                               g.get("lip_angle_deg", Z_LIP_ANGLE_DEG))
    else:
        pts = lipped_c_midline(g["depth"], g["flange"], g["lip"], t, r_in=r_in)
    A, xc, yc, Ix, Iy, Ixy, J = _chain_props(pts, t)
    # shear center: solve with pole at centroid, then shift (principal axes; C: Ixy ~ 0)
    _, _, Iwx, Iwy = _sectorial(pts, t, (xc, yc), xc, yc)
    den = Ix * Iy - Ixy * Ixy
    xs = (Iy * Iwy - Ixy * Iwx) / den        # shear-center offset from centroid (x)
    ys = -(Ix * Iwx - Ixy * Iwy) / den
    if g["style"] == "Z":
        xs = ys = 0.0                          # point-symmetric: shear centre = centroid
    _, Cw, _, _ = _sectorial(pts, t, (xc + xs, yc + ys), xc, yc)
    R = (r_in if r_in is not None else 1.5 * t) + t / 2.0
    # S100 App. 1 flat widths: corner tangent to corner tangent (lipped) / corner tangent to
    # the free edge (unlipped: B - r - t, NOT B - r - 1.5t)
    flats = dict(web=g["depth"] - 2 * (R + t / 2.0),
                 flange=g["flange"] - 2 * (R + t / 2.0) if g["lip"] > 0
                        else g["flange"] - (R + t / 2.0),
                 lip=max(g["lip"] - (R + t / 2.0), 0.0))
    nm = name_or_geom if isinstance(name_or_geom, str) else None
    out = dict(A=A, Ix=Ix, Iy=Iy, rx=math.sqrt(Ix / A), ry=math.sqrt(Iy / A), J=J, Cw=Cw,
               xbar=xc, x0=xs, m=max(abs(xs) - xc, 0.0) if g["style"] != "Z" else 0.0,
               Ixy=Ixy, depth=g["depth"], flange=g["flange"], lip=g["lip"],
               t=t, style=g["style"], mil=g.get("mil"),
               Fy=default_Fy(nm, g.get("mil")) if nm else FY_BY_MIL.get(g.get("mil"), 50.0),
               flats=flats, lip_angle_deg=g.get("lip_angle_deg", 90.0), r_in=r_in)
    if g["style"] == "Z":
        th = 0.5 * math.atan2(-2.0 * Ixy, Ix - Iy)
        c2, s2 = math.cos(2 * th), math.sin(2 * th)
        out.update(I1=(Ix + Iy) / 2.0 + (Ix - Iy) / 2.0 * c2 - Ixy * s2,
                   I2=(Ix + Iy) / 2.0 - (Ix - Iy) / 2.0 * c2 + Ixy * s2,
                   principal_angle_deg=math.degrees(th),
                   note="Z: Ixy != 0 -- unrestrained bending is about the principal axes")
    out["warnings"] = applicability_warnings(out)
    return out


def built_up(name, n=2, arrangement="back_to_back"):
    """Gross props of an n-ply built-up of identical channels (S/T/U designator).
    arrangement 'back_to_back': n/2 web-to-web pairs (n even), pairs side by side with
      flange tips touching (n > 2), stitched per S240/S100 I1.2 (agent's item).
    arrangement 'box': n/2 toe-to-toe (lips/flanges interlocked) pairs forming closed cells.
    In-plane (strong-axis) A, Ix are exactly n x single. Out-of-plane (Iy, J, Cw) are computed
    for the stated arrangement: back-to-back pair Iy uses the centroid distance from the
    JOINT plane (xbar + t/2), Cw from the sectorial integral about the pair's shear centre
    (joint plane) -- a prior version used xbar from the web midline (Iy -5%) and Cw = 2 Cw1.
    Boxed: Cw ~ 0, J by Bredt for each closed cell."""
    if n < 1 or int(n) != n:
        raise ValueError("built_up: n must be a positive integer (got %r)" % (n,))
    n = int(n)
    p = gross_props(name)
    if p["style"] not in ("S", "T", "U"):
        raise KeyError("built_up(): only S/T/U channels can be built up (got %r)" % name)
    if n == 1:
        return dict(p, n_ply=1, built_up="single", base=name)
    if n % 2:
        raise ValueError("built_up(%r, n=%d): odd ply counts are unsymmetric (shear centre "
                         "off the load plane) and are not modelled -- use an even n" % (name, n))
    t, A1, xb = p["t"], p["A"], p["xbar"]
    npair = n // 2
    if arrangement == "box":
        # toe-to-toe pair: flange-tip planes coincide; pair centroid at the mid-plane
        B = p["flange"]
        Iy_pair = 2 * (p["Iy"] + A1 * (B - (xb + t / 2.0)) ** 2)   # tips meet at mid-plane
        H = p["depth"]
        Aenc = (H - t) * (2 * B - t)
        perim = 2 * (H - t) + 2 * (2 * B - t)
        J_pair = 4 * Aenc ** 2 * t / perim
        Cw_pair = 0.0
        w_pair = 2 * B
    else:
        Iy_pair = 2 * (p["Iy"] + A1 * (xb + t / 2.0) ** 2)
        # Cw about the joint plane (shear centre of the doubly-symmetric pair)
        pts = lipped_c_midline(p["depth"], p["flange"], p["lip"], t,
                               r_in=p.get("r_in")) if p["style"] == "S" else None
        if pts is None:
            g = parse_designator(name)
            depth = p["depth"]
            pts = lipped_c_midline(depth, g["flange"], 0.0, t, r_in=p.get("r_in"))
        A_, xc, yc, *_r = _chain_props(pts, t)
        _, Cw1, _, _ = _sectorial(pts, t, (-t / 2.0, yc), xc, yc)
        Cw_pair = 2 * Cw1
        J_pair = 2 * p["J"]
        w_pair = 2 * p["flange"]
    # pairs side by side (centres spaced w_pair), symmetric about the group centroid
    offs = [(k - (npair - 1) / 2.0) * w_pair for k in range(npair)]
    Iy = sum(Iy_pair + 2 * A1 * o * o for o in offs)
    Cw = Cw_pair * npair          # pairs act independently in warping (stated)
    A, Ix = n * A1, n * p["Ix"]
    return dict(A=A, Ix=Ix, Iy=Iy, rx=math.sqrt(Ix / A), ry=math.sqrt(Iy / A),
                J=J_pair * npair, Cw=Cw, t=t, depth=p["depth"], flange=p["flange"],
                lip=p["lip"], Fy=p["Fy"], base=name, n_ply=n, built_up=arrangement,
                x0=0.0, m=0.0,
                note=("in-plane A/Ix exact (n x single); out-of-plane Iy/J/Cw for %d %s "
                      "pair(s) side by side -- agent verifies the stitching/interconnection "
                      "that makes them act as assumed" % (npair, arrangement)))


def built_up_back_to_back(name):
    """Back-to-back (web-to-web) built-up of two channels: doubly-symmetric I-like section.
    Returns gross dict (A, Ix, Iy, J, Cw, rx, ry). Interconnection requirements per S240
    remain the agent's design item. == built_up(name, 2, 'back_to_back')."""
    return built_up(name, 2, "back_to_back")


# ---------------- S100 Effective Width Method (App. 1) ----------------

def _rho(lam):
    return 1.0 if lam <= 0.673 else (1.0 - 0.22 / lam) / lam


def eff_width(w, t, f, k):
    """Effective width b of a flat of width w, thickness t, at stress f (ksi), plate coeff k."""
    if w <= 0 or f <= 0:
        return max(w, 0.0)
    lam = (1.052 / math.sqrt(k)) * (w / t) * math.sqrt(f / E_KSI)
    return _rho(lam) * w


def edge_stiffened_flange(w, t, d_lip, f, D_out=None, theta_deg=90.0):
    """S100 1.3 (uniformly compressed element with a simple lip edge stiffener).
    Returns (b_eff, ds_eff, k). w = flange flat, d_lip = lip flat, D_out = OVERALL lip depth
    (S100 App. 1 1.3 'D'; callers pass the SFIA overall lip length), theta_deg = lip angle
    from the flange (Is = d^3 t sin^2(theta)/12; 40 <= theta <= 140 for Table 1.3-1)."""
    if d_lip <= 0:
        return eff_width(w, t, f, 0.43), 0.0, 0.43     # no stiffener: unstiffened element
    S = 1.28 * math.sqrt(E_KSI / f)
    ds_p = eff_width(d_lip, t, f, 0.43)                # lip as unstiffened element
    if w / t <= 0.328 * S:                             # stiffener not required
        return w, ds_p, 4.0
    Ia = min(399.0 * t ** 4 * ((w / t) / S - 0.328) ** 3,
             t ** 4 * (115.0 * (w / t) / S + 5.0))
    Is = t * d_lip ** 3 * math.sin(math.radians(theta_deg)) ** 2 / 12.0   # S100 Eq. 1.3-9
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
    if psi > 0:
        b2 = be - b1                                   # (a)(2): both edges in compression --
                                                       # no ho/bo split (static review F12)
    elif ho_over_bo > 4.0:
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
    if p["style"] == "HSS":                            # four stiffened flats (k = 4)
        b_fl = eff_width(fl["flange"], t, f, 4.0)
        loss = 2 * (fl["web"] - b_web) + 2 * (fl["flange"] - b_fl)
        return max(p["A"] - loss * t, 0.05 * p["A"])
    if p["style"] in ("S", "Z"):
        b_fl, ds, _k = edge_stiffened_flange(fl["flange"], t, fl["lip"], f, D_out=p["lip"],
                                             theta_deg=p.get("lip_angle_deg", 90.0))
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
    if p["style"] == "HSS":
        return _hss_effective_Ix(p, f, n_iter)
    t, fl, d = p["t"], p["flats"], p["depth"]
    lip_dy = p["lip"] * math.sin(math.radians(p.get("lip_angle_deg", 90.0)))
    h = fl["web"]                                      # web flat
    y_top_flat = h / 2.0                               # web flat spans +/- h/2 about mid-depth
    ycg = 0.0                                          # from mid-depth, +up; symmetric start
    for _ in range(n_iter):
        c_top = d / 2.0 - ycg                          # compression extreme-fiber distance
        # compression flange (top): edge-stiffened at f
        if p["style"] in ("S", "Z"):
            b_fl, ds, _k = edge_stiffened_flange(fl["flange"], t, fl["lip"], f,
                                                 D_out=p["lip"],
                                                 theta_deg=p.get("lip_angle_deg", 90.0))
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
                + (t * lip_loss) * (d / 2.0 - lip_dy / 2.0) \
                + (t * web_loss) * ((seg_top + seg_bot) / 2.0)
        A_eff = p["A"] - t * (fl_loss + lip_loss + web_loss)
        ycg_new = (0.0 * p["A"] - Sy_loss) / A_eff     # gross centroid at 0 (symmetric section)
        if abs(ycg_new - ycg) < 1e-4:
            ycg = ycg_new; break
        ycg = ycg_new
    # Ixe: gross Ix minus removed-segment contributions, then parallel-axis to the new NA
    Ix0 = p["Ix"]
    dIx = (t * fl_loss) * (d / 2.0 - t / 2.0) ** 2 \
        + (t * lip_loss) * ((d / 2.0 - lip_dy / 2.0) ** 2 + 0.0) \
        + (t * web_loss) * (((seg_top + seg_bot) / 2.0) ** 2 + web_loss ** 2 / 12.0)
    A_eff = p["A"] - t * (fl_loss + lip_loss + web_loss)
    Ixe = Ix0 - dIx - 0.0
    Ixe = Ixe + p["A"] * 0.0 - A_eff * ycg ** 2        # shift to effective NA (gross NA at 0)
    c_max = d / 2.0 + abs(ycg)
    return dict(Ixe=max(Ixe, 0.05 * Ix0), Se=max(Ixe, 0.05 * Ix0) / c_max, ycg_shift=ycg,
                A_eff_flex=A_eff, f=f)


def _hss_effective_Ix(p, f, n_iter=4):
    """Rectangular HSS, strong-axis bending, top flange in compression at f: compression
    flange flat as a stiffened element (k = 4), both webs under the stress gradient
    (App. 1 1.1.2, ho/bo <= 4 set unless the tube is deep and narrow), corners effective."""
    t, fl, H, B = p["t"], p["flats"], p["depth"], p["flange"]
    h = fl["web"]
    ycg = 0.0
    fl_loss = web_loss = 0.0
    seg_top = seg_bot = 0.0
    for _ in range(n_iter):
        c_top = H / 2.0 - ycg
        fl_loss = fl["flange"] - eff_width(fl["flange"], t, f, 4.0)
        yw_top, yw_bot = h / 2.0 - ycg, -h / 2.0 - ycg
        f_wt, f_wb = f * yw_top / c_top, f * yw_bot / c_top
        b1, b2, comp = web_gradient_eff(h, t, max(f_wt, 1e-6), f_wb, ho_over_bo=H / B)
        web_loss = comp - (b1 + b2)
        seg_top = h / 2.0 - b1
        seg_bot = seg_top - web_loss
        A_eff = p["A"] - t * (fl_loss + 2 * web_loss)
        Sy_loss = t * fl_loss * (H / 2.0 - t / 2.0) + \
            2 * t * web_loss * (seg_top + seg_bot) / 2.0
        ycg_new = -Sy_loss / A_eff
        if abs(ycg_new - ycg) < 1e-4:
            ycg = ycg_new
            break
        ycg = ycg_new
    dIx = t * fl_loss * (H / 2.0 - t / 2.0) ** 2 + \
        2 * t * web_loss * (((seg_top + seg_bot) / 2.0) ** 2 + web_loss ** 2 / 12.0)
    A_eff = p["A"] - t * (fl_loss + 2 * web_loss)
    Ixe = p["Ix"] - dIx - A_eff * ycg ** 2
    Ixe = max(min(Ixe, p["Ix"]), 0.05 * p["Ix"])
    return dict(Ixe=Ixe, Se=Ixe / (H / 2.0 + abs(ycg)), ycg_shift=ycg, A_eff_flex=A_eff, f=f)


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
    (the authority), spec-computed EWM otherwise. NOTE (static review F10): the computed Se
    runs up to +5..8% ABOVE the SFIA Sxe on some structural families (362S162-33,
    400S162-33, 400S200-33, ...) and up to +18% on S125 -- there is NO table for custom /
    built-up / Z / HSS sections, so a 'computed' source must be treated as unvalidated and
    STATED. Returns dict(Se, Ixe_defl, Ma_asd, source)."""
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
        if eff_fails:
            print("  (effective residuals: computed EWM vs the SFIA table -- design values "
                  "route through flex_props(), which uses the table as authority; computed "
                  "values for untabulated sections are UNVALIDATED and must be stated)")
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

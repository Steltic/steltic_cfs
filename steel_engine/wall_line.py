"""
wall_line.py -- per-wall-line TRIBUTARY VALIDATOR (scope decision #2).

Independent of OpenSees: distributes story shears to wall lines by tributary area under the
FLEXIBLE-diaphragm idealization (the CFS light-frame default, ASCE 7-22 12.3.1.1), computes
per-line unit shears, stacks cumulative chord/hold-down tension story-by-story, and evaluates
the AISI S400-20 wall deflection (Eq. E1.4.1.4-1 wood structural panels, Eq. E2.4.1.4-1 steel
sheet; E3.4.4 / E6.4.1.4 "principles of mechanics" for strap-braced and other walls). In the
pipeline this runs alongside the OpenSees model as a GATE: wall-line shears from the two must
agree within tolerance, and divergence requires agent justification (open fronts, offsets,
mixed diaphragms).

NO capacities: unit shear DEMANDS and tension DEMANDS only. Sheathing/fastener selection,
hold-down selection, and every strength check remain agent/RAG work.

ALL OUTPUTS HERE ARE PURE ELF: no redundancy rho, no Omega_0. The seed-assembly layer
(cfs_pipeline) applies rho to strength-design seeds and Omega_0_eff to the capacity-design
seeds (via overturning_stack(shear_scale=...)); drift stays unamplified per 12.3.4.1.

PER-STORY LINE PRESENCE (CFS-09): a line is PRESENT at a story when it has sheathed length
there. distribute() resolves each story's shear onto the lines present at that story only: a
line that stops (breezeway, split level, discontinued wall) hands its shear from above to the
neighbouring present lines through the diaphragm at that level (flexible diaphragm = simple
span -> lever rule), recorded as a TRANSFER (ASCE 7-22 12.3.3.4 / Table 12.3-2 Type 4: the
elements supporting a discontinuous wall are designed for Omega_0). A line whose lower stories
are absent because it bears on a stepped foundation declares WallLine(base_story=k) and its
shear goes to that foundation instead. No line ever divides by a zero wall length.

Hold-down hardware classes (scope decision #7 -- hybrid class-envelope): capacity bands and
stiffness for DISCRETE devices are representative in-house values ("EOR substitutes a specific
product"); continuous RODS are computed (PL/AE + take-up allowance).

Geometry in FEET here (validator-facing, matches brief language); forces kips; stress ksi.
(The S400 deflection equations are evaluated internally in lb / in / psi, as printed.)
"""
import math

E_KSI = 29500.0
G_STEEL_KSI = 11300.0     # S400 E2.4.1.4: G of the steel sheet sheathing (= steel G)

# device class -> (max factored tension kip, stiffness kip/in) -- representative envelope bands
HOLDDOWN_BANDS = {
    "strap":  (5.0, 20.0),
    "bolted": (20.0, 50.0),
    # "rod" is computed, not banded
}
ROD_TAKEUP_IN = 0.05      # per-level take-up device travel allowance (in), stated assumption

# ---------------------------------------------------------------------------------------------
# AISI S400-20 deflection coefficients (US customary: v lb/in, beta lb/in^1.5, G psi, t in.)
#   E1.4.1.4 (wood structural panels): beta = 67.5 plywood other than CSP, 55 OSB and CSP;
#                                      rho  = 1.85 plywood other than CSP, 1.05 OSB and CSP;
#                                      omega4 = 1.
#   E2.4.1.4 (steel sheet):  beta = 29.12 (t_sh/0.018)  (Eq. E2.4.1.4-3a)
#                            rho  = 0.075 (t_sh/0.018)  (Eq. E2.4.1.4-4a)
#                            omega4 = sqrt(33/Fy), Fy in ksi;  G = 11,300 ksi (steel).
#   both: omega1 = s/6, omega2 = 0.033/t_stud, omega3 = sqrt((h/b)/2), with t_stud the stud
#   DESIGNATION thickness (S400 A2.1: minimum base steel thickness in mils -> 33 mil = 0.033
#   in., 43 = 0.043, 54 = 0.054, 68 = 0.068, 97 = 0.097), NOT the design thickness (0.0346,
#   0.0451, 0.0566 ...) -- the design thickness makes omega2, and so the shear and slip terms,
#   ~5% low (a note is added when t_stud_in equals a design thickness).
# G for wood structural panels is not tabulated in S400; Commentary C-E1.4.1.4 approximates
# G*t from the panel through-thickness shear rigidity: for 7/16-in. 24/16 OSB
# C_G*G_v*t_v = 3.1 x 25,000 = 77,500 lb/in (G = 177,300 psi at t = 0.437 in.). That example
# value is the default G*t when the agent supplies neither G_psi nor Gt_lb_in (stated loudly in
# the result's notes) -- supply the selected panel's value (SDPWS / APA Gv*tv x C_G).
# ---------------------------------------------------------------------------------------------
WSP_SHEATHING = {
    "plywood": dict(beta=67.5, rho=1.85, label="plywood (other than CSP)"),
    "osb":     dict(beta=55.0, rho=1.05, label="OSB"),
    "csp":     dict(beta=55.0, rho=1.05, label="Canadian softwood plywood"),
}
WSP_GT_DEFAULT_LB_IN = 77500.0          # C-E1.4.1.4 worked example (7/16 OSB 24/16)

# Conservative ASSUMED schedule used ONLY when a WSP / steel-sheet line gives no S400 schedule
# (legacy wall_props): widest tabulated edge spacing, thinnest tabulated stud, OSB (lower beta
# and rho than plywood), thinnest tabulated sheet, one sheathed face. Every use is reported.
_ASSUMED_WSP = dict(sheathing="osb", s_in=6.0, t_stud_in=0.033, t_sheathing_in=0.4375, faces=1)
_ASSUMED_STEEL = dict(sheathing="steel_sheet", s_in=6.0, t_stud_in=0.033, t_sheathing_in=0.018,
                      Fy_ksi=33.0, faces=1)

# CFS DESIGN thickness (in.) -> DESIGNATION thickness (mils/1000) for 33..118 mil: a t_stud_in
# equal to a design thickness is flagged (S400 omega2 takes the designation thickness)
_DESIGN_T_STUD = {0.0346: 0.033, 0.0451: 0.043, 0.0566: 0.054, 0.0713: 0.068, 0.1017: 0.097,
                  0.1242: 0.118}

_STEEL_SHEET_KEYS = ("steel_sheet", "steel", "steelsheet")
_STRAP_KEYS = ("strap",)


class WallLine(object):
    """One lateral line in one direction: position (ft), and per-story wall segments.
    segments[story] = list of (length_ft, height_ft). A story with no segments (or an empty
    list) means the line is ABSENT at that story (breezeway, split level, roof step) -- see the
    module docstring. Type II adjustment factors and 2w/h aspect-ratio reductions are applied
    to CAPACITY by the agent; the validator reports the aspect ratio so the agent must respond.

    Optional kwargs:
      trib_scale  -- scales this line's tributary width (default 1.0). Use < 1.0 for a
                     PARTIAL-DEPTH line (e.g. a notch-back wall that only collects a local
                     bay of a much deeper diaphragm); forces renormalize over the scaled
                     widths, so the balance flows to the neighbouring full-depth lines.
                     State the justification in the package.
      wall_props  -- per-line deflection inputs (see s400_deflection); overrides
                     cfg['wall_props'] for this line. dict(by_story={k: {...}}) gives
                     per-story schedules (chord packs, rods, fastener spacing change by story).
      base_story  -- lowest story of a line that bears on a STEPPED FOUNDATION / stem wall
                     (split level). Stories below it are absent and its shear goes to that
                     foundation; without base_story an absent lower story is a DISCONTINUITY
                     and the shear is transferred to the neighbouring lines (12.3.3.4).
    COLLINEAR lines (same position) are supported: they share the position's tributary
    width, split per story in proportion to sheathed length."""
    def __init__(self, name, pos_ft, segments, trib_scale=1.0, wall_props=None, base_story=None):
        self.name, self.pos, self.segments = name, float(pos_ft), segments
        self.trib_scale = float(trib_scale)
        self.wall_props = wall_props
        self.base_story = None if base_story is None else int(base_story)

    def length(self, story):
        return sum(L for (L, h) in (self.segments.get(story) or []))

    def present(self, story):
        """True when the line has sheathed length at this story."""
        return self.length(story) > 1e-9

    def high_aspect(self, story):
        return [(L, h) for (L, h) in (self.segments.get(story) or []) if L > 0 and h / L > 2.0]


class NoResistingLineError(ValueError):
    """A story has no present wall line in a direction -- the lateral system is incomplete."""


def _position_groups(lines):
    """Group lines by (rounded) position along the axis. Collinear lines previously broke
    the width computation (zero spans / misassigned edge strips)."""
    gs, order = {}, []
    for ln in sorted(lines, key=lambda l: l.pos):
        key = round(float(ln.pos), 6)
        if key not in gs:
            gs[key] = []
            order.append(key)
        gs[key].append(ln)
    return order, gs


def _group_widths(order, gs, dim_ft, shift, lo=0.0):
    """(width, width_shifted) per unique position; single-line groups carry the line's
    trib_scale (partial-depth lines). lo/dim_ft = the diaphragm extent along the axis."""
    gw = {}
    hi = lo + float(dim_ft)
    for i, x in enumerate(order):
        left = (x - order[i - 1]) if i > 0 else 0.0
        right = (order[i + 1] - x) if i < len(order) - 1 else 0.0
        edge_l = max(x - lo, 0.0) if i == 0 else 0.0        # cantilever edge strips
        edge_r = max(hi - x, 0.0) if i == len(order) - 1 else 0.0
        w = left / 2 + right / 2 + edge_l + edge_r
        w_sh = (left / 2) * (1 + shift) + (right / 2) * (1 + shift) + edge_l + edge_r
        sc = gs[x][0].trib_scale if len(gs[x]) == 1 else 1.0
        gw[x] = (w * sc, w_sh * sc)
    return gw


def tributary_shares(lines, dim_ft, shift=0.05):
    """Tributary width per line from line positions across a diaphragm of dimension dim_ft.
    Returns {name: (width, width_shifted)} where width_shifted applies the 5% eccentricity
    equivalent (each interior boundary moved 5% of its span toward the line -- conservative
    per-line envelope; flexible diaphragms take accidental eccentricity as a tributary shift,
    not rigid-body torsion). COLLINEAR lines split the position's width equally here;
    distribute() refines that split per story by sheathed length."""
    order, gs = _position_groups(lines)
    gw = _group_widths(order, gs, dim_ft, shift)
    out = {}
    for x in order:
        grp = gs[x]
        for ln in grp:
            out[ln.name] = (gw[x][0] / len(grp), gw[x][1] / len(grp))
    return out


def fit_positions(true_areas, dim_ft, on_infeasible="raise"):
    """IRREGULAR-PLAN HELPER (T/U/L plans): positions on a UNIFORM 1-D strip whose
    tributary widths reproduce the given per-line TRUE tributary areas (compute those by
    hand from the actual variable-depth plan, in axis order, first line at the 0 edge).

    The midpoint-tributary equations telescope into two decoupled chains (evens follow
    p0, odds follow p1 = 2*w0 - p0) with ONE free DOF, p0; the closure equation is
    independent of p0, so EVERY p0 reproduces the target widths exactly -- p0 only decides
    whether the positions are ordered.

    Returned positions are STRICTLY increasing (CFS-08: a zero gap between two lines makes
    distribute() merge them into one collinear group and re-split the width by wall length,
    which silently destroys the fitted tributaries -- [100,100,100,100] on 40 ft used to
    return [0, 20, 20, 40]). Edge lines may sit on the strip edges (edge gap 0 is fine).
      1. p0 = 0 (legacy) when that already gives strictly increasing in-range positions;
      2. otherwise p0 by maximin search of the smallest gap (concave -> ternary search);
      3. if no p0 can order the lines (target spread too large for a uniform strip) the
         call RAISES ValueError (on_infeasible="raise", the default) -- use natural
         positions with trib_scales_for(...) instead. on_infeasible="approx" returns
         strip-center positions with APPROXIMATE tributaries and a printed warning (legacy
         behaviour, for reproducing old jobs only).
    Pair with cfg['area_sf'] / cfg['perimeter_ft'] and STATE the fit table in the package."""
    tot = float(sum(true_areas))
    if tot <= 0 or any(a <= 0 for a in true_areas):
        raise ValueError("fit_positions: every true tributary area must be > 0 (got %r)"
                         % (list(true_areas),))
    dim = float(dim_ft)
    widths = [a / tot * dim for a in true_areas]
    n = len(widths)
    if n == 1:
        return [dim / 2.0]
    tol = 1e-6 * max(dim, 1.0)

    def chain(p0):
        p = [p0, 2.0 * widths[0] - p0]
        for i in range(1, n - 1):
            p.append(p[i - 1] + 2.0 * widths[i])
        return p

    def gaps(p):
        inner = [p[i + 1] - p[i] for i in range(n - 1)]
        return inner, [p[0] - 0.0, dim - p[-1]]

    def ok(p):
        inner, edges = gaps(p)
        return min(inner) > tol and min(edges) >= -1e-9

    def score(p):
        # maximin objective: interior gaps and edge clearances. Edge clearances are allowed
        # to be 0, so they only bind when negative (out of range).
        inner, edges = gaps(p)
        return min(min(inner), 2.0 * min(edges))

    legacy = chain(0.0)
    if ok(legacy):
        return legacy                       # legacy-exact path (regression-safe)

    lo, hi = -dim, 2.0 * dim
    for _ in range(200):
        m1 = lo + (hi - lo) / 3.0
        m2 = hi - (hi - lo) / 3.0
        if score(chain(m1)) < score(chain(m2)):
            lo = m1
        else:
            hi = m2
    best = chain((lo + hi) / 2.0)
    if ok(best):
        return best                         # exact tribs, strictly ordered

    msg = ("wall_line.fit_positions: the target tributary widths %s cannot be ordered on a "
           "uniform %.1f-ft strip with non-zero gaps (spread too large). Place the lines at "
           "their NATURAL positions and set WallLine(trib_scale=...) from "
           "wall_line.trib_scales_for(true_areas, positions, dim) instead, and state the "
           "substitution in the package." % ([round(w, 2) for w in widths], dim))
    if on_infeasible != "approx":
        raise ValueError(msg)
    print("WARNING " + msg + " -- returning strip-center positions with APPROXIMATE "
          "tributaries (on_infeasible='approx').")
    b, out = 0.0, []
    for w in widths:
        out.append(b + w / 2.0)
        b += w
    return out


def trib_scales_for(true_areas, positions_ft, dim_ft):
    """trib_scale per line so that lines at their NATURAL positions reproduce the TRUE
    tributary areas exactly (the alternative to fit_positions for plans whose spread cannot
    be ordered on a uniform strip). Positions must be strictly increasing (collinear lines:
    pass one combined area per position). Returns a list of trib_scale values."""
    pos = [float(p) for p in positions_ft]
    if any(b - a <= 0 for a, b in zip(pos, pos[1:])):
        raise ValueError("trib_scales_for: positions must be strictly increasing (one entry "
                         "per position; split collinear lines yourself)")
    tot = float(sum(true_areas))
    target = [a / tot * float(dim_ft) for a in true_areas]
    nat = []
    for i, x in enumerate(pos):
        l = (x - pos[i - 1]) / 2.0 if i else x
        r = (pos[i + 1] - x) / 2.0 if i < len(pos) - 1 else float(dim_ft) - x
        nat.append(l + r)
    if any(w <= 0 for w in nat):
        raise ValueError("trib_scales_for: a natural tributary width is zero")
    return [t / w for t, w in zip(target, nat)]


def _level_shares(lines, story, dim_ft, shift, extent=None):
    """Tributary fractions of the level force among the lines PRESENT at `story`
    (and inside the level's diaphragm extent, if given). Returns {name: (frac, frac_sh)};
    fracs sum to 1 (shifted fracs are the per-line 5% envelope, capped at 1)."""
    lo, hi = (0.0, float(dim_ft)) if extent is None else (float(extent[0]), float(extent[1]))
    act = [ln for ln in lines if ln.present(story) and lo - 1e-6 <= ln.pos <= hi + 1e-6]
    if not act:
        return None
    order, gs = _position_groups(act)
    gw = _group_widths(order, gs, hi - lo, shift, lo=lo)
    tot_w = sum(w for (w, _s) in gw.values())
    if tot_w <= 0:
        return None
    out = {}
    for x in order:
        grp = gs[x]
        Ls = [ln.length(story) for ln in grp]
        totL = sum(Ls)
        for ln, Lw in zip(grp, Ls):
            fr = Lw / totL
            out[ln.name] = (gw[x][0] / tot_w * fr, min(gw[x][1] / tot_w, 1.0) * fr)
    return out


def _lever_targets(lines, story, x_d):
    """Flexible-diaphragm (simple-span) transfer of a concentrated shear at position x_d to
    the lines present at `story`: lever rule between the nearest present positions on either
    side; all to the nearest side when x_d is outside them (cantilever diaphragm, flagged).
    Returns ({name: fraction}, note)."""
    act = [ln for ln in lines if ln.present(story)]
    order, gs = _position_groups(act)
    xd = round(float(x_d), 6)

    def grp_split(x, f):
        grp = gs[x]
        Ls = [ln.length(story) for ln in grp]
        return {ln.name: f * L / sum(Ls) for ln, L in zip(grp, Ls)}
    if xd in gs:
        return grp_split(xd, 1.0), "collinear present line"
    left = [x for x in order if x < xd]
    right = [x for x in order if x > xd]
    if left and right:
        xl, xr = left[-1], right[0]
        fl = (xr - xd) / (xr - xl)
        out = grp_split(xl, fl)
        for k, v in grp_split(xr, 1.0 - fl).items():
            out[k] = out.get(k, 0.0) + v
        return out, "lever rule between x=%.2f and x=%.2f" % (xl, xr)
    x = left[-1] if left else right[0]
    return grp_split(x, 1.0), ("CANTILEVER diaphragm transfer to x=%.2f (no present line on "
                               "the far side) -- verify the diaphragm/collector" % x)


def presence_issues(lines, stories, direction="", fatal_only=False):
    """Lint for per-story line presence (CFS-09): every story needs at least one present
    line in each direction (FATAL: cfs_engine.run raises); segment lengths must be positive
    (zero-length entries are skipped by the solver but flagged here); base_story must be a
    story where the line is present. Returns a list of issue strings ([] = ok)."""
    out = []
    lab = ("%s-direction " % direction) if direction else ""
    for k in stories:
        if not any(ln.present(k) for ln in lines):
            out.append("story %s has NO %sresisting wall line (no sheathed length on any line) "
                       "-- the lateral system is incomplete; add walls or a frame at this story"
                       % (k, lab))
    if fatal_only:
        return out
    for ln in lines:
        for k, segs in (ln.segments or {}).items():
            for seg in (segs or []):
                if not (seg[0] > 0 and seg[1] > 0):
                    out.append("line %s story %s: segment %r has a non-positive length/height "
                               "-- an absent story is an EMPTY list, not a zero-length segment"
                               % (ln.name, k, tuple(seg)))
        if ln.base_story is not None and not ln.present(ln.base_story):
            out.append("line %s: base_story=%s but the line has no walls at that story"
                       % (ln.name, ln.base_story))
    return out


def distribute(story_forces, lines, dim_ft, shift=0.05, extent_by_story=None):
    """story_forces = {story: F_kip} (the LEVEL force at the top of each story) for ONE
    direction; lines = [WallLine] resisting it. Returns
      {story: {line: dict(V, V_shifted, V_story, V_story_shifted, F_level, F_level_shifted,
                          wall_len_ft, v_unit_plf, aspect_flags, present, transfer_in,
                          transfer_out_kip, founded_kip)}}.
    V / V_shifted are the per-story INCREMENTS whose top-down running sum is the line's story
    shear (the encoding every consumer uses: sum_{j>=k} V_j = V_story_k), so a line absent at a
    story has V_story = 0 there (its increment cancels the shear from above).

    Mechanics (flexible diaphragm): the level-k force goes to the lines PRESENT at story k by
    tributary width (collinear lines split by sheathed length; trib_scale-reduced widths
    renormalize). Each line's story shear from story k+1 continues down where the line is
    present at story k; where it is absent the diaphragm at level k transfers it to the
    present neighbours by the lever rule (transfer_in / transfer_out_kip; 12.3.3.4 Omega_0
    applies to the supporting elements); where the line declares base_story=k+1 the shear
    goes to its stepped foundation instead (founded_kip). v_unit_plf = V_story_shifted / L
    (0 where absent). extent_by_story={story: (lo_ft, hi_ft)} limits a level's diaphragm to
    part of the strip (split levels / roof steps: e.g. the lower roof of a step).
    Raises NoResistingLineError when a story has no present line. Equilibrium is asserted."""
    extent_by_story = extent_by_story or {}
    stories = sorted(story_forces, reverse=True)
    S, S_sh = {}, {}
    meta = {}
    above = None
    for k in stories:
        F = float(story_forces[k])
        sh = _level_shares(lines, k, dim_ft, shift, extent_by_story.get(k))
        if sh is None:
            if extent_by_story.get(k) is not None and any(ln.present(k) for ln in lines):
                raise NoResistingLineError(
                    "story %s: no present wall line inside the level-%s diaphragm extent %r"
                    % (k, k, extent_by_story.get(k)))
            raise NoResistingLineError(
                "story %s: no wall line in this direction has sheathed length -- every story "
                "needs a resisting line (CFS-09 lint)" % k)
        Sk = {ln.name: 0.0 for ln in lines}
        Sk_sh = {ln.name: 0.0 for ln in lines}
        mk = {ln.name: dict(F_level=0.0, F_level_shifted=0.0, transfer_in={},
                            transfer_out_kip=0.0, founded_kip=0.0) for ln in lines}
        for nm, (f, fs) in sh.items():
            Sk[nm] += F * f
            Sk_sh[nm] += F * fs
            mk[nm]["F_level"] = F * f
            mk[nm]["F_level_shifted"] = F * fs
        founded = 0.0
        if above is not None:
            Sa, Sa_sh = above
            for ln in lines:
                Va, Va_sh = Sa[ln.name], Sa_sh[ln.name]
                if abs(Va) < 1e-12 and abs(Va_sh) < 1e-12:
                    continue
                if ln.present(k):
                    Sk[ln.name] += Va
                    Sk_sh[ln.name] += Va_sh
                elif ln.base_story is not None and k < ln.base_story:
                    founded += Va
                    mk[ln.name]["founded_kip"] = Va
                    mk[ln.name]["founded_shifted_kip"] = Va_sh
                else:
                    tg, note = _lever_targets(lines, k, ln.pos)
                    mk[ln.name]["transfer_out_kip"] = Va
                    mk[ln.name]["transfer_out_shifted_kip"] = Va_sh
                    mk[ln.name]["transfer_note"] = note
                    for nm, fr in tg.items():
                        Sk[nm] += Va * fr
                        Sk_sh[nm] += Va_sh * fr
                        mk[nm]["transfer_in"][ln.name] = Va * fr
        S[k], S_sh[k] = Sk, Sk_sh
        meta[k] = (mk, founded)
        above = (Sk, Sk_sh)
    out = {}
    prev = None
    for k in stories:
        mk, founded = meta[k]
        row = {}
        for ln in lines:
            nm = ln.name
            Lw = ln.length(k)
            pres = ln.present(k)
            Vs, Vs_sh = S[k][nm], S_sh[k][nm]
            if not pres:
                Vs, Vs_sh = 0.0, 0.0
            Va = prev[0][nm] if prev else 0.0
            Va_sh = prev[1][nm] if prev else 0.0
            r = dict(V=Vs - Va, V_shifted=Vs_sh - Va_sh, V_story=Vs, V_story_shifted=Vs_sh,
                     wall_len_ft=Lw, v_unit_plf=(Vs_sh * 1000.0 / Lw) if pres else 0.0,
                     aspect_flags=ln.high_aspect(k), present=pres)
            r.update(mk[nm])
            row[nm] = r
        F = float(story_forces[k])
        s = sum(r["V"] for r in row.values()) + founded
        assert abs(s - F) < 1e-6 * max(abs(F), 1.0), "tributary equilibrium broke"
        out[k] = row
        prev = (S[k], S_sh[k])
    return out


def overturning_stack(line, dist, story_heights_ft, dead_kip_per_story=None, dead_factor=0.9,
                      shear_scale=1.0):
    """Cumulative chord/hold-down TENSION demand at each story of one line, stacking from the
    top down (the CFS bookkeeping the rubric scores): at story k,
        T_k = sum_{j>=k} V_j * h_j / L_k  -  dead_factor * P_dead_above / 2
    per wall-line (single-segment idealization; the agent refines per segment). Returns
    {story: dict(T_kip, V_cum, M_ot_kipft, M_top_kipft, wall_len_ft)} for the stories where
    the line is PRESENT only (an absent story has no chord; the stack restarts below a
    discontinuity, where the overturning moment at the base of the wall above
    -- 'discontinuous_base_M_kipft' on the lowest present story above it -- is delivered to
    the supporting transfer elements, designed for Omega_0 per 12.3.3.4).

    PURE ELF by default: with shear_scale=1.0 (the default) the outputs carry NO rho and NO
    Omega_0 -- those multipliers are applied at the SEED-ASSEMBLY layer (cfs_pipeline), so
    the drift and capacity-design paths stay clean. shear_scale is the capacity-design hook:
    pass Omega_0_eff to stack the SAME mechanics at the overstrength-level story shears
    (Ve_cap = Omega_0_eff * V_ELF) for the T_cd seed."""
    stories = sorted(dist.keys(), reverse=True)
    out = {}
    M_cum = 0.0
    V_run = 0.0
    P_dead = 0.0
    last_present = None
    for k in stories:
        V = dist[k][line.name]["V_shifted"] * shear_scale
        h = story_heights_ft[k]
        # overturning accumulates as CUMULATIVE story shear x story height (identically
        # sum of story forces x their lever arms). The pre-2026-07-31 form used the
        # story FORCE x its own story height, understating multistory hold-down tension
        # ~3x -- fixed (BUG; see test_solutions_gold/ISSUES.md).
        V_run += V
        if not line.present(k):
            if last_present is not None and M_cum > 0:
                out[last_present]["discontinuous_base_M_kipft"] = M_cum
            M_cum, V_run, P_dead, last_present = 0.0, 0.0, 0.0, None
            continue
        M_top = M_cum
        M_cum += V_run * h
        if dead_kip_per_story:
            P_dead += dead_kip_per_story.get(k, 0.0)
        L = line.length(k)
        T = M_cum / L - dead_factor * P_dead / 2.0
        out[k] = dict(T_kip=max(T, 0.0), V_cum=V_run, M_ot_kipft=M_cum, M_top_kipft=M_top,
                      wall_len_ft=L)
        last_present = k
    return out


def pick_holddown(T_kip):
    """Class-envelope device selection (decision #7): returns (device, k_kip_in, feasibility_note).
    Beyond the bolted band the design MUST switch to a continuous rod (computed)."""
    for dev, (cap, k) in (("strap", HOLDDOWN_BANDS["strap"]), ("bolted", HOLDDOWN_BANDS["bolted"])):
        if T_kip <= cap:
            return dev, k, None
    return "rod", None, ("cumulative tension %.1f kip exceeds the discrete hold-down envelope "
                         "(~%.0f kip) -- continuous rod system REQUIRED" %
                         (T_kip, HOLDDOWN_BANDS["bolted"][0]))


def rod_elongation(T_kip, rod_area_in2, length_in, takeup_levels=1):
    """Continuous rod stretch: PL/AE + take-up allowance (computed path, no proprietary data)."""
    return T_kip * length_in / (rod_area_in2 * E_KSI) + ROD_TAKEUP_IN * takeup_levels


# ---------------------------------------------------------------------------------------------
# Wall deflection (CFS-04) and the rotation carried up the stack (CFS-01)
# ---------------------------------------------------------------------------------------------

def wall_family(props, system=None):
    """'wsp' | 'steel_sheet' | 'strap' | 'mechanics' from the props' declared sheathing,
    else from the cfg system key."""
    sh = str((props or {}).get("sheathing") or "").lower()
    if sh in WSP_SHEATHING:
        return "wsp"
    if sh in _STEEL_SHEET_KEYS:
        return "steel_sheet"
    if sh in _STRAP_KEYS or (props or {}).get("strap_area_in2"):
        return "strap"
    if sh:
        return "mechanics"
    return {"wsp_shearwall": "wsp", "steelsheet_wall": "steel_sheet",
            "strap_braced": "strap"}.get(str(system or ""), "mechanics")


def anchorage_delta_v(props, T_kip, h_ft):
    """delta_v of S400 E1.4.1.4/E2.4.1.4 -- vertical deformation of the anchorage/attachment
    details at the base of the story (in), and a basis string:
      rod_area_in2 (continuous rod run through this story): T*h/(E*A) + takeup_in
          (default ROD_TAKEUP_IN per level);
      k_anchor_kip_in (discrete hold-down device): T/k + takeup_in (default 0);
      delta_v_extra_in: any further seating/bearing deformation, added as given.
    No anchorage input -> 0 with an explicit UNCONSERVATIVE note."""
    p = props or {}
    T = max(float(T_kip or 0.0), 0.0)
    extra = float(p.get("delta_v_extra_in", 0.0) or 0.0)
    if p.get("rod_area_in2"):
        tu = float(p.get("takeup_in", ROD_TAKEUP_IN))
        d = T * h_ft * 12.0 / (E_KSI * float(p["rod_area_in2"])) + tu + extra
        return d, "rod T*h/(E*A_rod) + take-up %.3f in" % tu
    if p.get("k_anchor_kip_in"):
        tu = float(p.get("takeup_in", 0.0))
        return T / float(p["k_anchor_kip_in"]) + tu + extra, "device T/k_anchor"
    return extra, "NO anchorage stiffness supplied -- delta_v = 0 (UNCONSERVATIVE)"


def wall_story_response(v_plf, h_ft, b_ft, props, T_kip=0.0, m_top_kip=0.0, system=None):
    """One wall segment (length b, height h) in one story of a stacked line at unit shear
    v (plf). Returns the S400 deflection terms (in), the overturning term from the stories
    above, the rotation increment this story passes to the stories above, and the basis.

      bending   = 2 v h^3 / (3 E Ac b)          (strap bay: 2 v h^3/(E Ac b), constant chord force)
      shear     = omega1 omega2 v_s h / (rho G t_sheathing)
      slip      = omega1^(5/4) omega2 omega3 omega4 (v_s / beta)^2
      anchorage = (h / b) delta_v
         -> S400-20 Eq. E1.4.1.4-1 (WSP) / Eq. E2.4.1.4-1 (steel sheet), v_s = v / faces
            (each sheathed face carries its share of the unit shear).
      strap     = v Ld^3 / (E A_strap b)        (E3.4.4 principles of mechanics: tension-only
                                                 diagonal(s) of total area A_strap, Ld = sqrt(b^2+h^2))
      ot_bending = chord strain from the moment m_top (= M_line,top / L_line, the chord force
                   delivered by the stories above): m h^2/(E Ac b) sheathed, 2 m h^2/(E Ac b)
                   strap -- not in the single-story S400 expression, required for a stack.
      dtheta    = rotation of the top of this story relative to its base passed rigidly to the
                  stories above: 2 (m_top h + v h^2/2)/(E Ac b) sheathed [2 (m_top + v h) h/(E Ac b)
                  strap] + delta_v / b  (ASCE 7-22 12.8.6.5: story drift is the difference of
                  the displacements at the top and bottom of the story, so the rigid-body
                  rotation from the stories below enters every story above).

    props (inch / ksi / psi units as named):
      chord_area_in2  Ac (required, gross chord area per end)
      sheathing       'osb' | 'plywood' | 'csp' | 'steel_sheet' | 'strap' | other ('mechanics')
      s_in, t_stud_in (stud DESIGNATION thickness, mils/1000: 0.033 / 0.043 / 0.054 ...),
      t_sheathing_in, faces (1|2), Fy_ksi (steel sheet), G_psi | Gt_lb_in (WSP)
      strap_area_in2  (strap) total area of the tension diagonal(s) of one bay
      Ga_kip_in       (mechanics: gypsum / other walls, E6.4.1.4) apparent shear rigidity,
                      shear = v h / Ga; legacy key Gp_kip_in is read as Ga.
      rod_area_in2 | k_anchor_kip_in [, takeup_in, delta_v_extra_in]  -> delta_v
    A WSP / steel-sheet wall without its schedule (legacy wall_props with Gp/en only) is
    evaluated with the conservative ASSUMED schedule (_ASSUMED_WSP / _ASSUMED_STEEL) and the
    result says so in 'assumed'. There is NO constant (load-independent) slip term."""
    p = dict(props or {})
    fam = wall_family(p, system)
    notes, assumed = [], []
    if not p.get("chord_area_in2"):
        raise ValueError("wall deflection needs chord_area_in2 (gross chord area Ac)")
    Ac = float(p["chord_area_in2"])
    E = E_KSI * 1000.0                              # psi
    h = float(h_ft) * 12.0
    b = float(b_ft) * 12.0
    if b <= 0 or h <= 0:
        raise ValueError("wall segment needs positive length and height (b=%r ft, h=%r ft)"
                         % (b_ft, h_ft))
    v = max(float(v_plf), 0.0) / 12.0               # lb/in
    m_top = max(float(m_top_kip or 0.0), 0.0) * 1000.0   # lb (chord force from above)
    dv, dv_basis = anchorage_delta_v(p, T_kip, h_ft)
    if "UNCONSERVATIVE" in dv_basis:
        notes.append(dv_basis)
    res = dict(family=fam, bending=0.0, shear=0.0, slip=0.0, anchorage=(h / b) * dv,
               strap=0.0, ot_bending=0.0, delta_v_in=dv, delta_v_basis=dv_basis)
    if fam in ("wsp", "steel_sheet"):
        if not (p.get("s_in") and p.get("t_stud_in")
                and (fam == "wsp" or p.get("t_sheathing_in"))):
            dflt = _ASSUMED_WSP if fam == "wsp" else _ASSUMED_STEEL
            for kk, vv in dflt.items():
                if kk not in p or p.get(kk) in (None, ""):
                    p[kk] = vv
                    assumed.append("%s=%s" % (kk, vv))
            if p.get("en_in") is not None:
                notes.append("legacy en_in ignored -- S400 slip term is (v/beta)^2-dependent")
        faces = max(int(p.get("faces", 1) or 1), 1)
        vs = v / faces
        s, tst = float(p["s_in"]), float(p["t_stud_in"])
        dsg = next((d for t_, d in _DESIGN_T_STUD.items() if abs(tst - t_) < 5e-5), None)
        if dsg is not None:
            notes.append("t_stud_in = %.4f in. is a DESIGN thickness; S400 E1.4.1.4/E2.4.1.4 "
                         "omega2 = 0.033/t_stud uses the stud DESIGNATION thickness (mils/1000: "
                         "%.3f here) -- omega2, the shear and the slip terms are %.0f%% low"
                         % (tst, dsg, 100.0 * (1.0 - dsg / tst)))
        w1, w2 = s / 6.0, 0.033 / tst
        w3 = math.sqrt((h / b) / 2.0)
        if fam == "wsp":
            sh = str(p.get("sheathing") or "osb").lower()
            co = WSP_SHEATHING.get(sh, WSP_SHEATHING["osb"])
            beta, rho, w4 = co["beta"], co["rho"], 1.0
            if p.get("Gt_lb_in"):
                Gt = float(p["Gt_lb_in"])
            elif p.get("G_psi") and p.get("t_sheathing_in"):
                Gt = float(p["G_psi"]) * float(p["t_sheathing_in"])
            else:
                Gt = WSP_GT_DEFAULT_LB_IN
                assumed.append("G*t=%.0f lb/in (C-E1.4.1.4 7/16-in. OSB example)" % Gt)
            eq = "AISI S400-20 Eq. E1.4.1.4-1"
        else:
            tsh = float(p["t_sheathing_in"])
            beta, rho = 29.12 * (tsh / 0.018), 0.075 * (tsh / 0.018)
            Fy = float(p.get("Fy_ksi") or 33.0)
            if not p.get("Fy_ksi"):
                assumed.append("Fy_ksi=33")
            w4 = math.sqrt(33.0 / Fy)
            G = float(p.get("G_psi") or G_STEEL_KSI * 1000.0)
            Gt = G * tsh
            eq = "AISI S400-20 Eq. E2.4.1.4-1 (beta Eq. E2.4.1.4-3a, rho Eq. E2.4.1.4-4a)"
        res["bending"] = 2.0 * v * h ** 3 / (3.0 * E * Ac * b)
        res["shear"] = w1 * w2 * vs * h / (rho * Gt)
        res["slip"] = w1 ** 1.25 * w2 * w3 * w4 * (vs / beta) ** 2
        res["ot_bending"] = m_top * h ** 2 / (E * Ac * b)
        dth = 2.0 * (m_top * h + v * h * h / 2.0) / (E * Ac * b) + dv / b
        res.update(omega=dict(w1=w1, w2=w2, w3=w3, w4=w4), beta=beta, rho=rho, Gt_lb_in=Gt,
                   faces=faces)
    elif fam == "strap" and p.get("strap_area_in2"):
        A = float(p["strap_area_in2"])
        Ld = math.hypot(b, h)
        res["strap"] = v * Ld ** 3 / (E * A * b)
        res["bending"] = 2.0 * v * h ** 3 / (E * Ac * b)
        res["ot_bending"] = 2.0 * m_top * h ** 2 / (E * Ac * b)
        dth = 2.0 * (m_top + v * h) * h / (E * Ac * b) + dv / b
        eq = ("AISI S400-20 E3.4.4 principles of mechanics: strap elongation v Ld^3/(E A b) + "
              "chord axial 2 v h^3/(E Ac b) + (h/b) delta_v")
    else:
        Ga = p.get("Ga_kip_in") or p.get("Gp_kip_in")
        if not Ga:
            raise ValueError("%s wall without an S400 schedule needs Ga_kip_in (apparent shear "
                             "rigidity, agent-grounded) -- or declare sheathing/strap inputs"
                             % fam)
        if fam == "strap":
            assumed.append("strap_area_in2 missing: strap stiffness taken from Gp/Ga")
        if p.get("en_in"):
            notes.append("legacy en_in ignored (no load-independent slip term)")
        res["bending"] = 2.0 * v * h ** 3 / (3.0 * E * Ac * b)
        res["shear"] = (v / 1000.0) * h / float(Ga)
        res["ot_bending"] = m_top * h ** 2 / (E * Ac * b)
        dth = 2.0 * (m_top * h + v * h * h / 2.0) / (E * Ac * b) + dv / b
        eq = ("principles of mechanics (AISI S400-20 E6.4.1.4 / E1.4.2.3): bending 2vh^3/(3EAcb) "
              "+ v h/Ga (Ga agent-grounded) + (h/b) delta_v")
    res["total"] = (res["bending"] + res["shear"] + res["slip"] + res["anchorage"] +
                    res["strap"] + res["ot_bending"])
    res["single_story_s400"] = res["total"] - res["ot_bending"]
    res["dtheta_rad"] = dth
    res["equation"] = eq
    res["assumed"] = assumed
    res["notes"] = notes
    return res


def s400_deflection(v_plf, h_ft, b_ft, props=None, *legacy, **kw):
    """S400-20 single-story wall deflection (in) at unit shear v_plf for a segment of height
    h_ft and length b_ft: Eq. E1.4.1.4-1 (wood structural panels) / Eq. E2.4.1.4-1 (steel
    sheet) -- 2vh^3/(3EAcb) + omega1 omega2 vh/(rho G t) + omega1^(5/4) omega2 omega3 omega4
    (v/beta)^2 + (h/b) delta_v -- or E3.4.4 / E6.4.1.4 mechanics for strap / other walls.
    See wall_story_response for the props schema. Keyword args: T_kip (anchorage tension ->
    delta_v), m_top_kip (chord force from the stories above), system (cfg system key).
    Returns dict(total, bending, shear, slip, anchorage, strap, ot_bending, delta_v_in,
    dtheta_rad, equation, assumed, notes).

    Legacy positional call s400_deflection(v, h, L, chord_area, Gp, en, k_anchor, T) is still
    accepted: it is mapped onto props and evaluated per the `system` keyword -- WSP / steel
    sheet systems use the S400 equation with the conservative ASSUMED schedule (reported in
    'assumed'); without a system it is the mechanics form with Gp as the shear rigidity. The old
    constant 0.75*en 'slip' was not an S400 term and is gone (en_in is ignored, noted)."""
    if not isinstance(props, dict):
        vals = (props,) + tuple(legacy)
        names = ("chord_area_in2", "Gp_kip_in", "en_in", "k_anchor_kip_in", "T_kip")
        pp = {}
        for nm, val in zip(names, vals):
            if nm == "T_kip":
                kw.setdefault("T_kip", val)
            else:
                pp[nm] = val
        props = pp
    return wall_story_response(v_plf, h_ft, b_ft, props, T_kip=kw.get("T_kip", 0.0),
                               m_top_kip=kw.get("m_top_kip", 0.0), system=kw.get("system"))


def compare_with_model(dist, model_line_shears, tol=0.10):
    """The GATE: OpenSees wall-line shears vs tributary. model_line_shears = {story: {line: V}}.
    Returns list of divergence flags the agent must justify."""
    flags = []
    for story, row in dist.items():
        for name, r in row.items():
            Vm = (model_line_shears.get(story) or {}).get(name)
            if Vm is None:
                flags.append("story %s line %s: model shear missing" % (story, name))
                continue
            Vt = r["V"]
            if Vt > 1e-6 and abs(Vm - Vt) / Vt > tol:
                flags.append("story %s line %s: model %.1f kip vs tributary %.1f kip "
                             "(%.0f%%) -- justify (open front / offset / mixed diaphragm?)"
                             % (story, name, Vm, Vt, abs(Vm - Vt) / Vt * 100))
    return flags


def _selftest():
    print("wall_line self-test")
    # 4-story, 100-ft x 40-ft, three N-S lines at x = 0, 50, 100 resisting E-W force
    segs = {k: [(20.0, 9.5), (10.0, 9.5)] for k in (1, 2, 3, 4)}
    segsB = {k: [(15.0, 9.5)] for k in (1, 2, 3, 4)}
    lines = [WallLine("L1", 0.0, segs), WallLine("L2", 50.0, segsB), WallLine("L3", 100.0, segs)]
    F = {1: 18.0, 2: 26.0, 3: 20.0, 4: 12.0}
    dist = distribute(F, lines, 100.0)
    sh = tributary_shares(lines, 100.0)
    assert abs(sh["L2"][0] - 50.0) < 1e-9 and abs(sh["L1"][0] - 25.0) < 1e-9
    assert abs(sum(r["V"] for r in dist[2].values()) - 26.0) < 1e-9
    v2 = dist[2]["L2"]["v_unit_plf"]
    print("  L2 story-2: V=%.1f kip (shifted %.1f), story shear %.1f kip, v=%.0f plf" %
          (dist[2]["L2"]["V"], dist[2]["L2"]["V_shifted"], dist[2]["L2"]["V_story_shifted"], v2))
    ot = overturning_stack(lines[1], dist, {1: 10.0, 2: 9.5, 3: 9.5, 4: 9.5},
                           dead_kip_per_story={k: 6.0 for k in F})
    assert ot[1]["T_kip"] > ot[3]["T_kip"] >= 0.0, "tension must grow downward"
    # capacity-design scale hook: same stacking mechanics, scaled shears; ELF path unchanged
    ot_cd = overturning_stack(lines[1], dist, {1: 10.0, 2: 9.5, 3: 9.5, 4: 9.5},
                              shear_scale=2.5)
    ot_e = overturning_stack(lines[1], dist, {1: 10.0, 2: 9.5, 3: 9.5, 4: 9.5})
    assert abs(ot_cd[1]["T_kip"] - 2.5 * ot_e[1]["T_kip"]) < 1e-9
    assert abs(ot_cd[1]["V_cum"] - 2.5 * ot_e[1]["V_cum"]) < 1e-9
    dev1, k1, note1 = pick_holddown(ot[4]["T_kip"])
    dev0, k0, note0 = pick_holddown(ot[1]["T_kip"])
    print("  cumulative T: story4=%.1f kip -> %s; story1=%.1f kip -> %s%s"
          % (ot[4]["T_kip"], dev1, ot[1]["T_kip"], dev0,
             " (%s)" % note0[:40] if note0 else ""))
    if dev0 == "rod":
        dr = rod_elongation(ot[1]["T_kip"], 1.05, 4 * 115.0, takeup_levels=4)
        print("  rod elongation over height: %.2f in" % dr)
    props = dict(sheathing="osb", s_in=4.0, t_stud_in=0.043, t_sheathing_in=0.4375,
                 Gt_lb_in=77500.0, chord_area_in2=1.2, k_anchor_kip_in=50.0)
    d = s400_deflection(v2, 9.5, 15.0, props, T_kip=ot[2]["T_kip"])
    assert d["total"] > 0 and not d["assumed"]
    d2 = s400_deflection(2 * v2, 9.5, 15.0, props, T_kip=ot[2]["T_kip"])
    assert abs(d2["slip"] / d["slip"] - 4.0) < 1e-9, "S400 slip term must scale with v^2"
    print("  S400 E1.4.1.4-1 @L2 story2: %.3f in (b=%.3f s=%.3f slip=%.3f a=%.3f)"
          % (d["total"], d["bending"], d["shear"], d["slip"], d["anchorage"]))
    flags = compare_with_model(dist, {k: {ln.name: dist[k][ln.name]["V"] for ln in lines}
                                      for k in F})
    assert flags == []
    bad = compare_with_model(dist, {1: {"L1": dist[1]["L1"]["V"] * 1.5, "L2": dist[1]["L2"]["V"],
                                        "L3": dist[1]["L3"]["V"]}})
    assert any("justify" in f for f in bad)
    print("  gate: clean model passes; 50%% divergence flags correctly")
    # breezeway: L2 absent at story 1 -> its story-2 shear transfers to L1/L3 (lever rule)
    segsC = {1: [], 2: [(15.0, 9.5)], 3: [(15.0, 9.5)], 4: [(15.0, 9.5)]}
    lines_b = [WallLine("L1", 0.0, segs), WallLine("L2", 50.0, segsC),
               WallLine("L3", 100.0, segs)]
    db = distribute(F, lines_b, 100.0)
    assert db[1]["L2"]["V_story"] == 0.0 and db[1]["L2"]["v_unit_plf"] == 0.0
    assert abs(sum(r["V_story"] for r in db[1].values()) - sum(F.values())) < 1e-9
    assert set(db[1]["L1"]["transfer_in"]) == {"L2"}
    print("  breezeway: L2 story-1 shear %.1f kip transferred to L1/L3 (no 1/0)"
          % db[1]["L2"]["transfer_out_kip"])
    print("SELF-TEST PASS")


if __name__ == "__main__":
    _selftest()

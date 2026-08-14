"""
wall_line.py -- per-wall-line TRIBUTARY VALIDATOR (scope decision #2).

Independent of OpenSees: distributes story shears to wall lines by tributary area under the
FLEXIBLE-diaphragm idealization (the CFS light-frame default, ASCE 7-22 12.3.1.1), computes
per-line unit shears, stacks cumulative chord/hold-down tension story-by-story, and evaluates
the S400-style four-term wall deflection. In the pipeline this runs alongside the OpenSees
model as a GATE: wall-line shears from the two must agree within tolerance, and divergence
requires agent justification (open fronts, offsets, mixed diaphragms).

NO capacities: unit shear DEMANDS and tension DEMANDS only. Sheathing/fastener selection,
hold-down selection, and every strength check remain agent/RAG work.

Hold-down hardware classes (scope decision #7 -- hybrid class-envelope): capacity bands and
stiffness for DISCRETE devices are representative in-house values ("EOR substitutes a specific
product"); continuous RODS are computed (PL/AE + take-up allowance).

Geometry in FEET here (validator-facing, matches brief language); forces kips; stress ksi.
"""
import math

E_KSI = 29500.0

# device class -> (max factored tension kip, stiffness kip/in) -- representative envelope bands
HOLDDOWN_BANDS = {
    "strap":  (5.0, 20.0),
    "bolted": (20.0, 50.0),
    # "rod" is computed, not banded
}
ROD_TAKEUP_IN = 0.05      # per-level take-up device travel allowance (in), stated assumption


class WallLine(object):
    """One lateral line in one direction: position (ft), and per-story wall segments.
    segments[story] = list of (length_ft, height_ft). Type II adjustment factors and 2w/h
    aspect-ratio reductions are applied to CAPACITY by the agent; the validator reports the
    aspect ratio so the agent must respond to it.

    Optional kwargs:
      trib_scale  -- scales this line's tributary width (default 1.0). Use < 1.0 for a
                     PARTIAL-DEPTH line (e.g. a notch-back wall that only collects a local
                     bay of a much deeper diaphragm); forces renormalize over the scaled
                     widths, so the balance flows to the neighbouring full-depth lines.
                     State the justification in the package.
      wall_props  -- per-line four-term drift inputs dict (chord_area_in2, Gp_kip_in,
                     en_in, k_anchor_kip_in); overrides cfg['wall_props'] for this line
                     so different sheathing/anchorage schedules drift correctly.
    COLLINEAR lines (same position) are supported: they share the position's tributary
    width, split per story in proportion to sheathed length x trib_scale."""
    def __init__(self, name, pos_ft, segments, trib_scale=1.0, wall_props=None):
        self.name, self.pos, self.segments = name, float(pos_ft), segments
        self.trib_scale = float(trib_scale)
        self.wall_props = wall_props

    def length(self, story):
        return sum(L for (L, h) in self.segments.get(story, []))

    def high_aspect(self, story):
        return [(L, h) for (L, h) in self.segments.get(story, []) if L > 0 and h / L > 2.0]


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


def _group_widths(order, gs, dim_ft, shift):
    """(width, width_shifted) per unique position; single-line groups carry the line's
    trib_scale (partial-depth lines)."""
    gw = {}
    for i, x in enumerate(order):
        left = (x - order[i - 1]) if i > 0 else 0.0
        right = (order[i + 1] - x) if i < len(order) - 1 else 0.0
        edge_l = x if i == 0 else 0.0                    # cantilever edge strips
        edge_r = (dim_ft - x) if i == len(order) - 1 else 0.0
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


def fit_positions(true_areas, dim_ft):
    """IRREGULAR-PLAN HELPER (T/U/L plans): positions on a UNIFORM 1-D strip whose
    tributary widths reproduce the given per-line TRUE tributary areas (compute those by
    hand from the actual variable-depth plan, in axis order, first line at the 0 edge).

    The midpoint-tributary equations telescope into two decoupled chains (evens follow
    p0, odds follow p1 = 2*w0 - p0) with ONE free DOF, p0; the closure equation is
    independent of p0. The legacy behavior pinned p0 = 0, which is exact but can return
    NON-MONOTONE positions when target widths vary strongly (found on the Ex7 Z-plan,
    ~6:1 spread -- inverted line order broke distribute() with negative shears).
    FIX (2026-07-31): keep p0 = 0 bit-for-bit when it already yields monotone in-range
    positions (regression-safe); otherwise choose p0 by maximin gap search; if no p0
    can order the lines, fall back to strip-center positions (approximate tribs) with
    a printed warning -- in that case prefer WallLine(trib_scale=...) at natural
    positions instead. Pair with cfg['area_sf'] / cfg['perimeter_ft'] and STATE the
    fit table in the package."""
    tot = float(sum(true_areas))
    widths = [a / tot * float(dim_ft) for a in true_areas]
    n = len(widths)
    if n == 1:
        return [dim_ft / 2.0]

    def chain(p0):
        p = [p0, 2.0 * widths[0] - p0]
        for i in range(1, n - 1):
            p.append(p[i - 1] + 2.0 * widths[i])
        return p

    def min_gap(p):
        gaps = [p[0] - 0.0] + [p[i + 1] - p[i] for i in range(n - 1)] \
               + [float(dim_ft) - p[-1]]
        return min(gaps)

    legacy = chain(0.0)
    if min_gap(legacy) >= -1e-9:
        return legacy                       # legacy-exact path (regression-safe)

    # maximin over p0: min_gap(chain(p0)) is concave piecewise-linear -> ternary search
    lo, hi = -float(dim_ft), 2.0 * float(dim_ft)
    for _ in range(200):
        m1 = lo + (hi - lo) / 3.0
        m2 = hi - (hi - lo) / 3.0
        if min_gap(chain(m1)) < min_gap(chain(m2)):
            lo = m1
        else:
            hi = m2
    best = chain((lo + hi) / 2.0)
    if min_gap(best) > 1e-6:
        return best                         # exact tribs, reordered feasibly

    # infeasible ordering: strip centers (approximate tribs) + warning
    print("wall_line.fit_positions WARNING: target widths cannot be ordered on a "
          "uniform strip (spread too large) -- returning strip-center positions "
          "with APPROXIMATE tributaries; prefer trib_scale at natural positions "
          "and state the substitution in the package.")
    b, out = 0.0, []
    for w in widths:
        out.append(b + w / 2.0)
        b += w
    return out


def distribute(story_forces, lines, dim_ft, shift=0.05):
    """story_forces = {story: F_kip} for ONE direction; lines = [WallLine] resisting it.
    Returns {story: {line: dict(V, V_shifted, v_unit_plf, aspect_flags)}}. Equilibrium is
    asserted (sum V == F). Collinear lines share their position's tributary width in
    proportion to sheathed length at each story; trib_scale-reduced widths renormalize
    (the balance flows to the other lines)."""
    order, gs = _position_groups(lines)
    gw = _group_widths(order, gs, dim_ft, shift)
    tot_w = sum(w for (w, _s) in gw.values())
    out = {}
    for story, F in story_forces.items():
        row = {}
        for x in order:
            grp = gs[x]
            Vg = F * gw[x][0] / tot_w
            Vg_sh = F * min(gw[x][1] / tot_w, 1.0)
            Ls = [max(ln.length(story), 0.0) for ln in grp]
            totL = sum(Ls)
            for ln, Lw in zip(grp, Ls):
                fr = (Lw / totL) if totL > 0 else 1.0 / len(grp)
                V, V_sh = Vg * fr, Vg_sh * fr
                v = (V_sh * 1000.0) / Lw if Lw > 0 else float("inf")
                row[ln.name] = dict(V=V, V_shifted=V_sh, wall_len_ft=Lw, v_unit_plf=v,
                                    aspect_flags=ln.high_aspect(story))
        s = sum(r["V"] for r in row.values())
        assert abs(s - F) < 1e-6 * max(F, 1.0), "tributary equilibrium broke"
        out[story] = row
    return out


def overturning_stack(line, dist, story_heights_ft, dead_kip_per_story=None, dead_factor=0.9):
    """Cumulative chord/hold-down TENSION demand at each story of one line, stacking from the
    top down (the CFS bookkeeping the rubric scores): at story k,
        T_k = sum_{j>=k} V_j * h_j / L_k  -  dead_factor * P_dead_above / 2
    per wall-line (single-segment idealization; the agent refines per segment). Returns
    {story: dict(T_kip, V_cum, note)}."""
    stories = sorted(dist.keys(), reverse=True)
    out = {}
    M_cum = 0.0
    V_run = 0.0
    P_dead = 0.0
    for k in stories:
        V = dist[k][line.name]["V_shifted"]
        h = story_heights_ft[k]
        # overturning accumulates as CUMULATIVE story shear x story height (identically
        # sum of story forces x their lever arms). The pre-2026-07-31 form used the
        # story FORCE x its own story height, understating multistory hold-down tension
        # ~3x -- fixed (BUG; see test_solutions_gold/ISSUES.md).
        V_run += V
        M_cum += V_run * h
        if dead_kip_per_story:
            P_dead += dead_kip_per_story.get(k, 0.0)
        L = max(line.length(k), 1e-6)
        T = M_cum / L - dead_factor * P_dead / 2.0
        out[k] = dict(T_kip=max(T, 0.0), V_cum=sum(dist[j][line.name]["V_shifted"]
                                                   for j in stories if j >= k),
                      M_ot_kipft=M_cum, wall_len_ft=L)
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


def s400_deflection(v_plf, h_ft, L_ft, chord_area_in2, Gp_kip_in, en_in, k_anchor_kip_in, T_kip):
    """Four-term S400-style wall deflection (in): bending (chord strain) + shear (sheathing,
    apparent stiffness G'a) + fastener slip + anchorage/rod elongation. Generic form for the
    validator -- the exact S400 expression with its omega factors slots in at Stage 2 wiring;
    every term is pluggable so calibration replaces, not restructures.
      v_plf: unit shear (plf); h, L: wall height/length (ft); Gp: apparent shear stiffness
      (kip/in of wall length); en: fastener slip at demand (in); k_anchor: device stiffness."""
    v = v_plf / 1000.0                                   # kip/ft
    h, L = h_ft * 12.0, L_ft * 12.0                      # in
    d_bend = 2.0 * v * (h_ft) * h ** 2 / (3.0 * E_KSI * chord_area_in2 * L)   # 2vh^3/(3EAcb)
    d_shear = v * h / (Gp_kip_in * 12.0) if Gp_kip_in > 0 else 0.0
    d_slip = 0.75 * en_in                                # calibration placeholder (omega terms)
    d_anch = (T_kip / k_anchor_kip_in) * (h / L) if k_anchor_kip_in else 0.0
    return dict(total=d_bend + d_shear + d_slip + d_anch,
                bending=d_bend, shear=d_shear, slip=d_slip, anchorage=d_anch)


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
    print("  L2 story-2: V=%.1f kip (shifted %.1f), v=%.0f plf" %
          (dist[2]["L2"]["V"], dist[2]["L2"]["V_shifted"], v2))
    ot = overturning_stack(lines[1], dist, {1: 10.0, 2: 9.5, 3: 9.5, 4: 9.5},
                           dead_kip_per_story={k: 6.0 for k in F})
    assert ot[1]["T_kip"] > ot[3]["T_kip"] >= 0.0, "tension must grow downward"
    dev1, k1, note1 = pick_holddown(ot[4]["T_kip"])
    dev0, k0, note0 = pick_holddown(ot[1]["T_kip"])
    print("  cumulative T: story4=%.1f kip -> %s; story1=%.1f kip -> %s%s"
          % (ot[4]["T_kip"], dev1, ot[1]["T_kip"], dev0,
             " (%s)" % note0[:40] if note0 else ""))
    if dev0 == "rod":
        dr = rod_elongation(ot[1]["T_kip"], 1.05, 4 * 115.0, takeup_levels=4)
        print("  rod elongation over height: %.2f in" % dr)
    d = s400_deflection(v_plf=v2, h_ft=9.5, L_ft=15.0, chord_area_in2=1.2,
                        Gp_kip_in=9.0, en_in=0.03, k_anchor_kip_in=50.0,
                        T_kip=ot[2]["T_kip"])
    assert d["total"] > 0 and all(x >= 0 for x in d.values())
    print("  S400 4-term deflection @L2 story2: %.3f in (b=%.3f s=%.3f slip=%.3f a=%.3f)"
          % (d["total"], d["bending"], d["shear"], d["slip"], d["anchorage"]))
    flags = compare_with_model(dist, {k: {ln.name: dist[k][ln.name]["V"] for ln in lines}
                                      for k in F})
    assert flags == []
    bad = compare_with_model(dist, {1: {"L1": dist[1]["L1"]["V"] * 1.5, "L2": dist[1]["L2"]["V"],
                                        "L3": dist[1]["L3"]["V"]}})
    assert any("justify" in f for f in bad)
    print("  gate: clean model passes; 50%% divergence flags correctly")
    print("SELF-TEST PASS")


if __name__ == "__main__":
    _selftest()

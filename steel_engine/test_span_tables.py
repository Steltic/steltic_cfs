"""
test_span_tables.py -- the Stage-3b/Gate-3 SPAN/LOAD-TABLE TEST SUITE: the member-check
mechanics (simple-span bending / shear / deflection with the SFIA-basis section properties)
reproduced against the owner's extracted oracle tables. Pure python:

    python3 test_span_tables.py

What is tested (and the table conventions discovered while calibrating -- documented here
because the agent must apply the SAME conventions when it designs members):

CURTAINWALL LIMITING HEIGHTS (sfia_oracle_limiting_heights.csv, curtainwall_single):
  h = min( sqrt(8*Ma/w),                     bending, Ma = tabulated ASD allowable moment
           (384*E*Ixe/(5*w_d*n))^(1/3),      deflection at limit L/n, Ixe = tabulated
           2*Va_net/w )                      shear on the NET (punched) section
  with w_d = 0.7*w for wind pressures ABOVE 5 psf (the SFIA serviceability wind factor)
  and w_d = 1.0*w AT 5 psf (that column is the IBC interior 5-psf minimum -- not wind, no
  0.7 factor). This 0.7-vs-1.0 split was verified against the tables to ~0.1%.

JOIST ALLOWABLE LOADS (sfia_oracle_joist_spans.csv):
  - The published TOTAL is strength-based: our min(8*Ma_dist/L^2, 2*Va_net/L) is an UPPER
    BOUND on every row (100% at the 2% tolerance) and matches EXACTLY (<=3%) on the rows
    where distortional bending or shear governs (~40%+ of rows).
  - DEEPER/THICKER rows are governed by bearing web-crippling(+interaction), whose bearing
    assumptions are not tabulated in our extraction: those remain the AGENT's H3/G5 design
    territory (the web-crippling oracle carries the Pa values) -- the suite only requires
    that our strength bound NEVER sits below the published value (never unconservative to
    use the oracle as a cap).
  - Where the published LIVE column is deflection-governed it corresponds to L/360 on Ixe.

Gates asserted (calibrated 2026-07-30 on the real extraction; regressions = real breaks):
  curtainwall: >=99% of rows within 3%, median |dev| < 0.5%
  joists:      strength bound holds on 100% of rows; >=40% exact within 3%;
               live L/360 identification on >=250 rows
"""
import csv
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cfs_sections as CS

E_PSI = 29.5e6


def _limiting_height(member, fy, load_psf, spacing_in, defl_n):
    t = CS.table_effective(member, fy)
    if not t or not t["Ma_asd"] or not t["Ixe"]:
        return None
    w = load_psf * spacing_in / 12.0 / 12.0            # lb/in
    wd = w * (1.0 if load_psf <= 5.0 else 0.7)         # SFIA serviceability wind factor
    L_b = math.sqrt(8.0 * t["Ma_asd"] * 1000.0 / w)
    L_d = (384.0 * E_PSI * t["Ixe"] / (5.0 * wd * defl_n)) ** (1.0 / 3.0)
    L_v = 2.0 * (t["Va_net_asd_lb"] or t["Va_asd_lb"] or 9e9) / w
    return min(L_b, L_d, L_v)


def run_curtainwall(verbose=True):
    path = os.path.join(HERE, "sfia_oracle_limiting_heights.csv")
    rows = [r for r in csv.DictReader(open(path)) if r["table"] == "curtainwall_single"]
    devs, skipped = [], 0
    for r in rows:
        try:
            pred = _limiting_height(r["member"], int(r["Fy"]), float(r["load_psf"]),
                                    float(r["spacing_in"]),
                                    float(r["defl_limit"].split("/")[1]))
        except KeyError:
            pred = None
        if pred is None:
            skipped += 1
            continue
        devs.append((pred - float(r["height_in"])) / float(r["height_in"]))
    devs.sort()
    n = len(devs)
    within3 = sum(1 for x in devs if abs(x) <= 0.03) / n
    med = abs(devs[n // 2])
    if verbose:
        print("curtainwall: %d rows (%d skipped: sections outside cfs_shapes) | "
              "median %+0.2f%%, within 3%%: %.1f%%, max %.1f%%"
              % (n, skipped, 100 * devs[n // 2], 100 * within3,
                 100 * max(abs(x) for x in devs)))
    assert n > 8000, "curtainwall oracle rows missing"
    assert within3 >= 0.99, "curtainwall agreement regressed: %.1f%% within 3%%" \
        % (100 * within3)
    assert med < 0.005, "curtainwall median drifted: %.2f%%" % (100 * med)
    return n, within3


def run_joists(verbose=True):
    path = os.path.join(HERE, "sfia_oracle_joist_spans.csv")
    rows = list(csv.DictReader(open(path)))
    n = unc = exact = live360 = 0
    for r in rows:
        t = CS.table_effective(r["member"], int(r["Fy"]))
        if not t or not t["Ma_dist_asd"]:
            continue
        n += 1
        L = float(r["span_in"])
        pred = min(8.0 * t["Ma_dist_asd"] * 1000.0 / L ** 2 * 12.0,
                   2.0 * (t["Va_net_asd_lb"] or 9e9) / L * 12.0)
        ref = float(r["total_asd_plf"])
        if pred < ref * 0.98:
            unc += 1
        if abs(pred - ref) / ref <= 0.03:
            exact += 1
        rl = float(r["live_asd_plf"])
        if ref - rl > 0.5 and t["Ixe"]:
            wd = 384.0 * E_PSI * t["Ixe"] / (5.0 * 360.0 * L ** 3) * 12.0
            if abs(wd - rl) / rl <= 0.03:
                live360 += 1
    if verbose:
        print("joists: %d rows | strength bound (min(M_dist, Va) >= published) holds on "
              "%d/%d; exact within 3%% on %d (%.0f%%); live==L/360 identified on %d rows"
              % (n, n - unc, n, exact, 100 * exact / n, live360))
        print("  (remaining rows are bearing-crippling/interaction governed -- the agent's "
              "H3/G5 design item; the crippling oracle carries the Pa values)")
    assert n > 900, "joist oracle rows missing"
    assert unc == 0, "%d joist rows UNCONSERVATIVE vs published totals" % unc
    assert exact >= 0.40 * n, "exact-agreement subset regressed: %d/%d" % (exact, n)
    assert live360 >= 250, "live-load L/360 identification regressed: %d" % live360
    return n, exact


def main():
    print("span/load-table test suite (Gate 3) -- SFIA-basis member-check mechanics")
    run_curtainwall()
    run_joists()
    print("SPAN-TABLE SUITE PASS")


if __name__ == "__main__":
    main()

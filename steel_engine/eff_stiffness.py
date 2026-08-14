"""
eff_stiffness.py -- Stage 2b: the effective-section stiffness iteration (scope §2).

The decoupled scheme, mirroring S100's own separation: EA from Ae(f_axial), EI from
I_eff(f_bending), each per its own spec procedure; combination stays in the agent's H1
capacity interaction. Gross-property models are TOO STIFF for CFS (non-conservative drift/
deflection/distribution) -- this module runs the pass-1-gross -> stresses -> effective ->
reassign -> re-run loop to convergence.

Pipeline-agnostic: the caller supplies two callbacks --
  analyze(props) -> demands      (build + solve the model with {member: dict(A, Ix)} props)
  demands[member] = dict(P_kip, M_kipin_stations=[...])   (nseg station moments, from
                                                           static_model's existing sampling)
Two stiffness SETS are produced (scope decision): 'strength' (envelope stress, lowest I_eff --
drift + force distribution) and 'service' (deflection checks). Station-weighted prismatic
I_eff: I_eff computed per station, weighted by |M| (stiffness matters most where moment is
largest), one prismatic value per member -- the ACI-cracked-I analogy from the scope.
"""
import cfs_sections as SEC


def _member_stresses(section, dem):
    """(f_axial, [f_bend per station]) in ksi from member demands + gross props. Stresses are
    CAPPED at Fy: effective widths are undefined past yield, and stiffness for analysis maxes
    out at the yield-level effective section (a member stressed past Fy is a STRENGTH failure
    the agent's D/C check reports -- letting f run past Fy just destabilizes the iteration)."""
    p = SEC.gross_props(section)
    fy = p["Fy"]
    fa = min(abs(dem.get("P_kip", 0.0)) / p["A"], fy)
    Sx = p["Ix"] / (p["depth"] / 2.0)
    fbs = [min(abs(M) / Sx, fy) for M in dem.get("M_kipin_stations", [0.0])]
    return fa, fbs


def station_weighted_Ieff(section, f_bend_stations, M_stations=None):
    """Prismatic I_eff from per-station values, |M|-weighted (uniform if no M given)."""
    ws = [abs(m) for m in (M_stations or [1.0] * len(f_bend_stations))]
    if sum(ws) <= 0:
        ws = [1.0] * len(f_bend_stations)
    vals = [SEC.effective_Ix(section, max(f, 0.1))["Ixe"] for f in f_bend_stations]
    return sum(v * w for v, w in zip(vals, ws)) / sum(ws)


def member_effective_props(section, dem, service_factor=0.7):
    """One member's two stiffness sets. service_factor scales strength-level stresses to the
    service estimate when the caller has no separate service demands (stated approximation)."""
    fa, fbs = _member_stresses(section, dem)
    Ms = dem.get("M_kipin_stations")
    p = SEC.gross_props(section)
    out = {}
    for name, sf in (("strength", 1.0), ("service", service_factor)):
        Ae = min(SEC.effective_area(section, max(fa * sf, 0.1)), p["A"])
        Ieff = min(station_weighted_Ieff(section, [f * sf for f in fbs], Ms), p["Ix"])
        out[name] = dict(A=Ae, Ix=Ieff, A_over_gross=Ae / p["A"], I_over_gross=Ieff / p["Ix"])
    return out


def iterate(members, analyze, max_iter=4, tol=0.02):
    """The loop. members = {name: section_designator}; analyze(props) -> demands (see header).
    Starts from gross ('fully effective') properties; converges when every member's strength-set
    A and Ix change < tol between passes. Returns dict(props, demands, history, converged)."""
    props = {}
    for m, sec in members.items():
        p = SEC.gross_props(sec)
        props[m] = dict(A=p["A"], Ix=p["Ix"])
    history = []
    converged = False
    demands = None
    for it in range(max_iter):
        demands = analyze({m: dict(props[m]) for m in members})
        new = {}
        worst = 0.0
        for m, sec in members.items():
            eff = member_effective_props(sec, demands.get(m, {}))
            s = eff["strength"]
            dA = abs(s["A"] - props[m]["A"]) / props[m]["A"]
            dI = abs(s["Ix"] - props[m]["Ix"]) / props[m]["Ix"]
            worst = max(worst, dA, dI)
            new[m] = dict(A=s["A"], Ix=s["Ix"], service=eff["service"],
                          A_over_gross=s["A_over_gross"], I_over_gross=s["I_over_gross"])
        history.append(dict(iteration=it + 1, worst_change=worst))
        props = {m: dict(A=new[m]["A"], Ix=new[m]["Ix"]) for m in members}
        if worst < tol:
            converged = True
            props_full = new
            break
        props_full = new
    return dict(props=props_full, demands=demands, history=history, converged=converged)


def _selftest():
    print("eff_stiffness self-test")
    members = {"rafter": "1000S250-97", "column": "800S200-68", "joist": "600S162-54"}

    def fake_analyze(props):
        """Toy 'model': demands scale mildly with stiffness share (force follows stiffness),
        emulating the redistribution the real model does."""
        Itot = sum(p["Ix"] for p in props.values())
        out = {}
        base = dict(rafter=(8.0, [0, 180, 260, 180, 0]), column=(35.0, [120, 60, 0, 60, 120]),
                    joist=(2.0, [0, 60, 90, 60, 0]))
        for m, (P, Ms) in base.items():
            share = props[m]["Ix"] / Itot
            out[m] = dict(P_kip=P, M_kipin_stations=[mm * (0.5 + share) for mm in Ms])
        return out

    r = iterate(members, fake_analyze)
    assert r["converged"], r["history"]
    for m in members:
        p = r["props"][m]
        assert 0.4 < p["A_over_gross"] <= 1.0 and 0.5 < p["I_over_gross"] <= 1.0
        assert p["service"]["Ix"] >= p["Ix"] * 0.999, "service I_eff must be >= strength I_eff"
        print("  %-7s A/Ag=%.3f I/Ig=%.3f (service I/Ig=%.3f)  iters=%d"
              % (m, p["A_over_gross"], p["I_over_gross"], p["service"]["I_over_gross"],
                 len(r["history"])))
    # softer member sheds force in the toy model: check the loop actually moved something
    assert any(h["worst_change"] > 0.01 for h in r["history"])
    # station weighting: midspan-heavy moment pattern must weight the high-stress I_eff hardest
    Iu = station_weighted_Ieff("600S162-54", [5.0, 25.0, 35.0, 25.0, 5.0])
    Iw = station_weighted_Ieff("600S162-54", [5.0, 25.0, 35.0, 25.0, 5.0],
                               M_stations=[0, 60, 90, 60, 0])
    assert Iw <= Iu + 1e-9, "M-weighting must not exceed uniform weighting here"
    print("  station weighting: uniform %.3f vs M-weighted %.3f in^4" % (Iu, Iw))
    print("SELF-TEST PASS")


if __name__ == "__main__":
    _selftest()

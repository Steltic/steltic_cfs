"""
cfs_systems.py -- CFS seismic-system table, drift limits, materials, and the analysis-fidelity
tier definitions + preflight (scope decision #8). Reference data + screening only; no capacities.

Sources the agent must still GROUND in the RAG: ASCE 7-22 Table 12.2-1 (light-frame CFS rows)
and AISI S400 (system provisions). This module exists so cfg construction and the
preflight screens have one authoritative in-repo table, exactly like engine3d.seis() did for
hot-rolled.
"""

E_KSI = 29500.0
G_KSI = 11300.0

# system key -> dict(R, Cd, Om0, height limit ft by SDC {B,C,D,E,F}, standard, notes)
NL = None   # no limit
NP = 0.0    # not permitted
SYSTEMS = {
    # -- light-frame (CFS) bearing-wall systems, ASCE 7-22 Table 12.2-1 --
    "wsp_shearwall":   dict(R=6.5, Cd=4.0, Om0=3.0, hlim={"B": NL, "C": NL, "D": 65, "E": 65, "F": 65},
                            std="AISI S400 E1/E2", label="CFS shear wall, wood structural panels"),
    "steelsheet_wall": dict(R=6.5, Cd=4.0, Om0=3.0, hlim={"B": NL, "C": NL, "D": 65, "E": 65, "F": 65},
                            std="AISI S400 E2", label="CFS shear wall, steel sheet"),
    "gypsum_wall":     dict(R=2.0, Cd=2.0, Om0=2.5, hlim={"B": NL, "C": NL, "D": 35, "E": NP, "F": NP},
                            std="AISI S400 E5", label="CFS wall, other materials (gypsum)"),
    "strap_braced":    dict(R=4.0, Cd=3.5, Om0=2.0, hlim={"B": NL, "C": NL, "D": 65, "E": 65, "F": 65},
                            std="AISI S400 E3", label="CFS strap-braced wall"),
    "sbmf":            dict(R=3.5, Cd=3.5, Om0=3.0, hlim={"B": NL, "C": NL, "D": 35, "E": 35, "F": 35},
                            std="AISI S400 E4", label="CFS special bolted moment frame"),
    # -- systems not specifically detailed (portals etc.), Table 12.2-1 row H --
    "not_detailed":    dict(R=3.0, Cd=3.0, Om0=3.0, hlim={"B": NL, "C": NL, "D": NP, "E": NP, "F": NP},
                            std="AISI S100 only (no S400)", label="CFS system not specifically detailed"),
    # (storage racks EXCLUDED from scope 2026-07-30 -- no rack systems in this table)
}

# ASCE 7-22 Table 12.12-1 drift limits (fraction of h_sx) by structure class / Risk Category
def drift_limit(system_key, n_stories, risk_cat="II"):
    """Allowable story drift ratio. Light-frame <=4 stories with interior wall finishes gets the
    0.025 row (RC I/II); otherwise 0.020, tightened for RC III/IV."""
    light_frame = system_key in ("wsp_shearwall", "steelsheet_wall", "gypsum_wall", "strap_braced")
    if risk_cat in ("I", "II"):
        return 0.025 if (light_frame and n_stories <= 4) else 0.020
    if risk_cat == "III":
        return 0.020 if (light_frame and n_stories <= 4) else 0.015
    return 0.015 if (light_frame and n_stories <= 4) else 0.010     # RC IV


def seis_cfs(SDS, SD1, S1, system, Ie=1.0, TL=8.0, Ct=0.02, x=0.75):
    """cfg['seis'] builder from the CFS system table (drop-in analogue of engine3d.seis()).
    Ct/x default to the 'all other systems' approximate-period values -- light-frame CFS has no
    special Ta formula in 12.8.2.1."""
    s = SYSTEMS[system]
    return dict(SDS=SDS, SD1=SD1, S1=S1, R=s["R"], Cd=s["Cd"], Om0=s["Om0"], Ie=Ie, TL=TL,
                Ct=Ct, x=x, Cu=1.4 if SD1 >= 0.4 else 1.7, system=system)


def height_check(system, SDC, hn_ft):
    """(ok, message). NP -> not permitted; number -> limit; None -> no limit."""
    lim = SYSTEMS[system]["hlim"].get(SDC.upper())
    if lim is NP or (isinstance(lim, float) and lim == 0.0):
        return False, "%s is NOT PERMITTED in SDC %s (ASCE 7-22 Table 12.2-1)" % (system, SDC)
    if lim is NL or lim is None:
        return True, None
    if hn_ft > lim:
        return False, "%s exceeds the %s-ft height limit in SDC %s (hn = %.1f ft)" % (
            system, lim, SDC, hn_ft)
    return True, None


# ---------------- analysis fidelity tiers (scope decision #8) ----------------

TIERS = {
    0: dict(name="Standard", elements="elasticBeamColumn + Ae/I_eff iteration",
            for_="wall-framed buildings (walls are S400-calibrated springs)"),
    1: dict(name="Thin-walled members", elements="dispBeamColumnAsym/mixedBeamColumnAsym + Ae/I_eff",
            for_="portal frames, canopies, single-channel members"),
    2: dict(name="High fidelity", elements="Du&Hajjar elements + EWM effective fiber laws, incremental",
            for_="torsion-critical slender portals, verification passes"),
}

_TIER_MIN = {  # minimum sensible tier by declared structure kind
    "wall": 0, "podium": 0,
    "portal": 1, "canopy": 1, "purlin": 0,
    "portal_singlechannel": 1,   # Tier 2 recommended; 1 is the floor
}


def preflight_fidelity(structure_kind, tier):
    """Tier-vs-structure screen (the brief-form tickbox is user-chosen; this is the WARN gate).
    Returns list of warning strings (empty = ok)."""
    out = []
    if tier not in TIERS:
        return ["analysis_fidelity=%r is not a tier (0, 1, 2)" % (tier,)]
    floor_ = _TIER_MIN.get(structure_kind)
    if floor_ is None:
        out.append("unknown structure kind %r -- declare one of %s"
                   % (structure_kind, sorted(_TIER_MIN)))
        return out
    if tier < floor_:
        out.append("Tier %d selected for %r but Tier %d is the sensible minimum (%s). "
                   "Proceeding would mis-state stiffness (warping/torsion or P-Delta path). "
                   "Raise cfg['analysis_fidelity'] or justify explicitly in the report."
                   % (tier, structure_kind, floor_, TIERS[floor_]["for_"]))
    if structure_kind == "portal_singlechannel" and tier < 2:
        out.append("single-channel portals: shear-center torsion at every load point -- Tier 2 "
                   "recommended; justify Tier %d explicitly (see Ex27)." % tier)
    return out


def _selftest():
    print("cfs_systems self-test")
    assert SYSTEMS["wsp_shearwall"]["R"] == 6.5 and SYSTEMS["strap_braced"]["R"] == 4.0
    assert SYSTEMS["gypsum_wall"]["R"] == 2.0 and SYSTEMS["sbmf"]["R"] == 3.5
    ok, msg = height_check("steelsheet_wall", "D", 78.0)
    assert not ok and "65" in msg, msg
    ok, _ = height_check("steelsheet_wall", "C", 78.0)
    assert ok
    ok, msg = height_check("gypsum_wall", "E", 20.0)
    assert not ok and "NOT PERMITTED" in msg
    assert drift_limit("wsp_shearwall", 4) == 0.025
    assert drift_limit("wsp_shearwall", 6) == 0.020
    assert drift_limit("sbmf", 1) == 0.020
    s = seis_cfs(1.0, 0.45, 0.45, "strap_braced")
    assert s["R"] == 4.0 and s["Cd"] == 3.5 and s["Cu"] == 1.4
    w = preflight_fidelity("portal_singlechannel", 0)
    assert w and any("Tier 2" in x for x in w)
    assert preflight_fidelity("wall", 0) == []
    assert preflight_fidelity("portal", 1) == []
    assert "rack_downaisle" not in SYSTEMS and "rack_drivein" not in _TIER_MIN  # descope 2026-07-30
    print("  systems: %d entries; tiers: %d; sample WARN: %s..." % (len(SYSTEMS), len(TIERS), w[0][:60]))
    print("SELF-TEST PASS")


if __name__ == "__main__":
    _selftest()

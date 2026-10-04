"""
cfs_engine.py -- Stage 2a: the light-frame (wall-framed) analysis path.

Model: under the FLEXIBLE-diaphragm idealization each wall line is an independent vertical stack
of story shear springs, with story mass/force by tributary area (wall_line.py). Spring stiffness
is the SECANT of the S400-style four-term deflection at the line's unit shear -- iterated, since
slip and anchorage terms are load-dependent. This IS the OpenSees model: emit_opensees() builds
the identical spring stack as zeroLength elements when openseespy is available (pipeline/user
machines); the pure-python solve here is the same physics, exact for a series chain, and keeps
the validator and tests independent of the binary.

Also here: seismic weights, ELF (same ASCE 7-22 12.8 formulas as engine3d, CFS Ta defaults),
the two-stage podium procedure (12.2.3.2), drift checks vs cfs_systems.drift_limit, and the
model-vs-tributary comparison GATE.

NO capacities. cfg schema (feet/psf/kip at this level -- brief-facing):
cfg = dict(
  stories=4, heights_ft=[10.67, 9.5, 9.5, 9.5], plan_ft=(160.0, 62.0),
  D_floor=35.0, D_roof=20.0, clad=15.0, snow=0.0,          # psf
  seis=cfs_systems.seis_cfs(...), system="wsp_shearwall", risk_cat="II",
  lines_x=[wall_line.WallLine(...), ...],   # lines resisting X (E-W) force, positioned in y
  lines_y=[...],                            # lines resisting Y, positioned in x
  diaphragm="flexible",                     # or "semi-rigid" (Stage 2c: coupling elements)
  wall_props=dict(chord_area_in2=1.2, Gp_kip_in=9.0, en_in=0.03, k_anchor_kip_in=50.0),
  analysis_fidelity=0,
  # optional: partition_psf=10.0 (12.7.2 weight, floors only, W only; min 10 psf) or
  #   partitions=True; storage=True / storage_levels=[...] (12.7.2 item 1: 25% of the floor
  #   live in W; L_by_level={k: psf}); storage_5pct_exception=True (12.7.2 exc. (a), opt-in);
  #   extra_mass_floors={k: psf} (items 3/6);
  # structure_kind='platform'/'mezzanine' or top_level_is_floor=True -- a single elevated
  #   level is a FLOOR (D_floor + live + storage), never a roof;
  # seis_by_dir={'X':..,'Y':..} / system_by_dir / rho_by_dir -- 12.2.2 different systems per
  #   direction; line_systems={'X:A': key} -- 12.2.3.3 horizontal combination (least R), with
  #   use_12_2_3_3_exception=True for per-line R (RC I/II, <= 2 stories, light-frame);
  # drift_tolerant_finishes=True/False -- Table 12.12-1 row 1 (<= 4 stories) vs 'all other';
  #   drift_limit_no_limit_single_story=True (footnote a); drift_limit= (tighter only);
  # area_sf= / perimeter_ft= (TRUE values for T/U/L plans on a bounding-box model --
  #   pair with wall_line.fit_positions for line placement);
  # per-line drift props / partial-depth lines: WallLine(..., wall_props=..., trib_scale=...)
)
"""
import math
import wall_line as WL
import cfs_systems as CS

try:
    import openseespy.opensees as ops
    HAVE_OPS = True
except Exception:
    ops = None
    HAVE_OPS = False


# ---------------- weights & ELF ----------------

PLATFORM_KINDS = ("platform", "mezzanine")


def top_is_floor(cfg):
    """True when the TOP level N is a floor, not a roof: a single elevated platform /
    storage mezzanine (structure_kind 'platform'/'mezzanine', or cfg['top_level_is_floor']),
    or a top level listed in cfg['storage_levels']. Then level N takes D_floor + live load
    (gravity seeds) and the 12.7.2 storage/partition terms (seismic weight), not D_roof."""
    if cfg.get("top_level_is_floor"):
        return True
    if str(cfg.get("structure_kind", "")).lower() in PLATFORM_KINDS:
        return True
    lv = cfg.get("storage_levels") or ()
    return int(cfg["stories"]) in {int(i) for i in lv}


def level_live_psf(cfg, k):
    """Floor live load (psf) at level k: cfg['L_by_level'][k] override, else L_floor."""
    by = cfg.get("L_by_level") or {}
    if k in by or str(k) in by:
        return float(by[k] if k in by else by[str(k)])
    return float(cfg.get("L_floor", 0.0) or 0.0)


def is_storage_level(cfg, k):
    """ASCE 7-22 12.7.2 item 1 storage area at level k: cfg['storage']=True (every FLOOR level;
    the top level only when it is a floor, see top_is_floor) or k in cfg['storage_levels']."""
    lv = cfg.get("storage_levels")
    if lv and k in {int(i) for i in lv}:
        return True
    if cfg.get("storage"):
        return k < int(cfg["stories"]) or top_is_floor(cfg)
    return False


def _partition_psf(cfg):
    """12.7.2 item 2 partition weight (psf of floor area): where provision for partitions is
    made (cfg['partition_psf'] given, or cfg['partitions']=True) the actual weight or 10 psf,
    whichever is greater. Returns (psf, note or None)."""
    p = cfg.get("partition_psf")
    if p is None and not cfg.get("partitions"):
        return 0.0, None
    p = float(p or 0.0)
    if p < 10.0:
        return 10.0, ("partition weight raised to 10 psf (ASCE 7-22 12.7.2 item 2: actual "
                      "partition weight or 10 psf, whichever is greater; declared %.1f psf)" % p)
    return p, None


def story_weight(cfg, k, detail=False):
    """Seismic weight (kip) at level k (1..N, N = roof unless top_is_floor). Mirrors
    engine3d.floor_w (ASCE 7-22 12.7.2): area dead + tributary cladding
      + item 1: 25% of the floor live load in STORAGE areas (cfg['storage'] / ['storage_levels'],
        live per L_by_level / L_floor). Exception (a): where items 1/3/5/6 add <= 5% of W at
        the level they need not be included -- applied ONLY when cfg['storage_5pct_exception']
        is True (default: always included, conservative); the share is reported either way.
      + item 2: partitions at FLOOR levels (cfg['partition_psf'], min 10 psf where partitions
        are provided -- cfg['partitions']=True or a partition_psf value)
      + item 3/6: cfg['extra_mass_floors'] = {level: psf} (permanent equipment, bulk material)
      + item 4: 15% of flat snow at the ROOF only where pf > 45 psf.
    Optional cfg keys (irregular T/U/L plans -- pair with wall_line.fit_positions):
      area_sf       -- TRUE floor area (default Lx*Ly) so W is exact on a bounding-box model
      perimeter_ft  -- TRUE cladding perimeter (default 2*(Lx+Ly))
      partition_psf -- partition weight in W at FLOOR levels only -- keeps D_floor = true dead
                       for member design and the gravity stud stack
      structure_kind='platform'/'mezzanine' or top_level_is_floor=True -- a single elevated
                       level is a FLOOR (D_floor, storage, partitions), never a roof.
    detail=True returns (w, breakdown dict)."""
    Lx, Ly = cfg["plan_ft"]
    A = cfg.get("area_sf") or (Lx * Ly)
    N = cfg["stories"]
    roof = (k == N) and not top_is_floor(cfg)
    bd = {}
    if roof:
        bd["dead"] = cfg["D_roof"] * A / 1000.0
    else:
        bd["dead"] = cfg["D_floor"] * A / 1000.0
        ppsf, _note = _partition_psf(cfg)
        if ppsf:
            bd["partitions"] = ppsf * A / 1000.0
    per = cfg.get("perimeter_ft") or (2.0 * (Lx + Ly))
    h = cfg["heights_ft"][k - 1]
    bd["cladding"] = cfg.get("clad", 0.0) * per * (h / 2.0 if k == N else h) / 1000.0
    if roof and cfg.get("snow", 0.0) > 45.0:
        bd["snow"] = 0.15 * cfg["snow"] * A / 1000.0     # 12.7.2 item 4: 15% of pf where pf > 45
    if not roof and is_storage_level(cfg, k):
        bd["storage"] = 0.25 * level_live_psf(cfg, k) * A / 1000.0      # 12.7.2 item 1
    xm = (cfg.get("extra_mass_floors") or {})
    xv = xm.get(k, xm.get(str(k), 0.0)) if isinstance(xm, dict) else 0.0
    if xv:
        bd["equipment_bulk"] = float(xv) * A / 1000.0                   # 12.7.2 items 3/6
    w = sum(bd.values())
    items_1356 = bd.get("storage", 0.0) + bd.get("equipment_bulk", 0.0)
    bd["items_1_3_5_6_share"] = (items_1356 / w) if w > 0 else 0.0
    if items_1356 and cfg.get("storage_5pct_exception") and bd["items_1_3_5_6_share"] <= 0.05:
        w -= items_1356                                                  # 12.7.2 exception (a)
        bd["exception_a_applied"] = True
    return (w, bd) if detail else w


def load_screens(cfg):
    """Seismic-weight / occupancy screens for the WALL path (the CFS twin of the hot-rolled
    preflight storage + platform screens, which never ran on CFS). Returns warning strings."""
    out = []
    N = int(cfg["stories"])
    Lf = cfg.get("L_floor")
    lv_max = max([level_live_psf(cfg, k) for k in range(1, N + 1)] + [float(Lf or 0.0)])
    storage = bool(cfg.get("storage") or cfg.get("storage_levels"))
    if lv_max >= 125.0 and not storage:
        out.append("floor live %.0f psf suggests STORAGE occupancy -- ASCE 7-22 12.7.2 item 1 "
                   "requires >= 25%% of the storage live load in W: set cfg['storage']=True "
                   "or cfg['storage_levels']=[...]" % lv_max)
    kind = str(cfg.get("structure_kind", "")).lower()
    arch = (str(cfg.get("arch", "")) + " " + kind).lower()
    platformish = any(w in arch for w in ("platform", "mezzanine", "catwalk"))
    if platformish:
        out.append("platform/mezzanine: classify per ASCE 7-22 15.1.1 -- an OCCUPIED platform "
                   "(e.g. storage mezzanine with pickers) is a Ch. 12 building structure "
                   "(Table 12.2-1 R/Cd/Omega0); Ch. 15 applies to UNOCCUPIED nonbuilding "
                   "structures (15.4.1(1)(a) still permits Table 12.2-1 for building-like "
                   "ones). Document the classification; joist/beam/post schedules with L/360 "
                   "live-load deflection ARE deliverables")
    if N == 1 and storage and not top_is_floor(cfg):
        out.append("single-level model with storage declared but the level is treated as a "
                   "ROOF (D_roof, no live, no storage weight) -- declare structure_kind="
                   "'platform'/'mezzanine' or top_level_is_floor=True so the 12.7.2 storage "
                   "weight and the floor live load are carried")
    if platformish and not top_is_floor(cfg):
        out.append("platform/mezzanine keywords but the top level is modelled as a ROOF -- set "
                   "structure_kind='platform'/'mezzanine' (or top_level_is_floor=True)")
    _p, note = _partition_psf(cfg)
    if note:
        out.append(note)
    if cfg.get("storage_5pct_exception"):
        for k in range(1, N + 1):
            _w, bd = story_weight(cfg, k, detail=True)
            if bd.get("storage") and not bd.get("exception_a_applied"):
                out.append("level %d: storage weight is %.1f%% of W > 5%% -- 12.7.2 "
                           "exception (a) does not apply (storage weight kept)"
                           % (k, 100 * bd["items_1_3_5_6_share"]))
    return out


# ---------------- seismic system per direction (12.2.2) / per line (12.2.3.3 exception) -------

def _line_system_override(cfg, dirn, line):
    ls = cfg.get("line_systems") or {}
    return ls.get("%s:%s" % (dirn, line.name)) or ls.get(line.name) or \
        getattr(line, "system", None)


def direction_seismic(cfg, dirn):
    """Seismic parameters for one direction. ASCE 7-22 12.2.2: a different SFRS per
    orthogonal direction is permitted -- cfg['seis_by_dir'] = {'X': seis, 'Y': seis},
    cfg['system_by_dir'] = {'X': key, 'Y': key} (optional cfg['rho_by_dir']); default the
    building-wide cfg['seis'] / cfg['system'].
    Horizontal combinations in ONE direction (lines with different systems via
    cfg['line_systems'] = {'X:A': 'strap_braced', ...}): 12.2.3.3 -- R for the direction is
    the LEAST R of the systems used, Cd and Omega0 consistent with it; EXCEPTION (RC I/II,
    <= 2 stories above grade plane, light-frame construction or flexible diaphragms): each
    independent line may use its own least R -- requested with
    cfg['use_12_2_3_3_exception']=True (refused, with a warning, if a condition fails).
    Returns dict(seis, system, per_line={name: (system, seis)} or None, notes=[...])."""
    import copy as _copy
    base = (cfg.get("seis_by_dir") or {}).get(dirn) or cfg["seis"]
    sysname = (cfg.get("system_by_dir") or {}).get(dirn) or cfg.get("system", "wsp_shearwall")
    lines = cfg["lines_x"] if dirn == "X" else cfg["lines_y"]
    notes = []
    line_sys = {ln.name: (_line_system_override(cfg, dirn, ln) or sysname) for ln in lines}
    systems = sorted(set(line_sys.values()))

    def seis_of(key):
        s2 = _copy.deepcopy(base)
        t = CS.SYSTEMS[key]
        s2.update(R=t["R"], Cd=t["Cd"], Om0=t["Om0"], system=key)
        return s2
    if len(systems) <= 1:
        if systems and systems[0] != sysname and systems[0] in CS.SYSTEMS:
            notes.append("every %s line declares %s (line_systems) -- its Table 12.2-1 "
                         "R/Cd/Omega0 replace cfg['seis'] for the direction" % (dirn, systems[0]))
            return dict(seis=seis_of(systems[0]), system=systems[0], per_line=None, notes=notes)
        return dict(seis=base, system=sysname, per_line=None, notes=notes)
    unknown = [x for x in systems if x not in CS.SYSTEMS]
    if unknown:
        raise KeyError("line_systems %s not in the CFS system table" % unknown)
    least = min(systems, key=lambda k: CS.SYSTEMS[k]["R"])
    rc_ok = str(cfg.get("risk_cat", "II")).upper() in ("I", "II", "1", "2")
    n_ag = int(cfg.get("stories_above_grade", cfg["stories"]))
    lf_ok = all(CS.SYSTEMS[k].get("light_frame") for k in systems) or \
        str(cfg.get("diaphragm", "flexible")).lower() == "flexible"
    if cfg.get("use_12_2_3_3_exception"):
        if rc_ok and n_ag <= 2 and lf_ok:
            notes.append("ASCE 7-22 12.2.3.3 EXCEPTION: each line designed with its own system "
                         "R (RC I/II, %d stories above grade, light-frame/flexible); the "
                         "diaphragm uses the least R in the direction (%s, R=%.1f)"
                         % (n_ag, least, CS.SYSTEMS[least]["R"]))
            return dict(seis=seis_of(least), system=least,
                        per_line={nm: (k, seis_of(k)) for nm, k in line_sys.items()},
                        notes=notes)
        notes.append("12.2.3.3 exception REFUSED (needs RC I/II [%s], <= 2 stories above grade "
                     "[%d], light-frame or flexible diaphragms [%s]) -- least R used for the "
                     "whole direction" % (cfg.get("risk_cat", "II"), n_ag, lf_ok))
    notes.append("ASCE 7-22 12.2.3.3: lines in %s use %s -- R = least R (%s, R=%.1f), Cd and "
                 "Omega0 consistent with it, for the whole direction"
                 % (dirn, "/".join(systems), least, CS.SYSTEMS[least]["R"]))
    return dict(seis=seis_of(least), system=least, per_line=None, notes=notes)


def elf(cfg, direction=None, seis=None):
    """ASCE 7-22 12.8 ELF. Returns dict(V, Cs, Ta, T_used, k, Fx={level: kip}, W, R, Cd, Om0).
    direction='X'/'Y' uses that direction's seismic parameters (12.2.2, direction_seismic);
    seis= overrides. Default: cfg['seis'] (backward compatible)."""
    if seis is None:
        s = direction_seismic(cfg, direction)["seis"] if direction else cfg["seis"]
    else:
        s = seis
    N = cfg["stories"]
    W = sum(story_weight(cfg, k) for k in range(1, N + 1))
    hn = sum(cfg["heights_ft"])
    Ta = s["Ct"] * hn ** s["x"]
    T = min(cfg.get("T_analytical", Ta), s.get("Cu", 1.4) * Ta) if cfg.get("T_analytical") \
        else Ta
    R, Ie = s["R"], s["Ie"]
    Cs = s["SDS"] / (R / Ie)
    TL = s.get("TL", 8.0)
    cap = s["SD1"] / (T * (R / Ie)) if T <= TL else s["SD1"] * TL / (T ** 2 * (R / Ie))
    Cs = min(Cs, cap)
    cmin = max(0.044 * s["SDS"] * Ie, 0.01)
    if s.get("S1", 0) >= 0.6:
        cmin = max(cmin, 0.5 * s["S1"] / (R / Ie))
    Cs = max(Cs, cmin)
    V = Cs * W
    kk = 1.0 if T <= 0.5 else (2.0 if T >= 2.5 else 1.0 + (T - 0.5) / 2.0)
    z = [0.0]
    for h in cfg["heights_ft"]:
        z.append(z[-1] + h)
    whk = {k: story_weight(cfg, k) * z[k] ** kk for k in range(1, N + 1)}
    ss = sum(whk.values())
    return dict(V=V, Cs=Cs, Ta=Ta, T_used=T, k=kk, W=W, R=R, Cd=s.get("Cd"),
                Om0=s.get("Om0"), Fx={k: V * whk[k] / ss for k in range(1, N + 1)})


def fpx(cfg, e=None, direction=None):
    """Diaphragm design forces per ASCE 7-22 12.10.1.1 for one direction (pure ELF, rho = 1
    per 12.3.4.1 item 7): Fpx = (sum_{i>=x} Fi / sum_{i>=x} wi) * wpx (Eq. 12.10-1), not less
    than 0.2 SDS Ie wpx (12.10-2), need not exceed 0.4 SDS Ie wpx (12.10-3); wpx = level
    seismic weight (story_weight). Returns {level: dict(Fx, wpx, Fpx_eq1, Fpx_min, Fpx_max,
    Fpx)} (kip)."""
    e = e or elf(cfg, direction)
    s = direction_seismic(cfg, direction)["seis"] if direction else cfg["seis"]
    N = cfg["stories"]
    w = {k: story_weight(cfg, k) for k in range(1, N + 1)}
    out = {}
    for x in range(1, N + 1):
        sF = sum(e["Fx"][i] for i in range(x, N + 1))
        sW = sum(w[i] for i in range(x, N + 1))
        f1 = sF / sW * w[x] if sW > 0 else 0.0
        fmin = 0.2 * s["SDS"] * s["Ie"] * w[x]
        fmax = 0.4 * s["SDS"] * s["Ie"] * w[x]
        out[x] = dict(Fx=e["Fx"][x], wpx=w[x], Fpx_eq1=f1, Fpx_min=fmin, Fpx_max=fmax,
                      Fpx=min(max(f1, fmin), fmax))
    return out


# ---------------- per-line spring model (the OpenSees-equivalent stack) ----------------

def line_secant_stiffness(cfg, line, story, v_plf, T_kip):
    """Secant story stiffness (kip/in) of one wall line: K = V / delta(v). Uses the S400
    four-term deflection at the story's unit shear; wall segments on the line act in parallel.
    A per-line WallLine(..., wall_props=dict(...)) overrides cfg['wall_props'] so different
    sheathing/anchorage schedules drift correctly line-by-line."""
    wp = getattr(line, "wall_props", None) or cfg["wall_props"]
    h = cfg["heights_ft"][story - 1]
    segs = line.segments.get(story, [])
    if not segs or v_plf <= 0:
        return None
    K = 0.0
    for (L, hseg) in segs:
        d = WL.s400_deflection(v_plf, hseg, L, wp["chord_area_in2"], wp["Gp_kip_in"],
                               wp["en_in"], wp["k_anchor_kip_in"], T_kip)
        Vseg = v_plf * L / 1000.0
        K += Vseg / d["total"] if d["total"] > 0 else 0.0
    return K


def analyze_line(cfg, line, story_shears, n_iter=3):
    """Solve one wall line's spring stack under its story shears {story: V_kip}. Series chain:
    story drift = V_story_cum? No -- each story spring carries the shear of that story
    (sum of forces above), drift_k = Vk_cum / K_k. Iterates K on the deflection nonlinearity.
    Returns {story: dict(V, K_kip_in, drift_in, dr_ratio)}."""
    N = cfg["stories"]
    stories = sorted(story_shears)
    Vcum = {k: sum(story_shears[j] for j in stories if j >= k) for k in stories}
    ot = None
    out = {}
    v_prev = {k: 100.0 for k in stories}                       # plf seed
    for _ in range(n_iter):
        dist_like = {k: {line.name: dict(V_shifted=story_shears[k])} for k in stories}
        ot = WL.overturning_stack(line, dist_like, {k: cfg["heights_ft"][k - 1] for k in stories})
        for k in stories:
            L = max(line.length(k), 1e-6)
            v = Vcum[k] * 1000.0 / L
            v_prev[k] = v
            K = line_secant_stiffness(cfg, line, k, v, ot[k]["T_kip"])
            h_in = cfg["heights_ft"][k - 1] * 12.0
            dr = Vcum[k] / K if K else float("inf")
            out[k] = dict(V=Vcum[k], v_unit_plf=v, K_kip_in=K, drift_in=dr,
                          dr_ratio=dr / h_in, T_kip=ot[k]["T_kip"])
    return out


def drift_limit_for(cfg, dirn=None, system=None):
    """(allowable drift ratio or None, basis) for the wall path -- ASCE 7-22 Table 12.12-1 via
    cfs_systems.drift_limit with the DECLARED finishes flag cfg['drift_tolerant_finishes']
    (True: the <= 4-story row applies to ANY non-masonry structure, incl. SBMF/portals).
    Not declared: legacy -- light-frame wall systems keep the 0.025 row (stated as an
    assumption in the basis), other systems take the 'all other structures' row.
    cfg['drift_limit_no_limit_single_story']=True invokes footnote a (single story).
    A tighter cfg['drift_limit'] is honoured (never a looser one). 12.12.1.1 Delta_a/rho for
    moment-frame-only systems in SDC D-F."""
    sysname = system or ((cfg.get("system_by_dir") or {}).get(dirn) if dirn else None) or \
        cfg.get("system", "wsp_shearwall")
    acc = cfg.get("drift_tolerant_finishes")
    assumed = acc is None
    if assumed:
        acc = sysname in CS.LIGHT_FRAME_SYSTEMS
    s = (cfg.get("seis_by_dir") or {}).get(dirn) if dirn else None
    s = s or cfg["seis"]
    cat = CS.sdc(s.get("SDS", 0.0), s.get("SD1", 0.0), s.get("S1", 0.0),
                 cfg.get("risk_cat", "II"))
    rho = (cfg.get("rho_by_dir") or {}).get(dirn) if dirn else None
    rho = rho if rho is not None else cfg.get("rho")
    if rho is None:
        rho = 1.3 if cat in ("D", "E", "F") else 1.0
    dl, basis = CS.drift_limit(sysname, cfg["stories"], cfg.get("risk_cat", "II"),
                               finishes_accommodate=acc, SDC=cat, rho=rho,
                               single_story_no_limit=bool(
                                   cfg.get("drift_limit_no_limit_single_story")),
                               with_basis=True)
    if assumed:
        basis += ("; cfg['drift_tolerant_finishes'] NOT declared -> %s assumed (declare True/"
                  "False)" % ("finishes accommodate drift" if acc else "'all other structures'"))
    user = cfg.get("drift_limit")
    if dl is not None and isinstance(user, (int, float)) and float(user) < dl:
        dl = float(user)
        basis += "; tighter cfg['drift_limit']=%.3f used" % dl
    return dl, basis


def run(cfg):
    """Full light-frame run for BOTH directions: ELF -> tributary distribution -> per-line
    spring solve -> drift screen -> comparison gate (spring-model line shears vs tributary --
    identical by construction under 'flexible'; the gate becomes meaningful when the
    semi-rigid/OpenSees path replaces analyze_line). Returns the result dict.
    Per-direction seismic systems (12.2.2, cfg['seis_by_dir']/['system_by_dir']) and the
    12.2.3.3 per-line exception are honoured: res['elf'] is the ELF of cfg['seis'] (backward
    compatible), res['elf_by_dir'] holds each direction's, and each direction carries its system, R/Cd/Om0."""
    seis_dir = {d: direction_seismic(cfg, d) for d in ("X", "Y")}
    e_dir = {d: elf(cfg, seis=seis_dir[d]["seis"]) for d in ("X", "Y")}
    e = elf(cfg)                     # building-wide cfg['seis'] (backward compatible)
    res = dict(elf=e, elf_by_dir=e_dir, directions={})
    warn = CS.preflight_fidelity(cfg.get("structure_kind", "wall"),
                                 cfg.get("analysis_fidelity", 0))
    warn = list(warn or []) + load_screens(cfg)
    for d in ("X", "Y"):
        warn += ["%s: %s" % (d, n) for n in seis_dir[d]["notes"]]
        if seis_dir[d]["per_line"] and cfg.get("diaphragm", "flexible") == "semi-rigid":
            warn.append("%s: 12.2.3.3 per-line R is not combined with the semi-rigid solve -- "
                        "the least R is used for every line" % d)
            seis_dir[d]["per_line"] = None
    if warn:
        res["preflight_warnings"] = warn
    Ie = cfg["seis"]["Ie"]
    semirigid = cfg.get("diaphragm", "flexible") == "semi-rigid"
    for dirn, lines, dim in (("X", cfg["lines_x"], cfg["plan_ft"][1]),
                             ("Y", cfg["lines_y"], cfg["plan_ft"][0])):
        sd = seis_dir[dirn]
        ed = e_dir[dirn]
        Cd = sd["seis"]["Cd"]
        dl, dl_basis = drift_limit_for(cfg, dirn, sd["system"])
        dist = WL.distribute(ed["Fx"], lines, dim)
        line_Cd = {}
        if sd["per_line"] and not semirigid:
            # 12.2.3.3 exception: each line's forces from ITS system's ELF (same tributaries)
            by_sys = {}
            for nm, (skey, sseis) in sd["per_line"].items():
                if skey not in by_sys:
                    by_sys[skey] = WL.distribute(elf(cfg, seis=sseis)["Fx"], lines, dim)
                for k in dist:
                    dist[k][nm] = by_sys[skey][k][nm]
                line_Cd[nm] = sseis["Cd"]
        model_shears = {k: {} for k in ed["Fx"]}
        if semirigid:
            depth = cfg["plan_ft"][0] if dirn == "X" else cfg["plan_ft"][1]
            dres = analyze_semirigid(cfg, lines, dist, depth)
            for ln in lines:
                for k in ed["Fx"]:
                    model_shears[k][ln.name] = dres[ln.name][k]["V"] - \
                        dres[ln.name].get(k + 1, {}).get("V", 0.0)
        else:
            dres = {}
            for ln in lines:
                shears = {k: dist[k][ln.name]["V_shifted"] for k in ed["Fx"]}
                lr = analyze_line(cfg, ln, shears)
                dres[ln.name] = lr
                for k in ed["Fx"]:
                    model_shears[k][ln.name] = lr[k]["V"] - (
                        lr.get(k + 1, {}).get("V", 0.0)
                        if isinstance(lr.get(k + 1), dict) else 0.0)
        # drift screen: amplified Cd/Ie vs limit, per line per story
        drift_flags = []
        for name, lr in dres.items():
            for k, r in lr.items():
                amp = line_Cd.get(name, Cd) * r["dr_ratio"] / Ie
                r["drift_amplified"] = amp
                if dl is not None and amp > dl:
                    drift_flags.append("%s line %s story %d: Cd*dr/Ie = %.4f > %.3f"
                                       % (dirn, name, k, amp, dl))
        gate = WL.compare_with_model(
            dist, {k: {ln.name: dres[ln.name][k]["V"] - (dres[ln.name][k + 1]["V"]
                       if (k + 1) in dres[ln.name] else 0.0) for ln in lines}
                   for k in ed["Fx"]})
        res["directions"][dirn] = dict(dist=dist, lines=dres, drift_flags=drift_flags,
                                       gate_flags=gate,
                                       # footnote a (no limit): 1.0 keeps consumers numeric
                                       drift_limit=dl if dl is not None else 1.0,
                                       drift_limit_none=dl is None,
                                       drift_limit_basis=dl_basis,
                                       diaphragm="semi-rigid" if semirigid else "flexible",
                                       system=sd["system"], seis=sd["seis"],
                                       line_systems=({nm: v[0] for nm, v in
                                                      sd["per_line"].items()}
                                                     if sd["per_line"] else None),
                                       elf=ed)
    return res


# ---------------- semi-rigid diaphragm (Stage 2c: coupling elements) ----------------

def _gauss(A, b):
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            raise RuntimeError("singular diaphragm system")
        M[col], M[piv] = M[piv], M[col]
        for r in range(col + 1, n):
            f = M[r][col] / M[col][col]
            for cc in range(col, n + 1):
                M[r][cc] -= f * M[col][cc]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (M[i][n] - sum(M[i][j] * x[j] for j in range(i + 1, n))) / M[i][i]
    return x


def analyze_semirigid(cfg, lines, dist, depth_ft, n_iter=3):
    """Stage 2c semi-rigid path: wall-line secant springs COUPLED by story-level diaphragm
    shear springs between adjacent lines (K_d = G'_dia x depth / gap). The solved system
    REDISTRIBUTES story forces by relative stiffness -- this is where the model-vs-tributary
    gate becomes a real check instead of an identity. cfg['diaphragm_G_kip_in'] = effective
    diaphragm shear stiffness G' (kip/in per ft of depth; agent grounds the value -- panel
    type/fastening -- in S240/manufacturer data and states it in the report).
    Returns {line_name: {story: dict(V, v_unit_plf, K_kip_in, drift_in, dr_ratio, T_kip)}}."""
    N = cfg["stories"]
    Gp = cfg.get("diaphragm_G_kip_in", 8.0)
    order = sorted(lines, key=lambda ln: ln.pos)
    stories = list(range(1, N + 1))
    idx = {(ln.name, k): li * N + (k - 1) for li, ln in enumerate(order) for k in stories}
    n = len(order) * N
    # initial wall spring stiffnesses from the tributary state
    Kw = {}
    ot_by_line = {}
    for ln in order:
        shears = {k: dist[k][ln.name]["V_shifted"] for k in stories}
        lr = analyze_line(cfg, ln, shears)
        ot_by_line[ln.name] = lr
        for k in stories:
            Kw[(ln.name, k)] = lr[k]["K_kip_in"] or 1e-6
    out = None
    for _ in range(n_iter):
        K = [[0.0] * n for _ in range(n)]
        F = [0.0] * n
        for li, ln in enumerate(order):
            for k in stories:
                i = idx[(ln.name, k)]
                kw = Kw[(ln.name, k)]
                K[i][i] += kw
                if k > 1:
                    j = idx[(ln.name, k - 1)]
                    K[j][j] += kw
                    K[i][j] -= kw
                    K[j][i] -= kw
                F[i] += dist[k][ln.name]["V_shifted"]
        for a, b in zip(order, order[1:]):
            gap = abs(b.pos - a.pos)
            if gap < 1e-6:
                continue
            kd = Gp * depth_ft / gap
            for k in stories:
                i, j = idx[(a.name, k)], idx[(b.name, k)]
                K[i][i] += kd; K[j][j] += kd
                K[i][j] -= kd; K[j][i] -= kd
        u = _gauss(K, F)
        # recover wall spring shears, update secants
        out = {}
        for ln in order:
            Vsp = {}
            for k in stories:
                du = u[idx[(ln.name, k)]] - (u[idx[(ln.name, k - 1)]] if k > 1 else 0.0)
                Vsp[k] = Kw[(ln.name, k)] * du
            # per-story force introduced at each level (for OT/tension + the gate)
            lvl = {k: Vsp[k] - Vsp.get(k + 1, 0.0) for k in stories}
            dist_like = {k: {ln.name: dict(V_shifted=lvl[k])} for k in stories}
            ot = WL.overturning_stack(ln, dist_like,
                                      {k: cfg["heights_ft"][k - 1] for k in stories})
            res_line = {}
            for k in stories:
                L = max(ln.length(k), 1e-6)
                v = max(Vsp[k], 0.0) * 1000.0 / L
                Knew = line_secant_stiffness(cfg, ln, k, max(v, 1.0), ot[k]["T_kip"])
                if Knew:
                    Kw[(ln.name, k)] = Knew
                h_in = cfg["heights_ft"][k - 1] * 12.0
                dr = abs(Vsp[k]) / Kw[(ln.name, k)]
                res_line[k] = dict(V=Vsp[k], v_unit_plf=v, K_kip_in=Kw[(ln.name, k)],
                                   drift_in=dr, dr_ratio=dr / h_in, T_kip=ot[k]["T_kip"])
            out[ln.name] = res_line
    return out


# ---------------- two-stage podium (ASCE 7-22 12.2.3.2) ----------------

def two_stage_check(K_upper_kip_in, K_lower_kip_in, T_upper_s, T_combined_s):
    """Eligibility: lower portion stiffness >= 10x upper; combined period <= 1.1x upper.
    Returns (eligible, messages)."""
    msgs = []
    ok1 = K_lower_kip_in >= 10.0 * K_upper_kip_in
    ok2 = T_combined_s <= 1.1 * T_upper_s
    if not ok1:
        msgs.append("lower/upper stiffness ratio %.1f < 10 -- two-stage NOT eligible"
                    % (K_lower_kip_in / max(K_upper_kip_in, 1e-9)))
    if not ok2:
        msgs.append("combined period %.3fs > 1.1x upper %.3fs -- two-stage NOT eligible"
                    % (T_combined_s, T_upper_s))
    return ok1 and ok2, msgs


def two_stage_reactions(V_upper, R_upper, rho_upper, R_lower, rho_lower):
    """Upper-portion base reactions amplified for the lower-portion design:
    factor = (R_upper/rho_upper)/(R_lower/rho_lower), not less than 1.0."""
    f = (R_upper / rho_upper) / (R_lower / rho_lower)
    return V_upper * max(f, 1.0), max(f, 1.0)


# ---------------- OpenSees emitter (pipeline path; guarded) ----------------

def emit_opensees(cfg, result, direction="X"):
    """Build the SAME calibrated spring stacks in openseespy (zeroLength + Elastic materials),
    one 2D stick per wall line, masses by tributary. Requires openseespy (pipeline/user env).
    Returns node/element counts for the smoke check."""
    if not HAVE_OPS:
        raise RuntimeError("openseespy not available here -- emit_opensees runs in the "
                           "pipeline/user environment (Gate 2)")
    lines = cfg["lines_x"] if direction == "X" else cfg["lines_y"]
    dres = result["directions"][direction]["lines"]
    ops.wipe(); ops.model("basic", "-ndm", 1, "-ndf", 1)
    nid = mid = eid = 0
    counts = dict(nodes=0, elements=0)
    node_map = {}                                   # line name -> {story: node tag}
    for ln in lines:
        nid += 1; base = nid; ops.node(base, 0.0); ops.fix(base, 1); counts["nodes"] += 1
        prev = base
        node_map[ln.name] = {}
        for k in sorted(dres[ln.name]):
            r = dres[ln.name][k]
            nid += 1; ops.node(nid, 0.0); counts["nodes"] += 1
            mid += 1; ops.uniaxialMaterial("Elastic", mid, r["K_kip_in"])
            eid += 1; ops.element("zeroLength", eid, prev, nid, "-mat", mid, "-dir", 1)
            counts["elements"] += 1
            node_map[ln.name][k] = nid
            prev = nid
    counts["node_map"] = node_map
    return counts


# ---------------- self-test ----------------

def _selftest():
    print("cfs_engine self-test (openseespy %s)" % ("available" if HAVE_OPS else "absent -- pure-python path"))
    segs = {k: [(20.0, 9.5), (10.0, 9.5)] for k in (1, 2, 3, 4)}
    segsB = {k: [(15.0, 9.5)] for k in (1, 2, 3, 4)}
    cfg = dict(stories=4, heights_ft=[10.67, 9.5, 9.5, 9.5], plan_ft=(160.0, 62.0),
               D_floor=35.0, D_roof=20.0, clad=15.0, snow=0.0,
               seis=CS.seis_cfs(1.0, 0.45, 0.45, "wsp_shearwall"),
               system="wsp_shearwall", risk_cat="II", structure_kind="wall",
               analysis_fidelity=0, diaphragm="flexible",
               lines_x=[WL.WallLine("X1", 0.0, segs), WL.WallLine("X2", 31.0, segsB),
                        WL.WallLine("X3", 62.0, segs)],
               lines_y=[WL.WallLine("Y1", 0.0, segsB), WL.WallLine("Y2", 80.0, segsB),
                        WL.WallLine("Y3", 160.0, segsB)],
               wall_props=dict(chord_area_in2=1.2, Gp_kip_in=9.0, en_in=0.03,
                               k_anchor_kip_in=50.0))
    e = elf(cfg)
    assert e["W"] > 0 and abs(sum(e["Fx"].values()) - e["V"]) < 1e-9
    print("  ELF: W=%.0f kip, Cs=%.3f, V=%.1f kip, Ta=%.3fs" % (e["W"], e["Cs"], e["V"], e["Ta"]))
    # Ex1-like hand check: Cs = SDS/(R/Ie) = 1.0/6.5 = 0.154 unless the SD1 cap bites
    assert abs(e["Cs"] - min(1.0 / 6.5, 0.45 / (e["T_used"] * 6.5))) < 1e-6
    res = run(cfg)
    assert "preflight_warnings" not in res
    dx = res["directions"]["X"]
    assert dx["gate_flags"] == [], dx["gate_flags"]
    r11 = dx["lines"]["X1"][1]
    print("  X1 story1: Vcum=%.1f kip, v=%.0f plf, K=%.1f kip/in, Cd*dr/Ie=%.4f (limit %.3f)"
          % (r11["V"], r11["v_unit_plf"], r11["K_kip_in"], r11["drift_amplified"],
             dx["drift_limit"]))
    assert r11["V"] >= dx["lines"]["X1"][4]["V"], "cumulative shear must grow downward"
    assert dx["drift_limit"] == 0.025
    # two-stage machinery
    ok, msgs = two_stage_check(100.0, 1500.0, 0.4, 0.42)
    assert ok and not msgs
    ok, msgs = two_stage_check(100.0, 500.0, 0.4, 0.42)
    assert not ok and "NOT eligible" in msgs[0]
    Vamp, f = two_stage_reactions(100.0, 6.5, 1.0, 3.0, 1.0)
    assert abs(f - 6.5 / 3.0) < 1e-9 and abs(Vamp - 216.7) < 0.1
    print("  two-stage: eligibility + reaction amplification (f=%.2f) OK" % f)
    # Stage 2c: semi-rigid diaphragm redistribution + equilibrium + gate behavior
    cfg_sr = dict(cfg, diaphragm="semi-rigid", diaphragm_G_kip_in=1e-6)
    res_sr = run(cfg_sr)                                   # nearly-flexible: matches tributary
    dxs = res_sr["directions"]["X"]
    assert dxs["diaphragm"] == "semi-rigid"
    assert dxs["gate_flags"] == [], "G'~0 must reproduce the tributary distribution: %s" \
        % dxs["gate_flags"]
    cfg_sr2 = dict(cfg, diaphragm="semi-rigid", diaphragm_G_kip_in=500.0)
    res_sr2 = run(cfg_sr2)                                 # stiff: redistribution by stiffness
    dxs2 = res_sr2["directions"]["X"]
    Vtot1 = sum(dxs2["lines"][nm][1]["V"] for nm in ("X1", "X2", "X3"))
    Vapplied = sum(dxs2["dist"][k][nm]["V_shifted"] for k in (1, 2, 3, 4)
                   for nm in ("X1", "X2", "X3"))        # shifted envelope sums to ~1.05V
    assert abs(Vtot1 - Vapplied) / Vapplied < 1e-6, \
        "semi-rigid story-1 line shears must sum to the applied (shifted) base shear"
    trib_X2 = dxs2["dist"][1]["X2"]["V_shifted"] + dxs2["dist"][2]["X2"]["V_shifted"] + \
        dxs2["dist"][3]["X2"]["V_shifted"] + dxs2["dist"][4]["X2"]["V_shifted"]
    assert dxs2["lines"]["X2"][1]["V"] < trib_X2, \
        "stiff diaphragm must shed load AWAY from the weak line X2"
    assert dxs2["gate_flags"], "strong redistribution must trip the model-vs-tributary gate"
    print("  semi-rigid: G'~0 matches tributary (gate clean); stiff G' redistributes off the "
          "weak line and TRIPS the gate (%d flags) with equilibrium held" %
          len(dxs2["gate_flags"]))
    # tier misuse trips preflight
    cfg2 = dict(cfg, structure_kind="portal_singlechannel")
    res2 = run(cfg2)
    assert res2.get("preflight_warnings"), "single-channel portal @ Tier 0 must warn"
    print("  preflight: single-channel portal @ Tier 0 warns as required")
    if HAVE_OPS:
        c = emit_opensees(cfg, res, "X")
        print("  opensees emitter: %(nodes)d nodes / %(elements)d elements" % c)
        # solve the emitted model under the tributary story shears and compare story drifts
        # against the pure-python chain -- the dual-path equivalence check
        dist = res["directions"]["X"]["dist"]
        ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
        for ln in cfg["lines_x"]:
            for k, nd in c["node_map"][ln.name].items():
                ops.load(nd, dist[k][ln.name]["V_shifted"])
        ops.system("BandGeneral"); ops.numberer("RCM"); ops.constraints("Plain")
        ops.integrator("LoadControl", 1.0); ops.algorithm("Linear"); ops.analysis("Static")
        assert ops.analyze(1) == 0, "emitted model failed to solve"
        worst = 0.0
        for ln in cfg["lines_x"]:
            nm = c["node_map"][ln.name]
            prev_d = 0.0
            for k in sorted(nm):
                d = ops.nodeDisp(nm[k], 1)
                drift_ops = d - prev_d; prev_d = d
                drift_py = res["directions"]["X"]["lines"][ln.name][k]["drift_in"]
                if drift_py > 1e-9:
                    worst = max(worst, abs(drift_ops - drift_py) / drift_py)
        assert worst < 0.01, "OpenSees vs pure-python drift mismatch %.2f%%" % (worst * 100)
        print("  dual-path equivalence: OpenSees drifts match pure-python within %.3f%%"
              % (worst * 100))
    print("SELF-TEST PASS")


if __name__ == "__main__":
    _selftest()

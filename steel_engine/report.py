"""
report.py  --  one self-contained HTML engineer's report per building (CFS fork).

A senior engineer can follow the whole job: building summary + wall plan / frame model,
loads & distribution, drift/P-Delta, reactions, per-line (or per-member) demands, the CFS
design schedules (stud/track, sheathing + fastener, chord/hold-down/rod, collectors), and
Appendix A — a fully referenced AISI S100/S240/S400 design-calculation record for every
checked item.

Math is rendered with MathJax; figures are written to figs/ and referenced (keeps the .html small).

Run (operator, Linux/WSL where openseespy runs):
    python report.py B07
    python report.py T06            # auto-registers test_cfgs
    python report.py T01 T06 B02
"""
import os, sys, math, re, base64, io, json, datetime
import html as _html
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "engine"))
sys.path.insert(0, os.path.join(HERE, "eval_tests", "answer_key"))
# openseespy/engine3d are needed only by the FRAME-path chapters (build_report). The CFS
# wall/portal renderer (build_report_cfs) is pure python -- guard so it imports anywhere.
try:
    import openseespy.opensees as ops
    import engine3d as E
    HAVE_OPS = True
except Exception:                     # no openseespy (dev sandbox): CFS rendering still works
    ops = E = None
    HAVE_OPS = False
import sections as S
try:
    import design_post as DPOST      # run_case + capacity snippets (operator side; needs E)
except Exception:
    DPOST = None
try:
    import design_pipeline as PIPE   # combos() — the ASCE 7 load-case list
    HAVE_PIPE = True
except Exception:
    HAVE_PIPE = False

Fy, Emod = 50.0, 29500.0     # AISI: E = 29,500 ksi (Fy 33 or 50 by mil; 50 default)
g = E.g if E else 386.4

# ============================================================ small utilities
def _register(name):
    if name not in E.CFG:
        # re-render path: a fresh run_python process won't have the cfg registered -- load jobs/<name>/cfg.py
        _base = os.environ.get("STEEL_BUILDER_JOBS") or HERE
        _cfgfile = os.path.join(_base, name, "cfg.py")
        if os.path.exists(_cfgfile):
            try:
                import importlib.util as _ilu
                _spec = _ilu.spec_from_file_location("jobcfg_" + name, _cfgfile)
                _m = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_m)
                if hasattr(_m, "cfg"): E.CFG[name] = _m.cfg
            except Exception:
                pass
    if name not in E.CFG:
        try: import test_cfgs  # noqa: F401
        except Exception: pass
    if name not in E.CFG:
        raise SystemExit(f"unknown building '{name}' -- register its cfg or write jobs/{name}/cfg.py first")

# Figures are written to <root>/figs/ and referenced by relative path (set in build_report).
_FIGDIR = None          # absolute path of the current report's figs/ folder
_FIGSEQ = [0]           # running counter for auto-named inline figures

def _b64(fig):
    """Save a matplotlib figure to figs/ and return its relative path (data-URI fallback if no figdir).
    Uses tight_layout (O(subplots)) instead of bbox_inches='tight' (O(artists)) so a frame figure with
    thousands of annotations encodes in milliseconds, not tens of seconds (P8)."""
    try: fig.tight_layout()
    except Exception: pass
    if _FIGDIR:
        _FIGSEQ[0] += 1; fn = "auto_%03d.png" % _FIGSEQ[0]
        fig.savefig(os.path.join(_FIGDIR, fn), format="png", dpi=130)
        plt.close(fig); return "figs/" + fn
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=130)
    plt.close(fig); buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")

def _png_file_b64(path):
    """Reference an existing PNG (already in figs/) by relative path; data-URI fallback if no figdir."""
    if not os.path.exists(path): return None
    if _FIGDIR:
        return "figs/" + os.path.basename(path)
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")

def _materialize(uri):
    """Turn any leftover data:image/...;base64 URI (e.g. from viz3d/frame_diagram) into a figs/ file."""
    if _FIGDIR and isinstance(uri, str) and uri.startswith("data:image/"):
        try:
            head, b64 = uri.split(",", 1)
            ext = head.split("/")[1].split(";")[0].replace("jpeg", "jpg").replace("svg+xml", "svg")
            _FIGSEQ[0] += 1; fn = "auto_%03d.%s" % (_FIGSEQ[0], ext)
            with open(os.path.join(_FIGDIR, fn), "wb") as f: f.write(base64.b64decode(b64))
            return "figs/" + fn
        except Exception:
            return uri
    return uri

def _decode(tag):
    k = tag // 100000; r = tag % 100000; return r // 100, r % 100, k

def _loc(n1, n2):
    """Human-readable member location: grid (i,j) and building level k."""
    i1, j1, k1 = _decode(n1); i2, j2, k2 = _decode(n2)
    if (i1, j1) == (i2, j2):                       # vertical: column
        return f"({i1},{j1}) level {k1}→{k2}"
    return f"({i1},{j1})→({i2},{j2}) level {k1}"

def _table(headers, rows, cls=""):
    h = "".join(f"<th>{c}</th>" for c in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table class='{cls}'><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"

def _calc(title, rows, note=""):
    """Engineering calc-sheet block: a titled worksheet table whose rows are
    (quantity, calculation, value, code-reference) — one calc step per line."""
    n = f"<div class='cnote'><i>{note}</i></div>" if note else ""
    body = "".join(f"<tr><td>{q}</td><td>{c}</td><td class='v'>{v}</td><td class='r'>{r}</td></tr>"
                   for (q, c, v, r) in rows)
    return (f"<h5>{title}</h5>{n}<table class='calc'>"
            "<tr class='hd'><th>Quantity</th><th>Calculation</th><th>Value</th><th>Ref. (code)</th></tr>"
            + body + "</table>")

def _img(uri, caption, full=False):
    cls = "full" if full else ""
    if not uri: return f"<p class='note'>[{caption}: not available]</p>"
    if isinstance(uri, str) and uri.startswith("ERROR"):
        return f"<p class='note'>[{caption}: {uri}]</p>"
    uri = _materialize(uri)
    return f"<figure class='{cls}'><img src='{uri}'/><figcaption>@@FIGNUM@@ {caption}</figcaption></figure>"

def _fmt(d):
    if not isinstance(d, dict): return str(d)
    return "; ".join(f"{k}={v}" for k, v in d.items())

# ============================================================ analysis helpers
def _seismic(cfg):
    NF = len(cfg["heights"])
    T, w2, eX, eY, Mtot = E.modal(cfg, min(3*NF, 16))
    Cs, V, Tu, Ta, kk, Fx, W = E.elf(cfg, T[0])
    return T, eX, eY, Cs, V, Tu, Ta, Fx, W

def _run_case(cfg, direction, Fx, accidental=False):
    """Seismic ELF case in `direction`; leaves model live. Returns info, disp, drift, reactions."""
    info = E.build(cfg, "PDelta"); NF = info["NF"]; di = 0 if direction == "X" else 1
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    fD = 1.2 + 0.2*cfg["seis"]["SDS"]
    for k in range(1, NF+1):
        pts = info["present"][k]; p = fD*E.floor_grav(cfg, k)/len(pts)
        for (i, j) in pts: ops.load(E.ntag(i, j, k), 0, 0, -p, 0, 0, 0)
    SX, SY = cfg["SX"], cfg["SY"]
    for k in range(1, NF+1):
        f = [0.0]*6; f[di] = Fx[k]
        if accidental:
            pts = info["present"][k]; xs = [i*SX for i, j in pts]; ys = [j*SY for i, j in pts]
            B = (max(ys)-min(ys)+SY) if direction == "X" else (max(xs)-min(xs)+SX)
            f[5] = Fx[k]*0.05*B
        ops.load(E.mtag(k), *f)
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-7, 200); ops.algorithm("Newton")
    ops.integrator("LoadControl", 1.0); ops.analysis("Static"); ops.analyze(1)
    disp = {k: ops.nodeDisp(E.mtag(k), di+1) for k in range(1, NF+1)}
    drift = []; prev = 0.0
    for k in range(1, NF+1):
        drift.append((disp[k]-prev)/cfg["heights"][k-1]); prev = disp[k]
    ops.reactions()
    react = [(i, j, [ops.nodeReaction(E.ntag(i, j, 0), d) for d in (1, 2, 3, 4, 5, 6)])
             for (i, j) in info["present"][0]]
    return info, disp, drift, react

# ============================================================ figures
def fig_plan(cfg):
    """2D plan: column grid with (i,j) labels — explains the grid used in the reaction tables."""
    NX, NY = cfg["NX"], cfg["NY"]; SX, SY = cfg["SX"], cfg["SY"]
    xco = cfg.get("xcoords"); yco = cfg.get("ycoords"); skew = cfg.get("skew", 0.0)
    def XY(i, j): return ((xco[i] if xco else i*SX)+skew*j, (yco[j] if yco else j*SY))
    pres0 = set(cfg_present(cfg, 1))
    Lx = (xco[-1] if xco else NX*SX); Ly = (yco[-1] if yco else NY*SY)
    fig, ax = plt.subplots(figsize=(9, 9*max(Ly, 1)/max(Lx, 1) + 1))
    for i in range(NX):                            # shade framed bays -> non-rectangular footprints are visible
        for j in range(NY):
            if all(p in pres0 for p in [(i, j), (i+1, j), (i, j+1), (i+1, j+1)]):
                bx = [XY(i, j)[0], XY(i+1, j)[0], XY(i+1, j+1)[0], XY(i, j+1)[0]]
                by = [XY(i, j)[1], XY(i+1, j)[1], XY(i+1, j+1)[1], XY(i, j+1)[1]]
                ax.fill(bx, by, color="#eef3fb", zorder=0)
    for i in range(NX+1):                          # grid lines
        x0, _ = XY(i, 0); x1, y1 = XY(i, NY); ax.plot([XY(i,0)[0], XY(i,NY)[0]], [XY(i,0)[1], XY(i,NY)[1]], color="#ccc", lw=0.8)
    for j in range(NY+1):
        ax.plot([XY(0,j)[0], XY(NX,j)[0]], [XY(0,j)[1], XY(NX,j)[1]], color="#ccc", lw=0.8)
    for i in range(NX+1):
        for j in range(NY+1):
            x, y = XY(i, j)
            present = (i, j) in pres0
            ax.plot([x], [y], marker=("s" if present else "x"), ms=5, ls="none", color="black" if present else "#bbb")
            ax.annotate(f"({i},{j})", (x, y), textcoords="offset points", xytext=(4, 4), fontsize=7)
    ax.set_aspect("equal"); ax.set_xlabel("X (in)  —  i index"); ax.set_ylabel("Y (in)  —  j index")
    ax.set_title(f"Plan grid — column lines labelled (i, j); shaded = framed bays (level 1); × = no column")
    ax.grid(False)
    return _b64(fig)

def cfg_present(cfg, k):
    """present column (i,j) at floor k (handles plan-shape callables)."""
    try:
        return E.grid(cfg, k)
    except Exception:
        return [(i, j) for i in range(cfg["NX"]+1) for j in range(cfg["NY"]+1)]

def fig_mode_3d(cfg, mode, nmodes):
    """Full-frame 3D mode shape (undeformed grey + modal-deformed colour), true proportions.
    REUSES the cached modal eigenvector field (engine3d.mode_shapes) -- no ops.eigen re-solve."""
    ms = E.mode_shapes(cfg, max(nmodes, mode))
    nodes = ms["coords"]; evall = ms["ev"]; ele = ms["ele"]; zlev = ms["z"]
    ev = {t: (evall[t][mode-1] if (t in evall and len(evall[t]) >= mode) else [0,0,0,0,0,0]) for t in nodes}
    amp = max((abs(ev[t][0])+abs(ev[t][1]) for t in ev), default=1.0) or 1.0
    zmax = (max(zlev.values()) if isinstance(zlev, dict) else zlev[-1])
    sc = 0.12*zmax/amp
    fig = plt.figure(figsize=(11, 8)); ax = fig.add_subplot(111, projection="3d")
    for (et, kind, sec, n1, n2) in ele:
        if n1 not in nodes or n2 not in nodes: continue
        a, b = nodes[n1], nodes[n2]
        ax.plot([a[0], b[0]], [a[1], b[1]], [a[2], b[2]], color="0.8", lw=0.6)
        da = [a[i]+sc*ev[n1][i] for i in range(3)]; db = [b[i]+sc*ev[n2][i] for i in range(3)]
        ax.plot([da[0], db[0]], [da[1], db[1]], [da[2], db[2]], color="#2c5aa0", lw=1.3)
    ax.set_xlabel("X (in)"); ax.set_ylabel("Y (in)"); ax.set_zlabel("Z (in)")
    try:
        xr = ax.get_xlim3d(); yr = ax.get_ylim3d(); zr = ax.get_zlim3d()
        ax.set_box_aspect((xr[1]-xr[0], yr[1]-yr[0], zr[1]-zr[0]))
    except Exception: pass
    return _b64(fig)

def _elevation_from_live(cfg, direction, title):
    """Draw N/V/M for the perimeter frame line of `direction` from the CURRENTLY LIVE model
    (call right after a case has been analysed). Returns data-uri or None."""
    onln = (lambda i, j: j == 0) if direction == "X" else (lambda i, j: i == 0)
    al = (lambda n: ops.nodeCoord(n)[0]) if direction == "X" else (lambda n: ops.nodeCoord(n)[1])
    zo = lambda n: ops.nodeCoord(n)[2]
    mem = []
    for (t, kind, sec, n1, n2) in _LIVE_ELE:
        i1, j1, _ = _decode(n1); i2, j2, _ = _decode(n2)
        if onln(i1, j1) and onln(i2, j2): mem.append((t, kind, n1, n2))
    if not mem: return None
    fig, axes = plt.subplots(1, 3, figsize=(16, 6))
    for ax, which, ttl, col in zip(axes, ("N", "V", "M"),
                                   ("Axial N (kip)", "Shear V (kip)", "Moment M (k-ft)"),
                                   ("#1f77b4", "#2ca02c", "#d62728")):
        data = []; mx = 1e-9
        for (t, kind, n1, n2) in mem:
            bf = ops.basicForce(t)
            if kind == "brace":
                e1 = e2 = (bf[0] if which == "N" else 0.0)
            else:
                L = math.dist(ops.nodeCoord(n1), ops.nodeCoord(n2))
                if which == "N":   e1 = e2 = bf[0]
                elif which == "V": e1 = e2 = (abs(bf[1])+abs(bf[2]))/L
                else:              e1 = bf[1]/12.0; e2 = -bf[2]/12.0
            data.append((kind, n1, n2, e1, e2)); mx = max(mx, abs(e1), abs(e2))
        sc = (0.30*min(cfg["SX"], cfg["SY"]))/mx
        peak = 0.0; peaklab = None
        for kind, n1, n2, e1, e2 in data:
            a1, z1 = al(n1), zo(n1); a2, z2 = al(n2), zo(n2)
            ax.plot([a1, a2], [z1, z2], color="#bbb", lw=1.0, zorder=1)
            if abs(z2-z1) > abs(a2-a1):
                ax.plot([a1+e1*sc, a2+e2*sc], [z1, z2], color=col, lw=1.2)
                ax.plot([a1, a1+e1*sc], [z1, z1], color=col, lw=0.5)
                ax.plot([a2, a2+e2*sc], [z2, z2], color=col, lw=0.5)
            else:
                ax.plot([a1, a2], [z1+e1*sc, z2+e2*sc], color=col, lw=1.2)
                ax.plot([a1, a1], [z1, z1+e1*sc], color=col, lw=0.5)
                ax.plot([a2, a2], [z2, z2+e2*sc], color=col, lw=0.5)
            if max(abs(e1), abs(e2)) > peak:
                peak = max(abs(e1), abs(e2)); peaklab = (a1, z1, max(e1, e2, key=abs))
        if peaklab:
            ax.annotate(f"max {peak:.0f}", (peaklab[0], peaklab[1]), fontsize=8, color=col,
                        fontweight="bold")
        ax.set_title(f"{ttl}   (peak {peak:.0f})"); ax.set_xlabel("along (in)"); ax.set_ylabel("Z (in)")
        ax.set_aspect("equal", "datalim"); ax.grid(alpha=0.2)
    fig.suptitle(title)
    return _b64(fig)

_LIVE_ELE = []   # element registry of the currently-live model (set by run helpers)

def fig_drift_profile(cfg, driftX, driftY):
    NF = len(cfg["heights"]); Cd = cfg["seis"].get("Cd", 5.0); Ie = cfg["seis"]["Ie"]
    lvl = list(range(1, NF+1))
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.plot([d*100 for d in driftX], lvl, "-o", label=r"$\delta_e$ X")
    ax.plot([d*Cd/Ie*100 for d in driftX], lvl, "-o", label=r"$\delta=C_d\delta_e/I_e$ X")
    ax.plot([d*100 for d in driftY], lvl, "-s", label=r"$\delta_e$ Y")
    ax.plot([d*Cd/Ie*100 for d in driftY], lvl, "-s", label=r"$\delta$ Y")
    lim = cfg.get("drift_limit", 0.02)*100
    ax.axvline(lim, color="r", ls="--", label=f"limit {lim:.1f}%")
    ax.set_xlabel("interstory drift (%)"); ax.set_ylabel("story"); ax.set_title("Drift profile")
    ax.legend(fontsize=8); ax.grid(alpha=0.3)
    return _b64(fig)

def fig_story_shear_otm(cfg, Fx):
    NF = len(cfg["heights"]); z = E.zlevels(cfg)
    Vstory = [sum(Fx[k] for k in range(s, NF+1)) for s in range(1, NF+1)]
    OTM = [sum(Fx[k]*(z[k]-z[s-1])/12.0 for k in range(s, NF+1)) for s in range(1, NF+1)]
    OTM_base = sum(Fx[k]*z[k]/12.0 for k in range(1, NF+1))
    lvl = list(range(1, NF+1))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 5))
    a1.step(Vstory, lvl, where="mid"); a1.set_xlabel("story shear (kip)"); a1.set_ylabel("story"); a1.grid(alpha=0.3); a1.set_title("Story shear")
    a2.plot(OTM, lvl, "-o"); a2.set_xlabel("overturning moment (k-ft)"); a2.set_title("OTM (above story)"); a2.grid(alpha=0.3)
    return _b64(fig), Vstory, OTM_base

# ============================================================ per-load-case forces
def load_cases(cfg):
    """ASCE 7-22 LRFD combinations considered (label, fD, fL, fLr, lat, col_only)."""
    if HAVE_PIPE:
        try: return PIPE.combos(cfg)
        except Exception: pass
    # minimal fallback if design_pipeline unavailable
    return [("1.4D", 1.4, 0, 0, {}, False), ("1.2D+1.6L+0.5Lr", 1.2, 1.6, 0.5, {}, False)]

def case_forces(cfg, fD, fL, fLr, lat):
    """Run one combination through P-Delta; return (out dict, registry). Leaves model live."""
    out, info = DPOST.run_case(cfg, fD, fL, fLr, lat)
    global _LIVE_ELE; _LIVE_ELE = info["ele"]
    return out, info

def per_case_max_table(info):
    """Per member TYPE, the governing (max) member + its forces + location, read from basicForce —
    the SAME convention the elevation diagrams use, so table and figure magnitudes agree."""
    best = {}   # (kind,sec) -> (score, N, Mz, My, V, n1, n2)
    for (t, kind, sec, n1, n2) in info["ele"]:
        bf = ops.basicForce(t)
        if kind == "brace":
            N = bf[0]; Mz = My = V = 0.0; score = abs(N)
        else:
            L = math.dist(ops.nodeCoord(n1), ops.nodeCoord(n2))
            N = bf[0]; Mz = max(abs(bf[1]), abs(bf[2])); My = max(abs(bf[3]), abs(bf[4]))
            V = max((abs(bf[1])+abs(bf[2]))/L, (abs(bf[3])+abs(bf[4]))/L); score = max(abs(Mz), abs(N))
        key = (kind, sec)
        if key not in best or score > best[key][0]:
            best[key] = (score, N, Mz, My, V, n1, n2)
    rows = []
    for (kind, sec), (sc, N, Mz, My, V, n1, n2) in sorted(best.items()):
        rows.append([kind, sec, f"{N:.1f}", f"{Mz/12:.1f}", f"{My/12:.1f}", f"{V:.1f}", _loc(n1, n2)])
    return rows

def _case_desc(label, col_only):
    """Plain-English description of an ASCE 7-22 combination label."""
    L = label
    if ("WX" in L or "WY" in L) and "E" not in L.replace("Lr", "").replace("Le", ""):
        d = "X (E–W)" if "WX" in L else "Y (N–S)"
        upl = " (uplift / overturning case, 0.9D)" if L.startswith("0.9") else ""
        return f"Wind acting in the {d} direction, combined with gravity{upl}."
    if "Om0" in L:
        d = "X (E–W)" if "EX" in L else "Y (N–S)"
        upl = " on the 0.9D (uplift) gravity case" if L.startswith("(0.9") else ""
        return (f"Capacity-design (overstrength, \\(\\Omega_0\\)) seismic in the {d} direction{upl} — applied "
                "to the capacity-protected elements only, so they remain elastic while the fuse yields (AISI S400 / "
                "ASCE 7 §12.4.3).")
    if "rhoE" in L:
        d = "X (E–W)" if "EX" in L else "Y (N–S)"
        sense = "positive" if ("EX+" in L or "EY+" in L) else "negative"
        tor = ("with +5% accidental torsion" if "t+" in L else
               ("with −5% accidental torsion" if "t-" in L else "no accidental torsion"))
        grav = ("the reduced 0.9D (uplift) gravity case" if L.startswith("(0.9")
                else "the (1.2+0.2·S_DS)D + 0.5L gravity case")
        return f"Code-level seismic in the {d} direction ({sense} sense, {tor}), combined with {grav}."
    if L.strip() == "1.4D":
        return "Dead load only — the basic gravity check."
    if "1.6L" in L:
        return "Gravity only: dead load plus full floor live load and roof live load."
    return "Load combination."

def _lat_dir(lat):
    if not lat: return "X"
    sx = sum(abs(v[0]) for v in lat.values()); sy = sum(abs(v[1]) for v in lat.values())
    return "X" if sx >= sy else "Y"

# ============================================================ Appendix A — design calcs
def _safe_props(section):
    try: return S.props(section, SEC=E.SEC)
    except Exception:
        try: return S.props(section)
        except Exception: return None

def _member_calc_block(m):
    # Render one calc_package member: section inputs + demand envelope + the agent's RAG-derived
    # capacity / limit state / D-C. Framework codes NO capacity; if unfilled, show a 'derive from RAG' note.
    mid = m.get("id", ""); inp = m.get("inputs", {}) or {}
    sec = inp.get("section", ""); kind = inp.get("kind", "")
    h = ["<h4>%s &mdash; %s (%s)</h4>" % (mid, sec, kind)]
    pk = [("A","A (in2)"),("Zx","Zx (in3)"),("Sx","Sx (in3)"),("Zy","Zy (in3)"),("Sy","Sy (in3)"),
          ("rx","rx (in)"),("ry","ry (in)"),("r","r (in)"),("Aw","Aw (in2)"),("J","J (in4)"),
          ("length_in","L (in)"),("Lb_in","Lb (in)")]
    pr = [(lbl, inp[k]) for k, lbl in pk if inp.get(k) is not None]
    if pr:
        h.append("<p class='cnote'>Section properties / geometry (designator-computed gross + S100 App. 1 effective):</p>")
        h.append(_table([l for l, _ in pr], [["%s" % v for _, v in pr]]))
    dem = [("P_comp (kip)", inp.get("P_comp_kip")), ("P_tens (kip)", inp.get("P_tens_kip")),
           ("Mz (kip-in)", inp.get("Mz_kipin")), ("My (kip-in)", inp.get("My_kipin")),
           ("V (kip)", inp.get("V_kip"))]
    h.append("<p class='cnote'>Demand envelope (analysis):</p>")
    h.append(_table([l for l, _ in dem] + ["governing combo"],
                    [["%s" % v for _, v in dem] + [str(inp.get("governing_combo", ""))]]))
    cap = m.get("capacity"); dc = m.get("DC"); ls = m.get("limit_state"); cited = m.get("cited")
    if (isinstance(cap, dict) and cap) or dc is not None or ls or cited:
        rows = []
        if ls: rows.append(("Governing limit state", "selected from AISI S100/S240/S400", str(ls), str(cited or "")))
        if isinstance(cap, dict):
            for k, v in cap.items(): rows.append((k, "", str(v), ""))
        if dc is not None:
            ok = "ok" if (isinstance(dc,(int,float)) and dc <= 1.0) else ("NG" if isinstance(dc,(int,float)) else "")
            rows.append(("D/C", "demand / capacity", "%s %s" % (dc, ok), str(cited or "")))
        h.append(_calc("AISI capacity &amp; D/C - derived by the agent from the RAG", rows))
    else:
        h.append("<p class='note'>Capacity &amp; D/C: <b>to be derived by the agent from the AISI S100 / S240 / S400 "
                 "RAG</b> - the framework computes demands only (no coded capacity). Expected calc_package "
                 "fields: <code>limit_state</code>, <code>cited</code>, <code>capacity</code>, <code>DC</code>.</p>")
    return "".join(h)

def appendix(cfg, name, pkg):
    # Appendix A: per governing member, section inputs + demand envelope + the agent's RAG-derived
    # AISI capacity/limit-state/D-C. The framework codes NO capacity equations.
    h = ["<h3>Member calculations</h3>",
         "<p>For every governing member the framework lists the section properties and the enveloped "
         "demand from the analysis combinations. The AISI capacity, governing limit state, cited "
         "clause, and D/C are <b>derived by the agent from the RAG</b> and shown where recorded in "
         "calc_package.json - the framework computes no capacity.</p>"]
    members = pkg.get("members") if isinstance(pkg, dict) else None
    if isinstance(members, list):
        for m in members:
            if not (m.get("inputs", {}) or {}).get("section"): continue
            try: h.append(_member_calc_block(m))
            except Exception as ex: h.append("<p class='note'>[render failed: %s]</p>" % ex)
    else:
        h.append("<p class='note'>[no members in calc_package.json]</p>")
    cd = pkg.get("connections") or pkg.get("connection_demands")
    if cd:
        h.append("<h3>Connections (AISI S100 Ch. J; S400 capacity design)</h3>")
        items = cd.items() if isinstance(cd, dict) else [(c.get("id", ""), c) for c in cd]
        for cid, c in items:
            dem = _fmt(c.get("demand", c.get("demands", {})))
            comp = c.get("components", c.get("notes", ""))
            cited = c.get("cited", "")
            cdf = " (capacity-design)" if c.get("capacity_design") else ""
            h.append("<h4>%s &mdash; %s%s</h4><p><b>Demand:</b> %s</p><p><b>Components:</b> %s</p><p><b>Cited:</b> %s</p>"
                     % (cid, c.get("type", ""), cdf, dem, comp, cited))
            chks = c.get("checks")
            if isinstance(chks, list) and chks:
                rows = [[k.get("limit_state", ""),
                         ("%.2f" % k["DC"] if isinstance(k.get("DC"), (int, float)) else str(k.get("DC", ""))),
                         "OK" if (isinstance(k.get("DC"), (int, float)) and k["DC"] <= 1.0) else ""] for k in chks]
                h.append(_table(["Limit state", "D/C", "&le;1.0"], rows))
    return "".join(h)

def _num(x):
    try: return float(x)
    except Exception: return 0.0
def _kind_from_id(mid):
    s = (mid or "").lower()
    return "brace" if "brace" in s else ("beam" if "beam" in s else "col")
def _sec_from_id(mid):
    for part in (mid or "").replace("_", "-").split("-"):
        if part and (part[0] in "WHwh") and any(c.isdigit() for c in part): return part.upper()
    return None

# ============================================================ HTML shell
CSS = """body{font-family:Segoe UI,Arial,sans-serif;max-width:1040px;margin:24px auto;color:#222;line-height:1.55}
h1{border-bottom:3px solid #2c5aa0}h2{border-bottom:1px solid #ccc;margin-top:34px;color:#2c5aa0}
h3{color:#2c5aa0;margin-top:22px}h4{margin:18px 0 4px;color:#333}
table{border-collapse:collapse;margin:12px 0;font-size:14px}th,td{border:1px solid #bbb;padding:4px 9px;text-align:right}
th{background:#eef3fb}td:first-child,th:first-child{text-align:left}
figure{display:block;margin:16px 0}img{width:100%;max-width:100%;border:1px solid #ddd}
figcaption{font-size:12px;color:#555;text-align:center}.note{color:#a00;font-style:italic}
pre{background:#f6f8fa;padding:10px;border-radius:6px;overflow:auto;font-size:12px}
h5{margin:16px 0 2px;color:#2c5aa0;font-size:14px}
.cnote{font-size:12px;color:#666;margin:0 0 4px}
table.calc{width:100%;font-size:13px;margin:2px 0 14px}
table.calc td,table.calc th{text-align:left;padding:3px 9px;vertical-align:top}
table.calc td.v{text-align:right;white-space:nowrap;font-weight:600}
table.calc td.r{text-align:right;color:#888;white-space:nowrap;font-size:12px}
table.calc tr.hd th{background:#eef3fb}"""

MATHJAX = ("<script>window.MathJax={tex:{inlineMath:[['\\\\(','\\\\)']],displayMath:[['\\\\[','\\\\]']]}};</script>"
           "<script async src='https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js'></script>")

def _gravity_loads_table(cfg):
    rows = [["Floor dead, D", f"{cfg.get('D_floor','?')} psf"],
            ["Roof dead, D", f"{cfg.get('D_roof','?')} psf"],
            ["Cladding", f"{cfg.get('clad','?')} psf of wall"],
            ["Floor live, L", f"{cfg.get('L_floor','?')} psf"]]
    if cfg.get("Lr"):   rows.append(["Roof live, Lr", f"{cfg['Lr']} psf"])
    if cfg.get("snow"): rows.append(["Snow, S", f"{cfg['snow']} psf"])
    return _table(["Gravity load", "Value"], rows)

def _lateral_inputs_table(cfg):
    s = cfg["seis"]; w = cfg.get("wind", {})
    rows = [["Seismic SDS / SD1", f"{s['SDS']} / {s['SD1']} g"],
            ["R / Cd / Ω0 / Ie", f"{s['R']} / {s.get('Cd')} / {s.get('Om0')} / {s['Ie']}"],
            ["Period coeff Ct / x / Cu", f"{s['Ct']} / {s['x']} / {s['Cu']}"]]
    if w: rows.append(["Wind V / Exposure", f"{w.get('V','?')} mph / {w.get('exposure','C')}"])
    return _table(["Lateral-load input", "Value"], rows)

def _wind_section(cfg):
    """Returns (html, VwX, VwY). VwX/VwY are base shears (None if no wind defined)."""
    if not cfg.get("wind"):
        return "<p class='note'>No wind parameters defined for this building.</p>", None, None
    try:
        w = cfg["wind"]; FX = E.wind_forces(cfg, "X"); FY = E.wind_forces(cfg, "Y")
        VwX = sum(FX.values()); VwY = sum(FY.values()); NF = len(cfg["heights"])
        rows = [[k, f"{FX[k]:.1f}", f"{FY[k]:.1f}"] for k in range(1, NF+1)]
        h = ["<p>ASCE 7-22 §27 MWFRS. Velocity pressure qz = 0.00256·Kz·Kzt·Ke·V² (Eq. 26.10-1); design pressure "
             "p = qz·Kd·G·Cpnet (Kd applied at the pressure equation per Eq. 27.3-1); story force = p × tributary width × tributary height.</p>",
             _table(["Parameter", "Value"], [["Basic wind speed V", f"{w.get('V')} mph"],
                    ["Exposure", w.get("exposure", "C")],
                    ["Kd / G / Cpnet", f"{w.get('Kd',0.85)} / {w.get('G',0.85)} / {w.get('Cpnet',1.3)}"]]),
             "<p><b>Story wind forces (kip):</b></p>",
             _table(["Story", "X (E-W wind)", "Y (N-S wind)"], rows),
             f"<p><b>Wind base shear:</b> X = {VwX:.0f} kip, Y = {VwY:.0f} kip.</p>"]
        return "".join(h), VwX, VwY
    except Exception as ex:
        return f"<p class='note'>[wind determination failed: {ex}]</p>", None, None

def _horizontal_distribution(cfg, Fx, VwX, VwY):
    Vseis = sum(Fx.values()); nframe = 2
    def row(lbl, Vw):
        gov = "seismic" if Vseis >= (Vw or 0) else "wind"; g = max(Vseis, Vw or 0)
        return [lbl, (f"{Vw:.0f}" if Vw is not None else "—"), f"{Vseis:.0f}", gov, f"{g/nframe:.0f}"]
    rows = [row("E-W — two moment frames", VwX), row("N-S — two braced frames", VwY)]
    h = ["<p>The story shear in each direction is shared by the two parallel lateral frames. The "
         "governing base shear (greater of wind and seismic) is split about 50/50 to each frame, plus "
         "±5% accidental torsion (ASCE 7-22 §12.8.4.2) which biases demand toward the leading frame.</p>",
         _table(["Direction / frames", "Wind base (kip)", "Seismic base (kip)", "Governs", "Per frame (kip)"], rows)]
    return "".join(h)

def _role_of(mid, inp):
    r = (inp.get("role") or "").lower().strip().replace(" ", "_")
    _alias = {"roof": "roof", "roof_beam": "roof", "floor": "floor", "floor_beam": "floor",
              "gravity_col": "gravity_col", "interior_col": "gravity_col", "interior_column": "gravity_col",
              "gravity_column": "gravity_col", "lateral_col": "lateral_col", "mf_col": "lateral_col",
              "moment_col": "lateral_col", "braced_col": "lateral_col", "lateral_column": "lateral_col",
              "brace": "brace",
              # CFS roles (wall path + portals)
              "wall": "wall", "shear_wall": "wall", "wall_line": "wall",
              "stud": "stud", "bearing_stud": "stud", "jamb_stud": "stud",
              "chord": "chord", "chord_stud": "chord",
              "track": "track", "joist": "joist", "header": "joist", "rim_track": "track",
              "holddown": "holddown", "hold_down": "holddown", "rod": "holddown",
              "collector": "collector", "drag_strut": "collector",
              "strap": "strap", "purlin": "purlin", "girt": "purlin",
              "rafter": "lateral_col"}
    if r in _alias: return _alias[r]
    s = (mid or "").lower()
    for key, role in (("wall", "wall"), ("chord", "chord"), ("stud", "stud"), ("track", "track"),
                      ("joist", "joist"), ("header", "joist"), ("hd-", "holddown"),
                      ("holddown", "holddown"), ("rod", "holddown"), ("collector", "collector"),
                      ("strap", "strap"), ("purlin", "purlin"), ("girt", "purlin")):
        if key in s: return role
    if "roof" in s: return "roof"
    if "brace" in s or inp.get("kind") == "brace": return "brace"
    if "col" in s or inp.get("kind") == "col":
        return "gravity_col" if ("grav" in s or "interior" in s) else "lateral_col"
    if "floor" in s or "beam" in s or inp.get("kind") == "beam": return "floor"
    return "other"

def _member_section(cfg, name, pkg, want_roles, title, intro):
    h = [f"<h2>{title}</h2>", f"<p>{intro}</p>"]
    members = pkg.get("members") if isinstance(pkg, dict) else None
    Lcol = max(cfg["heights"]); Lbeam = max(cfg["SX"], cfg["SY"])
    Lbrace = math.sqrt(min(cfg["SX"], cfg["SY"])**2 + Lcol**2)
    found = False
    if isinstance(members, list):
        for m in members:
            inp = m.get("inputs", {})
            if _role_of(m.get("id", ""), inp) not in want_roles: continue
            if not inp.get("section"): continue
            try: h.append(_member_calc_block(m)); found = True
            except Exception as ex: h.append(f"<p class='note'>[render failed: {ex}]</p>")
    if not found:
        h.append("<p class='note'>No member with this role is present in calc_package.json — tag each "
                 "member's <code>role</code> (stud / chord / track / joist / strap / wall / collector; frame "
                 "paths: roof / floor / gravity_col / lateral_col / brace); Chapter 6 has a "
                 "section per role.</p>")
    return "".join(h)

def _case_extremes(out, info):
    """Overall max |axial| and max |moment| across all members for one load case."""
    reg = {t: (kind, sec) for (t, kind, sec, n1, n2) in info["ele"]}
    maxN = (0.0, "", ""); maxM = (0.0, "", "")
    for t, (N, Mz, My, V) in out.items():
        kind, sec = reg.get(t, ("", ""))
        if abs(N) > abs(maxN[0]): maxN = (N, kind, sec)
        m = max(abs(Mz), abs(My))
        if m > abs(maxM[0]): maxM = (m, kind, sec)
    return maxN, maxM

def _stability_section(cfg, Fx, drX, pkg):
    """Chapter 7 theta table. Hot-rolled route: the ONE 12.8.7 implementation shared with the
    stability_theta gate (engine3d.theta_rows: Px = D + 0.5(0.4L0 / 0.8L0) unfactored, the
    12.8.6.5 drift location incl. RBS, each direction's ELF shears and Cd/Omega0, theta/(1+theta)
    for the P-Delta analysis, theta_max >= 0.10) -- ported from steltic (hr-report/hr-gates); the
    old local formula (factored (1.2+0.2SDS)D, X drift only, no 0.10 floor) is the fallback."""
    if hasattr(E, "theta_rows"):
        try:
            rows = []; worst = (0.0, None, None)
            for d in ("X", "Y"):
                trs, tmax, beta = E.theta_rows(cfg, d)
                for r in trs:
                    ok = r["exempt"] or r["theta"] <= tmax + 1e-9
                    rows.append([d, r["s"], "%.0f" % r["Px"], "%.0f" % r["Vx"], "%.0f" % r["h"],
                                 "%.4f" % r["raw"], "%.4f" % r["theta"], "%.3f" % tmax,
                                 "exempt" if r["exempt"] else ("OK" if ok else "<b>NG</b>")])
                    if not r["exempt"] and r["theta"] / tmax > (worst[0] / worst[1] if worst[1] else 0):
                        worst = (r["theta"], tmax, d)
            th, tm, dd = worst
            return ("<p>ASCE 7-22 &sect;12.8.7: &theta; = P<sub>x</sub>&Delta;I<sub>e</sub>/(V<sub>x</sub>h<sub>sx</sub>"
                    "C<sub>d</sub>) (Eq. 12.8-18) with P<sub>x</sub> = D + 0.5L (L = 0.4L<sub>0</sub>, 0.8L<sub>0</sub> "
                    "where L<sub>0</sub> &gt; 100 psf; no factor above 1.0), &Delta; at the &sect;12.8.6.5 location, "
                    "each direction's ELF story shear and C<sub>d</sub>; the drifts come from a P-&Delta; analysis, "
                    "so &theta; is divided by (1+&theta;). &theta;<sub>max</sub> = 0.5/(&beta;C<sub>d</sub>) &le; 0.25, "
                    "not less than 0.10 (Eq. 12.8-19). The same rows drive the stability_theta gate.</p>"
                    + _table(["Dir", "Story", "P<sub>x</sub> (kip)", "V<sub>x</sub> (kip)", "h<sub>sx</sub> (in)",
                              "&theta; (Eq. 12.8-18)", "&theta;/(1+&theta;)", "&theta;<sub>max</sub>", "Status"], rows)
                    + "<p>Governing: &theta; = %.3f vs &theta;<sub>max</sub> = %.3f (%s).</p>"
                    % (th, tm or 0.10, dd or "-"))
        except Exception as _tex:
            pass
    NF = len(cfg["heights"]); s = cfg["seis"]; SDS = s["SDS"]; Cd = s.get("Cd", 5.5); Ie = s["Ie"]
    NX, NY, SX, SY = cfg["NX"], cfg["NY"], cfg["SX"], cfg["SY"]
    A = (NX*SX)*(NY*SY)/144.0
    Dk = {k: E.floor_w(cfg, k) for k in range(1, NF+1)}
    Lk = {k: (cfg.get("L_floor", 0)*A/1000.0 if k < NF else 0.0) for k in range(1, NF+1)}
    Pu = {k: (1.2+0.2*SDS)*Dk[k] + 0.5*Lk[k] for k in range(1, NF+1)}
    Pstory = {sx: sum(Pu[k] for k in range(sx, NF+1)) for sx in range(1, NF+1)}
    Vstory = {sx: sum(Fx[k] for k in range(sx, NF+1)) for sx in range(1, NF+1)}
    beta = 1.0; theta_max = min(0.5/(beta*Cd), 0.25); worst = 0.0; rows = []
    for sx in range(1, NF+1):
        th = (Pstory[sx]*drX[sx-1]/Vstory[sx]) if Vstory[sx] else 0.0
        worst = max(worst, th)
        rows.append([sx, "%.0f" % Pstory[sx], "%.0f" % Vstory[sx], "%.0f" % cfg["heights"][sx-1],
                     "%.3f" % (drX[sx-1]*100), "%.3f" % th, "OK" if th <= theta_max else "NG"])
    h = ["<p>Member demands already include second-order effects (a P-&Delta; geometric transformation is applied "
         "under every combination), so the second-order amplification is captured directly by the "
         "analysis. The ASCE 7-22 &sect;12.8.7 story stability coefficient "
         "&theta; = P<sub>x</sub>&Delta;I<sub>e</sub>/(V<sub>x</sub>h<sub>sx</sub>C<sub>d</sub>) (Eq. 12.8-18) is evaluated per "
         "story below; the limit is &theta;<sub>max</sub> = min(0.5/(&beta;C<sub>d</sub>), 0.25) (Eq. 12.8-19) = "
         "%.3f (&beta; = 1.0 conservatively). &theta; &le; 0.10 means P-&Delta; could be neglected; "
         "&theta; &gt; &theta;<sub>max</sub> is not permitted.</p>" % theta_max,
         _table(["Story", "P<sub>x</sub> (kip)", "V<sub>x</sub> (kip)", "h<sub>sx</sub> (in)",
                 "&delta;<sub>e</sub> %", "&theta;", "&le; %.3f" % theta_max], rows),
         "<p>Worst-story &theta; = %.3f &mdash; %s the %.3f limit; P-&Delta; effects are %s and the analysis "
         "includes them regardless.</p>" % (worst, "within" if worst <= theta_max else "EXCEEDS",
                                            theta_max, "significant" if worst > 0.10 else "small")]
    return "".join(h)

def _load_activity(name):
    """Records for this job. Reads jobs/<name>/activity_log.jsonl AND folds in any scratch-log SESSIONS that
    belong to this job -- e.g. work done after a server restart that logged to scratch because new_activity_log()
    was not re-called (its RAG queries would otherwise be invisible). Returns (recs_sorted, primary_source)."""
    _jobs = os.environ.get("STEEL_BUILDER_JOBS") or os.path.join(HERE, "desktop_app", "agent_workspace", "work", "claude_desktop", "jobs")
    def _read(p):
        out = []
        try:
            for line in open(p, encoding="utf-8"):
                if line.strip():
                    try: out.append(json.loads(line))
                    except Exception: pass
        except Exception: pass
        return out
    src = None; jrecs = []
    for c in (os.path.join(_jobs, name, "activity_log.jsonl"),
              os.path.join(HERE, "desktop_app", "agent_workspace", "work", name, "activity_log.jsonl"),
              os.path.join(HERE, "desktop_app", "agent_workspace", "work", "claude_desktop", "activity_log.jsonl")):
        if os.path.exists(c):
            src = c; jrecs = _read(c); break
    scratch = os.environ.get("STEEL_BUILDER_SCRATCH") or os.path.join(os.path.dirname(_jobs), ".scratch")
    srecs = _read(os.path.join(scratch, "activity_log.jsonl"))
    extra = []
    if srecs:
        def refs(r):
            if r.get("tool") == "new_activity_log" and str(r.get("detail", "")).strip() == name:
                return True
            d = str(r.get("detail", "")) + " " + str(r.get("result", ""))
            return ("jobs/" + name + "/" in d) or ("jobs/" + name in d) or (os.sep + name + os.sep in d)
        sessions = []; cur = []
        for r in srecs:
            st = r.get("step", 0)
            if cur and isinstance(st, int) and st <= cur[-1].get("step", 0):
                sessions.append(cur); cur = []
            cur.append(r)
        if cur: sessions.append(cur)
        for s in sessions:
            if any(refs(r) for r in s): extra += s
    seen = set(); merged = []
    for r in jrecs + extra:
        k = (r.get("ts"), r.get("tool"), str(r.get("detail"))[:80])
        if k in seen: continue
        seen.add(k); merged.append(r)
    merged.sort(key=lambda r: (r.get("ts") or "", r.get("step") or 0))
    return merged, src

def _activity_section(name):
    recs, src = _load_activity(name)
    h = ["<h2>Appendix C — Activity log</h2>",
         "<p>Every tool call made during the design, recorded by the MCP server, summarised below.</p>"]
    if not recs:
        h.append("<p class='note'>[no activity_log.jsonl found — call new_activity_log() at the start of "
                 "the design so the server records the tool calls]</p>")
        return "".join(h)
    counts = {}
    for r in recs: counts[r.get("tool", "?")] = counts.get(r.get("tool", "?"), 0) + 1
    _sd = os.path.relpath(src, HERE) if src else "scratch log"
    h.append(f"<p>Source: <code>{_sd}</code> — {len(recs)} tool calls.</p>")
    h.append(_table(["Tool", "Calls"], [[k, counts[k]] for k in sorted(counts)]))
    rows = [[i + 1, (r.get("ts", "").split("T")[-1]), r.get("tool", ""),
             r.get("detail", ""), r.get("result", "")] for i, r in enumerate(recs)]
    h.append("<h3>Chronological tool calls</h3>" + _table(["#", "time", "tool", "detail", "result"], rows))
    return "".join(h)

def _load_pkg(name, root=None):
    cands = []
    if root: cands.append(os.path.join(root, "design", "calc_package.json"))
    cands += [os.path.join(HERE, name, "design", "calc_package.json"),
              os.path.join(HERE, "desktop_app", "agent_workspace", "work", name, "calc_package.json"),
              os.path.join(HERE, "buildings", name, "design", "calc_package.json")]
    for c in cands:
        if os.path.exists(c):
            try: return json.load(open(c, encoding="utf-8")), c
            except Exception: pass
    return None, None



CHK_CSS = ("ol.toc{font-size:14px}ol.toc li{margin:2px 0}"
           ".chk{background:#f3f7fd;border:1px solid #cfe;border-left:4px solid #2c5aa0;"
           "border-radius:4px;padding:8px 14px;margin:8px 0 16px}"
           ".chk-h{font-weight:600;color:#2c5aa0;font-size:13px;margin-bottom:4px}"
           ".chk ul{margin:4px 0 2px 0;padding-left:20px}.chk li{font-size:13px;color:#333;margin:2px 0}"
           ".chk-status{font-weight:400;font-style:italic;color:#a06000;font-size:12px}")

CHAPTERS = {
 1: ("Design basis & codes", [
   "Governing codes and editions stated (IBC, ASCE 7-22, AISI S100-16(R2020) w/S2,S3, S240-20, S400-20); Risk Category and Importance Factors.",
   "Project criteria match the architectural/owner brief and the geotechnical report (site class, bearing, lateral soil, frost).",
   "Units, materials (ASTM A1003/A653 SS Gr 33/50, E = 29,500 ksi), member designators (e.g. 600S162-54) and coating/corrosion class stated and consistent throughout.",
   "Scope and design-responsibility boundaries defined (schedules and connections DESIGNED in this package; hold-down hardware as class envelopes with EOR product substitution; delegated/deferred: concrete anchorage detailing, stairs, cladding)."]),
 2: ("Structural system & load path", [
   "Gravity path continuous: joists &rarr; bearing walls (stud stacks, never lighter below) &rarr; foundation; lateral path: diaphragm &rarr; wall lines (or frames) &rarr; anchorage, in both orthogonal directions.",
   "Lateral system per direction identified (WSP / steel-sheet / gypsum / strap / SBMF; or portal frame) with the correct R, &Omega;<sub>0</sub>, C<sub>d</sub>, &rho;, the SDC height limit stated PASS/FAIL, and the least-R rule where systems share an axis.",
   "Diaphragm idealization (flexible default for light frame per 12.3.1.1) justified; collectors at re-entrant corners/steps/throats and chord forces addressed; mixed diaphragm types handled distinctly.",
   "Irregularities (plan &amp; vertical), podium/two-stage conditions, and movement/seismic joints identified; triggered provisions applied."]),
 3: ("Loads", [
   "Dead loads: realistic light-frame self-weight, superimposed (MEP, ceilings, finishes, partitions), cladding.",
   "Live loads per occupancy with correct reductions, COMPOUNDED down the stud/chord stacks (and not where prohibited).",
   "Roof live/snow: flat-roof, drift at every stated step/valley, unbalanced, rain-on-snow, minimums.",
   "Wind (Ch. 26-31): MWFRS and C&amp;C; enclosure classification where raised; NET UPLIFT (0.9D+1.0W) as a first-class case.",
   "Seismic (Ch. 11-12): S<sub>DS</sub>/S<sub>D1</sub>, SDC, period, C<sub>s</sub>, V, vertical distribution, E<sub>v</sub>, 5% tributary-shift accidental eccentricity, &rho;.",
   "Governing case (wind vs seismic) identified per line/direction \u2014 BOTH hazards run."]),
 4: ("Load combinations", [
   "Full ASCE 7-22 &sect;2.3 LRFD set (gravity, wind &plusmn;, seismic &plusmn; with E<sub>v</sub> &amp; &rho;, &Omega;<sub>0</sub> where required), &plusmn; tributary shift.",
   "Combinations applied to the right elements (&Omega;<sub>0</sub> to collectors and capacity-protected items only); net-uplift 0.9D+1.0W anchorage case carried through the whole path.",
   "Second-order effects handled per-combination on frame paths (no superposition of factored second-order results)."]),
 5: ("Analysis model fidelity", [
   "Wall lines, segment lengths per story, and openings match the brief; frame paths: geometry and member orientation correct.",
   "Wall-line springs calibrated to the S400 four-term deflection (bending + shear + fastener slip + anchorage/rod elongation); anchorage stiffness basis stated.",
   "Diaphragm flexibility MODELLED (not rigid-plate torsional redistribution); tributary validator vs model gate within tolerance or divergence justified.",
   "Analysis-fidelity tier (0/1/2) stated and consistent with the structure kind; effective-section stiffness: decoupled EA(A<sub>e</sub>)/EI(I<sub>eff</sub>) iteration on frame members (gross-only analysis of slender CFS flagged).",
   "Boundary conditions: base/anchorage assumptions match the hold-down/rod design; portal base fixity stated and consistent.",
   "Equilibrium verified: &Sigma;(line shears) = applied base shear (each direction) and total gravity recovered."]),
 6: ("Member & wall strength design (AISI S100 / S240 / S400)", [
   "Every wall line, every story: sheathing type/thickness/sides + edge/field fastener schedule with &phi;v<sub>n</sub> from the correct S400 table column (wind vs seismic), aspect-ratio (2w/h) reduction, Type II adjustment factor where perforated.",
   "Studs/chords: compression E2/E3 with DISTORTIONAL E4, the declared sheathing-braced/unbraced assumption, interaction H1; built-up chords with S240 interconnection.",
   "Track/joists: flexure F2-F4, shear G2, WEB CRIPPLING G5 at bearing (+ combined bending&ndash;web-crippling); headers per S240.",
   "Straps: A<sub>n</sub>F<sub>u</sub> &ge; A<sub>g</sub>F<sub>y</sub> ductility check.",
   "Cumulative bookkeeping correct: stud/chord/hold-down stacks step down with height, never lighter below.",
   "Governing D/C &le; 1.0 with sensible margins; calcs cited to specific AISI clauses and independently re-derivable; 2-3 governing items spot-checked."]),
 7: ("Stability & second-order", [
   "Frame paths (portals): P-&Delta; included in demands; &theta; per story within limits.",
   "Wall paths: overturning stability per line; stability of the anchorage chain under net uplift."]),
 8: ("Serviceability", [
   "Seismic design drift &delta;=C<sub>d</sub>&delta;<sub>xe</sub>/I<sub>e</sub> &le; the Table 12.12-1 limit (0.025 for light-frame &le;4 stories RC I/II) per LINE per story, from the S400 four-term deflection including hold-down/rod elongation.",
   "Wind drift &le; project limit; joist/header deflections LL &le; L/360, TL &le; L/240.",
   "Building separation &ge; sum of Cd-amplified drifts where structures adjoin (wings, host + mezzanine).",
   "Differential movement at podium/split-level steps; thermal movement where raised (cold storage)."]),
 9: ("Lateral-system detailing & capacity design (AISI S400)", [
   "System detailing matches the R used; S400 E1-E4 provisions for the exact system; R=3 'not specifically detailed' path stated where applicable.",
   "Capacity-design chains: strap R<sub>y</sub>F<sub>y</sub>A<sub>g</sub> into connections/chords/anchorage; expected wall strength into collectors and the story below; SBMF expected beam strength at design drift \u2014 never ELF-force-only.",
   "Type II: adjustment factor shown; end hold-downs + distributed track anchorage (hold-downs at every pier = silent Type I reversion).",
   "Hold-downs/rods sized for cumulative OVERTURNING TENSION (never shear); rod elongation + take-up in the drift; wind: C&amp;C on cladding/fasteners and the continuous net-uplift path."]),
 10: ("Connections", [
   "Connection demands (incl. capacity-design/overstrength where required) stated per type.",
   "Connections designed in this package per S100 Ch. J: screws (tilting/bearing/pull-out/pull-over), welds on thin sheet, bolts with tilting; sized components and every limit-state D/C &le; 1.0.",
   "Strap connections to the expected strap strength; uplift clips on the 0.9D+1.0W path; track-to-foundation and distributed Type II anchorage.",
   "Anchorage to concrete scoped to ACI 318 Ch. 17 with demands handed off; constructability of screw patterns and device placement."]),
 11: ("Foundations interface", [
   "Wall-line reactions (shear plf, hold-down/rod tensions, bearing) provided to foundation design; consistent with the anchorage scheme.",
   "Net uplift anchorage (0.9D+1.0W) explicit per line; overall overturning/sliding; load cases to geotech consistent with bearing/lateral capacity."]),
 12: ("Drawings, specifications & documentation", [
   "Drawings internally consistent and consistent with the calculations.",
   "General notes, material/coating specs, screw/weld specs, special-inspection schedule and design loads shown.",
   "Stud/track schedules, wall-line plans with sheathing + fastener schedules, hold-down/rod schedule, collector details complete and coordinated.",
   "Deferred-submittal items listed (specific hold-down products, trusses); assumptions and limitations documented."]),
 13: ("QA / professional acceptance", [
   "Independent check performed (different engineer or method); the tributary validator vs model gate reconciled.",
   "Software validated/appropriate; analysis assumptions vs detailing reconciled; fidelity tier appropriate.",
   "Special inspection &amp; testing program (IBC Ch. 17) defined.",
   "Calc package complete, traceable (input &rarr; demand &rarr; capacity &rarr; D/C &rarr; cited AISI clause), archived; S100+S240+S400 grounded together.",
   "All open items / RFIs closed; assumptions confirmed.",
   "EOR satisfied the design meets the governing codes and the standard of care &mdash; apply seal/signature."]),
}

def _toc():
    rows = "".join(f"<li><b>Chapter {n}</b> &mdash; {CHAPTERS[n][0]}</li>" for n in range(1, 14))
    return ("<h2>Report structure &mdash; EOR review checklist</h2>"
            "<p>This report is organised as the senior-engineer (EOR) acceptance checklist: one chapter per "
            "checklist section. Each chapter opens with the items a reviewer must accept, followed by the "
            "supporting analysis and design evidence. Appendix A is the fully-referenced AISI S100/S240/S400 "
            "design calc, Appendix B the demands for every load case, Appendix C the activity "
            "log of the tool calls that produced the design.</p><ol class='toc'>" + rows + "</ol>")

def _chapter(n, status=None):
    title, items = CHAPTERS[n]
    li = "".join(f"<li>{t}</li>" for t in items)
    extra = f" &mdash; <span class='chk-status'>{status}</span>" if status else ""
    return (f"<h2>Chapter {n} &mdash; {title}</h2>"
            f"<div class='chk'><div class='chk-h'>Reviewer acceptance items{extra}</div><ul>{li}</ul></div>")

def _risk_category(Ie):
    return {1.0: "II", 1.25: "III", 1.5: "IV"}.get(round(float(Ie), 2), "II")

def _sdc(SDS, SD1, S1=0.0, risk_cat="II"):
    """Seismic Design Category, ALIGNED with the canonical cfs_systems.sdc: worse of ASCE 7-22
    Tables 11.6-1 and 11.6-2, plus S1 >= 0.75 -> E (RC I-III) / F (RC IV)."""
    try:
        import cfs_systems as _CS
        return _CS.sdc(SDS, SD1, S1, risk_cat)
    except Exception:
        pass
    rc4 = str(risk_cat).strip().upper() in ("IV", "4")
    if S1 is not None and float(S1) >= 0.75:
        return "F" if rc4 else "E"
    def a(x, b):
        for thr, c123, c4 in b:
            if x < thr: return c4 if rc4 else c123
        return "D"
    c1 = a(SDS, [(0.167, "A", "A"), (0.33, "B", "C"), (0.50, "C", "D")])
    c2 = a(SD1, [(0.067, "A", "A"), (0.133, "B", "C"), (0.20, "C", "D")])
    return max(c1, c2)


def _rho_default(cfg):
    """Display default for the redundancy factor, aligned with the canonical rule
    (ASCE 7-22 12.3.4.2): 1.3 in SDC D/E/F, 1.0 in B/C; cfg['rho'] overrides."""
    if cfg.get("rho") is not None:
        return cfg["rho"]
    s = cfg.get("seis") or {}
    cat = _sdc(s.get("SDS", 0.0), s.get("SD1", 0.0), s.get("S1", 0.0),
               _risk_category(s.get("Ie", 1.0)))
    return 1.3 if cat in ("D", "E", "F") else 1.0

def _design_basis_codes(cfg, s):
    seismic = bool(s.get("R"))
    rows = [["International Building Code (IBC)", "adopting code &mdash; confirm locally adopted edition &amp; amendments"],
            ["ASCE/SEI 7-22", "loads &amp; load combinations (gravity, wind Ch.26-31, seismic Ch.11-12, &sect;2.3 LRFD)"],
            ["AISI S100-16 (R2020) w/S2,S3", "CFS member &amp; connection design (LRFD, EWM)"],
            ["AISI S240-20", "CFS structural framing (studs/track/built-up/bracing/trusses)"]]
    if seismic:
        rows += [["AISI S400-20", "seismic design of CFS systems (seismic wall/strap/SBMF capacities, capacity design); "
                  "wind shear walls per AISI S240 B5"],
                 ]
    rows += [["AWS D1.1", "structural welding"], ["ACI 318 (Ch. 17)", "cast-in anchorage at column bases"]]
    out = ["<h3>Governing standards</h3>",
           _table(["Reference", "Used for"], rows)]
    Ie = s.get("Ie", 1.0); RC = _risk_category(Ie)
    crows = [["Risk Category", RC], ["Importance factor I<sub>e</sub>", f"{Ie}"]]
    if seismic:
        sdc = _sdc(s["SDS"], s["SD1"], s.get("S1", 0.0), RC)
        crows += [["S<sub>DS</sub> / S<sub>D1</sub>", f"{s['SDS']} / {s['SD1']} g"],
                  ["Seismic Design Category", f"{sdc} <span class='cnote'>(Tables 11.6-1/2, worse governs, incl. S<sub>1</sub>&ge;0.75 &rarr; E/F)</span>"],
                  ["R / C<sub>d</sub> / &Omega;<sub>0</sub>", f"{s['R']} / {s.get('Cd')} / {s.get('Om0')}"],
                  ["Redundancy &rho;", f"{_rho_default(cfg)}"]]
    w = cfg.get("wind", {})
    if w:
        crows.append(["Basic wind speed V", f"{w.get('V','?')} mph, Exposure {w.get('exposure','?')}"])
    out += ["<h3>Risk &amp; hazard classification</h3>", _table(["Parameter", "Value"], crows)]
    out += ["<h3>Scope &amp; design responsibility</h3>",
            "<p class='cnote'>This package covers the primary steel gravity and lateral framing, its members, "
            "and the analysis behind them. Connection design, foundations, cold-formed/joist framing, stairs, "
            "cladding attachment and embeds are delegated/deferred unless explicitly included; their demands are "
            "transmitted in Chapters 10-11. The reviewer should confirm the geotechnical and architectural "
            "criteria match this basis.</p>"]
    return "".join(out)

def _system_loadpath(cfg):
    seismic = bool(cfg["seis"].get("R")); braced = E.is_braced(cfg)
    sysname = "concentrically braced / dual" if braced else "moment-resisting frame"
    return ("<h3>Lateral system &amp; load path</h3>"
            f"<p>The lateral force-resisting system is a steel {sysname} in each principal direction. "
            "<b>Gravity</b> path: floor/roof pressure &rarr; floor beams (two-way tributary) &rarr; girders/"
            "columns &rarr; column bases &rarr; foundation. <b>Lateral</b> path: story inertial/wind force &rarr; "
            "rigid floor diaphragm &rarr; vertical lateral frames &rarr; column bases &rarr; foundation, in both "
            "orthogonal directions. The design inputs for each system are below; the horizontal distribution to "
            "the frames and the deformed shape that confirms the path are shown in this chapter and Chapter 5.</p>")


def _lfrs_table(cfg):
    s = cfg["seis"]; R = s.get("R"); Om0 = s.get("Om0"); Cd = s.get("Cd")
    rho = _rho_default(cfg)          # canonical SDC-based default (12.3.4.2)
    # CFS R-fingerprint (ASCE 7-22 Table 12.2-1 light-frame rows) — an R-guess is
    # ambiguous (R=6.5 is WSP or steel-sheet): DECLARE cfg['system'].
    if R and abs(R-6.5) < .3:  name, hl = "CFS shear wall — WSP or steel sheet (DECLARE which)", "65 ft (SDC D/E/F)"
    elif R and abs(R-4) < .3:  name, hl = "CFS strap-braced wall", "65 ft (SDC D/E/F)"
    elif R and abs(R-3.5) < .3: name, hl = "CFS special bolted moment frame (SBMF)", "35 ft in EVERY SDC B&ndash;F (Table 12.2-1 row C.12; footnote n one-story exception)"
    elif R and abs(R-2) < .3:  name, hl = "CFS wall, gypsum/other materials", "35 ft (SDC D); NP in E/F"
    elif R and abs(R-3) < .3:  name, hl = "CFS system not specifically detailed (S100 only)", "NP in SDC D/E/F (confirm)"
    else: name, hl = "CFS system", "per ASCE 7-22 Table 12.2-1 / Ch. 15"
    sysdecl = cfg.get("system")                       # R1: use the system the agent DECLARED, not an R-guess
    sys_key = "Seismic force-resisting system (declared)" if sysdecl else "System (inferred from R &mdash; DECLARE cfg['system'])"
    sys_val = (sysdecl if sysdecl else name)
    rows = [[sys_key, sys_val],
            ["Response modification R", f"{R}"],
            ["Deflection amplification C<sub>d</sub>", f"{Cd}"],
            ["Overstrength &Omega;<sub>0</sub>", f"{Om0}"],
            ["Redundancy &rho;", f"{rho} <span class='cnote'>(confirm vs &sect;12.3.4.2; 1.0 if redundancy conditions met)</span>"],
            ["Structural height h<sub>n</sub> limit (Table 12.2-1)", hl]]
    if sysdecl:
        note = ("<p class='cnote'>System is the one DECLARED in cfg['system']; apply its AISI S400 provisions in "
                "Chapter 9 and confirm the Table 12.2-1 height limit for the SDC. Use the same system both directions "
                "unless cfg declares a different system per direction.</p>")
    else:
        note = ("<p class='cnote'>No cfg['system'] declared &mdash; the label above is INFERRED from R and is ambiguous "
                "(R=6.5 is WSP or steel-sheet). DECLARE cfg['system'] = the "
                "exact SFRS from the brief so the system, its S400 detailing (Ch 9) and height limit are locked.</p>")
    return _table(["Parameter", "Value"], rows) + note

def _diaphragm_section(cfg, Fx):
    NF = len(cfg["heights"]); s = cfg["seis"]; SDS = s["SDS"]; Ie = s["Ie"]
    w = [E.floor_w(cfg, k) for k in range(1, NF+1)]
    rows = []
    for x in range(1, NF+1):
        sumF = sum(Fx[i] for i in range(x, NF+1)); sumw = sum(w[i-1] for i in range(x, NF+1))
        wpx = w[x-1]; Fpx = (sumF/sumw)*wpx if sumw else 0.0
        lo = 0.2*SDS*Ie*wpx; hi = 0.4*SDS*Ie*wpx; gov = min(max(Fpx, lo), hi)
        tag = "min" if gov == lo else ("max" if gov == hi else "Eq.12.10-1")
        rows.append([x, f"{wpx:.0f}", f"{Fpx:.0f}", f"{lo:.0f}", f"{hi:.0f}", f"{gov:.0f} ({tag})"])
    dia = (cfg.get("diaphragm") or "flexible").lower()
    if dia == "rigid":
        just = ("<p>The floor/roof is taken as a <b>rigid diaphragm</b> (a departure from the light-frame default "
                "that MUST be justified — e.g. concrete-filled deck at a podium level), distributing story forces "
                "by stiffness per ASCE 7-22 &sect;12.3.1.2. Collectors carry the diaphragm shear into the wall "
                "lines/frames; chords resist M<sub>diaph</sub>/depth. Designed for F<sub>px</sub> below "
                "(&sect;12.10.1.1).</p>")
    else:
        just = ("<p>The floor/roof is idealized as a <b>%s diaphragm</b> (the light-frame default per ASCE 7-22 "
                "&sect;12.3.1.1): story force goes to each wall line by TRIBUTARY AREA (±5%% tributary shift for "
                "accidental eccentricity), not by rigid-plate torsional redistribution. Diaphragm unit shear "
                "(plf), chords at the boundaries, and collectors at re-entrant corners/steps/throats are designed "
                "for the force F<sub>px</sub> below (&sect;12.10.1.1), &Omega;<sub>0</sub>-amplified at "
                "collectors.</p>" % dia)
    tbl = _table(["Level x", "w<sub>px</sub> (kip)", "F<sub>px</sub> Eq.12.10-1 (kip)",
                  "min 0.2S<sub>DS</sub>I<sub>e</sub>w<sub>px</sub>", "max 0.4S<sub>DS</sub>I<sub>e</sub>w<sub>px</sub>",
                  "F<sub>px</sub> design (kip)"], rows)
    return just + tbl + ("<p class='cnote'>F<sub>px</sub> is the diaphragm/collector design force; the detailed "
                         "diaphragm, collector and chord design is delegated and confirmed on the drawings.</p>")

def _torsion_ratios(cfg, Fx):
    NF = len(cfg["heights"]); SX, SY = cfg["SX"], cfg["SY"]; NX, NY = cfg["NX"], cfg["NY"]
    Lx = (cfg["xcoords"][-1] if cfg.get("xcoords") else NX*SX); Ly = (cfg["ycoords"][-1] if cfg.get("ycoords") else NY*SY)
    out = {}
    for direction, Bperp in (("X", Ly), ("Y", Lx)):
        try:
            info, disp, drift, re = _run_case(cfg, direction, Fx, accidental=True)
            rot = {k: ops.nodeDisp(E.mtag(k), 6) for k in range(1, NF+1)}
            worst = 1.0; prevd = 0.0; prevr = 0.0
            for k in range(1, NF+1):
                dtr = disp[k] - prevd; drot = rot[k] - prevr; prevd = disp[k]; prevr = rot[k]
                if abs(dtr) > 1e-9:
                    worst = max(worst, 1.0 + abs(drot)*(Bperp/2.0)/abs(dtr))
            out[direction] = worst
        except Exception:
            out[direction] = None
    return out

def _irregularity_section(cfg, Fx):
    NF = len(cfg["heights"]); w = [E.floor_w(cfg, k) for k in range(1, NF+1)]
    h = cfg["heights"]; custom_plan = cfg.get("plan") is not None
    tors = _torsion_ratios(cfg, Fx)
    tr = max([v for v in tors.values() if v is not None], default=1.0)
    _Ax = min(max((tr/1.2)**2, 1.0), 3.0)   # ASCE 7-22 Eq. 12.8-15 accidental-torsion amplification
    if tr >= 1.4:   tcls = f"EXTREME torsional (1b): ratio {tr:.2f} &ge; 1.4 &mdash; apply A<sub>x</sub> = {_Ax:.2f} (&sect;12.8.4.3)"
    elif tr >= 1.2: tcls = f"Torsional (1a): ratio {tr:.2f} &ge; 1.2 &mdash; apply A<sub>x</sub> = {_Ax:.2f} (&sect;12.8.4.3)"
    else:           tcls = f"None: ratio {tr:.2f} &lt; 1.2 (A<sub>x</sub> = 1.0)"
    # mass screen -- SUPPLEMENTARY only: the 7-16 Vert-2 mass (weight) irregularity was
    # DELETED from ASCE 7-22 Table 12.3-2; the ratio is reported for reviewer information
    massr = max((max(w[k]/w[k-1], w[k-1]/w[k]) for k in range(1, NF)), default=1.0)
    mcls = ("None" if massr <= 1.5 else
            f"Supplementary (deleted in 7-22): adjacent mass ratio {massr:.2f} &gt; 1.5 &mdash; no longer a Table 12.3-2 irregularity; reported for information")
    # soft story screen via story height uniformity (stiffness ~ 1/h^3 proxy)
    hr = max((max(h[k]/h[k-1], h[k-1]/h[k]) for k in range(1, NF)), default=1.0)
    scls = "None (uniform story heights)" if hr <= 1.0001 else f"Check stiffness/soft-story: tallest/shortest story height ratio {hr:.2f}"
    # R3: FIRM plan/vertical determination from the ACTUAL per-level footprint (present-sets / plan= fn),
    # not the old 'cfg.get(plan) is None -> uniform rectangular' guess that denied custom_build L/T/U/cruciform plans.
    pir = E.plan_irregularities(cfg)
    if pir["reentrant"]:
        h2 = ("Type 2 RE-ENTRANT: YES &mdash; non-convex footprint. Requires a "
              "<b>25% increase to diaphragm-to-collector CONNECTION forces</b> (&sect;12.3.3.5, SDC D&ndash;F), with &Omega;<sub>0</sub> "
              "collectors on the re-entrant grid lines.")
    else:
        h2 = "None (rectangular / convex footprint)"
    h3 = "None (solid rigid diaphragm; confirm any large openings/atria)"
    h5 = ("Type 5 NONPARALLEL: YES &mdash; a skewed frame line; resolve its stiffness into BOTH principal directions and "
          "apply biaxial member / capacity-design checks with the 100/30 combination." if pir["nonparallel"]
          else "None (orthogonal frames)")
    v3 = ("Type 3 GEOMETRIC SETBACK: YES &mdash; footprint reduces with height; design the transfer/backstay diaphragm "
          "at the setback with &Omega;<sub>0</sub> collectors." if pir["setback"] else "None (uniform footprint over height)")
    rows = [
        ["Horizontal 1a/1b &mdash; Torsional / extreme", tcls],
        ["Horizontal 2 &mdash; Re-entrant corners", h2],
        ["Horizontal 3 &mdash; Diaphragm discontinuity", h3],
        ["Horizontal 4 &mdash; Out-of-plane offset", "None (continuous vertical frames; confirm no transfer)"],
        ["Horizontal 5 &mdash; Nonparallel system", h5],
        ["Vertical 1a/1b &mdash; Soft / extreme soft story", scls],
        ["Vertical 2 &mdash; Mass screen (supplementary; deleted in 7-22)", mcls],
        ["Vertical 3 &mdash; Geometric (setback)", v3],
        ["Vertical 4 &mdash; In-plane discontinuity", "None (aligned frames; confirm no transfer columns)"],
        ["Vertical 5a/5b &mdash; Weak / extreme weak story", "Confirm against story shear strengths (Ch 6/9)"]]
    intro = ("<p>Screening against ASCE 7-22 Tables 12.3-1 (plan) and 12.3-2 (vertical). The torsional ratio is "
             "the story diaphragm displacement-ratio under the &plusmn;5% accidental eccentricity (rigid-diaphragm "
             "estimate); &ge;1.2 triggers a torsional irregularity and amplification A<sub>x</sub> (&sect;12.8.4.3).</p>")
    # ASCE 7-22 12.6 PERMITS ELF for all structures (the 7-16 Table 12.6-1 procedure
    # restrictions were removed) -- MRSA is ADVISORY here, not required
    mrsa_advis = pir["reentrant"] or pir["setback"] or pir["nonparallel"] or tr >= 1.2
    if mrsa_advis and "RS" not in [a.upper() for a in cfg.get("analyses", [])]:
        intro += ("<p class='cnote'><b>Analysis procedure &mdash; ADVISORY:</b> ASCE 7-22 &sect;12.6 permits the ELF "
                  "procedure (&sect;12.8) for ALL structures (the 7-16 Table 12.6-1 restrictions were deleted). Given the "
                  "irregularities above, MODAL RESPONSE SPECTRUM (&sect;12.9.1) is recommended as a check; note the 3D "
                  "representation and accidental-torsion requirements of &sect;12.7.3/&sect;12.8.4.2 still apply.</p>")
    return intro + _table(["Irregularity type", "Determination"], rows)


def _gravity_loads_note():
    return ("<p class='cnote'>Dead D is the bundled member self-weight + superimposed dead (MEP, ceilings, "
            "finishes, partition allowance) plus fa&ccedil;ade cladding &mdash; itemise on the load schedule. "
            "Live L is applied at full L<sub>0</sub> (no &sect;4.7 reduction taken &mdash; conservative); "
            "L = L<sub>0</sub>(0.25 + 15/&radic;(K<sub>LL</sub>A<sub>T</sub>)) may be used in member design where "
            "K<sub>LL</sub>A<sub>T</sub> &ge; 400 ft&sup2; and reduction is permitted. Roof carries a uniform "
            "L<sub>r</sub>/snow; flat-roof snow, drift, unbalanced/sliding snow, rain-on-snow and ponding stability "
            "are confirmed separately (out of scope for the uniform roof model).</p>")

def _wind_cc_note():
    return ("<p class='cnote'>The forces above are the MWFRS (main wind force-resisting system) for the overall "
            "building (windward + leeward combined via C<sub>p,net</sub>). Components &amp; cladding (C&amp;C) "
            "pressures &mdash; zone GC<sub>p</sub> of &sect;30 for cladding, fasteners and their supports &mdash; "
            "and across-wind/torsional MWFRS load cases are part of the (delegated) cladding design.</p>")

def _seismic_loads_section(cfg, T, eX, eY, Cs, V, Tu, Ta, Fx, W):
    s = cfg["seis"]; NF = len(cfg["heights"]); z = E.zlevels(cfg)
    R = s["R"]; Ie = s["Ie"]; SDS = s["SDS"]; SD1 = s["SD1"]; S1 = s.get("S1", 0); TL = s.get("TL", 8.0)
    kk = 1.0 if Tu <= 0.5 else (2.0 if Tu >= 2.5 else 1 + (Tu-0.5)/2.0)
    Cs_eq = SDS/(R/Ie)
    cap = SD1/(Tu*(R/Ie)) if Tu <= TL else SD1*TL/(Tu**2*(R/Ie))
    cmin = max(0.044*SDS*Ie, 0.01); cmin_s1 = (0.5*S1/(R/Ie)) if S1 >= 0.6 else None
    low = max(cmin, cmin_s1 or 0.0); upper = min(Cs_eq, cap)
    if upper <= low: gov = "C<sub>s,min</sub> (Eq.12.8-7, S<sub>1</sub>)" if (cmin_s1 and low == cmin_s1) else "C<sub>s,min</sub> (Eq.12.8-6)"
    elif cap < Cs_eq: gov = "C<sub>s,max</sub> (Eq.12.8-4)"
    else: gov = "C<sub>s</sub> (Eq.12.8-3)"
    intro = (f"<p>ASCE 7-22 &sect;12.8 equivalent lateral force. Seismic weight W = {W:.0f} kip; S<sub>DS</sub> = "
             f"{SDS} g, S<sub>D1</sub> = {SD1} g, S<sub>1</sub> = {S1} g, R = {R}, I<sub>e</sub> = {Ie}. Approximate "
             f"period T<sub>a</sub> = C<sub>t</sub>h<sub>n</sub><sup>x</sup> = {Ta:.2f} s; design period "
             f"T = min(T<sub>computed</sub>, C<sub>u</sub>T<sub>a</sub>) = {Tu:.2f} s (&sect;12.8.2).</p>")
    crows = [["C<sub>s</sub> = S<sub>DS</sub>/(R/I<sub>e</sub>) &nbsp;(Eq.12.8-3)", f"{Cs_eq:.4f}"],
             ["C<sub>s,max</sub> = S<sub>D1</sub>/(T&middot;R/I<sub>e</sub>) &nbsp;(Eq.12.8-4)", f"{cap:.4f}"],
             ["C<sub>s,min</sub> = max(0.044&middot;S<sub>DS</sub>I<sub>e</sub>, 0.01) &nbsp;(Eq.12.8-6)", f"{cmin:.4f}"]]
    if cmin_s1 is not None:
        crows.append(["C<sub>s,min</sub> = 0.5&middot;S<sub>1</sub>/(R/I<sub>e</sub>) &nbsp;(Eq.12.8-7, S<sub>1</sub>&ge;0.6)", f"{cmin_s1:.4f}"])
    crows.append(["<b>Governing C<sub>s</sub></b>", f"<b>{Cs:.4f}</b> &nbsp;({gov})"])
    cstab = "<h4>Seismic response coefficient C<sub>s</sub> and its limits</h4>" + _table(["C<sub>s</sub> equation", "Value"], crows)
    base = f"<p>Design base shear V = C<sub>s</sub>W = {Cs:.4f} &times; {W:.0f} = <b>{V:.0f} kip</b> in each direction.</p>"
    vrows = [[k, f"{E.floor_w(cfg,k):.0f}", f"{z[k]/12:.1f}", f"{E.floor_w(cfg,k)*(z[k]/12)**kk:,.0f}",
              f"{Fx[k]/V:.3f}", f"{Fx[k]:.1f}"] for k in range(1, NF+1)]
    vtab = (f"<h4>Vertical distribution of base shear (k = {kk:.2f}, &sect;12.8.3)</h4>"
            "<p>F<sub>x</sub> = C<sub>vx</sub>V, &nbsp; C<sub>vx</sub> = w<sub>x</sub>h<sub>x</sub><sup>k</sup> / "
            "&Sigma; w<sub>i</sub>h<sub>i</sub><sup>k</sup>.</p>"
            + _table(["Floor", "w (kip)", "h (ft)", "w&middot;h<sup>k</sup>", "C<sub>vx</sub>", "F<sub>x</sub> (kip)"], vrows))
    cumX = cumY = 0.0; mrows = []
    for m in range(min(len(T), 12)):
        cumX += eX[m]; cumY += eY[m]
        mrows.append([m+1, f"{T[m]:.3f}", f"{eX[m]*100:.1f}", f"{cumX*100:.1f}", f"{eY[m]*100:.1f}", f"{cumY*100:.1f}"])
    mtab = ("<h4>Modal periods &amp; mass participation (&ge; 90% per &sect;12.9.1)</h4>"
            + _table(["Mode", "T (s)", "mX %", "&Sigma;mX %", "mY %", "&Sigma;mY %"], mrows)
            + "<p class='cnote'>Mode-shape figures are in Chapter 5. Vertical seismic E<sub>v</sub> = 0.2&middot;S<sub>DS</sub>D, "
            "redundancy &rho;, the 100/30 directional combination and &plusmn;5% accidental torsion are applied in the "
            "load combinations (Chapter 4); torsional amplification is screened in Chapter 2.</p>")
    return intro + cstab + base + vtab + mtab

def _governing_lateral(cfg, V, VwX, VwY):
    if V is None:
        return "<p class='note'>[seismic base shear unavailable]</p>"
    rows = []
    for d, Vw in (("X (E-W)", VwX), ("Y (N-S)", VwY)):
        if Vw is None:
            rows.append([d, f"{V:.0f}", "&mdash; (no wind defined)", "<b>Seismic</b>"])
        else:
            rows.append([d, f"{V:.0f}", f"{Vw:.0f}", f"<b>{'Seismic' if V >= Vw else 'Wind'}</b>"])
    note = ("<p class='cnote'>Strength-level base-shear comparison (seismic E and wind W are both strength-level in "
            "ASCE 7-22 LRFD). The governing system per direction sizes the lateral frames; both are carried through "
            "the load combinations (Chapter 4).</p>")
    return _table(["Direction", "Seismic V (kip)", "Wind V (kip)", "Governs"], rows) + note


def _combo_table(cases):
    def lab(label, lat):
        if not lat: return "&mdash;"
        if "WX" in label or "WY" in label: return "wind"
        if "EX" in label or "EY" in label: return "seismic"
        return "lateral"
    rows = [[L, f"{fD:.2f}", f"{fL:.2f}", f"{fLr:.2f}", lab(L, lat), "columns only" if co else "all members"]
            for (L, fD, fL, fLr, lat, co) in cases]
    return _table(["Combination", "D", "L", "L<sub>r</sub>/S", "Lateral", "Applies to"], rows)

def _combo_legend():
    items = [("D", "dead load"),
             ("L", "floor live load (reducible per ASCE 7-22 &sect;4.7)"),
             ("L<sub>r</sub> / S", "roof live load / snow"),
             ("E<sub>X</sub>, E<sub>Y</sub>", "horizontal seismic effect Q<sub>E</sub> (ELF) in the X (E-W) / Y (N-S) direction; the vertical term E<sub>v</sub>=0.2S<sub>DS</sub>D is folded into the D factor"),
             ("W<sub>X</sub>, W<sub>Y</sub>", "wind load in the X / Y direction"),
             ("&rho;", "redundancy factor multiplying Q<sub>E</sub> (&sect;12.3.4); 1.0 in SDC B/C, 1.3 in SDC D&ndash;F unless &sect;12.3.4.2 conditions are met"),
             ("&Omega;<sub>0</sub>", "overstrength factor; the &ldquo;[col]&rdquo; cases apply &Omega;<sub>0</sub>Q<sub>E</sub> to capacity-protected columns only (&sect;12.4.3)"),
             ("t+ / t&minus;", "&plusmn;5% accidental torsion M<sub>t</sub> = &plusmn;0.05B&middot;F<sub>x</sub> (&sect;12.8.4.2)"),
             ("+ / &minus;", "sign (direction) of the applied lateral load"),
             ("[col]", "combination applied to columns only"),
             ("0.5L, 0.15S, 0.9D", "companion / counteracting load factors (ASCE 7-22 &sect;2.3; 7-22 seismic snow companion is 0.15S)")]
    return "<h4>Notation used in the combination labels</h4>" + _table(["Symbol", "Meaning"], [[a, b] for a, b in items])

def _joint_figure(cfg):
    """Schematic of the ACTUAL modelled boundary conditions for THIS job (base fixity and joint
    types from cfg['model'], braced-bay panel only when the model has braces) -- no boilerplate
    about frames or grid lines that aren't in the present building."""
    import matplotlib.pyplot as _plt
    md = (cfg.get("model") or {})
    bases = str(md.get("bases") or cfg.get("base") or "pinned").lower()
    joints = str(md.get("joints") or ("pinned" if cfg.get("releases") else "rigid")).lower()
    try:
        braced = E.is_braced(cfg)
    except Exception:
        braced = bool(cfg.get("brace") or cfg.get("braces"))
    ncol = 3 if braced else 2
    fig, axs = _plt.subplots(1, ncol, figsize=(4.9*ncol + 1.0, 4.6))

    def _fixed_base(ax, x, label):
        ax.plot([x, x], [3, 8.5], lw=3, color="#333"); ax.plot([x-1.3, x+1.3], [3, 3], lw=4, color="#333")
        for h in (-1.1, -0.5, 0.1, 0.7): ax.plot([x+h, x+h-0.45], [3, 2.45], lw=1, color="#333")
        ax.text(x, 9.0, "FIXED base", ha="center", fontweight="bold", fontsize=11)
        ax.text(x, 1.6, label, ha="center", fontsize=8.5)
    def _pinned_base(ax, x, label):
        ax.plot([x, x], [3, 8.5], lw=3, color="#1f77b4"); ax.plot([x-0.7, x+0.7], [2.55, 2.55], lw=4, color="#333")
        for h in (-0.5, 0.1, 0.6): ax.plot([x+h, x+h-0.45], [2.55, 2.0], lw=1, color="#333")
        ax.plot([x-0.4, x, x+0.4], [2.55, 3, 2.55], color="#1f77b4", lw=2)
        ax.add_patch(_plt.Circle((x, 3), 0.16, fill=False, color="#1f77b4", lw=2))
        ax.text(x, 9.0, "PINNED base", ha="center", fontweight="bold", fontsize=11)
        ax.text(x, 1.6, label, ha="center", fontsize=8.5)

    ax = axs[0]; ax.set_title("Column base fixity"); ax.axis("off"); ax.set_xlim(0, 10); ax.set_ylim(0, 10)
    if "mix" in bases:
        _fixed_base(ax, 2.4, "lateral-frame columns"); _pinned_base(ax, 7.4, "all other columns")
    elif "fix" in bases:
        _fixed_base(ax, 5, "ALL column bases in this model")
    else:
        _pinned_base(ax, 5, "ALL column bases in this model")

    def _fr_joint(ax, x, label):
        ax.plot([x, x], [1, 9], lw=3, color="#333"); ax.plot([x, x+3.0], [6, 6], lw=3, color="#333")
        ax.add_patch(_plt.Rectangle((x-0.25, 5.55), 0.5, 0.9, color="#d62728"))
        ax.plot([x+0.3, x+1.0], [6.5, 6.9], lw=1.5, color="#d62728"); ax.plot([x+0.3, x+1.0], [5.5, 5.1], lw=1.5, color="#d62728")
        ax.text(x+1.4, 7.2, "FR (rigid)\nmoment joint", fontsize=9, color="#d62728", fontweight="bold")
        ax.text(x+1.4, 4.6, label, fontsize=8.5)
    def _pin_joint(ax, x, label, y=3.0):
        ax.plot([x, x], [1, 9], lw=3, color="#333"); ax.plot([x, x+2.6], [y, y], lw=3, color="#1f77b4")
        ax.add_patch(_plt.Circle((x+0.18, y), 0.16, fill=False, color="#1f77b4", lw=2))
        ax.text(x+0.35, y+1.0, "PINNED (shear) joint", fontsize=9, color="#1f77b4", fontweight="bold")
        ax.text(x+0.35, y-1.4, label, fontsize=8.5)

    ax = axs[1]; ax.set_title("Beam-to-column joints"); ax.axis("off"); ax.set_xlim(0, 10); ax.set_ylim(0, 10)
    if "mix" in joints:
        _fr_joint(ax, 1.6, "moment-frame girders"); _pin_joint(ax, 6.6, "all other girders")
    elif joints.startswith("pin"):
        _pin_joint(ax, 3.0, "ALL girder joints in this model\n(shear / simple connections)", y=6.0)
    else:
        _fr_joint(ax, 3.0, "ALL girder joints in this model")

    if braced:
        ax = axs[2]; ax.set_title("Bracing"); ax.axis("off"); ax.set_xlim(0, 10); ax.set_ylim(0, 10)
        ax.plot([2, 2], [2, 8], lw=3, color="#333"); ax.plot([8, 8], [2, 8], lw=3, color="#333")
        ax.plot([2, 8], [8, 8], lw=3, color="#333"); ax.plot([2, 8], [2, 2], lw=2, color="#666")
        ax.plot([2, 8], [2, 8], lw=2.5, color="#e67e22"); ax.plot([2, 8], [8, 2], lw=2.5, color="#e67e22")
        for (bx, by) in ((2, 2), (8, 8), (2, 8), (8, 2)):
            ax.add_patch(_plt.Circle((bx, by), 0.22, fill=False, color="#e67e22", lw=2))
        ax.text(5, 0.8, "braces: PIN-ENDED axial (truss) members\nno end moments, tension/compression only",
                ha="center", fontsize=8.5, color="#a3540a")
    fig.suptitle("Modelled joint & base fixity", fontweight="bold")
    return _b64(fig)

def _floor_serviceability(pkg):
    ms = (pkg or {}).get("members") or []
    rows = []
    for m in ms:
        sv = m.get("serviceability")
        if not sv: continue
        sec = (m.get("inputs", {}) or {}).get("section", m.get("id", ""))
        rows.append([m.get("id", ""), sec,
                     f"{sv.get('live_in','')}\" = {sv.get('live_ratio','')}", sv.get("live_limit", "L/360"),
                     f"{sv.get('total_in','')}\" = {sv.get('total_ratio','')}", sv.get("total_limit", "L/240"),
                     (f"{sv.get('camber_in')}\"" if sv.get("camber_in") else "&mdash;"),
                     "OK" if sv.get("ok") else "NG"])
    if not rows:
        return "<p class='cnote'>Floor/roof beam deflection &amp; camber: no serviceability data in calc_package.json.</p>"
    return ("<h3>Floor &amp; roof beam deflection and camber (ASCE 7-22 serviceability; AISI S100 Ch. B/L)</h3>"
            "<p>Live-load deflection is limited to L/360 and total-load deflection to L/240. Composite floor members "
            "use a lower-bound transformed moment of inertia I<sub>tr</sub>; the bare-steel (pre-composite, wet-concrete) "
            "dead-load deflection is offset by shop camber.</p>"
            + _table(["Member", "Section", "Live &delta;", "Limit", "Total &delta;", "Limit", "Camber", "&le; limit"], rows))

def _combo_notes(cfg):
    s = cfg["seis"]; SDS = s["SDS"]; rho = _rho_default(cfg); Om0 = s.get("Om0")
    return ("<p>The analysed set is the ASCE 7-22 &sect;2.3 LRFD strength combinations:</p><ul>"
            f"<li><b>Vertical seismic E<sub>v</sub></b> = 0.2&middot;S<sub>DS</sub>D = {0.2*SDS:.2f}D is folded into the "
            f"D factor: seismic combinations use (1.2+0.2S<sub>DS</sub>)D = {1.2+0.2*SDS:.2f}D and "
            f"(0.9&minus;0.2S<sub>DS</sub>)D = {0.9-0.2*SDS:.2f}D.</li>"
            f"<li><b>Redundancy &rho;</b> = {rho} multiplies the horizontal seismic effect Q<sub>E</sub> (&sect;12.3.4).</li>"
            f"<li><b>Overstrength &Omega;<sub>0</sub></b> = {Om0}: the <i>columns-only</i> cases apply "
            "E<sub>m</sub> = &Omega;<sub>0</sub>Q<sub>E</sub> to the capacity-protected columns (&sect;12.4.3) and are "
            "not applied to other members.</li>"
            "<li><b>Directional 100/30</b>: each seismic case carries 100% of F<sub>x</sub> in the loaded direction "
            "plus 30% in the orthogonal direction (&sect;12.5.3/12.5.4).</li>"
            "<li><b>Accidental torsion</b> &plusmn;M<sub>t</sub> = &plusmn;0.05B&middot;F<sub>x</sub> (&sect;12.8.4.2) "
            "is applied in both signs.</li>"
            "<li><b>Net uplift / overturning</b> is checked by the 0.9D combinations (minimum gravity with lateral).</li>"
            "<li><b>Second order</b>: every combination is solved independently through a P-&Delta; analysis; "
            "factored second-order results are never superposed.</li></ul>")

def _modal_mass_check(eX, eY):
    cx = sum(eX); cy = sum(eY)
    return (f"<p><b>Modal mass participation:</b> &Sigma;m<sub>X</sub> = {cx*100:.0f}% "
            f"({'OK' if cx >= 0.90 else 'review &lt; 90%'}), &Sigma;m<sub>Y</sub> = {cy*100:.0f}% "
            f"({'OK' if cy >= 0.90 else 'review &lt; 90%'}); ASCE 7-22 &sect;12.9.1 requires &ge; 90% in each "
            "direction. Periods and mode shapes are shown below and in Chapter 3.</p>")

def _stability_basis_note():
    return ("<p class='cnote'><b>Analysis basis:</b> a second-order P-&Delta; geometric transformation is applied to "
            "the columns under every factored combination (no superposition of factored results). Effective length "
            "K = 1 is used, consistent with a second-order analysis. If the Direct Analysis Method (AISI S100 "
            "&sect;C2) is the design basis, the 0.8&middot;EI / 0.8&tau;<sub>b</sub>&middot;EA stiffness reductions and "
            "notional loads N<sub>i</sub> = 0.002&alpha;Y<sub>i</sub> are confirmed at the member-design stage; the "
            "story stability coefficient &theta; (&sect;12.8.7) and the B<sub>2</sub> amplifier are reported in "
            "Chapter 7.</p>")

_KNOWN_PKG_KEYS = {"members", "connections", "connection_demands", "capacity_design", "scwb",
                   "framework_screen", "building", "code", "note", "notes", "meta", "name",
                   "composite_design"}   # composite_design gets its OWN Chapter-6 section (below)

_COMPOSITE_SYN = {   # tolerant key synonyms seen across agent packages (candidates + gold)
    "pct":   ("partial_composite_ratio", "partial_composite_percent", "partial_composite_pct",
              "degree_of_composite", "composite_ratio"),
    "Qn":    ("stud_Qn_kip", "Qn_kip", "Qn"),
    "studs": ("stud_count_total", "n_studs", "studs_total", "n_per_half_span", "stud_schedule", "studs"),
    "camber":("camber_in", "camber"),
    "ILB":   ("I_LB_in4", "ILB_in4", "I_LB", "lower_bound_I_in4", "I_lb_in4"),
    "wet":   ("wet_stage_phiMn_kipft", "wet_stage_phiMn_kipin", "Mu_wet_kipin",
              "wet_dead_deflection_in", "wet_deflection_in"),
}


def _extra_blocks_section(pkg, src="design/calc_package.json"):
    """Supplementary design records: render every agent-authored TOP-LEVEL calc_package block the
    report does not already show (serviceability, fatigue, crane_runway, equipment_supports,
    analysis_idealization, ponding, ...) through the same nested-table renderer as capacity_design.
    These are real design records the agent wrote for the reviewer -- before this section existed
    they lived only in calc_package.json."""
    if not isinstance(pkg, dict):
        return ""
    parts = []
    for k, v in pkg.items():
        if k in _KNOWN_PKG_KEYS or v in (None, "", [], {}):
            continue
        if not isinstance(v, (dict, list, str, int, float, bool)):
            continue
        blob = v if isinstance(v, str) else json.dumps(v)
        title = str(k).replace("_", " ").title()
        if len(blob) > 12000:                      # guard: a runaway dump must not drown the report
            parts.append("<h4>%s</h4><p class='note'>[block truncated at 12 kB -- full record in "
                         "%s]</p><pre>%s\u2026</pre>"
                         % (title, _html.escape(src), _html.escape(blob[:12000])))
            continue
        parts.append("<h4>%s</h4>" % title)
        parts.append(_capdesign_html(v) if isinstance(v, (dict, list))
                     else "<p>%s</p>" % str(v).replace("<", "&lt;"))
    if not parts:
        return ""
    return ("<h3>Supplementary design records (from the calc package)</h3>"
            "<p>Agent-authored design records beyond the member/connection/capacity-design tables "
            "&mdash; rendered verbatim from <code>%s</code>.</p>" % _html.escape(src) + "".join(parts))


def _composite_section(pkg):
    """Chapter-6 'Composite floor design (AISC 360 Ch. I)' section. Two tolerant sources:
    (a) the standard top-level `composite_design` block (rendered as nested tables), and
    (b) composite values embedded in member capacity dicts (stud counts / partial ratio / camber /
        I_LB / wet stage) -- summarised one row per member. Renders nothing when neither exists."""
    if not isinstance(pkg, dict):
        return ""
    parts = []
    cd = pkg.get("composite_design")
    rows = []
    for m in pkg.get("members", []) or []:
        cap = m.get("capacity") if isinstance(m.get("capacity"), dict) else {}
        found = {}
        for col, keys in _COMPOSITE_SYN.items():
            for kk in keys:
                if kk in cap and cap[kk] not in (None, ""):
                    found[col] = cap[kk]; break
        if found:
            rows.append([str(m.get("id", "")), str((m.get("inputs") or {}).get("section", "")),
                         str(found.get("pct", "&mdash;")), str(found.get("Qn", "&mdash;")),
                         str(found.get("studs", "&mdash;")), str(found.get("camber", "&mdash;")),
                         str(found.get("ILB", "&mdash;")), str(found.get("wet", "&mdash;"))])
    if not cd and not rows:
        return ""
    parts.append("<h3>Composite floor design (AISC 360 Ch. I)</h3>")
    if rows:
        parts.append("<p>Per-member composite design values recorded in the calc package (partial-"
                     "composite ratio, stud strength/schedule, camber, lower-bound moment of inertia, "
                     "unshored wet-concrete stage):</p>")
        parts.append(_table(["member", "section", "partial comp.", "Q<sub>n</sub> (kip)", "studs",
                             "camber (in)", "I<sub>LB</sub> (in<sup>4</sup>)", "wet stage"], rows))
    if cd:
        parts.append("<h4>Composite design record (calc package `composite_design`)</h4>")
        parts.append(_capdesign_html(cd))
    parts.append("<p class='note'>Stud strengths per AISC 360-22 &sect;I8.2a (with deck-rib position "
                 "factors); flexure per &sect;I3.2a; service deflection on the lower-bound moment of "
                 "inertia; camber rule and the unshored construction stage as recorded above.</p>")
    return "".join(parts)


def _member_dc_summary(pkg):
    ms = pkg.get("members") or []
    have = [m for m in ms if m.get("DC") is not None]
    if have:
        gov = max(have, key=lambda m: m.get("DC") or 0.0)
        return (f"<p>Governing member: <b>{gov.get('id')}</b> at D/C = <b>{gov.get('DC'):.2f}</b> "
                f"({gov.get('limit_state')}, combo {(gov.get('inputs', {}) or {}).get('governing_combo', '')}).</p>")
    return ("<p class='cnote'>The <b>demands</b> (unit shears, cumulative stacks, member forces, section properties "
            "and governing combination) are computed by the framework and listed in the schedules above; the "
            "<b>capacities</b>, limit states and D/C ratios are derived by the agent from the AISI S100/S240/S400 "
            "RAG (Appendix A) and are pending for this building.</p>")

def _ch6_notes(cfg):
    return ("<p class='cnote'>Limit states checked per item as applicable (AISI S100): tension (Ch. D — straps "
            "A<sub>n</sub>F<sub>u</sub> vs A<sub>g</sub>F<sub>y</sub>), compression (E2 global with A<sub>e</sub>, "
            "E3 local&ndash;global, <b>E4 distortional</b>), flexure (F2&ndash;F4 with S<sub>e</sub>/I<sub>eff</sub>), "
            "shear (G2), <b>web crippling (G5)</b> at track/bearing with the combined bending&ndash;web-crippling "
            "check, and interaction (H1). Effective-width properties per S100 App. 1 (EWM — no DSM in this "
            "package); framing rules and built-up interconnection per S240; wall shear capacities per S400 "
            "(Chapter 9 covers the capacity-design chain). The EOR should spot-check 2-3 governing items by hand "
            "against Appendix A.</p>")


def _drift_from_forces(cfg, Fdict, direction):
    info = E.build(cfg, "Linear"); NF = info["NF"]; di = 0 if direction == "X" else 1
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    for k in range(1, NF+1):
        f = [0.0]*6; f[di] = Fdict[k]; ops.load(E.mtag(k), *f)
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-8, 100); ops.algorithm("Linear")
    ops.integrator("LoadControl", 1.0); ops.analysis("Static"); ops.analyze(1)
    drift = []; prev = 0.0
    for k in range(1, NF+1):
        d = ops.nodeDisp(E.mtag(k), di+1); drift.append((d-prev)/cfg["heights"][k-1]); prev = d
    return drift

def _wind_drift_section(cfg):
    if not cfg.get("wind"):
        return "<p class='cnote'>Wind drift not evaluated (no wind parameters defined).</p>"
    NF = len(cfg["heights"]); lim = cfg.get("wind_drift_limit", 1.0/400.0)
    try:
        dX = _drift_from_forces(cfg, E.wind_forces(cfg, "X"), "X")
        dY = _drift_from_forces(cfg, E.wind_forces(cfg, "Y"), "Y")
    except Exception as ex:
        return f"<p class='note'>[wind drift run failed: {ex}]</p>"
    rows = [[k, f"{dX[k-1]*100:.3f}", f"{dY[k-1]*100:.3f}", "OK" if max(dX[k-1], dY[k-1]) <= lim else "NG"]
            for k in range(1, NF+1)]
    return ("<h3>Wind drift</h3>"
            f"<p>Interstory drift under the design MWFRS wind vs a serviceability limit of h/{int(round(1/lim))} "
            f"({lim*100:.2f}%). Wind drift has no code-mandated limit (ASCE 7-22 Appendix CC is advisory); a "
            "10-year-MRI service wind may be used for a less conservative check.</p>"
            + _table(["Story", "drift X %", "drift Y %", f"&le; {lim*100:.2f}%"], rows))

def _capdesign_html(cap):
    """Render the agent's capacity_design / scwb block as readable nested tables (NOT raw JSON)."""
    def fmt(v):
        if isinstance(v, bool): return "yes" if v else "no"
        if isinstance(v, float): return ("%.3f" % v).rstrip("0").rstrip(".")
        return str(v)
    def block(d):
        rows = []
        for k, v in d.items():
            label = str(k).replace("_", " ")
            if isinstance(v, dict):
                rows.append([label, block(v)])
            elif isinstance(v, list):
                if any(isinstance(x, dict) for x in v):
                    rows.append([label, "".join(block(x) if isinstance(x, dict)
                                                else "<p>%s</p>" % fmt(x) for x in v)])
                else:
                    rows.append([label, ", ".join(fmt(x) for x in v)])
            else:
                rows.append([label, fmt(v)])
        return _table(["Item", "Value"], rows)
    if isinstance(cap, dict): return block(cap)
    if isinstance(cap, list): return "".join(_capdesign_html(x) for x in cap)
    return "<p>%s</p>" % fmt(cap)

# per-system S400 capacity-design check lists (keys match cfs_systems.SYSTEMS)
_S400_CHECKS = {
    "wsp_shearwall": ("CFS shear wall, wood structural panels (S400 E1)", [
        ("Wall shear capacity", "S400 E1 (seismic, Table E1.3-1, &phi;<sub>v</sub> = 0.60): sheathing thickness &times; fastener schedule &times; sides; "
         "aspect-ratio (2w/h) reduction where h/w &gt; 2. WIND design: S240 B5.2.2.3 (Table B5.2.2.3-2), &phi;<sub>v</sub> = 0.65 (B5.2.3)"),
        ("Type II (perforated) provisions", "adjustment factor; END hold-downs + distributed track anchorage; "
         "uniform-height rule (split stepped lines)"),
        ("Chord studs &amp; anchorage", "capacity design from the EXPECTED wall strength, not ELF force"),
        ("Collectors / story below", "expected wall strength propagated (&Omega;<sub>0</sub> where applicable)"),
        ("Hold-down / rod chain", "cumulative overturning tension per line; rod elongation in drift")]),
    "steelsheet_wall": ("CFS shear wall, steel sheet (S400 E2)", [
        ("Wall shear capacity", "S400 E2 (seismic; table or effective strip method) + aspect-ratio reduction. WIND design: S240 B5.2.2.3 (Table B5.2.2.3-1 / B5.2.2.3.2.1), &phi;<sub>v</sub> = 0.65 (B5.2.3)"),
        ("Chord studs &amp; anchorage", "capacity design from the EXPECTED wall strength"),
        ("Collectors / story below", "expected wall strength propagated"),
        ("Hold-down / rod chain", "cumulative tension; rod elongation in drift")]),
    "strap_braced": ("CFS strap-braced wall (S400 E3)", [
        ("Strap ductility", "A<sub>n</sub>F<sub>u</sub> &ge; A<sub>g</sub>F<sub>y</sub> (yield before net rupture)"),
        ("Strap connections", "capacity design from R<sub>y</sub>F<sub>y</sub>A<sub>g</sub> of the strap — "
         "ELF-force-only sizing is a FAIL"),
        ("Chord studs", "expected strap strength resolved into the chords"),
        ("Anchorage", "expected strap strength into hold-downs/rods and the foundation")]),
    "sbmf": ("CFS special bolted moment frame (S400 E4)", [
        ("Beam expected strength", "at the design drift (bolt-bearing energy dissipation)"),
        ("Columns &amp; connections", "capacity-protected for the expected beam strength"),
        ("Height limit", "35 ft SDC D-F")]),
    "gypsum_wall": ("CFS wall, gypsum board / fiberboard sheathing (S400 E6; E5 is a Canada-only system)", [
        ("Wall shear capacity", "S400 E6 (seismic, Table E6.3-1) — R = 2; wind design per S240 B5.2.2.3 (Table B5.2.2.3-3) with &phi;<sub>v</sub> = 0.65 (B5.2.3); line-by-line upgrade map where mixed"),
        ("Hold-downs / anchorage", "cumulative tension; net-uplift combos")]),
}


def _wind_cap_basis(sysname):
    try:
        import cfs_systems as _CSw
        return _CSw.wind_capacity_basis(sysname)
    except Exception:
        return "AISI S240 B5.2.2.3 (phi_v 0.65, B5.2.3)"


def _s400_capacity_chapter(cfg, pkg):
    """Chapter 9: the S400 (or S100-only) capacity-design and detailing chapter — replaces
    the hot-rolled AISC 341 SCWB/panel-zone chapter."""
    s = cfg["seis"]
    sysname = (cfg.get("system") or s.get("system") or "").lower()
    det = (pkg or {}).get("detailing")
    if det:
        _rows = [[c.get("check", ""), c.get("status", "")] for c in det.get("checks", [])]
        _intro = (f"<p><b>System:</b> {det.get('system','')} &mdash; R = {det.get('R', s.get('R'))}, "
                  f"C<sub>d</sub> = {det.get('Cd', s.get('Cd'))}, &Omega;<sub>0</sub> = {det.get('Omega0', s.get('Om0'))}, "
                  f"SDC {det.get('SDC','')}.</p><p>{det.get('basis','')}</p>")
        return _intro + _table(["Required check", "Status / basis"], _rows)
    cap = (pkg or {}).get("capacity_design")
    if sysname in _S400_CHECKS:
        label, checks = _S400_CHECKS[sysname]
    elif s.get("R", 3) <= 3:
        label = "system not specifically detailed for seismic (R &le; 3)"
        checks = [("S400 detailing", "NOT applicable at R &le; 3 — design per AISI S100 alone; state this explicitly"),
                  ("Wind governance", "prove whether wind or seismic governs each direction"),
                  ("Net uplift", "0.9D+1.0W anchorage path is still required")]
    else:
        label = "CFS seismic system (declare cfg['system'] from the S400 set)"
        checks = [("System declaration", "cfg['system'] must name the exact S400 system — the report will not infer it from R")]
    rows = [[nm, basis, "see Appendix A" if cap else "agent-derived (S400 RAG) &mdash; pending"]
            for nm, basis in checks]
    intro = (f"<p>For the {label} (R = {s.get('R')}), the required capacity-design and detailing checks are listed "
             "below. These are derived by the agent from the AISI S400 RAG — the capacity-design "
             "chain propagates EXPECTED strengths, story by story; values populate from the calc package where "
             "present.</p>")
    extra = ("<h4>Capacity-design results (from the calc package)</h4>" + _capdesign_html(cap)) if cap else ""
    note = ("<p class='cnote'>Specific hold-down/connector products (the class-envelope bands are representative of "
            "commercially available devices), concrete anchorage detailing (ACI 318 Ch. 17) and the C&amp;C "
            "cladding-fastener checks are confirmed on the drawings and submittals (delegated). Wind "
            "capacity basis for this system: " + _e(_wind_cap_basis(sysname)) +
            " &mdash; S400 has no separate wind columns (Tables E1.3-1/E2.3-1 cover seismic and other "
            "in-plane loads; gypsum/fiberboard Table E6.3-1 is seismic only).</p>")
    return intro + _table(["Required check", "Basis", "Status"], rows) + extra + note


def _e(x):
    """HTML-escape agent/seed text for the CFS tables (criterion strings contain '<' etc.)."""
    return _html.escape(str(x)) if x is not None else ""


# ============================================================ CFS publication layer (2026-10)
# US-practice rules for the published CFS calc packages (review CFS_batch1-3, HR template audit):
#  * publication mode by default -- no harness / agent / RAG wording in the report body; the
#    source-grounding table, framework screening notes and the activity log go to qa_annex.html;
#  * ONE number formatter (_cn) -- no raw floats, dicts, None or Python reprs;
#  * schedule columns print the demand the D/C uses beside the ELF value, one basis per column;
#  * long citations once per table (numbered references under the table);
#  * template blocks only where they apply to the system.
_CFS_PUB = [True]          # publication mode of the report being rendered (build_report_cfs)

_PUB_SUBS = [(re.compile(p, f), r) for p, f, r in (
    (r"\s*\[(?:RAG|rag)\b[^\]]*\]", 0, ""),
    (r"\bnot\s+retrieved\b", re.I, "not verified"),
    (r"\s*--\s*logged\b", re.I, ""),
    (r"\s*\[\s*(?:both\s+)?retrieved[^\]]*\]", re.I, ""),
    (r"\s*\(\s*(?:both\s+)?retrieved(?:\s+verbatim)?\s*\)", re.I, ""),
    (r"[,;]?\s*(?:--\s*|-\s+)?(?:both\s+)?retrieved(?:\s+verbatim)?\b", re.I, ""),
    (r"\bverbatim\b", re.I, ""),
    (r"[,;]?\s*(?:--\s*)?(?:logged|flagged|recorded|noted)\s+in\s+RAG_QUALITY\b", re.I, ""),
    (r"\bRAG_QUALITY\b", 0, ""),
    (r"\bRAG[- ]GROUNDED(?:\s+(?:REDO|SOLUTION))?\b[.:]?", re.I, ""),
    (r"\bRAG[ -]chunks?\b[^;,)\]]*", re.I, ""),
    (r"\bthe RAG\b", 0, "the standard"),
    (r"\bRAG\b", 0, "standard"),
    (r"\bSEEDED\b(?:\s+numeric)?\s*:?\s*", 0, ""),
    (r"\bSEED\s*:\s*", 0, ""),
    (r"\bAGENT\s*:\s*", re.I, ""),
    (r"\b(?:the\s+)?(?:design\s+)?agent's\b", re.I, "the"),
    (r"\bagent[- ](?:derived|computed|authored|designed|filled)\b", re.I, "designed"),
    (r"\b(?:the\s+)?(?:design\s+)?agent\b", re.I, "the designer"),
    (r"\bseeded\b", re.I, "preliminary"),
    (r"\bseed\s+(values?|demands?|forces?)\b", re.I, r"preliminary \1"),
    (r"\bseeds\b", re.I, "preliminary values"),
    (r"\bseed\b(?!\s+band)", re.I, "preliminary value"),
    (r"\b(?:the\s+)?pipeline's\s+framework\s+", re.I, "the engine's "),
    (r"\bthe\s+pipeline's\b", re.I, "the engine's"),
    (r"\bframework\s+(screening|value|values|text)\b", re.I, r"engine \1"),
    (r"(?:design/)?calc_package(?:_cfs)?\.json", 0, "the calculation package"),
    (r"\s*\(?\bISSUES\.md\b\)?", 0, ""),
    (r"\bfill_package\.py\b", 0, ""),
    # capitals used for emphasis read as shouting in a calculation package (acronyms are untouched)
    (r"\b(BOTH|TENSION|COMPRESSION|REQUIRED|NEVER|ONLY|EVERY|ALSO|MIRRORED|ALWAYS|MUST|WITHOUT|OVERTURNING|"
     r"EXPECTED|SELECTED|EACH|BOTTOM|TOP)\b(?![-/]?[A-Z0-9])", 0, lambda m: m.group(1).lower()),
)]
_CFG_KEY_RX = re.compile(r"(?:cfg|pkg|calc_package_cfs|calc_package)\[['\"]([A-Za-z0-9_]+)['\"]\](?:\[['\"]([A-Za-z0-9_]+)['\"]\])?")


def _pub_scrub(s):
    """Remove design-process (harness) wording from a text that is printed in the report body."""
    for rx, rep in _PUB_SUBS:
        s = rx.sub(rep, s)
    s = _CFG_KEY_RX.sub(lambda m: (m.group(2) or m.group(1)).replace("_", " "), s)
    s = re.sub(r"\(\s*[,;:]\s*", "(", s)
    s = re.sub(r"\s*[,;:]\s*\)", ")", s)
    s = re.sub(r"\(\s*\)|\[\s*\]", "", s)
    s = re.sub(r"\s+([,;)\]]|\.(?!\d))", r"\1", s)
    s = re.sub(r"([,;])(?:\s*[,;])+", r"\1", s)
    s = re.sub(r"\s*--\s*(?=[)\];,]|$)", "", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip(" ;,")


def _pt(x):
    """Agent/seed TEXT for the report body: escaped, and scrubbed of harness wording in
    publication mode. Numbers and containers go through the one number formatter."""
    if x is None:
        return ""
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, (int, float, dict, list, tuple)):
        return _cn(x)
    s = str(x)
    if not _CFS_PUB[0]:
        return _html.escape(s)
    s = _html.escape(_pub_scrub(s))
    # engineering symbols for the framework / designer shorthand
    s = s.replace("&gt;=", "&ge;").replace("&lt;=", "&le;").replace(" -&gt; ", " &rarr; ")
    s = re.sub(r"(?<=\S) x (?=[\d(])", " &times; ", s)          # '4.5 psf x 15.0 ft' products
    s = re.sub(r"\bOm0_eff\b", "&Omega;<sub>0,eff</sub>", s)
    s = re.sub(r"\bOm(?:ega_?)?0\b", "&Omega;<sub>0</sub>", s)
    s = re.sub(r"\bOmega_E\b", "&Omega;<sub>E</sub>", s)
    s = re.sub(r"(?<![&\w])phi_([a-z]{1,2})\b(\s*\*\s*)?", r"&phi;<sub>\1</sub>", s)
    s = re.sub(r"(?<![&\w])phi\s*\*\s*", "&phi;", s)
    s = re.sub(r"(?<![&\w])phi\b", "&phi;", s)
    return s


def _cn(v, nd=None):
    """THE number formatter of the CFS report (no raw floats / dicts / None / reprs)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return "&mdash;"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return "%d" % v
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return "&mdash;"
        if nd is not None:
            return ("%%.%df" % nd) % v
        a = abs(v)
        if a == 0.0:
            return "0"
        if a >= 1000.0:
            return "%.0f" % v
        if a >= 10.0:
            s = "%.1f" % v
            return s[:-2] if s.endswith(".0") else s
        if a >= 1.0:
            s = ("%.2f" % v).rstrip("0")
            return s + "0" if s.endswith(".") else s
        if a >= 0.001:
            return "%.3g" % v
        return "%.2e" % v
    if isinstance(v, dict):
        items = [(k, x) for k, x in v.items() if x not in (None, "", [], {})]
        return "; ".join("%s: %s" % (_hk(k), _cn(x)) for k, x in items) or "&mdash;"
    if isinstance(v, (list, tuple)):
        return ", ".join(_cn(x) for x in v if x not in (None, "")) or "&mdash;"
    return _pt(v)


def _fnum(x):
    """float or None (numbers only -- strings are NOT parsed)."""
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x) if x == x else None
    return None


def _cap_num(x):
    """Numeric design strength from a number or a capacity string ('phi_v*vn = 1692 plf (...)',
    'phi*Rn = 108 kip'): the value after '=' with a unit first, else the first number with a
    unit, else None."""
    if _fnum(x) is not None:
        return _fnum(x)
    if not isinstance(x, str):
        return None
    m = re.search(r"=\s*(\d[\d,]*(?:\.\d+)?)\s*(?:plf|klf|kips?|kip-in|k-in|lb)\b", x, re.I) or \
        re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(?:plf|klf|kips?|kip-in|k-in)\b", x, re.I)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None
    m = re.match(r"\s*(\d[\d,]*(?:\.\d+)?)\s*$", x)
    return float(m.group(1).replace(",", "")) if m else None


_HK = {"DC": "D/C", "v_unit_plf": "seismic unit shear, &rho;E (plf)",
       "v_wind_plf": "wind unit shear, 1.0W (plf)", "V_kip": "shear V (kip)",
       "T_cum_kip": "ELF&middot;&rho; overturning tension (kip)",
       "T_cd_seed_kip": "capacity-design tension T<sub>cd</sub> (kip)",
       "T_bay_seed_kip": "ELF&middot;&rho; tension per bay (kip)",
       "T_wind_kip": "0.9D+1.0W tension (kip)", "T_design_kip": "design tension (kip)",
       "k_kip_in": "anchorage stiffness (kip/in.)", "capacity": "design strength",
       "limit_state": "limit state(s)", "cited": "reference", "selection": "selection",
       "governing_combo": "governing combination", "P_kip": "axial force P (kip)",
       "M_kipin": "moment M (kip-in.)", "V_base_kip": "base shear (kip)",
       "T_net_uplift_kip": "net uplift (kip)", "M_base_kipin": "base moment (kip-in.)",
       "uplift_combo": "uplift combination", "M_transfer_kipin": "joint moment (kip-in.)",
       "Ca_effective": "Type II factor C<sub>a</sub>", "wall_type": "wall type",
       "fastener_schedule": "fastener schedule", "sheathing": "sheathing",
       "P_cum_kip_by_story": "cumulative axial by story (kip)", "trib_ft": "tributary width (ft)",
       "device_class": "device class", "section": "section", "member": "member",
       "demand": "demand", "direction": "direction", "story": "story", "line": "line",
       "Om0_eff": "&Omega;<sub>0</sub> (effective)", "Omega_E": "&Omega;<sub>E</sub>",
       "Vn_selected_kip": "V<sub>n</sub> of the selected assembly (kip)",
       "T_Om0_stack_kip": "tension at the &Omega;<sub>0</sub> level (kip)",
       "T_OmegaE_Vn_stack_kip": "tension at &Omega;<sub>E</sub>V<sub>n</sub> (kip)",
       "Ve_cap_by_story_kip": "capacity-design story shear by story (kip)",
       "dead_relief_kip_available": "0.9D relief available (kip)",
       "L_ft": "line length L (ft)", "v_elf_plf": "&rho;-ELF unit shear (plf)",
       "v_elf_by_story_plf": "&rho;-ELF unit shear by story (plf)",
       "v_cd_seed_plf": "capacity-design unit shear v<sub>cd</sub> (plf)",
       "v_cd_by_story_plf": "capacity-design unit shear by story (plf)",
       "v_design_plf": "design unit shear (plf)", "v_design_klf": "design unit shear (klf)",
       "design_basis": "basis of the design demand"}
_HK_UNITS = (("_kip_in", "kip-in."), ("_kipin", "kip-in."), ("_kip_ft", "kip-ft"),
             ("_kipft", "kip-ft"), ("_klf", "klf"), ("_plf", "plf"), ("_psf", "psf"),
             ("_ksi", "ksi"), ("_kips", "kip"), ("_kip", "kip"), ("_in2", "in.&sup2;"),
             ("_in4", "in.<sup>4</sup>"), ("_in", "in."), ("_ft", "ft"), ("_hz", "Hz"))


def _hk(k):
    """Human label of a package key (unit suffixes spelled out)."""
    k = str(k)
    if k in _HK:
        return _HK[k]
    for suf, u in _HK_UNITS:
        if k.lower().endswith(suf) and len(k) > len(suf):
            return "%s (%s)" % (_html.escape(k[:-len(suf)].replace("_", " ")), u)
    return _html.escape(k.replace("_", " "))


def _cdc(v):
    """D/C cell: 2 decimals; a missing D/C is shown as PENDING (an undesigned item)."""
    if _fnum(v) is not None:
        return "%.2f" % float(v)
    if isinstance(v, str) and v.strip():
        return _pt(v)
    return "<b>PENDING</b>"


def _cdc_e(e):
    """D/C cell of an entry: a D/C of exactly 0 is shown as 'delegated' or 'existing (not in
    scope)' when the entry says so (never a bare 0.00 that reads as a designed item)."""
    v = (e or {}).get("DC")
    if _fnum(v) == 0.0:
        t = " ".join(str(e.get(k) or "") for k in ("selection", "section", "limit_state", "capacity",
                                                    "note", "cited", "waived", "description")).lower()
        if re.search(r"delegat|by others|manufacturer|proprietary|deferred", t):
            return "delegated"
        if re.search(r"existing|not in scope|out of scope", t):
            return "existing (not in scope)"
    return _cdc(v)


class _Refs:
    """Citation book: a long reference string is printed ONCE per table (numbered notes)."""

    def __init__(self):
        self.items = []

    def ref(self, s):
        s = (str(s).strip() if s is not None else "")
        if not s:
            return "&mdash;"
        if s not in self.items:
            self.items.append(s)
        return "[%d]" % (self.items.index(s) + 1)

    def single(self):
        return len(self.items) <= 1

    def notes(self):
        if not self.items:
            return ""
        if len(self.items) == 1:
            return "<p class='tnote'><b>Reference (all rows):</b> %s</p>" % _pt(self.items[0])
        return ("<p class='tnote'><b>References:</b></p><ol class='tnote'>"
                + "".join("<li>%s</li>" % _pt(x) for x in self.items) + "</ol>")


def _ref_table(head, rows, refs, ref_col=-1):
    """Table whose last column holds [n] reference marks; with a single reference the column
    is dropped and the reference is printed once under the table."""
    if refs.single():
        head = [h for i, h in enumerate(head) if i != len(head) + ref_col]
        rows = [[c for i, c in enumerate(r) if i != len(r) + ref_col] for r in rows]
    return _table(head, rows) + refs.notes()


def _cfs_sys(cfg, pkg=None):
    return str((cfg or {}).get("system") or ((cfg or {}).get("seis") or {}).get("system")
               or (pkg or {}).get("system") or "").lower()


def _cfs_dir_system(cfg, d):
    sbd = (cfg or {}).get("system_by_dir") or {}
    return str(sbd.get(d) or _cfs_sys(cfg)).lower()


def _cfs_capacity_design_required(cfg):
    try:
        import cfs_systems as _CS
        s = (cfg or {}).get("seis") or {}
        sysname = _cfs_sys(cfg)
        if sysname not in _CS.SYSTEMS:
            return False
        cat = _CS.sdc(float(s.get("SDS", 0) or 0), float(s.get("SD1", 0) or 0),
                      float(s.get("S1", 0) or 0), str((cfg or {}).get("risk_cat", "II")))
        return bool(_CS.capacity_design_required(sysname, s.get("R"), cat)[0])
    except Exception:
        return False


def _num_or(x, fmt="%.1f", dash="&mdash;"):
    return (fmt % x) if isinstance(x, (int, float)) and not isinstance(x, bool) else \
        (_pt(x) if x not in (None, "") else dash)


def _waived_cell(e):
    return "<b>WAIVED</b>: %s" % _pt(e.get("waived"))


_DRIFT_METHOD = {
    "wsp_shearwall": "AISI S400-20 Eq. E1.4.1.4-1 (wood structural panels: bending, shear, "
                     "fastener-slip and anchorage terms)",
    "steelsheet_wall": "AISI S400-20 Eq. E2.4.1.4-1 (steel sheet: bending, shear, fastener-slip "
                       "and anchorage terms)",
    "strap_braced": "principles of mechanics for strap bracing (AISI S400-20 E3.4.4): strap "
                    "elongation plus chord axial and anchorage deformation",
    "gypsum_wall": "principles of mechanics for gypsum / fiberboard walls (AISI S400-20 E6.4.1.4) "
                   "with the declared apparent shear rigidity",
}
_SYS_SHORT = {"wsp_shearwall": "wood-structural-panel shear walls",
              "steelsheet_wall": "steel-sheet shear walls", "strap_braced": "strap-braced walls",
              "gypsum_wall": "gypsum / fiberboard walls", "sbmf": "special bolted moment frames",
              "not_detailed": "system not specifically detailed"}


def _cfs_drift_rows(dt, extended=False):
    """Generic renderer for drift_table rows: the wall shape (direction/line/story/
    drift_amplified/limit/ok) and serviceability rows (check/ratio/value_in/criterion/DC/ok).
    extended=True (wall packages) adds the single-story / rotation-from-below split
    (12.8.6.5) and theta / theta_max (12.8.7)."""
    rows = []
    for d in dt:
        if not isinstance(d, dict):
            continue
        item = d.get("check") or "%s / line %s / story %s" % (d.get("direction", ""),
                                                             d.get("line", ""), d.get("story", ""))
        val = next((d.get(k) for k in ("drift_design", "drift_amplified_design",
                                         "drift_amplified", "ratio", "drift_ratio", "theta",
                                         "value_in", "Delta_in", "value") if d.get(k) is not None), None)
        lim = d.get("limit") if d.get("limit") is not None else d.get("criterion")
        if isinstance(lim, str) and lim.lstrip().upper().startswith("AGENT"):
            lim = None                                  # framework placeholder, not a criterion
        _dcv, _vv, _lv = _fnum(d.get("DC")), _fnum(val), _fnum(lim)
        num_fail = (_dcv is not None and _dcv > 1.0 + 1e-9) or \
            (_vv is not None and _lv is not None and _lv > 0 and _vv > _lv * (1 + 1e-6))
        if d.get("waived"):
            status = _waived_cell(d)
        elif num_fail:
            status = "<b>NG</b>"
        elif d.get("ok") is True:
            status = "OK"
        elif d.get("ok") is False:
            status = "<b>NG</b>"
        else:
            status = "<b>no verdict</b>"
        lim_cell = _pt(lim) if lim is not None else "<b>not stated</b>"
        dc_cell = _cdc(d.get("DC")) if d.get("DC") is not None else "&mdash;"
        if extended:
            ss, rot = d.get("drift_single_story"), d.get("drift_rotation_from_below")
            split = ("%s / %s" % (_num_or(ss, "%.4f"), _num_or(rot, "%.4f"))
                     if (ss is not None or rot is not None) else "&mdash;")
            th, thm = d.get("theta"), d.get("theta_max")
            thc = ("%s / %s" % (_num_or(th, "%.3f"), _num_or(thm, "%.3f"))
                   if th is not None else "&mdash;")
            if th is not None and thm and th > thm:
                thc = "<b>%s</b>" % thc
            rows.append([_pt(item), _num_or(val, "%.4f"), split, thc, lim_cell, dc_cell, status])
        else:
            rows.append([_pt(item), _num_or(val, "%.4g"), lim_cell, dc_cell, status])
    return rows


def _cfs_drift_section(pkg, cfg=None):
    """Story-drift record of a WALL package, titled by the deflection method of each
    direction's system (the S400 four-term equation only for WSP / steel-sheet walls)."""
    dt = [d for d in (pkg.get("drift_table") or []) if isinstance(d, dict)]
    wall = [d for d in dt if "drift_amplified" in d or "drift_design" in d]
    if not wall:
        return ""
    h = ["<h3>Story drift (ASCE 7-22 &sect;12.8.6, Table 12.12-1)</h3>"]
    hdr = ["Line / story", "C<sub>d</sub>&delta;<sub>x</sub>/(I<sub>e</sub>h<sub>sx</sub>)",
           "Single-story / rotation from below", "&theta; / &theta;<sub>max</sub> (&sect;12.8.7)",
           "Limit", "D/C", "Status"]
    for dirn in sorted({str(d.get("direction") or "") for d in wall}):
        rows = [d for d in wall if str(d.get("direction") or "") == dirn]
        sysd = _cfs_dir_system(cfg or {}, dirn) if cfg else str(pkg.get("system") or "").lower()
        meth = _DRIFT_METHOD.get(sysd)
        lab = _SYS_SHORT.get(sysd, sysd.replace("_", " ") or "walls")
        if meth:
            txt = ("Single-story deflection by %s, plus the rotation carried up from the stories "
                   "below (&sect;12.8.6.5), amplified by C<sub>d</sub>/I<sub>e</sub> and by "
                   "1/(1&minus;&theta;) where 0.10 &lt; &theta; &le; &theta;<sub>max</sub>." % meth)
        else:
            txt = ("Story drift from the analysis model (%s); the S400 wall-deflection equations "
                   "do not apply to this system." % _html.escape(lab))
        h.append("<h4>%s direction &mdash; %s</h4><p>%s</p>" % (_html.escape(dirn or "&mdash;"),
                                                            _html.escape(lab), txt))
        tr = _cfs_drift_rows(rows, extended=True)
        for r, d in zip(tr, rows):
            r[0] = "%s / %s" % (_pt(d.get("line")), _pt(d.get("story")))
        h.append(_table(hdr, tr))
    return "".join(h)


def _wall_design_demand(w):
    """(demand, basis label) -- the unit shear (plf) the wall D/C is computed on: an explicit
    design-demand field, else D/C x design strength; the label says which basis it is."""
    seis, wind = _fnum(w.get("v_unit_plf")), _fnum(w.get("v_wind_plf"))
    val = None
    for k in ("v_design_plf", "v_demand_plf", "demand_plf", "v_u_plf", "vu_plf"):
        if _fnum(w.get(k)) is not None:
            val = _fnum(w.get(k))
            break
    if val is None and _fnum(w.get("demand")) is not None:
        val = _fnum(w.get("demand"))
    derived = False
    if val is None:
        dc, cap = _fnum(w.get("DC")), _cap_num(w.get("capacity"))
        if dc is not None and cap:
            val, derived = dc * cap, True
    basis = w.get("design_basis") or w.get("demand_basis_used")
    if val is None:
        return None, "&mdash;"
    if basis:
        return val, _pt(basis)
    if seis and abs(val - seis) <= 0.03 * seis + 1.0:
        return (seis if derived else val), "seismic (&rho;E)"
    if wind and abs(val - wind) <= 0.03 * wind + 1.0:
        return (wind if derived else val), "wind (1.0W)"
    if seis and _fnum(w.get("demand_scale")) is not None:
        return val, "seismic &times; %s" % _cn(_fnum(w.get("demand_scale")))
    return val, ("D/C &times; strength (basis not stated)" if derived else "basis not stated")


def _is_strap_slot(cfg, w, pkg=None):
    import consistency as _CC
    if _CC.wall_slot_system(pkg or {}, w, cfg) == "strap_braced":
        return True
    sh = str(w.get("sheathing") or "").lower()
    return bool(re.search(r"\bnone\b|x-?strap|strap bay", sh))


def _cfs_wall_schedule(pkg, cfg=None):
    walls = [w for w in (pkg.get("wall_lines") or []) if isinstance(w, dict)]
    if not walls:
        return ""
    out = []
    for strap in (False, True):
        grp = [w for w in walls if _is_strap_slot(cfg, w, pkg) == strap]
        if not grp:
            continue
        refs = _Refs()
        rows = []
        for w in grp:
            seis, wind = _fnum(w.get("v_unit_plf")), _fnum(w.get("v_wind_plf"))
            dem, basis = _wall_design_demand(w)
            if w.get("waived"):
                rows.append([_pt(w.get("id")), _pt(w.get("story")), _num_or(seis, "%.0f"),
                             _num_or(wind, "%.0f"), "&mdash;", "&mdash;", _waived_cell(w), "",
                             "", "", refs.ref(w.get("cited"))])
                continue
            if strap:
                brace = next((w.get(k) for k in ("selection", "strap", "straps", "bracing",
                                                 "strap_schedule") if w.get(k)), None)
                if brace is None and w.get("sheathing") and \
                        not re.search(r"\bnone\b", str(w.get("sheathing") or ""), re.I):
                    brace = w.get("sheathing")
                conn = w.get("connection") or w.get("strap_connection")
                if brace is None:                       # strap data recorded in the fastener field
                    brace = w.get("fastener_schedule")
                elif conn is None:
                    conn = w.get("fastener_schedule")
                c1, c2 = _pt(brace) or "<b>PENDING</b>", _pt(conn) or "&mdash;"
            else:
                c1 = _pt(w.get("sheathing")) or "<b>PENDING</b>"
                c2 = _pt(w.get("fastener_schedule")) or "<b>PENDING</b>"
            cap = w.get("capacity")
            rows.append([_pt(w.get("id")), _pt(w.get("story")), _num_or(seis, "%.0f"),
                         _num_or(wind, "%.0f"), _num_or(dem, "%.0f"), basis, c1, c2,
                         _num_or(cap, "%.0f") if _fnum(cap) is not None else (_pt(cap) or "&mdash;"),
                         _cdc(w.get("DC")), refs.ref(w.get("cited"))])
        if strap:
            title = "Strap-braced walls &mdash; strap bracing schedule (per line per story)"
            head = ["Wall", "Story", "Seismic &rho;E (plf)", "Wind 1.0W (plf)",
                    "Design demand (plf)", "Demand basis", "Strap bracing (per bay)",
                    "Strap-end connection", "Design strength", "D/C", "Ref."]
        else:
            title = "Shear walls &mdash; sheathing and fastener schedule (per line per story)"
            head = ["Wall", "Story", "Seismic &rho;E (plf)", "Wind 1.0W (plf)",
                    "Design demand (plf)", "Demand basis", "Sheathing", "Fasteners (edge / field)",
                    "&phi;v<sub>n</sub> (plf)", "D/C", "Ref."]
        out.append("<h3>%s</h3><p>Seismic demand = tributary unit shear with the 5%% accidental "
                   "shift and &rho; (Section 3); wind demand = the 1.0W MWFRS unit shear. The "
                   "<i>design demand</i> is the value the D/C is computed on, and its basis is "
                   "stated per row.</p>" % title + _ref_table(head, rows, refs))
    return "".join(out)


def _hd_basis(d, T_des):
    """Basis label of a hold-down design tension (which demand the D/C uses)."""
    txt = d.get("design_basis") or d.get("demand_basis_used")
    if txt:
        return _pt(txt)
    if T_des is None:
        return "&mdash;"

    def near(x, tol=0.02):
        x = _fnum(x)
        return x is not None and x > 0 and abs(T_des - x) <= tol * x + 0.05
    tcd, rel = _fnum(d.get("T_cd_seed_kip")), _fnum(d.get("dead_relief_kip_available"))
    if near(tcd):
        return "capacity design, T<sub>cd</sub> (S400 B3.4)"
    if tcd and rel and near(tcd - rel):
        return "T<sub>cd</sub> less 0.9D relief"
    if near(d.get("T_wind_kip")):
        return "wind, 0.9D+1.0W"
    if near(d.get("T_bay_seed_kip")) or near(d.get("T_cum_kip")):
        return "ELF&middot;&rho;"
    if tcd and T_des > tcd:
        return "above T<sub>cd</sub> (designer's value)"
    return "designer's value (basis not stated)"


def _cfs_holddown_schedule(pkg, cfg=None):
    import consistency as _CC
    hds = [d for d in (pkg.get("holddowns") or []) if isinstance(d, dict)]
    if not hds:
        return ""
    refs = _Refs()
    rows = []
    for d in hds:
        T_des, _src = _CC.hd_design_demand(d)
        sel = next((d.get(k) for k in ("selection", "device", "rod", "device_selected",
                                       "rod_size") if d.get(k)), None)
        designed = d.get("DC") is not None or bool(d.get("checks"))
        dev = _pt(sel) if sel else (_pt(d.get("device_class")) +
                                    ("" if designed else " <i>(seed band)</i>"))
        elf = _num_or(d.get("T_cum_kip"))
        if d.get("T_bay_seed_kip") is not None and not _same(d.get("T_bay_seed_kip"), d.get("T_cum_kip")):
            elf += " (per bay %s)" % _num_or(d.get("T_bay_seed_kip"))
        if d.get("waived"):
            rows.append([_pt(d.get("id")), _pt(d.get("direction")), elf,
                         _num_or(d.get("T_cd_seed_kip")), _num_or(d.get("T_wind_kip")),
                         _waived_cell(d), "", "", "", "", refs.ref(d.get("cited"))])
            continue
        cap = d.get("capacity")
        rows.append([_pt(d.get("id")), _pt(d.get("direction")), elf,
                     _num_or(d.get("T_cd_seed_kip")), _num_or(d.get("T_wind_kip")),
                     ("<b>%s</b>" % _num_or(T_des)) if T_des is not None else "<b>PENDING</b>",
                     _hd_basis(d, T_des), dev,
                     _num_or(cap) if _fnum(cap) is not None else (_pt(cap) or "&mdash;"),
                     _cdc(d.get("DC")), refs.ref(d.get("cited"))])
    cd = _cfs_capacity_design_required(cfg) if cfg else True
    intro = ("<p>Overturning tension per wall line, accumulated top-down. ELF&middot;&rho; = the "
             "&rho;-amplified ELF stack; T<sub>cd</sub> = the capacity-design tension (expected "
             "strength &Omega;<sub>E</sub>V<sub>n</sub> of the wall, not to exceed the "
             "&Omega;<sub>0</sub>-level effect; S400 B3.4); wind = 0.9D+1.0W. The <i>design T</i> "
             "is the tension the D/C is computed on. Devices resist tension only; beyond the "
             "discrete hold-down range a continuous rod is designed and its elongation enters the "
             "drift.</p>" if cd else
             "<p>Overturning tension per wall line, accumulated top-down (ELF&middot;&rho; and "
             "0.9D+1.0W). Capacity design is not required for this system and SDC (S400 A1.2.3), "
             "so T<sub>cd</sub> is shown for reference only. The <i>design T</i> is the tension "
             "the D/C is computed on.</p>")
    return ("<h3>Hold-down / continuous rod schedule</h3>" + intro
            + _ref_table(["ID", "Dir", "ELF&middot;&rho; T (kip)", "T<sub>cd</sub> (kip)",
                          "Wind T (kip)", "Design T (kip)", "Basis of design T", "Device / rod",
                          "&phi;T<sub>n</sub> (kip)", "D/C", "Ref."], rows, refs))


def _cfs_anchorage_schedule(pkg, cfg=None):
    """Per-line anchorage (sill / bottom-track shear anchorage and hold-down anchorage at the base
    story): the rho-ELF, wind and capacity-design unit shears beside the design shear the D/C
    uses, and T_cd beside the design tension."""
    import consistency as _CC
    slots = [a for a in pkg.get("anchorage") or [] if isinstance(a, dict) and a.get("line") is not None]
    if not slots:
        return ""
    refs = _Refs()
    rows = []
    for a in slots:
        v_elf, v_w, v_cd = _fnum(a.get("v_elf_plf")), _fnum(a.get("v_wind_plf")), _fnum(a.get("v_cd_seed_plf"))
        dem = _CC._shear_demand_plf(a)
        T_des = next((_fnum(a.get(k)) for k in ("T_design_kip", "T_demand_kip", "Tu_kip")
                      if _fnum(a.get(k)) is not None), None)

        def near(x):
            return x is not None and dem is not None and abs(dem - x) <= 0.02 * x + 1.0
        lab = a.get("design_basis") or a.get("demand_basis_used")
        lab = _pt(lab) if lab else ("&mdash;" if dem is None else
                                    "capacity design (S400 B3.4)" if near(v_cd) else
                                    "wind (1.0W)" if near(v_w) else "seismic (&rho;E)" if near(v_elf) else
                                    "designer's value (basis not stated)")
        if a.get("waived"):
            rows.append([_pt(a.get("id")), _pt(a.get("direction")), _num_or(v_elf, "%.0f"), _num_or(v_w, "%.0f"),
                         _num_or(v_cd, "%.0f"), "", _waived_cell(a), _num_or(a.get("T_cd_seed_kip")), "", "", "",
                         "", refs.ref(a.get("cited"))])
            continue
        cap = a.get("capacity")
        rows.append([_pt(a.get("id")), _pt(a.get("direction")), _num_or(v_elf, "%.0f"), _num_or(v_w, "%.0f"),
                     _num_or(v_cd, "%.0f"), ("<b>%s</b>" % _num_or(dem, "%.0f")) if dem is not None else "<b>PENDING</b>",
                     lab, _num_or(a.get("T_cd_seed_kip")), _num_or(T_des),
                     _pt(a.get("selection")) or "<b>PENDING</b>",
                     _num_or(cap) if _fnum(cap) is not None else (_pt(cap) or "&mdash;"),
                     _cdc_e(a), refs.ref(a.get("cited"))])
    cd = _cfs_capacity_design_required(cfg) if cfg else any(_fnum(a.get("v_cd_seed_plf")) for a in slots)
    intro = ("<p>Anchorage of each wall line at the base story: sill / bottom-track shear anchorage (unit "
             "shear) and the hold-down anchorage (tension). &rho;-ELF and wind unit shears; "
             "v<sub>cd</sub> = the line's capacity-design story shear / L (expected strength "
             "&Omega;<sub>E</sub>V<sub>n</sub>, not above the &Omega;<sub>0</sub>-level effect; S400 B3.4). "
             "The <i>design v</i> and <i>design T</i> are the demands the D/C is computed on; concrete "
             "anchorage to ACI 318-19 Ch. 17.</p>" if cd else
             "<p>Anchorage of each wall line at the base story (unit shear and hold-down tension). Capacity "
             "design is not required for this system and SDC (S400 A1.2.3).</p>")
    return ("<h3>Anchorage schedule (per wall line)</h3>" + intro
            + _ref_table(["ID", "Dir", "&rho;-ELF v (plf)", "Wind v (plf)", "Capacity-design v<sub>cd</sub> (plf)",
                          "Design v (plf)", "Basis of design v", "T<sub>cd</sub> (kip)", "Design T (kip)",
                          "Anchorage", "Design strength", "D/C", "Ref."], rows, refs))


def _entry_design_demand(e, keys, derive=True):
    for k in keys:
        if _fnum(e.get(k)) is not None:
            return _fnum(e.get(k)), k
    if _fnum(e.get("demand")) is not None:
        return _fnum(e.get("demand")), "demand"
    if not derive:
        return None, None
    dc, cap = _fnum(e.get("DC")), _cap_num(e.get("capacity"))
    if dc is not None and cap:
        return dc * cap, "D/C x strength"
    return None, None


def _line_tcd(pkg, e):
    lines = ((pkg.get("capacity_design") or {}).get("lines") or {}) \
        if isinstance(pkg.get("capacity_design"), dict) else {}
    if _fnum(e.get("T_cd_seed_kip")) is not None:
        return _fnum(e.get("T_cd_seed_kip"))
    if e.get("line") is None:
        return None
    ln = lines.get("%s:%s" % (e.get("direction"), e.get("line"))) or next(
        (v for k, v in lines.items() if str(k).split(":")[-1] == str(e.get("line"))
         and isinstance(v, dict)), None)
    if isinstance(ln, dict):
        return _fnum(ln.get("T_cd_bay_seed_kip")) or _fnum(ln.get("T_cd_seed_kip"))
    return None


def _cfs_stud_schedule(pkg):
    studs = [s for s in (pkg.get("studs") or []) if isinstance(s, dict)]
    if not studs:
        return ""
    refs = _Refs()
    rows = []
    any_seed = False
    any_chord = any("chord" in str(s.get("id", "")).lower() for s in studs)
    for st in studs:
        dstack = st.get("P_cum_kip_by_story_design")
        stack = dstack if isinstance(dstack, dict) and dstack else (st.get("P_cum_kip_by_story") or {})
        designed = st.get("DC") is not None or bool(st.get("checks")) or bool(st.get("rows"))
        any_seed = any_seed or not designed
        sec = next((st.get(k) for k in ("section", "schedule", "designator", "selection")
                    if st.get(k)), None) or (st.get("inputs") or {}).get("section")
        brace = st.get("bracing_assumption") or st.get("bracing")
        if st.get("waived"):
            rows.append([_pt(st.get("id")), _waived_cell(st), "", "", "", "", "", "",
                         refs.ref(st.get("cited"))])
            continue
        try:
            _stk = ", ".join("L%s: %.2f" % (k, float(stack[k]))
                             for k in sorted(stack, key=lambda x: int(x), reverse=True))
        except Exception:
            _stk = _cn(stack)
        chord = "chord" in str(st.get("id", "")).lower()
        dem, _k = _entry_design_demand(st, ("P_design_kip", "Pu_kip", "C_design_kip",
                                            "T_design_kip", "demand_kip"), derive=False)
        tcd = _line_tcd(pkg, st) if chord else None
        cap = st.get("capacity")
        rows.append([_pt(st.get("id")),
                     _pt(sec) or ("<i>not designed</i>" if not designed else "&mdash;"),
                     _pt(brace) or "&mdash;",
                     (_stk + (" <i>(design)</i>" if stack is dstack else "")) if stack else "&mdash;",
                     _num_or(dem), _num_or(tcd) if chord else "&mdash;",
                     _num_or(cap) if _fnum(cap) is not None else (_pt(cap) or "&mdash;"),
                     _cdc_e(st), refs.ref(st.get("cited"))])
    head = ["ID", "Section / schedule", "Bracing assumption", "P<sub>cum</sub> by story (kip)",
            "Design demand (kip)", "Capacity-design T<sub>cd</sub> (kip)", "Design strength",
            "D/C", "Ref."]
    if not any_chord:
        head.pop(5)
        rows = [r[:5] + r[6:] for r in rows]
    intro = ("<p class='tnote'>P<sub>cum</sub> is the typical bearing-stud axial stack "
             "(1.2D+1.6L, one tributary width, roof live load 0, no wall self-weight) from the "
             "analysis; the designed stud schedule (sections by story group, bracing assumption, "
             "E2/E3/E4, G5 at the track, H1) is shown where filled.</p>" if any_seed else
             "<p>Stud sections step down with height and are never lighter below; the bracing "
             "assumption is declared per row; web crippling (G5) is checked at the track; chord "
             "studs are designed for the capacity-design tension T<sub>cd</sub> of their line "
             "(compression adds gravity).</p>")
    return "<h3>Stud and chord-stud schedule</h3>" + intro + _ref_table(head, rows, refs)


def _collector_framework_force(c):
    dk = c.get("demand_kip_by_level")
    if isinstance(dk, dict) and dk:
        vals = [_fnum(v) for v in dk.values() if _fnum(v) is not None]
        return max(vals) if vals else None
    best = None
    for _d, bd in ((c.get("by_direction") or {}) if isinstance(c.get("by_direction"), dict) else {}).items():
        for _lv, v in ((bd or {}).get("levels") or {}).items():
            x = _fnum((v or {}).get("design_level_kip"))
            if x is not None:
                best = x if best is None else max(best, x)
    return best


def _cfs_collector_schedule(pkg):
    colls = [c for c in (pkg.get("collectors") or []) if isinstance(c, dict)]
    if not colls:
        return ""
    refs = _Refs()
    rows = []
    for c in colls:
        fw = _collector_framework_force(c)
        if c.get("waived"):
            rows.append([_pt(c.get("id")), _pt(c.get("line")), _num_or(fw), "", _waived_cell(c),
                         "", "", "", refs.ref(c.get("cited"))])
            continue
        dem, _k = _entry_design_demand(c, ("F_design_kip", "T_design_kip", "P_design_kip",
                                           "demand_kip", "Emh_kip"))
        basis = next((c.get(k) for k in ("design_basis", "demand_basis_used",
                                         "capacity_design_basis") if c.get(k)), None)
        if basis is None and not c.get("framework_slot"):   # the framework's basis is not a design basis
            b = str(c.get("basis") or "")
            basis = None if (not b or b.strip().upper().startswith("SEEDED")) else b
        sel = c.get("selection") if not str(c.get("selection") or "").upper().startswith("SEEDED") \
            else None
        cap = c.get("capacity")
        rows.append([_pt(c.get("id")), _pt(c.get("line")), _num_or(fw), _num_or(dem),
                     _pt(basis) or "&mdash;", _pt(sel) or ("&mdash;" if c.get("DC") is not None
                                                          else "<b>PENDING</b>"),
                     _num_or(cap) if _fnum(cap) is not None else (_pt(cap) or "&mdash;"),
                     _cdc_e(c), refs.ref(c.get("cited"))])
    return ("<h3>Collector schedule</h3><p>Analysis force = the diaphragm design force carried "
            "along the collector run (ASCE 7-22 &sect;12.10.1.1, with &Omega;<sub>0</sub> per "
            "&sect;12.10.2.1 in SDC C&ndash;F); the <i>design force</i> is the force the D/C is "
            "computed on and its basis is stated per row.</p>"
            + _ref_table(["ID", "Line", "Analysis force (kip)", "Design force (kip)",
                          "Design basis", "Selection", "Design strength", "D/C", "Ref."],
                         rows, refs))


def _cfs_seed_blocks(pkg):
    """Framework demand blocks the designer works from: gravity framing, collector design levels
    per direction and SDC, Type II Ca / uniform uplift, strap ductility, discontinuity
    transfers and P-delta warnings. The designed values live in the schedules."""
    h = []
    gf = pkg.get("gravity_framing") or []
    if gf:
        rows = [[_pt(g.get("id", "")), _pt(g.get("member", "")), _num_or(g.get("span_ft"), "%.1f"),
                 _num_or(g.get("wu_klf"), "%.3f"), _num_or(g.get("Pu_kip"), "%.1f"),
                 _num_or(g.get("Mu_kipft"), "%.2f"), _num_or(g.get("Vu_kip"), "%.2f"),
                 _num_or(g.get("I_req_in4"), "%.2f"),
                 _pt(g.get("section") or g.get("selection") or "") or "<b>PENDING</b>",
                 _cdc_e(g) if g.get("DC") is not None else
                 (_waived_cell(g) if g.get("waived") else "<b>PENDING</b>")] for g in gf if isinstance(g, dict)]
        h.append("<h3>Gravity framing (platform / floor members)</h3>"
                 + "<p class='tnote'>%s</p>" % _pt((gf[0] or {}).get("basis", ""))
                 + _table(["ID", "Member", "Span (ft)", "w<sub>u</sub> (klf)", "P<sub>u</sub> (kip)",
                           "M<sub>u</sub> (kip-ft)", "V<sub>u</sub> (kip)", "I<sub>req</sub> (in<sup>4</sup>)",
                           "Section", "D/C"], rows))
    crow = []
    for c in pkg.get("collectors") or []:
        for d, bd in ((c or {}).get("by_direction") or {}).items():
            for lv, v in sorted((bd.get("levels") or {}).items(), key=lambda kv: int(kv[0])):
                crow.append([_pt(c.get("id", "")), _pt(d), _pt(bd.get("SDC", "")), _pt(lv),
                             _num_or(v.get("Fx_kip"), "%.1f"), _num_or(v.get("Fpx_kip"), "%.1f"),
                             _num_or(v.get("design_level_kip"), "%.1f"), _pt(v.get("basis", ""))])
    if crow:
        h.append("<h3>Collector design levels (ASCE 7-22 &sect;12.10.1.1 / &sect;12.10.2.1; S400 B3.4)</h3>"
                 "<p class='tnote'>Whole-diaphragm level forces; the collector force is its tributary "
                 "share plus transfers.</p>"
                 + _table(["Collector", "Dir", "SDC", "Level", "F<sub>x</sub>", "F<sub>px</sub>",
                           "Design level (kip)", "Basis"], crow))
    trow = []
    for hd in pkg.get("holddowns") or []:
        t = (hd or {}).get("type_ii")
        if isinstance(t, dict):
            for k in sorted((t.get("Ca_by_story") or {}), key=int):
                trow.append([_pt(hd.get("id", "")), _pt(k), _num_or(t["Ca_by_story"][k], "%.3f"),
                             _num_or((t.get("uplift_t_plf_by_story_strength") or {}).get(k), "%.0f"),
                             _num_or((t.get("uplift_t_plf_by_story_capacity") or {}).get(k), "%.0f")])
    if trow:
        h.append("<h3>Type II shear walls (S400 E1.4.2.2): C<sub>a</sub> and uniform uplift t</h3>"
                 + _table(["Hold-down", "Story", "C<sub>a</sub>", "t, strength level (plf)",
                           "t, capacity-design level (plf)"], trow))
    sd = ((pkg.get("capacity_design") or {}).get("strap_ductility"))
    if isinstance(sd, dict):
        verdict = {True: "PASS", False: "<b>FAIL</b>"}.get(sd.get("ok"), "<b>not evaluated</b>")
        h.append("<h3>Strap ductility (S400 E3.4.1(a))</h3><p>%s &mdash; %s</p>"
                 % (verdict, _pt(sd.get("message", ""))))
    tr = pkg.get("discontinuity_transfers") or []
    if tr:
        rows = [[_pt(t.get("id", "")), _pt(t.get("story", "")), _pt(t.get("line", "")),
                 _num_or(t.get("V_kip"), "%.2f"), _pt(t.get("to", "")),
                 _cdc(t.get("DC")) if t.get("DC") is not None else
                 (_waived_cell(t) if t.get("waived") else "<b>PENDING</b>")] for t in tr if isinstance(t, dict)]
        h.append("<h3>Discontinuous wall lines &mdash; transfers (ASCE 7-22 &sect;12.3.3.4, &Omega;<sub>0</sub>)</h3>"
                 + _table(["ID", "Story", "Line", "V (kip)", "To", "D/C"], rows))
    sw = pkg.get("stability_warnings") or []
    if sw:
        h.append("<h4>P-&Delta; amplification (&sect;12.8.7, 0.10 &lt; &theta; &le; &theta;<sub>max</sub>)</h4><ul>%s</ul>"
                 % "".join("<li>%s</li>" % _pt(w) for w in sw))
    return "".join(h)


def _cfs_schedules_section(pkg, cfg=None, res=None):
    """The wall-path design schedules (shear walls or strap bays / hold-downs and rods / studs and
    chords / collectors / framework demand blocks / story drift), each printing the demand the
    D/C uses beside the ELF value. Returns '' when the package has none (frame-path buildings)."""
    if not isinstance(pkg, dict):
        return ""
    h = [_cfs_wall_schedule(pkg, cfg), _cfs_holddown_schedule(pkg, cfg), _cfs_anchorage_schedule(pkg, cfg),
         _cfs_stud_schedule(pkg),
         _cfs_collector_schedule(pkg), _cfs_seed_blocks(pkg), _cfs_drift_section(pkg, cfg)]
    if not _CFS_PUB[0]:                 # draft: the framework flags stay inline
        for key, label in (("model_vs_tributary_flags", "Model-vs-tributary gate"),
                           ("drift_flags", "Drift flags (engine screen)"),
                           ("stability_flags", "P-delta stability (12.8.7)")):
            flags = pkg.get(key) or []
            if flags:
                res_ = pkg.get(key + "_resolution")
                h.append("<h4>%s</h4><ul>%s</ul>" % (label, "".join("<li>%s</li>" % _pt(f) for f in flags))
                         + ("<p><b>Resolution:</b> %s</p>" % _pt(res_) if res_ else
                            "<p class='note'><b>UNRESOLVED</b></p>"))
    return "".join(x for x in h if x)


def _connection_demands(cfg, pkg, reX):
    rows = []; ms = (pkg or {}).get("members") or []
    mf = not cfg.get("braces")
    for m in ms:
        inp = m.get("inputs", {}) or {}; kind = inp.get("kind", ""); sec = inp.get("section", "")
        V = inp.get("V_kip") or 0.0; Mz = inp.get("Mz_kipin") or 0.0
        P = max(inp.get("P_comp_kip", 0) or 0, inp.get("P_tens_kip", 0) or 0)
        if kind == "beam":
            typ = "beam-to-column (moment)" if mf else "beam-to-column (shear)"
            dem = f"V = {V:.0f} kip" + (f", M = {Mz/12:.0f} k-ft" if mf and Mz else "")
            basis = "CJP flange welds + web bolts (J2/J3)" if mf else "bolted shear tab (J3 / J4)"
        elif "col" in kind:
            typ = "column splice / base"; dem = f"P = {P:.0f} kip" + (f", M = {Mz/12:.0f} k-ft" if Mz else "")
            basis = "splice (J1.4) / base plate J8 + ACI 318 Ch.17"
        elif kind == "brace":
            typ = "brace-to-gusset"; dem = f"axial = {P:.0f} kip"; basis = "expected strength (S400 chain / strap RyFyAg)"
        else:
            continue
        rows.append([f"{sec} {kind}", typ, dem, basis])
    if reX is not None:
        pmax = max((r[2][2] for r in reX), default=0.0); vmax = max((abs(r[2][0]) for r in reX), default=0.0)
        rows.append(["column base", "base plate / anchor rods", f"P = {pmax:.0f} kip, V = {vmax:.0f} kip",
                     "J8/J9 + ACI 318 Ch.17 (min 4 rods)"])
    if not rows:
        return None
    return _table(["Member / location", "Connection type", "Governing demand", "Design basis (limit states)"], rows)

def _qa_scorecard(cfg, Fx, reX, eX, eY, drX, drY):
    s = cfg["seis"]; rows = []
    if reX is not None and Fx is not None:
        Rx = sum(r[2][0] for r in reX); base = sum(Fx.values()); Rz = sum(r[2][2] for r in reX)
        rows.append(["Equilibrium &mdash; |&Sigma;R<sub>x</sub>| = applied base shear", f"{abs(Rx):.0f} vs {base:.0f} kip",
                     "PASS" if base and abs(abs(Rx)-base)/base < 0.01 else "REVIEW"])
    if eX is not None and eY is not None:
        cx = sum(eX)*100; cy = sum(eY)*100
        rows.append(["Modal mass &ge; 90% (X / Y)", f"{cx:.0f}% / {cy:.0f}%", "PASS" if min(cx, cy) >= 90 else "REVIEW"])
    _done = False
    if drX is not None and Fx is not None and hasattr(E, "seismic_drift") and hasattr(E, "stability_theta"):
        try:   # hot-rolled route: the gate's drift (12.8.6.5 location, per-direction Cd, RBS) and theta
            sdr = E.seismic_drift(cfg)
            for d in ("X", "Y"):
                dm = max(sdr[d]["design"]); lim = sdr[d]["limit"]
                rows.append(["Seismic design drift %s &le; limit (%s)" % (d, _e(sdr[d].get("location", ""))),
                             f"{dm*100:.2f}% &le; {lim*100:.2f}%", "PASS" if dm <= lim + 1e-9 else "FAIL"])
            st = E.stability_theta(cfg)
            rows.append(["Stability &theta; &le; &theta;<sub>max</sub>", f"{st['max']:.3f} &le; {st['theta_max']:.3f}",
                         "PASS" if st["ok"] else "FAIL"])
            _done = True
        except Exception:
            _done = False
    if drX is not None and Fx is not None and not _done:
        NF = len(cfg["heights"]); SDS = s["SDS"]; Cd = s.get("Cd", 5.5); Ie = s["Ie"]; lim = cfg.get("drift_limit", 0.02)
        dmax = max(max(drX[k]*Cd/Ie, drY[k]*Cd/Ie) for k in range(NF))
        rows.append(["Seismic design drift &le; limit", f"{dmax*100:.2f}% &le; {lim*100:.1f}%", "PASS" if dmax <= lim else "FAIL"])
        A = (cfg["NX"]*cfg["SX"])*(cfg["NY"]*cfg["SY"])/144.0
        Pu = {k: (1.2+0.2*SDS)*E.floor_w(cfg, k) + 0.5*(cfg.get("L_floor", 0)*A/1000.0 if k < NF else 0) for k in range(1, NF+1)}
        Ps = {sx: sum(Pu[k] for k in range(sx, NF+1)) for sx in range(1, NF+1)}
        Vs = {sx: sum(Fx[k] for k in range(sx, NF+1)) for sx in range(1, NF+1)}
        tmax = min(0.5/Cd, 0.25); tw = max((Ps[sx]*drX[sx-1]/Vs[sx] if Vs[sx] else 0) for sx in range(1, NF+1))
        rows.append(["Stability &theta; &le; &theta;<sub>max</sub>", f"{tw:.3f} &le; {tmax:.3f}", "PASS" if tw <= tmax else "FAIL"])
    if not rows:
        return ""
    return ("<h3>QA scorecard (automated checks)</h3>"
            + _table(["Check", "Result", "Status"], rows))


def _save_case_fig(uri, figdir, label):
    """Write a per-combination N/V/M figure to figs/case_<label>.png for the user to inspect later;
    it is NOT embedded in the report (keeps report.html compact)."""
    if not (figdir and uri and isinstance(uri, str)):
        return
    safe = "".join(c if c.isalnum() else "_" for c in str(label))[:60].strip("_") or "case"
    try:
        if uri.startswith("data:image/"):
            head, b64 = uri.split(",", 1)
            ext = head.split("/")[1].split(";")[0].replace("jpeg", "jpg")
            with open(os.path.join(figdir, "case_%s.%s" % (safe, ext)), "wb") as f:
                f.write(base64.b64decode(b64))
        else:
            src = os.path.join(figdir, os.path.basename(uri))
            if os.path.exists(src):
                import shutil; shutil.copy2(src, os.path.join(figdir, "case_%s.png" % safe))
    except Exception:
        pass

def _static_case_table(cfg, combo, nseg=2, render_fig=True):
    """Per member TYPE governing forces for ONE combination, from the STATIC model (distributed
    gravity) so beams carry their true moment (fixes the lumped-model 1.4D = zero-beam-moment bug).
    Custom-geometry buildings (custom_build) fall back to the dynamic model's demand envelope, which
    already adds the beam gravity span moment and supports arbitrary geometry."""
    label, fD, fL, fLr, lat, co = combo
    nseg = 6 if render_fig else nseg   # App-B per-combo FIGURE -> finer (nseg 6); scalar summary table -> fast (nseg 2)
    import static_model as SM
    mm, _, _ = SM.run_combo(cfg, fD, fL, fLr, lat, nseg=nseg)
    best = {}; maxN = (0.0, "", ""); maxM = (0.0, "", "")
    def upd(kind, sec, N, Mz, My, V, n1, n2, score):
        key = (kind, sec)
        if key not in best or score > best[key][0]:
            best[key] = (score, N, Mz, My, V, n1, n2)
    for b in mm["beams"]:
        sec = b.get("sec") or cfg["beam"]; N = Mz = My = V = 0.0
        for tag in b["segs"]:
            lf = ops.eleResponse(tag, "localForces")
            N = max(N, abs(lf[0]), abs(lf[6])); Mz = max(Mz, abs(lf[4]), abs(lf[10]))
            My = max(My, abs(lf[5]), abs(lf[11])); V = max(V, abs(lf[2]), abs(lf[8]))
        upd("beam", sec, N, Mz, My, V, b["A"], b["B"], Mz)
        if N > abs(maxN[0]): maxN = (N, "beam", sec)
        if Mz > abs(maxM[0]): maxM = (Mz, "beam", sec)
    for c in mm["cols"]:
        sec = c.get("sec") or cfg["col"]; lf = ops.eleResponse(c["tag"], "localForces")
        N = max(abs(lf[0]), abs(lf[6])); Mz = max(abs(lf[5]), abs(lf[11])); My = max(abs(lf[4]), abs(lf[10]))
        V = max(abs(lf[1]), abs(lf[2]), abs(lf[7]), abs(lf[8]))
        upd("col", sec, N, Mz, My, V, c["n1"], c["n2"], max(Mz, N))
        if N > abs(maxN[0]): maxN = (N, "col", sec)
        if max(Mz, My) > abs(maxM[0]): maxM = (max(Mz, My), "col", sec)
    for br in mm.get("braces", []):
        sec = br.get("sec") or cfg.get("brace", ""); N = ops.basicForce(br["tag"])[0]
        upd("brace", sec, N, 0.0, 0.0, 0.0, None, None, abs(N))
        if abs(N) > abs(maxN[0]): maxN = (N, "brace", sec)
    rows = []
    for (kind, sec), (sc, N, Mz, My, V, n1, n2) in sorted(best.items()):
        rows.append([kind, sec, f"{N:.1f}", f"{Mz/12:.1f}", f"{My/12:.1f}", f"{V:.1f}",
                     _loc(n1, n2) if n1 else "&mdash;"])
    d = _lat_dir(lat) or "X"
    uri = None
    if render_fig:                       # per-combo Appendix-B N/V/M figure (opt-in; the governing
        try:                             # diagrams in Chapter 5 already cover the governing cases)
            import frame_diagram as FD
            uri, _pk = FD.render_solved(cfg, mm, d, label, "perimeter")
        except Exception:
            uri = None
    return rows, maxN, maxM, uri, d

def _jmark(ax, x, z, pinned):
    if pinned: ax.plot([x], [z], marker="o", mfc="white", mec="#c0392b", ms=8, mew=1.6, zorder=5)
    else:      ax.plot([x], [z], marker="s", mfc="#1f3b73", mec="#1f3b73", ms=7, zorder=5)

def _joint_dist_fig(cfg, direction, model=None):
    """2D elevations of the perimeter and an internal frame line, every beam-end and column base
    marked rigid (filled) or pinned/shear-released (open). Parametric buildings read cfg['releases']
    and cfg['base']; custom_build buildings pass the staticized `model` (per-beam relz/rely + per-
    column bases). A beam end is 'pinned' when its STRONG-axis (vertical/gravity) moment is released
    (rely), per the corrected release convention."""
    custom = model is not None
    if cfg.get("custom_build") and not custom:
        return None
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    import matplotlib.lines as mlines
    NX, NY, SX, SY = cfg["NX"], cfg["NY"], cfg["SX"], cfg["SY"]; NF = len(cfg["heights"]); z = E.zlevels(cfg)
    if custom:
        rel_lu = {(b["i"], b["j"], b["k"], b["dir"]): (b.get("relz", "none"), b.get("rely", "none")) for b in model["beams"]}
        base_lu = model.get("bases", {})
    else:
        relf = cfg.get("releases"); base0 = cfg.get("base", "fixed")
    # ABSENT members must draw NOTHING (never a fixed/rigid default): on notched / offset-core /
    # split-level plans the elevation lines cross bays and bases that do not exist in the model --
    # the old .get(..., "fixed"/"none") defaults painted phantom FIXED bases and RIGID joints there.
    if custom:
        present = {k: set(map(tuple, v)) for k, v in (model.get("present") or {}).items()}
    else:
        present = {k: E.grid(cfg, k) for k in range(NF+1)}
    def _pt(i, fx):
        return (i, fx) if direction == "X" else (fx, i)
    def _exists(i, fx, k):
        return _pt(i, fx) in (present.get(k) or set())
    if direction == "X":
        ncol, span, coords = NX, SX, [i*SX for i in range(NX+1)]
        lines = [("Perimeter frame (j=0)", 0), (f"Internal frame (j={NY//2})", NY//2)]
        def beam_rel(i, k, fx):
            return rel_lu.get((i, fx, k, "X")) if custom else ((relf(i, fx, k, "X") if relf else ("none", "none"))
                                                               if _exists(i, fx, k) and _exists(i+1, fx, k) else None)
        def base_at(i, fx):
            return base_lu.get((i, fx)) if custom else (base0 if _exists(i, fx, 0) else None)
    else:
        ncol, span, coords = NY, SY, [j*SY for j in range(NY+1)]
        lines = [("Perimeter frame (i=0)", 0), (f"Internal frame (i={NX//2})", NX//2)]
        def beam_rel(i, k, fx):
            return rel_lu.get((fx, i, k, "Y")) if custom else ((relf(fx, i, k, "Y") if relf else ("none", "none"))
                                                               if _exists(i, fx, k) and _exists(i+1, fx, k) else None)
        def base_at(i, fx):
            return base_lu.get((fx, i)) if custom else (base0 if _exists(i, fx, 0) else None)
    fig, axes = plt.subplots(1, 2, figsize=(16, 6), sharey=True)
    for ax, (ttl, fx) in zip(axes, lines):
        for i in range(ncol+1):
            zs = [k for k in range(NF+1) if _exists(i, fx, k)]
            if len(zs) >= 2:                                   # column line only over the levels that exist
                ax.plot([coords[i]]*2, [z[min(zs)], z[max(zs)]], color="#999", lw=2, zorder=1)
            bf = base_at(i, fx)
            if bf is not None:
                ax.plot([coords[i]], [z[0]], marker="^", ms=11, zorder=5,
                        mfc=("white" if bf == "pinned" else "#1f3b73"),
                        mec=("#c0392b" if bf == "pinned" else "#1f3b73"), mew=1.6)
        for k in range(1, NF+1):
            for i in range(ncol):
                rel = beam_rel(i, k, fx)
                if rel is None:
                    continue                                   # bay not present on this line/level: draw nothing
                x1, x2 = coords[i], coords[i+1]; zk = z[k]
                relz, rely = rel
                ax.plot([x1, x2], [zk, zk], color="#999", lw=2, zorder=1)
                _jmark(ax, x1 + 0.12*span, zk, relz in ("I", "both"))
                _jmark(ax, x2 - 0.12*span, zk, relz in ("J", "both"))
        ax.set_title(ttl, fontsize=11); ax.set_xlabel("plan (in)"); ax.grid(alpha=0.2)
    axes[0].set_ylabel("Z (in)")
    leg = [mlines.Line2D([], [], marker="s", color="none", mfc="#1f3b73", mec="#1f3b73", ms=9, label="rigid / continuous"),
           mlines.Line2D([], [], marker="o", color="none", mfc="white", mec="#c0392b", ms=9, mew=1.6, label="pinned / shear release"),
           mlines.Line2D([], [], marker="^", color="none", mfc="#1f3b73", mec="#1f3b73", ms=10, label="base fixed"),
           mlines.Line2D([], [], marker="^", color="none", mfc="white", mec="#c0392b", ms=10, mew=1.6, label="base pinned")]
    fig.suptitle(f"Joint fixity distribution - {direction}-direction frames", y=1.0, fontsize=12)
    fig.legend(handles=leg, loc="lower center", ncol=4, fontsize=9, frameon=False, bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout(rect=[0, 0.06, 1, 0.96])
    return _b64(fig)

def _beam_deflections(cfg, fD, fL, nseg=6):
    """Chord-relative mid-span vertical deflection of every beam under a service gravity case, from
    the static model (true distributed tributary load). Returns {(i,j,k,dir): (|defl_in|, L_in)}."""
    import static_model as SM
    mm, _, _ = SM.run_combo(cfg, fD, fL, 0.0, {}, nseg=nseg)
    out = {}
    for b in mm["beams"]:
        ch = b["nodes"]; zA = ops.nodeDisp(ch[0], 3); zB = ops.nodeDisp(ch[-1], 3); n = len(ch)-1
        dmax = 0.0
        for idx, nd in enumerate(ch):
            d = ops.nodeDisp(nd, 3) - (zA + (zB-zA)*idx/n)
            if abs(d) > abs(dmax): dmax = d
        out[(b["i"], b["j"], b["k"], b["dir"])] = (abs(dmax), b["L"])
    return out

def _deflection_section(cfg, nseg=6):
    NF = len(cfg["heights"])
    try:
        dD = _beam_deflections(cfg, 1.0, 0.0, nseg); dL = _beam_deflections(cfg, 0.0, 1.0, nseg)
    except Exception as ex:
        return f"<p class='note'>[deflection run failed: {ex}]</p>"
    def worst(roof):
        best = None
        for key, (dl, L) in dL.items():
            if (key[2] == NF) != roof: continue
            ratio = (L/dl) if dl > 1e-6 else 1e12
            if best is None or ratio < best[1]: best = (key, ratio, dl, L)
        return best
    def cell(d, L, lim):
        if d < 1e-3: return ("&lt; 0.01 in", "OK")
        return (f"{d:.2f} in (L/{L/d:.0f})", "OK" if d <= L/lim else "NG")
    rows = []
    for roof, lbl in ((False, "Floor beam (governing)"), (True, "Roof beam (governing)")):
        w = worst(roof)
        if not w: continue
        key, ratio, dl, L = w; dd = dD.get(key, (0.0, L))[0]; dtl = dd + dl
        ltxt, lok = cell(dl, L, 360); ttxt, tok = cell(dtl, L, 240)
        camber = round(0.8*dd*4)/4 if dd >= 0.75 else 0.0
        rows.append([lbl, f"{L/12:.0f} ft", ltxt, lok, ttxt, tok,
                     (f"{camber:.2f} in" if camber > 0 else "none")])
    if not rows:
        return ""
    return ("<h3>Beam deflection &amp; camber</h3>"
            "<p>Service deflection of the governing floor and roof beam from the static model "
            "(chord-relative, true two-way tributary gravity): live load against L/360 and total D+L "
            "against L/240. Suggested camber &asymp; 80% of the dead-load deflection (nearest 1/4 in) "
            "where it exceeds 3/4 in.</p>"
            + _table(["Member", "Span", "Live &delta; (vs L/360)", "&le;", "Total &delta; (vs L/240)", "&le;", "Camber"], rows))


_WALL_SYSTEMS = ("wsp_shearwall", "steelsheet_wall", "gypsum_wall", "strap_braced", "sbmf")


def _grounding_check(cfg, name, pkg):
    """Verify the design actually queried the RAG collections its systems require (reliability),
    per the rubric's required code set: S100 + S240 on every light-frame brief, plus S400 where an
    S400 seismic system is used (S400 A1.2.3: R = 3 in SDC B/C needs only S100/S240). S400 is the
    SEISMIC standard -- wind-designed shear walls take S240 B5.2.2.3 (phi_v 0.65, B5.2.3). AISC or
    retired-standard citations are red-flagged."""
    import re as _re
    recs, _ = _load_activity(name)
    col = {}
    for r in recs:
        if r.get("tool") == "search_engineering_standards":
            mm = _re.search(r"\[([A-Za-z0-9_]+)\]", r.get("detail", ""))
            if mm: col[mm.group(1)] = col.get(mm.group(1), 0) + 1
    def n(c): return col.get(c, 0)
    # ALSO credit grounding from the CITED CLAUSES in calc_package.json (not the activity log alone), so a
    # filled, RAG-grounded package is recognised even if the auto-logger did not record the queries (P5).
    def _cited(obj):
        out = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                out += ([str(v)] if k in ("cited", "cite") else _cited(v))
        elif isinstance(obj, list):
            for v in obj: out += _cited(v)
        return out
    _ct = " ".join(_cited(pkg or {}))
    cite_s100 = bool(_re.search(r"s\s*100", _ct, _re.I))
    cite_s240 = bool(_re.search(r"s\s*240", _ct, _re.I))
    cite_s400 = bool(_re.search(r"s\s*400", _ct, _re.I))
    sysname = (cfg.get("system") or cfg.get("seis", {}).get("system") or "").lower()
    kind = (cfg.get("structure_kind") or "").lower()
    is_wall = sysname in _WALL_SYSTEMS or kind in ("wall", "podium") or bool((pkg or {}).get("wall_lines"))
    R = cfg.get("seis", {}).get("R", 3)
    try:
        _s = cfg.get("seis", {})
        _sdc_g = _sdc(_s.get("SDS", 0), _s.get("SD1", 0), _s.get("S1", 0), cfg.get("risk_cat", "II"))
    except Exception:
        _sdc_g = "D"
    # S400 A1.2.3: R = 3 in SDC B/C -> S100/S240 only; otherwise an S400 system needs S400
    needs_s400 = (sysname in _WALL_SYSTEMS or sysname == "sbmf") and \
        not (float(R or 3) <= 3.0 + 1e-9 and _sdc_g in ("A", "B", "C"))
    has_conn = bool((pkg or {}).get("connections"))
    wall_sched = any(w.get("fastener_schedule") for w in ((pkg or {}).get("wall_lines") or [])
                     if isinstance(w, dict))
    has_cap = bool((pkg or {}).get("capacity_design"))
    rows = []
    def row(item, required, ok, ev, na=""):
        rows.append([item, "required" if required else (na or "n/a"),
                     ev, ("&mdash;" if not required else ("grounded" if ok else "<b>MISSING</b>"))])
    row("AISI S100 &mdash; member/connection limit states (EWM)", True,
        n("engineering_standards_S100") > 0 or cite_s100,
        f"{n('engineering_standards_S100')} queries" + (" + cited in calc_package" if cite_s100 else ""))
    row("AISI S240 &mdash; framing rules (studs/track/built-up/bracing/trusses)", is_wall,
        n("engineering_standards_S240") > 0 or cite_s240,
        f"{n('engineering_standards_S240')} queries" + (" + cited in calc_package" if cite_s240 else ""),
        na="n/a (no light-frame walls)")
    row("AISI S400 &mdash; seismic wall/strap/SBMF capacities + capacity design",
        needs_s400, n("engineering_standards_S400") > 0 or has_cap or cite_s400,
        f"{n('engineering_standards_S400')} queries" + (" + capacity_design block" if has_cap else "")
        + (" + cited in calc_package" if cite_s400 else ""),
        na="n/a (no S400 seismic system, or R = 3 in SDC B/C per S400 A1.2.3)")
    row("Connections grounded (S100 Ch. J / fastener schedules)", True, has_conn or wall_sched,
        ("connections block present" if has_conn else "")
        + (" / wall fastener schedules filled" if wall_sched else "")
        or "no connections block and no fastener schedules")
    nmiss = sum(1 for r in rows if "MISSING" in r[3])
    # staleness / hot-rolled red flags in the cited text
    flags = []
    if _re.search(r"aisc\s*3(41|60|58)", _ct, _re.I):
        flags.append("cites AISC 341/360/358 for CFS members or systems — hot-rolled reflex (red flag)")
    if _re.search(r"\bs\s*(110|213|214)\b", _ct, _re.I):
        flags.append("cites retired S110/S213/S214 (merged into S400/S240) — staleness tell")
    if _re.search(r"\bs\s*310\b", _ct, _re.I):
        flags.append("cites S310 — NOT in the RAG: verify the value's stated basis (a fabricated S310 "
                     "clause number is a red flag)")
    head = ("<h3>Grounding verification</h3>"
            f"<p>Whether the design queried the RAG collections its systems require. R = {R}; "
            + ("light-frame walls present &mdash; S100 + S240 required; S400 for the seismic "
               "system (wind shear walls: S240 B5)." if is_wall else
               "frame path &mdash; S100 required; S240/S400 only where light-frame walls/straps appear.")
            + "</p>")
    tail = ("<p class='note'>Grounding incomplete: the items marked MISSING were required for this building's "
            "systems but no RAG query / calc-package evidence was found. Re-run those checks against the RAG.</p>"
            if nmiss else "<p>All required RAG grounding is present.</p>")
    if flags:
        tail += ("<p class='note'><b>Citation red flags:</b></p><ul>"
                 + "".join("<li>%s</li>" % f for f in flags) + "</ul>")
    return head + _table(["Grounding requirement", "Applies", "Evidence", "Status"], rows) + tail

def _appendix_case_figs(cases, cfg):
    """Which load-case indices get a per-combo N/V/M figure in Appendix B. Default: a fast
    representative set -- the gravity combos + the primary seismic combo per direction/sign -- so the
    report stays under the MCP client timeout. cfg['appendix_case_figures']=True renders ALL combos
    (slow; run in the background); =False renders none."""
    flag = cfg.get("appendix_case_figures", "subset")  # default: representative subset (gravity + primary seismic)
    if flag is True:
        return set(range(len(cases)))                # ALL combos (slower; rendered inline)
    if not flag:
        return set()                                 # explicit False / None -> none
    picked, seen = [], set()
    for idx, (label, fD, fL, fLr, lat, co) in enumerate(cases):
        if co:                                    # skip Omega0 column-only duplicates
            continue
        lab = str(label)
        if lab.startswith("1.4D") or "1.2D+1.6L" in lab:
            picked.append(idx)
        else:
            for dirn in ("X", "Y"):
                for sgn in ("+", "-"):
                    key = "E" + dirn + sgn
                    if ("rhoE" + dirn + sgn) in lab and "+L" in lab and key not in seen:
                        seen.add(key); picked.append(idx)
        if len(picked) >= 6:
            break
    return set(picked[:6])


def _consistency_section(name, root, pkg):
    """Chapter 13 numerical self-consistency check (engine/consistency.py)."""
    try:
        import consistency
        issues = consistency.check(name, root=root, pkg=pkg, verbose=False)
    except Exception as ex:
        return f"<h3>Numerical self-consistency check</h3><p class='note'>[consistency check unavailable: {ex}]</p>"
    if not issues:
        return ("<h3>Numerical self-consistency check</h3>"
                "<p class='cnote'>&#10003; <b>PASS</b> &mdash; every member/connection carries a limit state, "
                "capacity and D/C; each headline D/C equals its worst limit-state check and demand&divide;capacity; "
                "and no quantity is reported with conflicting values.</p>")
    rows = [[str(i + 1), iss] for i, iss in enumerate(issues)]
    return ("<h3>Numerical self-consistency check</h3>"
            f"<p class='note'><b>{len(issues)} item(s) to reconcile</b> before the report is final &mdash; the same "
            "quantity must carry one value everywhere, every D/C must equal demand&divide;capacity, the headline D/C "
            "must equal its worst limit-state check, and no D/C may exceed 1.0:</p>" + _table(["#", "issue"], rows))


def _design_basis(cfg):
    """Top-of-report echo of the RESOLVED building parameters so a brief-vs-built mismatch (bay count,
    spans, stories, loads) is visible on page 1."""
    NX,NY=cfg["NX"],cfg["NY"]; SX,SY=cfg["SX"],cfg["SY"]; H=cfg["heights"]; s=cfg.get("seis",{})
    rows=[
      ["Lateral system", str(cfg.get("arch",""))],
      ["Plan grid", "%d &times; %d bays @ %.0f &times; %.0f ft  (%.0f &times; %.0f ft overall)"
                    % (NX,NY,SX/12.0,SY/12.0,NX*SX/12.0,NY*SY/12.0)],
      ["Stories", "%d @ %s ft  (H = %.0f ft)" % (len(H), ", ".join("%.0f"%(h/12.0) for h in H), sum(H)/12.0)],
      ["Gravity loads", "floor D %s / L %s psf; roof D %s / L %s psf"
                    % (cfg.get("D_floor","?"),cfg.get("L_floor","?"),cfg.get("D_roof","?"),cfg.get("L_roof","?"))],
      ["Seismic", "R=%s, Cd=%s, &Omega;<sub>0</sub>=%s, Ie=%s, S<sub>DS</sub>=%s, S<sub>1</sub>=%s"
                    % (s.get("R","?"),s.get("Cd","?"),s.get("Om0","?"),s.get("Ie","?"),s.get("SDS","?"),s.get("S1","?"))],
    ]
    return ("<h2>Design basis</h2><p class='note'>Model built to the parameters below &mdash; <b>verify these "
            "against your brief</b>, especially the bay count and spans.</p>" + _table(["Parameter","Value"], rows))


def _is_cfs_job(name, root):
    """CFS-13: a CFS job (cfg.py with lines_x/lines_y/span_ft, or a calc_package_cfs.json and no
    registered hot-rolled cfg) renders through the CFS report, never the grid report."""
    try:
        cfg = _load_job_cfg(root, name)
    except Exception:
        cfg = None
    if isinstance(cfg, dict):
        return "lines_x" in cfg or "lines_y" in cfg or "span_ft" in cfg or bool(cfg.get("spans"))
    if E is not None and name in E.CFG:
        return False
    return os.path.exists(os.path.join(root, "design", "calc_package_cfs.json"))


def build_report(name, root=None):
    """Re-render report.html from the job folder (the agent's FILLED package is preserved).
    CFS jobs dispatch to build_report_cfs_from_disk; hot-rolled grid jobs render below."""
    if root is None:
        root = os.path.join(os.environ.get("STEEL_BUILDER_JOBS") or HERE, name)
    if _is_cfs_job(name, root):
        return build_report_cfs_from_disk(name, root)
    if E is None:
        raise SystemExit("engine3d/openseespy unavailable -- the hot-rolled grid report needs it")
    _register(name); cfg = E.CFG[name]
    NF = len(cfg["heights"]); s = cfg["seis"]
    SX, SY = cfg["SX"], cfg["SY"]; NX, NY = cfg["NX"], cfg["NY"]
    Lx = (cfg["xcoords"][-1] if cfg.get("xcoords") else NX*SX); Ly = (cfg["ycoords"][-1] if cfg.get("ycoords") else NY*SY)
    Htot = E.zlevels(cfg)[-1]
    pkg, pkgsrc = _load_pkg(name, root)
    mat = "ASTM A1003/A653 SS: \\(F_y=33\\) or \\(50\\) ksi by mil, \\(E=29{,}500\\) ksi (AISI)."
    parts = [f"<h1>{name} &mdash; structural analysis &amp; design report</h1>",
             f"<p><b>{cfg.get('arch','')}</b> &middot; generated {datetime.date.today()}</p>",
             _toc(), _design_basis(cfg)]
    figdir = os.path.join(root, "figs"); os.makedirs(figdir, exist_ok=True)
    global _FIGDIR; _FIGDIR = figdir; _FIGSEQ[0] = 0
    try:
        import plot_model as PM
        PM.figures(name, figdir, deformed_fig=bool(cfg.get("deformed_shape_figure")))
        # Figure 2 (member orientation) is REQUIRED -- retry once on its own if missing
        if not os.path.exists(os.path.join(figdir, f"{name}_orientation.png")):
            try: PM.orientation(name, figdir)
            except Exception as _oex:
                parts.append(f"<p class='note'>[REQUIRED orientation figure (Fig 2) failed twice: {_oex} "
                             "&mdash; fix the model and re-render]</p>")
    except Exception as ex:
        parts.append(f"<p class='note'>[model figures failed: {ex}]</p>")

    # ---- shared computations (used across several chapters) ----
    Fx = None; T = eX = eY = None; Cs = V = Tu = Ta = W = None
    try: T, eX, eY, Cs, V, Tu, Ta, Fx, W = _seismic(cfg)
    except Exception as ex: parts.append(f"<p class='note'>[seismic analysis failed: {ex}]</p>")
    windhtml, VwX, VwY = _wind_section(cfg)
    print("[%s] report: model + ASCE 7 loads + modal done; rendering chapters%s ..."
          % (name, " + static-model force diagrams (~30-60 s)" if cfg.get("force_diagrams") else ""))
    drX = drY = None; reX = reY = None
    if Fx is not None:
        try:
            _, _dX, drX, reX = _run_case(cfg, "X", Fx); _, _dY, drY, reY = _run_case(cfg, "Y", Fx)
        except Exception as ex: parts.append(f"<p class='note'>[drift/equilibrium run failed: {ex}]</p>")

    # ============================== Chapter 1 — Design basis & codes ==============================
    parts.append(_chapter(1))
    parts.append(_design_basis_codes(cfg, s))
    parts.append("<h3>Building description</h3>")
    parts.append(_table(["Item", "Value"], [
        ["Archetype", cfg.get("arch", "")],
        ["Stories", f"{NF}"],
        ["Plan", f"{Lx/12:.0f} ft (X) &times; {Ly/12:.0f} ft (Y)"],
        ["Bays", f"{NX} &times; {NY} @ {SX/12:.0f} ft (X) / {SY/12:.0f} ft (Y)"],
        ["Height", f"{Htot/12:.0f} ft (h&#8345;)"],
        ["Column bases", cfg.get("base", "fixed")],
        ["Lateral system", "moment frame" if not cfg.get("braces") else "braced / dual / mixed"],
    ]))
    parts.append("<h3>Materials</h3><p>" + mat + "</p>")
    parts.append("<h3>Geometry</h3>")
    try: parts.append(_img(fig_plan(cfg), "Plan grid &mdash; the (i, j) labels used in the reaction/force tables"))
    except Exception as ex: parts.append(f"<p class='note'>[plan figure failed: {ex}]</p>")
    parts.append(_img(_png_file_b64(os.path.join(figdir, f"{name}_orientation.png")),
                      "Section orientation (web/depth ticks &mdash; beam strong axis must be vertical)"))

    # ====================== Chapter 2 — Structural system & load path =======================
    parts.append(_chapter(2))
    parts.append(_system_loadpath(cfg))
    parts.append("<h3>Seismic force-resisting system (each principal direction)</h3>")
    parts.append(_lfrs_table(cfg))
    parts.append("<h3>Lateral-load design inputs</h3>"); parts.append(_lateral_inputs_table(cfg))
    if cfg.get("deformed_shape_figure"):   # OFF the default workflow (viewer shows the same live)
        parts.append(_img(_png_file_b64(os.path.join(figdir, f"{name}_deformed_X.png")),
                          "Deformed shape under lateral X (confirms a continuous lateral load path)"))
    else:
        parts.append("<p class='note'>Static deformed-shape figure omitted for faster reporting &mdash; "
                     "set cfg['deformed_shape_figure']=True and re-render to include it; the interactive "
                     "3D viewer (viewer_3d.html in the job download) shows the load path live.</p>")
    try:
        # The standalone 3D viewer (viewer_3d.html) is WRITTEN here as a side effect -- the frontend
        # "View model" button and the job download need the file even though the report no longer
        # embeds a viewer section (removed by request). Do not drop this call again.
        import viewer3d
        viewer3d.report_section(cfg, name, root)   # returns markup we deliberately discard
    except Exception as ex:
        print(f"[report] viewer_3d.html generation failed: {ex}")
    if Fx is not None:
        parts.append("<h3>Horizontal force distribution to the lateral frames</h3>")
        try: parts.append(_horizontal_distribution(cfg, Fx, VwX, VwY))
        except Exception as ex: parts.append(f"<p class='note'>[distribution failed: {ex}]</p>")
        parts.append("<h3>Diaphragm classification &amp; design force (ASCE 7-22 &sect;12.10)</h3>")
        try: parts.append(_diaphragm_section(cfg, Fx))
        except Exception as ex: parts.append(f"<p class='note'>[diaphragm section failed: {ex}]</p>")
        parts.append("<h3>Plan &amp; vertical irregularity screening</h3>")
        try: parts.append(_irregularity_section(cfg, Fx))
        except Exception as ex: parts.append(f"<p class='note'>[irregularity screen failed: {ex}]</p>")
    else:
        parts.append("<p class='cnote'>Diaphragm design force and irregularity screening need the seismic "
                     "analysis (Chapter 3).</p>")

    # ================================= Chapter 3 — Loads ==================================
    parts.append(_chapter(3))
    parts.append("<h3>Gravity loads</h3>"); parts.append(_gravity_loads_table(cfg))
    parts.append(_gravity_loads_note())
    parts.append("<h3>Wind load determination (ASCE 7-22 Ch. 26-31)</h3>"); parts.append(windhtml)
    parts.append(_wind_cc_note())
    parts.append("<h3>Seismic load determination (ASCE 7-22 Ch. 11-12)</h3>")
    if Fx is not None:
        parts.append(_seismic_loads_section(cfg, T, eX, eY, Cs, V, Tu, Ta, Fx, W))
    else:
        parts.append("<p class='note'>[seismic data unavailable]</p>")
    parts.append("<h3>Governing lateral load per direction</h3>")
    parts.append(_governing_lateral(cfg, V, VwX, VwY))

    # ========================== Chapter 4 — Load combinations ============================
    parts.append(_chapter(4))
    case_detail_parts = []
    try:
        cases = load_cases(cfg)
        parts.append(_combo_notes(cfg))
        parts.append("<h3>Combinations analysed (load factors)</h3>")
        parts.append(_combo_table(cases))
        parts.append(_combo_legend())
        # --- THREE separate opt-in items (each requestable on its own) -------------------
        #   cfg['force_summary']          -> the per-combination force SUMMARY table (heavy 27-combo solve)
        #   cfg['appendix_case_figures']  -> the Appendix-B per-combo N/V/M FIGURES (heavy; figs in the same loop)
        #   cfg['force_diagrams']         -> the Chapter-5 governing N/V/M frame DIAGRAMS (light; rendered below)
        _want_summary  = bool(cfg.get("force_summary"))
        _want_casefigs = bool(cfg.get("appendix_case_figures"))
        if Fx is not None and (_want_summary or _want_casefigs):      # this 27-combo solve feeds the table AND/OR the figs
            # RESUMABLE per-combo cache (design/_case_cache.pkl): each solved combo is saved as soon as
            # it finishes, so an interrupted render (client timeout / instance recycle) resumes instead
            # of restarting the whole 27+ solve loop. Keyed by a cfg fingerprint -> stale after redesign.
            import pickle as _pk, hashlib as _hl, os as _os
            _cache_p = _os.path.join(root, "design", "_case_cache.pkl")
            _fp = _hl.md5(repr(sorted((k, str(v)) for k, v in cfg.items()
                                      if not callable(v))).encode()).hexdigest()
            try:
                _cc = _pk.load(open(_cache_p, "rb"))
                if _cc.get("_fingerprint") != _fp:
                    _cc = {"_fingerprint": _fp}
            except Exception:
                _cc = {"_fingerprint": _fp}
            def _cc_save():
                try:
                    _os.makedirs(_os.path.dirname(_cache_p), exist_ok=True)
                    _tmp = _cache_p + ".tmp~"
                    with open(_tmp, "wb") as _f:
                        _pk.dump(_cc, _f)
                    _os.replace(_tmp, _cache_p)
                except Exception:
                    pass
            srows = []
            for _ci, combo in enumerate(cases):
                label, fD, fL, fLr, lat, col_only = combo
                if col_only:        # Omega0 column-overstrength: column-axial only -> reported in the member schedule (Ch.6)
                    if _want_summary:
                        srows.append([label, "&mdash;", "&Omega;<sub>0</sub> column overstrength (see Ch. 6)", "&mdash;", ""])
                    continue
                try:
                    _ckey = (str(label), bool(_want_casefigs))
                    if _ckey in _cc:
                        rows, (Nv, Nk, Ns), (Mv, Mk, Ms), uri, d = _cc[_ckey]
                    else:
                        rows, (Nv, Nk, Ns), (Mv, Mk, Ms), uri, d = _static_case_table(cfg, combo, render_fig=_want_casefigs)
                        _cc[_ckey] = (rows, (Nv, Nk, Ns), (Mv, Mk, Ms), uri, d)
                        _cc_save()
                    if _want_summary:
                        srows.append([label, f"{Nv:.0f}", f"{Nk} {Ns}".strip(), f"{Mv/12:.0f}", f"{Mk} {Ms}".strip()])
                        case_detail_parts.append(f"<h4>Load case: {label}</h4><p>{_case_desc(label, col_only)}</p>")
                        case_detail_parts.append(_table(
                            ["member", "section", "N (kip)", "Mz (k-ft)", "My (k-ft)", "V (kip)", "location (i,j / level)"],
                            rows))
                    if uri and _want_casefigs:
                        _save_case_fig(uri, figdir, label)   # -> figs/case_<label>.png (NOT embedded)
                except Exception as ex:
                    if _want_summary:
                        srows.append([label, "&mdash;", f"[failed: {ex}]", "&mdash;", ""])
            if _want_summary:
                parts.append("<h3>Load-case force summary</h3>")
                parts.append("<p>One row per combination: the largest axial force and the largest bending moment "
                             "anywhere in the structure, with the member type/section that carries it (from the "
                             "<b>static model</b>, so gravity beam moments are correct). The governing N/V/M diagrams are "
                             "in Chapter 5 (cfg['force_diagrams']); the per-combination N/V/M figures are in Appendix B "
                             "(cfg['appendix_case_figures']).</p>")
                parts.append(_table(["Load case", "Max axial N (kip)", "carried by", "Max moment M (k-ft)", "carried by"], srows))
        elif Fx is not None:
            parts.append("<h3>Load-case force summary</h3>")
            parts.append("<p class='note'>The per-combination force-summary table (one row per LRFD combination) is "
                         "temporarily omitted for faster reporting &mdash; it can be populated here on request at the "
                         "completion of the design. The governing per-member design demands are the enveloped values in "
                         "Chapter 6 / the member schedule.</p>")
    except Exception as ex:
        parts.append(f"<p class='note'>[load combinations failed: {ex}]</p>")

    # ======================= Chapter 5 — Analysis model fidelity =========================
    print("[%s] report: chapter 5 (analysis-model fidelity) ..." % name)
    parts.append(_chapter(5))
    rel = cfg.get("releases"); cb = cfg.get("custom_build")
    jt = ("custom (defined in custom_build)" if cb else
          "rigid / continuous except where moment releases are set" if rel else "all rigid / continuous (no releases)")
    parts.append("<h3>Modelling assumptions</h3>")
    parts.append(_table(["Assumption", "As modelled"], [
        ["Column bases", cfg.get("base", "fixed")],
        ["Beam-column &amp; brace joints", jt],
        ["Floor diaphragm", "rigid (in-plane), masters at floor centroid"],
        ["Geometric transform", "P-&Delta; (second-order) on columns"],
        ["Leaning gravity columns", "yes" if cfg.get("lean_gravity") else "framed into the lateral system"],
        ["Gravity for member forces", "static model &mdash; true two-way (45&deg;) tributary line loads on sub-divided beams"],
    ]))
    try:
        _md = (cfg.get("model") or {})
        _bs = str(_md.get("bases") or cfg.get("base") or "pinned")
        _jt = str(_md.get("joints") or ("pinned" if cfg.get("releases") else "rigid"))
        _cap = f"Modelled joint &amp; base fixity &mdash; column bases: {_bs}; girder joints: {_jt}"
        try:
            if E.is_braced(cfg): _cap += "; braces pin-ended (axial-only truss members)"
        except Exception: pass
        if cfg.get("lean_gravity"): _cap += "; leaning gravity columns"
        parts.append(_img(_joint_figure(cfg), _cap + ".", full=True))
    except Exception as _jex: parts.append(f"<p class='note'>[joint figure failed: {_jex}]</p>")
    _jmodel = None
    if cfg.get("custom_build"):
        try:
            import static_model as SM; _jmodel = SM.build_static(cfg, "Linear", 2)
        except Exception:
            _jmodel = None
    for _d in ("X", "Y"):
        try:
            _ju = _joint_dist_fig(cfg, _d, model=_jmodel)
            if _ju:
                parts.append(_img(_ju, f"Joint-fixity distribution &mdash; {_d}-direction perimeter vs internal frame "
                                       "(filled square = rigid/continuous, open circle = pinned/shear release, "
                                       "triangle = column base; pinned = strong-axis vertical moment released)"))
        except Exception as ex:
            parts.append(f"<p class='note'>[joint-distribution figure {_d} failed: {ex}]</p>")
    parts.append(_stability_basis_note())
    if Fx is not None and reX is not None:
        Rx = sum(r[2][0] for r in reX); Rz = sum(r[2][2] for r in reX)
        parts.append("<h3>Equilibrium check (seismic X combination)</h3>")
        parts.append(f"<p>|&Sigma;R<sub>x</sub>| = {abs(Rx):.0f} kip vs applied base shear {sum(Fx.values()):.0f} kip "
                     f"(equal and opposite); &Sigma;R<sub>z</sub> = {abs(Rz):.0f} kip factored gravity delivered to the "
                     "ground. Reactions balance the applied loads in all three axes (load path verified).</p>")
        if eX is not None and eY is not None:
            parts.append(_modal_mass_check(eX, eY))
        _want_modefigs = bool(cfg.get("mode_figures"))     # 3D mode-shape figures: OFF the default workflow
        for m in (1, 2, 3):
            if m <= NF*3:
                if _want_modefigs:
                    try: parts.append(_img(fig_mode_3d(cfg, m, 3), f"Mode {m} (T = {T[m-1]:.2f} s)", full=True))
                    except Exception as ex: parts.append(f"<p class='note'>[mode {m} failed: {ex}]</p>")
                else:
                    parts.append(f"<p class='note'>Mode {m} (T = {T[m-1]:.2f} s) shape figure temporarily omitted for faster "
                                 "reporting &mdash; can be populated here on request at the completion of the design.</p>")
        # animated mode-shape GIFs removed from the report: the interactive 3D viewer
        # ("View model" button, Chapter 2) animates all six modes on demand.
        else:
            pass   # animated mode-shape GIFs are gone for good -- the interactive 3D viewer animates all modes live
    try:
        if not bool(cfg.get("force_diagrams")):
            parts.append("<p class='note'>Governing N/V/M frame diagrams (perimeter &amp; internal lines, from the "
                         "static model) temporarily omitted for faster reporting &mdash; can be populated here on "
                         "request at the completion of the design.</p>")
            gd = {}
        else:
            import frame_diagram as FD
            gd = FD.governing_diagrams(cfg, load_cases(cfg), nseg=6, lines=("perimeter", "internal"))   # nseg 2->6 (opt-in figures)
        if gd.get("perimeter"):
            parts.append("<h3>Governing internal-force diagrams &mdash; perimeter frames (static model)</h3>")
            parts.append("<p>N / V / M for each perimeter frame under its governing LRFD combination, from the "
                         "static model (true two-way tributary loads on sub-divided beams). Peak values annotated "
                         "per member; columns are single elements (linear between ends).</p>")
            for _d in ("X", "Y"):
                if _d in gd["perimeter"]:
                    _uri, _label, _pkM = gd["perimeter"][_d]
                    parts.append(_img(_uri, f"{_d}-direction perimeter frame &mdash; governing combo {_label} "
                                            f"(peak beam moment {_pkM:.0f} k-ft)"))
        if gd.get("internal"):
            parts.append("<h3>Governing internal-force diagrams &mdash; internal frames (static model)</h3>")
            parts.append("<p>The same diagrams for an interior frame line, which generally carries more gravity "
                         "tributary (and less lateral) than the perimeter &mdash; the gravity-governed companion check.</p>")
            for _d in ("X", "Y"):
                if _d in gd["internal"]:
                    _uri, _label, _pkM = gd["internal"][_d]
                    parts.append(_img(_uri, f"{_d}-direction internal frame &mdash; governing combo {_label} "
                                            f"(peak beam moment {_pkM:.0f} k-ft)"))
    except Exception as ex:
        parts.append(f"<p class='note'>[static-model diagrams unavailable: {ex}]</p>")

    # ========== Chapter 6 — Member & wall strength design (AISI S100/S240/S400) ==========
    parts.append(_chapter(6))
    _cfs_scheds = _cfs_schedules_section(pkg)
    if _cfs_scheds:
        parts.append(_cfs_scheds)
    parts.append(_member_section(cfg, name, pkg, {"stud", "chord"}, "Studs & chord studs",
        "Bearing studs and shear-wall chord studs for the CUMULATIVE axial stack (S100 E2/E3 + "
        "distortional E4, H1 interaction; declared bracing assumption; built-up chords per S240)."))
    parts.append(_member_section(cfg, name, pkg, {"track", "joist"}, "Track, joists & headers",
        "Track at bearing (S100 G5 web crippling + combined checks) and joists/headers "
        "(F2&ndash;F4 flexure, G2 shear, S240 framing rules)."))
    parts.append(_member_section(cfg, name, pkg, {"strap"}, "Straps",
        "Flat straps in tension: A<sub>g</sub>F<sub>y</sub> yield vs A<sub>n</sub>F<sub>u</sub> rupture "
        "(the S400 E3 ductility inequality)."))
    parts.append(_member_section(cfg, name, pkg, {"collector"}, "Collectors",
        "Collectors/drag struts at re-entrant corners, steps and diaphragm throats "
        "(&Omega;<sub>0</sub>-amplified demand)."))
    parts.append(_member_section(cfg, name, pkg, {"roof", "floor"}, "Frame-path roof/floor members",
        "Portal rafters / mezzanine beams (S100 F/G limit states)."))
    parts.append(_member_section(cfg, name, pkg, {"gravity_col", "lateral_col", "purlin"},
        "Frame-path columns, purlins/girts",
        "Portal columns (S100 E/H with the declared effective-stiffness basis), purlins/girts "
        "(uplift regime stated)."))
    if cfg.get("braces"):
        parts.append(_member_section(cfg, name, pkg, {"brace"}, "Braces",
            "Discrete braces for the design story shear (S100 E compression / D tension)."))
    parts.append(_ch6_notes(cfg))
    if pkg:
        parts.append("<h3>Member design summary (calc_package.json)</h3>")
        parts.append(f"<p>Source: <code>{os.path.relpath(pkgsrc, HERE)}</code>. Capacities/D-C derived by the agent "
                     "from the AISI S100/S240/S400 RAG; full referenced calc in Appendix A.</p>")
        parts.append(_member_dc_summary(pkg))
        _comp = _composite_section(pkg)
        if _comp:
            parts.append(_comp)
        _xtra = _extra_blocks_section(pkg)
        if _xtra:
            parts.append(_xtra)
        _msfig = ""
        if cfg.get("section_color_figure"):    # OFF the default workflow (viewer's 'Color by section' shows it live)
            try:
                import viz3d as _VZ
                _msu = _VZ.members_by_size(cfg)
                if _msu:
                    _msfig = _img(_msu, "Model members coloured by section size &mdash; each distinct member size "
                                        "(e.g. each W-column / beam / HSS brace) drawn in its own colour (legend)")
            except Exception as _mex:
                _msfig = f"<p class='note'>[member-size figure failed: {_mex}]</p>"
        else:
            _msfig = ("<p class='note'>Member-size colour figure omitted for faster reporting &mdash; set "
                      "cfg['section_color_figure']=True and re-render to include it; the interactive 3D viewer's "
                      "'Color by section' view shows the same.</p>")
        parts.append(_narrative_html(pkg, members_fig_html=_msfig))
    else:
        parts.append("<p class='note'>[no calc_package.json found &mdash; run the design first]</p>")

    # ====================== Chapter 7 — Stability & second-order =========================
    parts.append(_chapter(7))
    if Fx is not None and drX is not None:
        try:
            parts.append(_stability_section(cfg, Fx, drX, pkg))
            parts.append(_img(fig_drift_profile(cfg, drX, drY), "Interstory drift profile (both directions)"))
            ss_uri, Vstory, OTM = fig_story_shear_otm(cfg, Fx)
            parts.append(f"<p>Base shear &Sigma;F = {sum(Fx.values()):.0f} kip; base overturning &asymp; {OTM:.0f} k-ft.</p>")
            parts.append(_img(ss_uri, "Story-shear and overturning-moment profiles"))
        except Exception as ex:
            parts.append(f"<p class='note'>[stability section failed: {ex}]</p>")
    else:
        parts.append("<p class='note'>[needs seismic drift data]</p>")

    # ============================ Chapter 8 — Serviceability =============================
    parts.append(_chapter(8))
    if Fx is not None and drX is not None:
        Cd = s.get("Cd", 5.0); Ie = s["Ie"]; lim = cfg.get("drift_limit", 0.02)
        parts.append("<h3>Seismic design drift</h3>")
        parts.append(f"<p>Elastic story drift &delta;<sub>e</sub> amplified to &delta; = C<sub>d</sub>&delta;<sub>e</sub>/I<sub>e</sub> "
                     f"(&sect;12.8.6, C<sub>d</sub>={Cd}, I<sub>e</sub>={Ie}); allowable {lim*100:.1f}% of story height.</p>")
        _sdr = None
        try:
            _sdr = E.seismic_drift(cfg) if hasattr(E, "seismic_drift") else None
        except Exception:
            _sdr = None
        if _sdr:
            # hot-rolled route: the drift the gate checks (12.8.6.5 location, each direction's Cd,
            # 12.12.1.1 rho for moment frames, AISC 358 RBS) -- ported from steltic (HR-09/HR-22)
            parts.append("<p class='note'>Design drift at the &sect;12.8.6.5 location per direction "
                         "(X: %s, C<sub>d</sub> per direction; limit X %.2f%% / Y %.2f%%).</p>"
                         % (_e(_sdr["X"].get("location", "")), _sdr["X"]["limit"] * 100, _sdr["Y"]["limit"] * 100))
            drow = [[k, f"{_sdr['X']['elastic'][k-1]*100:.3f}", f"{_sdr['X']['design'][k-1]*100:.3f}",
                     f"{_sdr['Y']['elastic'][k-1]*100:.3f}", f"{_sdr['Y']['design'][k-1]*100:.3f}",
                     "OK" if (_sdr['X']['design'][k-1] <= _sdr['X']['limit'] + 1e-9 and
                              _sdr['Y']['design'][k-1] <= _sdr['Y']['limit'] + 1e-9) else "NG"]
                    for k in range(1, NF+1)]
            parts.append(_table(["Story", "&delta;e X %", "&Delta; X %", "&delta;e Y %", "&Delta; Y %", "Status"], drow))
        else:
            drow = [[k, f"{drX[k-1]*100:.3f}", f"{drX[k-1]*Cd/Ie*100:.3f}", f"{drY[k-1]*100:.3f}",
                     f"{drY[k-1]*Cd/Ie*100:.3f}", "OK" if max(drX[k-1], drY[k-1])*Cd/Ie <= lim else "NG"]
                    for k in range(1, NF+1)]
            parts.append(_table(["Story", "&delta;e X %", "&delta; X %", "&delta;e Y %", "&delta; Y %", f"&le;{lim*100:.1f}%"], drow))
    parts.append(_wind_drift_section(cfg))
    parts.append(_floor_serviceability(pkg))
    parts.append(_deflection_section(cfg))
    parts.append("<p class='cnote'><b>Out of scope (this model):</b> floor vibration and "
                 "building separation/pounding are detail-level serviceability checks confirmed against the framing drawings.</p>")

    # ========== Chapter 9 — Lateral-system detailing & capacity design (AISI S400) ==========
    parts.append(_chapter(9))
    parts.append(_s400_capacity_chapter(cfg, pkg))

    # ========================= Chapter 10 — Connections ==================================
    parts.append(_chapter(10))
    _ctbl = _connection_demands(cfg, pkg, reX)
    if _ctbl:
        parts.append("<p>Connection design demands transmitted to the fabricator / connection engineer (member end "
                     "forces from the analysis; each connection is then DESIGNED to these demands below):</p>")
        parts.append(_ctbl)
    parts.append("<p class='cnote'><b>Designed in this package:</b> each connection is sized to the demands above per "
                 "AISI S100 Ch. J (screws: tilting/bearing/pull-out/pull-over; welds on thin sheet; bolts with "
                 "tilting), with capacity-design demands per S400 where the chain requires them (strap connections "
                 "at R<sub>y</sub>F<sub>y</sub>A<sub>g</sub>) &mdash; limit "
                 "state, capacity and D/C &le; 1.0 derived by the agent from the RAG (see Chapter 6 / Appendix A). "
                 "Sheathing fastener schedules ARE the wall connection design (Chapter 6). Anchorage to concrete is "
                 "scoped to ACI 318 Ch. 17 with demands handed off; only shop-level detailing is confirmed on the "
                 "fabricator's submittal.</p>")

    # ===================== Chapter 11 — Foundations interface ============================
    parts.append(_chapter(11))
    if reX is not None:
        Rz = sum(r[2][2] for r in reX); Rx = sum(r[2][0] for r in reX)
        pmax = max((r[2][2] for r in reX), default=0.0); pmin = min((r[2][2] for r in reX), default=0.0)
        parts.append("<p>Column base reactions delivered to the foundation design (seismic X combination):</p>")
        parts.append(_table(["Quantity", "Value"], [
            ["&Sigma; vertical to ground &Sigma;R<sub>z</sub>", f"{Rz:.0f} kip"],
            ["&Sigma; horizontal base shear &Sigma;R<sub>x</sub>", f"{Rx:.0f} kip"],
            ["Max single-column vertical reaction", f"{pmax:.0f} kip (compression)"],
            ["Min single-column vertical reaction", f"{pmin:.0f} kip " + ("&mdash; <b>net uplift</b>" if pmin < 0 else "(no uplift)")]]))
    parts.append("<p class='cnote'>Net uplift is governed by the 0.9D+1.0W and (0.9&minus;0.2S<sub>DS</sub>)D+E "
                 "combinations — for light CFS at high wind it commonly GOVERNS the anchorage: hold-down/rod "
                 "tensions and the per-line shear (plf) and bearing handed to the foundation are in the Chapter-6 "
                 "schedules. <b>Out of scope:</b> foundation/geotechnical design and concrete anchorage detailing "
                 "(ACI 318 Ch. 17) are delegated with these demands. Overall overturning/sliding stability is "
                 "confirmed against the geotechnical capacities.</p>")

    # ============== Chapter 12 — Drawings, specifications & documentation ================
    parts.append(_chapter(12, status="out of scope for the analysis package &mdash; verified on the contract drawings"))
    parts.append("<p class='cnote'>Drawing/specification consistency, general notes, special-inspection schedule, "
                 "member schedules, framing plans, brace/MF elevations, connection details and the deferred-submittal "
                 "list are produced and checked on the contract documents, not in this analysis report.</p>")

    # ===================== Chapter 13 — QA / professional acceptance =====================
    parts.append(_chapter(13))
    parts.append(_qa_scorecard(cfg, Fx, reX, eX, eY, drX, drY))
    parts.append(_grounding_check(cfg, name, pkg))
    parts.append(_consistency_section(name, root, pkg))
    parts.append("<p>Automated QA evidence in this report: per-combination equilibrium balances to ~0 in all three "
                 "axes (Chapter 5); every member demand is enveloped over the full ASCE 7-22 combination set "
                 "(Chapter 4 / Appendix B); each capacity is traceable to a cited AISI clause (Appendix A); and "
                 "the tool-call activity log is in Appendix C.</p>")
    parts.append("<p class='cnote'><b>Out of scope (engineer judgement):</b> independent third-party check, software "
                 "validation sign-off, reconciliation of model assumptions against final detailing, and the EOR seal "
                 "are professional-responsibility steps completed outside the automated package.</p>")

    # ============================== Appendices ==========================================
    parts.append("<h2>Appendix A &mdash; Referenced AISI S100/S240/S400 calculations</h2>")
    if pkg:
        try: parts.append(appendix(cfg, name, pkg))
        except Exception as ex: parts.append(f"<p class='note'>[Appendix A failed: {ex}]</p>")
    else:
        parts.append("<p class='note'>[no calc_package.json found]</p>")
    parts.append("<h2>Appendix B &mdash; Member forces by load case</h2>")
    if case_detail_parts:
        parts.append("<p>Full per-combination detail (summarised in Chapter 4): the worst member of each type "
                     "with its location, from the static model (true distributed gravity). The per-combination "
                     "N / V / M diagrams are written to the <code>figs/</code> folder (<code>case_&lt;label&gt;.png</code>) for "
                     "review &mdash; not embedded here, to keep the report compact. The headline governing diagrams "
                     "(perimeter + internal) are in Chapter 5.</p>")
        parts.extend(case_detail_parts)
    else:
        parts.append("<p class='note'>Per-load-case member force tables temporarily omitted for faster "
                     "reporting &mdash; can be populated here on request at the completion of the design "
                     "(set cfg['force_summary']=True and re-render report.build_report).</p>")
    parts.append(_activity_section(name))

    html = (f"<!doctype html><html><head><meta charset='utf-8'><title>{name} report</title>"
            f"<style>{CSS}{CHK_CSS}</style>{MATHJAX}</head><body>" + "".join(parts) + "</body></html>")
    _fc = [0]
    def _fignum(_m):
        _fc[0] += 1; return f"<b>Figure {_fc[0]}.</b>"
    html = re.sub("@@FIGNUM@@", _fignum, html)        # number every figure in document order
    outdir = root; os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "report.html")
    open(path, "w", encoding="utf-8").write(html)
    print(f"[{name}] wrote {path}")
    return path

def _narrative_html(pkg, members_fig_html=""):
    out = []
    ar = pkg.get("analysis_results")
    if isinstance(ar, dict) and isinstance(ar.get("base_shear"), dict):
        out.append("<p><b>Lateral:</b> " + _fmt(ar["base_shear"]) + "</p>")
    if isinstance(pkg.get("members"), list):
        def _dmd(inp):
            return _fmt({k: inp.get(k) for k in ("P_comp_kip","P_tens_kip","Mz_kipin","My_kipin","V_kip") if inp.get(k) is not None})
        rows = [[m.get("id", ""), (m.get("inputs", {}) or {}).get("section", ""),
                 _dmd(m.get("inputs", {}) or {}), (m.get("inputs", {}) or {}).get("governing_combo", ""),
                 (str(m.get("limit_state")) if m.get("limit_state") else "- (agent/RAG)"),
                 (str(m.get("DC")) if m.get("DC") is not None else "- (agent/RAG)")] for m in pkg["members"]]
        out.append("<h3>Members (demands; capacities derived by the agent from the RAG)</h3>"
                   + members_fig_html
                   + _table(["member", "section", "demand", "gov. combo", "limit state", "D/C"], rows))
    cd = pkg.get("connections") or pkg.get("connection_demands")
    if isinstance(cd, list) and cd:
        def _conn_govern(c):
            # a connection has several limit states; the agent nests them in 'checks' [{limit_state, DC}, ...].
            # Surface the governing (max-D/C) one; fall back to any top-level limit_state/DC.
            ls, dc = c.get("limit_state"), c.get("DC")
            chk = c.get("checks")
            if (ls is None or dc is None) and isinstance(chk, list) and chk:
                best = max((k for k in chk if isinstance(k, dict) and k.get("DC") is not None),
                           key=lambda k: k.get("DC") or 0, default=None)
                if best:
                    if dc is None: dc = best.get("DC")
                    if ls is None: ls = best.get("limit_state")
            return ls, dc
        rows = []
        for c in cd:
            ls, dc = _conn_govern(c)
            nchk = len(c.get("checks")) if isinstance(c.get("checks"), list) else 0
            ls_txt = (str(ls) + (f"  (+{nchk - 1} more)" if nchk > 1 else "")) if ls else "- (agent/RAG)"
            dc_txt = (f"{dc:.2f}" if isinstance(dc, (int, float)) else (str(dc) if dc else "- (agent/RAG)"))
            rows.append([c.get("id", ""), c.get("type", ""), _fmt(c.get("demand", {})), ls_txt, dc_txt])
        out.append("<h3>Connections (demands; capacities derived by the agent from the RAG)</h3>"
                   + _table(["connection", "type", "demand", "governing limit state", "D/C"], rows))
    elif isinstance(cd, dict):
        rows = [[k, v.get("type", ""), _fmt(v.get("demands", {})), v.get("notes", "")] for k, v in cd.items()]
        out.append("<h3>Connections</h3>" + _table(["connection", "type", "demand", "design basis"], rows))
    return "".join(out) if out else "<pre>" + json.dumps(pkg, indent=1)[:4000] + "</pre>"

# ============================================================ CFS report entry (Stage 3b)

def _tbl(head, rows):
    h = "".join(f"<th>{x}</th>" for x in head)
    b = "".join("<tr>" + "".join(f"<td>{x}</td>" for x in r) + "</tr>" for r in rows)
    return f"<table><tr>{h}</tr>{b}</table>"


def _fmt_dc(v):
    return _cdc(v)


CFS_CSS = (".tnote{font-size:12px;color:#444;margin:2px 0 10px}ol.tnote{font-size:12px;color:#444;"
           "margin:2px 0 12px;padding-left:22px}ol.tnote li{margin:1px 0}"
           ".credit{font-size:13px;color:#333;margin:2px 0 10px}"
           "table.sc td{text-align:left;vertical-align:top}table.sc td.v{text-align:right;"
           "white-space:nowrap;font-weight:600}.pass{color:#1a6b2d;font-weight:600}"
           ".fail{color:#a00;font-weight:600}")

CFS_EDITION = "AISI S100-16 (2020) w/S2-20 (as referenced by IBC 2024)"


def _cfs_is_portal(cfg):
    return "span_ft" in (cfg or {}) or bool((cfg or {}).get("spans"))


def _cfs_publish(cfg, pkg):
    """Publication mode is the default: the report body carries engineering content only; the
    design-process records go to qa_annex.html. cfg/pkg 'report_mode' = 'draft' keeps them
    inline (development use)."""
    m = (cfg or {}).get("report_mode") or (pkg or {}).get("report_mode") or "publish"
    return str(m).strip().lower() != "draft"


def _cfs_design_credit(pkg):
    """'Designed by <model> (<provider>) with Steltic, <date>' from
    calc_package_cfs['design_agent'] (same wording as the hot-rolled report); '' when no
    model is recorded. A plain string is the model name."""
    da = (pkg or {}).get("design_agent") if isinstance(pkg, dict) else None
    if isinstance(da, str) and da.strip():
        da = {"model": da.strip()}
    if not isinstance(da, dict) or not da.get("model"):
        return ""
    harness = da.get("harness") or "Steltic"
    return _html.escape("Designed by %s%s with %s%s" % (
        da["model"], (" (%s)" % da["provider"]) if da.get("provider") else "", harness,
        (", %s" % da["date"]) if da.get("date") else ""))


def _cfs_rc(cfg):
    rc = str((cfg or {}).get("risk_cat") or "").strip().upper()
    return {"1": "I", "2": "II", "3": "III", "4": "IV"}.get(rc, rc)


def _cfs_sdc_detail(cfg):
    """(SDC or None, explanation): ASCE 7-22 Tables 11.6-1 (S_DS) and 11.6-2 (S_D1), the more
    severe governs; S1 >= 0.75 -> E/F. SDC A is recognised (11.7 / 1.4 minimum force)."""
    s = (cfg or {}).get("seis") or {}
    rc = _cfs_rc(cfg) or "II"
    SDS, SD1, S1 = _fnum(s.get("SDS")), _fnum(s.get("SD1")), _fnum(s.get("S1"))
    rc4 = rc == "IV"

    def cat(x, rows):
        for ub, c, c4 in rows:
            if x < ub:
                return c4 if rc4 else c
        return "D"
    if SDS is None and SD1 is None:
        return None, "<b>not stated</b> (S<sub>DS</sub> and S<sub>D1</sub> not given)"
    parts, cats = [], []
    if SDS is not None:
        c1 = cat(SDS, [(0.167, "A", "A"), (0.33, "B", "C"), (0.50, "C", "D")])
        cats.append(c1)
        parts.append("Table 11.6-1: S<sub>DS</sub> = %s &rarr; %s" % (_cn(SDS), c1))
    if SD1 is not None:
        c2 = cat(SD1, [(0.067, "A", "A"), (0.133, "B", "C"), (0.20, "C", "D")])
        cats.append(c2)
        parts.append("Table 11.6-2: S<sub>D1</sub> = %s &rarr; %s" % (_cn(SD1), c2))
    else:
        parts.append("Table 11.6-2 not evaluated (S<sub>D1</sub> not stated)")
    c = max(cats)
    if S1 is not None and S1 >= 0.75:
        c = "F" if rc4 else "E"
        parts.append("S<sub>1</sub> = %s &ge; 0.75 &rarr; %s (&sect;11.6)" % (_cn(S1), c))
    note = ""
    if c == "A":
        note = (" &mdash; SDC A: ASCE 7-22 &sect;11.7, the &sect;1.4 minimum lateral force applies; "
                "the Table 12.2-1 coefficients are not used")
    return c, "<b>%s</b> (%s, Risk Category %s)%s" % (c, "; ".join(parts), rc, note)


def _cfs_sys_row(cfg, sysname=None):
    try:
        import cfs_systems as _CS
        return _CS.SYSTEMS.get(sysname if sysname is not None else _cfs_sys(cfg)) or {}
    except Exception:
        return {}


def _cfs_factors(cfg, d=None):
    """(R, Cd, Om0, note) of the SFRS: the declared seis values; Cd / Omega0 not declared are
    taken from ASCE 7-22 Table 12.2-1 for the declared system when its R matches (portal frames:
    R = 3 'not specifically detailed', row H); otherwise 'not stated'."""
    s = dict((cfg or {}).get("seis") or {})
    if d and isinstance((cfg or {}).get("seis_by_dir"), dict) and cfg["seis_by_dir"].get(d):
        s.update(cfg["seis_by_dir"][d])
    sysname = _cfs_dir_system(cfg, d) if d else _cfs_sys(cfg)
    row = _cfs_sys_row(cfg, sysname)
    R = _fnum(s.get("R"))
    if not row and R is not None and abs(R - 3.0) < 1e-9 and _cfs_is_portal(cfg):
        row = _cfs_sys_row(cfg, "not_detailed")
    notes = []
    vals = {}
    for k in ("Cd", "Om0"):
        v = _fnum(s.get(k))
        if v is None and row and R is not None and abs(float(row.get("R", -1)) - R) < 1e-9:
            v = float(row[k])
            notes.append("%s from Table 12.2-1" % ("C<sub>d</sub>" if k == "Cd" else "&Omega;<sub>0</sub>"))
        vals[k] = v
    return R, vals["Cd"], vals["Om0"], ", ".join(notes)


def _cfs_s400_status(cfg, pkg=None):
    """One sentence on whether AISI S400 applies (A1.2.3 / B3.4) for the codes table."""
    try:
        import cfs_systems as _CS
    except Exception:
        return "seismic design of the CFS lateral system"
    sysname = _cfs_sys(cfg, pkg)
    row = _CS.SYSTEMS.get(sysname) or {}
    sdc, _x = _cfs_sdc_detail(cfg)
    s = (cfg or {}).get("seis") or {}
    if not row.get("s400"):
        return ("not applicable: the system is not an S400 system (design to AISI S100 / S240 "
                "only)" if sysname else "not applicable (no S400 seismic system declared)")
    if sdc is None:
        return "Section %s (%s)" % (row["s400"], _SYS_SHORT.get(sysname, sysname))
    req, _b = _CS.capacity_design_required(sysname, s.get("R"), sdc)
    if req:
        return ("Section %s (%s): seismic design of the SFRS, expected-strength capacity design "
                "per B3.4" % (row["s400"], _SYS_SHORT.get(sysname, sysname)))
    return ("not required: A1.2.3 (SDC %s, R = %s) &mdash; members and connections designed to "
            "AISI S100 / S240" % (sdc, _cn(_fnum(s.get("R")))))


def _cfs_agent_blob(pkg):
    try:
        import consistency as _CC
        return _CC._agent_text(pkg)
    except Exception:
        return json.dumps(pkg or {}).lower()


def _cfs_codes_rows(cfg, pkg):
    portal = _cfs_is_portal(cfg)
    blob = _cfs_agent_blob(pkg)
    rows = [["IBC 2024", "adopting building code (confirm the locally adopted edition and amendments)"],
            ["ASCE/SEI 7-22", "loads and LRFD combinations: gravity (Ch. 4), snow (Ch. 7), seismic "
                              "(Ch. 11&ndash;12), wind (Ch. 26&ndash;30)"],
            [CFS_EDITION, "cold-formed steel member and connection design (LRFD)"]]
    if not portal or pkg.get("studs") or pkg.get("wall_lines"):
        rows.append(["AISI S240-20", "cold-formed steel structural framing (studs, track, joists, "
                                     "headers, built-up members, bracing)"])
    rows.append(["AISI S400-20", _cfs_s400_status(cfg, pkg)])
    rows.append(["ACI 318-19 Ch. 17", "anchorage to concrete (anchor design forces handed to the "
                                      "foundation design)"])
    if re.search(r"\bweld", blob):
        rows.append(["AWS D1.3", "structural welding of sheet steel"])
    if re.search(r"aisc\s*360|\bhss\s*\d|\bw\d{1,2}x\d", blob):
        rows.append(["AISC 360-22", "hot-rolled steel components"])
    return rows


def _ft(x):
    v = _fnum(x)
    return ("%s ft" % _cn(v)) if v is not None else "&mdash;"


def _cfs_building_rows(cfg, res, pkg):
    portal = _cfs_is_portal(cfg)
    occ = cfg.get("occupancy") or pkg.get("occupancy")
    rc = _cfs_rc(cfg)
    rows = [["Occupancy / use", _pt(occ) if occ else "<b>not stated</b>"],
            ["Risk Category (IBC Table 1604.5)", rc if rc else "<b>not stated</b> (Risk Category II "
                                                             "used by the analysis)"]]
    sbd = cfg.get("system_by_dir") or {}
    if sbd:
        rows.append(["Seismic force-resisting system", "; ".join(
            "%s: %s" % (_html.escape(d), _pt((_cfs_sys_row(cfg, str(v).lower()) or {}).get("label") or v))
            for d, v in sorted(sbd.items()))])
    else:
        sysname = _cfs_sys(cfg, pkg)
        lab = (_cfs_sys_row(cfg, sysname) or {}).get("label")
        if sysname == "not_detailed":
            lab = "steel system not specifically detailed for seismic resistance (Table 12.2-1 row H)"
        rows.append(["Seismic force-resisting system",
                     _pt(lab or sysname.replace("_", " ")) if (lab or sysname)
                     else "<b>not stated</b>"])
    if portal:
        if cfg.get("spans"):
            spans = " + ".join(_cn(_fnum(s_.get("span_ft"))) for s_ in cfg["spans"])
            rows.append(["Frame geometry", "%d spans (%s ft), eave %s, apex %s" % (
                len(cfg["spans"]), spans, _ft(cfg.get("eave_ft")),
                ", ".join(_ft(s_.get("apex_ft")) for s_ in cfg["spans"]))])
        else:
            rows.append(["Frame geometry", "%s span, %s eave, %s apex%s" % (
                _ft(cfg.get("span_ft")), _ft(cfg.get("eave_ft")), _ft(cfg.get("apex_ft")),
                " (monoslope)" if cfg.get("monoslope") else "")])
        rows.append(["Frame spacing / building length", "%s / %s" % (
            _ft(cfg.get("spacing_ft")), _ft(cfg.get("length_ft")) if cfg.get("length_ft") else "not stated")])
        rows.append(["Base fixity", _pt(cfg.get("base", "pinned"))])
        rows.append(["Frame members", "columns %s; rafters %s" % (_pt(cfg.get("col_section")),
                                                                 _pt(cfg.get("raf_section")))])
    else:
        H = [_fnum(h) for h in (cfg.get("heights_ft") or []) if _fnum(h) is not None]
        rows.append(["Stories / story heights", "%s / %s ft" % (_pt(cfg.get("stories")),
                                                                ", ".join(_cn(h) for h in H))])
        rows.append(["Structural height h<sub>n</sub>", _ft(sum(H)) if H else "&mdash;"])
        pl = cfg.get("plan_ft") or ()
        if len(pl) == 2:
            rows.append(["Plan dimensions", "%s &times; %s ft" % (_cn(_fnum(pl[0])), _cn(_fnum(pl[1])))])
        dm = cfg.get("diaphragm_material")
        rows.append(["Floor / roof diaphragm", "%s%s" % (
            _pt(cfg.get("diaphragm", "flexible")),
            ("; " + _pt(dm)) if dm else "; diaphragm construction not stated")])
    return rows


def _cfs_height_limit(cfg):
    try:
        import cfs_systems as _CS
    except Exception:
        return None
    sysname = _cfs_sys(cfg)
    row = _CS.SYSTEMS.get(sysname)
    sdc, _x = _cfs_sdc_detail(cfg)
    if not row or not sdc or sdc == "A":
        return None
    H = [_fnum(h) for h in (cfg.get("heights_ft") or []) if _fnum(h) is not None]
    hn = sum(H) if H else _fnum(cfg.get("apex_ft"))
    lim = row["hlim"].get(sdc)
    if lim == 0.0:
        return "<b>not permitted in SDC %s</b> (Table 12.2-1)" % sdc
    if lim is None:
        return "not limited in SDC %s (Table 12.2-1)%s" % (sdc, (", h<sub>n</sub> = %s" % _ft(hn)) if hn else "")
    if hn is None:
        return "%s ft in SDC %s (Table 12.2-1)" % (_cn(float(lim)), sdc)
    ok = hn <= lim
    return "h<sub>n</sub> = %s %s %s ft (SDC %s, Table 12.2-1) &mdash; %s" % (
        _ft(hn), "&le;" if ok else "&gt;", _cn(float(lim)), sdc, "OK" if ok else "<b>EXCEEDS</b>")


def _find_theta(pkg):
    """(theta, theta_max, where) of the largest theta reported anywhere in the package; 'where'
    is a readable location for wall drift rows."""
    best = None

    def walk(o, path):
        nonlocal best
        if isinstance(o, dict):
            th = o.get("theta")
            if isinstance(th, (int, float)) and not isinstance(th, bool):
                lim = next((o.get(k) for k in ("theta_max", "theta_limit", "limit")
                            if isinstance(o.get(k), (int, float))), None)
                where = ("%s line %s, story %s" % (o.get("direction"), o.get("line"), o.get("story"))
                         if o.get("line") is not None else (o.get("check") or path.split(".")[0]))
                if best is None or th > best[0]:
                    best = (th, lim, where)
            for k, v in o.items():
                if k != "combos":
                    walk(v, path + "." + str(k) if path else str(k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, "%s[%d]" % (path, i))
    walk(pkg or {}, "")
    return best


def _find_key(obj, names):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in names and v not in (None, "", [], {}):
                return v
            r = _find_key(v, names)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, names)
            if r is not None:
                return r
    return None


_CFS_TIER_TEXT = {
    "wall": "linear per-line shear-spring stacks (secant stiffness from the S400 wall-deflection "
            "equations), %s",
    "portal": "planar frame analysis with effective-section stiffness (AISI S100 Appendix 1) and "
              "direct analysis per AISI S100-16 C1.1 (0.90 EA/EI &times; &tau;<sub>b</sub>, notional "
              "loads N<sub>i</sub> = Y<sub>i</sub>/240, P-&Delta;) on the strength combinations; no "
              "warping / torsion degree of freedom (single-channel torsion is checked separately)",
}


def _cfs_governing_hazard(pkg):
    gh = pkg.get("governing_hazard")
    if gh:
        return _cn(gh) if isinstance(gh, (dict, list)) else _pt(gh)
    cnt = {}
    for w in pkg.get("wall_lines") or []:
        if not isinstance(w, dict) or not w.get("governing_basis"):
            continue
        d = str(w.get("direction") or "")
        c = cnt.setdefault(d, [0, 0])
        c[0 if str(w["governing_basis"]).lower().startswith("wind") else 1] += 1
    if not cnt:
        return "<b>not stated</b>"
    return "; ".join("%s: wind governs %d of %d wall slots, seismic %d (analysis comparison, "
                     "&rho; included)" % (_html.escape(d), c[0], c[0] + c[1], c[1])
                     for d, c in sorted(cnt.items()))


def _cfs_basis_rows(cfg, res, pkg):
    """Seismic design and analysis basis: Ie, site class, S_DS/S_D1, SDC (Tables 11.6-1/-2), the
    SFRS R/Cd/Omega0 (portal frames too), rho, height limit, ELF, seismic weight by level,
    period, S400 capacity-design applicability, drift limit and basis, theta, Type II Ca,
    analysis method, diaphragm idealization, two-stage status and the governing hazard."""
    s = cfg.get("seis") or {}
    portal = _cfs_is_portal(cfg)
    sdc, sdc_txt = _cfs_sdc_detail(cfg)
    SD1 = _fnum(s.get("SD1"))
    rows = [["Importance factor I<sub>e</sub>", _cn(_fnum(s.get("Ie"))) if s.get("Ie") is not None
             else "<b>not stated</b> (1.0 used by the analysis)"],
            ["Site Class (ASCE 7-22 Ch. 20)", _pt(cfg.get("site_class")) or "<b>not stated</b>"]]
    if s.get("SS") is not None or s.get("S1") is not None:
        rows.append(["Mapped S<sub>S</sub> / S<sub>1</sub>", "%s / %s" % (
            _cn(_fnum(s.get("SS"))) if s.get("SS") is not None else "not stated",
            _cn(_fnum(s.get("S1"))) if s.get("S1") is not None else "not stated")])
    rows.append(["Design spectral accelerations S<sub>DS</sub> / S<sub>D1</sub>", "%s g / %s" % (
        _cn(_fnum(s.get("SDS"))) if s.get("SDS") is not None else "<b>not stated</b>",
        ("%s g" % _cn(SD1)) if SD1 is not None else "<b>not stated</b>")])
    rows.append(["Seismic Design Category (Tables 11.6-1, 11.6-2)", sdc_txt])
    if sdc != "A":
        dirs = sorted((cfg.get("seis_by_dir") or {}).keys()) or [None]
        cells = []
        for d in dirs:
            R, Cd, Om0, note = _cfs_factors(cfg, d)
            cells.append("%sR = %s, C<sub>d</sub> = %s, &Omega;<sub>0</sub> = %s%s" % (
                ("%s: " % d) if d else "",
                _cn(R) if R is not None else "<b>not stated</b>",
                _cn(Cd) if Cd is not None else "<b>not stated</b>",
                _cn(Om0) if Om0 is not None else "<b>not stated</b>",
                (" (%s)" % note) if note else ""))
        rows.append(["Response coefficients (Table 12.2-1)", "; ".join(cells)])
        rho = pkg.get("rho", cfg.get("rho"))
        if rho is not None:
            rj = pkg.get("rho_justification") or (pkg.get("rho_basis") if "12.3.4.2" in str(pkg.get("rho_basis") or "")
                                                   else None)
            rows.append(["Redundancy factor &rho; (&sect;12.3.4)", "%s%s%s" % (
                _cn(_fnum(rho)) if _fnum(rho) is not None else _pt(rho),
                " &mdash; applied to the strength demands; not to drift (&sect;12.3.4.1)"
                if not portal else "",
                ("<br>Basis: %s" % (_cn(rj) if isinstance(rj, (dict, list)) else _pt(rj))) if rj else "")])
        hl = _cfs_height_limit(cfg)
        if hl:
            rows.append(["Height limit (Table 12.2-1)", hl])
    elf = pkg.get("elf") or {}
    e = (res or {}).get("elf") or {}
    if e.get("V") is not None and e.get("W") is not None:
        rows.append(["ELF (&sect;12.8)", "V = C<sub>s</sub>W = %s &times; %s = %s kip; T<sub>a</sub> = %s s, "
                     "T used = %s s, k = %s" % (_cn(e.get("Cs"), 4), _cn(e.get("W"), 1), _cn(e.get("V"), 1),
                                                _cn(e.get("Ta"), 3), _cn(e.get("T_used", e.get("Ta")), 3),
                                                _cn(e.get("k")))])
    elif elf:
        rows.append(["ELF (&sect;12.8)", "V = %s kip, W = %s kip, C<sub>s</sub> = %s, T<sub>a</sub> = %s s"
                     % (_cn(_fnum(elf.get("V_kip"))), _cn(_fnum(elf.get("W_kip"))),
                        _cn(_fnum(elf.get("Cs"))), _cn(_fnum(elf.get("Ta_s"))))])
    elif portal and (s.get("W_frame_kip") is not None or
                     _cfs_portal_seis(pkg, res).get("W_frame_kip") is not None):
        se_ = _cfs_portal_seis(pkg, res)
        Wf = _fnum(se_.get("W_frame_kip") if se_.get("W_frame_kip") is not None else s.get("W_frame_kip"))
        rows.append(["Seismic weight per frame (&sect;12.7.2)", "W = %s kip%s" % (
            _cn(Wf), ("<br>%s" % _pt(se_["W_frame_basis"])) if se_.get("W_frame_basis") else "")])
        if se_.get("V_kip") is not None and se_.get("Cs") is not None:
            rows.append(["ELF (&sect;12.8), per frame", "V = C<sub>s</sub>W = %s &times; %s = %s kip%s" % (
                _cn(_fnum(se_["Cs"]), 4), _cn(Wf), _cn(_fnum(se_["V_kip"])),
                ("; T = %s s" % _cn(_fnum(se_["T_s"]), 3)) if _fnum(se_.get("T_s")) else "")])
    elif portal and sdc not in (None, "A") and not (pkg.get("seismic") or (res or {}).get("seismic")):
        rows.append(["Seismic load on the frames", "<b>not evaluated by the frame analysis</b> (seismic weight "
                     "per frame not stated); the governing-hazard statement covers the comparison"])
    wbl = (elf or {}).get("weight_by_level_kip") or {}
    if isinstance(wbl, dict) and wbl:
        parts = []
        for k, bd in sorted(wbl.items(), key=lambda kv: int(kv[0])):
            if not isinstance(bd, dict):
                continue
            items = ", ".join("%s %s" % (_html.escape(str(t)), _num_or(v, "%.1f")) for t, v in bd.items()
                              if t not in ("items_1_3_5_6_share", "exception_a_applied"))
            extra = ""
            if bd.get("items_1_3_5_6_share"):
                extra = " (storage/equipment share %.0f%%%s)" % (
                    100 * bd.get("items_1_3_5_6_share", 0.0),
                    ", &sect;12.7.2 exception (a) applied" if bd.get("exception_a_applied") else "")
            parts.append("L%s: %s%s" % (_html.escape(str(k)), items, extra))
        rows.append(["seismic weight by level (&sect;12.7.2), kip", "; ".join(parts)])
    ebd = pkg.get("elf_by_direction") or {}
    if isinstance(ebd, dict) and ebd:
        rows.append(["ELF per direction (&sect;12.2.2)", "; ".join(
            "%s: %s, V = %s kip, R/C<sub>d</sub>/&Omega;<sub>0</sub> = %s/%s/%s" % (
                _html.escape(str(d)), _pt(v.get("system", "")), _num_or(v.get("V_kip", v.get("V")), "%.1f"),
                _cn(v.get("R")), _cn(v.get("Cd")), _cn(v.get("Om0"))) for d, v in ebd.items()
            if isinstance(v, dict))])
    if pkg.get("L_factor_basis"):
        rows.append(["companion live-load factor (&sect;2.3.1 / &sect;2.3.6)", _pt(pkg.get("L_factor_basis"))])
    per = pkg.get("period_rayleigh") or {}
    if isinstance(per, dict) and any(isinstance(v, dict) for v in per.values()):
        rows.append(["fundamental period", "; ".join(
            "%s: T<sub>Rayleigh</sub> = %s s, C<sub>u</sub>T<sub>a</sub> = %s s, T used = %s s" % (
                _html.escape(str(d)), _cn(_fnum(v.get("T_rayleigh_s"))), _cn(_fnum(v.get("CuTa_s"))),
                _cn(_fnum(v.get("T_used_s")))) for d, v in per.items() if isinstance(v, dict))])
    cda = pkg.get("capacity_design_applicability")
    if isinstance(cda, dict) and cda:
        rows.append(["S400 capacity design (B3.4 / A1.2.3)", "; ".join(
            "%s: %s" % (_html.escape(str(d)), "required (expected strength &Omega;<sub>E</sub>V<sub>n</sub>, "
                        "not to exceed the &Omega;<sub>0</sub>-level effect)" if v.get("required")
                        else "not required &mdash; %s" % _pt(v.get("basis", "")))
            for d, v in cda.items() if isinstance(v, dict))])
    elif not portal:
        rows.append(["S400 capacity design (B3.4 / A1.2.3)", _cfs_s400_status(cfg, pkg)])
    # drift limit and basis (from the analysis of each direction)
    dl = cfg.get("drift_limit")
    if dl is None:
        for d in pkg.get("drift_table") or []:
            if isinstance(d, dict) and isinstance(d.get("limit"), (int, float)):
                dl = d["limit"]
                break
    if dl is not None:
        rows.append(["drift limit (Table 12.12-1)", "&Delta;<sub>a</sub> = %s h<sub>sx</sub>" % _cn(_fnum(dl))])
    dlb = pkg.get("drift_limit_basis") or (_cfs_portal_seis(pkg, res).get("drift_limit_basis")
                                           if portal else None)
    if dlb:
        rows.append(["drift-limit basis", "; ".join("%s: %s" % (_html.escape(str(d)), _pt(_drift_basis_clean(b)))
                                                     for d, b in dlb.items())
                     if isinstance(dlb, dict) else _pt(_drift_basis_clean(dlb))])
    if not portal:
        meth = sorted({_DRIFT_METHOD.get(_cfs_dir_system(cfg, d)) or "the analysis model"
                       for d in ("X", "Y")})
        rows.append(["story-drift basis (&sect;12.8.6)",
                     "single-story wall deflection by %s, plus the rotation carried up from the stories "
                     "below (&sect;12.8.6.5), &times; C<sub>d</sub>/I<sub>e</sub>; &times; 1/(1&minus;&theta;) "
                     "where 0.10 &lt; &theta; &le; &theta;<sub>max</sub> (&sect;12.8.7)" % " / ".join(meth)])
    th = _find_theta(pkg)
    if th:
        rows.append(["P-&Delta; stability coefficient &theta; (&sect;12.8.7)",
                     "&theta;<sub>max,found</sub> = %.3f%s (%s)" % (
                         th[0], (" vs &theta;<sub>max</sub> = %s" % _cn(_fnum(th[1]))) if th[1] is not None else "",
                         _pt(th[2]))])
    ca = _find_key(pkg, ("Ca", "Ca_effective", "type_II_adjustment"))
    if ca is not None:
        rows.append(["Type II adjustment C<sub>a</sub>", _cn(ca) if not isinstance(ca, str) else _pt(ca)])
    if portal:
        rows.append(["analysis method", _CFS_TIER_TEXT["portal"]])
    else:
        rows.append(["analysis method", _CFS_TIER_TEXT["wall"] % (
            "semi-rigid diaphragm (coupled solve)" if cfg.get("diaphragm") == "semi-rigid"
            else "flexible-diaphragm tributary distribution with the 5% accidental shift")])
        db = pkg.get("diaphragm_basis")
        if isinstance(db, dict):
            rows.append(["diaphragm idealization (&sect;12.3.1)", ("%s%s" % (
                "" if db.get("flexible_ok", True) else "<b>flexible idealization NOT permitted</b> &mdash; ",
                _pt(_drift_basis_clean(db.get("note", ""))))) + "".join(
                "<br>&bull; %s" % _pt(_drift_basis_clean(w)) for w in db.get("warnings") or [])])
    ts = pkg.get("two_stage_framework")
    if isinstance(ts, dict):
        rows.append(["two-stage procedure (&sect;12.2.3.2)", "; ".join(
            "%s: %s" % (_html.escape(str(d)), _pt(v.get("status")))
            for d, v in (ts.get("by_direction") or {}).items())
            + "; reaction amplification %s" % _cn(_fnum(ts.get("amplification")))])
    rows.append(["governing lateral hazard", _cfs_governing_hazard(pkg)])
    return rows


def _cfs_portal_seis(pkg, res):
    """The portal frame's seismic block (W per frame and its 12.7.2 basis, V, Cs, drift)."""
    se = (pkg or {}).get("seismic") or (res or {}).get("seismic") or {}
    return se if isinstance(se, dict) else {}


def _drift_basis_clean(s):
    """Framework basis text without its declaration hints (they belong to the designer)."""
    s = str(s or "")
    s = s.replace("-- ASSUMED, not declared", "(assumed; not stated)")
    s = s.replace("assumed (declare True/False)", "assumed (not stated)")
    s = re.sub(r";?\s*cfg\[['\"][A-Za-z0-9_]+['\"]\]\s*NOT declared\s*->\s*", "; ", s)
    s = re.sub(r"\(\s*declare [^)]*\)", "", s)
    s = re.sub(r"\s*--\s*declare cfg\[[^\]]+\][^;)]*", "", s)
    s = re.sub(r"declare cfg\[[^\]]+\][^;)]*;?", "", s)
    return s


def _cfs_wind_rows(cfg, pkg):
    w = cfg.get("wind") or {}
    if not w:
        if isinstance(cfg.get("wind_basis"), str) and cfg["wind_basis"].strip():   # e.g. interior structure
            return [["Wind", _pt(cfg["wind_basis"])]]
        return [["Wind", "<b>not stated</b> (no wind parameters; wind not analysed)"]]
    wb = pkg.get("wind_basis") or {}
    try:
        import cfs_frame as _CF
        encl, _w = _CF.enclosure_of(cfg)
        gcpi = _CF.GCPI_BY_ENCLOSURE.get(encl)
    except Exception:
        encl, gcpi = (w.get("enclosure") or ("enclosed" if w.get("enclosed", True) else "partially_enclosed")), None
    declared = w.get("enclosure") is not None or "enclosed" in w
    rows = [["Basic wind speed V (Fig. 26.5-1)", "%s mph" % _cn(_fnum(w.get("V"))) if w.get("V") else "<b>not stated</b>"],
            ["Exposure category (&sect;26.7)", _pt(w.get("exposure")) or "<b>not stated</b> (C used by the analysis)"],
            ["Directionality factor K<sub>d</sub> (Table 26.6-1)",
             _cn(_fnum(w.get("Kd"))) if w.get("Kd") is not None else
             "0.85 (Table 26.6-1, MWFRS of buildings; applied in the pressure equation)"],
            ["Topographic factor K<sub>zt</sub> (&sect;26.8)",
             _cn(_fnum(w.get("Kzt"))) if w.get("Kzt") is not None else "not stated (1.0 used by the analysis)"],
            ["Ground elevation factor K<sub>e</sub> (&sect;26.9)",
             _cn(_fnum(w.get("Ke"))) if w.get("Ke") is not None else "not stated (1.0 used by the analysis)"],
            ["Enclosure classification (&sect;26.12)", "%s%s" % (
                _pt(str(encl).replace("_", " ")), "" if declared else " (not stated; assumed by the analysis)")],
            ["Internal pressure coefficient GC<sub>pi</sub> (Table 26.13-1)",
             ("&plusmn;%s" % _cn(gcpi)) if gcpi else ("0 (open building)" if encl == "open" else "not stated")]]
    if wb.get("qh_psf") is not None:
        _at = (" at h = %s" % _ft(wb.get("h_mean_ft"))) if wb.get("h_mean_ft") else ""
        if wb.get("qh_Kd_psf") is not None and wb.get("Kd") is not None:
            rows.append(["Velocity pressure q<sub>h</sub> (Eq. 26.10-1)", "%s psf%s; K<sub>d</sub>q<sub>h</sub> = %s psf "
                         "in the pressure equations (K<sub>d</sub> = %s)" % (
                             _cn(_fnum(wb.get("qh_psf"))), _at, _cn(_fnum(wb.get("qh_Kd_psf"))), _cn(_fnum(wb.get("Kd"))))])
        else:
            rows.append(["Velocity pressure q<sub>h</sub> (Eq. 26.10-1)", "%s psf%s" % (_cn(_fnum(wb.get("qh_psf"))), _at)])
    return rows


def _cfs_snow_rows(cfg, pkg):
    portal = _cfs_is_portal(cfg)
    pg, ce, ct, is_ = (cfg.get("snow_pg"), cfg.get("snow_ce"), cfg.get("snow_ct"), cfg.get("snow_is"))
    rows = [["Ground snow load p<sub>g</sub> (&sect;7.2)", ("%s psf" % _cn(_fnum(pg))) if pg is not None
             else "<b>not stated</b>"],
            ["Exposure factor C<sub>e</sub> (Table 7.3-1)", _cn(_fnum(ce)) if ce is not None else
             ("not stated (1.0 used by the analysis)" if portal else "<b>not stated</b>")],
            ["Thermal factor C<sub>t</sub> (Table 7.3-2)", _cn(_fnum(ct)) if ct is not None else
             ("not stated (1.0 used by the analysis)" if portal else "<b>not stated</b>")],
            ["Snow importance factor I<sub>s</sub>", _cn(_fnum(is_)) if is_ is not None else
             "not used: ASCE 7-22 maps a risk-targeted p<sub>g</sub>"]]
    if portal:
        if cfg.get("snow_ps") is not None:
            rows.append(["Design roof snow load", "p<sub>s</sub> = %s psf (stated)" % _cn(_fnum(cfg.get("snow_ps")))])
        elif pg is not None:
            pf = 0.7 * float(ce or 1.0) * float(ct or 1.0) * float(is_ or 1.0) * float(pg)
            rows.append(["Flat-roof snow load p<sub>f</sub> = 0.7C<sub>e</sub>C<sub>t</sub>p<sub>g</sub> (Eq. 7.3-1)",
                         "%s psf" % _cn(pf)])
        rows.append(["Unbalanced / partial loading", "included" if cfg.get("pattern_snow", True) else "not included"])
        for n in pkg.get("notes") or []:
            if "snow" in str(n).lower():
                rows.append(["Snow note", _pt(n)])
    else:
        rows.append(["Flat-roof snow load p<sub>f</sub> used", ("%s psf" % _cn(_fnum(cfg.get("snow"))))
                     if cfg.get("snow") is not None else "<b>not stated</b>"])
    return rows


def _cfs_gravity_rows(cfg, pkg):
    portal = _cfs_is_portal(cfg)

    def psf(k, label, ns="<b>not stated</b>"):
        v = cfg.get(k)
        txt = ("%s psf" % _cn(_fnum(v))) if _fnum(v) is not None else (_pt(v) if v else ns)
        if cfg.get(k + "_basis"):                       # e.g. Lr_basis: 4.8.2 reduction of Lo
            txt += " (%s)" % _pt(cfg[k + "_basis"])
        return [label, txt]
    if portal:
        rows = [psf("D_roof", "Roof dead load (superimposed)"),
                psf("collateral", "Collateral load"),
                psf("Lr", "Roof live load L<sub>r</sub> (Table 4.3-1)")]
        if cfg.get("clad") is not None:
            rows.append(psf("clad", "Wall cladding (in the seismic weight, &sect;12.7.2)"))
        fw = ((pkg.get("analysis_basis") or {}).get("frame_self_weight_kip"))
        if fw is not None:
            rows.append(["Frame self-weight", "%s kip per frame (included by the analysis)" % _cn(_fnum(fw))])
        if cfg.get("point_loads") or cfg.get("crane"):
            rows.append(["Point / crane loads", "as declared (Section 3)"])
        return rows
    rows = [psf("D_floor", "Floor dead load"), psf("D_roof", "Roof dead load"),
            psf("clad", "Exterior cladding (per unit wall area)"),
            psf("partition_psf", "Partitions (&sect;4.3.2, live load in Ch. 4; in W per &sect;12.7.2)"),
            psf("L_floor", "Floor live load L<sub>o</sub> (Table 4.3-1)"),
            psf("Lr", "Roof live load L<sub>r</sub> (Table 4.3-1)")]
    rows.append(psf("collateral", "Collateral load"))
    if cfg.get("storage_levels") or cfg.get("storage"):
        rows.append(["Storage live load", _cn(cfg.get("L_by_level") or cfg.get("storage"))])
    return rows


# ---- materials --------------------------------------------------------------------------------
_GRADE_FU = {33: 45, 37: 52, 40: 55, 50: 65, 55: 70}


def _cfs_text_pool(pkg, keys):
    acc = []
    for k in keys:
        v = (pkg or {}).get(k)
        if v:
            try:
                import consistency as _CC
                acc.append(" | ".join(str(x) for _kk, x in _CC._walk_agent(v) if isinstance(x, str)))
            except Exception:
                acc.append(json.dumps(v))
    return " | ".join(acc)


def _cfs_materials_rows(cfg, pkg):
    """Materials table: the declared materials (cfg/pkg 'materials') win; otherwise the
    specification column says 'not stated' and the last column lists what the schedules use
    (designators, grades, screw sizes, rod diameters with the ASTM A449 strength by diameter)."""
    import consistency as _CC
    decl = (cfg or {}).get("materials") or (pkg or {}).get("materials") or {}
    if isinstance(decl, list):
        rows = []
        for m in decl:
            if isinstance(m, dict):
                rows.append([_pt(m.get("item") or m.get("use")), _pt(m.get("spec") or m.get("specification")),
                             _pt(m.get("strength") or m.get("grade")) or "&mdash;", _pt(m.get("used") or "")])
        return rows
    decl = decl if isinstance(decl, dict) else {}
    sysname = _cfs_sys(cfg, pkg)
    frame_txt = _cfs_text_pool(pkg, ("studs", "members", "wall_lines", "collectors", "gravity_framing",
                                     "schedules", "connections"))
    desig = sorted(set(re.findall(r"(?<!\d)\d{3,4}[STUF]\d{3}-\d{2,3}\b", frame_txt + " " +
                                  str(cfg.get("col_section") or "") + " " + str(cfg.get("raf_section") or ""))))
    grades = sorted({int(g) for g in re.findall(r"\bGr(?:ade)?\.?\s*(33|37|40|50|55)\b|\b(?:33|50)\s*ksi\b",
                                                frame_txt, re.I) if g})
    rows = []

    def row(key, item, default_spec, strength, used):
        spec = decl.get(key)
        rows.append([item, _pt(spec) if spec else "<b>not stated</b>%s" % (
            " (%s)" % default_spec if default_spec else ""), strength or "&mdash;", used or "&mdash;"])
    gtxt = "; ".join("Grade %d: F<sub>y</sub> = %d ksi, F<sub>u</sub> = %d ksi" % (g, g, _GRADE_FU.get(g, 0))
                     for g in grades) if grades else ""
    row("framing", "Framing (studs, track, joists, built-up chords, frame members)",
        "ASTM A1003 structural grade expected", _pt(decl.get("framing_strength")) or gtxt,
        ", ".join(desig[:14]) + (" &hellip;" if len(desig) > 14 else ""))
    strap_txt = re.findall(r"\b\d(?:\.\d+)?\s*-?\s*in\.?\s*x\s*\d{2,3}\s*-?\s*mil[^|;,)]*", frame_txt, re.I)
    if sysname == "strap_braced" or strap_txt or (pkg.get("schedules") and "strap" in frame_txt.lower()):
        sfy = _fnum(cfg.get("strap_Fy_ksi"))
        row("straps", "Flat straps", "ASTM A1003 structural grade expected",
            ("F<sub>y</sub> = %s ksi, F<sub>u</sub> = %s ksi" % (_cn(sfy), _cn(_fnum(cfg.get("strap_Fu_ksi")) or
                                                                   float(_GRADE_FU.get(int(sfy), 0)))))
            if sfy else "", ", ".join(sorted(set(s.strip() for s in strap_txt))[:4]))
    sh_txt = " ".join(str(w.get("sheathing") or "") for w in pkg.get("wall_lines") or [] if isinstance(w, dict))
    if sysname == "wsp_shearwall" or re.search(r"\bosb\b|plywood", sh_txt, re.I):
        kinds = sorted(set(m.strip() for m in re.findall(r"\d+/\d+\s*-?\s*in\.?\s*(?:OSB|plywood)[^,;|]*",
                                                           sh_txt, re.I)))[:3]
        row("sheathing", "Wood structural panel sheathing", "OSB: DOC PS 2; plywood: DOC PS 1", "",
            ", ".join(kinds))
    if sysname == "steelsheet_wall" or re.search(r"steel sheet", sh_txt, re.I):
        row("steel_sheet", "Steel sheet sheathing", "ASTM A1003 expected", "", "")
    if sysname == "gypsum_wall" or re.search(r"gypsum|fiberboard", sh_txt, re.I):
        row("gypsum", "Gypsum board / fiberboard sheathing", "ASTM C1396 / C208", "", "")
    screws = sorted(set(m.group(0).replace(" ", " ") for m in
                        re.finditer(r"\bNo\.\s?(?:6|8|10|12|14)\b|#\s?(?:8|10|12|14)\b", frame_txt, re.I)))
    row("screws", "Screws", "self-drilling tapping screws: ASTM C1513", _pt(decl.get("screws_strength")),
        ", ".join(screws))
    rod_txt = _cfs_text_pool(pkg, ("holddowns", "connections"))
    if pkg.get("holddowns") or re.search(r"(?<!anchor )\brods?\b", rod_txt, re.I):
        spec = "ASTM A449" if re.search(r"a\s*449", rod_txt, re.I) else None
        ds = set()
        for h in pkg.get("holddowns") or []:
            if isinstance(h, dict):
                d = _CC.parse_rod_diameter(_CC._entry_text({k: v for k, v in h.items()
                                                            if k not in _CC.SEED_KEYS}))
                if d:
                    ds.add(round(d, 4))
        strg = ""
        if spec == "ASTM A449" and ds:
            strg = "; ".join("%s in.: F<sub>y</sub> = %g ksi, F<sub>u</sub> = %g ksi" % (
                _frac_in(d), *(_CC.a449_strength(d) or (0, 0))) for d in sorted(ds) if _CC.a449_strength(d))
        rows.append(["Threaded rods (continuous tie-down)", _pt(decl.get("rods")) if decl.get("rods") else
                     (spec + " (from the rod selections)" if spec else "<b>not stated</b>"),
                     strg or "&mdash;", ", ".join("%s in." % _frac_in(d) for d in sorted(ds)) or "&mdash;"])
    blob = _cfs_agent_blob(pkg)
    if re.search(r"\ba325\b|\ba490\b|\ba307\b|f3125|(?<!anchor )\bbolts?\b", blob):
        b = []
        if re.search(r"\ba325\b", blob):
            b.append("ASTM F3125 Grade A325")
        if re.search(r"\ba490\b", blob):
            b.append("ASTM F3125 Grade A490")
        if re.search(r"\ba307\b", blob):
            b.append("ASTM A307")
        rows.append(["Bolts", _pt(decl.get("bolts")) if decl.get("bolts") else (", ".join(b) or "<b>not stated</b>"),
                     "&mdash;", "&mdash;"])
    if re.search(r"f1554|anchor (?:bolt|rod)s?", blob):
        gr = sorted(set(re.findall(r"f1554\s*(?:gr(?:ade)?\.?\s*)?(36|55|105)", blob)))
        rows.append(["Anchor rods / anchor bolts", _pt(decl.get("anchor_rods")) if decl.get("anchor_rods") else
                     ("ASTM F1554" + (" Grade %s" % "/".join(gr) if gr else "") if "f1554" in blob
                      else "<b>not stated</b> (ASTM F1554)"), "&mdash;", "&mdash;"])
    if re.search(r"\bweld", blob):
        rows.append(["Welds", _pt(decl.get("welds")) if decl.get("welds") else "<b>not stated</b> (E60XX/E70XX, AWS D1.3)",
                     "&mdash;", "&mdash;"])
    return rows


def _frac_in(d):
    whole = int(d)
    frac = d - whole
    for den in (2, 4, 8, 16):
        num = round(frac * den)
        if abs(frac - num / den) < 1e-3:
            if num == 0:
                return "%d" % whole
            if num == den:
                return "%d" % (whole + 1)
            from math import gcd
            g = gcd(num, den)
            return ("%d-%d/%d" % (whole, num // g, den // g)) if whole else ("%d/%d" % (num // g, den // g))
    return _cn(d)


def _cfs_design_basis(cfg, res, pkg):
    h = ["<h2>1. Design basis</h2>",
         "<h3>1.1 Codes and standards</h3>", _table(["Standard", "Used for"], _cfs_codes_rows(cfg, pkg)),
         "<h3>1.2 Building, occupancy and risk</h3>", _table(["Item", "Value"], _cfs_building_rows(cfg, res, pkg)),
         "<h3>1.3 Seismic design and analysis basis</h3>", _table(["Item", "Value"], _cfs_basis_rows(cfg, res, pkg)),
         "<h3>1.4 Wind</h3>", _table(["Item", "Value"], _cfs_wind_rows(cfg, pkg)),
         "<h3>1.5 Snow</h3>", _table(["Item", "Value"], _cfs_snow_rows(cfg, pkg)),
         "<h3>1.6 Gravity loads</h3>", _table(["Load", "Value"], _cfs_gravity_rows(cfg, pkg)),
         "<h3>1.7 Materials</h3>", _table(["Item", "Specification", "Strength", "Used in this design"],
                                          _cfs_materials_rows(cfg, pkg)),
         "<p class='tnote'>&ldquo;Not stated&rdquo; marks an input the project criteria must supply; the "
         "report does not assume it.</p>"]
    return "".join(h)


def _cfs_title_block(name, cfg, res, pkg):
    portal = _cfs_is_portal(cfg)
    title = pkg.get("project_title") or cfg.get("project_title") or cfg.get("title") or name
    kind = "Cold-formed steel portal frame" if portal else "Cold-formed steel light-frame building"
    stds = [CFS_EDITION]
    if not portal or pkg.get("studs") or pkg.get("wall_lines"):
        stds.append("AISI S240-20")
    if not _cfs_s400_status(cfg, pkg).startswith("not"):
        stds.append("AISI S400-20")
    stds.append("ASCE/SEI 7-22 LRFD")
    h = ["<h1>%s &mdash; cold-formed steel structural design</h1>" % _pt(title),
         "<p><b>%s</b> &middot; %s &middot; report date %s</p>" % (kind, "; ".join(stds),
                                                                   datetime.date.today())]
    cr = _cfs_design_credit(pkg)
    if cr:
        h.append("<p class='credit'>%s</p>" % cr)
    return "".join(h)


def _cfs_combos_section(cfg, res, pkg):
    combos = [c for c in (pkg.get("combos") or []) if isinstance(c, dict)]
    h = "<h2>2. Load combinations (ASCE 7-22 &sect;2.3 LRFD)</h2>"
    if combos:
        h += _table(["Combination", "Use"], [[_pt(c.get("label")), _pt(c.get("role") or "strength")]
                                             for c in combos])
    if pkg.get("combos_note"):
        h += "<p class='tnote'>%s</p>" % _pt(pkg.get("combos_note"))
    return h


def _cfs_elf_selfcheck(cfg, res, pkg):
    """(html, matches) -- the template's hand check of Section 3 (C_s and V from S_DS, S_D1,
    R, I_e, T and W), plus the package's own spot check compared with the SAME W and V."""
    import consistency as _CC
    e = (res or {}).get("elf") or {}
    s = cfg.get("seis") or {}
    W, V, T = _fnum(e.get("W")), _fnum(e.get("V")), _fnum(e.get("T_used", e.get("Ta")))
    R, Ie = _fnum(e.get("R") or s.get("R")), _fnum(s.get("Ie")) or 1.0
    SDS, SD1, S1, TL = _fnum(s.get("SDS")), _fnum(s.get("SD1")), _fnum(s.get("S1")) or 0.0, _fnum(s.get("TL")) or 8.0
    if not (W and V and R and SDS is not None):
        return "", None
    rows = []
    cs_a = SDS / (R / Ie)
    txt = "C<sub>s</sub> = S<sub>DS</sub>/(R/I<sub>e</sub>) = %s/(%s/%s) = %.4f" % (_cn(SDS), _cn(R), _cn(Ie), cs_a)
    cs = cs_a
    if SD1 is not None and T:
        cs_b = SD1 / (T * R / Ie) if T <= TL else SD1 * TL / (T * T * R / Ie)
        txt += "; S<sub>D1</sub> limit = %.4f %s" % (cs_b, "governs" if cs_b < cs_a else "does not govern")
        cs = min(cs_a, cs_b)
    cmin = max(0.044 * SDS * Ie, 0.01)
    if S1 >= 0.6:
        cmin = max(cmin, 0.5 * S1 / (R / Ie))
    if cs < cmin:
        txt += "; minimum %.4f governs" % cmin
        cs = cmin
    v_hand = cs * W
    ok = abs(v_hand - V) <= 0.005 * V + 0.05
    rows.append(["Seismic response coefficient", txt, "%.4f" % cs])
    rows.append(["Base shear", "V = C<sub>s</sub>W = %.4f &times; %s kip" % (cs, _cn(W, 1)), "%s kip" % _cn(v_hand, 1)])
    rows.append(["Analysis value (Section 3)", "V = %s kip" % _cn(V, 1),
                 "<span class='pass'>matches</span>" if ok else "<span class='fail'>differs</span>"])
    sc = _CC.elf_spot_check_compare(pkg, W_ref=W, V_ref=V)
    if sc and (sc["W"] is not None or sc["V"] is not None):
        quoted = ", ".join(x for x in (("W = %s kip" % _cn(sc["W"], 1)) if sc["W"] is not None else "",
                                       ("V = %s kip" % _cn(sc["V"], 1)) if sc["V"] is not None else "") if x)
        rows.append(["Designer's ELF spot check", quoted,
                     "<span class='pass'>matches Section 3</span>" if not sc["diffs"] else
                     "<span class='fail'>does not match Section 3 (W = %s kip, V = %s kip) &mdash; "
                     "reconcile</span>" % (_cn(W, 1), _cn(V, 1))])
        ok = ok and not sc["diffs"]
    return ("<h4>Self-check (hand ELF)</h4>" + _table(["Quantity", "Calculation", "Result"], rows)), ok


def _cfs_wall_demands_section(cfg, res, pkg):
    e = res.get("elf") or {}
    h = ["<h2>3. Seismic and wind demands</h2>",
         "<h3>3.1 Equivalent lateral force procedure (ASCE 7-22 &sect;12.8)</h3>"]
    ebd = res.get("elf_by_dir") or {}
    rows = [["Seismic weight W (&sect;12.7.2)", "%s kip" % _cn(_fnum(e.get("W")), 1)],
            ["Approximate period T<sub>a</sub> = C<sub>t</sub>h<sub>n</sub><sup>x</sup> (&sect;12.8.2.1)",
             "%s s" % _cn(_fnum(e.get("Ta")), 3)],
            ["Period used T", "%s s" % _cn(_fnum(e.get("T_used", e.get("Ta"))), 3)],
            ["Seismic response coefficient C<sub>s</sub> (&sect;12.8.1.1)", _cn(_fnum(e.get("Cs")), 4)],
            ["Distribution exponent k (&sect;12.8.3)", _cn(_fnum(e.get("k")))],
            ["Base shear V = C<sub>s</sub>W (pure ELF, before &rho;)", "%s kip" % _cn(_fnum(e.get("V")), 1)]]
    if isinstance(ebd, dict) and len(ebd) > 1:
        vs = {d: _fnum((v or {}).get("V")) for d, v in ebd.items() if isinstance(v, dict)}
        if len({round(x or 0, 1) for x in vs.values()}) > 1:
            rows.append(["Base shear by direction", "; ".join("%s: %s kip" % (d, _cn(v, 1)) for d, v in sorted(vs.items()))])
    h.append(_table(["Quantity", "Value"], rows))
    sc, _ok = _cfs_elf_selfcheck(cfg, res, pkg)
    h.append(sc)
    fx = e.get("Fx") or {}
    H = [_fnum(x) for x in (cfg.get("heights_ft") or [])]
    wbl = ((pkg.get("elf") or {}).get("weight_by_level_kip") or {})
    if fx and all(x is not None for x in H):
        cum, z, vrows = 0.0, 0.0, []
        lev = sorted(fx, key=lambda k: int(k))
        Vst = {}
        acc = 0.0
        for k in reversed(lev):
            acc += float(fx[k])
            Vst[k] = acc
        for k in lev:
            z += H[int(k) - 1]
            bd = wbl.get(k) or wbl.get(str(k)) or {}
            wx = sum(float(v) for t, v in bd.items() if isinstance(v, (int, float)) and not isinstance(v, bool)
                     and t not in ("items_1_3_5_6_share",)) if isinstance(bd, dict) and bd else None
            vrows.append([_html.escape(str(k)), _cn(z), _cn(wx, 1) if wx else "&mdash;", _cn(float(fx[k]), 1),
                          _cn(Vst[k], 1)])
        h.append("<h4>Vertical distribution (&sect;12.8.3)</h4>" + _table(
            ["Level", "h<sub>x</sub> (ft)", "w<sub>x</sub> (kip)", "F<sub>x</sub> (kip)", "Story shear V<sub>x</sub> (kip)"],
            vrows))
    h.append("<h3>3.2 Distribution to the wall lines</h3><p>Line shears by the %s; pure ELF before &rho;. The "
             "strength demands in Section 4 carry &rho; = %s; story drift uses the unamplified forces "
             "(&sect;12.3.4.1).</p>" % ("semi-rigid diaphragm solve" if cfg.get("diaphragm") == "semi-rigid"
                                        else "flexible-diaphragm tributary rule with the 5% accidental shift",
                                        _cn(_fnum(pkg.get("rho"))) if pkg.get("rho") is not None else "1.0"))
    for dirn, dd in (res.get("directions") or {}).items():
        rows = []
        for lname, lr in sorted(dd["lines"].items()):
            for k in sorted(lr):
                r = lr[k]
                rows.append([_html.escape(str(lname)), _html.escape(str(k)), "%.1f" % r["V"], "%.0f" % r["v_unit_plf"]])
        h.append("<h4>%s direction</h4>" % _html.escape(str(dirn)) + _table(
            ["Line", "Story", "V (kip)", "v (plf)"], rows))
        for t in dd.get("transfers", []):
            h.append("<p class='tnote'><b>Transfer:</b> line %s is absent at story %s: %.1f kip to %s (%s) "
                     "&mdash; %s</p>" % (_pt(t["line"]), _pt(t["story"]), t["V_kip"], _pt(t["to"]),
                                         _pt(t.get("note")), _pt(t["basis"])))
    return "".join(h)


# ---- portal ------------------------------------------------------------------------------------
def _cfs_custom_model(cfg, pkg):
    """(True, description) when the frame was designed with a model other than the engine
    frame: a component job on an existing shell, or a package that marks a custom / hand model
    (pkg['design_model'] = {'type': 'custom'|'hand', 'description': ...}, or custom_model /
    hand_model / analysis_basis.custom_model)."""
    if str((cfg or {}).get("structure_kind")) == "component" or (pkg or {}).get("component_mode"):
        return True, ("existing frame: the frame shown is an outline of the existing shell for the figures "
                      "only; the new components are designed from the schedules below")
    for k in ("design_model", "custom_model", "hand_model", "analysis_model"):
        v = (pkg or {}).get(k)
        if isinstance(v, dict):
            t = str(v.get("type") or v.get("kind") or "").lower()
            if t in ("custom", "hand", "independent", "manual") or v.get("custom") is True:
                return True, str(v.get("description") or v.get("basis") or v.get("note") or "")
        elif isinstance(v, str) and re.search(r"custom|hand|independent|manual", v, re.I):
            return True, v
        elif v is True:
            return True, ""
    ab = (pkg or {}).get("analysis_basis") or {}
    if isinstance(ab, dict) and ab.get("custom_model"):
        cm = ab["custom_model"]
        return True, cm if isinstance(cm, str) else str((cm or {}).get("description", "")) if isinstance(cm, dict) else ""
    return False, ""


def _cfs_portal_seeds(name, cfg, res):
    """The engine's own (unfilled) portal package for the same run -- the reference that tells a
    designer's value from a simplified-engine-frame value."""
    try:
        import cfs_pipeline as _CP
        c = dict(cfg, base="fixed", structure_kind="portal", pdelta=False) \
            if cfg.get("structure_kind") == "component" else cfg
        return _CP.build_portal_package(name or "job", c, res)
    except Exception:
        return {}


def _same(a, b, tol=0.02):
    """True when two printed values agree within a RELATIVE tolerance (2 % default) -- an absolute 0.02
    would call every small ratio (theta, drift) 'the same'."""
    a, b = _fnum(a), _fnum(b)
    if a is None or b is None:
        return False
    return abs(a - b) <= max(1e-9, tol * max(abs(a), abs(b)))


def _cfs_portal_loads(cfg, res, pkg):
    """Section 3 of the portal report: the frame loads (wind pressures for both internal-pressure
    cases, seismic per frame, load cases); load values are in the design basis."""
    s = cfg.get("seis") or {}
    h = ["<h2>3. Frame loads</h2>"]
    rows = [["Frame tributary width", _ft(cfg.get("spacing_ft"))]]
    wb = pkg.get("wind_basis") or res.get("wind_basis") or {}
    if wb.get("basis"):
        rows.append(["MWFRS wind basis", _pt(wb.get("basis")) + (" (q<sub>h</sub> = %s psf)" % _cn(_fnum(wb.get("qh_psf")))
                                                                  if wb.get("qh_psf") is not None else "")])
    se = pkg.get("seismic") or res.get("seismic") or {}
    if s.get("W_frame_kip") is not None or se:
        R, Cd, Om0, note = _cfs_factors(cfg)
        sdc, _t = _cfs_sdc_detail(cfg)
        Wf = se.get("W_frame_kip") or s.get("W_frame_kip") or se.get("W_kip")
        rows.append(["Seismic (per frame)", "%sR = %s, C<sub>d</sub> = %s, &Omega;<sub>0</sub> = %s, S<sub>DS</sub> = %s%s" % (
            ("W<sub>frame</sub> = %s kip (&sect;12.7.2); " % _cn(_fnum(Wf))) if Wf else "",
            _cn(R) if R is not None else "<b>not stated</b>", _cn(Cd) if Cd is not None else "<b>not stated</b>",
            _cn(Om0) if Om0 is not None else "<b>not stated</b>", _cn(_fnum(s.get("SDS"))),
            (", SDC %s" % sdc) if sdc else "")])
    h.append(_table(["Load", "Basis / value"], rows))
    prow = []
    for case, d in (("+GC<sub>pi</sub>", wb), ("&minus;GC<sub>pi</sub>", wb.get("case_neg") or {})):
        if any(k in d for k in ("wall_wind", "wall_lee", "roof_wind", "roof_lee")):
            prow.append([case] + [_num_or(d.get(k), "%.2f") for k in ("wall_wind", "wall_lee", "roof_wind", "roof_lee")])
    wcases = [c for c in (wb.get("cases") or []) if isinstance(c, dict)]
    if wcases and (wb.get("source") == "user" or str(wb.get("enclosure") or "").lower() == "open"):
        crow = []
        for c in wcases:
            w_ = c.get("walls") or {}
            rf = c.get("roof")
            if isinstance(rf, dict) and rf:
                rtxt = "left %s / right %s" % (_num_or(rf.get("raf_L"), "%.2f"), _num_or(rf.get("raf_R"), "%.2f"))
            elif isinstance(rf, (list, tuple)) and rf:
                rtxt = "; ".join("%s&ndash;%s ft: %s" % (_cn(z[0]), _cn(z[1]), _num_or(z[2], "%.2f"))
                                 for z in rf if isinstance(z, (list, tuple)) and len(z) == 3)
            else:
                rtxt = "&mdash;"
            crow.append([_pt(c.get("name")), _pt(c.get("desc") or ""),
                         _num_or(w_.get("col_L"), "%.2f") if w_ else "&mdash;",
                         _num_or(w_.get("col_R"), "%.2f") if w_ else "&mdash;", rtxt])
        h.append("<h4>MWFRS wind cases, psf (+ toward the surface; free-roof values are net pressures)</h4>"
                 + _table(["Case", "Description", "Left wall / column", "Right wall / column", "Roof"], crow))
        prow = []
    if prow:
        h.append("<h4>MWFRS surface pressures, psf (+ toward the surface)</h4>"
                 + _table(["Case", "Windward wall", "Leeward wall", "Windward roof", "Leeward roof"], prow))
    if pkg.get("torsion_companion"):
        rows = [[_pt(t.get("member")), _pt(t.get("designator")), _num_or(t.get("e0_in"), "%.2f"),
                 _num_or(t.get("Lb_in"), "%.0f"), _num_or(t.get("T_seg_kipin"), "%.1f"),
                 _num_or(t.get("B_seed_kipin2"), "%.0f")] for t in pkg["torsion_companion"] if isinstance(t, dict)]
        h.append("<h4>Single-channel torsion (analytic estimate, not a thin-walled analysis)</h4>"
                 + _table(["Member", "Section", "e<sub>0</sub> (in.)", "L<sub>b</sub> (in.)", "T (kip-in.)",
                           "B (kip-in.&sup2;)"], rows))
    return "".join(h)


def _cfs_member_demands(m, sm, custom):
    """(P, V, M) cells of a frame member: the package's design values; in a custom-model job a
    value identical to the simplified engine frame is replaced by a dash."""
    out = []
    for keys in (("P_design_kip", "P_kip"), ("V_design_kip", "V_kip"), ("M_design_kipin", "M_kipin")):
        v = next((m.get(k) for k in keys if m.get(k) is not None), None)
        eng = (not any(m.get(k) is not None for k in keys[:1])) and sm and _same(v, sm.get(keys[1]))
        out.append((v, bool(eng)))
    cells = []
    allV0 = False
    for i, (v, eng) in enumerate(out):
        if custom and eng:
            cells.append("n.r.")
        else:
            cells.append(_num_or(v, "%.1f" if i < 2 else "%.0f"))
    return cells, [eng for _v, eng in out]


def _cfs_portal_design_section(pkg, cfg=None, res=None, name=None):
    """Section 4 of the portal report: frame members, knee/apex connections, base anchorage, the
    purlin/girt/strap schedules and the serviceability record. In a custom / hand-model job the
    engine's skeleton values are not printed beside the design values."""
    if not isinstance(pkg, dict):
        return ""
    cfg = cfg or {}
    custom, cdesc = _cfs_custom_model(cfg, pkg)
    seeds = _cfs_portal_seeds(name, cfg, res) if (res and cfg) else {}
    seedm = {m.get("id"): m for m in seeds.get("members") or []}
    seedc = {c.get("id"): c for c in seeds.get("connections") or []}
    seeda = {a.get("id"): a for a in seeds.get("anchorage") or []}
    seedd = {d.get("check"): d for d in seeds.get("drift_table") or [] if isinstance(d, dict)}
    h = ["<h2>4. Frame design</h2>"]
    if custom:
        h.append("<p><b>Design model:</b> %s. Member, connection and base demands below are the design "
                 "model's values. The simplified engine frame (used for the 3D model view and load "
                 "bookkeeping) is not used for design; its values are not reported here (n.r. = not "
                 "reported by the design model).</p>"
                 % (_pt(cdesc) or "custom frame model"))
    h.append(_cfs_sample_calcs(pkg))
    mems = [m for m in pkg.get("members") or [] if isinstance(m, dict)]
    if mems:
        refs = _Refs()
        rows = []
        flags = []
        for m in mems:
            sm = seedm.get(m.get("id")) or {}
            cells, engs = _cfs_member_demands(m, sm, custom)
            flags.append(engs)
            if m.get("waived"):
                rows.append([_pt(m.get("id")), _pt(m.get("section")), _pt(m.get("governing_combo")),
                             *cells, _waived_cell(m), _cdc(m.get("DC")) if m.get("DC") is not None else "&mdash;",
                             refs.ref(m.get("cited"))])
                continue
            rows.append([_pt(m.get("id")), _pt(m.get("section")), _pt(m.get("governing_combo")), *cells,
                         _pt(m.get("capacity")) or "<b>PENDING</b>", _cdc_e(m), refs.ref(m.get("cited"))])
        head = ["Member", "Section", "Governing combination", "P (kip)", "V (kip)", "M (kip-in.)",
                "Design strength", "D/C", "Ref."]
        # every member at V = 0 in a custom model: shear was not reported by the design model
        if custom and all(_fnum(m.get("V_design_kip", m.get("V_kip"))) in (None, 0.0) for m in mems):
            for r in rows:
                r[4] = "not reported"
        h.append("<h3>Frame members</h3><p class='tnote'>P, V and M are the governing (maximum |M|) "
                 "demand set with its paired forces; every demand pair is checked in the limit-state "
                 "breakdown.</p>" + _ref_table(head, rows, refs))
        for m in mems:
            checks = [c for c in m.get("checks") or [] if isinstance(c, dict)]
            if checks:
                crow = [[_pt(c.get("limit_state", c.get("name", "check"))), *_check_dem_cap(c),
                         _cdc(c.get("DC")), _pt(c.get("note", ""))] for c in checks]
                h.append("<h4>%s &mdash; limit-state breakdown</h4>" % _pt(m.get("id"))
                         + _table(["Limit state", "Demand", "Strength", "D/C", "Note"], crow))
            if m.get("limit_state"):
                h.append("<p class='tnote'><b>%s basis:</b> %s</p>" % (_pt(m.get("id")), _pt(m["limit_state"])))
    conns = [c for c in pkg.get("connections") or [] if isinstance(c, dict)]
    if conns:
        h.append(_cfs_connections_table(pkg, "Connections &mdash; knee (column-rafter) and ridge (apex)",
                                        cfg, custom=custom, seeds=seedc))
    anch = [a for a in pkg.get("anchorage") or [] if isinstance(a, dict)]
    if anch:
        refs = _Refs()
        rows = []
        for a in anch:
            sa = seeda.get(a.get("id")) or {}

            def val(dk, sk, fmt="%.1f"):
                v = a.get(dk) if a.get(dk) is not None else a.get(sk)
                if custom and a.get(dk) is None and sa and _same(v, sa.get(sk)):
                    return "n.r."
                return _num_or(v, fmt)
            up = val("T_design_kip", "T_net_uplift_kip")
            combo = a.get("uplift_combo") if not (custom and a.get("T_design_kip") is None and
                                                 _same(a.get("T_net_uplift_kip"), sa.get("T_net_uplift_kip"))) else None
            cells = [val("V_design_kip", "V_base_kip"), up + ((" (%s)" % _pt(combo)) if combo else ""),
                     val("M_design_kipin", "M_base_kipin", "%.0f")]
            if a.get("waived"):
                rows.append([_pt(a.get("id")), _waived_cell(a), *cells, "", "",
                             _cdc(a.get("DC")) if a.get("DC") is not None else "&mdash;", refs.ref(a.get("cited"))])
                continue
            rows.append([_pt(a.get("id")), _pt(a.get("selection")) or "<b>PENDING</b>", *cells,
                         _pt(a.get("limit_state")) or "<b>PENDING</b>", _pt(a.get("capacity")) or "<b>PENDING</b>",
                         _cdc_e(a), refs.ref(a.get("cited"))])
        h.append("<h3>Base anchorage</h3><p>Net uplift (0.9D+1.0W) is a required case; concrete "
                 "embedment is designed to ACI 318-19 Ch. 17 with the interface forces below.</p>"
                 + _ref_table(["Base", "Selection", "V (kip)", "Net uplift T (kip)", "M<sub>base</sub> (kip-in.)",
                               "Limit state(s)", "Design strength", "D/C", "Ref."], rows, refs))
    for s in pkg.get("schedules") or []:
        if not isinstance(s, dict):
            continue
        h.append(_cfs_schedule_table(s))
    h.append(_cfs_serviceability_table(pkg, custom, seedd))
    return "".join(x for x in h if x)


def _cfs_schedule_table(s):
    rows = s.get("rows") or []
    title = str(s.get("schedule") or s.get("id", "")).replace("sched-", "").replace("_", " ").capitalize()
    h = ["<h3>%s schedule</h3>" % _pt(title)]
    if s.get("waived"):
        h.append("<p>%s</p>" % _waived_cell(s))
    if rows and isinstance(rows[0], dict):
        refs = _Refs()
        keys = []
        for r in rows:
            for k in r:
                if k not in keys and k not in ("cited",):
                    keys.append(k)
        cited = any(isinstance(r, dict) and r.get("cited") for r in rows)
        body = [[(_cdc(r.get(k)) if k == "DC" else _cn(r.get(k)) if not isinstance(r.get(k), str)
                  else _pt(r.get(k))) for k in keys] + ([refs.ref(r.get("cited"))] if cited else [])
                for r in rows]
        head = [_hk(k) for k in keys] + (["Ref."] if cited else [])
        h.append(_ref_table(head, body, refs) if cited else _table(head, body))
    meta = []
    if s.get("limit_state"):
        meta.append("<b>Limit state:</b> %s" % _pt(s["limit_state"]))
    if s.get("capacity") and s.get("capacity") != "see rows":
        meta.append("<b>Design strength:</b> %s" % _pt(s["capacity"]))
    if _fnum(s.get("DC")) is not None:
        meta.append("<b>Worst D/C:</b> %.2f" % s["DC"])
    if s.get("cited"):
        meta.append("<b>Reference:</b> %s" % _pt(s["cited"]))
    if s.get("note") and not str(s.get("note")).lstrip().upper().startswith("AGENT"):
        meta.append("%s" % _pt(s["note"]))
    if meta:
        h.append("<p class='tnote'>" + " &middot; ".join(meta) + "</p>")
    return "".join(h) if (rows or meta or s.get("waived")) else ""


def _cfs_serviceability_table(pkg, custom=False, seedd=None):
    dt = [d for d in pkg.get("drift_table") or [] if isinstance(d, dict) and d.get("check")]
    if not dt:
        return ""
    rows = []
    for d in dt:
        r = _cfs_drift_rows([d], extended=False)[0]
        sd = (seedd or {}).get(d.get("check")) or {}
        v = d.get("value_in", d.get("value"))
        if custom and sd and _same(v, sd.get("value_in", sd.get("value"))):
            r[1] = "%s <i>(simplified engine frame, not used for design)</i>" % r[1]
        r[0] = _pt(str(d.get("check")).replace("_", " "))
        rows.append(r)
    return ("<h3>Serviceability and seismic drift</h3>"
            + _table(["Check", "Value", "Criterion / limit", "D/C", "Status"], rows))


def _cfs_connections_table(pkg, title="Connection design", cfg=None, custom=False, seeds=None):
    """Connection designs: selection, the demand the D/C uses, limit states, strength, D/C. Shear
    anchorage rows also print the capacity-design reference demand beside the ELF value; long
    references are printed once under the table. In a custom-model job the engine joint moment is
    never printed as the demand."""
    import consistency as _CC
    conns = [c for c in (pkg or {}).get("connections") or [] if isinstance(c, dict)]
    if not conns:
        return ""
    cdu = _CC.capacity_design_unit_shear(pkg) if _cfs_capacity_design_required(cfg or {}) else {}
    v_elf = max((a for a, _b in cdu.values()), default=None)
    v_cd = max((b for _a, b in cdu.values()), default=None)
    tcd = max((_fnum(h.get("T_cd_seed_kip")) or 0.0 for h in pkg.get("holddowns") or []
               if isinstance(h, dict)), default=0.0)
    refs = _Refs()
    rows, any_ref = [], False
    for c in conns:
        cid = _pt(c.get("id", c.get("joint", "")))
        dem = c.get("demand")
        if dem is None:
            for k in ("M_design_kipin", "T_design_kip", "V_design_kip", "P_design_kip", "demand_kip"):
                if c.get(k) is not None:
                    dem = "%s = %s" % (_hk(k), _cn(c.get(k)))
                    break
        if dem is None and c.get("M_transfer_kipin") is not None:
            sc = (seeds or {}).get(c.get("id")) or {}
            if not (custom and sc and _same(c.get("M_transfer_kipin"), sc.get("M_transfer_kipin"))):
                dem = "M = %s kip-in. (%s)" % (_cn(_fnum(c.get("M_transfer_kipin"))),
                                               "both signs; analysis envelope" if not custom else "design model")
        head = " ".join(str(c.get(k) or "") for k in ("id", "type", "joint", "selection", "description"))
        refd = "&mdash;"
        if re.search(r"anchor|sill|track[- ]to|wall[- ]to|shear transfer", head, re.I) and \
                not re.search(r"uplift|clip|strap|rod", head, re.I) and v_cd:
            refd = "ELF&middot;&rho; &le; %s plf; capacity design &le; %s plf" % (_cn(v_elf, 0), _cn(v_cd, 0))
            any_ref = True
        elif re.search(r"\brod\b|bearing plate|take-up", head, re.I) and tcd:
            refd = "T<sub>cd</sub> &le; %s kip" % _cn(tcd, 1)
            any_ref = True
        if c.get("waived"):
            rows.append([cid, _waived_cell(c), "", refd, "", "", _cdc(c.get("DC")) if c.get("DC") is not None
                         else "&mdash;", refs.ref(c.get("cited"))])
            continue
        rows.append([cid, _pt(c.get("selection") or c.get("description")) or "&mdash;",
                     (_cn(dem) if not isinstance(dem, str) else _pt(dem)) if dem is not None else "&mdash;",
                     refd, _pt(c.get("limit_state")) or "<b>PENDING</b>",
                     _pt(c.get("capacity")) if not isinstance(c.get("capacity"), (int, float)) else _cn(c.get("capacity")),
                     _cdc_e(c), refs.ref(c.get("cited"))])
    head = ["Item", "Selection", "Design demand", "Reference demands", "Limit state(s)", "Design strength",
            "D/C", "Ref."]
    if not any_ref:
        head.pop(3)
        rows = [r[:3] + r[4:] for r in rows]
    intro = ("<p class='tnote'>Reference demands: the &rho;-ELF unit shear and the capacity-design "
             "unit shear of the wall lines at the base story (S400 B3.4: expected strength "
             "&Omega;<sub>E</sub>V<sub>n</sub>, not to exceed the &Omega;<sub>0</sub>-level effect) for "
             "shear anchorage; the largest capacity-design rod tension T<sub>cd</sub> for rod "
             "hardware. The design demand is the value the D/C is computed on.</p>" if any_ref else "")
    return "<h3>%s</h3>%s" % (title, intro) + _ref_table(head, rows, refs)


def _cfs_sample_calcs(pkg):
    """Sample calculations from pkg['sample_calcs'] (one wall, one stud, one rod, one connection
    in US calc-package practice): a list or dict of blocks; a block is a string, a list of steps,
    or {title, item, steps:[{step|description, expression|equation, value|result, ref}], result, ref}."""
    sc = (pkg or {}).get("sample_calcs") or (pkg or {}).get("sample_calculations")
    if not sc:
        return ""
    blocks = list(sc.items()) if isinstance(sc, dict) else [(None, b) for b in sc] if isinstance(sc, list) else []
    h = ["<h3>Sample calculations</h3><p class='tnote'>Representative items worked step by step; "
         "the schedules apply the same method to every line, story and member.</p>"]
    for key, b in blocks:
        if isinstance(b, str):
            h.append("<h4>%s</h4><p>%s</p>" % (_pt(key or "Calculation"), _pt(b)))
            continue
        if isinstance(b, list):
            b = {"steps": b}
        if not isinstance(b, dict):
            continue
        title = b.get("title") or key or b.get("item") or "Calculation"
        steps = b.get("steps") or b.get("calc") or []
        rows = []
        for s in steps:
            if isinstance(s, str):
                rows.append(("", _pt(s), "", ""))
            elif isinstance(s, dict):
                rows.append((_pt(s.get("step") or s.get("quantity") or s.get("description") or ""),
                             _pt(s.get("expression") or s.get("equation") or s.get("calc") or ""),
                             _cn(s.get("value") if s.get("value") is not None else s.get("result")),
                             _pt(s.get("ref") or s.get("cited") or s.get("reference") or "")))
        sub = []
        if b.get("item"):
            sub.append("item %s" % _pt(b["item"]))
        if b.get("result"):
            sub.append("<b>result:</b> %s" % (_pt(b["result"]) if isinstance(b["result"], str) else _cn(b["result"])))
        h.append(_calc(_pt(title), rows, " &middot; ".join(sub)))
        if b.get("ref") or b.get("cited"):
            h.append("<p class='tnote'><b>Reference:</b> %s</p>" % _pt(b.get("ref") or b.get("cited")))
    return "".join(h)


# ---- capacity design / detailing (Section 5) ----------------------------------------------------
_S400_ITEMS = {
    "wsp_shearwall": ("CFS shear walls sheathed with wood structural panels (AISI S400-20 E1)", [
        ("wall", "Wall shear strength", "Table E1.3-1 (seismic and other in-plane loads), &phi;<sub>v</sub> = 0.60; "
         "aspect-ratio (2w/h) reduction where h/w &gt; 2"),
        ("typeii", "Type II (perforated) walls", "adjustment factor C<sub>a</sub>; hold-downs at the wall ends "
         "plus distributed track anchorage (E1.4.2.2)"),
        ("chord", "Chord studs, hold-downs and anchorage", "capacity design from the expected wall strength "
         "&Omega;<sub>E</sub>V<sub>n</sub>, not to exceed the &Omega;<sub>0</sub>-level effect (E1.4.1.2, B3.4)"),
        ("collector", "Collectors", "expected wall strength (&Omega;<sub>0</sub> per ASCE 7-22 &sect;12.10.2.1)"),
        ("holddown", "Hold-down / rod chain", "cumulative overturning tension per line; rod elongation in the drift")]),
    "steelsheet_wall": ("CFS shear walls sheathed with steel sheet (AISI S400-20 E2)", [
        ("wall", "Wall shear strength", "Table E2.3-1 or the effective-strip method, &phi;<sub>v</sub> = 0.60; "
         "aspect-ratio reduction"),
        ("typeii", "Type II (perforated) walls", "adjustment factor C<sub>a</sub>; end hold-downs plus distributed "
         "track anchorage"),
        ("chord", "Chord studs, hold-downs and anchorage", "capacity design from the expected wall strength (E2.4.1.2, B3.4)"),
        ("collector", "Collectors", "expected wall strength (&Omega;<sub>0</sub> per &sect;12.10.2.1)"),
        ("holddown", "Hold-down / rod chain", "cumulative tension; rod elongation in the drift")]),
    "strap_braced": ("CFS strap-braced walls (AISI S400-20 E3)", [
        ("ductility", "Strap ductility", "R<sub>t</sub>A<sub>n</sub>F<sub>u</sub> &ge; R<sub>y</sub>A<sub>g</sub>F<sub>y</sub> "
         "(yield before net-section rupture)"),
        ("strapconn", "Strap connections", "designed for the expected strap strength R<sub>y</sub>F<sub>y</sub>A<sub>g</sub>"),
        ("chord", "Chord studs", "expected strap strength resolved into the chords"),
        ("holddown", "Anchorage and hold-downs", "expected strap strength into the hold-downs / rods and the foundation")]),
    "gypsum_wall": ("CFS walls sheathed with gypsum board or fiberboard (AISI S400-20 E6)", [
        ("wall", "Wall shear strength", "Table E6.3-1 (seismic); h:w &le; 2:1 gypsum, 1:1 fiberboard"),
        ("holddown", "Chords, hold-downs and anchorage", "capacity design with &Omega;<sub>E</sub> = 1.5 (E6.3.3, E6.4.1.2)")]),
    "sbmf": ("CFS special bolted moment frames (AISI S400-20 E4)", [
        ("frame", "Expected connection shear", "bolt slip and bearing at the design drift (E4.3.3)"),
        ("frame", "Columns and connections", "capacity-protected for the expected beam strength"),
        ("height", "Height limit", "35 ft (Table 12.2-1)")]),
}


_WIND_WALL_BASIS = {
    "wsp_shearwall": "AISI S400-20 Table E1.3-1 applies to seismic and other in-plane loads (one set of "
                     "nominal strengths), &phi;<sub>v</sub> = 0.60 (E1.3.2); alternatively AISI S240-20 B5.2.2.3.3",
    "steelsheet_wall": "AISI S400-20 Table E2.3-1 / E2.3.1.1.1 applies to seismic and other in-plane loads, "
                       "&phi;<sub>v</sub> = 0.60 (E2.3.2); alternatively AISI S240-20 B5.2.2.3.2",
    "gypsum_wall": "AISI S240-20 B5.2.2.3.4, Table B5.2.2.3-3 (gypsum) / B5.2.2.3.5 (fiberboard), "
                   "&phi;<sub>v</sub> = 0.65 (B5.2.3); S400 Table E6.3-1 is for seismic loads only",
    "strap_braced": "strap tension yielding T<sub>n</sub> = A<sub>g</sub>F<sub>y</sub> (AISI S400-20 E3.3.1, "
                    "AISI S100-16 D2) with the resistance factor of S400 E3.3.2 / S240 B5.3",
}


_SYM = {"pu": "P<sub>u</sub>", "pr": "P<sub>r</sub>", "mu": "M<sub>u</sub>", "mr": "M<sub>r</sub>",
        "vu": "V<sub>u</sub>", "vr": "V<sub>r</sub>", "tu": "T<sub>u</sub>", "tr": "T<sub>r</sub>",
        "mux": "M<sub>ux</sub>", "muy": "M<sub>uy</sub>", "tdesign": "T", "pdesign": "P", "mdesign": "M",
        "vdesign": "V", "phipn": "&phi;P<sub>n</sub>", "phimn": "&phi;M<sub>n</sub>",
        "phivn": "&phi;V<sub>n</sub>", "phitn": "&phi;T<sub>n</sub>", "phirn": "&phi;R<sub>n</sub>",
        "phipne": "&phi;P<sub>ne</sub>", "phipnd": "&phi;P<sub>nd</sub>", "phipnl": "&phi;P<sub>nl</sub>",
        "phimnd": "&phi;M<sub>nd</sub>", "phimne": "&phi;M<sub>ne</sub>", "phimnl": "&phi;M<sub>nl</sub>",
        "pc": "P<sub>c</sub>"}


def _check_dem_cap(c):
    """(demand cell, strength cell) of one limit-state check: its explicit demand / capacity
    fields, else every demand-type and strength-type key it carries (P_u, phiP_n, ...)."""
    import consistency as _CC

    def coll(keys):
        out = []
        for k, v in c.items():
            nk = _CC._norm_key(k)
            if nk in keys and _fnum(v) is not None:
                unit = next((u for suf, u in _HK_UNITS if str(k).lower().endswith(suf)), "")
                out.append("%s = %s%s" % (_SYM.get(nk, _hk(k)), _cn(_fnum(v)), (" " + unit) if unit else ""))
        return "; ".join(out) or "&mdash;"

    def cell(v, keys):
        if v is None:
            return coll(keys)
        return _pt(v) if isinstance(v, str) else _cn(v)
    return (cell(c.get("demand_kipin", c.get("demand_kip", c.get("demand"))), _CC.DEMAND_KEYS),
            cell(c.get("capacity_kipin", c.get("capacity_kip", c.get("capacity"))), _CC.CAPACITY_KEYS | {"pc"}))


def _cfs_has_type_ii(cfg, pkg):
    if (cfg or {}).get("type_ii"):
        return True
    if any(isinstance(h, dict) and h.get("type_ii") for h in (pkg or {}).get("holddowns") or []):
        return True
    return any(isinstance(w, dict) and re.search(r"type\s*ii|perforated", str(w.get("wall_type") or ""), re.I)
               for w in (pkg or {}).get("wall_lines") or [])


def _cfs_item_status(key, cfg, pkg):
    """Status html of one S400 check: where it is rendered and its worst D/C; 'not shown in this
    package' (bold) for a required check without evidence; None when the check does not apply
    to this building (the row is then omitted)."""
    import consistency as _CC
    NS = "<b>not shown in this package</b>"

    def worst(lst, filt=lambda e: True):
        ds = [_CC.worst_dc(e) for e in lst if isinstance(e, dict) and filt(e)]
        ds = [d for d in ds if d is not None]
        return max(ds) if ds else None
    W = pkg.get("wall_lines") or []
    if key == "wall":
        d = worst(W)
        return ("shear-wall schedule, %d slots, worst D/C %.2f" % (len(W), d)) if d is not None else NS
    if key == "typeii":
        return "Type II table (Section 4)" if _cfs_has_type_ii(cfg, pkg) else None
    if key == "chord":
        st = [s_ for s_ in pkg.get("studs") or [] if "chord" in str((s_ or {}).get("id", "")).lower()]
        dh = worst(pkg.get("holddowns") or [])
        dc = worst(st)
        da = worst([a for a in pkg.get("anchorage") or [] if isinstance(a, dict) and a.get("line") is not None])
        parts = []
        if dc is not None:
            parts.append("chord studs D/C %.2f" % dc)
        if dh is not None:
            parts.append("hold-downs D/C %.2f" % dh)
        if da is not None:
            parts.append("anchorage D/C %.2f" % da)
        return ", ".join(parts) or NS
    if key == "holddown":
        d = worst(pkg.get("holddowns") or [])
        return ("hold-down / rod schedule, worst D/C %.2f" % d) if d is not None else NS
    if key == "collector":
        if not pkg.get("collectors"):
            return "no collector lines in this building"
        d = worst(pkg.get("collectors") or [])
        return ("collector schedule, worst D/C %.2f" % d) if d is not None else NS
    if key == "ductility":
        sd = (pkg.get("capacity_design") or {}).get("strap_ductility")
        if isinstance(sd, dict) and sd.get("ok") is not None:
            return "PASS" if sd.get("ok") else "<b>FAIL</b>"
        return "<b>not evaluated</b>"
    if key == "strapconn":
        d = worst(pkg.get("connections") or [], lambda e: "strap" in json.dumps(e).lower())
        return ("connection table, worst D/C %.2f" % d) if d is not None else NS
    if key == "frame":
        d = worst(pkg.get("members") or []) if pkg.get("members") else None
        return ("frame design, worst D/C %.2f" % d) if d is not None else NS
    if key == "height":
        return _cfs_height_limit(cfg)
    return None


def _cfs_capacity_chapter(cfg, pkg):
    """Section 5: seismic detailing and capacity design -- only the checks that apply to the
    system, each with the place it is shown; the R <= 3 statement only where R <= 3."""
    s = cfg.get("seis") or {}
    sysname = _cfs_sys(cfg, pkg)
    sdc, _t = _cfs_sdc_detail(cfg)
    R = _fnum(s.get("R"))
    h = ["<h2>5. Seismic detailing and capacity design</h2>"]
    det = pkg.get("detailing")
    if isinstance(det, dict) and det.get("checks"):
        h.append("<p>%s</p>" % _pt(det.get("basis", "")))
        h.append(_table(["Required check", "Status / basis"],
                        [[_pt(c.get("check", "")), _pt(c.get("status", ""))] for c in det.get("checks", [])
                         if isinstance(c, dict)]))
    if sdc == "A":
        h.append("<p>SDC A: ASCE 7-22 &sect;11.7 applies (the &sect;1.4 minimum lateral force); the AISI S400 "
                 "seismic provisions are not used.</p>")
        return "".join(h)
    req = _cfs_capacity_design_required(cfg)
    if sysname in _S400_ITEMS and (req or sysname != "not_detailed"):
        label, items = _S400_ITEMS[sysname]
        rows = []
        for key, nm, basis in items:
            st = _cfs_item_status(key, cfg, pkg)
            if st is None:
                continue
            if not req and key in ("chord", "collector", "strapconn"):
                basis = "S400 capacity design not required (A1.2.3); designed for the analysis forces"
            rows.append([nm, basis, st])
        h.append("<p>%s, R = %s%s. %s</p>" % (label, _cn(R), (", SDC %s" % sdc) if sdc else "",
                                             "Capacity design is required (S400 B3.4): elements outside the "
                                             "energy-dissipating mechanism are designed for its expected strength, "
                                             "not to exceed the &Omega;<sub>0</sub>-level effect." if req else
                                             "Capacity design is not required (S400 A1.2.3)."))
        if rows:
            h.append(_table(["Check", "Basis", "Where shown / result"], rows))
    elif R is not None and R <= 3.0 + 1e-9:
        if (_cfs_sys_row(cfg, sysname) or {}).get("cantilever_column"):
            h.append("<p>R = %s: %s%s. AISI S400 does not cover cantilever column systems: members and "
                     "connections are designed to AISI S100 for the analysis forces. ASCE 7-22 &sect;12.2.5.2: the "
                     "required axial strength of each cantilever column in the load combinations with seismic "
                     "effects may not exceed 15 %% of its available axial strength, and the foundations and other "
                     "elements that resist overturning at the column bases are designed for the seismic load "
                     "effect including overstrength (&sect;12.4.3).</p>" % (
                         _cn(R), _pt((_cfs_sys_row(cfg, sysname) or {}).get("label", "cantilever column system")),
                         (", SDC %s" % sdc) if sdc else ""))
        else:
            h.append("<p>R = %s: steel system not specifically detailed for seismic resistance%s. Members and "
                     "connections are designed to AISI S100%s for the analysis forces; the AISI S400 capacity-design "
                     "provisions do not apply (S400 A1.2.3).</p>" % (
                         _cn(R), (", SDC %s" % sdc) if sdc else "", " / S240" if not _cfs_is_portal(cfg) else ""))
        rows = []
        gh = pkg.get("governing_hazard")
        if gh:
            rows.append(["Governing hazard per direction", _cn(gh) if not isinstance(gh, str) else _pt(gh)])
        an = [a for a in pkg.get("anchorage") or [] if isinstance(a, dict) and a.get("DC") is not None]
        if an:
            a = max(an, key=lambda e: _fnum(e.get("DC")) or 0.0)
            custom, _cd = _cfs_custom_model(cfg, pkg)
            if custom and a.get("T_design_kip") is None:
                rows.append(["Net-uplift anchorage (0.9D+1.0W)", "base anchorage table, D/C %s" % _cdc(a.get("DC"))])
            else:
                rows.append(["Net-uplift anchorage (0.9D+1.0W)", "base anchorage table, uplift %s kip (%s), D/C %s" % (
                    _num_or(a.get("T_design_kip", a.get("T_net_uplift_kip"))), _pt(a.get("uplift_combo")),
                    _cdc(a.get("DC")))])
        if rows:
            h.append(_table(["Item", "Where shown / result"], rows))
    cd = pkg.get("capacity_design") if isinstance(pkg.get("capacity_design"), dict) else {}
    lines = cd.get("lines") or {}
    if req and lines:
        rows = []
        for k, ln in lines.items():
            if not isinstance(ln, dict):
                continue
            vn = ln.get("Vn_selected_kip")
            vn = (", ".join("s%s %s" % (_html.escape(str(k_)), _cn(_fnum(x))) for k_, x in vn.items())
                  if isinstance(vn, dict) else _num_or(vn))
            rows.append([_pt(k), _cn(_fnum(ln.get("Om0_eff"))), _cn(_fnum(ln.get("Omega_E"))),
                         vn, _num_or(ln.get("T_Om0_stack_kip")),
                         _num_or(ln.get("T_OmegaE_Vn_stack_kip")), "<b>%s</b>" % _num_or(ln.get("T_cd_bay_seed_kip", ln.get("T_cd_seed_kip"))),
                         _num_or(ln.get("dead_relief_kip_available"))])
        h.append("<h3>Capacity-design tension by line (S400 B3.4)</h3><p class='tnote'>T<sub>cd</sub> = min(tension "
                 "at the &Omega;<sub>0</sub> level, tension at the expected strength &Omega;<sub>E</sub>V<sub>n</sub> of "
                 "the selected assembly); strap lines are per bay. %s %s</p>" % (
                     _pt(_drift_basis_clean(cd.get("Om0_eff_basis", ""))), _pt(cd.get("Omega_E_basis", "")))
                 + _table(["Line", "&Omega;<sub>0</sub> (eff.)", "&Omega;<sub>E</sub>", "V<sub>n</sub> selected (kip)",
                           "T at &Omega;<sub>0</sub> (kip)", "T at &Omega;<sub>E</sub>V<sub>n</sub> (kip)",
                           "T<sub>cd</sub> (kip)", "0.9D relief available (kip)"], rows))
    extra = {k: v for k, v in cd.items() if k not in ("basis", "note", "instruction", "seed_basis", "lines", "Ve_seed",
                                                      "Om0", "Om0_eff", "Om0_eff_basis", "Omega_E",
                                                      "Omega_E_basis", "Ry_by_Fy", "Rt_by_Fy", "strap_ductility")
             and v not in (None, "", [], {})}
    if extra:
        h.append("<h3>Capacity-design records</h3>" + _cfs_block_html(extra))
    if sysname in _WIND_WALL_BASIS:
        h.append("<p class='tnote'><b>Wind design of the lateral walls:</b> %s.</p>" % _WIND_WALL_BASIS[sysname])
    if pkg.get("holddowns") and not _cfs_is_portal(cfg):
        h.append("<p class='tnote'>Specific hold-down and rod products, concrete anchorage (ACI 318 Ch. 17) and "
                 "cladding attachment are confirmed on the drawings and submittals against the demands in "
                 "Section 4.</p>")
    return "".join(h)


def _cfs_block_html(v, depth=0):
    """Nested agent record as readable tables through the one number formatter (no JSON)."""
    if isinstance(v, dict):
        rows = []
        for k, x in v.items():
            if x in (None, "", [], {}):
                continue
            if isinstance(x, dict) and depth < 3:
                rows.append([_hk(k), _cfs_block_html(x, depth + 1)])
            elif isinstance(x, list) and any(isinstance(y, dict) for y in x) and depth < 3:
                rows.append([_hk(k), "".join(_cfs_block_html(y, depth + 1) for y in x)])
            elif isinstance(x, str):
                rows.append([_hk(k), _pt(x)])
            else:
                rows.append([_hk(k), _cn(x)])
        return _table(["Item", "Value"], rows) if rows else ""
    if isinstance(v, list):
        if v and all(isinstance(y, dict) for y in v):
            keys = []
            for y in v:
                for k in y:
                    if k not in keys:
                        keys.append(k)
            if len(keys) <= 9:
                return _table([_hk(k) for k in keys], [[_pt(y.get(k)) if isinstance(y.get(k), str)
                                                        else _cn(y.get(k)) for k in keys] for y in v])
            return "".join(_cfs_block_html(y, depth + 1) for y in v)
        return "<ul>%s</ul>" % "".join("<li>%s</li>" % (_pt(y) if isinstance(y, str) else _cn(y)) for y in v)
    return "<p>%s</p>" % (_pt(v) if isinstance(v, str) else _cn(v))


# keys rendered by the chapters above (never repeated in the supplementary records)
_CFS_PORTAL_SHOWN = {"members", "connections", "anchorage", "schedules", "drift_table",
                     "torsion_companion", "sections", "wind_basis", "combos",
                     "combos_note", "preflight_warnings", "kind", "system",
                     "structure_kind", "analysis_basis", "building", "code", "notes",
                     "capacity_design", "elf", "wall_lines", "holddowns", "studs",
                     "collectors", "model_vs_tributary_flags", "drift_flags",
                     "model_vs_tributary_flags_resolution", "drift_flags_resolution",
                     "stability_flags", "stability_flags_resolution",
                     "independent_tributary", "period_rayleigh", "two_stage_framework", "rho",
                     "rho_basis", "L_factor", "L_factor_basis", "diaphragm_basis", "drift_basis",
                     "drift_limit_basis", "capacity_design_applicability", "elf_by_direction",
                     "wind_basis_by_dir", "gravity_framing", "discontinuity_transfers",
                     "stability_warnings", "governing_hazard", "sample_calcs", "sample_calculations",
                     "design_agent", "report_mode", "materials", "occupancy", "project_title",
                     "detailing", "seismic", "sbmf", "crane", "design_model", "custom_model",
                     "hand_model", "analysis_model", "component_mode", "elf_spot_check",
                     "elf_self_check", "self_check"}
# design-process records: QA annex only (never the report body in publication mode)
_CFS_HARNESS_KEYS = {"rag_grounding", "grounding", "corpus_defects", "rag_quality", "solution_notes",
                     "solution_note", "issues", "issues_log", "brief_notes", "brief", "citation_red_flags",
                     "citation_flags", "engine_notes", "engine_gaps", "agent_notes", "activity", "tool_log",
                     "meta", "notes_for_reviewer", "process_notes", "deviation_notes", "deviations"}


def _cfs_extra_blocks(pkg, num=6):
    """Section 6: the remaining engineering records of the package (serviceability, snow maps,
    thermal movement, feasibility findings, base reactions by case, ...) as formatted tables;
    design-process records go to the QA annex."""
    pkg = pkg or {}
    keys = [k for k, v in pkg.items() if k not in _CFS_PORTAL_SHOWN and v not in (None, "", [], {})
            and (k not in _CFS_HARNESS_KEYS or not _CFS_PUB[0])]
    if not keys:
        return ""
    h = ["<h2>%d. Supplementary design records</h2>" % num]
    for k in keys:
        v = pkg[k]
        h.append("<h3>%s</h3>" % _hk(k).capitalize())
        h.append(_cfs_block_html(v) if isinstance(v, (dict, list)) else
                 "<p>%s</p>" % (_pt(v) if isinstance(v, str) else _cn(v)))
    return "".join(h)


def _cfs_framework_checks_html(pkg):
    """Two-stage block (engineering content of the body)."""
    h = []
    ts = pkg.get("two_stage_framework")
    if isinstance(ts, dict):
        rows = []
        for d, v in (ts.get("by_direction") or {}).items():
            rows.append([_pt(d), _num_or(v.get("K_upper_kip_in")), _num_or(v.get("K_lower_kip_in")),
                         _num_or(v.get("stiffness_ratio")), _num_or(v.get("T_upper_s"), "%.3f"),
                         _num_or(v.get("T_combined_s"), "%.3f"), _num_or(v.get("period_ratio"), "%.2f"),
                         "<b>%s</b>" % _pt(v.get("status")), _pt("; ".join(v.get("messages") or []))])
        h.append("<h3>Two-stage procedure (ASCE 7-22 &sect;12.2.3.2)</h3>"
                 "<p>(a) K<sub>lower</sub> &ge; 10 K<sub>upper</sub>; (b) T<sub>entire</sub> &le; 1.1 "
                 "T<sub>upper</sub>; (d) upper-portion reactions amplified by "
                 "(R/&rho;)<sub>upper</sub>/(R/&rho;)<sub>lower</sub> &ge; 1.0 &mdash; %s</p>"
                 % _pt(ts.get("amplification_basis"))
                 + _table(["Dir", "K<sub>upper</sub> (kip/in.)", "K<sub>lower</sub>", "ratio",
                           "T<sub>upper</sub> (s)", "T<sub>entire</sub> (s)", "T ratio", "Status",
                           "Notes"], rows))
        rx = ts.get("reactions_to_podium") or []
        if rx:
            h.append("<h4>Reactions to the podium (seismic E<sub>h</sub>, &rho; included, &times; amplification)</h4>"
                     + _table(["Dir", "Line", "V (kip)", "T (kip)", "V amplified", "T amplified"],
                              [[_pt(r.get("direction")), _pt(r.get("line")), _cn(r.get("V_Eh_kip")),
                                _cn(r.get("T_Eh_kip")), _cn(r.get("V_Eh_amplified_kip")),
                                _cn(r.get("T_Eh_amplified_kip"))] for r in rx if isinstance(r, dict)]))
    return "".join(h)


# ---- quality assurance (Section 7) and the QA annex -------------------------------------------
_QA_CATS = (
    ("Design basis inputs", "system, governing hazard per direction (with numbers) and the declared inputs "
                            "the analysis needs",
     r"governing_hazard|declare system|R=\d|declare cfg|NOT declared|12\.3\.4\.2"),
    ("Completeness", "every designed item carries a limit state, design strength, reference and D/C "
                     "(or a justified waiver)",
     r"no limit_state|no cited|no D/C|no sheathing|connections list is empty|not found|without an "
     r"engineering justification|has no verdict"),
    ("Strength", "no D/C exceeds 1.0", r"> 1\.0 \(NG|WAIVED at D/C|worst D/C"),
    ("Arithmetic", "each D/C equals demand &divide; capacity where both are numeric (4 % / 0.01); each "
                   "headline D/C equals its worst limit state; one value per quantity",
     r"!= demand/capacity|headline D/C|!= reported D/C|appears as"),
    ("Capacity design (S400 B3.4)", "hold-down, chord and anchorage design demands are not below the "
                                    "capacity-design demands (T<sub>cd</sub>, &Omega;<sub>0</sub>-capped shear)",
     r"capacity-design|T_cd|amplification not applied|shear anchorage designed|requires check"),
    ("Anchorage chain", "hold-down devices within their class envelope (else a continuous rod); the "
                        "0.9D+1.0W net-uplift path designed",
     r"class envelope|NET-UPLIFT|uplift path"),
    ("Code basis", "screw &phi; = 0.50 / &Omega; = 3.00 and the screw's own shear strength (S100-16 w/S2-20 J4); "
                   "ASTM A449 rod strength by diameter; interaction &ge; its axial term (H1-1b aware); "
                   "ASCE 7-22 snow factors; no preliminary (unchecked) values used as the design basis",
     r"screw|A449|interaction|H1-1b|H1\.1|snow factor|snow drift|rain-on-snow|seed label|placeholder label|rod strength"),
    ("Drift and stability", "every drift row within its Table 12.12-1 limit; &theta; &le; &theta;<sub>max</sub>",
     r"\[drift|theta|stability"),
    ("ELF self-check", "a quoted self-check uses the same W and V as Section 3", r"ELF self-check"),
    ("System and height limits", "Table 12.2-1 system permitted in the SDC and within its height limit",
     r"height limit|NOT PERMITTED|exceeds the [\d.]+-ft"),
)


def _cfs_qa_section(name, root, cfg, res, pkg, annex=None, num=7):
    """Section 7: what this report verifies (and what it does not), the module verdicts, the
    self-consistency result, the design credit and the link to the QA annex."""
    import consistency as _CC
    try:
        issues = _CC.check(name, root=root, pkg=pkg, verbose=False)
    except Exception as ex:
        issues = ["self-consistency check could not run: %s" % ex]
    h = ["<h2>%d. Quality assurance</h2>" % num]
    cr = _cfs_design_credit(pkg)
    if cr:
        h.append("<p class='credit'>%s</p>" % cr)
    buckets = {c[0]: [] for c in _QA_CATS}
    other = []
    for i in issues:
        for cat, _d, rx in _QA_CATS:
            if re.search(rx, i, re.I):
                buckets[cat].append(i)
                break
        else:
            other.append(i)
    rows = []
    for cat, desc, _rx in _QA_CATS:
        n = len(buckets[cat])
        rows.append([cat, desc, "<span class='pass'>PASS</span>" if not n else
                     "<span class='fail'>%d item(s)</span>" % n])
    if res and not _cfs_is_portal(cfg):
        _h, ok = _cfs_elf_selfcheck(cfg, res, pkg)
        if ok is not None:
            rows.append(["ELF hand check", "C<sub>s</sub> and V recomputed from S<sub>DS</sub>, S<sub>D1</sub>, R, "
                         "I<sub>e</sub>, T and W (Section 3)", "<span class='pass'>matches</span>" if ok else
                         "<span class='fail'>differs</span>"])
    it = pkg.get("independent_tributary")
    if isinstance(it, dict):
        n = it.get("n_flags")
        rows.append(["Independent tributary check", "line shears recomputed independently of the analysis "
                     "(simple-span tributary rule); expected ratio 1.00&ndash;1.05",
                     "<span class='pass'>no divergence</span>" if not n else
                     ("%s divergence(s), %s" % (_cn(n), "resolved" if pkg.get("model_vs_tributary_flags_resolution")
                                                else "<span class='fail'>unresolved</span>"))])
    if other:
        rows.append(["Other", "remaining screens", "<span class='fail'>%d item(s)</span>" % len(other)])
    h.append("<h3>%d.1 Checks performed on this package</h3>" % num + _table(["Check", "Scope", "Result"], rows))
    h.append("<p class='tnote'>Not verified by this report: the design strengths themselves (AISI table values "
             "and equations) are the designer's and are traceable through the references, the sample "
             "calculations and Appendix A; specific hold-down and rod products, concrete anchorage and "
             "delegated items are confirmed against the stated demands on the drawings and submittals.</p>")
    # module verdicts surfaced
    mv = []
    dt = [d for d in pkg.get("drift_table") or [] if isinstance(d, dict)]
    if dt:
        ng = [d for d in dt if d.get("ok") is False]
        nov = [d for d in dt if d.get("ok") is None and not d.get("waived") and d.get("DC") is None]
        def _dr(d):
            chk = str(d.get("check") or "")
            if "theta" in chk or "apex" in chk:          # stability / roof deflection rows
                return None
            v = _fnum(d.get("drift_design", d.get("drift_amplified")))
            if v is None and chk == "seismic_drift":     # portal: Cd*delta_xe/Ie / h_sx vs the limit
                v = _fnum(d.get("ratio"))
            lim = _fnum(d.get("limit"))
            if v is not None and lim:
                return v / lim
            return _fnum(d.get("DC")) if chk == "eave_sway" else None
        ratios = [r for r in (_dr(d) for d in dt) if r is not None]
        mv.append(["Drift record", "%d rows; worst drift / limit = %s; %d failing, %d without a verdict" % (
            len(dt), ("%.2f" % max(ratios)) if ratios else "&mdash;", len(ng), len(nov))])
    th = _find_theta(pkg)
    if th:
        mv.append(["P-&Delta; stability", "&theta;<sub>max,found</sub> = %.3f%s" % (
            th[0], (" &le; &theta;<sub>max</sub> = %s" % _cn(_fnum(th[1]))) if th[1] is not None and th[0] <= th[1]
            else (" &gt; &theta;<sub>max</sub> = %s" % _cn(_fnum(th[1])) if th[1] is not None else ""))])
    sd = (pkg.get("capacity_design") or {}).get("strap_ductility") if isinstance(pkg.get("capacity_design"), dict) else None
    if isinstance(sd, dict):
        mv.append(["Strap ductility (S400 E3.4.1)", {True: "PASS", False: "<b>FAIL</b>"}.get(sd.get("ok"), "not evaluated")])
    ts = pkg.get("two_stage_framework")
    if isinstance(ts, dict):
        mv.append(["Two-stage eligibility", "; ".join("%s: %s" % (_pt(d), _pt(v.get("status")))
                                                     for d, v in (ts.get("by_direction") or {}).items())])
    for key, what in (("model_vs_tributary_flags", "Model-vs-tributary divergence"),
                      ("drift_flags", "Drift screening flags"), ("stability_flags", "P-&Delta; flags")):
        fl = pkg.get(key) or []
        if fl:
            res_ = pkg.get(key + "_resolution")
            mv.append([what, "%d flag(s); %s" % (len(fl), ("resolution: %s" % _pt(res_)) if res_ else
                                                 "<b>unresolved</b>")])
    for k, v in pkg.items():
        if isinstance(v, dict) and k not in _CFS_HARNESS_KEYS:
            ver = v.get("verdict") or v.get("result")
            if isinstance(ver, str) and re.match(r"(pass|fail|ok|ng)\b", ver.strip(), re.I):
                mv.append(["%s (module verdict)" % _hk(k).capitalize(), _pt(ver)])
    try:
        wng = _CC.waived_ng_items(pkg)
    except Exception:
        wng = []
    if wng:
        mv.append(["Waived items with D/C &gt; 1.0", "; ".join("%s %s (D/C %.2f: %s)" % (_pt(k), _pt(i), d, _pt(j))
                                                              for k, i, d, j in wng)])
    pw = pkg.get("preflight_warnings") or []
    if pw:
        mv.append(["Framework screening notes", "%d note(s), listed in the QA annex" % len(pw)])
    if mv:
        h.append("<h3>%d.2 Analysis and design module verdicts</h3>" % num + _table(["Module", "Verdict"], mv))
    if issues:
        h.append("<h3>%d.3 Items to reconcile</h3><p class='note'><b>%d item(s)</b> must be reconciled before the "
                 "package is final:</p>" % (num, len(issues))
                 + _table(["#", "Item"], [[str(n + 1), _pt(i)] for n, i in enumerate(issues)]))
    else:
        h.append("<h3>%d.3 Self-consistency result</h3><p><span class='pass'>PASS</span> &mdash; every check "
                 "listed in %d.1 passed for this package.</p>" % (num, num))
    if annex:
        h.append("<p class='tnote'>Design-process records (source verification of the cited standards, "
                 "engine screening notes and the design log) are in the QA annex: "
                 "<a href='%s'>%s</a>.</p>" % (annex, annex))
    return "".join(h)


def _cfs_write_qa_annex(name, cfg, res, pkg, root, qa_num=7):
    """qa_annex.html beside report.html: the source-grounding table, the design-process records
    of the package, the framework screening notes and the activity log. Returns its file name."""
    parts = ["<h1>%s &mdash; QA annex</h1>" % _html.escape(str(name)),
             "<p>Design-process records supporting Section %d of the report. Not part of the calculation "
             "package.</p>" % qa_num]
    try:
        parts.append(_grounding_check(cfg, name, pkg))
    except Exception as ex:
        parts.append("<p class='note'>[source verification unavailable: %s]</p>" % _html.escape(str(ex)))
    blocks = {k: v for k, v in (pkg or {}).items() if k in _CFS_HARNESS_KEYS and v not in (None, "", [], {})}
    for k in ("elf_spot_check", "elf_self_check", "self_check"):
        if (pkg or {}).get(k):
            blocks[k] = pkg[k]
    if blocks:
        parts.append("<h2>Design-process records</h2>")
        for k, v in blocks.items():
            parts.append("<h3>%s</h3>" % _html.escape(k.replace("_", " ").capitalize()))
            parts.append("<pre>%s</pre>" % _html.escape(v if isinstance(v, str) else json.dumps(v, indent=1)[:20000]))
    notes = list((pkg or {}).get("preflight_warnings") or []) + list((res or {}).get("preflight_warnings") or [])
    for dd in ((res or {}).get("directions") or {}).values():
        notes += ["gate: %s" % f for f in dd.get("gate_flags", [])]
    if notes:
        parts.append("<h2>Framework screening notes</h2><ul>%s</ul>" % "".join(
            "<li>%s</li>" % _html.escape(str(n)) for n in dict.fromkeys(map(str, notes))))
    try:
        parts.append(_activity_section(name))
    except Exception as ex:
        parts.append("<p class='note'>[activity log unavailable: %s]</p>" % _html.escape(str(ex)))
    html = ("<!doctype html><html><head><meta charset='utf-8'><title>%s QA annex</title><style>%s%s</style>"
            "</head><body>%s</body></html>" % (_html.escape(str(name)), CSS, CFS_CSS, "".join(parts)))
    fn = "qa_annex.html"
    with open(os.path.join(root, fn), "w", encoding="utf-8") as f:
        f.write(html)
    return fn


# ---- Appendix A ---------------------------------------------------------------------------------
_CFS_APPX_GROUPS = [
    ("Shear walls and strap bays", "wall_lines"),
    ("Hold-downs / continuous rods", "holddowns"),
    ("Studs, chords and framing members", "studs"),
    ("Collectors", "collectors"),
    ("Frame members", "members"),
    ("Connections", "connections"),
    ("Anchorage", "anchorage"),
    ("Purlin / girt / strap schedules", "schedules"),
]
_CFS_APPX_ORDER = ["direction", "story", "line", "member", "joint", "group", "section",
                   "sheathing", "fastener_schedule", "selection", "description",
                   "wall_type", "Ca_effective", "v_unit_plf", "v_wind_plf", "V_kip", "demand",
                   "T_cum_kip", "T_bay_seed_kip", "T_cd_seed_kip", "T_wind_kip", "T_design_kip",
                   "M_transfer_kipin", "V_base_kip", "T_net_uplift_kip", "M_base_kipin",
                   "governing_combo", "limit_state", "capacity", "DC", "cited"]
# framework text / bookkeeping that is not part of an item's calculation record
_CFS_APPX_SKIP = {"id", "checks", "rows", "waived", "basis", "note", "notes", "instruction",
                  "seed_basis", "feasibility_note", "demand_basis", "governing_basis", "envelope_basis",
                  "demand_pairs", "by_joint", "per_base", "by_direction", "sign", "Om0_note", "k_kip_in",
                  "device_class", "type_ii", "recheck_after_rerun", "governing_label", "wind_case",
                  "M_signed_kipin", "Pc_paired_kip", "Pt_paired_kip", "combo_pos", "combo_neg", "at_pos",
                  "at_neg", "framework_slot"}


def _cfs_appendix_calcs(pkg, cfg=None, res=None, name=None):
    """APPENDIX A: the referenced design-calculation record -- one card per designed item (inputs
    and demands, limit states, references, strengths, D/C, per-check breakdown). In a custom /
    hand-model portal job an engine value left in a slot is labelled as such."""
    if not isinstance(pkg, dict):
        return ""
    custom = bool(cfg) and _cfs_custom_model(cfg, pkg)[0]
    seeds = _cfs_portal_seeds(name, cfg, res) if (custom and res and _cfs_is_portal(cfg)) else {}
    seed_by = {(g, e.get("id")): e for g in ("members", "connections", "anchorage")
               for e in seeds.get(g) or [] if isinstance(e, dict)}
    cards = []
    for gtitle, key in _CFS_APPX_GROUPS:
        items = [it for it in pkg.get(key) or [] if isinstance(it, dict)]
        if not items:
            continue
        cards.append("<h3>%s</h3>" % gtitle)
        for it in items:
            cid = it.get("id") or it.get("joint") or it.get("schedule") or "item"
            if it.get("waived"):
                cards.append("<h4>%s</h4><p><b>WAIVED:</b> %s</p>" % (_pt(cid), _pt(it["waived"])))
                continue
            rows = []
            keys = [k for k in _CFS_APPX_ORDER if k in it] + [k for k in it if k not in _CFS_APPX_ORDER]
            for k in keys:
                if k in _CFS_APPX_SKIP:
                    continue
                v = it[k]
                if v in (None, "", [], {}):
                    continue
                if key == "wall_lines" and k == "sheathing" and re.search(r"\bnone\b", str(v), re.I):
                    continue                            # strap bay: no sheathing
                if k == "DC" and _fnum(v) is not None:
                    sv = "<b>%.3f</b>" % float(v)
                elif isinstance(v, str):
                    sv = _pt(v)
                elif isinstance(v, dict) and k.endswith(("_by_story", "_by_level", "_by_story_kip")):
                    sv = ", ".join("%s: %s" % (_html.escape(str(s_)), _cn(x)) for s_, x in v.items())
                else:
                    sv = _cn(v)
                if custom and key == "members" and k in ("V_kip", "V_design_kip") and _fnum(v) == 0.0:
                    sv = "not reported"
                se = seed_by.get((key, it.get("id")))
                if se and k in se and _fnum(v) is not None and _same(v, se.get(k)):
                    sv += " <i>(simplified engine frame, not used for design)</i>"
                rows.append(["line shear, &rho;E (kip)" if (key == "wall_lines" and k == "V_kip") else _hk(k), sv])
            card = "<h4>%s</h4>" % _pt(cid) + _table(["Field", "Value"], rows)
            checks = [c for c in it.get("checks") or [] if isinstance(c, dict)]
            if checks:
                crow = [[_pt(c.get("limit_state", c.get("name", "check"))), *_check_dem_cap(c),
                         _cdc(c.get("DC")), _pt(c.get("note", ""))] for c in checks]
                card += _table(["Limit state", "Demand", "Strength", "D/C", "Note"], crow)
            srow = [r for r in it.get("rows") or [] if isinstance(r, dict)]
            if srow:
                ks = []
                for r in srow:
                    for k in r:
                        if k not in ks and k != "cited":
                            ks.append(k)
                rr = _Refs()
                body = [[(_cdc(r.get(k)) if k == "DC" else _pt(r.get(k)) if isinstance(r.get(k), str)
                          else _cn(r.get(k))) for k in ks] + [rr.ref(r.get("cited"))] for r in srow]
                if any(r.get("cited") for r in srow):
                    card += _ref_table([_hk(k) for k in ks] + ["Ref."], body, rr)
                else:
                    card += _table([_hk(k) for k in ks], [b[:-1] for b in body])
            cards.append(card)
    if not cards:
        return ""
    return ("<h2>Appendix A &mdash; design calculation record</h2>"
            "<p>The referenced AISI S100 / S240 / S400 record of every designed item: inputs and demands, "
            "governing limit state, reference, design strength and D/C.</p>" + "".join(cards))


def _cfs_iso_figure(cfg, name, res, pkg):
    """Section-1 annotated ISOMETRIC of the building (static PNG) so the element labels are
    visually identifiable: wall lines '<dir>-<name>' at their plan positions and story levels
    's1..sN' (wall path); col_L/col_R/raf_L/raf_R, knee/apex/base and frame numbering (portal).
    Geometry comes from the same builder as the interactive viewer (cfs_viewer3d.viewer_data)."""
    try:
        import cfs_viewer3d as V3
        from mpl_toolkits.mplot3d.art3d import Line3DCollection
        data = V3.viewer_data(cfg, name, res, pkg)
    except Exception as ex:
        return "" if _CFS_PUB[0] else "<p class='note'>[model figure unavailable: %s]</p>" % _html.escape(str(ex))
    nodes, els = data["nodes"], data["elements"]
    CMAP = {"column": "#5b8dd9", "beam": "#8a97a8", "brace": "#e67e22"}
    segs, cols = [], []
    for e in els:
        c1, c2 = nodes[str(e["n"][0])], nodes[str(e["n"][1])]
        segs.append([tuple(c1), tuple(c2)])
        cols.append(CMAP.get(e["type"], "#999999"))
    xs = [c[0] for c in nodes.values()]
    ys = [c[1] for c in nodes.values()]
    zs = [c[2] for c in nodes.values()]
    dx, dy, dz = max(xs) - min(xs) or 1, max(ys) - min(ys) or 1, max(zs) - min(zs) or 1
    fig = plt.figure(figsize=(16.0, 7.5))
    ax = fig.add_subplot(111, projection="3d")
    ax.add_collection3d(Line3DCollection(segs, colors=cols, linewidths=0.9, alpha=0.9))
    portal = _cfs_is_portal(cfg)
    if not portal:
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        z = [0.0]
        for hh in cfg["heights_ft"]:
            z.append(z[-1] + hh * 12.0)
        Lx, Ly = cfg["plan_ft"][0] * 12.0, cfg["plan_ft"][1] * 12.0
        plates = [[(0, 0, zz), (Lx, 0, zz), (Lx, Ly, zz), (0, Ly, zz)] for zz in z[1:]]
        ax.add_collection3d(Poly3DCollection(plates, facecolors="#7a8899", alpha=0.12,
                                             edgecolors="#7a8899", linewidths=0.4))
    try:
        ax.set_box_aspect((dx, dy, dz), zoom=1.42)
    except TypeError:
        ax.set_box_aspect((dx, dy, dz))
    ax.view_init(elev=19, azim=-64)
    ax.set_axis_off()
    ax.set_xlim(min(xs) - 0.08 * dx, max(xs) + 0.08 * dx)
    ax.set_ylim(min(ys) - 0.08 * dy, max(ys) + 0.08 * dy)
    ax.set_zlim(min(zs), max(zs) + 0.12 * dz)
    ax.set_position((-0.02, -0.03, 1.04, 1.06))
    from mpl_toolkits.mplot3d import proj3d

    def _ann(txt, tip, off_pts, color, fs=10):
        x2, y2, _ = proj3d.proj_transform(tip[0], tip[1], tip[2], ax.get_proj())
        ax.annotate(txt, xy=(x2, y2), xytext=off_pts, textcoords="offset points",
                    color=color, fontsize=fs, fontweight="bold",
                    ha="center", va="center", annotation_clip=False,
                    arrowprops=dict(arrowstyle="-", color=color, lw=1.2,
                                    shrinkA=8, shrinkB=2, alpha=0.9))
    if not portal:
        for i, ln in enumerate(cfg["lines_x"]):
            _ann("X-%s" % ln.name, (0.06 * Lx, ln.pos * 12.0, z[-1] * 0.55),
                 (-70 - 24 * (i % 2), 12 * (i % 2)), "#b30000")
        for i, ln in enumerate(cfg["lines_y"]):
            _ann("Y-%s" % ln.name, (ln.pos * 12.0, 0.30 * Ly, z[-1]),
                 (0, 55 + 16 * (i % 2)), "#006400")
        for k in range(1, len(z)):
            _ann("s%d" % k, (Lx, 0.0, (z[k - 1] + z[k]) / 2.0), (48, 0), "#444444", fs=9)
        ax.quiver(Lx * 1.02, Ly * 0.4, 0, 0, 0.14 * Ly, 0, color="k", arrow_length_ratio=0.35)
        ax.text(Lx * 1.02, Ly * 0.62, 0, "N (+Y)", fontsize=8, ha="left")
        cap = ("Wall labels read <code>wall-&lt;dir&gt;-&lt;line&gt;-s&lt;story&gt;</code> (e.g. "
               "<code>wall-X-%s-s2</code> = X-direction line %s at story 2); hold-downs are "
               "<code>hd-&lt;dir&gt;-&lt;line&gt;</code>. Blue = chord studs, grey = tracks, orange = "
               "panel / strap diagonals." % (_html.escape(cfg["lines_x"][0].name), _html.escape(cfg["lines_x"][0].name)))
    else:
        _sp = cfg.get("spans") or []
        He = cfg["eave_ft"] * 12.0
        if _sp:                                   # multi-span: every column line and ridge
            xs_ = [0.0]
            for x in _sp:
                xs_.append(xs_[-1] + float(x.get("span_ft", 0)) * 12.0)
            apx = [float(x.get("apex_ft") or cfg["eave_ft"]) * 12.0 for x in _sp]
        else:
            xs_ = [0.0, float(cfg["span_ft"]) * 12.0]
            apx = [float(cfg.get("apex_ft") or cfg["eave_ft"]) * 12.0]
        span, Ha = xs_[-1], max(apx)
        s1, sN = xs_[1] - xs_[0], xs_[-1] - xs_[-2]
        _ann("col_L", (0.0, 0.0, He * 0.5), (-55, 0), "#b30000")
        _ann("col_R", (span, 0.0, He * 0.5), (55, -10), "#b30000")
        for k, xk in enumerate(xs_[1:-1], 1):
            _ann("col_I%d" % k, (xk, 0.0, He * 0.45), (28, -38), "#b30000")
            _ann("valley", (xk, 0.0, He), (-10, 48), "#006400", fs=9)
        _ann("raf_L", (s1 * 0.30, 0.0, He + 0.60 * (apx[0] - He)), (-20, -55), "#b30000")
        _ann("raf_R", (span - sN * 0.28, 0.0, He + 0.55 * (apx[-1] - He)), (60, 25), "#b30000")
        _ann("knee", (0.0, 0.0, He), (-45, 40), "#006400")
        for k, ak in enumerate(apx):
            _ann("apex/ridge" if len(apx) == 1 else "ridge %d" % (k + 1),
                 ((xs_[k] + xs_[k + 1]) / 2.0, 0.0, ak), (0, 55), "#006400")
        _ann("base", (0.0, 0.0, 0.0), (-30, -35), "#006400")
        nf = int(round(dy / (cfg["spacing_ft"] * 12.0))) + 1
        for j in (0, nf - 1):
            _ann("frame %d" % (j + 1), (span / 2.0, j * cfg["spacing_ft"] * 12.0, 0.0),
                 (0, -45), "#555555", fs=9)
        cap = ("Element labels: <code>frame-col</code> / <code>frame-raf</code> (columns col_L/col_R%s, "
               "rafters raf_L/raf_R%s), <code>conn-knee</code> / <code>conn-apex</code> (joints), "
               "<code>base-anchor</code> (bases). Blue = columns, grey = rafters, purlins and girts, "
               "orange = X-straps." % (
                   ", interior columns col_I1%s" % ("&ndash;col_I%d" % (len(xs_) - 2) if len(xs_) > 3 else "")
                   if len(xs_) > 2 else "",
                   " (left / right halves of every span)" if len(xs_) > 2 else ""))
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140)
    plt.close(fig)
    buf.seek(0)
    src = "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")
    return ("<h3>Model key &mdash; element labels</h3>"
            "<img src='%s' style=\"display:block;width:96vw;max-width:1720px;"
            "position:relative;left:50%%;transform:translateX(-50%%);"
            "border:1px solid #ddd;border-radius:6px\">"
            "<p class='tnote'>%s Interactive version: the <b>View model</b> button above.</p>"
            % (src, cap))


def _chapter_guard(parts, label, fn, *a):
    """Run one report chapter; on failure PRINT the exception into the report (never pass)."""
    try:
        x = fn(*a)
        if x:
            parts.append(x)
    except Exception as ex:
        parts.append("<p class='note'><b>[%s failed to render: %s: %s]</b></p>"
                     % (_html.escape(label), _html.escape(type(ex).__name__), _html.escape(str(ex))))


def build_report_cfs(name, cfg, res, pkg, root, pkg_name="calc_package_cfs.json"):
    """The CFS-path report (wall OR portal), pure python -- no openseespy needed. Renders the
    engine results and the package with the designed values; publication mode by default (the
    design-process records go to qa_annex.html). Re-render after filling with
    report.build_report(name)."""
    pkg = pkg if isinstance(pkg, dict) else {}
    _CFS_PUB[0] = _cfs_publish(cfg, pkg)
    portal = _cfs_is_portal(cfg)
    os.makedirs(root, exist_ok=True)
    parts = [_cfs_title_block(name, cfg, res, pkg)]
    try:
        import cfs_viewer3d as _V3
        parts.append(_V3.report_section(cfg, name, res, pkg, root).replace("package D&#8725;C", "D&#8725;C"))
    except Exception as _vex:                  # publication: no tool error in the body
        if not _CFS_PUB[0]:
            parts.append("<p class='note'>[3D viewer unavailable: %s]</p>" % _html.escape(str(_vex)))
    _chapter_guard(parts, "design basis", _cfs_design_basis, cfg, res, pkg)
    _chapter_guard(parts, "model key figure", _cfs_iso_figure, cfg, name, res, pkg)
    _chapter_guard(parts, "load combinations", _cfs_combos_section, cfg, res, pkg)
    if portal:
        _chapter_guard(parts, "frame loads", _cfs_portal_loads, cfg, res, pkg)
        _chapter_guard(parts, "frame design", _cfs_portal_design_section, pkg, cfg, res, name)
    else:
        _chapter_guard(parts, "seismic and wind demands", _cfs_wall_demands_section, cfg, res, pkg)
        _chapter_guard(parts, "two-stage", _cfs_framework_checks_html, pkg)
        parts.append("<h2>4. Design</h2>")
        _chapter_guard(parts, "sample calculations", _cfs_sample_calcs, pkg)
        _chapter_guard(parts, "schedules", _cfs_schedules_section, pkg, cfg, res)
        _chapter_guard(parts, "connections", _cfs_connections_table, pkg,
                       "Connections and anchorage (straps, clips, track anchorage, rod hardware)", cfg)
    _chapter_guard(parts, "capacity-design chapter", _cfs_capacity_chapter, cfg, pkg)
    n0 = len(parts)
    _chapter_guard(parts, "supplementary records", _cfs_extra_blocks, pkg, 6)
    qa_num = 7 if len(parts) > n0 else 6
    annex = None
    try:
        annex = _cfs_write_qa_annex(name, cfg, res, pkg, root, qa_num)
    except Exception as _aex:
        parts.append("<p class='note'>[QA annex failed: %s]</p>" % _html.escape(str(_aex)))
    _chapter_guard(parts, "quality assurance", _cfs_qa_section, name, root, cfg, res, pkg, annex, qa_num)
    if not _CFS_PUB[0]:                    # draft: the design-process records inline
        _chapter_guard(parts, "grounding verification", _grounding_check, cfg, name, pkg)
    _chapter_guard(parts, "appendix A", _cfs_appendix_calcs, pkg, cfg, res, name)
    html = ("<!doctype html><html><head><meta charset='utf-8'><title>%s &mdash; CFS design report"
            "</title><style>%s%s%s</style>%s</head><body>" % (_html.escape(str(name)), CSS, CHK_CSS, CFS_CSS,
                                                              MATHJAX)
            + "".join(parts) + "</body></html>")
    path = os.path.join(root, "report.html")
    open(path, "w", encoding="utf-8").write(html)
    return path


def _load_job_cfg(root, name):
    import types as _types
    cfgp = os.path.join(root, "cfg.py")
    if not os.path.exists(cfgp):
        return None
    m = _types.ModuleType("cfg_rr_" + name)
    m.__file__ = cfgp
    exec(compile(open(cfgp, encoding="utf-8").read(), cfgp, "exec"), m.__dict__)
    return getattr(m, "cfg", None)


def build_report_cfs_from_disk(name, root=None):
    """CFS RE-RENDER: rebuild report.html from the job folder AFTER the agent has filled the
    package -- report.build_report(name) dispatches here for CFS jobs. Re-execs cfg.py, re-runs
    the engine (wall or portal auto-detected) for the demand-side `res`, loads the FILLED
    package from disk (capacities preserved -- never re-seeded; the ONE authoritative file per
    consistency.package_path: design/calc_package_cfs.json, legacy calc_package.json read for
    compatibility) and renders."""
    if root is None:
        base = os.environ.get("STEEL_BUILDER_JOBS") or HERE
        root = os.path.join(base, name)
    cfg = _load_job_cfg(root, name)
    if cfg is None:
        raise SystemExit("%s/cfg.py not found or defines no top-level cfg dict -- write cfg.py "
                         "first" % root)
    import consistency as _CC
    pkgp, notes, issues = _CC.package_path(root, cfg)
    for n_ in notes + issues:
        print("[report] " + n_)
    if pkgp is None:
        raise SystemExit("design/calc_package_cfs.json not found under %s -- run "
                         "pipeline.design_and_report first" % root)
    pkg = json.load(open(pkgp, encoding="utf-8"))
    try:
        import pipeline as _PL
        res = _PL._cfs_engine_res(cfg)
    except ImportError:
        if _cfs_is_portal(cfg):
            import cfs_frame as CF
            res = CF.run(cfg)
        else:
            import cfs_engine as CE
            res = CE.run(cfg)
    return build_report_cfs(name, cfg, res, pkg, root, pkg_name=os.path.basename(pkgp))


if __name__ == "__main__":
    for nm in (sys.argv[1:] or ["B02"]):
        try: build_report(nm)
        except Exception as ex: print(f"[{nm}] FAILED: {ex}")

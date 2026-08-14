"""
extract_sfia.py -- one-time extraction of the SFIA Technical Guide (2026 ed.) tables.

    python3 extract_sfia.py [path/to/SFIA-technical-product-guide_FINAL_20260324.pdf]

Writes, next to this script:
  cfs_shapes.csv                    -- S/T/U/F member properties (gross + tabulated effective)
                                       = the Stage-1 VALIDATOR for cfs_sections' analytical EWM
  sfia_oracle_limiting_heights.csv  -- limiting wall heights (composite / non-composite / curtain)
  sfia_oracle_joist_spans.csv       -- allowable uniform loads, simple-span joists (total + live)
  sfia_oracle_headers.csv           -- header allowable uniform loads
  sfia_oracle_web_crippling.csv     -- web crippling allowable loads (single + back-to-back)
  sfia_oracle_axial_lateral.csv     -- combined axial + lateral allowable loads (kip)

Then runs cfs_sections.validate_against_csv() (BUILD_STAGES Gate 1).

DOCTRINE (CONVERSION_SCOPE_CFS.md 4a): the properties CSV is design-time DATA (validator for the
EWM engine); every ORACLE csv is a TEST oracle only -- each table bakes in fixed assumptions
(simple span, bracing, bearing, deflection limit) and must NEVER be used as a design source.
Tabulated strengths are ASD ALLOWABLE values exactly as published (columns say _asd) -- convert
knowingly (Omega per limit state) if a comparison needs LRFD. Check SFIA's terms before
committing the generated CSVs; committing only this script and generating locally is the safe
default (same posture as the licensed-spec RAG).

Layout facts this parser relies on (2026 edition, 101 pages):
  p11-16 S-stud properties (22 numeric cols)   p17-22 T-track properties (19 cols)
  p81 U-channel / p83 F-furring properties (12 cols; F's Ma is ft-lb)
  p25-30 non-structural limiting heights (12 height cols) p32-40 curtain-wall (21 cols)
  p42-65 combined axial+lateral (x-position-mapped columns) p67-71 joist spans (paired rows)
  p73-75 headers   p77-80 web crippling ("550S - 68" rows)
"""
import csv, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PDF_DEFAULT = os.path.join(os.path.dirname(HERE), "SFIA-technical-product-guide_FINAL_20260324.pdf")
MILS = (118, 97, 68, 54, 43, 33, 30, 27, 18)

DES = re.compile(r"^(\d{2,4})([STUF])(\d{2,3})-(\d{2,4})$")
NUM = re.compile(r"^-?\d+(?:\.\d+)?$")
FTIN = re.compile(r"(\d+)'\s*-\s*(\d+(?:\.\d+)?)(?:''|\")")
HEIGHT_TOK = re.compile(r"(\d+'\s*-\s*\d+(?:\.\d+)?(?:''|\")|---)\s*([a-z]?)")


def split_mil(tail):
    """'54'->(54,''); '541'->(54,'1' footnote); '118'->(118,'')."""
    for m in MILS:
        s = str(m)
        if tail.startswith(s) and len(tail) - len(s) <= 1:
            return m, tail[len(s):]
    return None, None


def parse_designator(tok):
    """'600S162-54' (+optional glued footnote) -> (canonical, depth, fam, flange, mil) or None."""
    mm = DES.match(tok)
    if not mm:
        return None
    depth, fam, flange, tail = mm.groups()
    mil, foot = split_mil(tail)
    if mil is None:
        return None
    return ("%s%s%s-%d" % (depth, fam, flange, mil), int(depth), fam, int(flange), mil)


def ftin_to_in(tok):
    m = FTIN.search(tok)
    return round(int(m.group(1)) * 12 + float(m.group(2)), 2) if m else None


def _floats(toks):
    return [float(t) for t in toks] if all(NUM.match(t) for t in toks) else None


# ---------------------------------------------------------------- member properties
# column layouts AFTER the member token (see module docstring / page headers)
S_COLS = ("t_design_in Fy A wt_plf Ix Sx rx Iy ry Ixe Sxe Ma_asd_kipin Ma_dist_asd_kipin "
          "Va_asd_lb Va_net_asd_lb Jx1000 Cw xo_in m_in ro_in beta Lu_in").split()
T_COLS = ("t_design_in Fy A wt_plf Ix Sx rx Iy ry Ixe Sxe Ma_asd_kipin "
          "Va_asd_lb Jx1000 Cw xo_in m_in ro_in beta").split()
UF_COLS = "t_design_in Fy A wt_plf Ix rx Iy ry Ixe Sxe Ma_asd Va_asd_lb".split()

GROSS_OUT = ["A", "wt_plf", "Ix", "Sx", "rx", "Iy", "ry", "J", "Cw",
             "xo_in", "m_in", "ro_in", "beta", "Lu_in"]
PER_FY = ["Ixe", "Sxe", "Ma_asd_kipin", "Ma_dist_asd_kipin", "Va_asd_lb", "Va_net_asd_lb"]


def parse_properties(pages):
    """-> {designator: row-dict}. Merges the 33/50-ksi lines of one designator."""
    out = {}
    for page in pages:
        title = "\n".join((page.extract_text() or "").splitlines()[:3])
        if "Section Properties" not in title:
            continue
        for ln in (page.extract_text() or "").splitlines():
            toks = ln.split()
            if len(toks) < 10:
                continue
            d = parse_designator(toks[0])
            if not d:
                continue
            des, depth, fam, flange, mil = d
            vals = _floats(toks[1:])
            if vals is None:
                continue
            cols = {22: S_COLS, 19: T_COLS, 12: UF_COLS}.get(len(vals))
            if cols is None:
                continue
            r = dict(zip(cols, vals))
            fy = int(r["Fy"])
            row = out.setdefault(des, dict(designator=des, family=fam, depth_in=depth / 100.0,
                                           flange_in=flange / 100.0, mil=mil,
                                           t_design_in=r["t_design_in"]))
            for k in GROSS_OUT:                       # gross props are Fy-independent
                if k == "J":
                    if "Jx1000" in r: row["J"] = r["Jx1000"] / 1000.0
                elif k in r:
                    row[k] = r[k]
            ma = r.get("Ma_asd_kipin", r.get("Ma_asd"))
            if fam == "F" and "Ma_asd" in r:
                ma = r["Ma_asd"] * 12.0 / 1000.0      # F-channel Ma is tabulated in ft-lb
            for k in PER_FY:                          # effective/allowable are per-grade
                v = r.get(k) if k != "Ma_asd_kipin" else ma
                if v is not None:
                    row["%s_%d" % (k.replace("_asd_kipin", "_asd").replace("_asd_lb", "_asd"), fy)
                        if k in ("Ma_asd_kipin",) else "%s_%d" % (k, fy)] = v
            row.setdefault("Fy_avail", set()).add(fy)
    for r in out.values():
        r["Fy_avail"] = "/".join(str(x) for x in sorted(r["Fy_avail"]))
    return out


# ---------------------------------------------------------------- limiting wall heights
LH_CONFIGS = {12: [(5, 120), (5, 240), (5, 360), (7.5, 120), (7.5, 240), (7.5, 360),
                   (10, 120), (10, 240), (10, 360), (15, 120), (15, 240), (15, 360)],
              21: [(5, 120), (5, 240), (5, 360), (15, 240), (15, 360), (15, 600),
                   (20, 240), (20, 360), (20, 600), (25, 240), (25, 360), (25, 600),
                   (30, 240), (30, 360), (30, 600), (35, 240), (35, 360), (35, 600),
                   (40, 240), (40, 360), (40, 600)]}


def parse_limiting_heights(pages):
    rows = []
    for page in pages:
        text = page.extract_text() or ""
        head = "\n".join(text.splitlines()[:2])
        if "Limiting Wall Heights" not in head:
            continue
        table = ("curtainwall_single" if "Curtain" in head else
                 "nonstructural_composite" if "Composite" in head and "Non-Comp" not in head
                 else "nonstructural_noncomposite")
        member = fy = None
        for ln in text.splitlines():
            toks = ln.split()
            if not toks:
                continue
            d = parse_designator(toks[0])
            if d:                                     # lead row: member spacing fy heights...
                if len(toks) < 3 or not NUM.match(toks[1]) or not NUM.match(toks[2]):
                    continue
                member, spacing, fy = d[0], int(float(toks[1])), int(float(toks[2]))
                rest = ln.split(None, 3)[3] if len(toks) > 3 else ""
            elif member and NUM.match(toks[0]) and int(float(toks[0])) in (12, 16, 24):
                spacing = int(float(toks[0]))         # continuation row: spacing heights...
                rest = ln.split(None, 1)[1] if len(toks) > 1 else ""
            else:
                continue
            hts = HEIGHT_TOK.findall(rest)
            cfgkey = 12 if table.startswith("nonstructural") else 21
            cfgs = LH_CONFIGS[cfgkey]
            if len(hts) != len(cfgs):                 # ragged '---' tails: pad
                hts = hts + [("---", "")] * (len(cfgs) - len(hts)) if len(hts) < len(cfgs) else None
            if hts is None:
                continue
            for (tok, foot), (psf, dlim) in zip(hts, cfgs):
                h = ftin_to_in(tok)
                if h is not None:
                    rows.append(dict(table=table, member=member, Fy=fy, spacing_in=spacing,
                                     load_psf=psf, defl_limit="L/%d" % dlim,
                                     height_in=h, footnote=foot))
    return rows


# ---------------------------------------------------------------- joist spans (paired rows)
def parse_joists(pages):
    rows = []
    for page in pages:
        text = page.extract_text() or ""
        if "Allowable Uniform Load Table" not in text:
            continue
        spans = [ftin_to_in(m.group(0)) for m in
                 re.finditer(r"\d+'-\d+''", text.splitlines()[4] if len(text.splitlines()) > 4
                             else "")] or None
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            toks = lines[i].split()
            d = parse_designator(toks[0]) if toks else None
            if d and len(toks) >= 4 and NUM.match(toks[1]):
                tot = _floats(toks[2:])
                liv = _floats(lines[i + 1].split()) if i + 1 < len(lines) else None
                if tot and liv and len(liv) == len(tot):
                    sp = spans if spans and len(spans) == len(tot) else \
                        [72 + 12 * k for k in range(len(tot))]
                    for s, tl, ll in zip(sp, tot, liv):
                        rows.append(dict(member=d[0], Fy=int(float(toks[1])), span_in=s,
                                         total_asd_plf=tl, live_asd_plf=ll))
                    i += 2
                    continue
            i += 1
    return rows


# ---------------------------------------------------------------- headers
def parse_headers(pages):
    rows, spans_ft = [], (3, 4, 5, 6, 8, 10, 12)
    for page in pages:
        text = page.extract_text() or ""
        if "Header Allowable Uniform Loads" not in text:
            continue
        for ln in text.splitlines():
            toks = ln.split()
            d = parse_designator(toks[0]) if toks else None
            if not d or len(toks) < 3 or not NUM.match(toks[1]):
                continue
            vals = re.findall(r"(\d+(?:\.\d+)?)\s*([a-z]?)", " ".join(toks[2:]))
            if len(vals) != len(spans_ft):
                continue
            for sft, (v, foot) in zip(spans_ft, vals):
                rows.append(dict(member=d[0], Fy=int(float(toks[1])), span_ft=sft,
                                 w_asd_plf=float(v), footnote=foot))
    return rows


# ---------------------------------------------------------------- web crippling
def parse_web_crippling(pages):
    rows, bearings = [], (1.0, 3.5, 4.0, 6.0)
    for page in pages:
        text = page.extract_text() or ""
        if "Web Crippling Loads" not in text:
            continue
        kind = "back_to_back" if "Back-to-Back" in text else "single"
        for ln in text.splitlines():
            toks = ln.split()
            # rows look like: 550S - 68 t radius Fy v*16
            if len(toks) >= 22 and toks[1] == "-" and re.match(r"^\d+[SF]$", toks[0]):
                vals = _floats(toks[3:])
                if not vals or len(vals) != 19:
                    continue
                t, radius, fy = vals[0], vals[1], int(vals[2])
                for ci in range(4):
                    for bi, brg in enumerate(bearings):
                        rows.append(dict(member_series="%s-%s" % (toks[0], toks[2]), kind=kind,
                                         t_design_in=t, radius_in=radius, Fy=fy,
                                         condition=ci + 1, bearing_in=brg,
                                         P_asd_lb=vals[3 + 4 * ci + bi]))
    return rows


# ---------------------------------------------------------------- combined axial + lateral
def parse_axial(pages):
    """x-position-mapped: group headers '362S137-(mils)', a ksi row, a mils row, then data rows
    of 'value footnote' pairs zipped left-to-right onto the mil columns. Heights sit on the
    MIDDLE row (spacing 16) of each 3-row (12/16/24 o.c.) block."""
    rows = []
    for page in pages:
        text = page.extract_text() or ""
        if "Combined Axial and Lateral Load Tables" not in text:
            continue
        lat = re.search(r"(\d+(?:\.\d+)?)\s*psf Lateral Load", text)
        lat = float(lat.group(1)) if lat else None
        words = page.extract_words()
        groups = [(w["x0"], w["text"].split("-")[0]) for w in words
                  if re.match(r"^\d+S\d+-\(mils\)$", w["text"])]
        if not groups or lat is None:
            continue
        groups.sort()
        ksis = sorted((w["x0"], int(p["text"])) for p, w in zip(words, words[1:])
                      if w["text"] == "ksi" and p["text"] in ("33", "50"))
        # the mils header line: the line whose tokens are all in MILS and >= 8 long
        mil_words = None
        by_line = {}
        for w in words:
            by_line.setdefault(round(w["top"]), []).append(w)
        for y, ws in sorted(by_line.items()):
            ws = sorted(ws, key=lambda w: w["x0"])
            toks = [w["text"] for w in ws]
            ints = [t for t in toks if t.isdigit() and int(t) in MILS]
            if len(ints) >= 8 and len(ints) >= len(toks) - 2:
                mil_words = [w for w in ws if w["text"].isdigit() and int(w["text"]) in MILS]
                break
        if not mil_words:
            continue
        def before(seq, x):                            # last header whose x0 <= x (+small slack)
            best = None
            for gx, gv in seq:
                if gx <= x + 6:
                    best = gv
            return best
        colmap = [(before(groups, w["x0"]), before(ksis, w["x0"]), int(w["text"]))
                  for w in mil_words]
        ncol = len(colmap)
        height = None
        for ln in text.splitlines():
            toks = ln.split()
            nums = [t for t in toks if NUM.match(t)]
            vals = re.findall(r"(\d+(?:\.\d+)?)\s+([a-z])\b", ln)
            if len(vals) != ncol:
                continue
            lead = []
            for t in toks:
                if NUM.match(t) and float(t) == float(vals[0][0]):
                    break
                if NUM.match(t):
                    lead.append(int(float(t)))
            if len(lead) == 2:
                height, spacing = lead
            elif len(lead) == 1:
                spacing = lead[0]
            else:
                continue
            for (grp, fy, mil), (v, foot) in zip(colmap, vals):
                if grp and fy:
                    rows.append(dict(member="%s-%d" % (grp, mil), Fy=fy, lateral_psf=lat,
                                     height_ft=height, spacing_in=spacing,
                                     P_asd_kip=float(v), footnote=foot))
    return rows


# ---------------------------------------------------------------- main
def write_csv(path, rows, cols):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("  wrote %-38s %6d rows" % (os.path.basename(path), len(rows)))


def main(pdf_path=None):
    import pdfplumber
    pdf_path = pdf_path or PDF_DEFAULT
    print("[extract_sfia] reading %s" % pdf_path)
    pdf = pdfplumber.open(pdf_path)
    pages = pdf.pages

    props = parse_properties(pages)
    fy_cols = sorted({k for r in props.values() for k in r if re.search(r"_(33|50)$", k)})
    cols = (["designator", "family", "depth_in", "flange_in", "mil", "t_design_in", "Fy_avail"]
            + GROSS_OUT + fy_cols)
    write_csv(os.path.join(HERE, "cfs_shapes.csv"), sorted(props.values(),
              key=lambda r: (r["family"], r["depth_in"], r["flange_in"], r["mil"])), cols)

    lh = parse_limiting_heights(pages)
    write_csv(os.path.join(HERE, "sfia_oracle_limiting_heights.csv"), lh,
              ["table", "member", "Fy", "spacing_in", "load_psf", "defl_limit", "height_in", "footnote"])
    js = parse_joists(pages)
    write_csv(os.path.join(HERE, "sfia_oracle_joist_spans.csv"), js,
              ["member", "Fy", "span_in", "total_asd_plf", "live_asd_plf"])
    hd = parse_headers(pages)
    write_csv(os.path.join(HERE, "sfia_oracle_headers.csv"), hd,
              ["member", "Fy", "span_ft", "w_asd_plf", "footnote"])
    wc = parse_web_crippling(pages)
    write_csv(os.path.join(HERE, "sfia_oracle_web_crippling.csv"), wc,
              ["member_series", "kind", "t_design_in", "radius_in", "Fy", "condition",
               "bearing_in", "P_asd_lb"])
    ax = parse_axial(pages)
    write_csv(os.path.join(HERE, "sfia_oracle_axial_lateral.csv"), ax,
              ["member", "Fy", "lateral_psf", "height_ft", "spacing_in", "P_asd_kip", "footnote"])

    fams = {}
    for r in props.values():
        fams[r["family"]] = fams.get(r["family"], 0) + 1
    print("[extract_sfia] designators by family:", fams)
    print("[extract_sfia] running Stage-1 Gate: cfs_sections.validate_against_csv()")
    sys.path.insert(0, HERE)
    import cfs_sections
    n, worst = cfs_sections.validate_against_csv()
    if worst and worst[1]:
        print("[extract_sfia] gate: %d designators checked; worst dev %.1f%% (%s %s)"
              % (n, worst[0] * 100, worst[1], worst[2]))
    return props


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)

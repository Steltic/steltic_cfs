"""RAG acceptance harness (Fix 6, RAG_GAPS_FIX_SCOPE 2026-08-03).

Replays the gold-pass retrieval assessment as a regression suite against the live RAG:

  REGRESSION  -- queries that HIT during the 25-building gold pass must still hit
                 (right clause in top-k, with a content marker where one is stable).
  MUST_NOW_HIT-- the confirmed-missing items; RED before the re-ingest, GREEN after.
  STRUCTURE   -- the Fix 1/2/4 behaviors: 'Table X' chunks retrievable directly,
                 clause prefix matching, doc_part filter, glossary out of top-3.

Usage:
    python3 rag_v2/rag_acceptance.py --baseline   # pre-fix: MUST_NOW_HIT/STRUCTURE
                                                  # failures REPORTED, exit 0
    python3 rag_v2/rag_acceptance.py              # gate mode: EVERYTHING must pass
Env: RAG_API_URL (full /query endpoint) + RAG_API_TOKEN, or a .env at the repo root.
Pure stdlib.
"""
import json, os, sys, urllib.request, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
if not os.environ.get("RAG_API_URL") and (ROOT / ".env").exists():
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
URL = os.environ.get("RAG_API_URL", "")
TOK = os.environ.get("RAG_API_TOKEN", "")
if not URL:
    sys.exit("set RAG_API_URL (full /query endpoint)")

S100, S240, S400 = ("engineering_standards_S100", "engineering_standards_S240",
                    "engineering_standards_S400")


def q(query, collection, clause=None, top_k=6, doc_part=None):
    body = {"query": query, "collection": collection, "top_k": top_k}
    if clause:
        body["clause"] = clause
    if doc_part:
        body["doc_part"] = doc_part
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": "Bearer " + TOK} if TOK else {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
    return d.get("results", d if isinstance(d, list) else [])


def hit(results, want_clause=None, markers=(), spec_only=False):
    for r in results:
        txt = r.get("text", "")
        if want_clause and r.get("clause") != want_clause:
            continue
        if spec_only and r.get("doc_part") == "commentary":
            continue
        if markers and not any(m in txt for m in markers):
            continue
        return True
    return False


# (name, collection, clause-filter, query, want_clause, markers)
REGRESSION = [
    ("E1.3.1.1 Vn eq",        S400, "E1.3.1.1",   "Type I shear wall nominal strength", "E1.3.1.1", ["2w/h"]),
    ("E1.3.2 phi_v",          S400, "E1.3.2",     "resistance factor shear wall",       "E1.3.2",   ["0.60"]),
    ("E1.3.3 Omega_E",        S400, "E1.3.3",     "expected strength factor",           "E1.3.3",   ["finish"]),
    ("E1.4.2.1 TypeII limits",S400, "E1.4.2.1",   "Type II shear wall limitations",     "E1.4.2.1", ["4 in", "4 in."]),
    ("E1.4.2.2 anchorage",    S400, "E1.4.2.2.1", "Type II anchorage uplift",           "E1.4.2.2.1", []),
    ("B3.4 capacity design",  S400, "B3.4",       "capacity protected expected strength","B3.4",    []),
    ("E2.3.1.1 steel sheet",  S400, "E2.3.1.1",   "steel sheet Type I shear wall",      "E2.3.1.1", ["4:1"]),
    ("E2.3.1.1.1 eff strip",  S400, "E2.3.1.1.1", "effective strip method",             "E2.3.1.1.1", ["Effective Strip"]),
    ("E2.3.1.1.3 two-sided",  S400, "E2.3.1.1.3", "both faces same material",           "E2.3.1.1.3", ["adding", "sum"]),
    ("E2.3.2 phi",            S400, "E2.3.2",     "available strength steel sheet",     "E2.3.2",   ["0.60"]),
    ("E2.3.3 Omega_E sheet",  S400, "E2.3.3",     "expected strength steel sheathing",  "E2.3.3",   ["1.8"]),
    ("E2.4.1.1 limitations",  S400, "E2.4.1.1",   "tabulated system requirements",      "E2.4.1.1", ["blocking"]),
    ("E2.4.1.4 deflection",   S400, "E2.4.1.4",   "shear wall design deflection",       "E2.4.1.4", ["29.12", "omega", "ω"]),
    ("E3.3.1 strap Vn",       S400, "E3.3.1",     "strap braced wall nominal strength", "E3.3.1",   []),
    ("E3.3.3 strap Omega_E",  S400, "E3.3.3",     "strap expected strength factor",     "E3.3.3",   ["0.2"]),
    ("E3.4.1 strap conn",     S400, "E3.4.1",     "strap connection ductility methods", "E3.4.1",   ["1.2"]),
    ("E4.3.3 SBMF mech",      S400, "E4.3.3",     "bolted moment frame expected shear", "E4.3.3",   ["0.33"]),
    ("E6.3.2 gypsum phi",     S400, "E6.3.2",     "gypsum shear wall resistance factor","E6.3.2",   ["0.60"]),
    ("S100 E2 compression",   S100, "E2",         "flexural buckling nominal stress",   "E2",       ["0.658"]),
    ("S100 D2 tension",       S100, "D2",         "tension member yielding",            "D2",       ["0.90"]),
    ("S100 G5 crippling",     S100, "G5",         "web crippling nominal strength",     "G5",       ["G5-1"]),
    ("S100 H1.1 interaction", S100, "H1.1",       "combined tension bending",           "H1.1",     []),
    ("S100 J4.3.1 screws",    S100, "J4.3.1",     "screw shear tilting bearing",        "J4.3.1",   ["0.55"]),
    ("S100 J3.3.2 bolts",     S100, "J3.3.2",     "bolt bearing deformation",           "J3.3.2",   ["4.64"]),
    ("S100 J2.6 welds",       S100, "J2.6",       "fillet weld thin sheet",             "J2.6",     []),
    ("S100 I1.2.2 built-up",  S100, "I1.2.2",     "built-up compression interconnection","I1.2.2",  []),
    ("S100 I6.2.1 uplift R",  S100, "I6.2.1",     "through fastened uplift reduction",  "I6.2.1",   []),
    ("S100 F2.1 LTB",         S100, "F2.1",       "lateral torsional buckling regimes", "F2.1",     []),
    ("S100 H4 bimoment",      S100, "H4",         "bending torsion interaction",        "H4",       ["1.15"]),
    ("S100 G8.1 Bn",          S100, "G8.1",       "bimoment nominal strength",          "G8.1",     []),
    ("S240 sheathing braced", S240, "B1.2.2",     "sheathing braced design stud",       "B1.2.2",   []),
]

MUST_NOW_HIT = [
    ("H1.2 SPEC body",   S100, "H1.2", "combined compressive axial load and bending",
     dict(want_clause="H1.2", spec_only=True)),
    ("Table E2.3-1 cells", S400, "Table E2.3-1", "steel sheet nominal shear per unit length",
     dict(markers=["1305", "1070"])),
    ("Effective-strip we equation", S400, "E2.3.1.1.1", "effective strip width slenderness",
     dict(want_clause="E2.3.1.1.1", markers=["0.0819", "λ", "lambda"])),
    ("Table E1.3-1 cells clean", S400, "Table E1.3-1", "wood structural panel nominal shear",
     dict(markers=["2350", "1760"])),
    ("Ca location findable", S400, "E1.3.1.2", "shear resistance adjustment factor Ca",
     dict(want_clause=None, markers=["Ca", "adjustment"])),
]


def structure_tests():
    out = []
    # Table chunks retrievable DIRECTLY by clause tag (no part-walking)
    r = q("nominal shear strength", S400, clause="Table E1.3-1")
    out.append(("clause:'Table E1.3-1' direct", hit(r, markers=["940", "2350"])))
    # prefix matching returns children
    r = q("shear wall", S400, clause="E1.3")
    kids = {x.get("clause") for x in r}
    out.append(("prefix clause 'E1.3' returns children",
                any(k and k.startswith("E1.3.") for k in kids)))
    # doc_part filter honored
    r = q("expected strength", S400, clause="E1.3.3", doc_part="spec")
    out.append(("doc_part=spec filter", bool(r) and all(
        x.get("doc_part", "spec") == "spec" for x in r)))
    # glossary out of top-3 for the historically polluted phrasing
    r = q("flexural buckling stress Fn slenderness", S100, top_k=3)
    out.append(("glossary absent from top-3",
                not any((x.get("clause") in (None, "",) and x.get("section") in (None, ""))
                        for x in r)))
    return out


def main():
    baseline = "--baseline" in sys.argv
    fails = 0
    print("== REGRESSION (must ALWAYS pass) ==")
    for name, coll, cl, query, want, markers in REGRESSION:
        try:
            ok = hit(q(query, coll, clause=cl), want_clause=want, markers=markers)
        except Exception as e:
            ok = False
            name += "  [ERR %s]" % e
        print("  %-26s %s" % (name, "PASS" if ok else "FAIL"))
        fails += (not ok)
    print("== MUST-NOW-HIT (red pre-fix, green post-fix) ==")
    mn_fails = 0
    for name, coll, cl, query, kw in MUST_NOW_HIT:
        try:
            ok = hit(q(query, coll, clause=cl), **kw)
        except Exception:
            ok = False
        print("  %-26s %s" % (name, "PASS" if ok else ("MISS (expected pre-fix)"
                                                       if baseline else "FAIL")))
        mn_fails += (not ok)
    print("== STRUCTURE (Fix 1/2/4 behaviors) ==")
    st_fails = 0
    for name, ok in structure_tests():
        print("  %-36s %s" % (name, "PASS" if ok else ("MISS (expected pre-fix)"
                                                       if baseline else "FAIL")))
        st_fails += (not ok)
    total = fails + (0 if baseline else mn_fails + st_fails)
    print("\nRESULT: %s  (regression fails: %d; must-now-hit misses: %d; structure misses: %d)"
          % ("GREEN" if total == 0 else "RED", fails, mn_fails, st_fails))
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()

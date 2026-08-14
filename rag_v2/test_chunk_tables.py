"""Synthetic-md tests for the Fix 1/Fix 2 chunker changes (RAG_GAPS_FIX_SCOPE 2026-08-03).
Pure stdlib; run:  python3 rag_v2/test_chunk_tables.py
Covers: table emission w/ caption id, parent pointer, small-table non-emission,
symbol-glossary drop, frontmatter tagging, giant-table row-split w/ repeated header,
and the AISC-profile invariance gate (no emission, no drops, no frontmatter).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from chunk_v2 import chunk_markdown

ROW = "| 0.033 steel sheet | 2:1 | 1055 | 1170 | 1235 | 1305 |"
MD = """
| Symbol | Definition | Section |
|---|---|---|
| Pn | Nominal strength of screw connections | E2.3.1.1.1 |
| lam | Slenderness of compression element | A3.2.3 |

Some front-matter prose that is long enough to survive both the eighty-character floor
and, more importantly, the four-hundred-character tiny-segment merge threshold, so that
this preamble stands alone as its own untagged segment exactly the way the real S100 and
S400 reconstructions' front matter does (theirs runs to twenty-seven packed parts). This
prose must be KEPT after the glossary table above it is deleted, and it must carry the
doc_part tag "frontmatter" so the query API can exclude it from default searches.

## E2.3.1.1 Type I Shear Walls

For a Type I shear wall with steel sheet sheathing, the nominal strength for shear
shall be determined per Table E2.3-1 for the assemblies below, with enough prose here
to keep this clause chunk alive after its table is emitted separately.

Table E2.3-1
Nominal Shear Strength per Unit Length for Shear Walls with Steel Sheet Sheathing

| Assembly | Max AR | 6 | 4 | 3 | 2 |
|---|---|---|---|---|---|
| 0.030 steel sheet | 2:1 | 910 | 1015 | 1040 | 1070 |
%s

## E2.3.2 Available Strength

A tiny layout fragment follows that must NOT be emitted:

| a | b |
|---|---|
| 1 | 2 |

And this clause has enough prose to stand on its own after that non-emission, since
the two-line grid is layout debris rather than a capacity table for engineers.

## G5 Web Crippling

Giant table stress test with caption:

Table G5-2
Web Crippling Coefficients

| Support | C | CR | CN | Ch |
|---|---|---|---|---|
%s
""" % (ROW, "\n".join("| fastened row %d | 4 | 0.14 | 0.35 | 0.02 |" % i
                       for i in range(400)))


def run():
    ch = chunk_markdown(MD, "S400", "AISI S400-20")
    by_clause = {}
    for c in ch:
        by_clause.setdefault(c['clause'], []).append(c)

    # 1) Table E2.3-1 emitted, clause-tagged, caption included, has_table, values present
    t = by_clause.get("Table E2.3-1")
    assert t and len(t) == 1, "Table E2.3-1 not emitted exactly once: %s" % sorted(by_clause)
    assert t[0]['has_table'] and "1305" in t[0]['text'] and "Nominal Shear Strength" in t[0]['text']
    assert t[0]['doc_part'] == 'spec'

    # 2) parent clause survives with a pointer, table gone from it
    parent = by_clause.get("E2.3.1.1")
    assert parent, "parent clause chunk missing"
    ptxt = "\n".join(c['text'] for c in parent)
    assert "[Table E2.3-1 emitted as its own chunk]" in ptxt
    assert "1305" not in ptxt, "table body still in the parent"

    # 3) the 2-line fragment was NOT emitted and stayed in E2.3.2
    assert not any(k.startswith("Table") and "E2.3.2" in "".join(x['text'] for x in v)
                   for k, v in by_clause.items() if k != "Table E2.3-1" and k != "Table G5-2")
    e232 = "\n".join(c['text'] for c in by_clause.get("E2.3.2", []))
    assert "| 1 | 2 |" in e232, "small layout grid should stay in place"

    # 4) symbol glossary DROPPED; other front matter kept + tagged
    all_text = "\n".join(c['text'] for c in ch)
    assert "Slenderness of compression element" not in all_text, "glossary not dropped"
    fm = [c for c in ch if c['doc_part'] == 'frontmatter']
    assert fm and any("front-matter prose" in c['text'] for c in fm), "front matter lost/untagged"

    # 5) giant table row-split with repeated header + part crumbs
    g = by_clause.get("Table G5-2")
    assert g and len(g) >= 2, "giant table should split into parts"
    assert all("| Support | C |" in c['text'] for c in g), "header not repeated on parts"
    assert "(part 1/" in g[0]['text']

    # 6) AISC invariance: same md through the aisc profile -> NO emission/drops/frontmatter
    ca = chunk_markdown(MD, "A360", "AISC 360-22")
    assert not any(c['clause'].startswith("Table ") for c in ca), "AISC profile emitted tables"
    assert not any(c['doc_part'] == 'frontmatter' for c in ca), "AISC profile tagged frontmatter"
    atext = "\n".join(c['text'] for c in ca)
    assert "Slenderness of compression element" in atext, "AISC profile dropped glossary"
    assert "emitted as its own chunk" not in atext

    print("ALL CHUNKER TABLE/GLOSSARY TESTS PASS  (%d AISI chunks, %d AISC chunks)"
          % (len(ch), len(ca)))


if __name__ == '__main__':
    run()

# RAG quality — final addendum (2026-08-04)

> APPEND THIS to the archived `test_solutions_gold/RAG_QUALITY.md`. That document
> recorded the retrieval-quality findings of the 25-building gold pass; this addendum
> closes it out GREEN.

## Outcome

All retrieval gaps found during the gold pass are FIXED and verified against the
live store (`rag_v2/rag_acceptance.py`, gate mode, 2026-08-04):

- REGRESSION 31/31 PASS — every query that hit during the gold pass still hits.
- MUST-NOW-HIT 5/5 PASS — the confirmed-missing items now retrieve:
  - S100 **H1.2 spec body** (heading line had been dropped by conversion; body was
    intact and mis-chunked under H1.1 — heading hand-restored, provenance comment);
  - S400 **Table E2.3-1** steel-sheet cells (md was intact; old chunker buried it —
    now a standalone `Table E2.3-1` chunk, cells verified against the client-supplied
    image used during the gold pass);
  - S400 **E2.3.1.1.1 effective-strip we/λ equations** (destroyed by conversion;
    hand-transcribed from the licensed PDF, Eqs. E2.3.1.1.1-3 … -18);
  - S400 **Table E1.3-1** WSP cells (was a flattened run-on line; rebuilt as a grid,
    Structural-1 rows corrected against the licensed PDF — one value row
    890/1330/1775/2190 @ 2:1 shared by 43-or-54-mil №8 and 68-mil №10 options);
  - **Ca table** (E1.3.1.2-1) findable (rebuilt from the file's intact E2 copy).
- STRUCTURE 4/4 PASS — tables retrievable directly via `clause="Table X"`; clause
  PREFIX matching (`"E1.3"` → E1.3.x); `doc_part` filter honored; symbol-glossary
  chunks out of top-3 on the historically polluted phrasings.

## What changed (summary; full scope in `rag_v2/RAG_GAPS_FIX_SCOPE.md`)

1. **Chunker** (`rag_v2/chunk_v2.py`, AISI profile only — AISC byte-identical):
   tables emitted as standalone caption-tagged chunks with parent pointers and
   correct `has_table`/`has_equation`; giant tables row-split with repeated headers;
   symbol glossaries dropped; front matter tagged `doc_part: frontmatter`.
2. **Source md repairs** (VM, licensed content off-git): H1.2 heading, Table E1.3-1
   grid, Ca grid, we/λ block — each marked `<!-- hand-restored 2026-08 … -->`.
3. **Query API** (`local_api_v2.py` on the VM): clause prefix matching (dot-boundary;
   `Table …` exact), `doc_part` request filter, spec-first tie-break (ε=0.015),
   frontmatter excluded by default. Clause/doc_part filtering fetches depth-1000
   before post-filtering — a shallow fetch starved narrow clauses (caught by this
   harness on first run, fixed same day).
4. **Re-embed**: S100 (980) + S240 (484) + S400 (402) = 1,866 chunks.

## Standing notes

- Gold packages Ex3/Ex4/Ex7/Ex12 keep their "table missing from RAG at solve time,
  client-supplied image used" flags — accurate history, no regeneration needed; the
  store now agrees with the values they carried.
- `cfs_design_examples` remains EMPTY (Fix 5 deferred by owner — to be authored from
  the archived gold set). Contract language unchanged: probe once, empty is normal.
- S310/S220 remain absent BY DESIGN (test feature).
- Repeatable gate: `python3 rag_v2/rag_acceptance.py` (tunnel:
  `RAG_API_URL=http://localhost:8080/query`). Run after any future re-ingest.

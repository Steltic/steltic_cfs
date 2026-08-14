# CFS spec re-ingest runbook — S100 / S240 / S400

Scope + step-by-step for rebuilding the spec RAG the *v2 way* (clause-aware chunks, breadcrumbs,
clause/chapter pinpoint filters) for the CFS code set. Mirrors what was done for A360/A341/A358:
**reuse the existing PDF→MD conversions; only re-chunk + re-embed.** No fresh conversions are
needed. (Storage racks / RMI MH16.1 were DESCOPED 2026-07-30 — do not convert or ingest MH16.)

**Post-ingest fixes (2026-07-30 evening, found by the G5 smoke test):** two further chunker
defects surfaced against the real reconstructions and are fixed in `chunk_v2.py`:
(a) clause codes bold-wrapped INSIDE headings (`# **G5** Web Crippling…`) defeated the clause
regex — inline `**` runs are now stripped from heading cores; (b) the tiny-segment forward-merge
could swallow a whole DIFFERENTLY-TAGGED section when Marker duplicated a stub heading with a
one-line body (S100's `#### G4.2` stub ate all of G5) — the merge now never crosses a clause
boundary. The boundary fix un-swallowed ~230 sections across the three standards
(S100 947→1048, S240 361→481, S400 304→408 chunks). Both defects were standard-agnostic — a
future A360/A341/A358 re-ingest benefits identically.

Code changes already in place (2026-07-30, tested):
- `chunk_v2.py` — per-standard profiles gated by std code, **A360/A341/A358 output verified
  byte-identical**: (a) S100/S240/S400 lettered clauses matched already; numeric clauses are now
  ALSO tagged inside appendices (S100 App. 1 = the EWM provisions → clause "1.1", chapter APP1);
  (b) equation refs extended (E4.1.1-2 / 1.1-3 now land in `eqs` metadata);
  (c) fixed a latent `_pack` crash (`out[-2] += out.pop()` — fires on any 2-part clause with a
  tiny tail; the CFS docs WILL hit it);
  (d) new dry-run CLI: `python3 chunk_v2.py md/S100.md S100 "AISI S100-16 (R2020)"` prints chunk
  count, size percentiles, % clause-tagged, chapters seen, and the 5 biggest UNTAGGED chunks.
- `reingest_v2.py` — DISPLAY names for stems S100/S240/S400.

## Collection-name contract (must match exactly)
The agent, completion gates, and report Chapter 13 key on:
`engineering_standards_S100`, `engineering_standards_S240`, `engineering_standards_S400`
(+ `cfs_design_examples`, `cfs_opensees_models`).
`reingest_v2.py` derives the collection from the **md file stem** → the files MUST be named
`S100.md`, `S240.md`, `S400.md`. The old ingest used `AS100`-style stems — **rename, don't copy**
(a leftover `AS100.md` would waste an embedding run into a collection nobody queries).
Deliberately ABSENT: S310 and S220 (the contract instructs the agent to flag the S310 gap — the
rubric's Ex10 harness note) and MH16 (racks descoped). Do not ingest them without updating the
contract language.

## Steps (on the RAG deployment box, `~/rag`)

1. **Inventory the existing conversions.** `ls md/` — expect `AS100.md`, `AS240.md`, `AS400.md`
   from the first (inefficient) ingest. Those conversions are REUSABLE: the waste was in the old
   heading-only chunking, not the Marker output. Rename:
   `mv md/AS100.md md/S100.md && mv md/AS240.md md/S240.md && mv md/AS400.md md/S400.md`
2. **S2/S3 supplements (S100).** If the supplements were converted separately, append them to the
   base file so chunking sees one document: `cat md/AS100_S2.md md/AS100_S3.md >> md/S100.md`.
   If they were never converted, convert with the same Marker recipe first. (Supplement chunks
   tag as `doc_part: spec` under their own clause codes — desired: a query for a clause the
   supplement amended returns both texts, and the breadcrumb says which document part it is.)
3. **Dry-run each standard BEFORE embedding** (fast, pure stdlib, catches conversion damage):
   ```
   python3 chunk_v2.py md/S100.md S100   # then S240, S400
   ```
   Acceptance per standard — don't embed until these hold:
   - clause-tagged fraction ≥ ~80% (A360-v2 landed ~85–90%); untagged list shows only front
     matter / user notes, not runs of provisions;
   - chapters listed match the real TOC (S100: A–M + APP1[+APP2 if present]; S400: A–H-ish with
     E1–E5 sections visible as clauses);
   - S100 spot: clauses `E2`, `E3`, `E4`, `G5`, `1.1` present; S400: `E1`…`E5` sub-clauses;
   - size percentiles in the ~1–3.5k band (same as the A360 v2 profile).
   If a heading style is missed (Marker sometimes renders clause heads as plain bold or ALL
   CAPS), fix the regex or the MD, re-run the dry-run — never embed a bad chunking.
4. **Re-ingest** (recreates each collection fresh; ~minutes per standard at EMBED_BATCH=8):
   ```
   venv/bin/python reingest_v2.py S100 S240 S400
   ```
5. **Smoke-test the live API** with the queries the contract actually teaches the agent
   (each should return the right clause in the top hits; test the pinpoint filters too):
   ```
   S100:  "web crippling one-flange loading"                 + clause=G5
   S100:  "effective width uniformly compressed stiffened"   + clause=1.1
   S100:  "distortional buckling compression"                + clause=E4
   S240:  "built-up chord stud interconnection fastener spacing"
   S400:  "Type II perforated shear wall adjustment factor"
   S400:  "strap braced wall expected strength Ry Fy Ag"     + chapter=E
   ```
   Also verify a collection the server does NOT have (e.g. `engineering_standards_S310`) returns
   an **empty results list**, not an error — the agent depends on that soft-fail.
6. **Leave the AISC collections alone.** The A360/A341/A358 collections stay live (harmless) or
   can be deleted later; nothing in the CFS fork queries them, and the grounding check red-flags
   any AISC citation on a CFS design.

## Also queued for the same session (not spec chunking)
- `cfs_design_examples` — worked CFS Q&A via `chunk_examples.py`/`reingest_examples.py`
  (collection name changes from `steel_design_examples`; content authoring is the long pole).
- `cfs_opensees_models` — validated CFS reference models (wall-line stacks, portals), same jsonl
  embed+upsert path as `rag_update/*.jsonl`.
- SFIA **general notes / footnotes** (bearing conditions, punchout assumptions, K-factors behind
  the tables) → chunk into the RAG per scope §4a; the tables themselves are already extracted as
  DATA (`steel_engine/extract_sfia.py`), not RAG.

---

## RAG-gaps fix pass (2026-08-03) — ADDITIONAL steps for the next re-ingest

Companion: `RAG_GAPS_FIX_SCOPE.md`. Everything repo-side is BUILT AND TESTED; the VM
steps below are what remains. Baseline acceptance already recorded GREEN on the live
index (31/31 regression; the 4 known misses + 3 structure misses expected pre-fix).

**New chunker behavior (already in `chunk_v2.py`, AISI-gated, AISC byte-identical —
`test_chunk_tables.py` passes):**
- every capacity table is emitted as its OWN chunk, clause-tagged `Table <id>` from its
  caption, `has_table: True`, caption prepended for the embedding; parent keeps a
  pointer line; giant tables row-split with the header repeated per part;
- untagged front matter is tagged `doc_part: "frontmatter"`; symbol-glossary tables
  in it are deleted (block-level).

**VM sequence (insert between the existing steps 2 and 3):**

2b. **Apply the md repairs** from `rag_v2/md_repairs/` (carry the folder over — it is
    gitignored, licensed content): H1.2 skeleton into S100.md (TRANSCRIBE the
    equations), Table E2.3-1 grid into S400.md (cells pre-verified vs the client
    image), the E2.3.1.1.1 we-block (TRANSCRIBE — do not trust memory), the
    E1.3.1.1.3 / Table E1.3-1 rebuild, and the E1.3.1.2 Ca pointer.
2c. **Patch the query API**: copy `rag_v2/vm_patches/local_api_prefix_docpart.py` to
    `~/rag/` and integrate per its docstring (clause prefix match, `doc_part` filter,
    spec-first tie-break, frontmatter exclusion). `python3 local_api_prefix_docpart.py`
    self-tests green; do NOT touch `ctl_plane.py`; restart `steel-rag.service` and
    check `/healthz/rag` before continuing.

Step 3 dry-run acceptance additions:
- `EMITTED TABLE CHUNKS` must list `Table E1.3-1`, `Table E2.3-1`, `Table E4.3.3-1`,
  `Table E6.3-1` (S400) and `Table G5-2`-class, `Table I6.2.1-1` (S100);
- clause `H1.2` present with doc_part spec;
- frontmatter-tagged count > 0 and the untagged-chunks list free of symbol tables.

Step 6 (new final gate): **`python3 rag_v2/rag_acceptance.py`** (gate mode, no
`--baseline`) with RAG_API_URL/TOKEN set — must print GREEN. Only then update
`contract/AGENT_START.md` (drop the clause-enumeration workaround + the H1.2/E2.3-1
known-gaps note; document `clause:"Table X"` + prefix + `doc_part`) and close out the
archived RAG_QUALITY.md.

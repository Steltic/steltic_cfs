# RAG gaps — final fix scope (2026-08-03)

**STATUS 2026-08-04 (FINAL): GATE GREEN.** Fixes 1–4 + 6 DEPLOYED — md repairs
uploaded (we/λ transcribed + E1.3-1 rows verified from owner-supplied PDF pages),
API patched (prefix/doc_part/spec-first; clause-filtered fetch deepened to 1000
after the first gate run exposed shallow-fetch starvation on narrow clauses),
S100/S240/S400 re-embedded (1,866 chunks). `rag_acceptance.py` gate: 31/31
regression, 5/5 must-now-hit, 4/4 structure. Close-out done: AGENT_START retrieval
guidance simplified; archive addendum at `rag_v2/RAG_QUALITY_ADDENDUM_2026-08-04.md`.
Fix 5 (design examples) remains DEFERRED by owner decision.

Closes out the retrieval-quality findings from the 25-building gold pass (three probe
passes, logged in the archived `test_solutions_gold/RAG_QUALITY.md` + `rag_cache/`,
moved out of the repo with the gold set). Work splits between THIS REPO (chunker +
authoring + acceptance harness) and THE RAG VM (`~/rag`: md repairs, API, re-embed).
Companion runbook: `REINGEST_CFS.md` (unchanged steps still apply; this doc adds to it).

---

## 1 · Gap register (definitive)

### A — Missing spec bodies (ingestion drops; content absent from the store)
| id | item | evidence / impact |
|---|---|---|
| A1 | **S100 H1.2 spec chunk** (combined axial + bending interaction body) | commentary present, spec absent on every phrasing incl. clause-filtered; every portal gold (Ex16/17/18/19/26/27/28/30) carries the linear H1.1 form with a flag |
| A2 | **S400 Table E2.3-1** (steel-sheet vn cells) | table body reduced to a 382-char fragment host; cells unrecoverable. **Ground truth now in hand:** client-supplied table image (2026-08-03 chat) + transcribed cells carried in gold packages Ex3/Ex4/Ex7/Ex12 |
| A3 | **S400 E2.3.1.1.1 Effective-Strip `we` equation block** | `$$\begin{array}…` collapsed by conversion — the analytical alternative to A2 is unusable |

### B — Structure/ranking defects (content present but hard or risky to find)
| id | item |
|---|---|
| B1 | Capacity **tables ride inside sub-clause part-chunks** and rank poorly for direct queries — found only via the clause-enumeration + part-walking technique. Named sufferers: E1.3-1, A3.2-1, I6.2.1-1, G5-1..5, E4.3.3-1/-2, E6.3-1, E1.3.1.2-1 |
| B2 | `has_table` / `has_equation` flags wrong on table-bearing parts (e.g. G5 parts 2/4/6 titled, 5/7 hold the bodies, all `has_table: False`) |
| B3 | **Untagged S100 symbol-glossary chunks** (empty clause/section) outrank spec on symbol-heavy phrasings ("flexural buckling Fn", "effective strip lambda") |
| B4 | Two **flattened tables** from conversion: E1.3.1.1.3's WSP cells (run-on digits) and the Type-II Ca table inside E1 (clean copy exists in E2 commentary — converter is page-dependent, not broken) |

### C — Server-side conveniences (`local_api_v2.py` on the VM)
| id | item |
|---|---|
| C1 | Clause filter is **exact-leaf-only** — `clause:"E1.3"` returns nothing for E1.3.x |
| C2 | No `doc_part` filter/boost — commentary outranks spec on ~⅓ of equation queries; agents over-fetch and filter client-side |

### D — Content authoring / scope decisions
| id | item |
|---|---|
| D1 | **`cfs_design_examples` is EMPTY** (probes return nothing; agents told to probe once per building). The 25 gold solutions now exist as authoring source — mirror the AISC `steel_design_examples` (168 Q&A) pattern |
| D2 | S310/S220 absence is a **designed test feature** (contract instructs flagging the gap; Ex10/Ex4 deck flags are rubric items) — **DECISION: keep absent**, no change |
| D3 | ASCE 7 out of scope by design. If ever added, highest-value for this test set: Ch.7 snow, 12.2.2/12.2.3, Ch.26–30 wind. **DECISION: defer** (recorded, not scoped) |

---

## 2 · Fixes

### Fix 1 — table-emission pass in `chunk_v2.py` (repo) → closes B1, B2
Every markdown table (≥ 2 body rows) inside a clause chunk is emitted as its **own chunk**:
- `clause` = `"Table X"` parsed from the caption/title line (fallback: parent clause + `-tbl<n>`),
  `chapter` inherited, `has_table: True`, `doc_part` inherited;
- a one-line prose header (caption + parent breadcrumb) prepended for embedding;
- the parent chunk keeps a one-line pointer (`[Table X emitted as its own chunk]`) so prose
  context still mentions it;
- `has_table`/`has_equation` computed on the **emitted** bodies (fixes B2 for free);
- gate by std profile like the other CFS changes — **A360/A341/A358 output must stay
  byte-identical** (same discipline as the 2026-07-30 chunker fixes; verify with the existing
  byte-identity check before touching the AISC collections).
Dry-run acceptance: `chunk_v2.py md/S400.md S400` lists emitted tables; the 11 named tables in
B1 each appear as standalone `Table X` chunks.

### Fix 2 — glossary tagging in `chunk_v2.py` (repo) → closes B3
Symbol-glossary / front-matter pages (the untagged `(part i/27)` chunks) get
`doc_part: "frontmatter"` and are **excluded from default search** server-side (see Fix 4) or
dropped at ingest (simpler; nothing ever wanted them). Dry-run: the "5 biggest UNTAGGED chunks"
listing shows none of the symbol tables remaining untagged.

### Fix 3 — md repairs on the VM (licensed PDFs in hand) → closes A1, A2, A3, B4
Re-run Marker on just the damaged pages; where Marker reproduces the damage, **hand-transcribe
into the md** from the licensed copies (all three are small), marked with an HTML comment
(`<!-- hand-restored 2026-08 -->`) for provenance:
- A1: S100 H1.2 body (short interaction clause + equations);
- A2: S400 Table E2.3-1 — transcribe **verified against the client-supplied image**; the gold
  packages' row values are the cross-check (0.018/0.027×2/0.030×2/0.033×4 rows, No.8/No.10,
  33/43/54-mil columns);
- A3: the `we`/λ equation block of E2.3.1.1.1;
- B4: E1.3.1.1.3 WSP cells + the E1 copy of the Ca table (or point the E1 text at the clean E2
  copy with a pointer line — cheaper and honest).
Keep the table image and any transcription sources **off git** (same policy as the chunk
stores: licensed content stays on the VM).

### Fix 4 — API conveniences in `local_api_v2.py` (VM) → closes C1, C2
- `clause` filter matches **prefix on dot boundaries** (`"E1.3"` → E1.3, E1.3.1, E1.3.1.1…;
  `"Table E1.3-1"` stays exact). Backward compatible — existing exact-leaf queries unaffected.
- optional `doc_part` request param (`"spec" | "commentary"`), plus a spec-first tie-break when
  scores are within ε and no `doc_part` was given; `frontmatter` excluded unless asked for.
- `ctl_plane.py` untouched (remember: a syntax error there takes the whole service down —
  change only `local_api_v2.py`, restart `steel-rag.service`, `/healthz/rag` green before
  proceeding).

### Fix 5 — author `cfs_design_examples` (repo script + VM ingest) → closes D1
New generator `rag_v2/author_examples.py`: reads the **archived gold set** (each
`calc_package_cfs.json` + `solution_notes.md`) and drafts 6–10 Q&A chunks per building
(~150–250 total): system/R selection, the capacity-design chain with its Ω-values, the
governing-hazard answer, the brief's designed trap and its resolution, key clause pointers.
Original prose (paraphrase, no verbatim spec text — same copyright posture as the AISC 168).
Human skim before ingest (`reingest_examples.py` path already exists). Update the agent
contract's "probe once, expect empty" language once live.

### Fix 6 — acceptance harness `rag_v2/rag_acceptance.py` (repo) → proves the above
Replays the assessment as a regression suite against `$RAG_API_URL`:
1. the ~60 cached probe queries from the archived `rag_cache/` (ships as a JSONL copied from
   the archive; the harness asserts each previously-HIT query still hits — no regressions);
2. the known-miss list MUST now hit: H1.2 spec; `Table E2.3-1` cells (assert a known cell
   string, e.g. "1305"); the `we` equation (assert "0.0819" or the λ symbol line); E1.3.1.1.3
   cells; Ca table in E1;
3. structure asserts: `clause:"Table E1.3-1"` returns the table directly (no part-walking);
   glossary chunks absent from top-3 for the two known polluted phrasings; prefix filter
   returns children; `doc_part:"spec"` honored.
Exit non-zero on any failure → this is the green gate for the re-embed.

### Re-embed
Per `REINGEST_CFS.md` steps 3–6, S100/S240/S400 only (conversions reused except the repaired
pages). Dry-run gates first, then embed, then Fix-6 green, then delete nothing until green.

---

## 3 · Sequencing & effort

| step | where | effort | gate |
|---|---|---|---|
| 1. Fix 1 + 2 in `chunk_v2.py` + dry-runs on current md | repo | 0.5–1 day | dry-run acceptance + AISC byte-identity |
| 2. Fix 3 md repairs | VM | 0.5 day | dry-run shows the repaired clauses/tables tagged |
| 3. Fix 4 API | VM | 1–2 h | `/healthz/rag` green + manual prefix query |
| 4. Re-embed ×3 | VM | machine time | — |
| 5. Fix 6 harness green | repo→VM | 0.5 day (build) | **all asserts pass** |
| 6. Fix 5 examples authoring + ingest | repo→VM | ~1 day incl. skim | probe returns authored hits |
| 7. Doc close-out | repo | 1 h | — |

Step 6 is independent of 1–5 and can run in parallel after the harness exists.

## 4 · Doc/contract touches after green
- `contract/AGENT_START.md`: replace the clause-enumeration workaround guidance with
  "query `clause:'Table X'` directly"; delete the H1.2/E2.3-1 known-gaps note; update the
  `cfs_design_examples` probe-once language.
- Archived `RAG_QUALITY.md`: final addendum with the harness results (the document is the
  assessment deliverable — it should end green).
- `rag_v2/README.md`: document the Table-chunk convention + `doc_part`/prefix filters for
  self-hosters.
- Golds need **no regeneration**: the flagged carried-cell entries in Ex3/4/7/12 remain
  accurate history ("table missing from the RAG at solve time, client-supplied image used").

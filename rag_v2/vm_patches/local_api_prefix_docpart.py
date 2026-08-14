"""Fix 4 (RAG_GAPS_FIX_SCOPE 2026-08-03) -- drop-in helpers for ~/rag/local_api_v2.py.

Three query-side changes, all backward compatible:

  C1  clause PREFIX matching  -- clause "E1.3" now matches E1.3, E1.3.1, E1.3.1.1...
                                 (dot-boundary only: "E1.3" does NOT match "E1.30");
                                 "Table E1.3-1" stays an exact match.
  C2a doc_part request param  -- optional "doc_part": "spec" | "commentary" filter.
  C2b spec-first tie-break    -- when no doc_part is given and a spec and commentary
                                 chunk score within EPS, the spec chunk ranks first.
  +   frontmatter exclusion   -- chunks tagged doc_part == "frontmatter" (new chunker
                                 output) are excluded unless explicitly requested.

INTEGRATION (local_api_v2.py -- do NOT touch ctl_plane.py; a syntax error there takes
the whole service down):

1. copy this file to ~/rag/ and `from local_api_prefix_docpart import (
       clause_matches, docpart_allowed, spec_first)`

2. wherever the /query handler post-filters hits on the clause field, e.g.:
       hits = [h for h in hits if h.payload.get("clause") == req_clause]
   replace the equality with:
       hits = [h for h in hits if clause_matches(req_clause, h.payload.get("clause"))]

3. add the doc_part filter next to it (reading the new optional request key):
       req_part = (body.get("doc_part") or "").strip().lower() or None
       hits = [h for h in hits if docpart_allowed(req_part, h.payload.get("doc_part"))]

4. after scoring/sorting, apply the tie-break:
       hits = spec_first(hits, score_of=lambda h: h.score,
                         part_of=lambda h: h.payload.get("doc_part"))

5. restart + verify:
       sudo systemctl restart steel-rag.service
       curl .../query -d '{"query":"x","collection":"engineering_standards_S400",
                          "clause":"E1.3","top_k":5}'    # must return E1.3.x children
   then check /healthz/rag from the app is green before calling it done.

If the Qdrant search uses a server-side payload filter for clause (rather than a
python post-filter), drop the server-side clause condition and always post-filter
with clause_matches.

FETCH DEPTH (learned the hard way, 2026-08-04 gate run): a shallow headroom like
4x top_k is NOT enough -- the old server-side filter ranked WITHIN the clause,
so a post-filter over a shallow global fetch starves any narrow clause whose
chunks don't crack the global top-N (five regression probes + the Table E1.3-1
lookups failed exactly this way). Deployed fix: fetch deep when filtering --

    limit=1000 if (req.clause or req.doc_part) else max(req.top_k * 2, 10),

then trim to top_k AFTER clause/doc_part filtering + spec_first. 1000 exceeds
every collection here (<=980 chunks), making post-filtering equivalent to true
in-clause ranking. The unfiltered 2x headroom only covers frontmatter exclusion.
"""

EPS = 0.015     # score window treated as a tie for the spec-first re-rank


def clause_matches(requested, chunk_clause):
    """True if the chunk's clause tag satisfies the requested clause filter.
    - no filter -> everything passes (frontmatter still excluded via docpart_allowed)
    - 'Table X' requests are EXACT (case-insensitive)
    - otherwise: exact match OR dot-boundary prefix (E1.3 -> E1.3.1, not E1.30)
    """
    if not requested:
        return True
    if not chunk_clause:
        return False
    req = str(requested).strip()
    got = str(chunk_clause).strip()
    if req.lower().startswith("table"):
        return got.lower() == req.lower()
    if got == req:
        return True
    return got.startswith(req + ".")


def docpart_allowed(requested_part, chunk_part):
    """doc_part filter + frontmatter default-exclusion.
    - explicit request ('spec'/'commentary'/'frontmatter'): exact match
    - no request: spec + commentary pass, 'frontmatter' is EXCLUDED
    """
    part = (chunk_part or "spec").strip().lower()
    if requested_part:
        return part == requested_part
    return part != "frontmatter"


def spec_first(hits, score_of, part_of):
    """Stable re-rank: within any run of hits whose scores sit inside EPS of the
    run's leader, spec chunks come before commentary. Order across runs unchanged."""
    out, i, n = [], 0, len(hits)
    while i < n:
        j = i + 1
        lead = score_of(hits[i])
        while j < n and abs(score_of(hits[j]) - lead) <= EPS:
            j += 1
        run = list(hits[i:j])
        run.sort(key=lambda h: 0 if (part_of(h) or "spec") == "spec" else 1)
        out.extend(run)
        i = j
    return out


if __name__ == "__main__":
    # self-test (pure stdlib)
    assert clause_matches("E1.3", "E1.3")
    assert clause_matches("E1.3", "E1.3.1.1")
    assert not clause_matches("E1.3", "E1.30")
    assert not clause_matches("E1.3", "E13")
    assert clause_matches("Table E1.3-1", "Table E1.3-1")
    assert not clause_matches("Table E1.3-1", "Table E1.3-11")
    assert not clause_matches("Table E1.3", "Table E1.3-1")   # table requests exact
    assert clause_matches("", "anything") and clause_matches(None, None)
    assert docpart_allowed(None, "spec") and docpart_allowed(None, "commentary")
    assert not docpart_allowed(None, "frontmatter")
    assert docpart_allowed("frontmatter", "frontmatter")
    assert docpart_allowed("spec", None)          # legacy chunks default to spec
    H = [("a", .90, "commentary"), ("b", .895, "spec"), ("c", .80, "commentary"),
         ("d", .60, "spec")]
    r = spec_first(H, score_of=lambda h: h[1], part_of=lambda h: h[2])
    assert [x[0] for x in r] == ["b", "a", "c", "d"], r
    print("local_api patch helpers: ALL SELF-TESTS PASS")

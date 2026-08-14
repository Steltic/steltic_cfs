"""
chunk_v2.py -- clause-aware chunker for the engineering-standards markdown (AISC / AISI)
reconstructed in ./md/<STD>.md. Replaces the heading-only `chunk_by_section` splitter.

Implements review recommendations 2-6:
  2. clause-aware splitting + a breadcrumb prepended to every chunk + clause/chapter/eqs metadata
     (so the embedding AND the agent know exactly which clause it is -- fixes e.g. F2-vs-I3 confusion)
  3. size bounds: merge orphan/tiny segments forward, split overlong clauses on SAFE block boundaries
  4. atomic tables & equations (never split inside a $$...$$ block or a markdown table)
  5. spec vs commentary tagging (doc_part)
  6. operates on the full reconstructed MD, so there are no 50-page-part boundary artifacts

CFS profiles (2026-07-30, gated by std_code so A360/A341/A358 chunking is BYTE-IDENTICAL):
  * S100/S240/S400 (AISI): lettered clauses already match; ADDITIONALLY, numeric clauses ("1.1",
    "1.1.1") are recognized INSIDE an appendix (S100 App. 1 = the EWM provisions, the most-queried
    content of the whole CFS RAG) and tagged with chapter=APP1.
  * equation refs: deep/numeric eq codes ((E4.1.1-2), (1.1-3)) captured in `eqs` metadata for
    every standard (payload-only; chunk boundaries unaffected).
  (Storage racks / RMI MH16.1 were descoped 2026-07-30; the numeric machinery serves S100 App. 1.)

Dry-run CLI (run BEFORE embedding, sanity-checks a converted MD):
    python3 chunk_v2.py md/S100.md S100 "AISI S100-16 (R2020)"

Pure stdlib -- no embeddings here. `reingest_v2.py` calls chunk_markdown() then embeds + upserts.
"""
import re

TARGET_CHARS = 2200      # aim for ~550 tokens
MAX_CHARS    = 3500      # split any clause longer than this
MIN_CHARS    = 400       # merge any segment shorter than this into the next (kills orphan headings)
HARD_CHARS   = 6000      # even an "atomic" block (giant table / TOC) is line-split above this

_H    = re.compile(r'^(#{1,6})\s+(.*\S)\s*$')        # markdown heading line
_BOLD = re.compile(r'^\*\*(.+?)\*\*[:.]?\s*$')        # bold-only line (Marker renders many clause heads bold)
_SEC  = re.compile(r'^([A-N]\d+(?:\.\d+)*[a-z]?)[.\s)—:-]')   # chapter-section code: F2, F2.1, B4.1, A3
_SUB  = re.compile(r'^(\d+[a-z]?)[.)]\s')             # local sub-number under a section: "1.", "2.", "1a."
_CHAP = re.compile(r'^CHAPTER\s+([A-N])\b', re.I)
_APPX = re.compile(r'^APPENDIX\s+(\d+|[A-N])\b', re.I)
_COMM = re.compile(r'^#{1,2}\s+\**\s*COMMENTARY\b', re.I)   # the real Commentary division (top-level heading, latter half)

_NUMSEC  = re.compile(r'^(\d{1,2}(?:\.\d+)+)[.\s)—:-]')     # numeric clause: 1.1, 1.1.1 (App. 1)

_TAG   = re.compile(r'\\tag\{([^}]+)\}')
_EQREF = re.compile(r'\(((?:[A-N]\d+|\d+)(?:\.\d+)*-\d+[a-z]?)\)')   # E2-1, E4.1.1-2, 1.1-3
_TBLREF= re.compile(r'Table\s+([A-N]?\d+(?:\.\d+)*[a-z]?)', re.I)
_IMG   = re.compile(r'!\[[^\]]*\]\([^)]*\)')          # Marker image placeholders -> drop


def _heading_core(line):
    """De-noised heading text if `line` is heading-like (markdown #, bold, or ALL-CAPS title), else None."""
    s = line.strip()
    m = _H.match(s)
    if m:
        s = m.group(2)
    elif _BOLD.match(s):
        s = _BOLD.match(s).group(1)
    elif s.isupper() and 3 <= len(s) <= 95 and not s.startswith('|'):
        pass
    else:
        return None
    s = s.strip().strip('*').strip()
    # kill INLINE bold markers -- "**G5** Web Crippling" otherwise defeats the clause regex
    # (and backtracks "**G4.2**" into a bogus plain-G4 match that swallows the section)
    return re.sub(r'\*{2,}', ' ', s).strip()


def _meta(text):
    eqs  = sorted(set(_TAG.findall(text)) | set(_EQREF.findall(text)))
    tbls = sorted(set(t for t in _TBLREF.findall(text) if any(c.isdigit() for c in t)))
    has_eq  = ('$$' in text) or ('\\tag{' in text) or ('\\[' in text)
    has_tbl = ('|---' in text) or ('| ---' in text) or bool(re.search(r'\n\s*\|[^\n]+\|[^\n]+\|', text))
    return eqs, tbls, has_eq, has_tbl


def _hardsplit(block):
    """Last-resort line-split for a pathologically large block (TOC, giant material table)."""
    out, cur, n = [], [], 0
    for ln in block.split('\n'):
        if cur and n + len(ln) > TARGET_CHARS:
            out.append('\n'.join(cur)); cur, n = [], 0
        cur.append(ln); n += len(ln) + 1
    if cur:
        out.append('\n'.join(cur))
    return out


def _blocks(body):
    """Split a body into ATOMIC blocks: $$..$$ equations and |..| tables are indivisible; prose splits
    on blank lines."""
    lines = body.split('\n')
    blocks, buf, i = [], [], 0
    def flush():
        if buf:
            t = '\n'.join(buf).strip()
            if t:
                blocks.append(t)
            buf.clear()
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith('$$'):                       # equation block -> to closing $$
            flush()
            if ln.count('$$') >= 2:
                blocks.append(ln); i += 1; continue
            eq = [ln]; i += 1
            while i < len(lines):
                eq.append(lines[i]); done = '$$' in lines[i]; i += 1
                if done:
                    break
            blocks.append('\n'.join(eq)); continue
        if ln.strip().startswith('|'):                        # table block
            flush(); tb = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                tb.append(lines[i]); i += 1
            blocks.append('\n'.join(tb)); continue
        if ln.strip() == '':
            flush(); i += 1; continue
        buf.append(ln); i += 1
    flush()
    out = []
    for b in blocks:
        out += _hardsplit(b) if len(b) > HARD_CHARS else [b]
    return out


def _pack(blocks, target=TARGET_CHARS, hard=MAX_CHARS):
    """Greedily pack atomic blocks into sub-chunks ~target chars, never splitting a block."""
    out, cur, n = [], [], 0
    for b in blocks:
        if cur and n + len(b) > target:
            out.append('\n\n'.join(cur)); cur, n = [], 0
        cur.append(b); n += len(b) + 2
        if n >= hard:
            out.append('\n\n'.join(cur)); cur, n = [], 0
    if cur:
        out.append('\n\n'.join(cur))
    if len(out) >= 2 and len(out[-1]) < MIN_CHARS:        # fold a tiny tail back into the previous part
        tail = out.pop()                                   # (pop FIRST: `out[-2] += out.pop()` shrinks the
        out[-1] += '\n\n' + tail                           #  list before the store -> IndexError on 2 parts)
    return out


def _profile(std_code):
    """('aisi' | 'aisc'): which clause-numbering conventions to recognize."""
    c = (std_code or "").upper()
    return "aisi" if c.startswith(("S1", "S2", "S3", "S4", "AS")) else "aisc"


# ---- Fix 1 (2026-08-03, RAG_GAPS_FIX_SCOPE): TABLE EMISSION (AISI profile only) ----
# The gold-pass retrieval assessment found every capacity table (E1.3-1, A3.2-1,
# I6.2.1-1, G5-1..5, E4.3.3-1/-2, E6.3-1, ...) riding inside whichever sub-clause
# part-chunk preceded it, rankable only via clause-enumeration + part-walking. Every
# markdown table with a real body now becomes ITS OWN CHUNK, clause-tagged
# "Table <id>" from its caption, has_table=True, with a prose caption header for the
# embedding; the parent keeps a one-line pointer. Gated to prof=='aisi' so
# A360/A341/A358 output stays BYTE-IDENTICAL.
_TBLCAP = re.compile(r'\bTABLE\s+([A-N]?\d+(?:[.\-]\d+)*[a-z]?)', re.I)
_SYMHDR = re.compile(r'^\|\s*(?:Symbol|λ|\\lambda)\s*\|', re.I)
MIN_TBL_CHARS = 120      # smaller |...| runs are layout debris, not capacity tables


def _extract_tables(body, drop_glossary=False):
    """Split `body` into (parent_body, [ (tab_id|None, caption_lines, table_md) ]).
    A table = a run of '|' lines with >= 2 body rows. Its caption = the nearest run of
    preceding non-blank lines whose FIRST line matches 'Table <id>' (Marker usually puts
    'Table E2.3-1' then a title line directly above the grid). Symbol-glossary tables
    ('| Symbol | ...') are never emitted; with drop_glossary=True (front-matter
    segments) they are DELETED outright -- Fix 2, block-level so the rest of the front
    matter survives."""
    lines = body.split('\n')
    out, tables, i = [], [], 0
    while i < len(lines):
        if not lines[i].strip().startswith('|'):
            out.append(lines[i]); i += 1
            continue
        tb = []
        while i < len(lines) and lines[i].strip().startswith('|'):
            tb.append(lines[i]); i += 1
        table_md = '\n'.join(tb)
        body_rows = max(0, len(tb) - 2)
        if _SYMHDR.search(tb[0]):
            if not drop_glossary:
                out.append(table_md)                   # tagged-clause context: leave in place
            continue                                   # front matter: deleted (B3)
        if body_rows < 2 or len(table_md) < MIN_TBL_CHARS:
            out.append(table_md)                       # too small: leave in place
            continue
        # caption scan: walk back over at most 3 non-blank emitted lines
        cap_start, seen = None, 0
        for k in range(len(out) - 1, -1, -1):
            if not out[k].strip():
                if seen:
                    break
                continue
            ls = out[k].strip()
            if ls.startswith('<!--') or ls.endswith('-->'):
                continue                       # provenance comments never end a caption scan
            seen += 1
            if _TBLCAP.search(out[k]):
                cap_start = k
                break
            if seen >= 3:
                break
        if cap_start is not None:
            caption = [l for l in out[cap_start:] if l.strip()]
            tab_id = _TBLCAP.search(out[cap_start]).group(1)
            del out[cap_start:]
        else:
            m = _TBLCAP.search(tb[0])                  # caption merged into the grid itself
            caption, tab_id = [], (m.group(1) if m else None)
        pointer = "[Table %s emitted as its own chunk]" % (tab_id or "(untitled)")
        out.append(pointer)
        tables.append((tab_id, caption, table_md))
    return '\n'.join(out), tables


_SPAN = re.compile(r'</?span[^>]*>')   # Marker page anchors wrap headings: strip everywhere
_AISI_CHAP = re.compile(r'^([A-N])\.\s+\S')             # "B. DESIGN REQUIREMENTS" chapter style
_AISI_COMM = re.compile(r'\bCOMMENTARY ON\b', re.I)     # the commentary division title
_DOTLEAD = re.compile(r'\.{5,}\s*\d*\s*\|?\s*$')        # TOC dot-leader line "... Title ....... 45"


def chunk_markdown(md, std_code, std_display):
    """Return a list of chunk payload dicts for one standard's reconstructed markdown."""
    prof = _profile(std_code)
    md = _IMG.sub('', md)
    md = _SPAN.sub('', md)                 # anchors otherwise defeat every heading regex
    lines = md.split('\n')
    if prof == 'aisi':
        # drop TOC dot-leader lines at the SOURCE (front matter + the mid-document commentary
        # TOC) -- line-level, so a TOC that merged into a real section can never take the
        # section's chunk down with it
        lines = [l for l in lines if not _DOTLEAD.search(l)]
    nlines = len(lines)
    if prof == 'aisi':
        # AISI reconstructions title the division "COMMENTARY ON THE ..."; it can sit as early
        # as ~40% of the file (S400), and a "TABLE OF CONTENTS COMMENTARY ..." line precedes it.
        comm_line = next((i for i, ln in enumerate(lines)
                          if ln.strip().startswith('#') and _AISI_COMM.search(ln)
                          and 'TABLE OF CONTENTS' not in ln.upper()
                          and i > nlines * 0.20), None)
    else:
        comm_line = next((i for i, ln in enumerate(lines)
                          if _COMM.match(ln.strip()) and i > nlines * 0.40), None)

    # ---- pass 1: segment by clause boundary ----
    segs = []
    chapter = chapter_title = section = section_title = ''
    in_comm = False
    cur = None

    def newseg(clause, title):
        nonlocal cur
        if cur is not None:
            segs.append(cur)
        cur = dict(chapter=chapter, chapter_title=chapter_title, section=section,
                   section_title=section_title, clause=clause, title=title,
                   doc_part='commentary' if in_comm else 'spec', body=[])

    newseg('', '')  # preamble / front matter
    for idx, ln in enumerate(lines):
        if comm_line is not None and idx >= comm_line:
            in_comm = True
        core = _heading_core(ln)
        if core is None:
            cur['body'].append(ln); continue
        if comm_line is not None and idx == comm_line:
            chapter = chapter_title = section = section_title = ''   # commentary restarts clean
            newseg('', core); continue
        cm = _CHAP.match(core); ap = _APPX.match(core)
        if not cm and prof == 'aisi':
            cm = _AISI_CHAP.match(core)     # "A. GENERAL PROVISIONS" style chapter heads
        if cm:
            chapter = cm.group(1).upper(); chapter_title = ''; section = section_title = ''
            newseg(chapter, core); continue
        if ap:
            chapter = 'APP' + ap.group(1); chapter_title = core; section = section_title = ''
            newseg(chapter, core); continue
        sm = _SEC.match(core)
        if sm:
            section = sm.group(1); section_title = core[len(section):].strip(' .:)-—')
            if not chapter:
                chapter = section[0]
            elif prof == 'aisi' and not chapter.startswith('APP'):
                # AISI reconstructions have no "CHAPTER X" heading lines -- derive the chapter
                # from each clause's leading letter (E2 -> E), else every chapter collapses into
                # the first one and the chapter= pinpoint filter is wrong for B..M. Country
                # appendices (APPA/APPB) keep their APP chapter for their mirrored sections.
                chapter = section[0]
            newseg(section, core); continue
        # numeric clauses: AISI, inside an appendix only (S100 App. 1 EWM)
        if prof == 'aisi' and chapter.startswith('APP'):
            nm = _NUMSEC.match(core)
            if nm:
                num = nm.group(1)          # S100 App sections carry the appendix no. ("1.1" = App 1)
                section = num              # -> clause matches how the agent naturally cites it
                section_title = core[len(num):].strip(' .:)-—')
                newseg(section, core); continue
        subm = _SUB.match(core)
        if subm and section:
            clause = f"{section}.{subm.group(1)}"
            newseg(clause, core); continue
        # a CHAPTER title line (ALL CAPS) immediately after "CHAPTER X"
        if chapter and not chapter_title and not section and core.isupper():
            chapter_title = core; cur['chapter_title'] = core
        cur['body'].append(ln)
    if cur is not None:
        segs.append(cur)

    # ---- pass 2: merge tiny forward -- but NEVER across a clause boundary ----
    # (a duplicated stub heading with a one-line body would otherwise swallow the whole NEXT
    #  section into the wrong clause -- e.g. S100's "#### G4.2" stub eating all of G5)
    merged, i = [], 0
    while i < len(segs):
        s = dict(segs[i]); body = '\n'.join(s['body']).strip()
        while len(body) < MIN_CHARS and i + 1 < len(segs):
            nx = segs[i + 1]
            if s['clause'] and nx['clause'] and nx['clause'] != s['clause']:
                break                          # tiny stub stays tiny; (<80-char stubs drop later)
            i += 1
            if not s['clause'] and nx['clause']:
                for k in ('clause', 'section_title', 'title', 'chapter', 'chapter_title', 'doc_part'):
                    s[k] = nx[k] or s[k]
            body = (body + '\n\n' + '\n'.join(nx['body']).strip()).strip()
        merged.append((s, body)); i += 1

    # ---- pass 3: split huge, build breadcrumb + metadata, emit ----
    chunks = []
    def emit(s, body_text, part=None, nparts=1):
        chap = s['chapter'] or ''
        clause = s['clause'] or chap or ''
        title = (s['section_title'] or s['title'] or '').strip().strip('*').strip()
        bits = [std_display]
        if chap:
            bits.append(f"Ch.{chap}" + (f" {s['chapter_title'].title()}" if s['chapter_title'] else ""))
        if clause and clause != chap:
            bits.append(f"{clause} {title}".strip())
        crumb = " — ".join(b for b in bits if b)
        if part:
            crumb += f"  (part {part}/{nparts})"
        text = crumb + "\n\n" + body_text.strip()
        eqs, tbls, has_eq, has_tbl = _meta(text)
        chunks.append(dict(
            text=text, page_content=text, source=std_code + ".md", standard=std_code,
            section=(title or clause), clause=clause, chapter=chap, doc_part=s['doc_part'],
            eqs=eqs, tables=tbls, has_equation=has_eq, has_table=has_tbl,
            has_figure=False, breadcrumb=crumb))

    for s, body in merged:
        if prof == 'aisi':
            # Fix 2: untagged preamble = FRONT MATTER, tagged so the API can exclude it
            # from default search; its symbol-glossary tables (which outranked spec on
            # symbol-heavy phrasings) are deleted block-level in _extract_tables.
            is_front = not s['clause'] and not s['chapter']
            if is_front:
                s = dict(s, doc_part='frontmatter')
            # Fix 1: emit tables as their own chunks
            body, tabs = _extract_tables(body, drop_glossary=is_front)
            for tab_id, caption, table_md in tabs:
                clause_tag = ("Table %s" % tab_id) if tab_id else \
                             ((s['clause'] or s['chapter'] or 'tbl') + "-table")
                cap_txt = ' '.join(l.strip() for l in caption).strip()
                st = dict(s, clause=clause_tag,
                          section_title=(cap_txt or ("table in %s" % (s['clause'] or 'front matter'))))
                full = ('\n'.join(caption + [table_md])).strip()
                if len(full) <= HARD_CHARS:
                    emit(st, full)
                else:                                   # giant table: row-split, repeat header
                    hdr = '\n'.join(caption + table_md.split('\n')[:2])
                    parts = _hardsplit(table_md)
                    for k, p in enumerate(parts, 1):
                        emit(st, (p if k == 1 else hdr + '\n' + p), k, len(parts))
        if len(body.strip()) < 80:
            continue
        if len(body) <= MAX_CHARS:
            emit(s, body)
        else:
            parts = _pack(_blocks(body))
            for k, p in enumerate(parts, 1):
                emit(s, p, k, len(parts))
    return chunks


def _dryrun(path, std_code, std_display):
    """Chunk one MD and print the sanity report to eyeball BEFORE embedding: sizes, clause
    coverage, chapters seen, and the biggest untagged chunks (usually a heading style the
    profile missed -- fix the regex or the MD, then re-run)."""
    md = open(path, encoding='utf-8').read()
    ch = chunk_markdown(md, std_code, std_display)
    n = len(ch)
    sizes = sorted(len(c['text']) for c in ch)
    tagged = [c for c in ch if c['clause'] and c['clause'] != c['chapter']]
    spec = sum(1 for c in ch if c['doc_part'] == 'spec')
    chaps = {}
    for c in ch:
        chaps[c['chapter'] or '(none)'] = chaps.get(c['chapter'] or '(none)', 0) + 1
    print("%s (%s): %d chunks | %d spec / %d commentary" % (std_display, std_code, n, spec, n - spec))
    print("  sizes: min %d / median %d / p90 %d / max %d chars"
          % (sizes[0], sizes[n // 2], sizes[int(n * .9)], sizes[-1]))
    print("  clause-tagged: %d/%d (%.0f%%) | with equations: %d | with tables: %d"
          % (len(tagged), n, 100.0 * len(tagged) / n,
             sum(1 for c in ch if c['has_equation']), sum(1 for c in ch if c['has_table'])))
    print("  chapters:", " ".join("%s:%d" % kv for kv in sorted(chaps.items())))
    tabs = [c for c in ch if c['clause'].startswith('Table ') or c['clause'].endswith('-table')]
    fm = sum(1 for c in ch if c['doc_part'] == 'frontmatter')
    print("  EMITTED TABLE CHUNKS: %d | frontmatter-tagged: %d" % (len(tabs), fm))
    for c in tabs[:40]:
        print("    · %-18s %s" % (c['clause'], (c['section'] or '')[:70]))
    untagged = sorted((c for c in ch if not c['clause'] or c['clause'] == c['chapter']),
                      key=lambda c: -len(c['text']))[:5]
    for c in untagged:
        print("  [untagged %5d ch] %s" % (len(c['text']), c['text'][:100].replace('\n', ' | ')))
    return ch


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 3:
        sys.exit("usage: python3 chunk_v2.py md/<STD>.md <STD_CODE> [display name]")
    _dryrun(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else sys.argv[2])

# CFS worked reference — a four-story WSP shear-wall building (condensed method)

Treat this as a **METHOD TEMPLATE and a SELF-CHECK DISCIPLINE, not numbers to copy.** Your
building's geometry, loads and system differ; reuse the *sequence* and the grounding habit (every
capacity pulled from the RAG and cited), and finish with a numeric self-check. Unlike a published
textbook example, this reference's answer key is **reproducible in-repo**: the cfg below runs
through `cfs_engine`/`cfs_pipeline` and must return the quoted numbers — if your understanding of
the machinery disagrees with them, fix your understanding before designing.

## Problem (condensed)
4-story CFS light-frame residential building, 120 ft × 60 ft; story heights 10 / 9.5 / 9.5 / 9.5 ft
(hn = 38.5 ft). System both directions: **WSP-sheathed shear walls** (R = 6.5, Cd = 4.0, Ω0 = 3.0);
SDS = 1.00, SD1 = 0.50, S1 = 0.40 (SDC D — 65-ft height limit: 38.5 ft PASS), Ie = 1.0, RC II.
Loads: D_floor = 35 psf, D_roof = 22 psf, cladding 12 psf, S = 25 psf, L = 40 psf. Diaphragms
flexible. Wall lines X (resisting E-W force): A (y=0) and C (y=60 ft) with 2×24-ft segments per
story, B (y=30 ft) with 4×24-ft; lines Y: 1 (x=0) and 3 (x=120 ft) with 3×16-ft, 2 (x=60 ft) with
6×16-ft. Drift inputs `wall_props = dict(chord_area_in2=2.4, Gp_kip_in=18.0, en_in=0.015,
k_anchor_kip_in=250.0)`. NOTE on `k_anchor_kip_in=250`: that is a CONTINUOUS-ROD-class anchorage
stiffness; the discrete bolted-device band in `wall_line.HOLDDOWN_BANDS` is ~50 kip/in, 5× softer.
This example's drift answer (0.0175) depends on the 250 — a bolted-device design must EITHER use
k≈50 in `wall_props` (drift grows) or switch to rods; your own building's k_anchor must match your
selected anchorage, per line where they differ (`WallLine(..., wall_props=...)`). Find: ELF base
shear; per-line unit shears; a sheathing/fastener schedule; the chord/hold-down stack; drift vs
the 0.025 limit.

## Method — the sequence to mirror
1. **Weights & ELF** (ASCE 7-22 §12.8, computed by the engine): story weights from area dead +
   tributary cladding + 20% flat snow at the roof; Ta = Ct·hn^x (0.02/0.75 — light-frame CFS has no
   special formula); Cs = SDS/(R/Ie) with the SD1 cap and minima; V = Cs·W; Fx by w·h^k. Spot-check
   Cs and V by hand before trusting anything downstream.
2. **Tributary distribution (flexible diaphragm):** each wall line takes the tributary width to the
   adjacent lines' midlines, ±5% accidental shift; per-line story shear stacks downward; unit shear
   v = V_line / Σ(segment lengths) at each story. Interior lines with wide tributaries (B, 2) govern.
3. **Wall shear design per line per story (S400 E1):** pick sheathing (thickness, one/two-sided)
   and edge-fastener spacing so φ·vn ≥ v at every story — RAG-ground the table value; apply the
   2w/h aspect-ratio reduction where h/w > 2 (24-ft and 16-ft segments at 9.5 ft: h/w < 1, none
   here); step the schedule down the height with demand. **v without a thickness + fastener
   schedule is not a design.**
4. **Chords & hold-downs (cumulative, top-down):** overturning tension per line accumulates
   story-by-story net of 0.9D; the base value sizes the device — within the bolted band (≲15–20
   kip) use a discrete hold-down; beyond it SWITCH TO A CONTINUOUS ROD (computed: tension, PL/AE
   elongation + take-up; elongation feeds drift). Chord studs: built-up per S240 interconnection,
   compression per S100 E2/E3/E4 with the sheathing-braced assumption STATED.
5. **Studs, track, web crippling:** gravity stud stack (cumulative, live reduction compounded),
   H1 interaction with out-of-plane wind, **G5 web crippling at track bearing**; stud schedule
   never lighter below.
6. **Drift (S400 four-term):** bending + shear + fastener slip + anchorage elongation, amplified
   ×Cd/Ie, vs 0.025 (light-frame ≤4 stories, RC II). Reconcile every `drift_flags` entry.
7. **Both hazards:** wind MWFRS run and compared per direction; net-uplift 0.9D+1.0W anchorage
   path checked roof→wall→floor→foundation even where seismic governs in-plane shear.
8. **Self-check vs the answer key below**, and reconcile any discrepancy beyond a few percent
   BEFORE you report.

## Answer key (engine-reproducible — for self-check only, do NOT copy into your building)
W = **1,096 kip**; Ta = **0.309 s**; Cs = **0.1538** (= SDS/(R/Ie) governs); **V = 168.6 kip**
each direction; k = 1.0; Fx = {L1: 19.6, L2: 37.9, L3: 56.3, roof: 54.9} kip.
Line B (interior, 60-ft tributary of the 60-ft depth… i.e. half the plan): story shears
88.5 / 78.3 / 58.4 / 28.8 kip → over 96 ft of wall, unit shears **922 / 815 / 608 / 300 plf**
(stories 1–4) — a two-sided (or dense-nailed) WSP schedule at the base stepping to a light
single-sided schedule at the roof. Base chord/hold-down tension: **25.6 kip on every line**
(overturning = Σ cumulative-story-shear × story height; A/C carry half B's shear over half
B's length, so all six lines coincide here) → **beyond the ~20-kip bolted band: continuous
RODS** (computed tension + PL/AE elongation + take-up; a lighter/shorter variant can drop
back into the bolted band). Typical bearing stud (16 in. o.c., 2-ft trib): cumulative
P = 0.07 / 0.35 / 0.64 / **0.92 kip** (roof→L1).
Worst amplified drift ratio **0.0188** (line 1, story 1) ≤ **0.025** — PASS, all lines.
(Numbers re-derived 2026-07-31 after the overturning-stack fix — the prior key quoted
8.8 kip / 0.0175 from the pre-fix engine, which accumulated story force × own story height
and understated multistory hold-down tension ~3×.)

## Discipline to carry into every design
- Ground EVERY capacity in the RAG (S100 + S240 + S400 together); cite the exact
  clause/equation you applied. The framework computes demands and drift — never a capacity.
- State the diaphragm idealization, the per-line bracing assumption, and the anchorage scheme
  explicitly; reconcile the model-vs-tributary gate and every drift flag.
- Finish with a numeric self-check against a known answer (this reference, or your own hand ELF +
  tributary calc), and reconcile discrepancies beyond a few percent BEFORE you report.

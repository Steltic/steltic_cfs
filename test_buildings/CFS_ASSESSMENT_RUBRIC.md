# Qualitative Assessment Rubric — CFS Design Agent (CFS_Ex1–CFS_Ex30)

Companion to `ASSESSMENT_RUBRIC.md` (hot-rolled). Score 0–4 per dimension with quoted evidence.
Part 1 covers wall-framed buildings (Ex1–15); Part 2 (below) covers portal frames and component
design (Ex16–19, Ex25–30, authored 2026-07 — this file is now the authoritative version).
SCOPE CHANGE 2026-07-30: storage racks (former Ex20–24) are EXCLUDED from the repo — those briefs
are removed and RMI MH16.1 is no longer part of the code set. Ex numbering keeps its gaps.

**Scale:** 0 = absent/wrong · 1 = attempted, major errors · 2 = mostly right, reviewer must fix gaps · 3 = correct, minor polish · 4 = correct, complete, engineer-ready

---

## A. Core Dimensions (every example)

### A1. Input fidelity
- Uses the stated wall layout, story heights, sheathing type, and system — does not swap strap bracing for shear walls, one-side for two-side sheathing, or Type II for Type I.
- Wall segment widths/counts and plan dimensions as given; height sums check out.
- **Red flag:** treats the building as a hot-rolled frame (designs "beams and columns" instead of walls, joists, studs).

### A2. Vertical load path & cumulative bookkeeping
- Story-by-story axial accumulation on studs and chords; live-load reduction compounded correctly.
- Stud schedule steps down with height, never lighter below; built-up chords (back-to-back / box) with interconnection requirements at high loads.
- Checks the local limit states that actually govern CFS: web crippling at track, distortional buckling, sheathing-braced vs unbraced design assumptions stated.

### A3. Lateral system per AISI S400
- Correct R/Cd/Ω0 for the exact system (WSP 6.5 / steel sheet 6.5 / strap 4 / gypsum 2 / SBMF 3.5) and height-limit checks.
- Capacity-design chains applied where S400 requires them: strap walls (Ry·Fy·Ag drives connections/chords/anchorage), steel-sheet walls (expected strength), SBMF (expected beam strength at design drift).
- Shear capacity from the correct S400 table concept: fastener schedule + sheathing thickness + aspect-ratio (2w/h) reduction where h/w > 2; Type II adjustment factor where specified.
- Hold-down/rod logic: cumulative tension level-by-level; anchorage in net-uplift wind combos (0.6D + 0.6W); distributed track anchorage for Type II walls.

### A4. Diaphragms & force distribution
- Flexible-diaphragm idealization used (or explicitly justified otherwise); shear to wall lines by tributary area, not rigid-diaphragm torsion.
- Collectors at re-entrant corners/steps/throats identified and designed (Ω0 where seismic).
- Mixed diaphragm types (bare deck vs gyp-crete floors, gable vs flat) handled distinctly.

### A5. Hazard governing & load cases
- Runs BOTH wind and seismic and states which governs per line/direction — critical where R is low or wind is high (Ex8, Ex11, Ex12).
- Uplift load path continuous: roof-to-wall, wall-to-floor, floor-to-foundation.
- Snow drift at every stated roof step/valley.

### A6. Deliverable completeness & judgment
- Outputs the actual CFS design products: stud/track schedules, sheathing + fastener schedules per line per story, chord stud sizes, hold-down/rod schedule, collector sizes, drift table.
- States assumptions where free; flags real feasibility problems (e.g., chord tension too large for proprietary hold-downs) instead of forcing numbers.

---

## B. Example-Specific Traps (CFS_Ex6–Ex15)

| Ex | System | What a strong response MUST catch |
|----|--------|-----------------------------------|
| 6 | WSP, L-plan | Re-entrant collectors with flexible diaphragm; amenity deck makes Level 5 stud demands non-uniform; truss vs flat roof on the same plate |
| 7 | Steel sheet, Z-plan | 2w/h reduction for 2:1–4:1 segments; narrow diaphragm throat = only load path (or justify a joint); hn vs 65-ft limit statement |
| 8 | Strap braced, T-plan | Capacity design from Ry·Fy·Ag — sizing connections for ELF force only = fail; wind may govern despite seismic framing; An·Fu ≥ Ag·Fy strap check |
| 9 | 5-over-2 podium | Two-stage ELF eligibility computed (10× stiffness, 1.1× period); reaction amplification (R_up/ρ)/(R_low/ρ); CFS base = top of podium for height & drift |
| 10 | CFS-SBMF + straps | Two R values sharing one axis → least-R rule or joint; open-front torsion with a flexible diaphragm can't redistribute — needs a south-line element or code-based open-front justification |
| 11 | Gypsum, coastal wind | R=2 seismic is trivial, 150-mph Exposure D is not; net-uplift combos with 0.6D; gypsum line-by-line upgrade map; breezeway weak-story transfer; corrosion spec |
| 12 | Steel sheet, 8 st | SDC C vs D height-limit knife edge noticed; 8-story cumulative rods/chords/reductions; rod elongation included in drift; wind likely governs base overturning |
| 13 | Type II perforated | Adjustment-factor calculation shown; anchorage at ends only PLUS distributed track anchorage; step wall violates uniform-height rule → line must be split; hold-downs at every pier = silent Type I reversion (fail) |
| 14 | Mixed direction, split-level | Direction-specific R/Cd applied (not averaged); stepped-base wall segments designed to their own bases; mid-split line is both tallest LFRS stack and bearing line |
| 15 | WSP stepped bar | Shared step-wall line collects from both blocks without double-count/omission; 240-ft movement-joint decision with numbers; snow drift + C&C on the exposed step face |

Score B 0–4: caught unprompted (4) · partially/hinted (2) · designed past it (0).

CFS_Ex1–Ex5 were source-based briefs with [TODO] gaps; the TODOs were filled with stated reasonable
values (2026-07), so the briefs are now fully specified — score input fidelity (A1) the same as
Ex6–15. Where an agent overrides a filled value, it must flag the override as an assumption.

---

## B2. Required Code Set (citation check — every example)

A competent response leans on **S100 + S240 + S400 together**, not S100 alone:

| Standard | Role | Applies to |
|----------|------|-----------|
| AISI S100 | Member/connection strength (columns, studs as beam-columns, screws, web crippling, distortional buckling) | All 15 |
| AISI S240 | Structural framing: stud/track/joist rules, built-up interconnection, bracing, truss provisions (ex-S214) | All 15 (trusses: Ex6, 8, 11, 13, 14, 15) |
| AISI S400 | Lateral: shear-wall/strap/SBMF capacities (BOTH the wind and seismic table columns), capacity-design chains (E1/E3/E4), Type II provisions | All 15 — including wind-governed Ex11/Ex12 |
| AISI S220 | Nonstructural interior members (Gr 33 partitions) | Peripheral (Ex1, 6, 11, 13); don't penalize omission |
| AISI S310 | Steel deck diaphragms | Ex10 only (bare-deck high-bay roof) |

**Scoring:** response cites the right standard for the right provision (4) · leans on S100/S240 but misses S400's role in wind-governed walls (2) · designs walls with no AISI citation at all, or cites AISC 341 for CFS systems (0).
**Staleness tell:** citing S110, S213, or S214 as current standards (merged into S400/S240) — note it.
**Harness note:** the RAG has no S310 collection (README lists it but `reingest_v2.py` never ingests it) — for Ex10, expect the agent to flag the gap or ground deck-diaphragm values another way; hallucinated "S310" values with fake section numbers = red flag.

---

## C. CFS-Specific Red Flags (instant score cap of 1 on the affected dimension)

- Hot-rolled reflexes: A992 steel, W-shapes, "moment frames" where none exist, AISC 341 citations for wall systems.
- Rigid-diaphragm torsional distribution in a WSP-floor light-frame building without justification.
- Shear wall capacity quoted without sheathing thickness + fastener schedule (a bare "wall is OK" is unverifiable).
- Hold-downs sized for the shear force instead of the overturning tension.
- Stud checked as a pure column with no sheathing-bracing assumption stated, or claimed sheathed-braced on an unsheathed line.
- Seismic-only design at a 150-mph wind site (Ex11) or wind-only at an SDC D site.

---

## Scoring Summary Sheet

| Example | A1 | A2 | A3 | A4 | A5 | A6 | B (trap) | Red flags | Notes / evidence |
|---------|----|----|----|----|----|----|----------|-----------|------------------|
| CFS_Ex__ |   |    |    |    |    |    |          |           |                  |

**Interpretation:** A-avg ≥3 and B ≥3 → deployable with review. Any red flag from Section C → treat the response as unreliable regardless of totals. B=0 on Ex9, Ex10, or Ex13 → the agent knows shear walls but not AISI S400's system-specific machinery — the discriminator between "light-frame aware" and "CFS competent."

**Cross-model tip:** Ex8 (capacity design), Ex11 (hazard inversion), and Ex13 (Type II mechanics) are the fastest discriminators — run those three first when comparing LLMs.

---

# Part 2 — Portal frames, components (CFS_Ex16–19, Ex25–30)

Same 0–4 scale. Core dimensions A1–A6 apply with these re-readings: A2 = load path through
frames instead of stud stacks (pattern/drift/point loads to the right members); A3 = the
governing lateral standard is S100 frame design, NOT S400 wall tables;
A4 = there is often NO diaphragm — bracing schedules with forces replace tributary wall shear.

## P1. Analysis fidelity tier (new dimension, every Part 2 example)
- Correct tier selected (or the brief's stated tier honored) AND justified against the structure:
  Tier 1 for portals/canopies, Tier 2 for torsion-critical cases (Ex27 justify-or-run).
- Effective-section stiffness handled: gross-property-only analysis on slender CFS members flagged
  or corrected; decoupled EA(Ae)/EI(I_eff) assignment stated.
- Score 0 if the agent runs Tier 0 on Ex27 without justification or acknowledging the preflight
  warning.

## P2. Example-specific traps

| Ex | Structure | What a strong response MUST catch |
|----|-----------|-----------------------------------|
| 16 | Single-span portal | Uplift flange-reversal unbraced case; knee distortional; no panel-diaphragm credit |
| 17 | Two-span portal, snow | Valley drift governs interior column; pattern snow; hogging-flange bracing |
| 18 | Portal + mezzanine | Least-R on the shared axis (or joint); two diaphragm types; shared frame dual role |
| 19 | Portal + monorail | Fatigue SCOPED not claimed; web crippling at hangers; hot-rolled beam by exception |
| 20–24 | *(removed — storage racks excluded from scope 2026-07-30)* | — |
| 25 | Purlin/girt retrofit | R-factor uplift method (through-fastened) vs unrestrained (standing-seam, no test data); C&C zones; reaction handoff |
| 26 | Open canopy | Cn clear AND obstructed; net uplift drives piers; end frames govern |
| 27 | Single-channel portal | Shear-center torsion designed, not assumed away; tier justified; premise-failure honesty |
| 28 | Partially enclosed ag | Enclosure classification run, ±0.55 GCpi consequences; eave-strut collector with a force |
| 29 | Free-standing mezzanine | Ch. 15 vs 12/13 cited; independence + handoff (no host bracing, slab scoped out); anchor edge distance |
| 30 | Cold storage | Ct = 1.2; discrete bracing (no panel credit); drifts mapped; thermal movement + toughness spec |

Score P2 0–4 as Part 1's B: caught unprompted (4) · partial (2) · designed past it (0).

## P3. Required code set (Part 2 additions)

| Standard | Role | Applies to |
|----------|------|-----------|
| AISI S100 | All member/connection strength, EWM; combined bending+torsion; web crippling; uplift R-factor method | All |
| ASCE 7 Ch. 15 / 13 | Nonbuilding structures / platforms — classification and cites | Ex29 |
| ASCE 7 Ch. 27/29/30 | Open-building and C&C wind; enclosure classification | Ex16–17, 25–28, 30 |
| AISI S240/S400 | Only where light-frame walls/straps appear (Ex18, 29) — citing S400 wall tables for portal frames is a staleness/competence tell |

## P4. Part 2 red flags (instant cap of 1 on the affected dimension)
- Panel/skin diaphragm credited where the brief voids it (thermal clips, standing seam, longitudinal portal path).
- Merged model of independent structures (Ex29 host).
- Enclosed-building wind on Ex26/Ex28; warm-roof Ct on Ex30.
- Hot-rolled reflexes: W-shape portals, AISC citations for CFS frame members.

**Cross-model tip (Part 2):** Ex25 (uplift restraint regimes), Ex27 (shear-center torsion), and
Ex28 (enclosure classification) are the fastest discriminators; Ex29 separates load-path thinkers
from model mergers.

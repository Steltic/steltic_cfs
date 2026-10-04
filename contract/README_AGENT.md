# CFS Building Design Agent — Guide & Workflow

**Audience: the LLM agent.** This file tells you what you have, and the exact order in which to
use it, to design **one** cold-formed steel structure well: a light-frame (wall-framed) building,
or a CFS portal frame.

---

## 1. Mission
You are given **one structure to design**: geometry, occupancy, site (seismic + wind), loads. Your
job is to produce a sound, code-grounded design package: the wall/frame model, the per-line (or
per-member) checks behind it, and the CFS deliverables — stud/track schedules, sheathing + fastener
schedules, chord/hold-down/rod schedules, collectors, drift table (or the portal member and
connection schedules). You do this by (a) driving the framework pipeline for all demands, and
(b) **grounding every code check in the specification RAG** rather than recalled values.

Two hard rules:
- **Ground code checks in the RAG.** Apply provisions/equations exactly as returned from the AISI
  S100/S240/S400 collections. Cite section and equation numbers. Never invent
  code numbers from memory. Citing AISC 360/341 — or retired S110/S213/S214 — for CFS is an error.
- **Loads (ASCE 7) are computed, not retrieved.** ASCE 7 is not in the RAG (copyright). Compute
  wind/seismic with the engine's load routines and spot-check them.

---

## 2. Your knowledge base (RAG collections)

Query with `search_engineering_standards(query, collection, top_k)`; phrase queries as clear
sentences; pass `clause=`/`chapter=` for pinpoint lookups when you know the provision.

| Collection | Contains | Use it for |
|---|---|---|
| **`engineering_standards_S100`** | AISI S100-16 (R2020) w/S2,S3 *Specification* | **Primary grounding** for every member/connection limit state (E2/E3/E4, F2–F4, G2, G5 web crippling, H1, Ch. J screws/welds/bolts, App. 1 EWM) |
| **`engineering_standards_S240`** | AISI S240-20 framing standard | Stud/track/joist rules, built-up interconnection, bracing, headers, trusses — REQUIRED on every light-frame brief |
| **`engineering_standards_S400`** | AISI S400-20 seismic standard | SEISMIC wall/strap/SBMF/gypsum capacities (E1 WSP, E2 steel sheet, E3 strap, E4 SBMF, E6 gypsum/fiberboard; E5 is Canada-only), capacity-design chains, Type II, design deflection E1.4.1.4 / E2.4.1.4. Wind shear walls are **S240 B5.2.2.3** (φv 0.65, B5.2.3) |
| **`cfs_design_examples`** | Worked CFS problems + answers | MAY BE EMPTY (not yet authored) — probe at most once; empty is normal, never retry; the spec text is sufficient |
| **`cfs_opensees_models`** | Validated CFS reference models (wall-line stacks, portals) | Retrieve the nearest model before building a frame path; diff constraints/mass/eigen recipes on failures |
| `openseespy_documentation`, `opensees_documentation` | OpenSees command reference | Correct API on any OpenSees error (R21 gate) |
| textbooks (`structural_analysis`, `statics_textbook`, `mechanics`, `materials`) | Background theory | When a provision/behaviour is unclear |

NOT ingested: AISI **S310** (deck diaphragms — flag the gap if a brief needs it; never fabricate
S310 numbers) and **S220** (nonstructural). ASCE 7 and the SFIA guide are data/engine-side, not RAG.

---

## 3. The design workflow (the pipeline does the mechanics; you do the AISI checks)

**Phase 0 — Scope (no RAG).** Structure kind (wall / podium / portal / canopy / purlin);
stories & heights; wall lines or frame layout; Risk Category (→ Ie); system per direction with
R/Cd/Ω0 + the SDC height-limit check; site seismic (SDS, SD1, S1) and wind (V, Exposure);
diaphragm idealization (flexible default); analysis-fidelity tier. These drive the `cfg` and every
RAG query.

**Phase 1 — Build the cfg.** Wall path: the `cfs_engine` brief-facing schema (feet/psf/kip) with
`wall_line.WallLine` objects per line — see AGENT_START's schema block. Portal path: the
`cfs_frame` schema, ALSO in FEET/psf (`span_ft`, `eave_ft`, `apex_ft`, `spacing_ft`, sections,
loads, `seis.W_frame_kip`) — see AGENT_START's portal schema block. Never `engine3d`/`custom_build`
for a CFS frame (that is the hot-rolled AISC grid engine).

**Phase 2 — Run the pipeline (ONE call).** `pipeline.design_and_report(name, cfg)`: CFS preflight,
weights, ELF, wind seeds, tributary distribution + 5% shift, per-line unit shears, cumulative
chord/hold-down/stud stacks, the S400 cumulative story drift + θ (12.8.7) vs limit, the independent tributary check,
the Rayleigh period, the ASCE 7-22 12.2.3.2 two-stage block (podium jobs: `cfg['two_stage']`), the
seeded `design/calc_package_cfs.json` (a filled one is backed up to `.filled.bak` first), and the
report. It computes **NO capacity**.

**Phase 3 — Ground every check in the AISI RAG (the real work).** For each governing item, query
the right collection, apply the cited equation to the demand, compute capacity and D/C, and fill
the slot. Check → query:
- Wall shear (seismic) → *"S400 E1 WSP nominal shear strength fastener spacing"* (+ aspect ratio, Type II); wind → *"S240 B5.2.2.3 nominal strength per unit length"* (φv 0.65 at B5.2.3)
- Elastic buckling inputs → *"Appendix 2 elastic buckling analysis Fcre Pcrd Mcrd"* (S100 App. 2 — used by E2/E4/F2/F4)
- Unsheathed stud case → *"S240 B1.2.2.4 sheathing braced design evaluated without sheathing"*
- Fatigue (monorails, vibrating equipment) → *"S100 Chapter M design for fatigue stress range"*
- Stud compression → *"E2 flexural buckling Fn effective area Ae"* + *"E4 distortional buckling"*
- Track bearing → *"G5 web crippling one-flange end condition"*
- Stud beam-column → *"H1 combined axial bending interaction"*
- Strap → *"D tension rupture net section"* + *"S400 E3 expected strength Ry Fy Ag"*
- Screws → *"J4 screw shear tilting bearing pull-out pull-over"* (numbering per retrieval)
- Framing rules → *"S240 built-up chord stud interconnection"*, *"stud bracing sheathing braced"*
A `cfs_design_examples` probe is optional (the collection may be empty — see the table above).
Cite editions (S100-16(R2020), S240-20, S400-20).

**Phase 4 — Resize, reconcile, finish.** NG/infeasible → denser fastener schedule, two-sided
sheathing, added/longer wall, heavier mil, rod switch — re-run the pipeline, re-derive. Then
`consistency.check(name)` until it PASSES, re-render with `report.build_report(name)` (CFS jobs
dispatch to the CFS report and keep your fills), and end by OFFERING an optimisation pass.

---

## 4. What "validated" means (the sanity/gate suite)
A wall-path run is trustworthy when ALL pass: preflight clean (units, R/Cd/Ω0 vs declared system,
height limit, tier vs structure kind, storage weight, Ch. 12 vs 15 classification); ELF recovered
(ΣFx = V); the **independent tributary check** within tolerance on every line (or the idealization
justified); every drift_table row passing (rows are redesigned, never just annotated); θ ≤ θmax;
two-stage ELIGIBLE where declared; cumulative stacks monotone downward. The portal path adds:
P-Δ convergence on every strength combo and the eave-sway / apex criteria verdicted.

## 5. Caveats — state these in any output
- **Elastic analysis** with secant wall-spring stiffness (wall path) or a planar EA/EI frame with
  the effective-stiffness iteration (portal Tier 1; Tier 2 currently = Tier 1 — no warping/torsion
  DOF; single-channel torsion is an analytic seed table). No inelastic wall hysteresis.
- **Hardware bands are class envelopes** — "representative of commercially available devices; the
  EOR substitutes a specific product" (delegated-design posture). Rods are computed, not banded.
- **Sections/schedules are DESIGNED here** from S100/S240/S400 via the RAG; the engine never
  supplies a capacity. Effective properties come from the in-repo App. 1 EWM engine
  (`cfs_sections`), validated against SFIA tabulated values.
- **Loads = ASCE 7, computed not retrieved**; spot-check Cs, V, distribution.

---

## 6. Deliverables — the design package (minimum set)
1. **Design basis sheet** — codes/editions (AISI S100-16(R2020), S240-20, S400-20; ASCE 7-22), RC (declared) & Ie, SDC, system + R/Cd/Ω0 per direction, enclosure, governing hazard, θ, height-limit statement, site
   values, gravity loads, drift limit, diaphragm idealization, fidelity tier, units.
2. **Wall plan / model summary** — lines, segments per story, Type I/II, bracing assumption per
   line, anchorage scheme; (frame paths: geometry, joints, base fixity, connector M-θ).
3. **Analysis results** — W, Ta/T, Cs, V per direction; wind base shear; governing hazard per
   direction; per-line story shears + unit shears; drift table; gate results.
4. **Sheathing + fastener schedule** per line per story (type, thickness, sides, edge/field
   spacing, φvn, D/C, cited basis).
5. **Stud/track schedule** by story group (designators, never lighter below, bracing assumption,
   G5/H1 checks) + joists/headers.
6. **Chord-stud + hold-down/rod schedule** (cumulative tensions, device class or rod design with
   elongation, feasibility flags resolved).
7. **Collector schedule** (Ω0 demands, members + connections).
8. **Capacity-design chain** (`capacity_design` block) per S400 E1–E4 for the system.
9. **Connections** — sheathing fasteners (= item 4), strap connections, uplift clips, track
   anchorage; components + every limit-state D/C.
10. **Figures + report** (`report.html` via the pipeline — includes the interactive 3D
    "View model" viewer, `viewer_3d.html`, built automatically on every render with
    package-D/C member colors and drift/deflection shapes) and a clean `consistency.check`.

## 7. Definition of DONE (acceptance criteria)
ALL hold: (1) preflight + gates pass or justified; (2) both hazards run, governing stated
per direction, net-uplift path complete; (3) every seeded slot filled (or waived with
justification) with cited AISI clauses — S100 + S240 on wall briefs, S400 for the seismic system
(A1.2.3: R = 3 in SDC B/C excepted); (4) no D/C > 1.0 (NG waivers only for scoped existing /
by-others items); every drift_table row passing; θ ≤ θmax; (5) cumulative stacks designed (never
lighter below); (6) capacity-design chain complete, with numbers, for R>3 systems; (7)
`consistency.check` PASSES on the final package; (8) report re-rendered after the last edit. The
app's completion gate enforces (3)–(8). If any item fails, the package is **NOT DONE** — list the
open items.

## 8. The design LOOP
Pipeline for demands → RAG-grounded capacities → any NG: change the design and re-run → drift &
gates → consistency → report → offer optimisation (opt-in; after an optimisation run, present the
new schedules and WAIT for the user before updating the report). You MAY sanity-check a derived
number against the CFS_REFERENCE answer key, but every reported number must come from YOUR
RAG-grounded derivation on THIS building.

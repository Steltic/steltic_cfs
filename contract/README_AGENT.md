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
| **`engineering_standards_S400`** | AISI S400-20 seismic standard | Wall/strap/SBMF capacities (WIND and seismic columns), E1–E4 capacity-design chains, Type II, drift expression |
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
`wall_line.WallLine` objects per line — see AGENT_START's schema block. Frame path (portal):
`engine3d` kip-inch cfg with a `custom_build`, Tier 1/2 elements per the fidelity tier, and the
brief's connector M-θ / base-fixity data where supplied.

**Phase 2 — Run the pipeline (ONE call).** `pipeline.design_and_report(name, cfg)`: preflight,
weights, ELF (+ two-stage podium), wind, tributary distribution + 5% shift, per-line unit shears,
cumulative chord/hold-down/stud stacks, S400 four-term drift vs limit, the model-vs-tributary
comparison gate, figures, the seeded `design/calc_package.json`, and the report scaffold. It
computes **NO capacity**.

**Phase 3 — Ground every check in the AISI RAG (the real work).** For each governing item, query
the right collection, apply the cited equation to the demand, compute capacity and D/C, and fill
the slot. Check → query:
- Wall shear → *"S400 E1 WSP nominal shear strength fastener spacing"* (+ aspect ratio, Type II)
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
`consistency.check(name)`, reconcile every flag, re-render with
`report.build_report_cfs_from_disk(name)` (CFS jobs; the hot-rolled grid path keeps
`report.build_report(name)`), and end by OFFERING an optimisation pass.

---

## 4. What "validated" means (the sanity/gate suite)
A wall-path run is trustworthy when ALL pass: preflight clean (units, R/Cd/Ω0 vs declared system,
height limit, tier vs structure kind); ELF recovered (ΣFx = V); the **model-vs-tributary gate**
within tolerance on every line (or divergence justified); drift table clean (or flags reconciled);
cumulative stacks monotone downward. Frame paths add: equilibrium, stability (eigen > 0),
reasonable periods, modal mass ≥ 90%, P-Δ included.

## 5. Caveats — state these in any output
- **Elastic analysis** with secant wall-spring stiffness (Tier 0) or thin-walled elements +
  effective-stiffness iteration (Tier 1/2). No inelastic wall hysteresis.
- **Hardware bands are class envelopes** — "representative of commercially available devices; the
  EOR substitutes a specific product" (delegated-design posture). Rods are computed, not banded.
- **Sections/schedules are DESIGNED here** from S100/S240/S400 via the RAG; the engine never
  supplies a capacity. Effective properties come from the in-repo App. 1 EWM engine
  (`cfs_sections`), validated against SFIA tabulated values.
- **Loads = ASCE 7, computed not retrieved**; spot-check Cs, V, distribution.

---

## 6. Deliverables — the design package (minimum set)
1. **Design basis sheet** — codes/editions (AISI S100-16(R2020), S240-20, S400-20; ASCE 7-22), RC & Ie, SDC, system + R/Cd/Ω0 per direction, height-limit statement, site
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
justification) with cited AISI clauses — S100 + S240 + S400 together on wall briefs; (4) no D/C > 1.0; drift table clean; (5) cumulative stacks designed (never lighter below);
(6) capacity-design chain complete for R>3 systems; (7) `consistency.check` clean; (8) report
built. If any item fails, the package is **NOT DONE** — list the open items.

## 8. The design LOOP
Pipeline for demands → RAG-grounded capacities → any NG: change the design and re-run → drift &
gates → consistency → report → offer optimisation (opt-in; after an optimisation run, present the
new schedules and WAIT for the user before updating the report). You MAY sanity-check a derived
number against the CFS_REFERENCE answer key, but every reported number must come from YOUR
RAG-grounded derivation on THIS building.

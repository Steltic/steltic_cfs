# `cfs_opensees_models` — reference-model library scope

The CFS analogue of `opensees_buildings_3d` (40 validated hot-rolled frames, retired): a library of
**validated reference models the agent retrieves and stands on** before building its own cfg.
Wall-path models are `cfs_engine` cfgs (WallLine stacks, pure python — validated in-repo today);
portal models are `engine3d` frame cfgs (validated on the owner's WSL openseespy).

## Two design rules (the train/test firewall)

1. **No parameter collisions with the test briefs.** Every model differs from its nearest
   CFS_Ex brief in at least THREE of: story count, plan shape/dimensions, system, site hazard
   (SDS/wind), and the feature mix. The roster table below carries a "nearest brief / how it
   differs" column as the audit.
2. **Models teach MECHANICS, briefs test JUDGMENT.** A reference model demonstrates the build
   recipe — the cfg grammar, wall-line/segment layout, wall_props basis, podium two-stage setup,
   collector declaration, gate outputs, portal constraints/mass/eigen — with clean, unremarkable
   parameters. The briefs' trap features (hazard inversion, silent Type-I reversion, least-R
   collisions at the knife edge) deliberately do NOT get "answer-shaped" models: an agent that
   retrieves W10 learns how to SET UP a Type II wall, not what Ex13's answer is.

## Roster — wall path (16 models, validated by `cfs_engine.run()` gates)

| ID | Model | Key features exercised | Nearest brief → how it differs |
|----|-------|------------------------|--------------------------------|
| W01 | WSP 2-st office, 80×40 ft bar, SDC C | the "hello world": minimal cfg, tributary, drift table | Ex1 (4-st) → stories, plan, SDC |
| W02 | WSP 3-st residential, 96×48, SDC D, 0.025 row | discrete hold-downs, cumulative stacks | Ex1 → stories, plan, hold-down class |
| W03 | WSP 5-st bar 140×55, SDC D | ROD anchorage switch (interior lines past bolted band), 0.020 row (>4 st), rod elongation in drift | Ex15 (6-st stepped) → regular bar, stories, anchorage focus |
| W04 | Steel-sheet 4-st, 100×60, SDC D | E2 tables, expected-strength seeds | Ex7 (6-st Z-plan) → stories, plan, SDC |
| W05 | Steel-sheet 7-st, 120×65, SDC C | deep cumulative stacks, rods everywhere, no height limit at C | Ex12 (8-st cross-plan D-edge) → stories, SDC, plan |
| W06 | Gypsum 2-st interior partitions office, 110 mph inland | R=2, wind-governed workflow, S400 wind columns | Ex11 (3-st 150 mph coastal D) → hazard level, exposure, stories |
| W07 | Strap-braced 3-st, 72×36, SDC D | strap An·Fu ≥ Ag·Fy, Ry·Fy·Ag chain seeds, chord/anchorage | Ex2/Ex5 (6-st) → stories, plan |
| W08 | Mixed direction: strap X / WSP Y, 4-st, SDC C | direction-specific R sets, two base shears | Ex14 (3-st split-level mixed) → stories, no split, SDC |
| W09 | SBMF 2-st showroom, 60×40, SDC D | E4 system, 35-ft limit stated, expected beam strength seed | Ex10 (SBMF+straps high-bay) → single system, no mixed axis |
| W10 | Type II perforated 3-st, uniform-height piers, SDC C | adjustment factor setup, end hold-downs + distributed track anchorage declared | Ex13 (4-st, stepped line trap) → stories, NO stepped-line trap, SDC |
| W11 | L-plan 4-st WSP, 90/60 legs, SDC D | re-entrant collector declaration, per-leg tributary | Ex6 (5-st L, amenity deck) → stories, leg sizes, no deck feature |
| W12 | U-plan 3-st steel-sheet, open-front court, SDC C | open-front justification + model-vs-tributary divergence JUSTIFIED example | Ex9 (5-over-2 U podium) → no podium, stories, system |
| W13 | 4-over-1 concrete podium WSP, SDC D | two-stage ELF eligibility + reaction amplification recipe, CFS base = podium top | Ex3 (8-lvl)/Ex9 (5-over-2) → 4-over-1, plan, system detail |
| W14 | Split-level 2/3-st WSP, 20-in step, SDC C | step shear transfer, drift-exempt story declaration | Ex14 (mixed-system split) → single system, stories, SDC |
| W15 | Stepped bar 5+3-st WSP, 180 ft, shared step line, SDC D | shared-line collection from both blocks, no double-count | Ex15 (6-st, 240-ft joint decision) → block heights, length, no joint trap |
| W16 | WSP 3-st coastal 140 mph Exp C, SDC B | net-uplift 0.9D+1.0W path as the GOVERNING case, uplift clip chain | Ex11 (gypsum 150 D) → system, wind level, exposure |

## Roster — portal path (6 models, Tier 1/2, validated on WSL openseespy)

| ID | Model | Key features exercised | Nearest brief → how it differs |
|----|-------|------------------------|--------------------------------|
| P01 | Single-span 40-ft portal, 16-ft eave, SDC C, 115 mph | frame grammar, kip-inch, Tier 1 elements, balanced + uplift cases, Ae/I_eff iteration | Ex16 (warehouse) → span, height, site; no knee-trap emphasis |
| P02 | Two-span 2×50-ft continuous, 30 psf ground snow | valley member setup, pattern-load case mechanics | Ex17 → spans, snow load |
| P03 | Portal + strap-braced mezzanine, 55-ft span | two-system cfg in one model, mezzanine mass vs framing | Ex18 → span, config, R pairing |
| P04 | Open canopy 24×60, Cn wind setup | open-building wind case grammar, net uplift on piers | Ex26 → geometry, no obstruction question |
| P05 | Single-channel 20-ft portal, Tier 2 | shear-center torsion modeling, Tier 2 fiber laws, preflight WARN example | Ex27 → span, section, loads |
| P06 | Purlin/girt roof on portal, through-fastened | purlin line modeling, R-factor uplift method setup | Ex25 (retrofit) → new-build, no standing-seam premise |

## Document schema (mirrors the old `opensees_buildings_3d` payload)

One doc per model, `cfs_models.jsonl` (one JSON object per line):
- `text` / `page_content` — the retrievable body: ~1.5–3k chars = one-paragraph description,
  the COMPLETE cfg construction code (WallLine lines, wall_props with stated basis, seis_cfs
  call; portals: the custom_build), and the validated results block (W, Ta, Cs, V, per-line
  base unit shears, worst amplified drift vs limit, hold-down classes/rod switches, gate
  status). The cfg code is the point — the agent copies the recipe.
- metadata: `building_id` (W01…P06), `kind` ("cfs_wall" | "cfs_portal"), `system`, `storeys`,
  `plan`, `SDC`, `R`, `governing` ("seismic"|"wind"), `feature` (comma list), `Ta_s`,
  `base_shear_kip`, `worst_drift_ratio`, `anchorage` ("bolted"|"rod"|"mixed"), `tier`,
  `status` ("validated"), `units`, `source` ("cfs_models_v1").

## Validation gates (a model ships only if ALL pass)

Wall: preflight clean; ELF recovered (ΣFx = V); every drift row ≤ limit (except W14's declared
exempt story); hold-down classes feasible (rod switches only where INTENDED — W03/W05); stacks
monotone; where a divergence exists (W12) it is the documented example, justified in the text.
Portal: eigen > 0, equilibrium, P-Δ run converges, drift ≤ limit, Tier matches structure kind.

## Build steps

1. `steel_engine/build_cfs_models.py` — constructs all 22 cfgs, runs wall models through
   `cfs_engine.run()` + gates NOW (pure python), portals guarded behind openseespy (owner WSL);
   emits `cfs_models.jsonl` + a PASS/FAIL table. Committed; jsonl is original content (no
   licensing issue — commit it).
2. Embed + upsert on the VM: small loader (pattern of `add_buildings.py`/`reingest_examples.py`)
   → collection `cfs_opensees_models`, nomic-embed, 768-dim. Runs in the same 32-GB window as
   the spec re-ingest if convenient.
3. Smoke queries: "3 story WSP shear wall building reference model", "podium two-stage CFS",
   "strap braced wall model", "portal frame CFS Tier 1", each returning the intended W/P model
   top-1; a rack query returns nothing rack-flavored (descope check).

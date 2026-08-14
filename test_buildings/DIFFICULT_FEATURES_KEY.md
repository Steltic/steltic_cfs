# Difficult Features Key (assessor only — do NOT attach with the briefs)

Intentional traps removed from each example brief. Use with the assessment rubric when scoring agent responses.

## CFS_Ex10_SBMF_2levels_highbay.txt

- Two R values in one direction along the shared axis: the least-R rule (or a justified seismic joint at the junction) is the core test
- Open-front torsion: N-S stiffness is symmetric (4 portals) but E-W walls are only on the north side of the high-bay - E-W load case creates torsion the flexible roof diaphragm cannot redistribute; agent must add a south-line E-W element at the storefront head (moment-resisting spandrel/portal) or demonstrate the diaphragm-cantilever provisions for open-front light-frame structures are satisfied
- Mixed diaphragms: bare deck (flexible) + gyp-crete floors (stiff) in one model

## CFS_Ex11_Gypsum_3levels_coastal_wind.txt

- R = 2 system but a 150-mph Exposure D site: the "seismic-trivial, wind-brutal" inversion catches agents that autopilot on seismic
- Net uplift on light CFS construction: 20 psf roof dead vs Exposure D uplift - hold-downs work in BOTH senses (shear overturning + direct uplift)
- Vertical irregularity at the breezeway (Level 1 opening under 2 stories of wall)
- Corrosion environment: fastener/connector spec is a completeness check

## CFS_Ex12_SteelSheet_8levels_crossplan.txt

- Tall light-frame: overturning stability (0.6D+0.6W) at the base of a 76-ft-tall, ~38-psf building; anchorage tension is huge relative to member sizes
- SDC C/D knife edge: one sentence in the seismic data legitimizes the whole building - does the agent notice?
- Cumulative-effects bookkeeping over 8 stories (studs, rods, reductions) without a podium to reset it
- Four-corner re-entrant + flexible diaphragms: collector forces from tributary logic, not FEM hand-waving

## CFS_Ex13_TypeII_perforated_4levels.txt

- Type II mechanics (adjustment factor, end-anchorage-only, distributed track anchorage) are the test - an agent that designs every pier with hold-downs has silently reverted to Type I
- Mixed Type I / Type II in one building: keep the bookkeeping straight per line
- The 3-story east mass creates a vertical step: in-plane shear collects at the step wall; verify the perforated south line continues across both masses at Levels 1-3 with a top-of-wall elevation break (Type II uniform-height rule violated at the step - agent must split the line)
- Porte-cochere notch interrupts the south perforated wall at Level 1 only

## CFS_Ex14_Mixed_3levels_splitlevel.txt

- Two R/Cd/Omega_0 sets live in one model: direction-specific ELF base shears differ by ~60% - agents often average or apply one set everywhere
- Split-level base: "number of stories" differs by wall line; the mid-split line is the tallest stacked LFRS and also a bearing line
- Heavier brick-veneer mass on the north line vs walkout openness on the south: mass eccentricity with a flexible diaphragm - handle via the required 5% accidental-torsion equivalent for flexible-diaphragm structures (tributary shift), not a rigid-diaphragm torsion calc

## CFS_Ex15_WSP_6levels_steppedbar.txt

- One building, two heights, one shared LFRS line at the step: double-counting or omitting the shared wall's tributary from either block is the classic error

---

# Part 2 — Portals, components (CFS_Ex16–19, Ex25–30, authored 2026-07)

*(SCOPE CHANGE 2026-07-30: storage racks — former Ex20–24 — excluded from the repo; Ex numbering keeps its gaps.)*

## CFS_Ex16_Portal_singlespan_warehouse.txt
- Uplift reversal flips which rafter flange is braced: the purlin-connected flange becomes the tension flange under 0.9D+1.0W — unbraced-length treatment must switch, or the uplift case is silently unconservative
- Knee distortional buckling of the unbraced inside flange under closing moment; bracing assumption must be stated, not implied
- Through-fastened panel stiffness is NOT credited longitudinally — straps take it all; a "panel diaphragm" shortcut is a fail

## CFS_Ex17_Portal_twospan_snow.txt
- Valley drift (not balanced snow) governs the interior column and valley purlins; missing the drift case = designing the wrong member
- Pattern snow (one span loaded) maximizes hogging and far-column uplift — both live nowhere in a balanced-only run
- Continuous-rafter hogging: bottom flange compression between brace points; purlin laps carry continuity moments

## CFS_Ex18_Portal_mezzanine_mixedR.txt
- R=3.5 portals and R=4 strap walls share a global axis where the mezzanine attaches: least-R rule or a justified joint — averaging or per-substructure R on one axis is the classic error
- Stiff mezzanine floor diaphragms vs flexible bare-deck roof in one structure — one idealization for both is wrong
- The shared portal frame carries both roles; checking it only as a portal misses the mezzanine mass it anchors

## CFS_Ex19_Portal_monorail_fatigue.txt
- Static design does not cover cyclic hanger demands: the expected answer sizes statically then SCOPES fatigue explicitly; claiming the static D/C covers fatigue = fail
- Web crippling / pull-through at hanger points on thin CFS rafter webs — a global-member-only check misses the governing local state
- Monorail beam is hot-rolled: design by exception with citation, or scoped exclusion with reactions — silently designing it as CFS is a red flag

## CFS_Ex25_PurlinGirt_uplift_retrofit.txt
- Uplift with free-flange compression is the design case: through-fastened option uses the tested R-factor method; the standing-seam option has NO test data in the brief — unrestrained design + a stated test-data requirement is the expected move; borrowing the through-fastened R for standing seam = fail
- C&C zoning: corner/edge purlins differ from interior; one-section-everywhere passes only if it passes the corner zone
- Existing-frame interface: reactions must not exceed the original design basis (or be handed off explicitly)

## CFS_Ex26_Canopy_open_wind.txt
- Open-building Cn cases including OBSTRUCTED flow (trucks below) — clear-flow-only underestimates; cases A and B both run
- Net uplift drives pier design (uplift + moment at fixed bases): a gravity-first design misses the governing foundation case
- No diaphragm: end frames govern via edge/corner zones + overhangs; torsion distributed by roof straps

## CFS_Ex27_Portal_singlechannel_torsion.txt
- Shear-center eccentricity: every load through the web plane induces torsion — the back-to-back reflex ("assume doubly-symmetric") caps the score
- Tier justification is itself a deliverable: Tier 1 minimum for warping; Tier 2 defended or run
- If single channels can't pass, the premise fails — the agent must say so, not silently double up

## CFS_Ex28_AgBuilding_partially_enclosed.txt
- Enclosure classification IS the design: the open side makes it partially enclosed (±0.55 GCpi) — an assumed-enclosed design fails roof C&C and frame uplift by roughly a factor of magnitude on net pressures
- Internal pressure loads the roof underside and solid-wall inside: purlin free-flange uplift, girt reversal
- The eave strut on the open side is a collector with a force, not a detail

## CFS_Ex29_Mezzanine_freestanding_existing.txt
- Classification first: Ch. 15 platform vs Ch. 12/13 — the R value follows from the cited determination, not habit
- Independence from the host: clearance at amplified drift, no bracing to host, slab reactions handed off (slab check scoped OUT) — bracing to the building or certifying the landlord's slab are both fails
- Anchor edge distance on a 6-in slab may govern before strap capacity — feasibility flag expected

## CFS_Ex30_ColdStorage_portal.txt
- Ct = 1.2 (cold roof holds snow): the warm-roof Ct = 1.0 reflex under-designs the whole roof by ~17% before drift
- Thermal-clip envelope = NO panel bracing credit anywhere: discrete brace/sag-rod schedule with forces is the load path
- Two drift sources (condenser platform, lean-to) map to specific members; 250-ft thermal movement decision with numbers; toughness spec at −10 F is the completeness check
- 240-ft jointless light-frame bar: serviceability/movement reasoning, not just strength
- Gable (flexible, sloped) diaphragm on one block, flat terrace-loaded diaphragm on the other, meeting at one line
- Snow drift against the step + wind C&C on the tall exposed step-wall face

## CFS_Ex6_WSP_5levels_Lplan.txt

- Re-entrant corner: collectors through the flexible diaphragm at the L-junction; justify force path with flexible idealization
- Two roof systems at the same top plate: truss uplift/outward thrust detailing vs flat-roof amenity live load on the same Level 5 walls
- Amenity deck (100 psf) over the west leg raises Level 5 wall/stud demands locally - one stud schedule cannot be uniform across wings

## CFS_Ex7_SteelSheet_6levels_Zplan.txt

- Narrow diaphragm throat between offset bars: agent must either design the throat as the only load path (chord/collector forces spike) or justify treating bars as separate structures with a seismic joint - EITHER is acceptable if carried through consistently
- h/w up to 4:1 segments: 2w/h strength reduction AND stricter drift; verify anchorage stiffness assumption
- 58.5 ft vs 65 ft limit: agent must state how hn is measured and that parapets/mech screens are excluded

## CFS_Ex8_StrapBraced_4levels_Tplan.txt

- R = 4 seismic vs 115-mph wind: different governing hazards by direction is likely - agent must run both and say which governs where
- Capacity design chain (strap yield -> everything else) is the core S400 E3 concept: an agent that sizes connections for the ELF force only (not Ry Fy Ag) fails this example
- Valley snow drift at the T-junction loads the stem's first interior truss zone
- Exterior walkway on the stem: eccentric line load + guardrail lateral on a braced wall line

## CFS_Ex9_Podium_5over2_Uplan.txt

- The two-stage procedure is the test: correct amplification of reactions handed to the podium, correct base definition for CFS height/drift, correct separate rho for each portion
- U-shape sits asymmetrically on a symmetric podium: courtyard-side wall lines carry disproportionate N-S shear in the north half
- Courtyard podium deck (occupied roof at +22') loads the same slab that anchors CFS hold-downs - flag coordination demands, don't design the concrete

## Ex10_Dual_SMF_SCBF_16levels_podium.txt

- Level 3 diaphragm is a BACKSTAY/transfer diaphragm: tower shears partially redistribute to podium-only frames. Design Level 3 diaphragm, chords, and collectors with Omega_0 combinations; report the backstay force.
- Confirm the 25% SMF dual-system check at podium levels where podium frames participate.
- Terrace live/snow-type loading on Level 3 outside tower must be carried by podium gravity framing sized for it.

## Ex11_BRBF_10levels_Uplan.txt

- Unequal wing heights create a mass/stiffness step at Level 7 in one wing only: check Type 1a/1b torsional irregularity above Level 7 where only the tall wing resists Y-direction shear at Grids 1-4.
- Courtyard-face frames (Y2, Y3, X3) act as re-entrant collectors: Omega_0 combinations on collectors along Grids C (4-6) and Grids 4 & 6 (C-F).
- SDC E triggers: no extreme torsional irregularity permitted (verify 1b does not occur); redundancy rho evaluation per 12.3.4.

## Ex12_SMF_14levels_chamfer_setback.txt

- Skewed chamfer frame: resolve stiffness contribution into both principal directions; accidental torsion with the chamfer removes symmetry entirely.
- Setback tower is offset (northwest quadrant): expect torsional irregularity above Level 10 - verify and amplify per 12.8.4.3.
- Grid 4 tower SMF terminates at Level 10: columns below are gravity-only - design the Level 10 collector to drag tower shear into the Level 1-9 perimeter frames; Omega_0 combinations on that collector.

## Ex13_OCBF_4levels_splitlevel.txt

- Split-level diaphragms: seismic force distribution between wings cannot use a single rigid diaphragm per level - semi-rigid or multi-diaphragm model needed.
- Snow drift + the 14-ft roof step: drift surcharge on the workshop roof along Grid 5.
- Wind may govern the workshop wing while seismic governs the office wing - two governing cases in one building.
- Grid 5 shared columns: bending from staggered connections at 6 elevations; check unbraced lengths between non-aligned framing points.

## Ex14_EBF_11levels_cruciform_hillside.txt

- Two-level base: west frames terminate at Level 1, east frames at B1. Design for the base-level shear distribution between short and tall columns; check the Level 1 diaphragm on the east half as a transfer level to the basement walls.
- Four re-entrant corners: collectors with Omega_0 at all eight inside-corner grid segments.
- Cruciform arms are 52 ft wide x 52 ft long: check diaphragm aspect ratios and chord forces at arm necks.

## Ex15_Dual_SMF_BRBF_20levels_weddingcake.txt

- Setback transitions L8 and L15: terrace-level diaphragms act as transfer diaphragms pulling perimeter SMF shear back to the core and to the new inboard SMF lines - Omega_0 on those collectors.
- Centroid drift with height + core held fixed in plan = growing static eccentricity in upper tiers; check the 25% SMF rule per tier, not just globally.
- Overturning at 255 ft on BRBF core columns: expect very large capacity-design axial forces; verify W14 feasibility or flag built-up sections.

## Ex16_IMF_3levels_Zplan_school.txt

- Three different roof diaphragm elevations, none aligned except classroom L1/link L1 floor; seismic force distribution requires per-wing diaphragm analysis with shared columns at Grids 4 and 6.
- Gym is a flexible bare-deck diaphragm while floors are rigid composite: mixed diaphragm assumptions in one model.
- RC III: Ie = 1.25 raises seismic demands ~25%; allowable story drift tightens to 0.015hsx per ASCE 7 Table 12.12-1 (agent to confirm applicable row).
- Snow drift accumulates in the 24-ft link roof valley from BOTH taller neighbors.

## Ex17_SCBF_9levels_offset_core.txt

- Extreme plan eccentricity: center of rigidity near Grid 7, center of mass near Grid 4.5 - the Y-direction frames see large torsional shear amplification, and the diaphragms span ~145 ft in drag.
- Collectors along Grids B and C from Grid 1 to Grid 6: full-length drag struts with Omega_0 combinations; sizes will be substantial - the agent should size them explicitly, not lump into gravity beams.
- If Type 1b (extreme torsional) results: SDC D still permits it but requires 12.3.3.4 25% diaphragm force increase, Ax amplification, rho = 1.3, and MRSA scaling to 100% - agent must apply all consistently, or move/add a braced bay west and justify the change.
- Level 10 partial story sits directly over the core: verify whether it is a "story" or rooftop structure per 11.2, and include its mass either way.

## Ex9_SPSW_12levels_Tplan.txt

- Torsion: stem piers are offset from the flange centroid - expect torsional response; verify Type 1a/1b torsional irregularity from MRSA results and amplify accidental torsion as required
- Penthouse plate piers (Y1, Y2) terminate on Level 12 HBEs of adjacent bays - design the transfer HBEs for the penthouse pier overturning boundary forces


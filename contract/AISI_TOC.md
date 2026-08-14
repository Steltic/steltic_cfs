# AISI S100 / S240 / S400 — tables of contents (query aid)

Use this to phrase **clause-anchored** RAG queries (e.g. "Section E2 flexural buckling Fn Ae",
"Section G5 web crippling one-flange loading", "S400 Type II perforated shear wall adjustment
factor"). The chapter map below is a NAVIGATION aid:
**anchor your citation on the section number the RETRIEVED text carries, not on this list** — if a
retrieved provision's number differs from the map, the retrieval wins. Confirm you pulled the
*design provision*, not commentary or an appendix scope note.

## AISI S100-16 (R2020) w/ S2, S3 — North American Specification for the Design of
## Cold-Formed Steel Structural Members  →  `engineering_standards_S100`
Chapter lettering intentionally mirrors AISC 360, so the map is familiar — but the CONTENT is
CFS-specific (effective width, distortional buckling, web crippling, screws):
- **A — General Provisions** (scope, materials: ASTM A1003/A653, Fy 33/50; E = 29,500 ksi)
- **B — Design Requirements** (LRFD φRn ≥ Ru; member properties; serviceability)
- **C — Design for Stability** (system stability, second-order; effective length)
- **D — Tension** (yielding Ag·Fy; rupture An·Fu — the strap check An·Fu ≥ Ag·Fy lives here)
- **E — Compression** — **E2 yielding & global (flexural / torsional / flexural-torsional)
  buckling** (Fn at KL/r; Pn = Ae·Fn — Ae at Fn, the EWM heart); **E3 local buckling interacting
  with global** ; **E4 distortional buckling** (the CFS-only limit state — never skip it for
  edge-stiffened flanges)
- **F — Flexure** — **F2 yielding & global (LTB) strength** (Se at Fn); **F3 local-global
  interaction**; **F4 distortional buckling**; inelastic reserve where permitted
- **G — Shear** — **G2 shear strength of webs** (kv, h/t regimes); web stiffeners
- **G5 — Web crippling** (one-flange / two-flange, interior/end, fastened vs unfastened flanges —
  GOVERNS at track/bearing points; combined bending + web crippling interaction)
- **H — Combined Forces** — **H1 combined axial + bending** (the stud beam-column check);
  combined bending + shear; bending + web crippling
- **I — Assemblies & Systems** (built-up members: interconnection spacing; wall studs & wall-stud
  assemblies — sheathing-braced design pointers to S240; floor/roof system provisions;
  metal roof/wall systems: purlin/girt R-factor uplift method, standing-seam tests)
- **J — Connections & Joints** — welds (arc spot/seam, fillet on thin sheet), **bolts** (bearing,
  tilting), **screws** (shear: tilting/bearing/pull-out; tension: pull-out/pull-over; the
  workhorse of every CFS connection), power-actuated fasteners, rupture at connections
- **K — Rational Engineering Analysis / testing** (Ch. K tests; rational analysis basis)
- **Appendix 1 — Effective Width Method (EWM)** — plate effective widths: stiffened (k=4),
  unstiffened (k=0.43), edge-stiffened with lip adequacy; stress gradients; THIS repo's
  `cfs_sections` computes App. 1 properties for you — cite the App. 1 basis when you use them
- **Appendix 2 — Direct Strength Method** (OUT OF SCOPE in this repo — EWM only; do not cite DSM)

## AISI S240-20 — North American Standard for Cold-Formed Steel Structural Framing
## →  `engineering_standards_S240`  (framing rules — REQUIRED on every light-frame brief)
Topic map (anchor citations on retrieved numbering):
- **General / materials / corrosion protection** (coating classes; the coastal-brief spec hook)
- **Structural framing members** — stud/track/joist section designators & minimum properties;
  web punchout rules (size/spacing/reinforcement); bearing stiffeners
- **Wall systems** — stud bracing (sheathing-braced vs mechanically braced — the DECLARED
  assumption), track-to-stud connection, built-up members (back-to-back / box chord studs:
  interconnection fastener spacing at high axial), headers (box/back-to-back/L-header)
- **Floor & roof systems** — joist bracing/blocking, web stiffening at reactions, cantilevers.
  (Practical span ceiling: single C-joists run out around 22 ft — the in-repo SFIA span data ends
  there; deeper floor plates need an intermediate bearing line or floor trusses, not a forced joist.)
- **Lateral force-resisting systems** — pointer to S400 (do not stop at the pointer; go to S400)
- **Trusses** (ex-S214) — chord/web member design, gusset & screw joints, quality
- **Nonstructural members** — pointer to S220 (NOT ingested; peripheral)
- **Installation / quality** (tolerances, splices — cite for constructability notes)

## AISI S400-20 — North American Standard for Seismic Design of Cold-Formed Steel
## Structural Systems  →  `engineering_standards_S400`  (walls/straps/SBMF — ALSO the
## wind-capacity source: its tabulated wall strengths carry WIND and SEISMIC columns)
- **A/B — General & design requirements** (expected strength factors Ry/Ω-analogues, capacity
  design principles, when seismic detailing applies vs R=3 "not specifically detailed")
- **C — Analysis** (drift: the four-term wall deflection expression — bending + shear + fastener
  slip + anchorage/rod elongation; this repo's `wall_line.s400_deflection` implements it)
- **E1 — CFS light-frame shear walls, wood structural panels (WSP)** — nominal strength tables
  (sheathing thickness × fastener size × edge spacing × one/two-sided), aspect-ratio (2w/h)
  reduction for h/w > 2, **Type II (perforated) provisions**: adjustment factor, end hold-downs +
  distributed track anchorage, uniform-height rule; chord stud & anchorage capacity design
- **E2 — steel-sheet sheathed shear walls** (same machinery, steel-sheet tables; expected wall
  strength into collectors and the story below)
- **E3 — strap-braced wall systems** — strap An·Fu ≥ Ag·Fy ductility check; **capacity design
  from Ry·Fy·Ag of the strap** into connections, chord studs, anchorage
- **E4 — CFS special bolted moment frames (SBMF)** — expected beam strength at design drift,
  bolt-bearing energy dissipation, beam/column limits
- **E5 — gypsum / other-material walls** (R=2 rows; wind-governed sites still use these tables)
- (Numbering caution: some editions letter these differently — anchor on the retrieved header.)


> NOT ingested: **AISI S310** (steel-deck diaphragms — if a bare-deck diaphragm brief needs it, SAY
> SO and ground deck values on a stated manufacturer/test basis; never invent S310 clause numbers)
> and **AISI S220** (nonstructural members — peripheral). Retired designations S110/S213/S214 are
> merged into S400/S240 — citing them as current is a staleness error, as is ANY AISC 360/341
> citation for a CFS member or system. Worked numeric examples: `cfs_design_examples`.

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
- **C — Design for Stability** (C1.1 direct analysis method, C1.2 effective length; second-order)
- **D — Tension** (yielding Ag·Fy; rupture An·Fu — the strap check An·Fu ≥ Ag·Fy lives here)
- **E — Compression** — **E2 yielding & global (flexural / torsional / flexural-torsional)
  buckling**: **P_ne = A_g·F_n** (Eq. E2-1, GROSS area), F_n from λc = √(F_y/F_cre) with F_cre
  from Appendix 2; **E3 local buckling interacting with yielding and global buckling**: E3.1 EWM
  P_nℓ = A_e·F_n ≤ P_ne (A_e per App. 1 at F_n — the EWM heart) or E3.2 DSM; **E4 distortional
  buckling** (P_nd from P_crd, App. 2 — the CFS-only limit state; never skip it for
  edge-stiffened flanges)
- **F — Flexure** — **F2 yielding & global (LTB)**: M_ne = S_fc·F_n ≤ M_y (Eq. F2.1-1, FULL
  unreduced section modulus; F_n from F_cre, App. 2); **F3 local-global interaction** (F3.1 EWM
  M_nℓ with S_e at F_n, or F3.2 DSM); **F4 distortional buckling** (M_crd, App. 2); inelastic
  reserve where permitted
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
- **M — Design for Fatigue** (stress range vs cycles for members and connections — monorails,
  crane/hoist supports, vibrating equipment; never "claimed" from a static run)
- **Appendix 1 — Effective Width Method (EWM)** — plate effective widths: stiffened (k=4),
  unstiffened (k=0.43), edge-stiffened with lip adequacy; stress gradients; THIS repo's
  `cfs_sections` computes App. 1 properties for you — cite the App. 1 basis when you use them
- **Appendix 2 — Elastic Buckling Analysis of Members** — the analytical/numerical elastic buckling
  values the main chapters call for: F_cre / M_cre (global), F_crℓ / P_crℓ / M_crℓ (local),
  F_crd / P_crd / M_crd (distortional). E2, E4, F2 and F4 cannot be evaluated without it — CITE it.
  (The Direct Strength Method itself lives in the main chapters, E3.2 / F3.2 / E4 / F4; this repo's
  `cfs_sections` computes the App. 1 EWM properties, and either E3.1 EWM or E3.2 DSM is permitted.)

## AISI S240-20 — North American Standard for Cold-Formed Steel Structural Framing
## →  `engineering_standards_S240`  (framing rules — REQUIRED on every light-frame brief)
Topic map (anchor citations on retrieved numbering):
- **General / materials / corrosion protection** (coating classes; the coastal-brief spec hook)
- **Structural framing members** — stud/track/joist section designators & minimum properties;
  web punchout rules (size/spacing/reinforcement); bearing stiffeners
- **B1.2.2 Wall studs** — all-steel vs sheathing-braced design (the DECLARED assumption);
  **B1.2.2.4 (US): sheathing-braced studs are ALSO checked WITHOUT the sheathing bracing for
  1.2D + (0.5L or 0.2S) + 0.2W (Eq. B1.2.2-1)**; B1.3 built-up members
- **B3 Wall framing** — stud design (B3.2: compression, bending, shear, axial + bending, web
  crippling / stud-to-track B3.2.5), headers (B3.3 back-to-back / box / double-L), track-to-stud
  connection, built-up chord studs (interconnection fastener spacing at high axial)
- **Floor & roof systems** — joist bracing/blocking, web stiffening at reactions, cantilevers.
  (Practical span ceiling: single C-joists run out around 22 ft — the in-repo SFIA span data ends
  there; deeper floor plates need an intermediate bearing line or floor trusses, not a forced joist.)
- **B5 Lateral force-resisting systems** — the WIND design of gypsum / fiberboard walls (mandatory),
  the alternative wind basis for WSP / steel-sheet walls, and R = 3 / non-S400 shear walls:
  **B5.2.2.3** nominal strength per unit length (Tables B5.2.2.3-1 steel sheet, -2 WSP, -3 gypsum,
  -4 fiberboard; B5.2.2.2 Type II Ca), **B5.2.3 φv = 0.65 (LRFD)**, B5.2.4 collectors / uplift
  anchorage, B5.2.5 design deflection; B5.3 strap-braced walls. Seismic design of S400 systems:
  go to S400.
- **Trusses** (ex-S214) — chord/web member design, gusset & screw joints, quality
- **Nonstructural members** — pointer to S220 (NOT ingested; peripheral)
- **Installation / quality** (tolerances, splices — cite for constructability notes)

## AISI S400-20 — North American Standard for Seismic Design of Cold-Formed Steel
## Structural Systems  →  `engineering_standards_S400`  (SEISMIC walls/straps/SBMF/diaphragms —
## no separate wind columns: WSP / steel-sheet Tables E1.3-1 / E2.3-1 are 'for Seismic and Other
## In-Plane Loads' (one vn set, φv 0.60 LRFD, E1.3.2 / E2.3.2) -- or S240 B5.2.2.3 with φv 0.65
## (B5.2.3); gypsum / fiberboard Table E6.3-1 is SEISMIC only -> wind per S240 B5.2.2.3.4 / .5)
- **A/B — General & design requirements** (A1.2.3: R = 3 in SDC B/C → S100/S240 only;
  expected-strength factors Table A3.2-1; capacity design B3)
- **C — Analysis** (C1 seismic load effects). The DESIGN DEFLECTION is per system: **E1.4.1.4**
  (WSP), **E1.4.2.3** (Type II WSP), **E2.4.1.4** (steel sheet), **E3.4.4** (strap) — bending +
  shear + fastener slip + anchorage/rod elongation. (This repo's `wall_line.s400_deflection` is a
  generic four-term SCREEN, not the S400 equation — compute the S400 expression for the design.)
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
- **E5 — WSP one side + gypsum the other side — CANADA ONLY** (no US/Mexico provisions)
- **E6 — gypsum board / fiberboard sheathed walls (US & Mexico)** — R = 2 systems; Table E6.3-1
- **E7 — conventional-construction strap-braced walls (Canada)**
- **F — Diaphragms** — F1 general (stiffness F1.3.1, overstrength F1.3.2, shear strength F1.4),
  **F2 CFS diaphragms sheathed with wood structural panels**, **F3 bare steel deck diaphragms** (seismic detailing; the deck strength itself is S310 — not ingested)
- (Numbering caution: anchor on the retrieved header.)


> NOT ingested: **AISI S310** (steel-deck diaphragms — if a bare-deck diaphragm brief needs it, SAY
> SO and ground deck values on a stated manufacturer/test basis; never invent S310 clause numbers)
> and **AISI S220** (nonstructural members — peripheral). Retired designations S110/S213/S214 are
> merged into S400/S240 — citing them as current is a staleness error, as is ANY AISC 360/341
> citation for a CFS member or system. Worked numeric examples: `cfs_design_examples`.

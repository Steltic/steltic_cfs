# CFS worked examples — quick index for `cfs_design_examples` queries

> **STATUS: the collection is NOT YET AUTHORED — queries return empty results.** That is
> normal: probe it AT MOST ONCE per session, never retry, and proceed directly from the spec
> text (which is sufficient and authoritative). This index describes the query taxonomy for
> when the collection lands.

When populated: pair your spec query (S100/S240/S400) with a `cfs_design_examples` query
(`collection="cfs_design_examples"`) for the matching worked example, and **mirror its check
sequence — the method, not its numbers.** Phrase the example query with the clause +
member/system type, e.g. `"E2 stud compression effective area sheathing braced worked
example"` or `"S400 E1 WSP shear wall fastener schedule aspect ratio worked example"`. If the
collection returns nothing for a niche check, say so in one line and proceed from the spec text —
never invent an example.

## Members (S100)
- **Stud in compression (E2/E3/E4):** global P_ne = A_g·F_n (E2; F_cre from App. 2); local-global
  P_nℓ = A_e·F_n ≤ P_ne (E3.1 EWM, A_e at F_n per App. 1); DISTORTIONAL P_nd from P_crd (E4, App. 2)
  checked separately; sheathing-braced vs all-steel basis, plus the S240 B1.2.2.4 unsheathed check
  (1.2D + (0.5L or 0.2S) + 0.2W without sheathing bracing).
- **Stud/joist beam-column (H1):** axial + out-of-plane wind (or axial + bending at headers);
  use the same effective-property basis as the isolated checks.
- **Joist / header flexure (F2/F3/F4):** M_ne = S_fc·F_n ≤ M_y (F2.1, full section), local
  M_nℓ with S_e at F_n (F3.1 EWM), distortional M_nd from M_crd (F4, App. 2); built-up box and
  back-to-back headers (S240 B3.3).
- **Web crippling at track / bearing (G5):** one-flange vs two-flange, end vs interior, fastened
  flanges; combined bending + web crippling at continuous-joist supports.
- **Track, shear (G2)** and shear + bending interaction.
- **Strap in tension (D):** Ag·Fy yield vs An·Fu rupture — the S400 E3 ductility inequality.
- **Built-up chord studs (I + S240):** interconnection fastener spacing under high axial.
- **Purlin/girt uplift (I / metal-roof provisions):** R-factor method (through-fastened) vs
  standing-seam (test-based) — know which regime the brief's roof is in.

## Walls & lateral (S400)
- **WSP / steel-sheet shear wall:** SEISMIC per S400 E1/E2 (table strength, φv 0.60 for WSP at
  E1.3.2); the S400 tables are for seismic AND other in-plane loads (one vn set, φv 0.60), or WIND
  per S240 B5.2.2.3 tables with φv 0.65 (B5.2.3); gypsum/fiberboard WIND is S240 only; edge
  spacing steps; one vs two-sided; 2w/h aspect-ratio reduction; design deflection E1.4.1.4 /
  E2.4.1.4.
- **Type II perforated wall (E1):** adjustment factor calc; end hold-downs + distributed track
  anchorage; uniform-height rule.
- **Strap-braced wall (E3):** capacity-design chain from Ry·Fy·Ag — strap connection, chord stud,
  anchorage each sized to the expected strap strength.
- **SBMF (E4):** expected beam strength at design drift; bolt-bearing mechanism.
- **Hold-down / rod stack:** cumulative tension (capacity-design demand on R>3 systems, not the
  ρ-ELF seed); device band vs computed rod (elongation into the design deflection).
- **Collector at a re-entrant corner:** Ω0-amplified demand, member + connection.

## Connections (S100 Ch. J)
- **Screws in shear** (tilting/bearing) and **tension** (pull-out/pull-over) — sheathing
  fasteners, strap connections, clip angles, track-to-stud.
- **Welds on thin sheet** (arc spot/seam, fillet effective throat on mils).
- **Bolts in CFS** (bearing with tilting; track-to-foundation).
- **Uplift clips / joist-to-wall** — the 0.9D+1.0W path components.

## Fatigue (S100 Ch. M)
- **Monorail / hoist support, vibrating equipment:** stress range at the detail vs the Ch. M
  category and cycle count — a static run is never a fatigue check.

## Portal frames (S100)
- **Portal frame member checks:** singly-symmetric channel LTB (F2), combined bending + torsion
  for loads off the shear center, knee distortional check, flange-reversal (uplift) bracing case.

## Building-scale method
- The whole worked four-story wall building (method + engine-reproducible answer key) is the
  **CFS_REFERENCE above** — mirror its 8-step sequence on every wall-framed brief.

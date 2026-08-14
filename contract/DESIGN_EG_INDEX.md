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
- **Stud in compression (E2/E3/E4):** global Fn at KL/r → Pn = Ae·Fn; local-global interaction;
  DISTORTIONAL checked separately; sheathing-braced vs unbraced bracing basis.
- **Stud/joist beam-column (H1):** axial + out-of-plane wind (or axial + bending at headers);
  use the same effective-property basis as the isolated checks.
- **Joist / header flexure (F2/F3/F4):** Se at Fn, local interaction, distortional; built-up box
  and back-to-back headers.
- **Web crippling at track / bearing (G5):** one-flange vs two-flange, end vs interior, fastened
  flanges; combined bending + web crippling at continuous-joist supports.
- **Track, shear (G2)** and shear + bending interaction.
- **Strap in tension (D):** Ag·Fy yield vs An·Fu rupture — the S400 E3 ductility inequality.
- **Built-up chord studs (I + S240):** interconnection fastener spacing under high axial.
- **Purlin/girt uplift (I / metal-roof provisions):** R-factor method (through-fastened) vs
  standing-seam (test-based) — know which regime the brief's roof is in.

## Walls & lateral (S400)
- **WSP / steel-sheet shear wall (E1/E2):** table strength → φvn; edge spacing steps; one vs
  two-sided; 2w/h aspect-ratio reduction; wind vs seismic table columns.
- **Type II perforated wall (E1):** adjustment factor calc; end hold-downs + distributed track
  anchorage; uniform-height rule.
- **Strap-braced wall (E3):** capacity-design chain from Ry·Fy·Ag — strap connection, chord stud,
  anchorage each sized to the expected strap strength.
- **SBMF (E4):** expected beam strength at design drift; bolt-bearing mechanism.
- **Hold-down / rod stack:** cumulative tension; device band vs computed rod (elongation into the
  four-term drift).
- **Collector at a re-entrant corner:** Ω0-amplified demand, member + connection.

## Connections (S100 Ch. J)
- **Screws in shear** (tilting/bearing) and **tension** (pull-out/pull-over) — sheathing
  fasteners, strap connections, clip angles, track-to-stud.
- **Welds on thin sheet** (arc spot/seam, fillet effective throat on mils).
- **Bolts in CFS** (bearing with tilting; track-to-foundation).
- **Uplift clips / joist-to-wall** — the 0.9D+1.0W path components.

## Portal frames (S100)
- **Portal frame member checks:** singly-symmetric channel LTB (F2), combined bending + torsion
  for loads off the shear center, knee distortional check, flange-reversal (uplift) bracing case.

## Building-scale method
- The whole worked four-story wall building (method + engine-reproducible answer key) is the
  **CFS_REFERENCE above** — mirror its 8-step sequence on every wall-framed brief.

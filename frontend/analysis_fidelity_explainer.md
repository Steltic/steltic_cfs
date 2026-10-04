# Analysis fidelity — which tier should I pick?

Before designing, choose how the analysis model treats **cross-section buckling** in your members.
Cold-formed steel sections are thin: under load, parts of the cross-section can buckle locally
before the member fails, which makes members *softer* than their full cross-section suggests. All
tiers design your members to the same code checks (AISI S100 — this choice never relaxes a strength
check); the tiers differ only in the **analysis model's stiffness** (gross vs effective section
properties), which affects drift, deflections, and how forces distribute.

## Tier 0 — Standard (default for wall-framed buildings)
Shear walls and strap-braced walls are modelled as per-line story springs whose stiffness comes
from the four-term wall deflection (bending, sheathing shear, fastener slip, anchorage), iterated
at the working load, distributed through a flexible (tributary) or semi-rigid diaphragm. On a
portal frame, Tier 0 runs the planar frame with GROSS section properties (the preflight warns).
**Pick for:** stud-wall / shear-wall buildings of any height, podium buildings.
**Runtime:** fastest.

## Tier 1 — Effective-stiffness frame (default for portal frames)
A planar frame analysis whose member axial and bending stiffness are iterated to the code
"effective" section properties at the working stress (AISI S100 Appendix 1), with the
direct-analysis stiffness reduction, notional loads and P-Δ on the strength combinations.
It does **not** model warping or torsion: for single (not back-to-back) channels the report
adds an analytic torsion SEED table, and the torsion/bimoment check is the engineer's.
**Pick for:** portal-frame buildings, canopies, purlin/girt design.
**Runtime:** comparable to Tier 0; slightly longer per run.

## Tier 2 — High fidelity (not yet implemented)
Selecting Tier 2 currently runs the Tier 1 analysis — there is no thin-walled (warping) element
or nonlinear section model in this version. For torsion-sensitive or very slender single-channel
portals, the engineer performs and documents a separate torsion check.
**Not a substitute** for the code member checks — those are always performed and reported.

## Quick guide

| Your structure | Recommended |
|---|---|
| Stud-wall building (shear walls, straps, podium) | **Tier 0** |
| Portal frame, canopy, purlin/girt design | **Tier 1** |
| Slender single-channel portal | **Tier 1** + a documented torsion check |

If you pick a tier that looks wrong for the declared structure (e.g. Tier 0 for a portal), the
preflight check will warn you before any design work starts. The report always states which tier
was used and what it assumes.

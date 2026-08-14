# Analysis fidelity — which tier should I pick?

Before designing, choose how the analysis model treats **cross-section buckling** in your members.
Cold-formed steel sections are thin: under load, parts of the cross-section can buckle locally
before the member fails, which makes members *softer* than their full cross-section suggests. All
three tiers design your members to the same code checks (AISI S100 Effective Width Method — this
choice never relaxes a strength check); the tiers differ in how faithfully the **analysis model's
stiffness** reflects that softening, which affects drift, deflections, and how forces distribute.

## Tier 0 — Standard (default for wall-framed buildings)
Elastic members with code-based reduced ("effective") section properties, updated iteratively at
the working stress. Shear walls and strap-braced walls use code-calibrated stiffness that already
includes these effects, so for stud-wall buildings this tier is accurate and fast.
**Pick for:** stud-wall / shear-wall buildings of any height, podium buildings.
**Runtime:** fastest.

## Tier 1 — Thin-walled members (default for portal frames)
Adds beam elements that carry **warping and torsion** and handle single-channel (asymmetric)
sections correctly, still with the code-based effective stiffness. Portal rafters, columns, and
channel rafters twist as well as bend — this tier captures that coupling; Tier 0 does not.
**Pick for:** portal-frame buildings, canopies, any design with single (not
back-to-back) channel members or long unbraced member lengths.
**Runtime:** comparable to Tier 0; slightly longer per run.

## Tier 2 — High fidelity (opt-in)
Tier 1 elements plus **nonlinear section response**: the member's stiffness degrades progressively
as local buckling develops, following curves derived from the same code method used for the
strength checks. The analysis is run incrementally rather than in one elastic step.
**Pick for:** torsion-sensitive or very slender portals where
second-order (P-Δ) effects dominate; when you want an independent system-level check on a design
near its limits.
**Not a substitute** for the code member checks — those are always performed and reported.
**Runtime:** noticeably longer (incremental nonlinear analysis per load case).

## Quick guide

| Your structure | Recommended |
|---|---|
| Stud-wall building (shear walls, straps, podium) | **Tier 0** |
| Portal frame, canopy, purlin/girt design | **Tier 1** |
| Slender single-channel portal | **Tier 2** |
| Verification pass on a design close to its drift or D/C limits | **Tier 2** |

If you pick a tier that looks wrong for the declared structure (e.g. Tier 0 for a portal), the
preflight check will warn you before any design work starts. The report always states which tier
was used and what it assumes.

# CFS worked reference — a four-story WSP shear-wall building (condensed method)

Treat this as a **METHOD TEMPLATE and a SELF-CHECK DISCIPLINE, not numbers to copy.** Your
building's geometry, loads and system differ; reuse the *sequence* and the grounding habit (every
capacity pulled from the RAG and cited), and finish with a numeric self-check. Unlike a published
textbook example, this reference's answer key is **reproducible in-repo**: the cfg below runs
through `cfs_engine`/`cfs_pipeline` and returns the quoted numbers — the repo test
`tests/test_cfs_reference_key.py` executes the cfg block below and asserts every number in the
answer key, so key and engine cannot drift apart silently. If your understanding of the machinery
disagrees with them, fix your understanding before designing.

## Problem (condensed)
4-story CFS light-frame residential building, 120 ft × 60 ft; story heights 10 / 9.5 / 9.5 / 9.5 ft
(hn = 38.5 ft). System both directions: **WSP-sheathed shear walls** (R = 6.5, Cd = 4.0, Ω0 = 3.0);
SDS = 1.00, SD1 = 0.50, S1 = 0.40 (SDC D — 65-ft height limit: 38.5 ft PASS), Ie = 1.0, RC II.
Loads: D_floor = 35 psf, D_roof = 22 psf, cladding 12 psf, S = 25 psf, L = 40 psf. Diaphragms
flexible. Wall lines X (resisting E-W force): A (y=0) and C (y=60 ft) with 2×24-ft segments per
story, B (y=30 ft) with 4×24-ft; lines Y: 1 (x=0) and 3 (x=120 ft) with 3×16-ft, 2 (x=60 ft) with
6×16-ft. Drift inputs `wall_props = dict(chord_area_in2=2.4, Gp_kip_in=18.0, en_in=0.015,
k_anchor_kip_in=250.0)`. NOTE on `k_anchor_kip_in=250`: that is a CONTINUOUS-ROD-class anchorage
stiffness; the discrete bolted-device band in `wall_line.HOLDDOWN_BANDS` is ~50 kip/in, 5× softer.
This example's drift answer (0.0175) depends on the 250 — a bolted-device design must EITHER use
k≈50 in `wall_props` (drift grows) or switch to rods; your own building's k_anchor must match your
selected anchorage, per line where they differ (`WallLine(..., wall_props=...)`). Find: ELF base
shear; per-line unit shears; a sheathing/fastener schedule; the chord/hold-down stack; drift vs
the 0.025 limit.

<!-- REF_CFG -->
```python
import cfs_systems as CS, wall_line as WL
H = {1: 10.0, 2: 9.5, 3: 9.5, 4: 9.5}
def segs(n, L):
    return {k: [(L, H[k])] * n for k in H}
cfg = dict(
    stories=4, heights_ft=[10.0, 9.5, 9.5, 9.5], plan_ft=(120.0, 60.0),
    D_floor=35.0, D_roof=22.0, clad=12.0, snow=25.0, L_floor=40.0,
    seis=CS.seis_cfs(1.0, 0.5, 0.4, "wsp_shearwall"), system="wsp_shearwall", risk_cat="II",
    structure_kind="wall", analysis_fidelity=0, diaphragm="flexible",
    lines_x=[WL.WallLine("A", 0.0, segs(2, 24.0)), WL.WallLine("B", 30.0, segs(4, 24.0)),
             WL.WallLine("C", 60.0, segs(2, 24.0))],
    lines_y=[WL.WallLine("1", 0.0, segs(3, 16.0)), WL.WallLine("2", 60.0, segs(6, 16.0)),
             WL.WallLine("3", 120.0, segs(3, 16.0))],
    wall_props=dict(chord_area_in2=2.4, Gp_kip_in=18.0, en_in=0.015, k_anchor_kip_in=250.0))
```

## Method — the sequence to mirror
1. **Weights & ELF** (ASCE 7-22 §12.8, computed by the engine): story weights from area dead +
   tributary cladding (+ 15% of the flat roof snow only where p_f > 45 psf, ASCE 7-22 12.7.2
   item 4 — here p_f = 25 psf, so NO snow in W; partitions ≥ 10 psf where 4.3.2 requires them,
   `cfg['partition_psf']`); Ta = Ct·hn^x (0.02/0.75 — light-frame CFS has no
   special formula); Cs = SDS/(R/Ie) with the SD1 cap and minima; V = Cs·W; Fx by w·h^k. Spot-check
   Cs and V by hand before trusting anything downstream.
2. **Tributary distribution (flexible diaphragm):** each wall line takes the tributary width to the
   adjacent lines' midlines, ±5% accidental shift; per-line story shear stacks downward; unit shear
   v = V_line / Σ(segment lengths) at each story. Interior lines with wide tributaries (B, 2) govern.
3. **Wall shear design per line per story (S400 E1):** pick sheathing (thickness, one/two-sided)
   and edge-fastener spacing so φ·vn ≥ v at every story — RAG-ground the table value; apply the
   2w/h aspect-ratio reduction where h/w > 2 (24-ft and 16-ft segments at 9.5 ft: h/w < 1, none
   here); step the schedule down the height with demand. **v without a thickness + fastener
   schedule is not a design.**
4. **Chords & hold-downs (cumulative, top-down):** overturning tension per line accumulates
   story-by-story net of 0.9D; the base value sizes the device — within the bolted band (≲15–20
   kip) use a discrete hold-down; beyond it SWITCH TO A CONTINUOUS ROD (computed: tension, PL/AE
   elongation + take-up; elongation feeds drift). Chord studs: built-up per S240 interconnection,
   compression per S100 E2/E3/E4 with the sheathing-braced assumption STATED.
5. **Studs, track, web crippling:** gravity stud stack (cumulative, live reduction compounded),
   H1 interaction with out-of-plane wind, **G5 web crippling at track bearing**; stud schedule
   never lighter below.
6. **Drift (S400 four-term):** bending + shear + fastener slip + anchorage elongation, amplified
   ×Cd/Ie, vs 0.025 (Table 12.12-1 row 1: ≤ 4 stories with finishes that accommodate the drift,
   RC II). The pipeline's drift table is a four-term SCREEN; the design value is the S400
   E1.4.1.4 deflection of the selected schedule. Every failing row must be redesigned.
7. **Both hazards:** wind MWFRS run and compared per direction; net-uplift 0.9D+1.0W anchorage
   path checked roof→wall→floor→foundation even where seismic governs in-plane shear.
8. **Self-check vs the answer key below**, and reconcile any discrepancy beyond a few percent
   BEFORE you report.

## Answer key (engine-reproducible — for self-check only, do NOT copy into your building)
Pure ELF (no ρ) unless stated. SDC D (S_DS = 1.00) → the seeded strength demands carry
**ρ = 1.3** (12.3.4.2 conditions not shown); drift never carries ρ (12.3.4.1 item 2).

<!-- ANSWER_KEY_BEGIN -->
| quantity | value |
|---|---|
| W_kip | 1060.2 |
| Ta_s | 0.309 |
| Cs | 0.1538 |
| V_kip | 163.1 |
| k | 1.00 |
| Fx_kip_L1 | 20.0 |
| Fx_kip_L2 | 38.7 |
| Fx_kip_L3 | 57.6 |
| Fx_kip_roof | 46.7 |
| lineB_V_kip_s1 | 85.6 |
| lineB_V_kip_s2 | 75.1 |
| lineB_V_kip_s3 | 54.8 |
| lineB_V_kip_s4 | 24.5 |
| lineB_v_plf_s1 | 892 |
| lineB_v_plf_s2 | 783 |
| lineB_v_plf_s3 | 571 |
| lineB_v_plf_s4 | 255 |
| T_base_kip_every_line | 24.2 |
| rho | 1.3 |
| seed_lineB_v_plf_s1_with_rho | 1160 |
| seed_T_cum_kip_with_rho | 31.5 |
| seed_T_cd_kip_Om0eff_2p5 | 60.5 |
| stud_P_kip_L4 | 0.07 |
| stud_P_kip_L3 | 0.35 |
| stud_P_kip_L2 | 0.64 |
| stud_P_kip_L1 | 0.92 |
| worst_drift_ratio | 0.0191 |
<!-- ANSWER_KEY_END -->

How to read it: W = 3 floors × 35 psf × 7,200 sf + 22 psf × 7,200 sf + 12 psf cladding × 360 ft ×
(10 + 9.5 + 9.5 + 4.75 ft) = 756 + 158.4 + 145.8 = **1,060 kip**; Cs = S_DS/(R/Ie) = 1.0/6.5 =
0.1538 (the S_D1 cap 0.5/(0.309·6.5) = 0.249 does not govern); **V = 163.1 kip** each direction.
Line B (interior, half the 60-ft depth) carries half of each story force: story shears
85.6 / 75.1 / 54.8 / 24.5 kip over 96 ft of wall → **892 / 783 / 571 / 255 plf** pure ELF (the
seeded slot shows ×ρ = 1160 plf at story 1) — a two-sided (or dense-nailed) WSP schedule at the
base stepping to a light single-sided schedule at the roof. Base chord/hold-down tension
(overturning = Σ cumulative-story-shear × story height, over the line length): **24.2 kip on every
line** pure ELF (A/C carry half B's shear over half B's length); the seed shows ρ·T = 31.5 kip and
the Ω0-level capacity-design seed (Ω0 = 3.0 − 0.5 for the flexible diaphragm, footnote b) 60.5 kip
→ **beyond the ~20-kip bolted band: continuous RODS** designed for min(Ω_E·V_n stack, Ω0-level).
Typical bearing stud (16 in. o.c., 2-ft trib, 1.2D + 1.6L seed): cumulative P = 0.07 / 0.35 /
0.64 / **0.92 kip** (roof→L1). Worst amplified drift-screen ratio **0.0191** (Y lines, story 1)
≤ **0.025** — PASS on the screen; the design value is the S400 E1.4.1.4 deflection of the
selected schedule. (Key regenerated 2026-10 from the engine: the previous key used the retired
20%-snow-in-W rule — W = 1,096 kip, V = 168.6 kip — and was silent on ρ.)

## Discipline to carry into every design
- Ground EVERY capacity in the RAG (S100 + S240 + S400 together); cite the exact
  clause/equation you applied. The framework computes demands and drift — never a capacity.
- State the diaphragm idealization, the per-line bracing assumption, and the anchorage scheme
  explicitly; reconcile the model-vs-tributary gate and every drift flag.
- Finish with a numeric self-check against a known answer (this reference, or your own hand ELF +
  tributary calc), and reconcile discrepancies beyond a few percent BEFORE you report.

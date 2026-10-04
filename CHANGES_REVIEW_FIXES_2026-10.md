# Review fixes — CFS Steel (steltic_cfs)

October 2026. These changes fix the engine, gate, report, contract and hub defects found in the October 2026 review of the Steltic repos (30 test buildings, 6 nonlinear runs and a static review). Every Critical and High item was re-checked by an independent pass against the 2022 editions and hand calculations.

**Out of scope, deferred to a separate RAG/retrieval change set:**
- the grounding server and the search tool's escalation ladder;
- grokbot conversion and indexing;
- collection names and retrieval-only contract text;
- the nonlinear Collect retrieval and validators. Component values for nonlinear (SNL) runs are verified manually by the user with the RAG and supplied in the hinge-params file.

**Effect on existing designs.** Most of these fixes make demands larger, which is the correct direction: the old values were unconservative. Designs produced before these changes should be re-run, and some will now fail gates they used to pass. That is expected.

Item IDs (HR-xx, CFS-xx, NL-xx, HUB-xx) refer to the review's verified bug register.


## Workstream `cfs-walldrift`

### Status

| ID | Status | Files | What changed | Test(s) | Before → after |
|---|---|---|---|---|---|
| CFS-01 | FIXED | cfs_engine.py, wall_line.py (+ package/report hooks) | Story drift = S400 single-story deflection + chord strain from the overturning above + h × rotation carried up from the stories below (chord axial strain under the cumulative overturning + rod T·h/EA + take-up, or device T/k), per 12.8.6.5. The gate (`drift_flags`) and the report use the cumulative value; the single-story value is also reported. | `test_two_story_stack_closed_form_rotation_carried_up`, `test_ex1_line_A_upper_story_drift_includes_rotation_from_below`, `test_drift_gate_uses_cumulative_drift` | Ex1 line A (selected schedule rebuilt from the job's design files), Cd·Δ/Ie s1–s4: 0.0121/0.0105/0.0086/0.0070 → 0.0111/0.0170/0.0205/0.0226 (agent hand calc 0.0122/0.0182/0.0224/0.0243). Ex2 line A s6: 0.0012 → 0.0216; Ex6 LP1 s5: 0.0073 → 0.0243; Ex7 XS-C1 s3: 0.0078 → 0.0198; Ex3 line A s8: 0.0013 → 0.043. |
| CFS-04 | FIXED | wall_line.py (`s400_deflection`, `wall_story_response`) | AISI S400-20 Eq. E1.4.1.4-1 for WSP (β 67.5/55, ρ 1.85/1.05; ω1=s/6, ω2=0.033/t_stud, ω3=√((h/b)/2), ω4=1) and Eq. E2.4.1.4-1 for steel sheet (β=29.12·t/0.018 per Eq. E2.4.1.4-3a, ρ=0.075·t/0.018 per Eq. E2.4.1.4-4a, ω4=√(33/Fy), G=11,300 ksi). The slip term is ω1^1.25·ω2·ω3·ω4·(v/β)², with v per sheathed face. The constant slip term is gone. E3.4.4 strap mechanics: v·Ld³/(E·A·b) + 2vh³/(E·Ac·b) + (h/b)δv. E6.4.1.4 mechanics for gypsum/other walls uses an agent-grounded Ga. WSP G·t defaults to the C-E1.4.1.4 example (77,500 lb/in) and is reported as assumed. Legacy Gp/en props run on a conservative assumed schedule with a loud preflight warning. | `test_s400_wsp_eq_E1_4_1_4_1_matches_hand_calc`, `test_s400_steel_sheet_eq_E2_4_1_4_1_matches_hand_calc`, `test_s400_slip_is_quadratic_and_has_no_constant_term`, `test_legacy_props_use_assumed_schedule_and_say_so`, `test_strap_mechanics_E3_4_4` | Ex1 s1 terms now equal the agent's independent hand calc (bend 0.0113, shear 0.0301, slip 0.1375). Before, slip was the constant 0.75·en. |
| CFS-05 | FIXED (wall path) | cfs_engine.py (+ pipeline/consistency/report hooks) | θ = Px·Δe/(Vx·hsx) per line and story (12.8.7, Eq. 12.8-18). Px uses 1.0D + 0.5·(0.4 or 0.8)L0 per 12.8.6.1 (`gravity_Px_level`, override `Px_level_kip`). θmax = 0.5/(βCd) ≤ 0.25 (Eq. 12.8-19), with β ≥ 1.25/Ω0 and the 7-22 0.10 floor (`theta_beta`). For 0.10 < θ ≤ θmax, drift is multiplied by 1/(1−θ) and a warning is raised. For θ > θmax, `stability_flags` go into the package and fail consistency until resolved. The report shows θ/θmax per row. | `test_theta_max_eq_12_8_19_and_7_22_floor`, `test_theta_per_story_hand_calc_and_gate`, `test_theta_between_010_and_max_amplifies_drift` | Ex3 (selected schedule): θ was never computed (agent's single-story hand θ was 0.089). Now θmax = 0.23 (X) / 0.27 (Y) > 0.125, with 28/35 stability flags. Ex1/Ex6/Ex7: θ ≤ 0.031. **The portal path (P/V·Δ/h) is NOT done** (not in my files). |
| CFS-08 | FIXED | wall_line.py (`fit_positions`, new `trib_scales_for`) | Interior gaps must be > 0. The legacy p0=0 result is kept when it is already strictly ordered; otherwise a maximin search runs; otherwise ValueError, which points to `trib_scales_for` (`on_infeasible="approx"` keeps the old fallback). | `test_fit_positions_strictly_ordered_and_exact` (4 cases), `test_fit_positions_legacy_ordered_result_unchanged`, `test_fit_positions_infeasible_raises_and_trib_scales_alternative` | [100]×4 on 40 ft: [0,20,20,40] with shares 25/21.4/28.6/25 → [5,15,25,35] with 25 each. [150,200,250,200,150] on 95: L4/L5 16.7/20.1 → 21.1/15.8 (exact). Ex6 positions unchanged. Ex7 Y-lines (legacy had 120.0, 120.0 coincident) now raise. |
| CFS-09 | FIXED (PARTIAL on Ω0 design numbers) | wall_line.py, cfs_engine.py (+ consistency lint) | Per-story presence: `distribute()` resolves each story onto the present lines. A discontinued line's shear goes to its neighbours by the lever rule (`transfers`, 12.3.3.3/Type 4 basis). `WallLine(base_story=k)` sends it to a stepped foundation instead. `diaphragm_extent_ft` limits a level's diaphragm. Per-level weights: `level_weights_kip`, `area_sf_by_level`, `perimeter_ft_by_level`, `roof_area_sf_by_level`. Absent stories return no line results, so no 1/0 occurs. A story with no resisting line raises `NoResistingLineError`, and the consistency pre-lint reports it, along with zero-length segments. | `test_missing_story_line_is_finite_and_transfers_by_lever_rule`, `test_story_without_any_line_raises_and_lints`, `test_zero_length_segment_is_linted_not_divided`, `test_base_story_line_is_founded_not_transferred`, `test_split_level_extent_and_per_level_weights`, `test_semirigid_with_absent_line_is_finite` | Ex11 with the patch removed (P5/P6 have no L1 wall): before, v = 7.95e9 plf, T = 1.6e8 kip, drift = inf, Infinity in the JSON, P4/P7 at 306 plf. After: finite JSON; P4/P7 s1 at 611 plf (2/3–1/3 lever rule, same as the agent's hand transfer). Ex13 with the patch removed: inf → finite. Ex14 with the patch removed: KeyError → runs; with native `base_story=2` + extent it reproduces the patched X shears (max 2520 plf). |

- **as-saved:** the cfg as stored.
- **unpatched:** runtime patch removed, or the bug geometry restored.
- **s400:** the selected schedule rebuilt from each job's design files (`s400_schedules.py`).
- **native:** Ex14 declared with the new keys instead of the patch.

Results are in `battery_before.json` and `battery_after.json`.

### Behaviour changes users will notice

- **Drift goes up on every multi-story wall stack**, typically 2–3× at the upper stories. Several saved designs now fail the drift gate with their selected schedules:
  - Ex2: 0.030 vs 0.020.
  - Ex3: 0.052 vs 0.020.
  - Ex6: 0.026 vs 0.020.
- **New θ gate.** Ex3-type tall stacks fail with θ ≈ 0.23–0.27 > θmax 0.125.
- **Legacy `wall_props`** (chord_area/Gp/en/k_anchor only) on WSP and steel-sheet systems still run. The slip term then uses a conservative ASSUMED schedule (s=6, 33-mil, OSB or 18-mil sheet, one face), and a preflight warning lists the line-stories. Drift is usually much larger until the selected schedule is declared. Strap lines without `strap_area_in2` and gypsum lines use Gp as the linear shear rigidity, with no constant slip term.
- **`fit_positions` raises** where it used to return coincident positions. The Ex7 cfg as saved now raises at import; use `trib_scales_for` or `on_infeasible="approx"`.
- **Absent stories:**
  - A line with an empty or missing segment list at a story transfers its shear (it no longer gets a 1/n share).
  - Line results, wall slots and hold-down bases skip absent stories.
  - A story with no line in a direction raises.
- **Monkeypatched cfgs that replace engine internals may break.** Ex14 as saved raises a clear error; it runs natively with `base_story` + `diaphragm_extent_ft`. Ex13 as saved still runs.
- `analyze_line` returns more fields (drift_own_in, drift_rot_in, theta…). `K_kip_in` is now the effective secant, V/total drift. dist rows gain V_story, F_level, transfer fields, and `v_unit_plf` is now the story unit shear.
- `build_cfs_models.py` (the RAG model-doc generator, out of scope) now reports its legacy-prop specs as FAIL because of the assumed-schedule warning. It already failed before, on rod switches.

### Follow-ups

- CFS-05 portal path: θ = P·Δ/(V·h) for `cfs_frame` is not done (cfs_frame.py not assigned).
- CFS-09: the Ω0 transfer/collector **numbers** for `discontinuity_transfers` are seeded (V, targets, basis) but not amplified or design-checked. Direction-specific systems (`seis_by_dir`, CFS-28) are still unsupported, so Ex14 Y uses the X seismic coefficients.
- Rod tension per story uses the cumulative T at the base of the story with no dead-load relief. This is conservative; the agent can refine it.
- Story drift for flexible diaphragms is per line, with K-weighted segment averaging. Segments of very different lengths on one line are approximated.
- `build_cfs_models.py` specs and the `agent.py` mock cfg still use legacy wall_props; they should migrate to S400 inputs (RAG/doc scope).
- The WSP G·t default (C-E1.4.1.4 example) should become a per-panel table once the Gv·tv values are grounded in the corpus.

## Workstream `cfs-wallloads`

### Status


**Tests.** `PYTHONPATH=$PWD:$PWD/steel_engine python -m pytest tests -q` gives 33 passed: 17 existing plus 16 new in `tests/test_cfs_wall_loads.py`. The module self-tests pass for cfs_systems, cfs_engine (including the OpenSees dual-path check), cfs_pipeline and consistency. The `build_cfs_models` W04/W07/W08/W11/W13 "unintended ROD switch" failures were already there before this branch and are unchanged.


| ID | Status | Files | What changed | Test | Before → after evidence |
|---|---|---|---|---|---|
| CFS-39 | FIXED | cfs_engine.py | `story_weight` now includes the 12.7.2 terms: storage (item 1, from `storage`, `storage_levels` or `L_by_level`), partitions (≥10 psf), and items 3/6 (`extra_mass_floors`). Exception (a) is opt-in (`storage_5pct_exception`) and the 5 % share is always reported. `load_screens()` brings the storage and platform (15.1.1) preflight screens to the CFS path, and their output lands in `preflight_warnings`. The package shows a `weight_by_level_kip` breakdown. | test_cfs39_* | Ex29 with the D_roof workaround removed (`D_roof=12`, `structure_kind='mezzanine'`): W 25.8 → 85.8 kip, V 2.26 → 7.51 kip (3.3×). The saved Ex29 cfg (workaround still in it) is unchanged at 85.8 and now gets 3 warnings ("treated as a ROOF", platform classification). |
| CFS-40 | FIXED | cfs_pipeline.py | `live_companion_factor`: the companion L factor in 2.3.1 combos 3a/4a and 2.3.6 combo 6 is 0.5 only when Lo ≤ 100 psf and the occupancy is not a garage or assembly; otherwise it is 1.0. The overstrength combo 6 now carries L and 0.15S (it carried no L before). The labels show the factor actually used. | test_cfs40_* | Ex29 E/W combos L 0.5 → 1.0. Ex2/3/11/13/14: wind combos L 1.0 → 0.5, which Exception 1 permits there. |
| CFS-41 | FIXED | cfs_engine.py, cfs_pipeline.py, cfs_systems.py | New `structure_kind` values `'platform'`/`'mezzanine'` (Tier 0), plus a `top_level_is_floor` flag. A storage level at N also counts as a floor. The top level then carries D_floor, live load and storage (weight and seeds). `stud_axial_stack` and `_chord_dead_relief` use the floor values. New `gravity_framing` seeds for joist, beam and post: wu, Mu, Vu, the L/360 and L/240 limits, and I_req. | test_cfs41_* | Ex29 clean, post/stud seed: 0.31 → 4.57 kip per stud. Post Pu 41.2 kip (192 sf, matches the review's 41.6). Joist Mu 9.15 kip-ft, I_req 15.6 in⁴. Beam (12 ft) Mu 61.8 kip-ft. |
| CFS-06 | FIXED | cfs_systems.py, cfs_pipeline.py | `capacity_design_required` covers every S400 system except the A1.2.3 waiver (R = 3 in SDC B/C), and `CD_WALL_SYSTEMS` now includes gypsum. Ω_E comes from E1.3.3/E2.3.3/E3.3.3/E6.3.3. T_cd = min(Ω_E·Vn stack, Ω0 stack). Vn comes from `selected_Vn_kip`, or for straps from `strap_Ag_in2` + `strap_Fy_ksi`; without Vn the Ω0 stack is used and the package says so. | test_cfs06_* | Ex11 (gypsum, SDC B): there was no capacity_design block → now Ω_E 1.5, Ω0_eff 2.0. Line N T_cd = 33.2 kip vs the ELF seed 16.6 (the review's hand value was 29.2 at its chosen Vn). |
| CFS-07 | FIXED (in owned files) | cfs_systems.py, cfs_pipeline.py | Gypsum now maps to S400 E6 (label, std), with the E6.3.1.1 aspect limits (2:1 gypsum, 1:1 fiberboard, ≥24 in.) flagged per segment. The wind governing basis is taken from `wind_capacity_basis`: S400 Tables E1.3-1/E2.3-1 with φ 0.60, and gypsum/fiberboard per S240 B5.2.2.3.4/.5 with φ 0.65. The "S400 WIND capacity columns" text is gone from the engine. | test_cfs07_* | Ex11: the governing_basis strings now cite S240 B5.2.2.3.4 with φ 0.65. |
| CFS-10 | FIXED | cfs_pipeline.py | `wind_line_screen` distributes wind by face width, independent of `trib_scale` and with no 5 % shift. It envelopes Case 1 with Case 2 ± (Fig. 27.3-8, Note-4 pressure block 1.2p/0.3p on the face halves). `trib_width_ft` overrides are allowed, and lines with trib_scale ≠ 1 get a warning. The `_wind_line_screen` alias is kept. | test_cfs10_* | Ex11 P-lines v_wind 517 → 874 plf (review: 941 by face width). The wing Q lines go 328 → 778 plf. N (X) 913 → 469 and G26 977 → 394 plf; the seismic mass scales no longer leak into wind. |
| CFS-11 | FIXED (roof uplift → follow-up) | cfs_pipeline.py | `wind_story_forces` now measures z from grade (`z_base_ft` or the two-stage podium height), puts the leeward wall at qh with Cp(L/B), uses G per 26.11 (Gf from 26.11.5 when n1 < 1 Hz; n1 from `n1_hz`, T_analytical, or a stated 1/(Cu·Ta)), applies the 27.1.5 16/8-psf minimums, and adds optional parapet / roof projection. A legacy `Cnet` is mapped to Cp_lw = −(Cnet − 0.8) at qh. | test_cfs11_* | Ex3, Y base wind with the Cnet calibration removed: 392.8 kip (z from grade 15 ft, Gf 0.95, n1 0.55 Hz). The review's hand value was 394.2; the old engine (Cnet 1.3) gave 334.8. Ex2 Y 346.6 → 365.6. Ex2 X 115.5 → 96.5 (L/B = 3 gives Cp_lw −0.25). |
| CFS-18 | FIXED | cfs_systems.py, cfs_pipeline.py | Strap Ω_E is numeric: Ry(Fy) + max(v_finish, 0.2) ≤ 1.8, so Gr 50 → 1.3 and Gr 33 → 1.7. The basis now limits Ry·Fy·Ag to the connection and net section. There is a per-line Ω_E·Vn stack. | test_cfs18_* | Box strap test: Vn from Ag·Fy·w/√(h²+w²) is exact to the hand value, and T_cd ≤ the Ω0 stack. |
| CFS-19 | FIXED | cfs_systems.py, cfs_pipeline.py | `strap_ductility_check` implements E3.4.1(a): Methods 1 and 3 are declared; Method 2 is computed for both inequalities. A failure goes into `capacity_design.strap_ductility` (ok=False) and a preflight "STRAP DUCTILITY FAILS". | test_cfs19_* | Gr 33: RtFu/RyFy = 1.09 < 1.2 → FAIL. Gr 50, An 0.26 / Ag 0.30 → PASS. |
| CFS-20 | FIXED | cfs_systems.py, cfs_pipeline.py | `cfg['type_ii']` takes Ca per line and story, either directly or from the Table E1.3.1.2-1 interpolation. T_cum, T_wind and T_cd are divided by Ca story by story (E1.4.2.2-2). The package seeds the uniform uplift t and the collector v = V/(Ca ΣLi) (E1.4.2.2.1/.3) at both strength and capacity level. Wall slots carry v/Ca. | test_cfs20_* | Ex13 with the agent's Ca values, T_cd: NB 30.2 → 42.2 kip (agent's hand value 42.3), N 42.9 → 48.7 (hand 49.5 using min Ca), S-W 47.4 → 52.9 (hand 51.8). Uplift t at capacity level is 1,548–1,949 plf at story 1, matching the review's "1,500–1,840 never seeded". |
| CFS-21 | FIXED | cfs_pipeline.py | `diaphragm_idealization` allows the footnote-b Ω0 − 0.5 only when 12.3.1.1 holds (no concrete or topping > 1.5 in., per `diaphragm_material` / `diaphragm_topping_in`, and every line meets the drift limit) or 12.3.1.3 holds (`diaphragm_MDD_ADVE` > 2). Rigid and semi-rigid get no cut. Otherwise it warns loudly. | test_cfs21_* | 3-in. topping declared flexible: Ω0_eff 2.5 → 3.0 plus a warning. The self-test fixture fails drift and now keeps 3.0. |
| CFS-22 | FIXED (engine/package; consistency clause text is cfs-gates') | cfs_engine.py, cfs_pipeline.py | `CE.fpx` implements 12.10-1/-2/-3. `collector_seeds` gives per-level, per-direction Fx, Fpx and the design level: 12.10.2.1 max(a, b, c) in SDC C–F; S400 B3.4 (Ω0 cap) or 2.3.6 + Fpx in SDC A/B. Dict entries (`trib_fraction`, `transfer_kip`) give a numeric demand. | test_cfs22_* | Ex29 collector design level 15.0 kip (Ω0·Fx). Ex14 shows different X and Y levels (Ω0 2.0 vs 2.5). Before, the seed was text only. |
| CFS-27 | FIXED (in cfs_systems / engine) | cfs_systems.py, cfs_engine.py | `drift_limit` row 1 is now keyed on ≤ 4 stories plus drift-tolerant finishes for any non-masonry system. Footnote a is opt-in. 12.12.1.1 Δa/ρ applies to SBMF in SDC D–F. Called without the finishes flag (as consistency does), the ≤ 4-story row is returned, so a correct 0.025 is not rejected. `run()` uses `drift_limit_for`: the declared `drift_tolerant_finishes`, the legacy light-frame assumption stated when undeclared, and a tighter `cfg['drift_limit']`. | test_cfs27_* | `drift_limit('sbmf', 1)`: 0.020 → 0.025 (consistency no longer says "tighten"). SBMF on the engine path without the flag stays at 0.020 with a stated basis. A tighter `cfg['drift_limit']` is now honoured by the engine screen (Ex3 is 8 stories, so it was already 0.020). |
| CFS-28 | FIXED | cfs_engine.py, cfs_pipeline.py | New cfg keys `seis_by_dir` / `system_by_dir` / `rho_by_dir` (12.2.2), and `line_systems` with least-R (12.2.3.3) plus the opt-in per-line exception. These carry through ELF, Cd, drift, combos (Ω0 per direction), capacity design `by_direction`, slots and `elf_by_direction`. | test_cfs28_* | Ex14 (its monkeypatch is still active) gives identical seismic seeds (wind changes per CFS-10/11). Native mixed cfg: V_X/V_Y = 6.5/4 and overstrength combos Ω0 X 2.0 / Y 3.0. |

### Behaviour changes users will notice

- Storage floors: W and V go up. On a storage mezzanine that declares `structure_kind='mezzanine'` they are 3.3× higher. New warnings appear for 125-psf floors without `storage` and for single-level "roof" storage models.
- Combos: 1.0L on >100-psf, garage or assembly floors. Wind combos drop to 0.5L where Exception 1 permits. The overstrength combo 6 now includes L + 0.15S. Combo labels show the L factor.
- Gypsum buildings now get a `capacity_design` block and T_cd seeds (≈ 2× ELF). consistency's numeric capacity-design gate does not cover gypsum yet (`_CD_GATE_SYSTEMS`); that is for cfs-gates.
- Wind per line changes substantially on irregular plans that use `trib_scale` (Ex11: some lines +70 %, others −50 %). Case 2 raises edge-half lines by up to 1.2×. Tall or podium buildings get more wind (z from grade, leeward qh, Gf). Long directions (L/B > 1) get a little less.
- Type II lines (when declared) get chord and hold-down seeds ÷ Ca plus uplift t seeds.
- Ω0_eff is no longer reduced when the diaphragm is topped or rigid, or when any line fails drift. The pipeline self-test fixture now shows Ω0_eff 3.0. Collector seeds are numeric.
- SBMF ≤ 4 stories: consistency accepts 0.025. The engine uses it only when `drift_tolerant_finishes=True`.
- Mixed systems are native. Old monkeypatch cfgs (Ex14) still run and give the same numbers.

### Follow-ups

- CFS-11 roof uplift in T_wind (S240 B5.2.4.2.1) is not added; the basis says so. The flexible-building eccentricity uses eQ = 0.15B (Eq. 27.3-4 with eR = 0 would be ≤ eQ, so this is conservative). Partially enclosed or open-front internal pressure still needs `Cp_*` overrides.
- Roof Lr/S is still absent from `stud_axial_stack` at a roof level (D only), as before.
- Omega_E·Vn needs the agent to declare the selected Vn (`selected_Vn_kip`), or `strap_Ag_in2` + `strap_Fy_ksi` for straps. Without it the seed stays at the Ω0 level, and the package says so.
- Collector seeds for string `collector_lines` are whole-diaphragm level forces. The agent applies the tributary fraction, or uses dict entries.
- Type II deflection (E1.4.2.3) is noted but not wired into the spring model (that is the cfs-walldrift area).
- Saved cfgs with workarounds that now double count: Ex3 `wind.Cnet=1.55` (an old-engine calibration) now maps to Cp_lw −0.75 at qh, so remove it. Ex29 `D_roof=43.25` should become `D_roof=12, structure_kind='mezzanine'`.

## Workstream `cfs-portal`

### Status

| ID | Status | Files | What changed | Tests | Before → after evidence |
|---|---|---|---|---|---|
| CFS-02 | FIXED | cfs_frame, cfs_pipeline | Wind from BOTH sides (seed and mirrored legacy overrides, `wind_mirror=False` opts out); `governing`, member slots, torsion companion envelope every `col_*`/`raf_*` label; signed moments (+ = inside flange in tension) and paired P/V. | `test_package_envelopes_both_columns_and_rafters`, `test_mirrored_wind_is_the_mirror_image` | Demo pkg col M 621.4 (0.9D+1.0W, col_L) → 900.6 (col_R), P 6.64 → 12.58. Ex19 old pkg col 1070 vs true 1293 (−17 %) → pkg = true 1288. Ex26 agent pkg col 420 vs true 581 (−28 %) → 619. |
| CFS-03 | FIXED | cfs_frame | `Frame2D.solve` recovers q = k_used·u − feq with the assembled (scaled + geometric) stiffness; `equilibrium_residual()`; residual stored per combo. | `test_recovery_uses_scaled_stiffness`, `test_recovery_includes_pdelta_and_matches_reactions`, `test_every_portal_combo_in_equilibrium` | Cantilever H=1 at 200 in, scale 0.8: element M 250 → 200 (= reaction). Ex16 col M 1172.9 → 938.3 (same loads, no SW; agent's own patched solve 936.7). Ex28 2711 → 2183; Ex10 1242 → 1002. Residual ≤ 1e-6 on all combos. |
| CFS-24 | FIXED | cfs_frame | AISI S100-16 C1.1: 0.90 on EA/EI (C1.1.1.3(a)), τb on EI from αPr/Py per element (b), Ni = Yi/240 (Eq. C1.1.1.2-1, α = 1) at every gravity node, in every combo, in the direction of the lateral resultant (gravity-only: direction of the gravity sway); P-Δ on every compressed member to convergence; Δ2/Δ1 reported. | `test_direct_analysis_constants`, `test_notional_is_yi_over_240_in_gravity_combo`, `test_notional_follows_lateral_resultant` | 1.4D: ΣRx = ΣRy/240 exactly (was 0.002, always +x). |
| CFS-23 | FIXED (portal) | cfs_frame | 2.3.1: 3a wind-free branch (L not acting / with L) and every wind case at 0.5W; 2a with L; 4a with L; 5a for every wind case; crane L with ±lateral. | `test_wind_free_and_uplift_combos`, `test_crane_impact_lateral_and_combos` | Demo 15 → 86 combos; `1.2D+1.6Lr`, `1.2D+1.0S_bal` present. |
| CFS-25 | FIXED (listed parts) | cfs_frame, cfs_pipeline | Member self-weight in D (`self_weight=True` default); `point_loads`, `crane` (4.9.3 impact 25/25/10/0 %, 4.9.4 20 % lateral ±, 4.9.5 longitudinal reported out-of-plane), `knee_braces` (pin-ended struts, geometry required); `custom_build` → PortalInputError; 7.3.3 pm as separate `S_min` (Table 7.3-4 by RC); monoslope uses 7.5 partial patterns, not gable unbalanced; 7.6.1 only for 2.38–30.2°; `truss_roof` reroutes wind too. | `test_self_weight_in_dead_case`, `test_point_load_inserts_node_and_balances`, `test_custom_build_refused`, `test_minimum_snow_pm_case`, `test_crane_*`, `test_knee_braces_need_explicit_geometry` | Ex19 6-ply + monorail (new native) col M 1621 vs agent's hand crane analysis 1614 (pipeline without crane 1288: +26 %). Ex16 SW 2.96 kip → col M 938 → 1036 (gravity now governs). Ex19 pm=30 > ps=21 → `S_min` governs. |
| CFS-26 | PARTIAL | cfs_frame, cfs_pipeline | Apex slot = moment at the ridge node (both signs); monoslope/flat beam get no ridge slot; knee slot covers BOTH knees with signed max/min and combos; members carry M-max, Pc-max, Pt-max, inside/outside pairs. Not done: component-mode items (`_TIER_MIN` lacks "component", DC=0 auto-fill, re-render overrides) and `report.py:2814` KeyError (report.py not mine). | `test_apex_slot_is_the_ridge_moment_and_knees_both_sides`, `test_monoslope_has_no_apex_slot` | Demo apex seed 957.7 → 503.1 (true ridge). Ex16 1151 → 641; Ex10 1242 → none (flat beam). Ex10 col paired P 4.61 → 8.0 (+ Pc-max pair 14.7). |
| CFS-15 | FIXED (engine) | cfs_frame | `wind['enclosure']` enclosed / partially_enclosed / partially_open / open → Table 26.13-1 GCpi 0.18/0.55/0.18/0; legacy `enclosed=False` → partially enclosed + preflight warning; Fig. 27.3-1 Cp(θ, h/L) with both windward branches + same-sign interpolation, θ<10 distance zones, leeward wall Cp(L/B), along-ridge case (frame distance), 27.3.3 overhang soffit, `open_sides`; open buildings: free-roof CN Figs. 27.3-4/5/7 (values from the corpus), A/B × clear/obstructed, both directions, fascia (inverted parapet), column drag; `qh_psf` now WITHOUT Kd (`qh_Kd_psf` added); service sway over every case. | `test_cp_interpolation_matches_hand_values`, `test_enclosure_gcpi[*]`, `test_closed_cases_cover_*`, `test_open_monoslope_uses_free_roof_cn`, `test_legacy_enclosed_false_is_loud`, `test_open_side_has_no_wall_pressure` | Cp seed reproduces the agents' hand values (Ex16 −0.754/−0.431, Ex28 −0.688/−0.490, Ex19 2nd branch −0.18). Ex16 seeded: col M 1036 → 1249 (2nd branch), uplift 6.45 → 7.44 (along-ridge, +15 %). Ex28 W2 service sway 2.59 → 5.14 in. |
| CFS-16 | FIXED (engine) | cfs_frame, cfs_pipeline | ELF Cs with SD1/T cap and minima; ±E with ρ (12.3.4, 1.3 default in SDC D–F); Ω0 pair (role `overstrength`, anchorage Ω0 seeds); seismic drift Cd·δxe/Ie vs Table 12.12-1 (÷ρ in SDC D–F, `T_drift` per 12.8.6.2, footnote-a flag); 12.2-1 height/NP screen keyed on apex height; S400 E4.4 SBMF screens (one story ≤ 35 ft, pin bases, HSS 8–12 in and w/t ≤ 1.40√(E/Fy), C beam Gr 55, t ≥ 0.105, d 12–20, h/t ≤ 6.18√(E/Fy), 1-in 8-bolt); `sbmf_expected_shear()` (E4.3.3, implicit Me solve); package SBMF capacity-design basis = Ve, not Ry·Fy·Ag. | `test_seismic_drift_theta_rho_om0`, `test_sbmf_expected_shear_matches_hand_calc`, `test_sbmf_screens_fail_fixed_base_and_c_column`, `test_hss_column_runs_on_portal_path` | Ve helper = Ex10 agent's independent calc 4.319 kip exactly. Ex10 native HSS run: K 1.684 kip/in (agent 1.71); strength-level Δ = 15.1 in = 0.059 h → "SEISMIC DRIFT NG" (agent's own strength-level 14.9 in; passes only with 12.8.6.2 forces → declare `seis['T_drift']`). |
| CFS-05 (portal) | FIXED | cfs_frame, cfs_pipeline | θ = Px·Δ·Ie/(Vx·hsx·Cd), Px = D + L + 0.15S, θmax = 0.5/(βCd) ≤ 0.25; FAIL → preflight warning; rows in `drift_table`. | `test_seismic_drift_theta_rho_om0` | Ex28 2x proxy θ = 0.085, 8/6-ply 0.021; Ex10 0.039; Ex19 (6-ply + crane) 0.037. |
| CFS-17 | FIXED | cfs_frame, cfs_sections | `Nx<des>` (N even 2–8, `/box` toe-to-toe), HSS, Z in `frame_section`; EFF loop runs the frame at n-ply stiffness and the EWM on PER-PLY demands (no hard-coded ×2); `built_up()` Iy from the joint plane, I-section Cw, boxed J. | `test_nply_sections_and_per_ply_eff_iteration`, `test_nply_built_up`, `test_back_to_back_iy_from_joint_plane`, `test_hss_*`, `test_z_section_point_symmetric` | Ex16/19/28/10 run natively (no shims). Demo 4x vs 2x: sway < 0.6×. Built-up 800S250-97 Iy 3.003 → 3.177. |
| CFS-36 | FIXED (listed defects) | cfs_sections, cfs_frame | Unlipped tip t/2 fix + flat B−r−t; x0 AISI sign (negative for C) + `m` = |x0| − xbar; torsion e0 = m (was |x0|+xbar, ~2.2×); F hats refused; Fy from SFIA Fy_avail; S100 1.3 overall lip D and sin²θ; both-compression gradient branch; B4.1 h/t warnings; misleading clamp/residual text corrected. Not done: `sfia_oracle_axial_lateral.csv` re-extraction (extractor only). | `test_track_flange_tip_reaches_sfia_iy[*]`, `test_x0_sign_and_web_to_shear_centre`, `test_furring_hat_is_refused`, `test_gr33_only_54mil_defaults_to_33ksi`, `test_both_compression_gradient_branch`, `test_validation_gate_improves` | 600T125-54 Iy 0.0505 → 0.0539 (SFIA 0.054); 1600T150-97 0.165 → 0.183 (0.183); 250U050-54 Iy −13 % → +3 %. Validation worst 77 % (furring) → 18 %; effective checks > 5 %: 42 → 22. Demo single-channel e0 1.96 → 0.92 in. |
| CFS-12 (code) | FIXED | cfs_frame | `check_units()` (called by `run`) raises PortalInputError for ×12 dimensions (span > 400, eave > 100, apex > 120, spacing > 80, purlin/girt > 30 ft), implausible psf/mph, apex < eave without `monoslope`, and `custom_build`; soft warnings for unusual feet. | `test_x12_inch_cfg_refused`, `test_custom_build_refused` | 480/168/204/180 cfg → "portal dimensions look like INCHES (x12)". Contract text → cfs-gates (below). |

### Behaviour changes users will notice

- Many more portal combinations (17 → 40–130) with new names: `W_R`, `W2_R`, `*_b2` (2nd windward-roof Cp),
  `Wpar`/`Wpar2` (along ridge), open-building `W_A_obs`…, `S_min`, `S_part_k`, `L(H+)`/`L(H-)`, `E_neg`,
  `…Om0E…` (role `overstrength`, excluded from `governing`). The first two wind cases keep the names `W`/`W2`.
- Member slots envelope both sides: column/rafter demands rise where the leeward/right member governs (demo +45 %),
  while every deformation-induced force drops (recovery fix, −12…−25 % on the jobs). Apex seeds drop to the true
  ridge moment. Package members gain `demand_pairs`, `M_signed_kipin`, `governing_label`; connections gain signed
  max/min per joint; anchorage gains per-base, compression and Ω0 seeds; `pkg['combos']` lists every combo.
- Frame self-weight is now in D (`self_weight=False` to suppress — e.g. Ex28 smeared it into D_roof).
- `wind_pressures_psf` overrides are mirrored by default (`wind_mirror=False` when the supplied cases are already
  directional, e.g. Ex28's open-side north/south cases).
- `wind_basis['qh_psf']` no longer contains Kd (use `qh_Kd_psf`); `M_kipin_stations` sign convention is now
  "+ = inside flange in tension" and includes element mid-points.
- New preflight warnings (loud, by prefix): `WIND ENCLOSURE:` (undeclared / legacy bool), `SEISMIC DRIFT NG`,
  `THETA … > theta_max`, `SEISMIC SYSTEM SCREEN:` (e.g. not_detailed NP in SDC D–F), `SBMF SCREEN FAIL (…)`,
  `P-DELTA DIVERGED` (now also covers rafter compression), `P-DELTA OFF`.
- Errors instead of silent runs: ×12 cfgs, `custom_build`, crane impact below 4.9.3, knee braces without geometry,
  odd ply counts, furring `F` designators.
- `gross_props()['x0']` is negative for C/track (AISI sign); 54-mil Gr-33-only SFIA designators default to 33 ksi.
- Component mode (Ex25) now runs its shell surrogate first-order (`pdelta=False`) — the second-order solve of
  that 100-ft 2x1200 surrogate is sway-unstable once rafter compression is included.

### Follow-ups

- consistency.py (not mine): `_height_limit_issues` still keys on heights_ft only (the engine now screens heights
  itself via `CS.height_check(apex_ft)`); add an enclosure-vs-brief-openings lint and a crane/fatigue lint; read the
  new drift/θ rows.
- cfs_systems.drift_limit (CFS-27) still returns 0.020 for SBMF/portals; the seismic drift row uses
  `cfg['drift_limit']` when declared, else that function (÷ρ in SDC D–F), and supports `drift_no_limit_single_story`.
- report.py (CFS-26 remainder): `.get('governing_combo')` for agent-added members; render the new demand pairs,
  seismic/θ rows and crane block. Component-mode `_TIER_MIN`/DC=0 items unchanged.
- Wall path (not mine): `enumerate_combos` lacks the 3a +0.5W variant; the wall-path SBMF capacity-design text in
  `build_package` still says "Ry·Fy·Ag" (SBMF is a portal-path system; the portal package now states Ve).
- `rag_v2/cfs_models.jsonl` (RAG, out of scope) still quotes "0.8E + 0.002 notional"; regenerate with
  `python3 steel_engine/build_cfs_models.py` when RAG work resumes (gates pass on this branch).
- 7.6.1 drift surcharge is still a user-declared uniform-equivalent factor (`unbalanced_factors`); per-span
  unbalanced for multi-span roofs and troughed free roofs (Fig. 27.3-6) are not seeded (warned).
- Fig. 27.3-1 Cp values are the printed ASCE 7-22 values (corpus figure garbled); they reproduce three agents'
  independent interpolations. The CN tables are taken from the corpus. Eq. E4.3.3-5 is reconstructed (garbled print)
  and reproduces the Ex10 agent's hand calc.
- Tier labels (CFS-37) still overstate the planar solver.

## Workstream `cfs-gates`

### Status

#### CFS-13 — FIXED
- **Files:** consistency.py, report.py, pipeline.py, steltic/agent.py, contract/*, steltic/contract.py
- **What changed:**
  - One authoritative package: `consistency.package_path`. CFS jobs use `calc_package_cfs.json`. A legacy `calc_package.json` is read only when it is the sole file. Having both files is an issue.
  - Report, gate, consistency, NEXT_STEP and contract all use this name.
  - `report.build_report(name)` dispatches CFS jobs to `build_report_cfs_from_disk`.
- **Tests:** `test_package_path_rules`, `test_pipeline_backup_merge_and_build_report_dispatch`, `test_completion_gate_two_package_files`
- **Before → after:**
  - `build_report` on Ex1, Ex13, Ex3 and Ex10 raised `KeyError 'heights'` on all four. All four now render (1.0, 1.06, 2.1 and 0.33 MB).
  - Report captions now name `design/calc_package_cfs.json`.

#### CFS-14 — FIXED
- **Files:** pipeline.py
- **What changed:**
  - `design_and_report` on a CFS cfg copies a FILLED package to `calc_package_cfs.json.filled.bak` and warns. An older, different `.bak` is kept with a timestamp.
  - A legacy `calc_package.json` is moved aside.
  - `merge_fills(name)` (or `keep_fills=True`) carries the fills back by slot id and appends agent-added slots. When a seeded demand changed, it clears the D/C and sets `recheck_after_rerun`.
- **Test:** `test_pipeline_backup_merge_and_build_report_dispatch`
- **Before → after:**
  - Before: the Ex10 re-run silently lost every capacity.
  - Now, re-running the four saved designs gives `.filled.bak` plus a warning, and `merge_fills` restores them:
    - Ex1: 100 slots
    - Ex13: 129 slots
    - Ex3: 155 slots
    - Ex10: 10 slots + 28 appended
  - After the merge, Ex10 consistency = 0 issues.

#### CFS-29(a) — FIXED
- **Files:** cfs_gates.py (new), pipeline.py
- **What changed:** an independent tributary recomputation (simple-span widths from line positions × ELF Fx) is compared with the engine line shears on flexible diaphragms. Flags go to `model_vs_tributary_flags`, and the package gets an `independent_tributary` block.
- **Tests:** `test_independent_tributary_is_not_an_identity` (hand check: X2 = ΣFx·31/62), `..._flags_line_without_wall_at_story`
- **Before → after:**
  - Ex13 engine gate: {X: [], Y: []} before. It now gives **40 flags**, because it detects the cfg's monkey-patched `WL.distribute` (S-W ratio 1.12, NB 0.71).
  - A declared `trib_scale=0.5` line: engine gate [] → flagged.
  - Ex1: 0 flags (clean geometry).

#### CFS-29(b–e) — FIXED
- **Files:** consistency.py
- **What changed:**
  - **(b)** Seed-owned keys are excluded from keyword gates. System checks now require computed agent evidence.
  - **(c)** `drift_table`, `anchorage` and `schedules` are checked. A failing drift row fails even with a `*_resolution` note. Portal rows need a verdict. θ > θmax fails.
  - **(d)** A waiver needs ≥ 15 characters of justification. An NG waiver also needs `waiver_scope` set to existing / by_others / out_of_scope, or a justification that names one of those. NG waivers are listed in the report.
  - **(e)** The hold-down band uses the design tension, else max(T_cum, T_bay, T_cd, T_wind).
- **Tests:** `test_raw_seed_does_not_pass_its_own_system_checks_nor_fail_r9`, `test_uplift_screen_ignores_seed_combos`, `test_failing_drift_row_fails_even_with_resolution`, `test_theta_over_limit_fails`, `test_waiver_with_dc_over_one_needs_scope`, `test_check_runs_waivers_anchorage_and_schedules`, `test_holddown_band_uses_design_or_capacity_design_tension`
- **Before → after (review repros):**
  - Raw WSP seed `_system_checks_issues`: [] → 2 issues.
  - `{DC: 2.0, waived: "x"}`: [] → 2 issues.
  - Drift row DC 1.6 + resolution: 0 → 1 issue.
  - "bolted" at T_cum 17.1 kip / T_cd 49.5 kip: silent → flagged at 49.5 kip.
  - Ex10 re-seeded portal rows: unverdicted → 2 issues, which clear once the fills are merged.

#### HR-14 port (CFS-29) — FIXED
- **Files:** steltic/agent.py `_completion_gate`
- **What changed:**
  - The gate requires `design/consistency_result.json`, written by `consistency.check`, to be PASS for the CURRENT package sha1.
  - It also checks: drift rows and θ, unresolved flags, two-stage ELIGIBLE, `report.html` newer than the package, string D/Cs, schedule rows, and justified/scoped waivers.
- **Tests:** `test_completion_gate_requires_consistency_drift_and_fresh_report`, `..._two_package_files`
- **Before → after:**
  - Before: consistency FAIL, drift FAIL and θ were all ignored.
  - After, each of these problems is reported:
    - missing, stale or failing stamp
    - stale report
    - drift row FAIL
    - θ 0.2 > 0.1
    - unscoped NG waiver
    - NOT EVALUATED two-stage

#### CFS-30 — FIXED
- **Files:** consistency.py
- **What changed:**
  - Exact, normalized demand/capacity keys.
  - Interaction rows (H1 / "combined") are not recomputed.
  - 0.01 absolute D/C floor.
  - Seed `instruction`/`basis` text is exempt from R9.
  - Type II and B8 screens are negation-aware and use agent text only (CFS B8 counts only proprietary/SJI-type items).
  - Schedule rows are recomputed.
- **Tests:** `test_phiPn_is_not_read_as_both_demand_and_capacity`, `test_interaction_rows_...`, `test_schedule_rows_...`, `test_type_ii_screen_ignores_negations`
- **Before → after:** `{phiPn_kip 50, Pu_kip 40, DC 0.8}` was flagged "50/50 = 1.000"; it is now clean. The raw seed failed its own R9 check; it is now clean.

#### CFS-31 — FIXED
- **Files:** report.py
- **What changed:**
  - The hold-down table shows the seeds (ρ-ELF / per-bay / Ω0 / wind) beside the agent's DESIGN T, device and D/C.
  - WAIVED renders everywhere; a waived-NG table is added.
  - Basis now includes: declared RC (not inferred from Ie), SDC, ρ, ELF, period, wind/enclosure, governing hazard, drift limit, θ, Ca, tier and two-stage status.
  - The engine per-line tables are labelled "screen". The package drift table is the record (portal: "Serviceability").
  - Added a portal loads chapter.
  - Chapter exceptions are printed instead of passed. Agent text is escaped.
- **Test:** `test_pipeline_backup_merge_and_build_report_dispatch` (asserts design values are rendered)
- **Before → after:** the Ex13 hold-down table showed "T_cum 17.1" next to D/C 0.93 computed on 49.5. It now shows "ELF·ρ 17.1 / Ω0 42.9 / W 4.8 | **49.5** (T_design_kip) | rod | 0.93".

#### CFS-32 — FIXED (wired as check + amplification)
- **Files:** cfs_gates.py, pipeline.py, consistency.py, report.py
- **What changed:**
  - `cfg['two_stage']` takes R_lower, rho_lower, K_lower_kip_in, and either T_combined_s or W_lower_kip (+ podium_height_ft).
  - The resulting `two_stage_framework` block contains:
    - (a) K_lower ≥ 10·K_upper, where K = V/δe at the top of each portion
    - (b) T_entire ≤ 1.1·T_upper (Rayleigh; an entire-structure estimate when only W_lower is given)
    - (d) amplification max((R/ρ)u/(R/ρ)l, 1)
    - the amplified per-line reactions
    - status ELIGIBLE / NOT ELIGIBLE / NOT EVALUATED; anything but ELIGIBLE fails consistency and the gate
  - The Rayleigh period per direction is also reported, for every wall job.
- **Tests:** `test_two_stage_eligibility_and_amplification`, `test_two_stage_rayleigh_estimate_...`, `test_rayleigh_period_single_spring_hand_check` (T = 2π√(W/gK))
- **Before → after (Ex3):**
  - Before: `structure_kind='podium'` changed nothing, and the agent derived 1.625 by hand.
  - Now, with the cfg as saved: NOT EVALUATED, giving 2 consistency issues.
  - With K_lower = {X 48573, Y 44975} (from the agent's package) and W_lower 2500 kip: ELIGIBLE. Stiffness ratio 259 / 293, T ratio 1.003 / 1.002, amplification **1.625**. Line A reactions 47.2 → 76.7 kip.

#### CFS-33 — FIXED
- **Files:** contract/AISI_TOC.md, AGENT_START.md, README_AGENT.md, DESIGN_EG_INDEX.md
- **What changed:**
  - S100 App. 2 is "Elastic Buckling Analysis" (Fcre/Pcrd/Mcrd for E2/E4/F2/F4); cite it.
  - E2 Pne = Ag·Fn; F2.1 Sfc·Fn; E3.1/F3.1 EWM with Ae/Se.
  - Ch. M fatigue.
  - S240 B1.2.2.4 unsheathed check (Eq. B1.2.2-1).
  - Wind shear walls: S240 B5.2.2.3 with φv 0.65 (B5.2.3); "S400 wind columns" removed.
  - Design deflection: S400 E1.4.1.4 / E2.4.1.4 / E1.4.2.3 / E3.4.4 (not Ch. C).
  - Gypsum = E6 (E5 is Canada-only).
  - S400 A1.2.3; S400 F diaphragms.
  - Accidental torsion on flexible diaphragms.
  - 12.12.2 separation √(δDE1²+δDE2²).
- All checked against the corpus.
- **Test:** n/a (text)

#### CFS-34 — FIXED
- **Files:** contract/CFS_REFERENCE.md, AGENT_START.md, steltic/contract.py
- **What changed:**
  - The CFS_REFERENCE cfg is printed in the doc. The answer key was regenerated from the engine (no 20 % snow rule; ρ = 1.3 stated) and is asserted by a test.
  - The nonexistent `cfs_sections.props` is replaced by `gross_props`.
  - Added a racks out-of-scope line and a brief-deviation protocol (`brief_deviations`).
- **Test:** `test_cfs_reference_answer_key_reproduces`
- **Before → after:** key W 1,096 / V 168.6 / v 922 / T 25.6 / drift 0.0188 → engine 1060.2 / 163.1 / 892 / 24.2 / 0.0191.
- Retrieval-only items (ASCE 7 "not in RAG", cfs_opensees_models, cfs_design_examples) were deliberately left unchanged.

#### CFS-12 (contract) / CFS-27 (wording) / CFS-41 (contract) — FIXED
- **Files:** AGENT_START.md, README_AGENT.md, steltic/contract.py, consistency.py
- **What changed:**
  - Portals go through `cfs_frame` in FEET. The portal cfg schema is documented from `cfs_frame` and `cfs_pipeline`. Span/eave/spacing unit lint added (consistency + preflight).
  - Table 12.12-1 row 1 wording, footnote a, and Δa/ρ.
  - Mezzanine = ASCE 7 Ch. 12 building (15.1.1, 13.1.1); CFS beams/posts/joists are designed, not a "failed brief".
- **Test:** `test_cfs_preflight_runs_storage_platform_rack_and_units`

#### CFS-35 — FIXED (report side)
- **Files:** report.py
- **What changed:** the stud stack is labelled "SEED, not a schedule"; the agent's section, schedule, bracing and design stack are shown when present. Unfilled hold-down devices are labelled "(seed band)".
- **Test:** dispatch test (asserts "seed band")
- **Before → after:** seeds were shown as schedules; they are now labelled as seeds.

#### CFS-37 — FIXED
- **Files:** cfs_systems.TIERS + single-channel note, frontend/index.html, frontend/analysis_fidelity_explainer.md, report, contract
- **What changed:** the tiers now describe what runs: a planar EA/EI frame with the Ae/I_eff iteration, no warping DOF, and Tier 2 = Tier 1.
- **Test:** existing self-tests
- **Before → after:** the text read "dispBeamColumnAsym / Du & Hajjar"; it is now honest.

#### CFS-38 — FIXED
- **Files:** test_buildings/CFS_Ex20–24 (deleted), CFS_ASSESSMENT_RUBRIC.md
- **What changed:** the stubs are removed. `EXAMPLE_BRIEFS` already excluded them; no frontend list contains them (the dropdown is built from `/api/config`).
- **Test:** n/a

#### Preflight on the CFS path (CFS-39 companion) — FIXED
- **Files:** preflight.py, pipeline.py
- **What changed:** `preflight.check` dispatches CFS cfgs to `check_cfs`, which covers:
  - units and portal lint
  - seis keys and system
  - **storage 12.7.2** (+ the 2.3.6 L = 1.0 note)
  - **Ch. 12 vs Ch. 15 (15.1.1)** for platforms and mezzanines
  - **racks → ERROR (out of scope)**
  - drift limit vs RC
  - podium without `two_stage`
  - missing wind
- The CFS pipeline now runs it.
- **Test:** `test_cfs_preflight_runs_storage_platform_rack_and_units`
- **Before → after:** an Ex29-like cfg (L_floor 125, "storage mezzanine platform") got no preflight (it never ran on CFS). It now gets a 12.7.2 WARN and a 15.1.1 classification WARN, and a rack brief gets an ERROR.

### Behaviour changes users will notice

- **More consistency / completion-gate failures on real designs:**
  - Independent-tributary flags on any job with `trib_scale`, collinear tricks or a patched distribution (Ex13: 40 flags). These need a fix or `model_vs_tributary_flags_resolution`.
  - Failing drift rows can no longer be cleared by a resolution note.
  - Portal eave/apex rows need a verdict.
  - Waivers need a sentence; an NG waiver needs a scope.
  - Capacity-design "expected" / strap checks need numbers (seed text no longer counts).
  - `anchorage` and `schedules` slots are now checked.
- **Podium jobs** (`structure_kind='podium'` or `cfg['two_stage']`) fail until `cfg['two_stage']` supplies K_lower and T_combined or W_lower and the result is ELIGIBLE. Ex3 as saved is NOT EVALUATED (2 issues).
- **The app's completion gate** refuses to finish until `consistency.check` has PASSED on the final package and `report.html` was re-rendered after the last package edit.
- **Re-running `design_and_report`** prints a backup warning and creates `*.filled.bak`. A legacy `design/calc_package.json` on a CFS job is moved to `calc_package.json.filled.bak` / `.stale.bak`.
- **Reports:**
  - `report.build_report(name)` now works on CFS jobs.
  - Hold-down tables show the design tension.
  - New basis rows, plus two-stage, independent-tributary and waived-NG sections.
- **Tributary keyword check removed for CFS packages:** the consistency check no longer emits "never distributes lateral force by TRIBUTARY AREA" on CFS packages, because they are tributary by construction.
- **Preflight:** CFS runs now print preflight findings (storage, mezzanine, racks, units).

### Follow-ups

- **Seed side of CFS-29e** (owner of `cfs_pipeline`): `cfs_pipeline.pick_holddown` still seeds `device_class` from the ρ-ELF tension. The gate now uses design/T_cd.
- **Backups bypassed on direct calls:** calling `cfs_pipeline.design_and_report` directly (not via `pipeline`) still re-seeds without a backup. Consider moving `backup_filled_packages` into `write_package` at merge time.
- **Engine-gate wording (owner of cfs_engine):** the `cfs_engine.run` "model-vs-tributary" gate is still an identity on flexible diaphragms. Its docstring/comment should point to `cfs_gates.independent_tributary`.
- **CFS-05 θ:** the gates are generic (any `theta` with `theta_max`/`limit`). Whoever adds the θ computation should write `theta` / `theta_max` keys.
- **Storage key name (cfs-wallloads):** preflight accepts `storage`, `storage_levels`, `storage_live_psf` and `storage_psf` as the declaration. Align with the key cfs-wallloads implements.
- **CFS_REFERENCE key after merges:** other CFS branches (cladding tributary, rotation drift) will change the engine numbers. `tests/test_cfs_reference_key.py` will then fail on purpose; regenerate with `python tests/test_cfs_reference_key.py` and update the table.
- **Rayleigh period** is reported only. The ELF still uses `T_analytical` (agent-set) with the Cu·Ta cap.
- **Ex29 F3** (the numeric capacity-design gate failing an Ω0-level demand net of 0.9D relief) was not addressed.
- **Hub catalog blurb** (`steltic_hub/catalog/steltic_cfs.json`) still says "racks" — hub repo, not in my worktree.
- **Hot-rolled steltic `_completion_gate`:** porting this there is the HR agent's job.

## Workstream `hub`

### Status

| ID | Status | Files | What changed | Tests | Before → after |
|---|---|---|---|---|---|
| HUB-01 | FIXED | `steltic/steltic/main.py`, `steltic_cfs/steltic/main.py`, `steltic_hub/runners.py`, `steltic_hub/main.py`, `catalog/{steltic_admin/admin,steltic_variations/variations,steltic_probabilistic/probabilistic}/local_guard.py` + their `main.py` | **Modules.** A new `_local_only` middleware does three things. It requires a loopback Host on every request; `STELTIC_ALLOWED_HOSTS` adds names. It refuses a state-changing request that has a non-loopback or `null` Origin, or `Sec-Fetch-Site: cross-site`. If a request sends `X-Steltic-Hub-Token`, the value must match `STELTIC_HUB_TOKEN`. CFS applies the guard only when DEMO=0. **Hub.** It creates one random token per launch and passes it in the env of each server it starts. It sends the token on the credential push, health check, http runs, Stop and the `/m/` proxy. The proxy replaces any token header the page sent. The same guard was added to the bundled Admin, Variations and Probabilistic servers. | `steltic/tests/test_local_only_guard.py` (5), `steltic_cfs/tests/test_local_only_guard.py` (5), `steltic_hub/tests/test_review_fixes_hub.py::test_bundled_module_servers_carry_the_same_local_guard`, `::test_local_guard_refuses_cross_site_and_rebinding_and_checks_the_token`, `::test_the_hub_hands_each_server_a_token_and_sends_it_back` (a real server process), `::test_the_proxy_sends_the_hubs_token_never_the_browsers` | **HR**, pristine: a text/plain `POST /api/creds` with `Origin: https://evil.example` returned **200** and changed base_url. `GET /api/me` with `Host: attacker.example` returned **200**. Now both return **403**. **CFS** (listed as UNVERIFIED in the register) behaves the same on pristine: 200 before, so it was exploitable. Now **403**. |
| HUB-04 | FIXED | `steltic_hub/runners.py` (stage_inputs), `manifest.py` (validation), `catalog/steltic_nonlinear.json`, `jobs.py` and `main.py` (listing order), README | **Record.** Folder and zip staging writes `.hub_staged.json` with a content signature of each package file (sha256, or size+CRC for zips). **Re-stage.** Files the new package dropped are moved aside. When a `design_files` file differs from the package *staged last time* (not from the project copy), the stage entry's `supersede` outputs and the earlier design's files move aside together. An edited project copy is moved aside before a changed package file replaces it. Everything goes to `_superseded/<YYYYmmdd-HHMMSS>/`; nothing is deleted, and each move is logged. **Projects with no record** move differing files aside once and log a warning; their outputs are not moved. **Nonlinear manifest:** every design-package stage declares `design_files` and `supersede`. | `test_a_new_design_package_supersedes_the_old_one_and_its_analyses`, `test_an_edited_project_copy_is_kept_before_a_new_package_replaces_it`, `test_what_the_module_adds_to_a_package_file_is_not_a_new_design`, `test_a_project_staged_before_the_record_existed_is_handled`, `test_zip_packages_are_tracked_the_same_way`, `test_stage_globs_are_validated`, `test_every_nonlinear_design_package_stage_declares_what_it_supersedes` | **Synthetic repro** (review harness): pristine leaves `design/old_only.json` and `pushover/po.json` next to the new cfg. Now they are in `_superseded/<t>/` with the old cfg.py. **Real data:** copies of saved SNL job Ex22 and HR Ex22 in the review harness. Pristine overlays 127 files and keeps all SNL results. The fix's first stage finds no record, moves aside SNL's extended `design/calc_package.json` and warns. A re-stage after SNL edits calc_package moves nothing. A simulated HR redesign moves the 151 MB of results (pushover, nlrha, ddm, hinge params, review…) plus the old cfg and calc_package into one snapshot. |
| HUB-05 | FIXED | `steltic_hub/runners.py` (ServerSupervisor), `main.py` (RUNS passed in) | If a dependency becomes available while a hub run is using the server, the restart is deferred and logged. A run counts as using the server if it is the module's own run, an http run of a module whose server `requires` it, or a CLI run whose command or env names `{server.<id>}`. The run that asked for the server does not count. The first `ensure()` after the server goes idle restarts it. | `test_a_dependency_becoming_available_waits_for_the_run_to_finish` | Before: `stop()` was called right away, with no check of RUNS. After: no stop while `hr.design` or `loop.design` is live. The stop happens once those runs are idle. |
| HUB-06 | FIXED | `steltic_hub/runners.py` (field_values, stage_inputs) | An absolute file-field path is accepted only if it equals the field's expanded default or lies under `config.DATA`. Outside those, staged fields are refused. A field that is not staged may still name one existing **file**, never a folder. This keeps Admin's standards queue working: it converts PDFs from a folder the user picked. A stage whose `to` lies inside its `from` folder is refused; before, the copy recursed. | `test_absolute_file_fields_are_confined`, `test_a_folder_is_never_staged_into_itself` | Before: `package=/` or `package=$HOME` was accepted and its whole tree copied into the project. After: RunError "is outside the project and the hub's data folder". |

Out of scope and untouched: HUB-02, HUB-03, HUB-07, HUB-08 (RAG/grounding), HUB-09 (another agent), `job_tools.py`, `rag_server.py`.

Test results:
- hub: 127 passed, 1 skipped (was 113/1; 14 new)
- steltic: 23 passed (was 18)
- steltic_cfs: 22 passed (was 17)

### Behaviour changes users will notice

- **Module servers refuse non-loopback requests.** HR, CFS (local), Admin, Variations and Probabilistic return 403 for any request whose Host is not loopback, and for cross-site state-changing requests. Reaching a server by LAN IP or hostname now needs `STELTIC_ALLOWED_HOSTS`. CFS DEMO=1 (Cloud Run) is unchanged.
- **New folders in Nonlinear projects.** `_superseded/<time>/` folders appear when HR Steel's design changes. After a redesign, the earlier pushover, NLRHA, DDM, Collect (hinge_params_collected.json), review and hazard results move there. The Run gate then asks for Collect again, which is correct for new sections. The run log says what moved and where.
- **Projects staged by the old hub** get a one-time `_superseded` copy of any differing package files. They also get a warning, not a supersede, when design files differ.
- **Package copies are overwritten as before.** A re-stage with an unchanged package still overwrites module-extended package files with the package version, as it always did (for example SNL's `ddm_analysis` key in design/calc_package.json).
- **Absolute paths outside the data folder are refused for the design package** (and any staged field). Admin `@file:/elsewhere/pkg.zip` for a package now fails; uploads and `{out.steltic}` are unaffected.
- **Deferred restart is visible in the server log.** Restarting HR Steel when the standards server appears now waits for running designs. Until then it keeps running without the new dependency, and the server log says "restart … deferred".

### Follow-ups

- **The token is a positive credential, not mandatory.** A request with no token and no browser headers (curl, another module's server) is still accepted. That is because the snl loop_server and variations call HR's `/api/run` without it. A strict mode needs snl's HR client to send `X-Steltic-Hub-Token` (steltic_nonlinear is not in my worktrees). The browser-side protection does not depend on the token.
- **HUB-05 only sees hub-started runs.** A design started from a module's own embedded page is invisible to the hub. A module "busy" endpoint would close this.
- **HUB-04 does not check for a live run on the same project.** If HR's design changed and two Nonlinear tabs start at once on the same project, the second stage could move outputs from under the first run.
- **The supersede list is hand-written.** The `supersede` list in steltic_nonlinear.json comes from SNL's output names. It should be kept in step with SNL, or SNL could declare its outputs itself.
- **rag_server has no Host check** (part of HUB-01's original note). It is out of scope under the RAG deferral.

## After integration: remaining follow-ups and PARTIAL items

- **PARTIAL carried from branches:** CFS-09 Ω0 transfer/collector numbers seeded, not amplified or design-checked; CFS-26 component-mode items (`_TIER_MIN`, DC = 0 auto-fill); CFS-11 roof uplift in T_wind; CFS-36 `sfia_oracle` re-extraction; CFS-05 θ for flexible-diaphragm lines is per line (no building-level P-Δ redistribution).
- `build_cfs_models.py` W-specs (RAG model-doc generator, out of scope) FAIL their own gate on legacy wall_props (assumed-schedule warning) — migrate them and the `agent.py` mock cfg to S400 inputs when RAG work resumes; portal specs pass.
- `merge_fills` is opt-in and `cfs_pipeline.design_and_report` called directly still re-seeds without the backup (gates' follow-up); `pick_holddown` still seeds `device_class` from ρ-ELF tension.
- Ex11-style L-plans: the independent tributary check flags every line when any `trib_scale` is declared (renormalisation) — correct but noisy; a plan-aware independent check (true areas) would be better.
- Portal θ Px is D + L + 0.15S (conservative vs 12.8.6.1's 1.0D + 0.5L) — left as cfs-portal chose.
- HR-route ports not done: HR system-specific consistency rows/composite A1, HR report chapters (irregularity, wind); the HR route in this module stays a fallback.
- Hub catalog blurb still says "racks" (hub repo); hub token not mandatory (hub notes).
- `steltic/agent.py:77` RAG tool description still says "S400 … WIND columns" (retrieval text, deferred).

## Independent verification

| ID | Verdict | Evidence |
|---|---|---|
| CFS-01 cumulative drift | **CORRECT** | **Mechanics re-derived.** For a sheathed wall, the rotation passed up is ∫M/EI = 2(m_top·h + v·h²/2)/(E·Ac·b) + δv/b. The overturning term inside a story is m·h²/(E·Ac·b). Story drift = own + h·Σθ_below, per 12.8.6.5, where Δ is the difference of δ at the top and bottom of the story.<br>**Ex1 line A-extS** (selected schedule rebuilt from the job files): an independent script gives own / rotation / total = 0.354/0, 0.289/0.194, 0.252/0.332, 0.180/0.463 in, so Cd·Δ/h = 0.0111/0.0170/0.0205/0.0226. These are identical to the engine values.<br>The strap-bay chord term (2× both chords) is conservative relative to a single tension diagonal whose uplift goes straight into the anchor. I accepted it. |
| CFS-04 s400_deflection | **CORRECT**, plus **CORRECTED here** (ω2) | **Terms checked against S400 l.1385–1440 and 1845–1905.** All four terms of E1.4.1.4-1 / E2.4.1.4-1 match:<br>- WSP: β 67.5 plywood / 55 OSB and CSP; ρ 1.85 / 1.05; ω4 = 1.<br>- Steel sheet: β = 29.12·t/0.018 (Eq. -3a), ρ = 0.075·t/0.018 (Eq. -4a), ω4 = √(33/Fy), G = 11,300 ksi.<br>- Both: ω1 = s/6, ω3 = √((h/b)/2).<br>- Units are lb/in, psi, in.<br>**Hand calcs.** OSB s=4, 43 mil, v=600 plf, 10×8 ft, T=5 kip, k=50 gives 0.0203 / 0.0377 / 0.3021 / 0.125 in. Steel sheet 27 mil, Fy 50, s=2, v=900 gives 0.0305 / 0.0534 / 0.2931 in. Both equal the code to machine precision.<br>The per-face split (v/faces in the shear and slip terms) is consistent mechanics for parallel faces; S400 is silent on it.<br>**Defect fixed (e3d7d3d).** S400 A2.1 defines t_stud as the *designation* thickness (minimum base steel thickness in mils, so 33 mil = 0.033 in). The "conservative" ASSUMED schedule used the design thickness 0.0346 (ω2 = 0.954, so the shear and slip terms were ~5 % low), and the contract/docstring examples used 0.0451. The assumed stud is now 0.033 and the examples use 0.043. A design-thickness input now gets a note plus one preflight warning; inputs are not changed silently. |
| CFS-05 θ | **CORRECT** (wall), **CORRECTED here** (portal) | **Wall path.** θ = Px·Δe/(Vx·hsx) uses elastic Δ with no Cd, per 7-22 l.10074–10086 and C12.8.7. Px = 1.0D + 0.5·(0.4/0.8)L0 per 12.8.6.1 as printed. θmax = 0.5/(βCd) ≤ 0.25, β ≥ 1.25/Ω0, and θmax need not be taken below 0.10. Ex1 story 1: Px 2094.2 kip (L1 = 517.4 + 0.5·0.4·55·9920/1000 = 626.5), Vx 271.8, θ = 0.0213 by hand, which equals the engine. θmax 0.125.<br>**Portal defect fixed (9ef58b5).** With `seis['T_drift']`, θ divided the 12.8.6.2-scaled δxe by the unscaled V. On the demo SDC D frame, T_drift = 2 s moved θ from 0.0188 to 0.0056. Vx/Δxe is now taken from one loading. Ex16 θ = 7.53·0.4046/(0.333·240) = 0.0381 by hand, which equals the engine. |
| CFS-02 portal envelope / wind both sides | **CORRECT** | Envelopes are taken by prefix over col*/raf*/kb*, with paired forces. Wind cases come from both sides, with ±GCpi, both Fig. 27.3-1 windward branches, and the along-ridge case; supplied pressures are mirrored. Ex16 (native): col_L under W2_R = col_R under W2 = 1036 k-in, so the mirror is exact. The governing member is col_R, which the agent's left-only analysis had at 936.7. |
| CFS-03 Frame2D recovery / equilibrium | **CORRECT** | q = k_used·u − feq uses the same scaled + geometric k that was assembled. Cantilever H = 1 kip at stiff_scale 0.9 gives M = 200 exactly. All 41 Ex16 combos have equilibrium residual 0.0. Ex16 statics check on 1.2D+1.6Lr: knee 980.4 = Rx·He 964.8 + P·Δ(spread). |
| CFS-24 S100 C1.1 constants | **CORRECT** | Corpus C1.1.1.3(a): "A factor of 0.90 shall be applied to all stiffnesses". C1.1.1.2(b) Eq. C1.1.1.2-1: Ni = (1/240)αYi with α = 1.0 for LRFD. τb = 4(αPr/Py)(1 − αPr/Py) above 0.5. The code uses DA_EA = 0.90 and DA_NOTIONAL = 1/240, and applies notionals in every combination (conservative; the (b)(3) relief is not taken). |
| CFS-39 storage in W | **CORRECT** | 12.7.2 items 1–6 and exception (a) match the corpus text (l.9788). Ex1 by hand: W = 517.4 + 2·509.7 + 230.0 = 1766.8, so V = 0.1538·W = 271.8, which equals the engine. The storage test value 85.80 kip is an independent hand number.<br>Minor point: the 5 % share is items / W_total, not items / (W − items). It is opt-in and matters only at the boundary. |
| CFS-40 0.5L rule | **CORRECT** | The 2.3.1 Exc. 1 text (l.4953) is: Lo ≤ 100 psf, not a garage, not public assembly. Otherwise the factor is 1.0, and it is applied to the 2.3.6 seismic combos as well. |
| CFS-06/07 gypsum E6 + capacity design | **CORRECT** | Corpus:<br>- E5 is Canada-only.<br>- E6.3.3: Ω_E = 1.5.<br>- E6.4.1.2: capacity-protected components.<br>- A1.2.3 commentary: only R = 3 in SDC B/C is waived, and it names gypsum.<br>- B3.4: expected strength, need not exceed the Ω0 effect.<br>Table 12.2-1 gypsum row: R 2, Ω0 2.5, Cd 2, D = 35 ft, E/F NP. T_cd = min(Ω_E·Vn stack, Ω0_eff stack) with footnote-b Ω0 = 2.0. The test's 7.5 kip is hand-derived. |
| CFS-18/19 strap E3.3.3 / E3.4.1 | **CORRECT** | E3.3.3: Ω_E = (Ry·Vn/w + v_finish)/(Vn/w) ≤ 1.8 with v_finish ≥ 0.2Vn/w, giving Ry + 0.2. Table A3.2-1 Ry/Rt bands match the corpus. E3.4.1(a)(2) Method 2 requires both RtFu/(RyFy) ≥ 1.2 and RtAnFu > RyAgFy. Gr 33: 1.2·45/(1.5·33) = 1.09, which fails. Gr 50: 1.3, which passes. Vn = Ag·Fy·w/√(h² + w²). |
| CFS-20 Type II Ca | **CORRECT** | Table E1.3.1.2-1 matches the corpus row for row (10–100 %, ratios 1/3 to 1). Chord C = Vh/(Ca·ΣLi) (Eq. E1.4.2.2-2) is applied per story increment. Uplift v = V/(Ca·ΣLi) is computed at the capacity level. |
| CFS-15 enclosure GCpi / open-roof CN | **CORRECT** | GCpi is 0.18 / 0.55 / 0.18 / 0 by enclosure class. qh is computed without Kd, and Kd is applied in the pressure, per 7-22. Free-roof CN tables were checked entry by entry against corpus Figs. 27.3-4, 27.3-5 and 27.3-7, and all match. Fig. 27.3-1 Cp tables (the corpus copy is garbled) match the printed 7-22 values. The windward overhang soffit uses Cp 0.8. |
| CFS-13/14 package naming / backup | **CORRECT** | **Ex1 re-run on a copy of the saved filled package:**<br>1. The re-run wrote `calc_package_cfs.json.filled.bak` and printed a WARNING.<br>2. `build_report` dispatched to the CFS report and rendered it.<br>3. `merge_fills` restored the fills: Ex1 227 → 93 issues, all stale-demand / cleared-D/C; Ex16 27 → 5.<br>**Backward-compat note:** the saved Ex16 cfg's runtime monkeypatch block now raises `TypeError: _frame_section() takes 1 positional argument but 2 were given`. That is loud, not silent, and the native cfg (patch removed) runs. |
| CFS-29 gates | **CORRECT** (with one concern) | The completion gate fails on a missing or stale consistency stamp, a stale report, changed seeded demands, failing drift rows and θ > θmax (Ex16: 6 problems, Ex1: 14). Tests are mostly hand-number based; the portal θ test mirrored the code formula, and test_cfs_verify2 adds an invariance check.<br>**Concern:** a package filled under the OLD engine still passes consistency and the gate after `consistency.check` (saved Ex1: 0 issues; the gate asks only for a fresh report). The gates check the package, not a re-derivation from cfg. A pre-fix design is therefore accepted until it is re-run. |

### Open concerns

- **Wall-path θ** uses building Px/Vx × the line's drift. This is fine where tributary P/V is uniform, but there is no per-line P.
- **Portal Px = D + L + 0.15S** of the frame, while V comes from the declared `W_frame_kip`. This is conservative but inconsistent: Ex16 has Px 7.53 vs W 4.0.
- **Cladding mass in `story_weight`:** level k takes the full height of the story below. This is pre-existing and not in the fix scope; it shifts mass down by half a story.
- **`_drift_row_problem` key choice:** it picks the first numeric value key. The `stability_theta` row uses `value`, not `theta`, so only its `ok` verdict is gated.
- **Old filled packages pass the gate** until they are re-run (see CFS-29 above).

"""cfs_sections property defects (CFS-36 / static review F7-F12) and the new section
families (CFS-17: n-ply built-ups, Z, cold-formed HSS)."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ENG = os.path.join(os.path.dirname(HERE), "steel_engine")
if ENG not in sys.path:
    sys.path.insert(0, ENG)

import cfs_sections as S        # noqa: E402


@pytest.mark.parametrize("des,Iy_sfia", [("600T125-54", 0.054), ("1600T150-97", 0.183)])
def test_track_flange_tip_reaches_sfia_iy(des, Iy_sfia):
    # free flange tip was t/2 short (track Iy -3..-12 %)
    assert S.gross_props(des)["Iy"] == pytest.approx(Iy_sfia, rel=0.02)


def test_unlipped_flat_width_is_B_minus_r_minus_t():
    p = S.gross_props("600T125-54")
    r = S.RADIUS_BY_MIL[54]
    assert p["flats"]["flange"] == pytest.approx(1.25 - r - p["t"], abs=1e-9)


def test_x0_sign_and_web_to_shear_centre():
    p = S.gross_props("600S162-54")
    assert p["x0"] < 0                                    # AISI/SFIA sign (SFIA xo = -1.049)
    assert p["x0"] == pytest.approx(-1.049, rel=0.01)
    assert p["m"] == pytest.approx(0.663, rel=0.02)       # SFIA m
    assert S.gross_props("800S250-97")["m"] == pytest.approx(1.008, rel=0.02)


def test_back_to_back_iy_from_joint_plane():
    b = S.built_up_back_to_back("800S250-97")
    p = S.gross_props("800S250-97")
    assert b["Iy"] == pytest.approx(2 * (p["Iy"] + p["A"] * (p["xbar"] + p["t"] / 2) ** 2))
    assert b["Iy"] == pytest.approx(3.177, rel=0.01)
    assert b["Cw"] > 2 * p["Cw"]                          # I-section warping, not 2 x Cw1


def test_nply_built_up():
    p = S.gross_props("1200S250-118")
    b = S.built_up("1200S250-118", 6)
    assert b["A"] == pytest.approx(6 * p["A"]) and b["Ix"] == pytest.approx(6 * p["Ix"])
    with pytest.raises(ValueError):
        S.built_up("1200S250-118", 3)
    box = S.built_up("1400S300-118", 2, "box")
    assert box["J"] > 100 * S.built_up("1400S300-118", 2)["J"]   # closed cell


def test_furring_hat_is_refused():
    with pytest.raises(KeyError, match="HAT"):
        S.gross_props("150F125-43")


def test_gr33_only_54mil_defaults_to_33ksi():
    assert S.gross_props("550S125-54")["Fy"] == 33.0
    assert S.gross_props("600S162-54")["Fy"] == 50.0


def test_hss_from_aisc_table_and_effective():
    h = S.gross_props("HSS12X12X5/8")
    assert h["A"] == pytest.approx(25.7) and h["Ix"] == pytest.approx(548.0)
    assert h["t"] == pytest.approx(0.93 * 0.625)
    assert S.effective_area("HSS12X12X5/8", 50.0) == pytest.approx(h["A"])   # compact
    thin = S.gross_props("HSS12X12X1/8")
    assert S.effective_area("HSS12X12X1/8", 50.0) < thin["A"]
    assert S.effective_Ix("HSS12X12X1/8", 50.0)["Ixe"] < thin["Ix"]


def test_z_section_point_symmetric():
    z = S.gross_props("800Z250-68")
    assert z["x0"] == 0.0 and abs(z["Ixy"]) > 0.1
    assert z["I1"] > z["Ix"] > z["I2"] > 0
    assert 0 < S.effective_area("800Z250-68", 50.0) < z["A"]


def test_both_compression_gradient_branch():
    # psi -> 1 with ho/bo > 4 used to divide by zero (static review F12)
    b1, b2, comp = S.web_gradient_eff(10.0, 0.05, 30.0, 29.99, ho_over_bo=6.0)
    assert comp == 10.0 and b1 + b2 <= comp + 1e-9 and b2 >= 0


def test_validation_gate_improves():
    n, worst = S.validate_against_csv(verbose=False)
    assert n > 390 and worst[0] < 0.25

"""CFS-34: the CFS_REFERENCE answer key must be reproducible by the engine. This executes the cfg
block printed in contract/CFS_REFERENCE.md and asserts every number between the ANSWER_KEY
markers. If the engine changes a number on purpose, regenerate the key:
    python tests/test_cfs_reference_key.py        (prints the current values)"""
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (os.path.join(REPO, "steel_engine"), REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DOC = os.path.join(REPO, "contract", "CFS_REFERENCE.md")


def _doc():
    return open(DOC, encoding="utf-8").read()


def _cfg_from_doc():
    s = _doc()
    i = s.index("<!-- REF_CFG -->")
    m = re.search(r"```python\n(.*?)```", s[i:], re.S)
    mod = types.ModuleType("refcfg")
    exec(compile(m.group(1), "CFS_REFERENCE.md", "exec"), mod.__dict__)
    return mod.cfg


def _key_from_doc():
    s = _doc()
    blk = s[s.index("<!-- ANSWER_KEY_BEGIN -->"):s.index("<!-- ANSWER_KEY_END -->")]
    out = {}
    for name, val in re.findall(r"^\|\s*([A-Za-z0-9_]+)\s*\|\s*([-0-9.]+)\s*\|", blk, re.M):
        out[name] = float(val)
    return out


def engine_values():
    import cfs_engine as CE
    import cfs_pipeline as CP
    cfg = _cfg_from_doc()
    r = CE.run(cfg)
    e = r["elf"]
    B = r["directions"]["X"]["lines"]["B"]
    v = dict(W_kip=e["W"], Ta_s=e["Ta"], Cs=e["Cs"], V_kip=e["V"], k=e["k"],
             Fx_kip_L1=e["Fx"][1], Fx_kip_L2=e["Fx"][2], Fx_kip_L3=e["Fx"][3],
             Fx_kip_roof=e["Fx"][4])
    for k in (1, 2, 3, 4):
        v["lineB_V_kip_s%d" % k] = B[k]["V"]
        v["lineB_v_plf_s%d" % k] = B[k]["v_unit_plf"]
    Ts = [lr[1]["T_kip"] for d in r["directions"].values() for lr in d["lines"].values()]
    assert max(Ts) - min(Ts) < 0.05, "the key says every line has the same base tension"
    v["T_base_kip_every_line"] = Ts[0]
    pkg = CP.build_package("REF", cfg, r)
    v["rho"] = pkg["rho"]
    wB1 = [w for w in pkg["wall_lines"] if w["id"] == "wall-X-B-s1"][0]
    v["seed_lineB_v_plf_s1_with_rho"] = wB1["v_unit_plf"]
    v["seed_T_cum_kip_with_rho"] = pkg["holddowns"][0]["T_cum_kip"]
    v["seed_T_cd_kip_Om0eff_2p5"] = pkg["holddowns"][0]["T_cd_seed_kip"]
    st = pkg["studs"][0]["P_cum_kip_by_story"]
    for k, nm in ((4, "L4"), (3, "L3"), (2, "L2"), (1, "L1")):
        v["stud_P_kip_" + nm] = st[k]
    rows = [x for d in r["directions"].values() for lr in d["lines"].values() for x in lr.values()]
    w = max(rows, key=lambda x: x["drift_amplified"])
    v["worst_drift_ratio"] = w["drift_amplified"]
    # CFS-01: cumulative story drift = S400 single-story deflection + rotation carried from below
    v["worst_drift_single_story"] = w["drift_amplified_single_story"]
    v["worst_drift_rotation_from_below"] = w["drift_amplified_first_order"] - \
        w["drift_amplified_single_story"]
    v["theta_max_found"] = max(x["theta"] for x in rows)            # CFS-05, 12.8.7
    return v


def test_cfs_reference_answer_key_reproduces():
    key = _key_from_doc()
    assert len(key) >= 25, key
    got = engine_values()
    bad = []
    for name, want in key.items():
        have = got[name]
        # printed precision: integers to 1 unit, decimals to the last printed digit (+ rounding)
        tol = max(0.5 * 10 ** (-len(str(want).split(".")[1])) if "." in str(want) else 0.5,
                  1e-9) * 1.01 + 1e-9
        if name.endswith("plf_s1_with_rho") or name.endswith("v_plf_s1") or "_plf_" in name:
            tol = 0.51
        if abs(have - want) > tol:
            bad.append("%s: key %s vs engine %.4f" % (name, want, have))
    assert not bad, ("contract/CFS_REFERENCE.md answer key is stale -- regenerate it "
                     "(python tests/test_cfs_reference_key.py):\n" + "\n".join(bad))


if __name__ == "__main__":
    for k_, v_ in engine_values().items():
        print("| %s | %s |" % (k_, round(v_, 4)))

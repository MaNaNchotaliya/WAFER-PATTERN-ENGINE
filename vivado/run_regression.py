"""RTL regression: runs the test wafers in xsim and compares every field of every
checkpoint record with model/wpe_model.py.

usage:
  python vivado/run_regression.py              # all tests, input gaps 0% and 35%
  python vivado/run_regression.py --quick      # 4 small tests
  python vivado/run_regression.py --netlist build/impl/wpe_engine_funcsim.v --only a,b
summary goes to build/regress/summary.txt
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "training"))

from wpe_gbt import Trees                                            # noqa: E402
from wpe_geom import dies_from_map, geometry, inject_site_fault, multisite, serpentine  # noqa: E402
from wpe_model import Engine                                         # noqa: E402
from wpe_params import CK_KEYS, CLASSES, Config, read_ck, write_cfg, write_ck, write_stim  # noqa: E402

VIVADO_BIN = Path(os.environ.get("VIVADO_BIN", r"C:\AMDDesignTools\2026.1\Vivado\bin"))
RTL = ["rtl/wpe_zone.v", "rtl/wpe_div.v", "rtl/wpe_gbt.v", "rtl/wpe_engine.v", "tb/tb_wpe_engine.v"]
MEMS = ["rtl/wpe_nodes.mem", "rtl/wpe_leaves.mem"]
BUILD = ROOT / "build"
HW = ROOT / "vivado"                                # rtl/, tb/, sim/ live here


def trained_cfg(valid, **kw):
    """geometry from the map plus the calibrated registers from model/trained.json"""
    t = json.loads((ROOT / "model" / "trained.json").read_text())
    cfg = geometry(valid, Config(chk=t["chk"], persist=t["persist"], min_frac=t["min_frac"],
                                 margin=list(t["margin"]), site_min=t["site_min"], site_delta=t["site_delta"]))
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def real_tests(quick):
    """WM-811K wafers from the test lots: one per class plus a few special ones"""
    import dataset
    D = dataset.load()
    te = dataset.split(D)["test"]
    lab = D["label"]
    rng = np.random.default_rng(7)
    T = []
    for k, name in enumerate(CLASSES):
        idx = np.nonzero(te & (lab == k) & (D["h"] <= 60) & (D["w"] <= 60))[0]
        i = int(rng.choice(idx))
        m = dataset.wafer_map(i, D)
        T.append((f"wm_{name.lower().replace('-', '')}_{i}", trained_cfg(m > 0), dies_from_map(m)))
        if quick and k >= 3:
            break
    if quick:
        return T
    # 4-site prober with a damaged site 2 (fails 80% of its good dies)
    i = int(rng.choice(np.nonzero(te & (lab == 0) & (D["h"] <= 50))[0]))
    m = dataset.wafer_map(i, D)
    dies = inject_site_fault(dies_from_map(m, "multisite"), 2, 0.8, np.random.default_rng(3))
    T.append((f"wm_site_fault_{i}", trained_cfg(m > 0, n_sites=4), dies))
    # same wafer, healthy probe card, 4 sites
    T.append((f"wm_4site_ok_{i}", trained_cfg(m > 0, n_sites=4), dies_from_map(m, "multisite")))
    # the largest map in the data set (up to 212 x 204)
    big = int(np.argmax(D["h"].astype(int) * D["w"]))
    m = dataset.wafer_map(big, D)
    T.append((f"wm_largest_{big}", trained_cfg(m > 0, chk=64), dies_from_map(m)))
    return T


def synthetic_tests():
    rng = np.random.default_rng(2026)
    T = []
    # full 256 x 256 map, every die fails: counters and pair totals at their maximum
    valid = np.ones((256, 256), bool)
    T.append(("full_256_all_fail", trained_cfg(valid, chk=255), [(x, y, 1, (x + y) % 8) for x, y, _ in serpentine(valid)]))
    # one die
    valid = np.ones((1, 1), bool)
    T.append(("single_die", trained_cfg(valid, chk=1), [(0, 0, 1, 0)]))
    # only rejected dies: outside the map, then a duplicate as the last die
    valid = np.ones((5, 7), bool)
    dies = [(7, 0, 1, 0), (0, 5, 0, 1), (255, 255, 1, 2), (3, 2, 1, 3), (3, 2, 0, 3)]
    T.append(("rejects", trained_cfg(valid, chk=1), dies))
    # final checkpoint only (chk = 0), random order, all sites, permissive call rule
    from selftest_models import random_stream, random_wafer
    for j in range(10):
        valid, fail = random_wafer(rng)
        cfg = geometry(valid)
        cfg.chk = int(rng.choice([0, 1, 5, 16, 16, 37]))
        cfg.persist = int(rng.integers(0, 16))
        cfg.min_frac = int(rng.choice([0, 409, 4096]))
        cfg.margin = [int(v) for v in rng.integers(0, 1500, len(CLASSES))]
        cfg.n_sites = int(rng.integers(0, 9))
        cfg.site_min = int(rng.integers(0, 40))
        cfg.site_delta = int(rng.integers(-300, 2000))
        dies = random_stream(rng, valid, fail, cfg)
        T.append((f"random_{j:02d}", cfg, dies))
    return T


def clean_env():
    env = os.environ.copy()
    env.pop("NoDefaultCurrentDirectoryInExePath", None)   # breaks xsim's spawned tools
    return env


def tool_cmd(name):
    if os.name == "nt":
        return ["cmd", "/c", str(VIVADO_BIN / f"{name}.bat")]
    return [str(VIVADO_BIN / name)]


def compile_sim(netlist=None):
    env = clean_env()
    if netlist is None:
        simdir = BUILD / "sim"
        cmds = [tool_cmd("xvlog") + ["-i", str(HW / "rtl")] + [str(HW / s) for s in RTL],
                tool_cmd("xelab") + ["-debug", "off", "-O2", "tb_wpe_engine", "-s", "tb_snap"]]
    else:
        simdir = BUILD / "sim_netlist"
        glbl = VIVADO_BIN.parent / "data" / "verilog" / "src" / "glbl.v"
        cmds = [tool_cmd("xvlog") + ["-i", str(HW / "rtl"), str(HW / "tb" / "tb_wpe_engine.v")],
                tool_cmd("xvlog") + [str(Path(netlist).resolve()), str(glbl)],
                tool_cmd("xelab") + ["-debug", "off", "-O2", "-L", "unisims_ver", "-L", "secureip",
                                     "tb_wpe_engine", "glbl", "-s", "tb_snap"]]
    simdir.mkdir(parents=True, exist_ok=True)
    for m in MEMS:                                   # $readmemh looks in the run directory
        shutil.copy(HW / m, simdir)
    for cmd in cmds:
        r = subprocess.run(cmd, cwd=simdir, env=env, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-4000:], r.stderr[-3000:])
            raise SystemExit(f"compile step failed: {' '.join(cmd)}")
    return simdir


def run_sim(simdir, listfile, gap, seed, logname):
    cmd = tool_cmd("xsim") + ["tb_snap", "-R", "-testplusarg", f"LIST:{listfile.as_posix()}",
                              "-testplusarg", f"GAP:{gap}", "-testplusarg", f"SEED:{seed}", "-log", logname]
    t = time.time()
    r = subprocess.run(cmd, cwd=simdir, env=clean_env(), capture_output=True, text=True)
    if "TB_FINISHED" not in r.stdout:
        print(r.stdout[-3000:], r.stderr[-3000:])
        raise SystemExit("simulation did not finish")
    return time.time() - t


def cycles_of(path):
    for line in open(path):
        if line.startswith("# cycles"):
            return int(line.split()[2])
    return -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-compile", action="store_true")
    ap.add_argument("--gaps", default="0,35")
    ap.add_argument("--netlist")
    ap.add_argument("--only")
    args = ap.parse_args()
    out = BUILD / ("regress_netlist" if args.netlist else "regress")
    out.mkdir(parents=True, exist_ok=True)

    trees = Trees.load(ROOT / "model" / "trees.npz")
    tests = real_tests(args.quick) + ([] if args.quick else synthetic_tests())
    if args.only:
        keep = set(args.only.split(","))
        tests = [t for t in tests if t[0] in keep]
    if args.quick:
        args.gaps = "0"

    print(f"preparing {len(tests)} tests (model) ...")
    expected = {}
    for name, cfg, dies in tests:
        expected[name] = Engine(trees).run(cfg, dies)
        write_cfg(out / f"{name}.cfg", cfg)
        write_stim(out / f"{name}.die", dies)
        write_ck(out / f"{name}.exp", expected[name])

    simdir = (BUILD / ("sim_netlist" if args.netlist else "sim")) if args.no_compile else compile_sim(args.netlist)
    gaps = [int(g) for g in args.gaps.split(",")]
    lines, all_ok = [], True
    res = {n: {} for n, _, _ in tests}
    cyc = {}
    for gap in gaps:
        listfile = out / f"list_gap{gap}.txt"
        with open(listfile, "w", newline="\n") as f:
            for name, _, _ in tests:
                f.write(f"{(out / (name + '.cfg')).as_posix()} {(out / (name + '.die')).as_posix()} "
                        f"{(out / f'{name}.gap{gap}.out').as_posix()}\n")
        dt = run_sim(simdir, listfile, gap, 11 + gap, f"xsim_gap{gap}.log")
        lines.append(f"xsim run with gap {gap}%: {dt:.1f} s")
        for name, _, _ in tests:
            got = read_ck(out / f"{name}.gap{gap}.out")
            exp = expected[name]
            bad = []
            if len(got) != len(exp):
                bad.append(f"{len(got)} records, expected {len(exp)}")
            for j, (g, e) in enumerate(zip(got, exp)):
                diff = [k for k in CK_KEYS if g[k] != e[k]]
                if diff:
                    bad.append(f"record {j} (ndies {e['ndies']}): " + ",".join(diff[:4]))
                    break
            res[name][gap] = "PASS" if not bad else "FAIL"
            if bad:
                all_ok = False
                lines.append(f"  {name} gap {gap}: " + "; ".join(bad))
            if gap == gaps[0]:
                cyc[name] = cycles_of(out / f"{name}.gap{gap}.out")

    hdr = (f"{'test':<26}{'dies':>7}{'records':>8}{'final':>11}{'call':>11}{'@die':>7}{'site':>6}"
           + "".join(f"{'gap' + str(g) + '%':>9}" for g in gaps) + f"{'clocks':>11}")
    lines.append(hdr)
    for name, cfg, dies in tests:
        e = expected[name]
        last = e[-1]
        call = CLASSES[last["call_cls"]] if last["called"] else "-"
        row = (f"{name:<26}{len(dies):>7}{len(e):>8}{CLASSES[last['top']]:>11}{call:>11}"
               f"{last['call_ndies'] if last['called'] else '-':>7}"
               f"{last['site_id'] if last['site_flag'] else '-':>6}")
        row += "".join(f"{res[name].get(g, '?'):>9}" for g in gaps) + f"{cyc.get(name, -1):>11}"
        lines.append(row)
    what = "routed netlist" if args.netlist else "RTL"
    nrec = sum(len(expected[n]) for n, _, _ in tests)
    lines.append(f"{len(tests)} tests, {nrec} checkpoint records x {len(gaps)} gap settings: "
                 + (f"ALL TESTS PASS ({what} == model, every field of every record)" if all_ok else "SOME TESTS FAILED"))
    text = "\n".join(lines)
    print(text)
    (out / "summary.txt").write_text(text + "\n")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

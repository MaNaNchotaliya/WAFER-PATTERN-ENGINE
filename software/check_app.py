"""Checks that the app shows what the hardware would do.

1. vivado/sim/demo_center.die (the stimulus Vivado's Run Simulation plays) opened the way the app
   opens a file: the registers must match vivado/sim/demo_center.cfg and the checkpoint records
   must match vivado/sim/demo_center.exp, the file the Verilog testbench compares against.
2. every sample with three probe settings: the fast model the app uses must give the same
   records as the per-die model (model/wpe_model.py), field by field.
3. bad input gives an error with the right line number.

usage: python software/check_app.py
"""
import sys
from pathlib import Path

import wafer_files as wf
from wpe_model import Engine
from wpe_params import CK_KEYS, read_ck

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT / "vivado" / "sim"


def diff(a, b):
    """first difference between two record lists, or None"""
    if len(a) != len(b):
        return f"{len(a)} records vs {len(b)}"
    for j, (x, y) in enumerate(zip(a, b)):
        for k in CK_KEYS:
            if x[k] != y[k]:
                return f"record {j}, {k}: {x[k]} vs {y[k]}"
    return None


def main():
    fails = 0

    w = wf.load(SIM / "demo_center.die")
    r = wf.run(w, "file")
    regs = [tuple(int(v) for v in line.split()) for line in open(SIM / "demo_center.cfg")]
    d = diff(r.recs, read_ck(SIM / "demo_center.exp"))
    if r.cfg.registers() != regs:
        d = "the registers differ from vivado/sim/demo_center.cfg"
    if d:
        fails += 1
        print(f"FAIL vivado/sim/demo_center.die: {d}")
    else:
        print(f"ok   vivado/sim/demo_center.die: registers and all {len(r.recs)} records match what the testbench expects")

    eng = Engine(wf.TREES)
    n = 0
    for title, path in wf.samples():
        w = wf.load(path)
        for order, site in (("serpentine", None), ("multisite", None), ("multisite", 2)):
            r = wf.run(w, order, site, 0.5)
            ref = eng.run(r.cfg, [tuple(int(v) for v in x) for x in r.dies])
            d = diff(r.recs, ref)
            n += 1
            if d:
                fails += 1
                print(f"FAIL {title}, {order}, bad site {site}: {d}")
    print(f"{'ok  ' if fails == 0 else 'FAIL'} {n} sample runs: app records == per-die model")

    bad = {"0110\n1121\n1131\n0110\n": 3, "011110\n111211\n11121\n011110\n": 3, "x,y,fail\n1,1,1\n2,1,X\n": 3,
           "12 4 1\n300 4 1\n": 2, "1 2 1 9\n": 1}
    for text, line in bad.items():
        try:
            wf.parse(text)
            got = None
        except wf.WaferFileError as e:
            got = e.line
        if got != line:
            fails += 1
            print(f"FAIL bad input {text!r}: error on line {got}, expected line {line}")
    print(f"{'ok  ' if fails == 0 else 'FAIL'} {len(bad)} bad inputs give an error on the right line")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

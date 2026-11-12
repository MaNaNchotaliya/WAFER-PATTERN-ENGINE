"""Picks the sample wafers that ship with the desktop app (software/samples).

One wafer per class from the test lots, small enough to look at in a text editor, plus the
Center wafer from the Vivado simulation and one big map. For the defect classes I take a
wafer where the full-map result is right and, except for Scratch (it never gets an early
call), where the early call is right too. That's the typical case, not the only one: the
README has the real numbers.

usage: python training/make_app_samples.py
"""
import json
import sys

import numpy as np

import dataset
from dataset import ROOT
from wpe_fast import run_fast
from wpe_gbt import Trees
from wpe_geom import dies_from_map, geometry
from wpe_params import CLASSES, Config

OUT = ROOT / "software" / "samples"
SIM_WAFER = 27361          # the wafer in vivado/sim/demo_center.*


def engine_cfg(m):
    t = json.loads((ROOT / "model" / "trained.json").read_text())
    return geometry(m > 0, Config(chk=t["chk"], persist=t["persist"], min_frac=t["min_frac"],
                                  margin=list(t["margin"])))


def typical(m, k, trees):
    last = run_fast(engine_cfg(m), dies_from_map(m), trees)[-1]
    if last["top"] != k:
        return False
    if k == 0:
        return not last["called"]
    if CLASSES[k] == "Scratch":
        return True
    return bool(last["called"]) and last["call_cls"] == k


def write(path, m, comments):
    with open(path, "w", newline="\n") as f:
        for c in comments:
            f.write(f"# {c}\n")
        for row in m:
            f.write("".join(str(int(v)) for v in row) + "\n")


def main():
    D = dataset.load()
    te = dataset.split(D)["test"]
    lab, h, w, lot = D["label"], D["h"].astype(int), D["w"].astype(int), D["lot"]
    trees = Trees.load(ROOT / "model" / "trees.npz")
    rng = np.random.default_rng(11)
    picks = []
    for k, name in enumerate(CLASSES):
        idx = np.nonzero(te & (lab == k) & (h <= 64) & (w <= 64) & (h >= 25))[0]
        rng.shuffle(idx)
        for i in idx[:500]:
            if int(i) != SIM_WAFER and typical(dataset.wafer_map(int(i), D), k, trees):
                picks.append((int(i), "", ""))
                break
        else:
            print(f"nothing found for {name}")
    picks.append((SIM_WAFER, "Vivado simulation",
                  "the same wafer as vivado/sim/demo_center.die, the one Run Simulation plays in Vivado"))
    # one big map: the largest test wafer with a defect label that gets the right early call
    big = np.nonzero(te & (lab > 0) & (h * w >= 150 * 150))[0]
    for i in big[np.argsort(-(h[big] * w[big]))]:
        if typical(dataset.wafer_map(int(i), D), int(lab[i]), trees):
            picks.append((int(i), "big map", "one of the biggest maps in the test lots"))
            break

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.txt"):
        old.unlink()
    for i, tag, extra in picks:
        m = dataset.wafer_map(i, D)
        name = CLASSES[int(lab[i])]
        notes = [f"title: {name}, wafer {i}" + (f" ({tag})" if tag else ""),
                 f"label: {name}",
                 f"WM-811K wafer {i} ({lot[i]}, wafer {int(D['wafer_index'][i])}), from the test lots",
                 "map rows: 0 = no die, 1 = pass, 2 = fail"]
        if extra:
            notes.insert(3, extra)
        path = OUT / f"{name.lower().replace('-', '_')}_{i}.txt"
        write(path, m, notes)
        print(f"{path.name}: {m.shape[1]} x {m.shape[0]}, {int((m > 0).sum())} dies")
    return 0


if __name__ == "__main__":
    sys.exit(main())

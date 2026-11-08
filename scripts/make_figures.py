"""Figures for the README (needs results/eval_results.json from scripts/evaluate.py).

  docs/figures/example_calls.png      wafers at the moment the engine named the pattern
  docs/figures/accuracy_vs_tested.png class accuracy vs share of the wafer tested
  docs/figures/earliness.png          per class: share named early, and how early
"""
import json
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import to_rgb  # noqa: E402

import dataset  # noqa: E402
from dataset import ROOT  # noqa: E402
from wpe_fast import run_fast  # noqa: E402
from wpe_gbt import Trees  # noqa: E402
from wpe_geom import dies_from_map, geometry  # noqa: E402
from wpe_params import CLASSES, Config  # noqa: E402

FIG = ROOT / "docs" / "figures"
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e4e3df"
BLUE = "#2a78d6"
FAIL = "#eb6834"
PASS = "#c9c8c2"

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": GRID,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE})


def style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def trained_cfg(valid):
    t = json.loads((ROOT / "model" / "trained.json").read_text())
    return geometry(valid, Config(chk=t["chk"], persist=t["persist"], min_frac=t["min_frac"],
                                  margin=list(t["margin"]), site_min=t["site_min"], site_delta=t["site_delta"]))


def example_calls(R):
    D = dataset.load()
    te = dataset.split(D)["test"]
    trees = Trees.load(ROOT / "model" / "trees.npz")
    want = ["Edge-Ring", "Near-full", "Random", "Center", "Donut", "Edge-Loc"]
    rng = np.random.default_rng(4)
    fig, axes = plt.subplots(2, 3, figsize=(10.5, 7.6))
    for ax, name in zip(axes.ravel(), want):
        k = CLASSES.index(name)
        med = R["E3"]["per_class"][name]["median_tested"]
        idx = np.nonzero(te & (D["label"] == k) & (D["h"] <= 56) & (D["w"] <= 56) & (D["h"] >= 24))[0]
        chosen = None
        for i in rng.permutation(idx)[:400]:
            m = dataset.wafer_map(int(i), D)
            cfg = trained_cfg(m > 0)
            dies = dies_from_map(m)
            last = run_fast(cfg, dies, trees)[-1]
            if last["called"] and last["call_cls"] == k and last["call_ndies"] < cfg.n_total:
                f = last["call_ndies"] / cfg.n_total
                if med is None or abs(f - med) < 0.06:
                    chosen = (int(i), m, dies, last["call_ndies"], cfg.n_total)
                    break
        if chosen is None:
            ax.axis("off")
            continue
        i, m, dies, n_call, n_tot = chosen
        tested = np.zeros(m.shape, bool)
        for x, y, _, _ in dies[:n_call]:
            tested[y, x] = True
        img = np.ones(m.shape + (3,)) * np.array(to_rgb(SURFACE))
        for val, col in ((1, PASS), (2, FAIL)):
            c = np.array(to_rgb(col))
            faded = 0.2 * c + 0.8 * np.array(to_rgb(SURFACE))
            img[(m == val) & tested] = c
            img[(m == val) & ~tested] = faded
        ax.imshow(img, interpolation="nearest")
        ax.set_xticks([])
        ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(False)
        ax.set_title(f"{name}: named after {100 * n_call / n_tot:.0f}% of dies\n({n_call} of {n_tot} tested)",
                     color=INK, fontsize=10.5)
    fig.suptitle("What the engine had seen when it named the pattern (held-out wafers, rows probed top to "
                 "bottom)", color=INK, fontsize=12, y=0.995)
    fig.text(0.5, 0.012, "Tested dies in full colour (orange = fail, grey = pass); faded dies were not "
             "tested yet.", ha="center", color=INK2, fontsize=9.5)
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    fig.savefig(FIG / "example_calls.png", dpi=130)
    plt.close(fig)


def accuracy_curve(R):
    fr = [100 * r["frac"] for r in R["E1"]]
    ba = [100 * r["balanced_accuracy"] for r in R["E1"]]
    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    style(ax)
    ax.plot(fr, ba, color=BLUE, linewidth=2, marker="o", markersize=6, markeredgecolor=SURFACE,
            markeredgewidth=1.5)
    for x, y in zip(fr, ba):
        if x in (10, 50, 100):
            ax.annotate(f"{y:.0f}%", (x, y), textcoords="offset points", xytext=(0, 9), ha="center",
                        color=INK, fontsize=9.5)
    ax.set_xlim(5, 103)
    ax.set_ylim(0, 100)
    ax.set_xlabel("share of the wafer tested (%)")
    ax.set_ylabel("balanced accuracy, 9 classes (%)")
    ax.set_title("Pattern accuracy while the wafer is being probed (held-out lots)", color=INK, fontsize=11,
                 loc="left")
    fig.tight_layout()
    fig.savefig(FIG / "accuracy_vs_tested.png", dpi=130)
    plt.close(fig)


def earliness(R):
    rows = R["E3"]["per_class"]
    names = [n for n in CLASSES[1:]]
    names.sort(key=lambda n: (rows[n]["median_tested"] is None, rows[n]["median_tested"] or 0))
    y = np.arange(len(names))[::-1]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.6, 3.9), sharey=True, gridspec_kw={"width_ratios": [1, 1.25]})
    for ax in (a1, a2):
        style(ax)
        ax.grid(axis="y", visible=False)
        ax.tick_params(axis="y", length=0)
    cov = [100 * (rows[n]["coverage"] or 0) for n in names]
    a1.barh(y, cov, height=0.55, color=BLUE)
    for yy, v in zip(y, cov):
        a1.text(v + 1.5, yy, f"{v:.0f}%", va="center", color=INK, fontsize=9)
    a1.set_xlim(0, 115)
    a1.set_yticks(y)
    a1.set_yticklabels(names, color=INK)
    a1.set_xlabel("named correctly before the end\n(% of the class's wafers)")
    a1.set_title("How many get an early call", color=INK, fontsize=10.5, loc="left")
    for yy, n in zip(y, names):
        r = rows[n]
        if r["median_tested"] is None:
            a2.text(2, yy, "no early calls (never reaches 90% precision)", va="center", color=INK2, fontsize=9)
            continue
        a2.plot([100 * r["p25_tested"], 100 * r["p75_tested"]], [yy, yy], color=BLUE, linewidth=2,
                solid_capstyle="round")
        a2.plot(100 * r["median_tested"], yy, "o", color=BLUE, markersize=8, markeredgecolor=SURFACE,
                markeredgewidth=1.5)
        a2.text(100 * r["p75_tested"] + 2, yy, f"{100 * r['median_tested']:.0f}%", va="center", color=INK,
                fontsize=9)
    a2.set_xlim(0, 100)
    a2.set_xlabel("share of the wafer tested at the call (%)\nmedian and 25th-75th percentile")
    a2.set_title("How early", color=INK, fontsize=10.5, loc="left")
    fig.tight_layout()
    fig.savefig(FIG / "earliness.png", dpi=130)
    plt.close(fig)


def main():
    R = json.loads((ROOT / "results" / "eval_results.json").read_text())
    FIG.mkdir(parents=True, exist_ok=True)
    accuracy_curve(R)
    earliness(R)
    example_calls(R)
    print("wrote", ", ".join(p.name for p in sorted(FIG.glob("*.png"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())

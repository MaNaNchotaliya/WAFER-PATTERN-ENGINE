"""Trains the tree ensemble, writes the ROM files, then calibrates the early call margins
and the site check on the calibration lots.

usage: python training/train.py
writes model/trees.npz, rtl/wpe_nodes.mem, rtl/wpe_leaves.mem, model/trained.json
"""
import json
import sys
import time
from multiprocessing import Pool

import numpy as np

import dataset
from dataset import ROOT
from wpe_fast import RATE, stream_series
from wpe_gbt import from_sklearn
from wpe_geom import dies_from_map, geometry
from wpe_params import CLASSES, FI, NCLS, NITER, ONE

CHK = 16                 # checkpoint every 16 accepted dies
PERSIST = 3              # same class on 3 checkpoints in a row
MIN_FRAC = 205           # no call before 5% of the wafer (205 / 4096)
TARGET_PREC = 0.90       # early calls must be right at least 90% of the time (calibration lots)
MIN_CALLS = 10           # precision is only trusted with at least this many calls
SITE_MIN = 32            # dies a probe site must have tested before it is checked
SITE_FA = 0.001          # allowed share of fault-free wafers with a site flag (calibration lots)


def fit_hgb(X, y):
    from sklearn.ensemble import HistGradientBoostingClassifier   # here: worker processes skip it
    cnt = np.bincount(y, minlength=NCLS)
    w = len(y) / (NCLS * cnt)                  # balanced classes
    # l2 keeps leaf values bounded (without it a few near-pure leaves reach +-12,000)
    clf = HistGradientBoostingClassifier(max_iter=NITER, learning_rate=0.2, max_depth=3,
                                         l2_regularization=1.0, early_stopping=False, random_state=0)
    clf.fit(X, y, sample_weight=w[y])
    return clf


def top_margin(S):
    """vectorized top2"""
    top = np.argmax(S, axis=1)
    best = S[np.arange(len(S)), top]
    rest = S.copy()
    rest[np.arange(len(S)), top] = np.iinfo(np.int64).min
    return top, best - rest.max(1)


def best_threshold(top, margin, frac, k):
    """largest margin threshold that would still give a class k call on this wafer (-1 = never)"""
    ok = (top == k) & (frac >= MIN_FRAC)
    m = np.where(ok, margin, -1)
    if len(m) < PERSIST:
        return -1
    return int(np.lib.stride_tricks.sliding_window_view(m, PERSIST).min(1).max())


def calibrate_margins(cal_seq, y):
    """per class the lowest margin where precision >= TARGET_PREC, and it has to stay there
    for every higher margin that has at least MIN_CALLS calls. Classes that never get
    there are switched off (65535).
    """
    margins, report = [0] * NCLS, {}
    for k in range(1, NCLS):
        mx = np.array([best_threshold(t, m, f, k) for t, m, f in cal_seq])
        cand = np.unique(mx[mx >= 0])[::-1]           # descending thresholds
        best, run_min = None, 1.0
        for v in cand:
            sel = mx >= v
            n_calls = int(sel.sum())
            prec = float(np.mean(y[sel] == k))
            if n_calls >= MIN_CALLS:
                run_min = min(run_min, prec)
                if run_min >= TARGET_PREC:
                    best = int(v)
        margins[k] = min(best, 0xFFFF) if best is not None else 0xFFFF
        sel = mx >= margins[k]
        report[CLASSES[k]] = dict(margin=margins[k], calls=int(sel.sum()),
                                  precision=float(np.mean(y[sel] == k)) if sel.any() else None,
                                  coverage=float(np.mean(mx[y == k] >= margins[k])))
    return margins, report


def _site_job(args):
    i, = args
    m = dataset.wafer_map(i)
    cfg = geometry(m > 0)
    cfg.chk = CHK
    S = stream_series(cfg, dies_from_map(m, "multisite"))
    T = S["ck_T"]
    Ts, Fs = S["Ts"][T][:, :4], S["Fs"][T][:, :4]
    F = S["Fz"][T].sum(1)[:, None]
    ok = (Ts >= SITE_MIN) & (T[:, None] > Ts)
    d = RATE(Fs, Ts) - RATE(F - Fs, T[:, None] - Ts)
    d = np.where(ok, d, -ONE - 1)
    return int(d.max()) if d.size else -ONE - 1


def main():
    D = dataset.load()
    sp = dataset.split(D)
    y = D["label"].astype(np.int64)
    fit_idx, cal_idx = np.nonzero(sp["fit"])[0], np.nonzero(sp["cal"])[0]
    print(f"wafers: fit {len(fit_idx)}, calibration {len(cal_idx)}, test {int(sp['test'].sum())} "
          f"(lot-disjoint)")

    t = time.time()
    Xf = dataset.frac_features(fit_idx)                     # (n, 20, NF)
    print(f"training features {Xf.shape} in {time.time() - t:.0f} s")
    X = Xf.reshape(-1, Xf.shape[2])
    Y = np.repeat(y[fit_idx], Xf.shape[1])
    t = time.time()
    clf = fit_hgb(X, Y)
    print(f"tree ensemble fitted in {time.time() - t:.0f} s")
    trees = from_sklearn(clf)
    trees.save(ROOT / "model" / "trees.npz")
    trees.write_mem(ROOT / "vivado" / "rtl" / "wpe_nodes.mem", ROOT / "vivado" / "rtl" / "wpe_leaves.mem")
    # quantization check: integer argmax vs floating-point argmax on the full maps
    full = Xf[:, -1, :]
    agree = np.mean(np.argmax(clf._raw_predict(full.astype(np.float64)), 1)
                    == np.argmax(trees.scores_vec(full), 1))
    print(f"integer vs float class agreement on full training maps: {agree * 100:.3f}%")

    # early-call calibration on the calibration lots
    t = time.time()
    ck = dataset.checkpoint_features(cal_idx, chk=CHK)
    cal_seq = []
    for F, Tl, N in ck:
        top, margin = top_margin(trees.scores_vec(F))
        cal_seq.append((top, margin, F[:, FI["frac"]]))
    margins, rep = calibrate_margins(cal_seq, y[cal_idx])
    print(f"calibration features + scores in {time.time() - t:.0f} s")
    for name, r in rep.items():
        print(f"  {name:<10} margin {r['margin']:>5}  calls {r['calls']:>5}  precision "
              f"{r['precision'] if r['precision'] is None else round(r['precision'], 3)}  "
              f"coverage {r['coverage']:.3f}")

    # probe-site check: smallest delta with at most SITE_FA flagged fault-free wafers
    with Pool(15, initializer=dataset._init) as p:
        dmax = np.array(p.map(_site_job, [(int(i),) for i in cal_idx], chunksize=64))
    srt = np.sort(dmax)[::-1]
    k_allowed = int(np.floor(SITE_FA * len(srt)))
    site_delta = int(srt[k_allowed]) + 1 if len(srt) > k_allowed else 1
    fa = float(np.mean(dmax >= site_delta))
    print(f"site check: delta {site_delta} ({site_delta / ONE:.3f}), false alarms on calibration "
          f"lots {fa * 100:.2f}%")

    out = dict(chk=CHK, persist=PERSIST, min_frac=MIN_FRAC, margin=margins, target_precision=TARGET_PREC,
               site_min=SITE_MIN, site_delta=site_delta, n_sites_multisite=4,
               calibration=rep, site_false_alarm_cal=fa, quant_agreement=float(agree),
               wafers=dict(fit=int(len(fit_idx)), cal=int(len(cal_idx)), test=int(sp["test"].sum())))
    (ROOT / "model" / "trained.json").write_text(json.dumps(out, indent=1))
    print("wrote model/trees.npz, rtl/wpe_nodes.mem, rtl/wpe_leaves.mem, model/trained.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

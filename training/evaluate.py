"""Evaluation on the test lots, using the fast model (same results as the RTL).

  E1  accuracy vs how much of the wafer is tested
  E2  confusion matrix on full maps
  E3  early calls, serpentine order
  E4  early calls, 4-site order (not used in training)
  E5  probe site faults
  E6  same recipe on the official WM-811K split, for reference

usage: python training/evaluate.py   -> results/eval_results.json, results/eval_tables.md
"""
import json
import sys
import time

import numpy as np

import dataset
from dataset import ROOT
from wpe_gbt import Trees
from wpe_params import CLASSES, NCLS, NITER

FRACS = [round(0.1 * k, 1) for k in range(1, 11)]
TREES = ROOT / "model" / "trees.npz"


def params():
    t = json.loads((ROOT / "model" / "trained.json").read_text())
    return t, dict(chk=t["chk"], persist=t["persist"], min_frac=t["min_frac"], margin=list(t["margin"]),
                   site_min=t["site_min"], site_delta=t["site_delta"])


def confusion_matrix(y, p, labels=None):
    cm = np.zeros((NCLS, NCLS), np.int64)
    np.add.at(cm, (np.asarray(y), np.asarray(p)), 1)
    return cm


def balanced_accuracy_score(y, p):
    """mean recall over the classes that show up in y"""
    cm = confusion_matrix(y, p)
    n = cm.sum(1)
    return float(np.mean(np.diag(cm)[n > 0] / n[n > 0]))


def early_table(recs, y):
    """per class: wafers, calls, correct calls, precision, coverage and how early"""
    rows = {}
    called = np.array([r["called"] for r in recs], bool)
    ccls = np.array([r["call_cls"] for r in recs])
    frac = np.array([r["call_ndies"] / max(1, r["N"]) for r in recs])
    early = called & (frac < 1.0)
    for k in range(1, NCLS):
        made = early & (ccls == k)
        corr = made & (y == k)
        n_k = int(np.sum(y == k))
        rows[CLASSES[k]] = dict(
            wafers=n_k, calls=int(made.sum()), correct=int(corr.sum()),
            precision=float(corr.sum() / made.sum()) if made.any() else None,
            coverage=float(corr.sum() / n_k) if n_k else None,
            median_tested=float(np.median(frac[corr])) if corr.any() else None,
            p25_tested=float(np.percentile(frac[corr], 25)) if corr.any() else None,
            p75_tested=float(np.percentile(frac[corr], 75)) if corr.any() else None)
    defect = y > 0
    tot = dict(
        false_alarm_none=float(np.mean(early[y == 0])),
        precision_all=float(np.mean(ccls[early] == y[early])) if early.any() else None,
        defect_called_correct=float(np.mean(early[defect] & (ccls[defect] == y[defect]))),
        median_tested_correct=float(np.median(frac[early & (ccls == y) & defect])),
        dies_saved_correct=float(np.mean(1 - frac[early & (ccls == y) & defect])))
    return rows, tot


def fmt(v, p=3):
    return "-" if v is None else (f"{v:.{p}f}" if isinstance(v, float) else str(v))


def main():
    t_all = time.time()
    D = dataset.load()
    sp = dataset.split(D)
    y_all = D["label"].astype(np.int64)
    te_idx = np.nonzero(sp["test"])[0]
    y = y_all[te_idx]
    trees = Trees.load(TREES)
    tj, prm = params()
    R = {"test_wafers": int(len(te_idx)), "test_lots": int(len(np.unique(D["lot"][te_idx])))}
    md = []

    # E1 / E2: accuracy vs share tested, full-map confusion
    t = time.time()
    Xf = dataset.frac_features(te_idx, fracs=FRACS)
    top = np.stack([np.argmax(trees.scores_vec(Xf[:, j]), 1) for j in range(len(FRACS))], 1)
    e1 = []
    for j, f in enumerate(FRACS):
        cm = confusion_matrix(y, top[:, j], labels=range(NCLS))
        e1.append(dict(frac=f, balanced_accuracy=float(balanced_accuracy_score(y, top[:, j])),
                       accuracy=float(np.mean(top[:, j] == y)),
                       recall={CLASSES[k]: float(cm[k, k] / cm[k].sum()) for k in range(NCLS)}))
    R["E1"] = e1
    cm = confusion_matrix(y, top[:, -1], labels=range(NCLS))
    R["E2"] = dict(confusion=cm.tolist(), recall=[float(cm[k, k] / cm[k].sum()) for k in range(NCLS)],
                   precision=[float(cm[k, k] / max(1, cm[:, k].sum())) for k in range(NCLS)],
                   balanced_accuracy=e1[-1]["balanced_accuracy"], accuracy=e1[-1]["accuracy"])
    print(f"E1/E2 done in {time.time() - t:.0f} s: full-map balanced accuracy "
          f"{R['E2']['balanced_accuracy']:.4f}")
    md.append("## E1. Class accuracy vs share of the wafer tested (test lots, serpentine order)\n")
    md.append("| tested | balanced acc. | accuracy | " + " | ".join(CLASSES) + " |")
    md.append("|---" * (3 + NCLS) + "|")
    for r in e1:
        md.append(f"| {r['frac'] * 100:.0f}% | {r['balanced_accuracy']:.3f} | {r['accuracy']:.3f} | "
                  + " | ".join(f"{r['recall'][c]:.2f}" for c in CLASSES) + " |")
    md.append("\nPer-class columns are recall (share of that class's wafers labelled correctly).\n")
    md.append("## E2. Full-map confusion matrix (rows: true class, columns: engine)\n")
    md.append("| true \\ engine | " + " | ".join(CLASSES) + " | recall |")
    md.append("|---" * (NCLS + 2) + "|")
    for k in range(NCLS):
        md.append(f"| {CLASSES[k]} | " + " | ".join(str(v) for v in cm[k]) + f" | {R['E2']['recall'][k]:.3f} |")
    md.append("| precision | " + " | ".join(f"{p:.3f}" for p in R["E2"]["precision"]) + " | |\n")

    # E3 / E4: early calls, serpentine and 4-site order
    for key, order in (("E3", "serpentine"), ("E4", "multisite")):
        t = time.time()
        recs = dataset.run_wafers(te_idx, TREES, prm, order=order)
        rows, tot = early_table(recs, y)
        R[key] = dict(order=order, per_class=rows, overall=tot)
        print(f"{key} ({order}) done in {time.time() - t:.0f} s: precision {tot['precision_all']:.3f}, "
              f"false alarms on normal wafers {tot['false_alarm_none'] * 100:.2f}%")
        title = ("## E3. Early calls (test lots, serpentine order)" if key == "E3" else
                 "## E4. Early calls with a 4-site (2 x 2) prober order (not seen in training)")
        md.append(title + "\n")
        md.append("| class | wafers | early calls | correct | precision | coverage | median share tested "
                  "(25th-75th pct.) |")
        md.append("|---|---|---|---|---|---|---|")
        for c, r in rows.items():
            rng_s = (f"{r['median_tested'] * 100:.0f}% ({r['p25_tested'] * 100:.0f}-{r['p75_tested'] * 100:.0f}%)"
                     if r["median_tested"] is not None else "-")
            md.append(f"| {c} | {r['wafers']} | {r['calls']} | {r['correct']} | {fmt(r['precision'])} | "
                      f"{fmt(r['coverage'])} | {rng_s} |")
        md.append(f"\nAll early calls: precision {fmt(tot['precision_all'])}; normal wafers with a false "
                  f"early call: {tot['false_alarm_none'] * 100:.2f}%; defect wafers named correctly before "
                  f"the end: {tot['defect_called_correct'] * 100:.1f}%; for those, median share tested "
                  f"{tot['median_tested_correct'] * 100:.0f}% (mean dies saved "
                  f"{tot['dies_saved_correct'] * 100:.0f}%).\n")

    # E5: probe-site faults (4-site order)
    rng = np.random.default_rng(55)
    sub = np.sort(rng.choice(te_idx, 6000, replace=False))
    prm4 = dict(prm, n_sites=4)
    e5 = []
    for p_fail in (0.0, 0.1, 0.25, 0.5, 1.0):
        t = time.time()
        faults = None if p_fail == 0 else [(int(rng.integers(0, 4)), p_fail, int(i)) for i in sub]
        recs = dataset.run_wafers(sub, TREES, prm4, order="multisite", faults=faults)
        flag = np.array([r["site_flag"] for r in recs], bool)
        sid = np.array([r["site_id"] for r in recs])
        fr = np.array([r["site_ndies"] / max(1, r["N"]) for r in recs])
        if p_fail == 0:
            row = dict(p_fail=0.0, flagged=float(flag.mean()))
        else:
            true_site = np.array([f[0] for f in faults])
            ok = flag & (sid == true_site)
            row = dict(p_fail=p_fail, detected=float(ok.mean()), wrong_site=float(np.mean(flag & ~ok)),
                       median_tested=float(np.median(fr[ok])) if ok.any() else None)
        e5.append(row)
        print(f"E5 p_fail {p_fail}: {row} ({time.time() - t:.0f} s)")
    R["E5"] = dict(wafers=int(len(sub)), site_min=prm["site_min"], site_delta=prm["site_delta"], rows=e5)
    md.append("## E5. Probe-site faults (6,000 test wafers, 4-site order, one damaged site)\n")
    md.append(f"Check: a site is flagged when it has tested at least {prm['site_min']} dies and its fail "
              f"rate exceeds the other sites' by {prm['site_delta'] / 4096:.3f} or more.\n")
    md.append("| damaged site fails this share of good dies | flagged (correct site) | wrong site | "
              "median share of wafer tested at the flag |")
    md.append("|---|---|---|---|")
    for r in e5:
        if r["p_fail"] == 0:
            md.append(f"| 0 (healthy card) | {r['flagged'] * 100:.2f}% false alarms | - | - |")
        else:
            md.append(f"| {r['p_fail']:.2f} | {r['detected'] * 100:.1f}% | {r['wrong_site'] * 100:.2f}% | "
                      f"{fmt(r['median_tested'] * 100 if r['median_tested'] is not None else None, 1)}% |")
    md.append("")

    # E6: official split (full maps, same recipe, floating point)
    from sklearn.ensemble import HistGradientBoostingClassifier   # here: worker processes skip it
    t = time.time()
    otr, ote = np.nonzero(sp["official_train"])[0], np.nonzero(sp["official_test"])[0]
    Xo = dataset.frac_features(otr)
    Xt = dataset.frac_features(ote, fracs=[1.0])[:, 0]
    X = Xo.reshape(-1, Xo.shape[2])
    Y = np.repeat(y_all[otr], Xo.shape[1])
    cnt = np.bincount(Y, minlength=NCLS)
    w = len(Y) / (NCLS * cnt)
    clf = HistGradientBoostingClassifier(max_iter=NITER, learning_rate=0.2, max_depth=3, l2_regularization=1.0,
                                         early_stopping=False, random_state=0)
    clf.fit(X, Y, sample_weight=w[Y])
    p = clf.predict(Xt)
    R["E6"] = dict(train_wafers=int(len(otr)), test_wafers=int(len(ote)),
                   balanced_accuracy=float(balanced_accuracy_score(y_all[ote], p)),
                   accuracy=float(np.mean(p == y_all[ote])))
    print(f"E6 official split: balanced accuracy {R['E6']['balanced_accuracy']:.4f} ({time.time() - t:.0f} s)")
    md.append("## E6. Reference: WM-811K's official split\n")
    md.append(f"Same features and training recipe (floating point), trained on the official 'Training' "
              f"wafers ({len(otr)}) and tested on the official 'Test' wafers ({len(ote)}) at 100% tested: "
              f"balanced accuracy {R['E6']['balanced_accuracy']:.3f}, accuracy {R['E6']['accuracy']:.3f}. "
              f"The official split is also lot-disjoint but has a different class mix between its halves "
              f"(for example 8,554 Edge-Ring training wafers vs 1,126 test wafers).\n")

    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "eval_results.json").write_text(json.dumps(R, indent=1))
    head = (f"# Evaluation on held-out lots\n\nTest set: {R['test_wafers']} wafers from {R['test_lots']} lots "
            f"that were not used for training or calibration. Engine settings from model/trained.json "
            f"(checkpoint every {tj['chk']} dies, call after {tj['persist']} checkpoints in a row, "
            f"margins for 90% precision on the calibration lots).\n")
    (ROOT / "results" / "eval_tables.md").write_text(head + "\n" + "\n".join(md))
    print(f"wrote results/eval_results.json and results/eval_tables.md ({time.time() - t_all:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

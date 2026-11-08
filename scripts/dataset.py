"""WM-811K access, the lot-disjoint split, and parallel feature extraction."""
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))

from wpe_fast import features_at, run_fast, stream_series   # noqa: E402
from wpe_geom import dies_from_map, geometry, inject_site_fault  # noqa: E402

DATA = ROOT / "data" / "wm811k_labeled.npz"
TEST_SEED, CAL_SEED = 1, 2
TEST_SHARE, CAL_SHARE = 0.3, 0.2
TRAIN_FRACS = [round(0.05 * k, 2) for k in range(1, 21)]      # 5%, 10%, ..., 100%


def load():
    if not DATA.exists():
        raise SystemExit("data/wm811k_labeled.npz missing: run python scripts/get_data.py first")
    d = np.load(DATA)
    return {k: d[k] for k in d.files}


def split(D):
    """Lot-disjoint split: 30% of lots for test; 20% of the remaining lots for calibration.

    Wafers of one lot are similar (72% of defect wafers share their label with another
    wafer of the same lot), so a wafer-level random split would leak."""
    lot = D["lot"]
    lots = np.unique(lot)
    test_lots = set(np.random.default_rng(TEST_SEED).choice(lots, int(TEST_SHARE * len(lots)), replace=False))
    te = np.array([l in test_lots for l in lot])
    tr_lots = np.unique(lot[~te])
    cal_lots = set(np.random.default_rng(CAL_SEED).choice(tr_lots, int(CAL_SHARE * len(tr_lots)), replace=False))
    cal = np.array([(not t) and (l in cal_lots) for l, t in zip(lot, te)])
    return {"fit": ~te & ~cal, "cal": cal, "test": te, "official_train": D["split"] == 0,
            "official_test": D["split"] == 1}


_D = None


def _init():
    global _D
    d = np.load(DATA)
    _D = {k: d[k] for k in ("maps", "offset", "h", "w")}


def wafer_map(i, D=None):
    D = D or _D
    a, b = D["offset"][i], D["offset"][i + 1]
    return D["maps"][a:b].reshape(int(D["h"][i]), int(D["w"][i]))


def _frac_job(args):
    i, fracs, order = args
    m = wafer_map(i)
    cfg = geometry(m > 0)
    S = stream_series(cfg, dies_from_map(m, order))
    Tl = np.clip(np.ceil(np.array(fracs) * S["Tf"]).astype(np.int64), 1, S["Tf"])
    return features_at(S, cfg, Tl).astype(np.int32)


def frac_features(indices, fracs=TRAIN_FRACS, order="serpentine", procs=15):
    """(n, len(fracs), NF) features after ceil(frac * N) dies."""
    with Pool(procs, initializer=_init) as p:
        return np.stack(p.map(_frac_job, [(int(i), fracs, order) for i in indices], chunksize=64))


def _ck_job(args):
    i, chk, order = args
    m = wafer_map(i)
    cfg = geometry(m > 0)
    cfg.chk = chk
    S = stream_series(cfg, dies_from_map(m, order))
    return features_at(S, cfg, S["ck_T"]).astype(np.int32), S["ck_T"].astype(np.int32), S["Tf"]


def checkpoint_features(indices, chk=16, order="serpentine", procs=15):
    """Per wafer: (features at every checkpoint, dies tested at each checkpoint, N)."""
    with Pool(procs, initializer=_init) as p:
        return p.map(_ck_job, [(int(i), chk, order) for i in indices], chunksize=32)


_T = None


def _init_run(trees_path):
    global _T
    _init()
    from wpe_gbt import Trees
    _T = Trees.load(trees_path)


def _run_job(args):
    i, params, order, fault = args
    m = wafer_map(i)
    cfg = geometry(m > 0)
    for k, v in params.items():
        setattr(cfg, k, v)
    dies = dies_from_map(m, order)
    if fault is not None:
        site, p_fail, seed = fault
        dies = inject_site_fault(dies, site, p_fail, np.random.default_rng(seed))
    recs = run_fast(cfg, dies, _T)
    keep = ("ndies", "top", "margin", "called", "call_cls", "call_ndies", "site_flag", "site_id", "site_ndies")
    return {k: recs[-1][k] for k in keep} | {"N": cfg.n_total}


def run_wafers(indices, trees_path, params, order="serpentine", faults=None, procs=15):
    """Full engine (fast model) per wafer; returns the final record of each wafer."""
    faults = faults or [None] * len(indices)
    with Pool(procs, initializer=_init_run, initargs=(str(trees_path),)) as p:
        return p.map(_run_job, [(int(i), params, order, f) for i, f in zip(indices, faults)], chunksize=32)

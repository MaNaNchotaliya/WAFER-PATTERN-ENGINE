"""Cross-check the per-die model (wpe_model) against the vectorized model (wpe_fast).

Random wafers (sizes up to 256 x 256, random die masks and fail patterns), random probe
orders (serpentine, multi-site, random), injected duplicate and out-of-range dies, random
configuration registers and random or trained trees. Every field of every checkpoint
record must match.

Usage: python model/selftest_models.py [cases] [--trained]
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wpe_fast import run_fast                        # noqa: E402
from wpe_gbt import Trees                            # noqa: E402
from wpe_geom import geometry, multisite, serpentine  # noqa: E402
from wpe_model import Engine                         # noqa: E402
from wpe_params import CK_KEYS, NCLS, NF, NLEAF, NNODE, NTREE, ONE  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def random_trees(rng):
    return Trees(rng.integers(0, NF, (NTREE, NNODE)), rng.integers(-4200, 4200, (NTREE, NNODE)),
                 rng.integers(-300, 300, (NTREE, NLEAF)), rng.integers(-500, 500, NCLS))


def random_wafer(rng):
    big = rng.random() < 0.08
    H = int(rng.integers(150, 257)) if big else int(rng.integers(1, 48))
    W = int(rng.integers(150, 257)) if big else int(rng.integers(1, 48))
    yy, xx = np.mgrid[0:H, 0:W]
    shape = rng.integers(0, 3)
    if shape == 0:      # disc
        valid = ((xx - (W - 1) / 2) / (W / 2)) ** 2 + ((yy - (H - 1) / 2) / (H / 2)) ** 2 <= 1.0
    elif shape == 1:    # full rectangle
        valid = np.ones((H, W), bool)
    else:               # random holes
        valid = rng.random((H, W)) < 0.8
    if not valid.any():
        valid[rng.integers(0, H), rng.integers(0, W)] = True
    kind = rng.integers(0, 4)
    if kind == 0:
        fail = rng.random((H, W)) < rng.uniform(0, 0.6)
    elif kind == 1:     # blob
        cx, cy, r = rng.uniform(0, W), rng.uniform(0, H), rng.uniform(1, max(H, W) / 2)
        fail = (xx - cx) ** 2 + (yy - cy) ** 2 < r * r
        fail |= rng.random((H, W)) < 0.03
    elif kind == 2:     # line
        a = rng.uniform(0, np.pi)
        fail = np.abs((xx - W / 2) * np.sin(a) - (yy - H / 2) * np.cos(a)) < 0.7
    else:
        fail = rng.random((H, W)) < 0.97
    return valid, fail & valid


def random_stream(rng, valid, fail, cfg):
    o = rng.integers(0, 3)
    if o == 0:
        seq = serpentine(valid)
    elif o == 1:
        sx, sy = int(rng.integers(1, 4)), int(rng.integers(1, 3))
        seq = multisite(valid, sx, sy)
    else:
        ys, xs = np.nonzero(valid)
        p = rng.permutation(len(xs))
        seq = [(int(xs[i]), int(ys[i]), int(rng.integers(0, 8))) for i in p]
    dies = [(x, y, int(fail[y, x]), s) for x, y, s in seq]
    # duplicates (retests) and dies outside the map
    for _ in range(int(rng.integers(0, 6))):
        if dies:
            x, y, _, s = dies[int(rng.integers(0, len(dies)))]
            dies.insert(int(rng.integers(0, len(dies) + 1)), (x, y, int(rng.integers(0, 2)), s))
    for _ in range(int(rng.integers(0, 4))):
        dies.insert(int(rng.integers(0, len(dies) + 1)),
                    (int(rng.integers(cfg.w, 256)) if cfg.w < 256 else 0,
                     int(rng.integers(0, 256)) if cfg.w < 256 else int(rng.integers(cfg.h, 256)) if cfg.h < 256 else 0,
                     1, int(rng.integers(0, 8))))
    return dies


def random_cfg(rng, valid):
    cfg = geometry(valid)
    cfg.chk = int(rng.choice([0, 1, 3, 8, 16, 16, 16, 255]))
    cfg.persist = int(rng.integers(0, 16))
    cfg.min_frac = int(rng.choice([0, 0, 409, 2048, ONE]))
    cfg.margin = [int(v) for v in rng.integers(0, 2000, NCLS)]
    cfg.n_sites = int(rng.integers(0, 9))
    cfg.site_min = int(rng.integers(0, 80))
    cfg.site_delta = int(rng.integers(-500, 4097))
    return cfg


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 300
    rng = np.random.default_rng(12345)
    trained = ROOT / "model" / "trees.npz"
    use_trained = "--trained" in sys.argv
    bad = 0
    nck = 0
    for i in range(n):
        if use_trained and trained.exists() and i % 2 == 0:
            trees = Trees.load(trained)
        else:
            trees = random_trees(rng)
        valid, fail = random_wafer(rng)
        cfg = random_cfg(rng, valid)
        dies = random_stream(rng, valid, fail, cfg)
        a = Engine(trees).run(cfg, dies)
        b = run_fast(cfg, dies, trees)
        nck += len(a)
        same = len(a) == len(b) and all(ra[k] == rb[k] for ra, rb in zip(a, b) for k in CK_KEYS)
        if not same:
            bad += 1
            if bad <= 3:
                print(f"case {i}: MISMATCH (W={cfg.w} H={cfg.h} n={len(dies)} chk={cfg.chk}) "
                      f"records {len(a)} vs {len(b)}")
                for ra, rb in zip(a, b):
                    diff = [k for k in CK_KEYS if ra[k] != rb[k]]
                    if diff:
                        print("   first difference at ndies", ra["ndies"], diff[:6],
                              [(ra[k], rb[k]) for k in diff[:6]])
                        break
    print(f"{n} cases, {nck} checkpoint records: {'ALL MATCH' if bad == 0 else f'{bad} MISMATCHES'}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

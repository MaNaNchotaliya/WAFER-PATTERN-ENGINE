"""Integer gradient-boosted tree ensemble (the engine's classifier) and the early-call rule.

Tree format (same in the ROM): NTREE = NITER * NCLS complete binary trees of depth 3,
tree t adds to class t % NCLS. Internal node n (heap order, root 0) holds a feature index
and a threshold; the walk goes right when feature > threshold, so after 3 levels
node - 7 is the leaf index. Leaves and per-class start values are Q.8 integers.
"""
import numpy as np

from wpe_params import (DEPTH, FI, LEAF_Q, NCLS, NF, NITER, NLEAF, NNODE, NTREE, RUN_MAX,
                        SCORE_W)

THR_MIN, THR_MAX = -(1 << 13), (1 << 13) - 1          # 14-bit signed thresholds
SCORE_MIN = -(1 << (SCORE_W - 1))


class Trees:
    def __init__(self, feat, thr, leaf, base):
        self.feat = np.asarray(feat, np.int64).reshape(NTREE, NNODE)
        self.thr = np.asarray(thr, np.int64).reshape(NTREE, NNODE)
        self.leaf = np.asarray(leaf, np.int64).reshape(NTREE, NLEAF)
        self.base = np.asarray(base, np.int64).reshape(NCLS)
        lim = int(np.abs(self.base).max() + np.abs(self.leaf).reshape(NITER, NCLS, NLEAF).max(2).sum(0).max())
        if lim >= (1 << (SCORE_W - 1)):
            raise ValueError("scores could overflow the accumulator")
        if self.feat.min() < 0 or self.feat.max() >= NF:
            raise ValueError("bad feature index")

    # -------------------------------------------------------------- files
    def save(self, path):
        np.savez(path, feat=self.feat, thr=self.thr, leaf=self.leaf, base=self.base)

    @classmethod
    def load(cls, path):
        d = np.load(path)
        return cls(d["feat"], d["thr"], d["leaf"], d["base"])

    def write_mem(self, node_path, leaf_path):
        """$readmemh images: node word {feat[5:0], thr[13:0]}; leaf word 16-bit, then NCLS bases."""
        with open(node_path, "w", newline="\n") as f:
            for t in range(NTREE):
                for n in range(NNODE):
                    f.write(f"{(int(self.feat[t, n]) << 14) | (int(self.thr[t, n]) & 0x3FFF):05x}\n")
        with open(leaf_path, "w", newline="\n") as f:
            for t in range(NTREE):
                for k in range(NLEAF):
                    f.write(f"{int(self.leaf[t, k]) & 0xFFFF:04x}\n")
            for k in range(NCLS):
                f.write(f"{int(self.base[k]) & 0xFFFF:04x}\n")

    # -------------------------------------------------------------- evaluation
    def scores(self, x):
        """Scalar walk (mirrors the hardware): x = list of NF ints -> list of NCLS ints."""
        s = [int(b) for b in self.base]
        for t in range(NTREE):
            n = 0
            for _ in range(DEPTH):
                go_right = x[self.feat[t, n]] > self.thr[t, n]
                n = 2 * n + 1 + int(go_right)
            s[t % NCLS] += int(self.leaf[t, n - NNODE])
        return s

    def scores_vec(self, X, chunk=20000):
        """Vectorized over samples and trees: X (n, NF) -> (n, NCLS)."""
        X = np.asarray(X, np.int64)
        out = np.empty((X.shape[0], NCLS), np.int64)
        tid = np.arange(NTREE)
        for a in range(0, X.shape[0], chunk):
            Xc = X[a:a + chunk]
            n = Xc.shape[0]
            rows = np.arange(n)[:, None]
            node = np.zeros((n, NTREE), np.int64)
            for _ in range(DEPTH):
                f = self.feat[tid, node]                     # (n, NTREE) feature index per tree
                go = Xc[rows, f] > self.thr[tid, node]
                node = 2 * node + 1 + go
            lv = self.leaf[tid, node - NNODE]                # (n, NTREE)
            out[a:a + n] = self.base + lv.reshape(n, NITER, NCLS).sum(1)
        return out


def from_sklearn(clf):
    """Quantize a fitted HistGradientBoostingClassifier (max_depth <= 3, NCLS classes)."""
    preds = clf._predictors
    if len(preds) != NITER or len(preds[0]) != NCLS:
        raise ValueError(f"need {NITER} iterations x {NCLS} classes")
    feat = np.zeros((NTREE, NNODE), np.int64)
    thr = np.zeros((NTREE, NNODE), np.int64)
    leaf = np.zeros((NTREE, NLEAF), np.int64)
    scale = 1 << LEAF_Q
    for it in range(NITER):
        for c in range(NCLS):
            t = it * NCLS + c
            nodes = preds[it][c].nodes

            def fill(src, dst, depth):
                # dst: heap index in the complete tree; a shallow leaf is copied to every slot below
                nd = nodes[src]
                if depth == DEPTH:
                    if not nd["is_leaf"]:
                        raise ValueError("tree deeper than DEPTH")
                    leaf[t, dst - NNODE] = int(np.rint(nd["value"] * scale))
                    return
                if nd["is_leaf"]:
                    feat[t, dst], thr[t, dst] = 0, THR_MAX     # dummy test, both sides equal
                    fill(src, 2 * dst + 1, depth + 1)
                    fill(src, 2 * dst + 2, depth + 1)
                    return
                if nd["is_categorical"]:
                    raise ValueError("categorical split")
                feat[t, dst] = int(nd["feature_idx"])
                # integer features: x <= thr  <=>  x <= floor(thr)
                thr[t, dst] = int(np.clip(np.floor(nd["num_threshold"]), THR_MIN, THR_MAX))
                fill(int(nd["left"]), 2 * dst + 1, depth + 1)
                fill(int(nd["right"]), 2 * dst + 2, depth + 1)

            fill(0, 0, 0)
    base = np.rint(np.asarray(clf._baseline_prediction, np.float64).ravel() * scale).astype(np.int64)
    return Trees(feat, thr, leaf, base)


# ------------------------------------------------------------------ decisions
def top2(s):
    """Best class (lowest index wins ties) and its margin over the runner-up."""
    best, bi, second = s[0], 0, SCORE_MIN
    for k in range(1, NCLS):
        if s[k] > best:
            second, best, bi = best, s[k], k
        elif s[k] > second:
            second = s[k]
    return bi, best - second


class Decider:
    """Early-call rule: a pattern (class != none) is called once the same class has led
    with margin >= margin[class] for `persist` checkpoints in a row and at least
    min_frac of the wafer has been tested. The first call is latched."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.run_cls = 0
        self.run_len = 0
        self.called = 0
        self.call_cls = 0
        self.call_ndies = 0

    def step(self, feats, scores, ndies):
        top, margin = top2(scores)
        ok = top != 0 and margin >= self.cfg.margin[top] and feats[FI["frac"]] >= self.cfg.min_frac
        if ok:
            if self.run_len > 0 and top == self.run_cls:
                self.run_len = min(self.run_len + 1, RUN_MAX)
            else:
                self.run_cls, self.run_len = top, 1
        else:
            self.run_len = 0
        if not self.called and ok and self.run_len >= max(self.cfg.persist, 1):
            self.called, self.call_cls, self.call_ndies = 1, top, ndies
        return top, margin

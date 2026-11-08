"""Shared constants, configuration and file formats of the wafer-pattern engine.

Keep every constant in sync with rtl/wpe_pkg.sv.
"""
from dataclasses import dataclass, field
from typing import List

# ------------------------------------------------------------------ geometry
CW_XY = 8                    # die coordinate width: maps up to 256 x 256
MAXD = 1 << CW_XY
NR = 5                       # rings (centre ... edge)
NS = 8                       # sectors (octants)
NZ = NR * NS                 # zones
# ring boundaries at 0.3, 0.5, 0.7, 0.9 of the wafer radius, as (b^2 * 65536)
RING_B2 = [5898, 16384, 32113, 53084]

# ------------------------------------------------------------------ fixed point
QB = 12                      # rates are Q.12: 4096 = 1.0
ONE = 1 << QB
TMIN = 4                     # a sector rate counts once the sector has this many dies
CNT_W = 17                   # die counters (up to 65,536 dies)
FW = 14                      # feature width (signed)
NSITE = 8                    # probe sites tracked (site id 0..7)

# ------------------------------------------------------------------ classifier
CLASSES = ["none", "Center", "Donut", "Edge-Loc", "Edge-Ring", "Loc", "Near-full", "Random", "Scratch"]
NCLS = len(CLASSES)
DEPTH = 3                    # every tree is a complete binary tree of this depth
NNODE = (1 << DEPTH) - 1     # 7 internal nodes per tree
NLEAF = 1 << DEPTH           # 8 leaves per tree
NITER = 64                   # boosting rounds -> NITER * NCLS trees
NTREE = NITER * NCLS
LEAF_Q = 8                   # leaf values and scores in Q.8 logit units
SCORE_W = 20                 # signed score accumulator width
RUN_MAX = 15                 # run-length counter saturates here

FEATURES = (["frac", "f"]
            + [f"fr{r}" for r in range(NR)]          # ring fail rates
            + [f"cov{r}" for r in range(NR)]         # ring coverage (tested / dies in ring)
            + [f"dr{r}" for r in range(NR)]          # ring rate minus wafer rate (signed)
            + [f"smax{r}" for r in range(NR)]        # highest sector rate in the ring
            + [f"smin{r}" for r in range(NR)]        # lowest sector rate in the ring
            + [f"sconc{r}" for r in range(NR)]       # share of the ring's fails in its worst sector
            + ["zconc", "q", "lift", "qmax", "qmin", "aniso", "ffpf"])
NF = len(FEATURES)           # 39
FI = {n: i for i, n in enumerate(FEATURES)}

# ------------------------------------------------------------------ config registers
# address map of the configuration bus (cfg_addr), shared with the testbench
REG = {"w": 0, "h": 1, "cx2": 2, "cy2": 3, "wx": 4, "wy": 5,
       "thr0": 6, "thr1": 7, "thr2": 8, "thr3": 9, "n_total": 10,
       "nr0": 11, "nr1": 12, "nr2": 13, "nr3": 14, "nr4": 15,
       "chk": 16, "persist": 17, "min_frac": 18,
       **{f"margin{k}": 19 + k for k in range(NCLS)},
       "n_sites": 28, "site_min": 29, "site_delta": 30}
CFG_DW = 36


@dataclass
class Config:
    w: int = 1
    h: int = 1
    cx2: int = 0
    cy2: int = 0
    wx: int = 255
    wy: int = 255
    thr: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    n_total: int = 0
    n_ring: List[int] = field(default_factory=lambda: [0] * NR)
    chk: int = 16
    persist: int = 3
    min_frac: int = 0
    margin: List[int] = field(default_factory=lambda: [0] * NCLS)
    n_sites: int = 0
    site_min: int = 64
    site_delta: int = 1024

    def registers(self):
        """(address, value) pairs in bus order."""
        v = {"w": self.w, "h": self.h, "cx2": self.cx2, "cy2": self.cy2, "wx": self.wx, "wy": self.wy,
             "n_total": self.n_total, "chk": self.chk, "persist": self.persist, "min_frac": self.min_frac,
             "n_sites": self.n_sites, "site_min": self.site_min, "site_delta": self.site_delta}
        for k in range(4):
            v[f"thr{k}"] = self.thr[k]
        for r in range(NR):
            v[f"nr{r}"] = self.n_ring[r]
        for k in range(NCLS):
            v[f"margin{k}"] = self.margin[k] & 0xFFFF
        return sorted((REG[n], val) for n, val in v.items())


# ------------------------------------------------------------------ test vector files
def write_cfg(path, cfg: Config):
    """One 'address value' line per register (decimal)."""
    with open(path, "w", newline="\n") as f:
        for a, v in cfg.registers():
            f.write(f"{a} {v}\n")


def write_stim(path, dies):
    """One die per line: x y fail site last (decimal)."""
    with open(path, "w", newline="\n") as f:
        n = len(dies)
        for i, (x, y, fl, s) in enumerate(dies):
            f.write(f"{x} {y} {fl} {s} {1 if i == n - 1 else 0}\n")


# checkpoint record fields, in the order the testbench writes them
CK_KEYS = (["ndies", "n_dup", "n_oor", "final"] + [f"f_{n}" for n in FEATURES]
           + [f"s{k}" for k in range(NCLS)]
           + ["top", "margin", "run_cls", "run_len", "called", "call_cls", "call_ndies",
              "site_flag", "site_id", "site_ndies"])


def write_ck(path, records):
    with open(path, "w", newline="\n") as f:
        for r in records:
            f.write(" ".join(str(r[k]) for k in CK_KEYS) + "\n")


def read_ck(path):
    recs = []
    with open(path) as f:
        for line in f:
            p = line.split()
            if len(p) == len(CK_KEYS):
                recs.append({k: int(v) for k, v in zip(CK_KEYS, p)})
    return recs

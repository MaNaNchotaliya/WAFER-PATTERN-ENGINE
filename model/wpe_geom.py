"""Tester (host) side: geometry registers, probe orders and probe site faults.

The geometry registers are computed once per product from the probe map: the centre, an
x/y scale so the die grid comes out round, and the 4 ring thresholds. All integer math so
the engine and the model agree exactly.
"""
import numpy as np

from wpe_params import NR, RING_B2, Config


def zone_of(x, y, cx2, cy2, wx, wy, thr):
    """ring and sector of one die, same math as wpe_zone.v"""
    u = (2 * x - cx2) * wx
    v = (2 * y - cy2) * wy
    d2 = u * u + v * v
    ring = sum(1 for t in thr if d2 > t)
    a, b = u, -v                     # b > 0 points up (towards row 0)
    if a > 0 and b >= 0:
        q = 0
    elif a <= 0 and b > 0:
        q = 1
    elif a < 0 and b <= 0:
        q = 2
    else:
        q = 3
    lead = abs(a) > abs(b) if q % 2 == 0 else abs(b) > abs(a)
    return ring, 2 * q + (0 if lead else 1)


def geometry(valid, cfg=None):
    """fill the geometry registers from a bool map of the dies to test"""
    cfg = cfg or Config()
    ys, xs = np.nonzero(valid)
    xmin, xmax, ymin, ymax = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    cfg.h, cfg.w = int(valid.shape[0]), int(valid.shape[1])
    cfg.cx2, cfg.cy2 = xmin + xmax, ymin + ymax
    ex, ey = xmax - xmin + 1, ymax - ymin + 1
    m = max(ex, ey)
    cfg.wx = (255 * ey + m // 2) // m          # x scale follows the y extent and vice versa
    cfg.wy = (255 * ex + m // 2) // m
    u = (2 * xs.astype(np.int64) - cfg.cx2) * cfg.wx
    v = (2 * ys.astype(np.int64) - cfg.cy2) * cfg.wy
    d2 = u * u + v * v
    rmax2 = int(d2.max())
    cfg.thr = [(rmax2 * b) >> 16 for b in RING_B2]
    ring = np.zeros(len(d2), np.int64)
    for t in cfg.thr:
        ring += d2 > t
    cfg.n_ring = [int(c) for c in np.bincount(ring, minlength=NR)]
    cfg.n_total = int(len(xs))
    return cfg


def zone_maps(cfg):
    """ring and sector of every grid position"""
    yy, xx = np.mgrid[0:cfg.h, 0:cfg.w]
    u = (2 * xx.astype(np.int64) - cfg.cx2) * cfg.wx
    v = (2 * yy.astype(np.int64) - cfg.cy2) * cfg.wy
    d2 = u * u + v * v
    ring = np.zeros(d2.shape, np.int64)
    for t in cfg.thr:
        ring += d2 > t
    a, b = u, -v
    q = np.where((a > 0) & (b >= 0), 0,
                 np.where((a <= 0) & (b > 0), 1, np.where((a < 0) & (b <= 0), 2, 3)))
    lead = np.where(q % 2 == 0, np.abs(a) > np.abs(b), np.abs(b) > np.abs(a))
    return ring, 2 * q + np.where(lead, 0, 1)


# probe orders
def serpentine(valid):
    """single site prober: rows top to bottom, direction alternates"""
    out = []
    H, W = valid.shape
    for y in range(H):
        xs = range(W) if y % 2 == 0 else range(W - 1, -1, -1)
        out += [(x, y, 0) for x in xs if valid[y, x]]
    return out


def multisite(valid, sx=2, sy=2):
    """sx*sy dies per touchdown, touchdowns in serpentine order, site = j*sx + i"""
    out = []
    H, W = valid.shape
    nbx, nby = (W + sx - 1) // sx, (H + sy - 1) // sy
    for by in range(nby):
        bxs = range(nbx) if by % 2 == 0 else range(nbx - 1, -1, -1)
        for bx in bxs:
            for j in range(sy):
                for i in range(sx):
                    x, y = bx * sx + i, by * sy + j
                    if x < W and y < H and valid[y, x]:
                        out.append((x, y, j * sx + i))
    return out


def dies_from_map(m, order="serpentine"):
    """die stream (x, y, fail, site) from a WM-811K map (0 no die, 1 pass, 2 fail)"""
    valid = m > 0
    seq = serpentine(valid) if order == "serpentine" else multisite(valid)
    return [(x, y, int(m[y, x] == 2), s) for x, y, s in seq]


def inject_site_fault(dies, site, p_fail, rng):
    """bad probe site: every good die it tests fails with probability p_fail"""
    out = []
    for x, y, fl, s in dies:
        if s == site and not fl and rng.random() < p_fail:
            fl = 1
        out.append((x, y, fl, s))
    return out

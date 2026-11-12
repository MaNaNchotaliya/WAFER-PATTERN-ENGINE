# How the engine works (exact rules)

These are the exact rules the Verilog follows. `model/wpe_model.py` does the same thing one
die at a time (the RTL gets checked against it), and `model/wpe_fast.py` does it again with
cumulative sums, which is what training and evaluation use. Everything is integer math.

## 1. Inputs

**Die stream** (valid/ready handshake): `x`, `y` (8 bits each, column and row in the probe
map), `fail` (1 bit), `site` (3 bits, probe site of a multi-site card) and `last` (1 on the
wafer's last die).

**Configuration registers** (written over the `cfg_we / cfg_addr / cfg_wdata` bus before
`start`). The host computes the geometry once per product from its probe map
(`model/wpe_geom.py: geometry`):

| addr | register | bits | meaning |
|---|---|---|---|
| 0, 1 | `w`, `h` | 9 | map width and height, 1..256 |
| 2, 3 | `cx2`, `cy2` | 9 | twice the centre: `xmin + xmax`, `ymin + ymax` of the dies to test |
| 4, 5 | `wx`, `wy` | 8 | axis scales: `wx = round(255 ey / max(ex, ey))`, `wy = round(255 ex / max(ex, ey))`, where `ex`, `ey` are the x and y extents of the dies |
| 6..9 | `thr0..3` | 36 | ring thresholds: `(rmax2 * B_k) >> 16` with `B = 5898, 16384, 32113, 53084` (0.3, 0.5, 0.7, 0.9 of the radius, squared, times 65536) and `rmax2` the largest `d2` (below) of any die |
| 10 | `n_total` | 17 | dies in the probe map |
| 11..15 | `nr0..4` | 17 | dies per ring |
| 16 | `chk` | 8 | checkpoint every `chk` accepted dies (0: only at the last die) |
| 17 | `persist` | 4 | checkpoints in a row needed for an early call (0 counts as 1) |
| 18 | `min_frac` | 13 | no call before this share of the wafer (Q.12) |
| 19..27 | `margin0..8` | 16 | score margin needed to call each class (Q.8; 65535 = never) |
| 28 | `n_sites` | 4 | probe sites to check (0: off) |
| 29 | `site_min` | 17 | dies a site must have tested before it is checked |
| 30 | `site_delta` | 14 | signed; flag a site when its fail rate exceeds the other sites' by this much (Q.12) |

## 2. Zones

For a die at (x, y):

    u  = (2x - cx2) * wx
    v  = (2y - cy2) * wy
    d2 = u^2 + v^2
    ring = number of k in 0..3 with d2 > thr_k            (0 = centre ... 4 = edge)

The scales make the elliptical die grid round (dies are rarely square). The sector is the
octant of (a, b) = (u, -v):

    q = 0 if a > 0 and b >= 0;  1 if a <= 0 and b > 0;  2 if a < 0 and b <= 0;  else 3
    lead = |a| > |b| for even q,  |b| > |a| for odd q
    sector = 2q + (0 if lead else 1)

Zone = ring * 8 + sector (40 zones).

## 3. Per die

1. If `x >= w` or `y >= h`: count it in `n_oor` and ignore it.
2. If the position was already tested: count it in `n_dup` and ignore it (the first result
   counts; retests are not used).
3. Otherwise, for each of the 8 neighbours inside the map that was already tested: if both
   dies fail, add 1 to `FF[dir]`; if exactly one fails, add 1 to `X[dir]`. Directions:
   0 horizontal (-1,0)(1,0), 1 vertical (0,-1)(0,1), 2 "\" (-1,-1)(1,1), 3 "/" (1,-1)(-1,1).
   Each neighbouring pair is therefore counted once, when its second die arrives.
4. Add 1 to the tested counter, and `fail` to the failed counter, of the die's zone, ring,
   probe site and of the wafer (`T`, `F`). Mark the position tested (and failed).
5. If `chk > 0`, a checkpoint is due after every `chk`-th accepted die.

A checkpoint also runs after the die flagged `last`, whether or not that die was accepted.
When both apply, it runs once. `n_dup` and `n_oor` are 17-bit counters that wrap.

## 4. Features (Q.12, 4096 = 1.0)

    RATE(a, b) = 0 if b = 0;  4096 if a >= b;  else floor(a * 4096 / b)

| # | name | value |
|---|---|---|
| 0 | `frac` | RATE(T, n_total) |
| 1 | `f` | RATE(F, T) |
| 2-6 | `fr0..4` | RATE(F_ring, T_ring) |
| 7-11 | `cov0..4` | RATE(T_ring, nr_ring) |
| 12-16 | `dr0..4` | fr_r - f (signed) |
| 17-21 | `smax0..4` | highest RATE(F_zone, T_zone) over the ring's sectors with T_zone >= 4; 0 if none |
| 22-26 | `smin0..4` | lowest of the same; 0 if none |
| 27-31 | `sconc0..4` | RATE(max over the ring's sectors of F_zone, F_ring) |
| 32 | `zconc` | RATE(max over all zones of F_zone, F) |
| 33 | `q` | RATE(2 FF, 2 FF + X), FF and X summed over directions |
| 34 | `lift` | q - f (signed) |
| 35, 36 | `qmax`, `qmin` | max and min over directions of RATE(2 FF_d, 2 FF_d + X_d) |
| 37 | `aniso` | qmax - qmin |
| 38 | `ffpf` | RATE(FF, 4 F) |

`q` estimates the chance that a neighbour of a failing die also fails; it equals `f` when
fails are independent and is higher for clusters. `aniso` is high for lines (scratches),
`ffpf` (fail-fail pairs per fail) is about 1/4 for thin lines and near 1 for solid blobs.

## 5. Tree ensemble

576 trees (64 rounds x 9 classes) of depth 3, trained with scikit-learn's
`HistGradientBoostingClassifier` (`training/train.py`) and quantized: thresholds are floored
to integers (the features are integers, so `x <= t` and `x <= floor(t)` agree), leaf values
and class start values are rounded to Q.8. Shallower trees are padded to depth 3 by copying
a leaf into every slot below it.

    score[c] = start[c] + sum over trees t with t mod 9 = c of leaf_t(features)

At each node the walk goes right when `feature > threshold`. Scores are 20-bit signed.

## 6. Early-call rule

    top    = class with the highest score (lowest index on ties)
    margin = score[top] - highest other score
    ok     = top != none  and  margin >= margin[top]  and  frac >= min_frac

    if ok:   run_len = run_len + 1 (saturates at 15) if top == run_cls and run_len > 0,
             else run_cls = top, run_len = 1
    else:    run_len = 0

The first time `ok` and `run_len >= max(persist, 1)`, the engine latches the call (class,
number of dies tested). It keeps running to the end of the wafer; the last checkpoint's
`top` is the full-map class.

## 7. Probe-site check

At each checkpoint until a site is flagged, for sites s = 0 .. min(n_sites, 8) - 1 in order:
if site s has tested at least `site_min` dies and some dies were tested by other sites,

    d = RATE(F_s, T_s) - RATE(F - F_s, T - T_s)

and the first site with `d >= site_delta` is latched (site, number of dies tested).

## 8. Checkpoint record

`ndies` (= T), `n_dup`, `n_oor`, `final`, the 39 features, the 9 scores, `top`, `margin`,
`run_cls`, `run_len`, `called`, `call_cls`, `call_ndies`, `site_flag`, `site_id`,
`site_ndies`. They come out packed on `ck_flat` (bit offsets in `vivado/rtl/wpe_defs.vh`). The
testbench writes every record to a file and the regression compares every field.

## 9. Clock cycles

| step | clocks |
|---|---|
| accepted die | 13 (handshake, bounds check, 9 map reads, update, next) |
| duplicate / outside the map | 4 / 3 |
| checkpoint | about 5,400 (5,346 to 5,384 measured on WM-811K wafers): up to 64 ratios (15 clocks each, 3 for the special cases), 576 trees x 8 clocks, top-2 scan, decision, site check |
| wafer start | max(W x H, 40) clocks to clear the map and the zone counters |

With a checkpoint every 16 dies that is about 350 clocks per die (351 to 357 measured,
including the map clear), roughly 285,000 dies per second at 100 MHz.

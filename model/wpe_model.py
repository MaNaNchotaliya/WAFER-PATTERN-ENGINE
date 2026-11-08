"""Bit-exact model of the engine, one die at a time (the reference for the RTL).

Per die: reject it if it lies outside the W x H map or was already tested; otherwise
count its pairs with already-tested neighbours (8-neighbourhood, four directions), add it
to its zone, ring, site and wafer counters, and mark it in the map. A checkpoint runs
after every `chk` accepted dies and after the die flagged `last`: features, tree
ensemble, early-call rule, probe-site check. Every checkpoint produces one record.
"""
from wpe_geom import zone_of
from wpe_gbt import Decider
from wpe_params import CNT_W, FEATURES, MAXD, NCLS, NR, NS, NSITE, ONE, QB, TMIN

CNT_MASK = (1 << CNT_W) - 1    # the engine's 17-bit counters wrap

# neighbour offsets and their direction class: 0 horizontal, 1 vertical, 2 "\" diagonal, 3 "/"
NEIGH = [(-1, 0, 0), (1, 0, 0), (0, -1, 1), (0, 1, 1), (-1, -1, 2), (1, 1, 2), (1, -1, 3), (-1, 1, 3)]


def rate(a, b):
    """Q.12 ratio a/b: 0 when b = 0, 1.0 when a >= b, else floor(a * 4096 / b)."""
    if b == 0:
        return 0
    if a >= b:
        return ONE
    return (a << QB) // b


def compute_features(c, n_total, n_ring):
    """Feature vector from the counter state c (a dict), in FEATURES order."""
    T, F = c["T"], c["F"]
    f = rate(F, T)
    fr = [rate(c["Fr"][r], c["Tr"][r]) for r in range(NR)]
    cov = [rate(c["Tr"][r], n_ring[r]) for r in range(NR)]
    smax, smin, sconc = [], [], []
    maxfz = 0
    for r in range(NR):
        hi, lo, any_ok, maxf = 0, ONE, False, 0
        for s in range(NS):
            z = r * NS + s
            maxf = max(maxf, c["Fz"][z])
            if c["Tz"][z] >= TMIN:
                zr = rate(c["Fz"][z], c["Tz"][z])
                hi, lo, any_ok = max(hi, zr), min(lo, zr), True
        smax.append(hi if any_ok else 0)
        smin.append(lo if any_ok else 0)
        sconc.append(rate(maxf, c["Fr"][r]))
        maxfz = max(maxfz, maxf)
    ff_t, x_t = sum(c["FF"]), sum(c["X"])
    q = rate(2 * ff_t, 2 * ff_t + x_t)
    qk = [rate(2 * c["FF"][k], 2 * c["FF"][k] + c["X"][k]) for k in range(4)]
    v = ([rate(T, n_total), f] + fr + cov + [x - f for x in fr] + smax + smin + sconc
         + [rate(maxfz, F), q, q - f, max(qk), min(qk), max(qk) - min(qk), rate(ff_t, 4 * F)])
    assert len(v) == len(FEATURES)
    return v


class Engine:
    def __init__(self, trees):
        self.trees = trees

    def run(self, cfg, dies):
        """dies: list of (x, y, fail, site); the last one carries the 'last' flag."""
        if not (1 <= cfg.w <= MAXD and 1 <= cfg.h <= MAXD):
            raise ValueError("map width and height must be 1..256")
        tested = bytearray(MAXD * MAXD)
        failbit = bytearray(MAXD * MAXD)
        c = dict(T=0, F=0, Tz=[0] * (NR * NS), Fz=[0] * (NR * NS), Tr=[0] * NR, Fr=[0] * NR,
                 FF=[0] * 4, X=[0] * 4, Ts=[0] * NSITE, Fs=[0] * NSITE)
        n_dup = n_oor = ck_cnt = 0
        dec = Decider(cfg)
        site_flag = site_id = site_ndies = 0
        recs = []
        for i, (x, y, fl, site) in enumerate(dies):
            last = i == len(dies) - 1
            periodic = False
            if x >= cfg.w or y >= cfg.h:
                n_oor += 1
            elif tested[y * MAXD + x]:
                n_dup += 1
            else:
                for dx, dy, k in NEIGH:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < cfg.w and 0 <= ny < cfg.h and tested[ny * MAXD + nx]:
                        nf = failbit[ny * MAXD + nx]
                        if fl and nf:
                            c["FF"][k] += 1
                        elif fl != nf:
                            c["X"][k] += 1
                ring, sect = zone_of(x, y, cfg.cx2, cfg.cy2, cfg.wx, cfg.wy, cfg.thr)
                z = ring * NS + sect
                c["Tz"][z] += 1
                c["Fz"][z] += fl
                c["Tr"][ring] += 1
                c["Fr"][ring] += fl
                c["T"] += 1
                c["F"] += fl
                c["Ts"][site] += 1
                c["Fs"][site] += fl
                tested[y * MAXD + x] = 1
                failbit[y * MAXD + x] = fl
                if cfg.chk > 0:
                    ck_cnt += 1
                    if ck_cnt == cfg.chk:
                        ck_cnt = 0
                        periodic = True
            if not (periodic or last):
                continue
            # ---------------- checkpoint
            feats = compute_features(c, cfg.n_total, cfg.n_ring)
            scores = self.trees.scores(feats)
            top, margin = dec.step(feats, scores, c["T"])
            if not site_flag and cfg.n_sites > 0:
                for s in range(min(cfg.n_sites, NSITE)):
                    ts, fs = c["Ts"][s], c["Fs"][s]
                    if ts >= cfg.site_min and c["T"] > ts:
                        d = rate(fs, ts) - rate(c["F"] - fs, c["T"] - ts)
                        if d >= cfg.site_delta:
                            site_flag, site_id, site_ndies = 1, s, c["T"]
                            break
            r = dict(ndies=c["T"], n_dup=n_dup & CNT_MASK, n_oor=n_oor & CNT_MASK, final=int(last), top=top, margin=margin,
                     run_cls=dec.run_cls, run_len=dec.run_len, called=dec.called, call_cls=dec.call_cls,
                     call_ndies=dec.call_ndies, site_flag=site_flag, site_id=site_id, site_ndies=site_ndies)
            for n, v in zip(FEATURES, feats):
                r[f"f_{n}"] = v
            for k in range(NCLS):
                r[f"s{k}"] = scores[k]
            recs.append(r)
        return recs

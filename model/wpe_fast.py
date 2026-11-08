"""Vectorized model: the same integer features and decisions as wpe_model.py, computed
with cumulative sums over the whole die stream instead of one die at a time.

Used for training and for the large evaluations. It is written independently of the
per-die model, and model/selftest_models.py checks that both give identical records.
"""
import numpy as np

from wpe_gbt import Decider
from wpe_geom import zone_maps
from wpe_params import CNT_W, FEATURES, MAXD, NCLS, NR, NS, NSITE, NZ, ONE, QB, TMIN

CNT_MASK = (1 << CNT_W) - 1

# forward offsets: each unordered neighbour pair once; direction class = list index
OFFS = [(1, 0), (0, 1), (1, 1), (-1, 1)]


def RATE(a, b):
    a = np.asarray(a, np.int64)
    b = np.asarray(b, np.int64)
    q = np.where(a >= b, ONE, (a << QB) // np.maximum(b, 1))
    return np.where(b == 0, 0, q)


def stream_series(cfg, dies):
    """Cumulative counters indexed by the number of accepted dies, plus checkpoint list."""
    d = np.asarray(dies, np.int64).reshape(-1, 4)
    x, y, fl, site = d[:, 0], d[:, 1], d[:, 2], d[:, 3]
    E = len(d)
    inr = (x < cfg.w) & (y < cfg.h)
    acc = np.zeros(E, bool)
    ii = np.nonzero(inr)[0]
    if len(ii):
        _, first = np.unique(y[ii] * MAXD + x[ii], return_index=True)
        acc[ii[first]] = True
    rank = np.cumsum(acc)                          # accepted dies after each event
    n_dup = np.cumsum(inr & ~acc)
    n_oor = np.cumsum(~inr)
    Tf = int(rank[-1]) if E else 0

    ring_m, sect_m = zone_maps(cfg)
    ax, ay, af, asite = x[acc], y[acc], fl[acc], site[acc]
    r = np.arange(1, Tf + 1)
    zone = ring_m[ay, ax] * NS + sect_m[ay, ax]
    Tz = np.zeros((Tf + 1, NZ), np.int64)
    Fz = np.zeros((Tf + 1, NZ), np.int64)
    Tz[r, zone] = 1
    Fz[r, zone] = af
    Ts = np.zeros((Tf + 1, NSITE), np.int64)
    Fs = np.zeros((Tf + 1, NSITE), np.int64)
    Ts[r, asite] = 1
    Fs[r, asite] = af
    # neighbour pairs: a pair is counted when its second die is accepted
    rk = np.zeros((cfg.h, cfg.w), np.int64)
    fm = np.zeros((cfg.h, cfg.w), np.int64)
    rk[ay, ax] = r
    fm[ay, ax] = af
    FF = np.zeros((Tf + 1, 4), np.int64)
    X = np.zeros((Tf + 1, 4), np.int64)
    H, W = cfg.h, cfg.w
    for k, (dx, dy) in enumerate(OFFS):
        xa0, xa1 = max(0, -dx), W - max(0, dx)
        ra = rk[0:H - dy, xa0:xa1]
        rb = rk[dy:H, xa0 + dx:xa1 + dx]
        both = (ra > 0) & (rb > 0)
        t = np.maximum(ra[both], rb[both])
        fa = fm[0:H - dy, xa0:xa1][both]
        fb = fm[dy:H, xa0 + dx:xa1 + dx][both]
        FF[:, k] = np.bincount(t, weights=fa & fb, minlength=Tf + 1)
        X[:, k] = np.bincount(t, weights=fa ^ fb, minlength=Tf + 1)
    S = dict(Tz=Tz, Fz=Fz, Ts=Ts, Fs=Fs, FF=FF, X=X)
    for k in S:
        np.cumsum(S[k], axis=0, out=S[k])
    # checkpoints: after every chk-th accepted die, and at the last event
    ev = set()
    if cfg.chk > 0:
        ev = set(np.nonzero(acc & (rank % cfg.chk == 0))[0].tolist())
    if E:
        ev.add(E - 1)
    ev = sorted(ev)
    S["ck_event"] = np.array(ev, np.int64)
    S["ck_T"] = rank[ev] if E else np.zeros(0, np.int64)
    S["ck_dup"] = n_dup[ev] if E else np.zeros(0, np.int64)
    S["ck_oor"] = n_oor[ev] if E else np.zeros(0, np.int64)
    S["ck_final"] = (np.array(ev) == E - 1).astype(np.int64)
    S["Tf"] = Tf
    return S


def features_at(S, cfg, Tl):
    """Feature matrix (len(Tl), NF) after Tl accepted dies."""
    Tl = np.asarray(Tl, np.int64)
    n = len(Tl)
    Tz = S["Tz"][Tl].reshape(n, NR, NS)
    Fz = S["Fz"][Tl].reshape(n, NR, NS)
    T, F = Tz.sum((1, 2)), Fz.sum((1, 2))
    Tr, Fr = Tz.sum(2), Fz.sum(2)
    f = RATE(F, T)
    fr = RATE(Fr, Tr)
    cov = RATE(Tr, np.broadcast_to(np.asarray(cfg.n_ring, np.int64), Tr.shape))
    zr = RATE(Fz, Tz)
    ok = Tz >= TMIN
    anyok = ok.any(2)
    smax = np.where(anyok, np.where(ok, zr, -1).max(2), 0)
    smin = np.where(anyok, np.where(ok, zr, ONE + 1).min(2), 0)
    sconc = RATE(Fz.max(2), Fr)
    zconc = RATE(Fz.reshape(n, -1).max(1), F)
    FF, X = S["FF"][Tl], S["X"][Tl]
    fft, xt = FF.sum(1), X.sum(1)
    q = RATE(2 * fft, 2 * fft + xt)
    qk = RATE(2 * FF, 2 * FF + X)
    cols = ([RATE(T, np.full(n, cfg.n_total)), f] + [fr[:, i] for i in range(NR)]
            + [cov[:, i] for i in range(NR)] + [fr[:, i] - f for i in range(NR)]
            + [smax[:, i] for i in range(NR)] + [smin[:, i] for i in range(NR)]
            + [sconc[:, i] for i in range(NR)]
            + [zconc, q, q - f, qk.max(1), qk.min(1), qk.max(1) - qk.min(1), RATE(fft, 4 * F)])
    return np.stack(cols, 1)


def site_check(S, cfg, T):
    """First flagged site at T accepted dies (or None), same rule as the engine."""
    if cfg.n_sites <= 0:
        return None
    Ts, Fs = S["Ts"][T], S["Fs"][T]
    Ft = int(S["Fz"][T].sum())
    for s in range(min(cfg.n_sites, NSITE)):
        ts, fs = int(Ts[s]), int(Fs[s])
        if ts >= cfg.site_min and T > ts:
            if int(RATE(fs, ts)) - int(RATE(Ft - fs, T - ts)) >= cfg.site_delta:
                return s
    return None


def decide(cfg, S, feats, scores):
    """Run the early-call rule and the site check over the checkpoint list."""
    dec = Decider(cfg)
    site_flag = site_id = site_ndies = 0
    out = []
    for j in range(len(S["ck_T"])):
        T = int(S["ck_T"][j])
        top, margin = dec.step(feats[j], scores[j], T)
        if not site_flag:
            s = site_check(S, cfg, T)
            if s is not None:
                site_flag, site_id, site_ndies = 1, s, T
        out.append(dict(ndies=T, n_dup=int(S["ck_dup"][j]) & CNT_MASK, n_oor=int(S["ck_oor"][j]) & CNT_MASK,
                        final=int(S["ck_final"][j]), top=top, margin=margin, run_cls=dec.run_cls,
                        run_len=dec.run_len, called=dec.called, call_cls=dec.call_cls,
                        call_ndies=dec.call_ndies, site_flag=site_flag, site_id=site_id,
                        site_ndies=site_ndies))
    return out


def run_fast(cfg, dies, trees):
    """Same records as wpe_model.Engine.run."""
    S = stream_series(cfg, dies)
    feats = features_at(S, cfg, S["ck_T"])
    scores = trees.scores_vec(feats)
    recs = decide(cfg, S, [list(map(int, f)) for f in feats], [list(map(int, s)) for s in scores])
    for r, fv, sv in zip(recs, feats, scores):
        for nm, v in zip(FEATURES, fv):
            r[f"f_{nm}"] = int(v)
        for k in range(NCLS):
            r[f"s{k}"] = int(sv[k])
    return recs

"""Wafer input and engine runs for the desktop app. No GUI code in here.

Two kinds of input:
  map       one row per line, 0 = no die, 1 = pass, 2 = fail. Digits can be written
            together ("0011121100") or split by spaces or commas. This is how WM-811K
            stores its maps and how the samples are written.
  die list  one die per line in probe order: x, y, fail and optionally the probe site.
            A header line (x,y,fail,site) is allowed. fail can be 0/1, P/F or pass/fail.
            The .die files the testbench reads (x y fail site last) work too.
Lines starting with # are comments. "# label: Center" gives the known class, "# title: ..."
the name shown in the app, "# format: map" or "# format: dies" skips the guessing.
"""
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

if getattr(sys, "frozen", False):            # inside the PyInstaller exe
    BASE = Path(sys._MEIPASS)
    MODEL = BASE / "model"
else:
    BASE = Path(__file__).resolve().parent
    MODEL = BASE.parent / "model"
SAMPLES = BASE / "samples"
sys.path.insert(0, str(MODEL))

from wpe_fast import run_fast                                   # noqa: E402
from wpe_gbt import Trees                                       # noqa: E402
from wpe_geom import geometry, inject_site_fault, multisite, serpentine  # noqa: E402
from wpe_params import CLASSES, LEAF_Q, MAXD, NCLS, Config      # noqa: E402

TRAINED = json.loads((MODEL / "trained.json").read_text())
TREES = Trees.load(MODEL / "trees.npz")
FAULT_SEED = 1                 # the damaged site hits the same dies every time
MAX_FILE = 10 * 1024 * 1024    # a 256 x 256 die list is about 1 MB

ORDERS = {"serpentine": "Serpentine, 1 site",
          "multisite": "2 x 2 sites",
          "file": "Order in the input"}

FAIL_WORDS = {"0": 0, "p": 0, "pass": 0, "1": 1, "f": 1, "fail": 1}
SPLIT = re.compile(r"[,;\s]+")


class WaferFileError(Exception):
    def __init__(self, msg, line=None):
        super().__init__(msg)
        self.line = line              # 1-based line of the input, if there is one


@dataclass
class Wafer:
    title: str
    kind: str                         # "map" or "dies"
    valid: np.ndarray                 # h x w, True where there is a die
    fail: np.ndarray                  # h x w, first result of each die
    dies: list = None                 # (x, y, fail, site) in input order, die lists only
    label: str = None
    comments: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def size(self):
        return self.valid.shape[1], self.valid.shape[0]

    @property
    def n_dies(self):
        return int(self.valid.sum())

    @property
    def n_fail(self):
        return int((self.fail & self.valid).sum())

    @property
    def n_sites(self):
        return len({d[3] for d in self.dies}) if self.dies else 1


def read_text(path):
    path = Path(path)
    size = path.stat().st_size
    if size > MAX_FILE:
        raise WaferFileError(f"{path.name} is {size / 2**20:.0f} MB. A wafer file is at most a few hundred KB, "
                             f"so this is probably not one.")
    raw = path.read_bytes()
    if b"\0" in raw[:4096]:
        raise WaferFileError(f"{path.name} is not a text file. Wafer files are plain text (.txt, .csv, .die).")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def parse(text, title="Untitled"):
    """input text -> Wafer, or WaferFileError with the line number"""
    name, label, kind, comments, rows = None, None, None, [], []
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            comments.append(s)
            key, colon, val = s.lstrip("#").partition(":")
            key = key.strip().lower()
            if colon and key == "label":
                label = val.strip() or None
            elif colon and key == "title":
                name = val.strip() or None
            elif colon and key == "format":
                kind = val.strip().lower()
            continue
        rows.append((n, s))
    if not rows:
        raise WaferFileError("There are no dies yet. Type a map (rows of 0, 1, 2) or a die list (x, y, fail).")
    if kind is None:
        kind = _guess_kind(rows)
    warnings = []
    if kind in ("die", "dies"):
        kind = "dies"
        valid, fail, dies = _read_dies(rows)
        retests = len(dies) - int(valid.sum())
        if retests:
            warnings.append(f"{retests} {'die is' if retests == 1 else 'dies are'} tested more than once. "
                            f"The engine keeps the first result.")
    elif kind == "map":
        valid, fail = _read_map(rows)
        dies = None
    else:
        raise WaferFileError(f'"# format: {kind}" is not a format I know. Use "map" or "dies".')
    if not valid.any():
        raise WaferFileError("The map has no dies, it is all zeros.")
    if not (fail & valid).any():
        warnings.append("No die fails. Only 1 of the 172,950 WM-811K wafers looks like that, so the "
                        "engine's answer for this input doesn't mean much.")
    if label is not None and label not in CLASSES:
        warnings.append(f'Label "{label}" is not one of the 9 classes, so there is nothing to compare with.')
        label = None
    return Wafer(name or title, kind, valid, fail, dies, label, comments, warnings)


def load(path):
    path = Path(path)
    return parse(read_text(path), path.stem)


def _tokens(s):
    t = [x for x in SPLIT.split(s) if x]
    if any(len(x) > 1 for x in t):
        return list("".join(t))       # digits written together (a stray space doesn't matter)
    return t


def _guess_kind(rows):
    """map or die list. A header means a die list. Rows like "0112210" mean a map (die lists
    always have separators), otherwise more than 5 values or only 0/1/2 per row means a map."""
    if rows[0][1][0].isalpha():
        return "dies"
    blocks = sum(1 for _, s in rows if len([x for x in SPLIT.split(s) if x]) == 1 and len(s) > 1)
    if blocks * 2 >= len(rows):
        return "map"
    counts = {}
    small = total = 0
    for _, s in rows:
        t = [x for x in SPLIT.split(s) if x]
        counts[len(t)] = counts.get(len(t), 0) + 1
        small += sum(x in ("0", "1", "2") for x in t)
        total += len(t)
    width = max(counts, key=counts.get)
    if width > 5:
        return "map"
    return "map" if len(rows) >= 3 and small >= 0.95 * total else "dies"


def _read_map(rows):
    grid = []
    for n, s in rows:
        t = _tokens(s)
        bad = [x for x in t if x not in ("0", "1", "2")]
        if bad:
            raise WaferFileError(f'Line {n}: "{bad[0]}" is not allowed in a map. Use 0 (no die), 1 (pass) '
                                 f'or 2 (fail).', n)
        if grid and len(t) != len(grid[0]):
            raise WaferFileError(f"Line {n} has {len(t)} dies across, the rows above have {len(grid[0])}. "
                                 f"Every row of a map needs the same length.", n)
        grid.append([int(x) for x in t])
    m = np.array(grid, np.int64)
    if m.shape[0] > MAXD or m.shape[1] > MAXD:
        raise WaferFileError(f"The map is {m.shape[1]} x {m.shape[0]}. The engine takes up to {MAXD} x {MAXD}.",
                             rows[min(len(rows) - 1, MAXD)][0])
    return m > 0, m == 2


def _read_dies(rows):
    cols = {"x": 0, "y": 1, "fail": 2, "site": 3}
    first_n, first = rows[0]
    t0 = [x for x in SPLIT.split(first) if x]
    if t0 and not t0[0].lstrip("-").isdigit():
        names = [x.lower() for x in t0]
        alias = {"pf": "fail", "result": "fail", "f": "fail", "pass_fail": "fail", "probe_site": "site"}
        names = [alias.get(x, x) for x in names]
        missing = [c for c in ("x", "y", "fail") if c not in names]
        if missing:
            raise WaferFileError(f"Line {first_n}: the header has no {' or '.join(missing)} column. "
                                 f"A die list needs x, y and fail (site is optional).", first_n)
        cols = {k: names.index(k) for k in ("x", "y", "fail", "site") if k in names}
        rows = rows[1:]
    need = max(cols["x"], cols["y"], cols["fail"]) + 1
    dies = []
    for n, s in rows:
        t = [x for x in SPLIT.split(s) if x]
        if len(t) < need:
            raise WaferFileError(f'Line {n}: "{s}" has {len(t)} value{"s" if len(t) != 1 else ""}. '
                                 f"Each die needs x, y and fail, for example 12, 4, 1.", n)
        try:
            x, y = int(t[cols["x"]]), int(t[cols["y"]])
        except ValueError:
            raise WaferFileError(f'Line {n}: x and y have to be whole numbers ("{s}").', n) from None
        site = 0
        if "site" in cols and len(t) > cols["site"]:
            try:
                site = int(t[cols["site"]])
            except ValueError:
                raise WaferFileError(f'Line {n}: the probe site has to be a whole number, not '
                                     f'"{t[cols["site"]]}".', n) from None
        fl = FAIL_WORDS.get(t[cols["fail"]].lower())
        if fl is None:
            raise WaferFileError(f'Line {n}: fail has to be 0 or 1 (or P/F, pass/fail), not '
                                 f'"{t[cols["fail"]]}".', n)
        if x < 0 or y < 0:
            raise WaferFileError(f"Line {n}: die ({x}, {y}) has a negative coordinate.", n)
        if x >= MAXD or y >= MAXD:
            raise WaferFileError(f"Line {n}: die ({x}, {y}) is outside the {MAXD} x {MAXD} map the engine "
                                 f"takes.", n)
        if not 0 <= site < 8:
            raise WaferFileError(f"Line {n}: probe site {site}. The engine tracks sites 0 to 7.", n)
        dies.append((x, y, fl, site))
    if not dies:
        raise WaferFileError("The header is there but there are no dies under it.", first_n)
    w = max(d[0] for d in dies) + 1
    h = max(d[1] for d in dies) + 1
    valid = np.zeros((h, w), bool)
    fail = np.zeros((h, w), bool)
    for x, y, fl, _ in dies:
        if not valid[y, x]:           # the engine keeps the first result of a die
            valid[y, x] = True
            fail[y, x] = bool(fl)
    return valid, fail, dies


def map_text(valid, fail, comments=()):
    """map back to text, the way the samples are written"""
    rows = np.where(valid, np.where(fail, "2", "1"), "0")
    return "\n".join(list(comments) + ["".join(r) for r in rows]) + "\n"


def blank_wafer(across, fail_share=0.08, seed=None):
    """round wafer with some random fails. A normal WM-811K wafer has about 10%, only 1 of
    172,950 has none at all."""
    c = (across - 1) / 2
    yy, xx = np.mgrid[0:across, 0:across]
    valid = (xx - c) ** 2 + (yy - c) ** 2 <= (across / 2) ** 2
    fail = valid & (np.random.default_rng(seed).random(valid.shape) < fail_share)
    note = [f"# title: Blank wafer, {across} dies across",
            f"# {fail_share * 100:.0f}% random fails, paint a pattern on top: left button paints, right button undoes"]
    return map_text(valid, fail, note)


def samples():
    """(title, path) of the sample wafers, in class order"""
    out = []
    for p in SAMPLES.glob("*.txt"):
        title, label = p.stem, None
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.startswith("#"):
                    break
                key, _, val = line.lstrip("#").partition(":")
                if key.strip() == "title":
                    title = val.strip()
                elif key.strip() == "label":
                    label = val.strip()
        k = CLASSES.index(label) if label in CLASSES else NCLS
        out.append((k, "big map" in title, title, p))
    return [(t, p) for *_, t, p in sorted(out)]


def engine_config(valid):
    t = TRAINED
    return geometry(valid, Config(chk=t["chk"], persist=t["persist"], min_frac=t["min_frac"],
                                  margin=list(t["margin"]), site_min=t["site_min"],
                                  site_delta=t["site_delta"]))


def zone_radii2(cfg, valid):
    """squared radius of the 4 ring boundaries and the wafer edge, in the engine's scaled units"""
    ys, xs = np.nonzero(valid)
    u = (2 * xs.astype(np.int64) - cfg.cx2) * cfg.wx
    v = (2 * ys.astype(np.int64) - cfg.cy2) * cfg.wy
    rmax2 = int((u * u + v * v).max())
    return [int(t) for t in cfg.thr] + [rmax2]


@dataclass
class Run:
    wafer: Wafer
    order: str
    bad_site: int
    bad_share: float
    cfg: Config
    dies: np.ndarray                  # (events, 4): x, y, fail, site as the engine sees them
    first: np.ndarray                 # True for the first test of a die (retests are ignored)
    recs: list                        # one checkpoint record each, see wpe_params.CK_KEYS
    ck_event: np.ndarray              # die event that triggered each checkpoint

    @property
    def n_events(self):
        return len(self.dies)

    @property
    def n_total(self):
        return self.cfg.n_total

    def ck_before(self, pos):
        """last checkpoint once `pos` die events have gone in, or -1"""
        return int(np.searchsorted(self.ck_event, pos, side="left")) - 1

    def first_called(self):
        for j, r in enumerate(self.recs):
            if r["called"]:
                return j
        return None

    def first_site_flag(self):
        for j, r in enumerate(self.recs):
            if r["site_flag"]:
                return j
        return None

    def events_for_dies(self, n):
        """die events it takes to have n dies tested"""
        if n <= 0:
            return 0
        cum = np.cumsum(self.first)
        return int(min(np.searchsorted(cum, n, side="left") + 1, self.n_events))


def run(wafer, order="serpentine", bad_site=None, bad_share=0.5):
    if order == "file":
        if wafer.dies is None:
            raise ValueError("a map has no probe order of its own")
        dies = list(wafer.dies)
    else:
        seq = serpentine(wafer.valid) if order == "serpentine" else multisite(wafer.valid)
        dies = [(x, y, int(wafer.fail[y, x]), s) for x, y, s in seq]
    if bad_site is not None:
        dies = inject_site_fault(dies, bad_site, bad_share, np.random.default_rng(FAULT_SEED))
    cfg = engine_config(wafer.valid)
    sites = {d[3] for d in dies}
    if order == "multisite":
        cfg.n_sites = 4
    elif len(sites) > 1:
        cfg.n_sites = max(sites) + 1
    recs = run_fast(cfg, dies, TREES)

    d = np.asarray(dies, np.int64).reshape(-1, 4)
    first = np.zeros(len(d), bool)
    _, idx = np.unique(d[:, 1] * MAXD + d[:, 0], return_index=True)
    first[idx] = True
    count = np.cumsum(first)
    ck = set(np.nonzero(first & (count % cfg.chk == 0))[0].tolist()) | {len(d) - 1}
    ck_event = np.array(sorted(ck), np.int64)
    if len(ck_event) != len(recs):
        raise RuntimeError("checkpoint count does not match the engine records")
    return Run(wafer, order, bad_site, bad_share if bad_site is not None else 0.0, cfg, d, first, recs,
               ck_event)


def probabilities(rec):
    """softmax of the 9 integer scores (they are Q.8 logits). Only for display: the engine
    itself just compares the top two scores."""
    s = np.array([rec[f"s{k}"] for k in range(NCLS)], np.float64) / (1 << LEAF_Q)
    e = np.exp(s - s.max())
    return e / e.sum()


def share(n, total):
    return n / total if total else 0.0


# exports
RESULT_HEAD = (["dies_tested", "share_tested", "on_top", "margin", "margin_needed", "in_a_row",
                "called", "call_class", "call_at_dies", "site_flag", "site", "site_at_dies"]
               + [f"score_{c}" for c in CLASSES])


def save_checkpoints(r_, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(RESULT_HEAD)
        for r in r_.recs:
            w.writerow([r["ndies"], f"{share(r['ndies'], r_.n_total):.4f}", CLASSES[r["top"]], r["margin"],
                        r_.cfg.margin[r["top"]] if r["top"] else "", r["run_len"], r["called"],
                        CLASSES[r["call_cls"]] if r["called"] else "", r["call_ndies"] if r["called"] else "",
                        r["site_flag"], r["site_id"] if r["site_flag"] else "",
                        r["site_ndies"] if r["site_flag"] else ""]
                       + [r[f"s{k}"] for k in range(NCLS)])


BATCH_HEAD = ["file", "label", "dies", "probe_order", "early_call", "call_at_dies", "call_at_share",
              "full_wafer", "site_flag", "site", "error"]


def run_folder(paths, order, progress=None):
    """every file in `paths`: maps in `order`, die lists in their own order"""
    rows = []
    for i, p in enumerate(paths):
        try:
            w = load(p)
            o = "file" if w.dies is not None else order
            r = run(w, o)
            last = r.recs[-1]
            j = r.first_called()
            call = r.recs[j] if j is not None else None
            rows.append([p.name, w.label or "", r.n_total, ORDERS[o],
                         CLASSES[call["call_cls"]] if call else "",
                         call["call_ndies"] if call else "",
                         f"{share(call['call_ndies'], r.n_total):.4f}" if call else "",
                         CLASSES[last["top"]], last["site_flag"], last["site_id"] if last["site_flag"] else "",
                         ""])
        except (WaferFileError, OSError, ValueError) as e:
            rows.append([p.name, "", "", "", "", "", "", "", "", "", str(e)])
        if progress:
            progress(i + 1, len(paths))
    return rows


def save_rows(rows, head, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(rows)


def wafer_files_in(folder):
    exts = {".txt", ".csv", ".die", ".map"}
    return sorted(p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in exts)

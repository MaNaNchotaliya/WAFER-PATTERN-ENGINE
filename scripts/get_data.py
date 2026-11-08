"""Download the WM-811K wafer-map dataset and convert the labeled maps to a compact .npz.

WM-811K (LSWMD.pkl) is a pandas DataFrame pickled with Python 2. Unpickling runs code
named inside the file, so this script never calls pickle.load on it directly: a
restricted unpickler builds only numpy arrays and inert placeholder objects, and stops
on any other global. No pandas install is needed.

Usage:
  python scripts/get_data.py                 # download (157 MB) if needed, then convert
  python scripts/get_data.py --cache D:/wm   # keep the downloaded zip somewhere else
Output: data/wm811k_labeled.npz (labeled maps only, about 25 MB)
"""
import argparse
import hashlib
import pickle
import sys
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import numpy._core.multiarray as _ma

ROOT = Path(__file__).resolve().parents[1]
URL = "https://www.kaggle.com/api/v1/datasets/download/qingyi/wm811k-wafer-map"
ZIP_SIZE = 156_686_283
ZIP_SHA256 = "0e4bd4316a110a04e475a88071f5e1c7fe4303b12ee53c851d2cae4d995a93e2"
OUT = ROOT / "data" / "wm811k_labeled.npz"

# class index order used everywhere in this project (0 = no pattern)
CLASSES = ["none", "Center", "Donut", "Edge-Loc", "Edge-Ring", "Loc", "Near-full", "Random", "Scratch"]
SPLITS = ["Training", "Test"]


# ---------------------------------------------------------------- safe unpickling
class _Inert:
    """Placeholder for a pandas object: keeps constructor arguments and state, runs nothing."""
    _qual = "?"

    def __init__(self, *args, **kw):
        self.args = args
        self.kw = kw

    def __setstate__(self, state):
        self.state = state


_INERT = {}


def _inert(module, name):
    key = f"{module}.{name}"
    if key not in _INERT:
        _INERT[key] = type(name, (_Inert,), {"_qual": key})
    return _INERT[key]


# exactly the globals LSWMD.pkl uses; anything else stops the load
_NUMPY = {
    ("numpy.core.multiarray", "_reconstruct"): _ma._reconstruct,
    ("numpy.core.multiarray", "scalar"): _ma.scalar,
    ("numpy", "ndarray"): np.ndarray,
    ("numpy", "dtype"): np.dtype,
    ("__builtin__", "slice"): slice,
}
_PANDAS = {
    ("pandas.core.frame", "DataFrame"),
    ("pandas.core.internals", "BlockManager"),
    ("pandas.indexes.base", "_new_Index"),
    ("pandas.indexes.base", "Index"),
    ("pandas.indexes.range", "RangeIndex"),
}


class SafeUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) in _NUMPY:
            return _NUMPY[(module, name)]
        if (module, name) in _PANDAS:
            return _inert(module, name)
        raise pickle.UnpicklingError(f"refusing global {module}.{name}")


def load_columns(zip_path):
    """Return {column name: 1-D object/float array} from the pickled DataFrame."""
    with zipfile.ZipFile(zip_path) as z, z.open("LSWMD.pkl") as f:
        frame = SafeUnpickler(f, encoding="latin1").load()
    mgr = frame.state["_data"]
    extra = mgr.state[3]["0.14.1"]          # pandas 0.14.1+ BlockManager state layout
    col_index = extra["axes"][0]            # _new_Index(Index, {"data": names, ...})
    names = [str(n) for n in col_index.args[1]["data"]]
    cols = {}
    for blk in extra["blocks"]:
        locs = blk["mgr_locs"]
        idx = range(locs.start, locs.stop, locs.step or 1) if isinstance(locs, slice) else list(locs)
        for k, i in enumerate(idx):
            cols[names[i]] = blk["values"][k]
    return cols


# ---------------------------------------------------------------- download
def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(zip_path):
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = zip_path.with_suffix(".part")
    print(f"downloading WM-811K (157 MB) from Kaggle to {zip_path} ...")
    with urllib.request.urlopen(URL, timeout=60) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(zip_path)


def label_of(x):
    """failureType / trianTestLabel cells hold [['name']] when labeled, [] otherwise."""
    a = np.asarray(x)
    return str(a.ravel()[0]) if a.size else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(Path.home() / "datasets" / "wm811k"),
                    help="folder for the downloaded zip (outside any synced folder)")
    args = ap.parse_args()
    zip_path = Path(args.cache) / "archive.zip"
    if not zip_path.exists():
        download(zip_path)
    if zip_path.stat().st_size != ZIP_SIZE or sha256(zip_path) != ZIP_SHA256:
        print("WARNING: archive.zip differs from the copy this project was built with")

    print("reading LSWMD.pkl with the restricted unpickler (about 1 minute) ...")
    cols = load_columns(zip_path)
    wmap, ftype, split = cols["waferMap"], cols["failureType"], cols["trianTestLabel"]
    n_all = len(wmap)

    keep, lab, spl = [], [], []
    for i in range(n_all):
        name = label_of(ftype[i])
        if not name:
            continue
        keep.append(i)
        lab.append(CLASSES.index(name))
        spl.append(SPLITS.index(label_of(split[i])))
    keep = np.array(keep, dtype=np.int64)

    h = np.array([wmap[i].shape[0] for i in keep], dtype=np.int16)
    w = np.array([wmap[i].shape[1] for i in keep], dtype=np.int16)
    sizes = h.astype(np.int64) * w
    offset = np.zeros(len(keep) + 1, dtype=np.int64)
    np.cumsum(sizes, out=offset[1:])
    maps = np.empty(offset[-1], dtype=np.uint8)
    for k, i in enumerate(keep):
        m = np.asarray(wmap[i])
        if m.min() < 0 or m.max() > 2:
            raise SystemExit(f"row {i}: unexpected map values")
        maps[offset[k]:offset[k + 1]] = m.astype(np.uint8).ravel()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        OUT, maps=maps, offset=offset, h=h, w=w,
        label=np.array(lab, dtype=np.int8), split=np.array(spl, dtype=np.int8),
        row=keep.astype(np.int32),
        lot=np.array([str(cols["lotName"][i]) for i in keep]),
        wafer_index=np.array([int(cols["waferIndex"][i]) for i in keep], dtype=np.int16),
        classes=np.array(CLASSES), splits=np.array(SPLITS))
    print(f"{n_all} maps in the file, {len(keep)} labeled -> {OUT} "
          f"({OUT.stat().st_size / 1e6:.1f} MB)")
    lab = np.array(lab)
    spl = np.array(spl)
    for c, name in enumerate(CLASSES):
        print(f"  {name:<10} train {np.sum((lab == c) & (spl == 0)):>7}  test {np.sum((lab == c) & (spl == 1)):>7}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

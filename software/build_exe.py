"""Builds the Windows app as one file: build/software/dist/WaferPatternEngine.exe

Needs PyInstaller and Pillow (pip install pyinstaller pillow). The exe carries Python,
numpy, the trained trees and the sample wafers, so it runs on a PC without Python.

usage: python software/build_exe.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "software"


def make_icon(path):
    """small wafer with an edge ring, same colours as the app"""
    from PIL import Image, ImageDraw
    n = 256
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cells = 15
    step = n / cells
    c = (cells - 1) / 2
    for y in range(cells):
        for x in range(cells):
            r = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            if r > 7.3:
                continue
            color = (31, 38, 51, 255) if r > 5.6 else (168, 197, 225, 255)
            x0, y0 = x * step + 1, y * step + 1
            d.rounded_rectangle((x0, y0, x0 + step - 3, y0 + step - 3), radius=3, fill=color)
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ico = OUT / "wafer.ico"
    make_icon(ico)
    sep = ";" if sys.platform == "win32" else ":"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
           "--name", "WaferPatternEngine", "--icon", str(ico),
           "--paths", str(ROOT / "model"),
           "--add-data", f"{ROOT / 'model' / 'trees.npz'}{sep}model",
           "--add-data", f"{ROOT / 'model' / 'trained.json'}{sep}model",
           "--add-data", f"{ROOT / 'software' / 'samples'}{sep}samples",
           "--distpath", str(OUT / "dist"), "--workpath", str(OUT / "work"), "--specpath", str(OUT)]
    for mod in ("matplotlib", "scipy", "sklearn", "PIL", "pandas", "IPython", "pytest"):
        cmd += ["--exclude-module", mod]
    cmd.append(str(ROOT / "software" / "wafer_app.py"))
    subprocess.run(cmd, check=True)
    exe = OUT / "dist" / "WaferPatternEngine.exe"
    print(f"\n{exe} ({exe.stat().st_size / 2**20:.1f} MB)")


if __name__ == "__main__":
    main()

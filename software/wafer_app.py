"""Desktop app for the wafer pattern engine.

Type, paste or open a wafer map on the left, the right side shows what the engine does with
it. Runs the Python model from model/, same records as the Verilog in vivado/rtl/.

usage: python software/wafer_app.py [file] [--sample TEXT] [--order serpentine|multisite|file]
                               [--bad-site N] [--bad-share PERCENT] [--at SHARE] [--zones] [--play]
"""
import argparse
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np

import wafer_files as wf
from wpe_geom import zone_of
from wpe_params import CLASSES, RUN_MAX

VERSION = "1.0"
REPO = "https://github.com/MaNaNchotaliya/WAFER_PATTERN_ENGINE"

BG = "#f3f2ee"
CARD = "#ffffff"
FIELD = "#fbfaf7"
INK = "#1c1c1a"
INK2 = "#57564f"
INK3 = "#8e8d86"
LINE = "#dcdad2"
TRACK = "#eeece6"
ACCENT = "#2a78d6"
DIE_NEW = "#e2e0d9"            # not probed yet
DIE_PASS = "#a8c5e1"
DIE_FAIL = "#1f2633"
ZONE = "#f07f13"
SASH = "#d3d0c7"               # divider colour under the mouse
GOOD = "#0c8a0c"
BAD = "#d03b3b"
# message styles: text colour, background, sign
MSG = {"error": (BAD, "#fbeaea", "✕"),
       "note": ("#a06d00", "#fdf3dc", "!"),
       "ok": (GOOD, "#eaf5ea", "✓"),
       "info": (ACCENT, "#eaf1fb", "i")}
# one colour per class, none is grey
CLASS_COLOR = ["#bdbbb3", "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]

HOW = """How the engine works

A prober tests a wafer one die at a time. The engine gets each result (x, y, pass or fail, probe site) and keeps a few running counts: fails per ring and per sector of the wafer, and how often a failing die has failing neighbours, per direction.

Every 16 dies it turns those counts into 39 numbers and runs them through 576 small decision trees. The trees give a score for each of the 9 classes (none, Center, Donut, Edge-Loc, Edge-Ring, Loc, Near-full, Random, Scratch). The bars on the right show those scores as percentages.

It names a pattern early when the same class stays on top with a big enough lead at 3 checkpoints in a row, and at least 5% of the wafer is tested. Each class needs a different lead. The leads were set on separate calibration lots so that 90% of the early calls are right. Scratch never got there, so a scratch is only named at the end.

With a 2 x 2 probe card the engine also compares the 4 probe sites. When one site fails a lot more of its dies than the others, that site gets flagged, so a bad probe card doesn't get reported as a wafer pattern. Try it with "Damaged probe site".

Things to try
  - Pick a sample, press Play and watch when the call happens.
  - New blank wafer, then paint a ring around the edge or a blob in the middle.
  - Turn on Zones to see the 5 rings and 8 sectors the engine counts in.
  - Change the probe order and see if the call comes earlier or later.

This app runs the Python model of the engine. It gives the same checkpoint records as the Verilog design in Vivado, bit for bit. The design, the model and the training code are on GitHub:
""" + REPO

FORMATS = """Input formats

Map: one row of dies per line. 0 = no die, 1 = pass, 2 = fail.

    00111100
    01122110
    11222211
    01122110

The digits can also be split by spaces or commas. This is how the WM-811K data set stores its maps, and how the samples are written.

Die list: one die per line, in the order the prober tests them.

    x,y,fail,site
    12,0,1,0
    13,0,0,1
    12,1,0,2

fail is 0 or 1 (P/F and pass/fail work too). site is optional, 0 to 7. If a die shows up twice, the engine keeps its first result, the same as for a retest. The header line is optional when the columns are in this order. The .die files of the Vivado testbench open as they are.

Lines starting with # are comments. Three of them mean something:

    # title: My wafer     the name shown above the map
    # label: Center       the class you expect, the app compares it with the result
    # format: map         or "dies", if the app guesses wrong

Limits: up to 256 x 256 dies, probe sites 0 to 7."""


def pct(x):
    return f"{x * 100:.1f}%" if x < 0.1 else f"{x * 100:.0f}%"


class ScrollFrame(tk.Frame):
    """frame that scrolls when the window is too short for it"""

    def __init__(self, parent, bg, width):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, borderwidth=0, width=width)
        self.bar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.item = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.inner.bind("<Configure>", self._fit)
        self.canvas.bind("<Configure>", self._fit)
        self.bind_all("<MouseWheel>", self._wheel, add="+")

    def _fit(self, _=None):
        self.canvas.itemconfigure(self.item, width=self.canvas.winfo_width())
        h = self.inner.winfo_reqheight()
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), h))
        if h > self.canvas.winfo_height() + 1:
            self.bar.grid(row=0, column=1, sticky="ns")
        else:
            self.bar.grid_remove()
            self.canvas.yview_moveto(0)

    def _wheel(self, e):
        w = self.winfo_containing(e.x_root, e.y_root)
        while w is not None and w is not self:
            w = w.master
        if w is self and self.bar.winfo_ismapped():
            self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")


def tint(color, amount):
    """mix a colour with white"""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(c + (255 - c) * amount) for c in (r, g, b))


def text_on(color):
    """ink or white text, whichever reads better on this fill"""
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return INK if lum > 0.5 else "#ffffff"


class App:
    def __init__(self, root, args):
        self.root = root
        self.k = root.winfo_fpixels("1i") / 96.0
        self.samples = wf.samples()
        self.wafer = None              # last input that worked
        self.run = None                # engine run of that input
        self.input_error = None
        self.title = "Untitled"
        self.last_text = None
        self.loading = False
        self.pos = 0                   # die events shown so far
        self.playing = False
        self.last_ck = None
        self.jobs = {}
        self.img = None
        self.cell = self.gap = self.ox = self.oy = 0
        self.painting = None           # None, or True/False while painting fail/pass
        self.edit_fail = None
        self.last_die = None
        self.event_of = self.run_fail = self.rank_of = None
        self.radii2 = None

        self.order = tk.StringVar(value="serpentine")
        self.bad_site = tk.StringVar(value="none")
        self.bad_share = tk.StringVar(value="50")
        self.zones = tk.BooleanVar(value=False)
        self.brush = tk.StringVar(value="fail")
        self.brush_size = tk.StringVar(value="1")
        self.speed = tk.DoubleVar(value=math.log10(150))

        root.title("Wafer Pattern Engine")
        root.configure(bg=BG)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{min(self.px(1280), sw - self.px(40))}x{min(self.px(860), sh - self.px(110))}")
        root.minsize(min(self.px(1060), sw - self.px(40)), min(self.px(640), sh - self.px(110)))
        root.report_callback_exception = self._crash
        self._icon()
        self._fonts()
        self._style()
        self._menu()
        self._layout()
        self._keys()
        root.after(50, lambda: self._start(args))

    def px(self, n):
        return int(round(n * self.k))

    def _icon(self):
        n = 32
        img = tk.PhotoImage(width=n, height=n)
        c = (n - 1) / 2
        for y in range(n):
            row = []
            for x in range(n):
                r = math.hypot(x - c, y - c)
                if r > 15.5:
                    row.append("#ffffff")
                elif x % 4 == 3 or y % 4 == 3:
                    row.append("#ffffff")
                elif r > 11.5:
                    row.append(DIE_FAIL)
                else:
                    row.append(DIE_PASS)
            img.put("{" + " ".join(row) + "}", to=(0, y))
        self.icon_img = img
        self.root.iconphoto(True, img)

    def _fonts(self):
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
            tkfont.nametofont(name).configure(family="Segoe UI", size=9)
        self.f_small = tkfont.Font(family="Segoe UI", size=8)
        self.f_head = tkfont.Font(family="Segoe UI Semibold", size=8)
        self.f_bold = tkfont.Font(family="Segoe UI Semibold", size=9)
        self.f_big = tkfont.Font(family="Segoe UI Semibold", size=17)
        self.f_mono = tkfont.Font(family="Consolas", size=9)
        self.f_mono_b = tkfont.Font(family="Consolas", size=9, weight="bold")

    def _style(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=INK, bordercolor=LINE, lightcolor=CARD, darkcolor=LINE,
                    troughcolor=TRACK, focuscolor=ACCENT, selectbackground="#cfe0f5", selectforeground=INK)
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=INK)
        s.configure("Muted.TLabel", background=BG, foreground=INK2)
        s.configure("TButton", padding=(self.px(10), self.px(3)), background=FIELD, bordercolor=LINE,
                    lightcolor=FIELD, darkcolor=FIELD, foreground=INK)
        s.map("TButton", background=[("disabled", BG), ("pressed", "#e4e2dc"), ("active", "#efede7")],
              foreground=[("disabled", INK3)])
        s.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", bordercolor=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT)
        s.map("Accent.TButton", background=[("disabled", "#9ec5f4"), ("pressed", "#1c5cab"), ("active", "#256abf")],
              foreground=[("disabled", "#ffffff")])
        for w in ("TRadiobutton", "TCheckbutton"):
            s.configure(f"Card.{w}", background=CARD, foreground=INK, indicatorbackground=FIELD,
                        indicatorforeground=ACCENT)
            s.map(f"Card.{w}", background=[("active", CARD)], foreground=[("disabled", INK3)],
                  indicatorbackground=[("disabled", BG)])
        s.configure("TCombobox", fieldbackground=FIELD, background=FIELD, arrowcolor=INK2, padding=self.px(2))
        s.map("TCombobox", fieldbackground=[("readonly", FIELD), ("disabled", BG)],
              foreground=[("disabled", INK3)])
        s.configure("TSpinbox", fieldbackground=FIELD, background=FIELD, arrowcolor=INK2, padding=self.px(2))
        s.configure("Horizontal.TScale", background=BG, troughcolor=TRACK)
        s.configure("Vertical.TScrollbar", background=BG, troughcolor=FIELD, arrowcolor=INK3, bordercolor=FIELD)
        s.configure("Horizontal.TScrollbar", background=BG, troughcolor=FIELD, arrowcolor=INK3, bordercolor=FIELD)

    def _menu(self):
        m = tk.Menu(self.root)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Open...", accelerator="Ctrl+O", command=self.open_file)
        f.add_command(label="Save input as...", accelerator="Ctrl+S", command=self.save_input)
        f.add_command(label="Save checkpoint results as CSV...", command=self.save_results)
        f.add_separator()
        f.add_command(label="Run every file in a folder...", command=self.run_folder)
        f.add_separator()
        f.add_command(label="Exit", command=self.root.destroy)
        m.add_cascade(label="File", menu=f)
        s = tk.Menu(m, tearoff=0)
        for title, path in self.samples:
            s.add_command(label=title, command=lambda p=path: self.open_path(p))
        s.add_separator()
        s.add_command(label="New blank wafer...", command=self.new_blank)
        m.add_cascade(label="Samples", menu=s)
        h = tk.Menu(m, tearoff=0)
        h.add_command(label="How the engine works", command=lambda: self._text_window("How it works", HOW))
        h.add_command(label="Input formats", command=lambda: self._text_window("Input formats", FORMATS))
        h.add_separator()
        h.add_command(label="About", command=self.about)
        m.add_cascade(label="Help", menu=h)
        self.root.configure(menu=m)

    def _card(self, parent):
        return tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)

    def _pane(self, parent, orient):
        """paned window, the divider gets darker under the mouse"""
        pw = tk.PanedWindow(parent, orient=orient, bg=BG, bd=0, sashwidth=self.px(8), sashrelief="flat",
                            opaqueresize=True)

        def hover(e):
            hit = pw.identify(e.x, e.y)            # (index, "sash") over a divider, "" elsewhere
            pw.configure(bg=SASH if hit else BG)

        pw.bind("<Motion>", hover)
        pw.bind("<Leave>", lambda e: pw.configure(bg=BG))
        return pw

    def _head(self, parent, text):
        return tk.Label(parent, text=text, font=self.f_head, fg=INK3, bg=CARD, anchor="w")

    def _layout(self):
        P = self.px
        r = self.root
        r.columnconfigure(0, weight=1)
        r.rowconfigure(1, weight=1)

        tb = ttk.Frame(r, padding=(P(10), P(8), P(10), P(4)))
        tb.grid(row=0, column=0, sticky="ew")
        ttk.Button(tb, text="Open file...", command=self.open_file).pack(side="left")
        ttk.Button(tb, text="Save input as...", command=self.save_input).pack(side="left", padx=(P(6), 0))
        ttk.Label(tb, text="Sample", style="Muted.TLabel").pack(side="left", padx=(P(18), P(6)))
        self.sample_box = ttk.Combobox(tb, state="readonly", width=36, values=[t for t, _ in self.samples])
        self.sample_box.pack(side="left")
        self.sample_box.bind("<<ComboboxSelected>>", self._sample_chosen)
        ttk.Button(tb, text="New blank wafer...", command=self.new_blank).pack(side="left", padx=(P(6), 0))
        ttk.Button(tb, text="Input formats",
                   command=lambda: self._text_window("Input formats", FORMATS)).pack(side="right")
        ttk.Button(tb, text="How it works",
                   command=lambda: self._text_window("How it works", HOW)).pack(side="right", padx=(0, P(6)))

        # panels in paned windows so they can be resized
        rows = self.rows_pane = self._pane(r, "vertical")
        rows.grid(row=1, column=0, sticky="nsew", padx=P(10), pady=(P(4), 0))
        cols = self.cols_pane = self._pane(rows, "horizontal")
        cols.add(self._input_card(cols), minsize=P(230), width=P(340), stretch="never")
        cols.add(self._map_card(cols), minsize=P(470), stretch="always")
        cols.add(self._output_card(cols), minsize=P(290), width=P(360), stretch="never")
        rows.add(cols, minsize=P(360), stretch="always")
        rows.add(self._timeline_card(rows), minsize=P(90), height=P(112), stretch="never")
        self._playback_bar(r).grid(row=3, column=0, sticky="ew", padx=P(10), pady=P(6))

        sb = tk.Frame(r, bg="#e8e6e0")
        sb.grid(row=4, column=0, sticky="ew")
        self.status = tk.Label(sb, text="", bg="#e8e6e0", fg=INK2, anchor="w", font=self.f_small)
        self.status.pack(side="left", fill="x", expand=True, padx=P(10), pady=P(2))
        tk.Label(sb, text="Engine: Python model of the Verilog design, same checkpoint results",
                 bg="#e8e6e0", fg=INK3, font=self.f_small).pack(side="right", padx=P(10))

    def _input_card(self, parent):
        P = self.px
        c = self._card(parent)
        c.columnconfigure(0, weight=1)
        c.rowconfigure(2, weight=1)
        head = tk.Frame(c, bg=CARD)
        head.grid(row=0, column=0, sticky="ew", padx=P(12), pady=(P(10), P(2)))
        self._head(head, "INPUT").pack(side="left")
        self.toggle_lbl = tk.Label(head, text="Hide text", font=self.f_small, fg=ACCENT, bg=CARD, cursor="hand2")
        self.toggle_lbl.pack(side="right")
        self.toggle_lbl.bind("<Button-1>", lambda e: self._toggle_text())
        cap = tk.Label(c, text="The wafer as text. Each line is one row of dies: 0 = no die there, 1 = pass, "
                               "2 = fail. Type or paste your own, or paint on the map.",
                       font=self.f_small, fg=INK3, bg=CARD, justify="left", anchor="w", wraplength=P(300))
        cap.grid(row=1, column=0, sticky="ew", padx=P(12), pady=(0, P(6)))
        c.bind("<Configure>", lambda e: cap.configure(wraplength=max(P(150), e.width - P(28))), add="+")

        ed = tk.Frame(c, bg=CARD)
        ed.grid(row=2, column=0, sticky="nsew", padx=P(12))
        self.ed_frame = ed
        ed.columnconfigure(0, weight=1)
        ed.rowconfigure(0, weight=1)
        self.editor = tk.Text(ed, width=36, height=8, wrap="none", undo=True, maxundo=200, font=self.f_mono,
                              bg=FIELD, fg=INK2, insertbackground=INK, relief="flat", borderwidth=0,
                              highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT,
                              padx=P(6), pady=P(4), selectbackground="#cfe0f5", selectforeground=INK)
        self.editor.grid(row=0, column=0, sticky="nsew")
        ys = tk.Scrollbar(ed, orient="vertical", command=self.editor.yview)
        ys.grid(row=0, column=1, sticky="ns")
        xs = tk.Scrollbar(ed, orient="horizontal", command=self.editor.xview)
        xs.grid(row=1, column=0, sticky="ew")
        self.editor.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.editor.tag_configure("comment", foreground=INK3)
        self.editor.tag_configure("zero", foreground="#c9c7c0")
        self.editor.tag_configure("fail", foreground=DIE_FAIL, font=self.f_mono_b)
        self.editor.tag_configure("error", background="#f8d7d7")
        self.editor.tag_raise("error")
        self.editor.bind("<<Modified>>", self._on_modified)
        self.editor.bind("<Control-Return>", lambda e: (self.run_now(), "break")[1])

        self.msg = tk.Frame(c, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        self.msg.grid(row=3, column=0, sticky="ew", padx=P(12), pady=(P(8), 0))
        self.msg.columnconfigure(1, weight=1)
        self.msg_sign = tk.Label(self.msg, text="", font=self.f_bold, bg=CARD, width=2)
        self.msg_sign.grid(row=0, column=0, sticky="n", padx=(P(4), 0), pady=P(6))
        self.msg_text = tk.Label(self.msg, text="", justify="left", anchor="w", bg=CARD, fg=INK,
                                 wraplength=P(250))
        self.msg_text.grid(row=0, column=1, sticky="ew", padx=(P(2), P(8)), pady=P(6))
        self.msg.bind("<Configure>", lambda e: self.msg_text.configure(wraplength=max(P(120), e.width - P(44))))

        po = tk.Frame(c, bg=CARD)
        po.grid(row=4, column=0, sticky="ew", padx=P(12), pady=(P(12), 0))
        self._head(po, "PROBE ORDER").grid(row=0, column=0, sticky="w", pady=(0, P(2)))
        self.order_btns = {}
        for i, (key, text) in enumerate(wf.ORDERS.items()):
            b = ttk.Radiobutton(po, text=text, value=key, variable=self.order, style="Card.TRadiobutton",
                                command=self._settings_changed)
            b.grid(row=1 + i, column=0, sticky="w")
            self.order_btns[key] = b

        ds = tk.Frame(c, bg=CARD)
        ds.grid(row=5, column=0, sticky="ew", padx=P(12), pady=(P(10), 0))
        ds.columnconfigure(4, weight=1)
        self._head(ds, "DAMAGED PROBE SITE").grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, P(3)))
        self.site_box = ttk.Combobox(ds, state="disabled", width=7, textvariable=self.bad_site, values=["none"])
        self.site_box.grid(row=1, column=0)
        self.site_box.bind("<<ComboboxSelected>>", lambda e: self._settings_changed())
        tk.Label(ds, text="fails", bg=CARD, fg=INK2).grid(row=1, column=1, padx=(P(8), P(4)))
        self.share_spin = ttk.Spinbox(ds, from_=5, to=100, increment=5, width=4, textvariable=self.bad_share,
                                      command=self._settings_changed)
        self.share_spin.grid(row=1, column=2)
        self.share_spin.bind("<KeyRelease>", lambda e: self._later("settings", 600, self._settings_changed))
        tk.Label(ds, text="%", bg=CARD, fg=INK2).grid(row=1, column=3, padx=(P(3), 0))
        hint = tk.Label(ds, text="Needs 2 x 2 sites or a die list with sites. The damaged site fails this "
                                 "share of its good dies.",
                        font=self.f_small, fg=INK3, bg=CARD, justify="left", anchor="w", wraplength=P(280))
        hint.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(P(3), 0))
        ds.bind("<Configure>", lambda e: hint.configure(wraplength=max(P(150), e.width - P(4))))

        bt = tk.Frame(c, bg=CARD)
        bt.grid(row=6, column=0, sticky="ew", padx=P(12), pady=(P(12), P(12)))
        ttk.Button(bt, text="Run  (Ctrl+Enter)", style="Accent.TButton", command=self.run_now).pack(side="left")
        ttk.Button(bt, text="Clear", command=self.clear_input).pack(side="left", padx=(P(6), 0))
        return c

    def _map_card(self, parent):
        P = self.px
        c = self._card(parent)
        c.columnconfigure(0, weight=1)
        c.rowconfigure(1, weight=1)
        head = tk.Frame(c, bg=CARD)
        head.grid(row=0, column=0, sticky="ew", padx=P(12), pady=(P(10), 0))
        self.map_title = tk.Label(head, text="", font=self.f_bold, fg=INK, bg=CARD, anchor="w")
        self.map_title.pack(side="left")
        legend = tk.Canvas(head, bg=CARD, highlightthickness=0, width=P(196), height=P(16))
        legend.pack(side="right")
        x = 0
        for color, text in ((DIE_NEW, "not probed"), (DIE_PASS, "pass"), (DIE_FAIL, "fail")):
            legend.create_rectangle(x, P(4), x + P(9), P(13), fill=color, outline="")
            t = legend.create_text(x + P(13), P(8), text=text, anchor="w", fill=INK2, font=self.f_small)
            x = legend.bbox(t)[2] + P(12)
        self.map_info = tk.Label(c, text="", font=self.f_small, fg=INK3, bg=CARD, anchor="w", justify="left")
        self.map_info.grid(row=3, column=0, sticky="ew", padx=P(12), pady=(0, P(8)))
        c.bind("<Configure>", lambda e: self.map_info.configure(wraplength=max(P(200), e.width - P(28))), add="+")

        self.map_cv = tk.Canvas(c, bg=CARD, highlightthickness=0, width=P(440), height=P(380), cursor="crosshair")
        self.map_cv.grid(row=1, column=0, sticky="nsew", padx=P(6), pady=P(4))
        self.map_cv.bind("<Configure>", lambda e: self._later("render", 80, self._render_map))
        self.map_cv.bind("<Motion>", self._map_hover)
        self.map_cv.bind("<Leave>", lambda e: self._status(""))
        for b in (1, 3):
            self.map_cv.bind(f"<ButtonPress-{b}>", lambda e, b=b: self._paint_start(e, b))
            self.map_cv.bind(f"<B{b}-Motion>", self._paint_move)
            self.map_cv.bind(f"<ButtonRelease-{b}>", self._paint_end)

        tools = tk.Frame(c, bg=CARD)
        tools.grid(row=2, column=0, sticky="ew", padx=P(12), pady=(0, P(4)))
        self._head(tools, "PAINT").pack(side="left")
        ttk.Radiobutton(tools, text="Fail", value="fail", variable=self.brush,
                        style="Card.TRadiobutton").pack(side="left", padx=(P(8), 0))
        ttk.Radiobutton(tools, text="Pass", value="pass", variable=self.brush,
                        style="Card.TRadiobutton").pack(side="left", padx=(P(4), 0))
        tk.Label(tools, text="brush", bg=CARD, fg=INK2).pack(side="left", padx=(P(10), P(4)))
        ttk.Combobox(tools, state="readonly", width=2, values=["1", "3", "5", "9"],
                     textvariable=self.brush_size).pack(side="left")
        ttk.Checkbutton(tools, text="Zones", variable=self.zones, style="Card.TCheckbutton",
                        command=self._draw_zones).pack(side="left", padx=(P(14), 0))
        ttk.Button(tools, text="Clear fails", command=self.clear_fails).pack(side="right")
        return c

    def _output_card(self, parent):
        P = self.px
        card = self._card(parent)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)
        sf = ScrollFrame(card, CARD, P(330))
        sf.grid(row=0, column=0, sticky="nsew")
        c = sf.inner
        c.columnconfigure(0, weight=1)
        self._head(c, "OUTPUT").grid(row=0, column=0, sticky="w", padx=P(12), pady=(P(10), P(4)))

        res = tk.Frame(c, bg=CARD)
        res.grid(row=1, column=0, sticky="ew", padx=P(12))
        res.columnconfigure(1, weight=1)
        self.res_bar = tk.Frame(res, bg=LINE, width=P(5))
        self.res_bar.grid(row=0, column=0, rowspan=4, sticky="ns", padx=(0, P(10)))
        self.res_big = tk.Label(res, text="", font=self.f_big, fg=INK, bg=CARD, anchor="w")
        self.res_big.grid(row=0, column=1, sticky="w")
        self.res_sub = tk.Label(res, text="", fg=INK2, bg=CARD, anchor="w", justify="left", wraplength=P(290))
        self.res_sub.grid(row=1, column=1, sticky="w")
        self.res_more = tk.Label(res, text="", fg=INK2, bg=CARD, anchor="w", justify="left", wraplength=P(290))
        self.res_more.grid(row=2, column=1, sticky="w", pady=(P(4), 0))
        self.res_stale = tk.Label(res, text="", fg=BAD, bg=CARD, anchor="w", justify="left", wraplength=P(290))
        self.res_stale.grid(row=3, column=1, sticky="w", pady=(P(4), 0))
        self.res_stale.grid_remove()

        tk.Frame(c, bg=LINE, height=1).grid(row=2, column=0, sticky="ew", padx=P(12), pady=P(10))
        sh = tk.Frame(c, bg=CARD)
        sh.grid(row=3, column=0, sticky="ew", padx=P(12))
        self._head(sh, "CLASS SCORES").pack(side="left")
        self.score_note = tk.Label(sh, text="", font=self.f_small, fg=INK3, bg=CARD)
        self.score_note.pack(side="right")
        self.score_cv = tk.Canvas(c, bg=CARD, highlightthickness=0, width=P(312), height=P(9 * 20 + 4))
        self.score_cv.grid(row=4, column=0, sticky="ew", padx=P(12), pady=(P(4), 0))
        self.score_cv.bind("<Configure>", lambda e: self._update_output(force=True))

        tk.Frame(c, bg=LINE, height=1).grid(row=5, column=0, sticky="ew", padx=P(12), pady=P(10))
        self._head(c, "EARLY-CALL RULE").grid(row=6, column=0, sticky="w", padx=P(12))
        rule = tk.Frame(c, bg=CARD)
        rule.grid(row=7, column=0, sticky="ew", padx=P(12), pady=(P(2), 0))
        rule.columnconfigure(1, weight=1)
        self.rule = {}
        for i, (key, text) in enumerate((("top", "On top"), ("lead", "Lead over 2nd"),
                                         ("row", "In a row"), ("tested", "Wafer tested"))):
            tk.Label(rule, text=text, fg=INK2, bg=CARD, anchor="w").grid(row=i, column=0, sticky="w")
            v = tk.Label(rule, text="", fg=INK, bg=CARD, anchor="w")
            v.grid(row=i, column=1, sticky="w", padx=(P(10), 0))
            self.rule[key] = v

        tk.Frame(c, bg=LINE, height=1).grid(row=8, column=0, sticky="ew", padx=P(12), pady=P(10))
        self._head(c, "PROBE SITES").grid(row=9, column=0, sticky="w", padx=P(12))
        self.site_lbl = tk.Label(c, text="", fg=INK2, bg=CARD, anchor="w", justify="left", wraplength=P(290))
        self.site_lbl.grid(row=10, column=0, sticky="ew", padx=P(12), pady=(P(2), 0))
        self.site_cv = tk.Canvas(c, bg=CARD, highlightthickness=0, width=P(312), height=1)
        self.site_cv.grid(row=11, column=0, sticky="ew", padx=P(12), pady=(P(4), P(10)))

        def rewrap(e):
            # rewrap when the panel gets resized
            for lbl, pad in ((self.res_sub, 50), (self.res_more, 50), (self.res_stale, 50),
                             (self.site_lbl, 30)):
                lbl.configure(wraplength=max(P(160), e.width - P(pad)))

        c.bind("<Configure>", rewrap, add="+")
        return card

    def _timeline_card(self, parent):
        P = self.px
        c = self._card(parent)
        c.columnconfigure(0, weight=1)
        c.rowconfigure(1, weight=1)
        head = tk.Frame(c, bg=CARD)
        head.grid(row=0, column=0, sticky="ew", padx=P(12), pady=(P(8), 0))
        self._head(head, "CLASS ON TOP AT EACH CHECKPOINT").pack(side="left")
        tk.Label(head, text="bright: lead big enough for the call rule    pale: not yet    click to jump there",
                 font=self.f_small, fg=INK3, bg=CARD).pack(side="right")
        self.tl_cv = tk.Canvas(c, bg=CARD, highlightthickness=0, height=P(64), cursor="hand2")
        self.tl_cv.grid(row=1, column=0, sticky="nsew", padx=P(6), pady=(0, P(4)))
        self.tl_cv.bind("<Configure>", lambda e: self._draw_timeline())
        self.tl_cv.bind("<Motion>", self._tl_hover)
        self.tl_cv.bind("<Leave>", lambda e: self._status(""))
        self.tl_cv.bind("<Button-1>", self._tl_seek)
        self.tl_cv.bind("<B1-Motion>", self._tl_seek)
        return c

    def _playback_bar(self, parent):
        P = self.px
        f = ttk.Frame(parent)
        ttk.Button(f, text="Restart", command=self.restart).pack(side="left")
        self.play_btn = ttk.Button(f, text="Play", style="Accent.TButton", width=8, command=self.toggle_play)
        self.play_btn.pack(side="left", padx=P(6))
        ttk.Button(f, text="Step", command=lambda: self.step(1)).pack(side="left")
        ttk.Button(f, text="End", command=self.to_end).pack(side="left", padx=(P(6), 0))
        ttk.Label(f, text="Speed", style="Muted.TLabel").pack(side="left", padx=(P(18), P(6)))
        ttk.Scale(f, from_=1.0, to=4.3, variable=self.speed, length=P(170),
                  command=lambda v: self._speed_label()).pack(side="left")
        self.speed_lbl = ttk.Label(f, text="", style="Muted.TLabel", width=16)
        self.speed_lbl.pack(side="left", padx=(P(8), 0))
        self.pos_lbl = ttk.Label(f, text="", style="Muted.TLabel")
        self.pos_lbl.pack(side="right")
        self._speed_label()
        return f

    def _keys(self):
        r = self.root
        r.bind("<Control-o>", lambda e: self.open_file())
        r.bind("<Control-s>", lambda e: self.save_input())
        for key, fn in (("<space>", self.toggle_play), ("<Right>", lambda: self.step(1)),
                        ("<Left>", lambda: self.step(-1)), ("<Home>", self.restart), ("<End>", self.to_end)):
            r.bind(key, lambda e, fn=fn: None if self._typing(e) else (fn(), "break")[1])

    def _typing(self, e):
        """don't take keys from text boxes and buttons"""
        return isinstance(e.widget, (tk.Text, tk.Entry, ttk.Entry, ttk.Spinbox, ttk.Combobox, ttk.Button,
                                     ttk.Radiobutton, ttk.Checkbutton))

    def _start(self, a):
        if a.order:
            self.order.set(a.order)
        if a.bad_site is not None:
            self.bad_site.set(f"site {a.bad_site}")
        if a.bad_share is not None:
            self.bad_share.set(str(a.bad_share))
        self.zones.set(a.zones)
        if a.file:
            self.open_path(Path(a.file), keep_settings=True)
        else:
            want = (a.sample or "Edge-Ring").lower()
            hit = [p for t, p in self.samples if want in t.lower()]
            if hit:
                self.open_path(hit[0], keep_settings=True)
            else:
                self.set_text(wf.blank_wafer(40), "Blank wafer")
        if self.run and a.at is not None:
            self.seek(self.run.events_for_dies(round(a.at * self.run.n_total)))
        if a.play:
            self.toggle_play()

    def _later(self, name, ms, fn):
        if name in self.jobs:
            self.root.after_cancel(self.jobs[name])
        self.jobs[name] = self.root.after(ms, lambda: (self.jobs.pop(name, None), fn()))

    def _on_modified(self, _=None):
        if not self.editor.edit_modified():
            return
        self.editor.edit_modified(False)
        if self.loading:
            return
        self.pause()
        self._later("parse", 450, self.parse_and_run)

    def set_text(self, text, title, keep_view=False):
        view = self.editor.yview()[0], self.editor.xview()[0]
        self.loading = True
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)
        self.editor.edit_modified(False)
        self.editor.edit_reset()
        self.loading = False
        if keep_view:
            self.editor.yview_moveto(view[0])
            self.editor.xview_moveto(view[1])
        self.title = title
        self.last_text = None
        self.parse_and_run()

    def run_now(self):
        self.jobs.pop("parse", None)
        self.last_text = None
        self.parse_and_run()

    def parse_and_run(self):
        text = self.editor.get("1.0", "end-1c")
        if text == self.last_text:
            return
        self.last_text = text
        self.editor.tag_remove("error", "1.0", "end")
        if not any(s.strip() and not s.strip().startswith("#") for s in text.splitlines()):
            self.input_error = None
            self.wafer = self.run = None
            self._show_all()
            self._message("info", "Type or paste a wafer map here (rows of 0, 1, 2), open a file or pick a "
                                  "sample. Help > Input formats has examples.")
            return
        try:
            w = wf.parse(text, self.title)
        except wf.WaferFileError as e:
            self.input_error = e
            self._message("error", str(e))
            if e.line:
                self.editor.tag_add("error", f"{e.line}.0", f"{e.line}.0 lineend +1c")
                self.editor.see(f"{e.line}.0")
            self._sync_controls()
            self._update_output(force=True)
            return
        old_kind = self.wafer.kind if self.wafer else None
        self.input_error = None
        self.wafer = w
        self._color_text(text, w.kind)
        if w.kind == "dies" and old_kind != "dies":
            self.order.set("file")
        if w.kind == "map" and self.order.get() == "file":
            self.order.set("serpentine")
        self._sync_controls()
        self.rerun()

    def _color_text(self, text, kind):
        ed = self.editor
        for t in ("comment", "zero", "fail"):
            ed.tag_remove(t, "1.0", "end")
        lines = text.split("\n")
        big = len(text) > 400_000
        for n, line in enumerate(lines, 1):
            s = line.lstrip()
            if s.startswith("#"):
                ed.tag_add("comment", f"{n}.0", f"{n}.end")
            elif kind == "map" and not big:
                for tag, ch in (("fail", "2"), ("zero", "0")):
                    i = line.find(ch)
                    while i >= 0:
                        j = i
                        while j < len(line) and line[j] == ch:
                            j += 1
                        ed.tag_add(tag, f"{n}.{i}", f"{n}.{j}")
                        i = line.find(ch, j)

    def _sync_controls(self):
        w = self.wafer
        self.order_btns["file"].configure(state="normal" if w and w.kind == "dies" else "disabled")
        order = self.order.get()
        if order == "multisite":
            sites = 4
        elif order == "file" and w and w.n_sites > 1:
            sites = max(d[3] for d in w.dies) + 1
        else:
            sites = 0
        values = ["none"] + [f"site {s}" for s in range(sites)]
        self.site_box.configure(values=values, state="readonly" if sites else "disabled")
        if self.bad_site.get() not in values:
            self.bad_site.set("none")
        self.share_spin.configure(state="normal" if sites else "disabled")

    def _fault(self):
        """(site or None, share) from the controls, or raise ValueError with a message"""
        s = self.bad_site.get()
        if s == "none" or str(self.site_box.cget("state")) == "disabled":
            return None, 0.5
        try:
            share = float(self.bad_share.get())
        except ValueError:
            raise ValueError(f'"{self.bad_share.get()}" is not a number. The damaged site needs a share '
                             f'from 1 to 100%.') from None
        if not 1 <= share <= 100:
            raise ValueError(f"{self.bad_share.get()}% is out of range. Use 1 to 100%.")
        return int(s.split()[1]), share / 100

    def _settings_changed(self):
        self._sync_controls()
        if self.wafer and not self.input_error:
            self.rerun()

    def rerun(self, to_end=True):
        w = self.wafer
        try:
            site, share = self._fault()
        except ValueError as e:
            self._message("error", str(e))
            return
        try:
            self.run = wf.run(w, self.order.get(), site, share)
        except Exception as e:                      # should not happen, but say so if it does
            self.run = None
            self._message("error", f"The engine could not run this input: {e}")
            return
        r = self.run
        h, wd = w.valid.shape
        idx = np.nonzero(r.first)[0]
        d = r.dies[idx]
        self.event_of = np.full((h, wd), -1, np.int64)
        self.event_of[d[:, 1], d[:, 0]] = idx
        self.rank_of = np.zeros((h, wd), np.int64)
        self.rank_of[d[:, 1], d[:, 0]] = np.arange(1, len(idx) + 1)
        self.run_fail = np.zeros((h, wd), bool)
        self.run_fail[d[:, 1], d[:, 0]] = d[:, 2] == 1
        self.radii2 = wf.zone_radii2(r.cfg, w.valid)
        self.pos = r.n_events if to_end else 0
        self.last_ck = None

        what = (f"Map, {wd} x {h}, {w.n_dies:,} dies, {w.n_fail:,} fail." if w.kind == "map" else
                f"Die list, {len(w.dies):,} dies on a {wd} x {h} map, {w.n_fail:,} fail"
                + (f", sites 0 to {max(x[3] for x in w.dies)}." if w.n_sites > 1 else "."))
        notes = list(w.warnings)
        if site is not None:
            notes.append(f"Site {site} is damaged: it fails {pct(share)} of its good dies "
                         f"({int(r.dies[:, 2].sum()) - w.n_fail:,} extra fails).")
        self._message("note" if w.warnings else "info" if site is not None else "ok", " ".join([what] + notes))
        self._show_all()

    def _message(self, kind, text):
        fg, bg, sign = MSG[kind]
        self.msg.configure(bg=bg, highlightbackground=tint(fg, 0.55))
        self.msg_sign.configure(text=sign, fg=fg, bg=bg)
        self.msg_text.configure(text=text, bg=bg)

    def clear_input(self):
        if not self.ed_frame.winfo_ismapped():
            self._toggle_text()
        self.set_text("", "Untitled")
        self.editor.focus_set()

    def _toggle_text(self):
        if self.ed_frame.winfo_ismapped():
            self.ed_frame.grid_remove()
            self.toggle_lbl.configure(text="Show text")
        else:
            self.ed_frame.grid()
            self.toggle_lbl.configure(text="Hide text")

    def _sample_chosen(self, _=None):
        i = self.sample_box.current()
        if i >= 0:
            self.open_path(self.samples[i][1])
        self.root.focus_set()

    def open_file(self):
        p = filedialog.askopenfilename(
            title="Open a wafer map or die list",
            filetypes=[("Wafer files", "*.txt *.csv *.die *.map"), ("All files", "*.*")])
        if p:
            self.open_path(Path(p))

    def open_path(self, path, keep_settings=False):
        path = Path(path)
        try:
            text = wf.read_text(path)
        except (OSError, wf.WaferFileError) as e:
            msg = str(e) if isinstance(e, wf.WaferFileError) else f"Could not open {path.name}: {e.strerror or e}"
            self._message("error", msg)
            return
        self.pause()
        titles = [t for t, p in self.samples if p == path]
        self.sample_box.set(titles[0] if titles else "")
        if not keep_settings:
            self.bad_site.set("none")
        self.set_text(text, path.stem)
        self.editor.yview_moveto(0)
        self._auto_speed()

    def _auto_speed(self):
        """new wafer plays in about 10 s"""
        if self.run:
            self.speed.set(math.log10(min(max(self.run.n_total / 10, 40), 4000)))
            self._speed_label()

    def save_input(self):
        p = filedialog.asksaveasfilename(title="Save the input", defaultextension=".txt",
                                         initialfile=f"{self.title}.txt",
                                         filetypes=[("Text", "*.txt"), ("CSV", "*.csv"), ("All files", "*.*")])
        if not p:
            return
        try:
            Path(p).write_text(self.editor.get("1.0", "end-1c"), encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Save input", f"Could not save {p}:\n{e}", parent=self.root)
            return
        self._status(f"Saved the input to {p}")

    def save_results(self):
        if not self.run:
            messagebox.showinfo("Save results", "There is no result yet. Give the app a wafer first.",
                                parent=self.root)
            return
        p = filedialog.asksaveasfilename(title="Save checkpoint results", defaultextension=".csv",
                                         initialfile=f"{self.title}_checkpoints.csv",
                                         filetypes=[("CSV", "*.csv")])
        if not p:
            return
        try:
            wf.save_checkpoints(self.run, p)
        except OSError as e:
            messagebox.showerror("Save results", f"Could not save {p}:\n{e}", parent=self.root)
            return
        self._status(f"Saved {len(self.run.recs)} checkpoints to {p}")

    def run_folder(self):
        folder = filedialog.askdirectory(title="Folder with wafer files")
        if not folder:
            return
        files = wf.wafer_files_in(folder)
        if not files:
            messagebox.showinfo("Run a folder", "There are no .txt, .csv, .die or .map files in that folder.",
                                parent=self.root)
            return
        out = filedialog.asksaveasfilename(title="Save the results as", defaultextension=".csv",
                                           initialdir=folder, initialfile="wafer_results.csv",
                                           filetypes=[("CSV", "*.csv")])
        if not out:
            return
        order = self.order.get() if self.order.get() != "file" else "serpentine"
        win = tk.Toplevel(self.root, bg=CARD, padx=self.px(16), pady=self.px(14))
        win.title("Running the folder")
        win.transient(self.root)
        win.resizable(False, False)
        lbl = tk.Label(win, text=f"0 of {len(files)} files", bg=CARD, fg=INK, anchor="w")
        lbl.pack(fill="x")
        bar = ttk.Progressbar(win, length=self.px(320), maximum=len(files))
        bar.pack(pady=(self.px(8), 0))
        q = queue.Queue()

        def work():
            try:
                rows = wf.run_folder(files, order, progress=lambda i, n: q.put(("step", i)))
                wf.save_rows(rows, wf.BATCH_HEAD, out)
                q.put(("done", rows))
            except Exception as e:
                q.put(("fail", e))

        def poll():
            try:
                while True:
                    kind, val = q.get_nowait()
                    if kind == "step":
                        bar["value"] = val
                        lbl.configure(text=f"{val} of {len(files)} files")
                    elif kind == "done":
                        win.destroy()
                        self._folder_done(val, out)
                        return
                    else:
                        win.destroy()
                        messagebox.showerror("Run a folder", f"Stopped: {val}", parent=self.root)
                        return
            except queue.Empty:
                pass
            win.after(60, poll)

        threading.Thread(target=work, daemon=True).start()
        poll()

    def _folder_done(self, rows, out):
        errors = sum(1 for r in rows if r[-1])
        calls = sum(1 for r in rows if r[4])
        text = (f"Ran {len(rows)} files: {calls} with an early call, {len(rows) - calls - errors} without, "
                f"{errors} could not be read.\n\nResults are in {out}")
        if messagebox.askyesno("Run a folder", text + "\n\nOpen the results now?", parent=self.root):
            try:
                os.startfile(out)
            except OSError as e:
                messagebox.showerror("Run a folder", f"Could not open {out}:\n{e}", parent=self.root)

    def _show_all(self):
        self._render_map()
        self._draw_timeline()
        self._update_output(force=True)
        w = self.wafer
        if w:
            wd, h = w.size
            self.map_title.configure(text=w.title)
            self.map_info.configure(text=f"{wd} x {h} grid, {w.n_dies:,} dies, {w.n_fail:,} fail in the input. "
                                         f"Left button paints, right button does the opposite.")
            self.root.title(f"Wafer Pattern Engine - {w.title}")
        else:
            self.map_title.configure(text="No wafer")
            self.map_info.configure(text="")
            self.root.title("Wafer Pattern Engine")

    def _states(self):
        """0 no die, 1 not probed, 2 pass, 3 fail for every grid position"""
        w = self.wafer
        st = np.zeros(w.valid.shape, np.int8)
        st[w.valid] = 1
        if self.painting is not None or self.run is None:
            fail = self.edit_fail if self.painting is not None else w.fail
            st[w.valid] = np.where(fail[w.valid], 3, 2)
        else:
            shown = (self.event_of >= 0) & (self.event_of < self.pos)
            st[shown] = np.where(self.run_fail[shown], 3, 2)
        return st

    def _render_map(self):
        cv = self.map_cv
        cv.delete("all")
        W, H = cv.winfo_width(), cv.winfo_height()
        if not self.wafer or W < 20 or H < 20:
            cv.create_text(W // 2, H // 2, text="No wafer yet", fill=INK3, font=self.f_bold)
            self.cell = 0
            return
        h, w = self.wafer.valid.shape
        pad = self.px(10)
        cell = max(1, min((W - 2 * pad) // w, (H - 2 * pad) // h))
        gap = 1 if cell >= 6 else 0
        self.cell, self.gap = cell, gap
        self.ox, self.oy = (W - w * cell) // 2, (H - h * cell) // 2
        pal = np.array([CARD, DIE_NEW, DIE_PASS, DIE_FAIL])
        pix = np.repeat(np.repeat(self._states(), cell, 0), cell, 1)
        if gap:
            pix[:, (np.arange(w * cell) % cell) >= cell - gap] = 0
            pix[(np.arange(h * cell) % cell) >= cell - gap, :] = 0
        names = pal[pix]
        self.img = tk.PhotoImage(width=w * cell, height=h * cell)
        self.img.put(" ".join("{" + " ".join(row) + "}" for row in names), to=(0, 0))
        cv.create_image(self.ox, self.oy, image=self.img, anchor="nw", tags="map")
        self._draw_zones()
        self._draw_cursor()

    def _put(self, x, y, state):
        c, g = self.cell, self.gap
        color = (CARD, DIE_NEW, DIE_PASS, DIE_FAIL)[state]
        self.img.put(color, to=(x * c, y * c, (x + 1) * c - g, (y + 1) * c - g))

    def _draw_cursor(self):
        cv = self.map_cv
        cv.delete("cursor")
        r = self.run
        if not r or self.cell == 0 or not 0 < self.pos < r.n_events or self.painting is not None:
            return
        x, y = int(r.dies[self.pos - 1, 0]), int(r.dies[self.pos - 1, 1])
        c = self.cell
        x0, y0 = self.ox + x * c, self.oy + y * c
        m = max(2, c // 2)
        cv.create_rectangle(x0 - m, y0 - m, x0 + c + m - 1, y0 + c + m - 1, outline=INK, width=2, tags="cursor")
        cv.create_rectangle(x0 - m + 2, y0 - m + 2, x0 + c + m - 3, y0 + c + m - 3, outline="#ffffff", width=1,
                            tags="cursor")

    def _draw_zones(self):
        cv = self.map_cv
        cv.delete("zones")
        r = self.run
        if not (self.zones.get() and r and self.cell):
            return
        cfg, c = r.cfg, self.cell
        cx = self.ox + (cfg.cx2 / 2 + 0.5) * c
        cy = self.oy + (cfg.cy2 / 2 + 0.5) * c
        for i, t in enumerate(self.radii2):
            rx = math.sqrt(t) / (2 * cfg.wx) * c + (c / 2 if i == 4 else 0)
            ry = math.sqrt(t) / (2 * cfg.wy) * c + (c / 2 if i == 4 else 0)
            cv.create_oval(cx - rx, cy - ry, cx + rx, cy + ry, outline=ZONE, width=2 if i == 4 else 1,
                           dash=() if i == 4 else (4, 3), tags="zones")
        big = math.sqrt(self.radii2[-1])
        for deg in (0, 45, 90, 135):
            a = math.radians(deg)
            dx = big * math.cos(a) / (2 * cfg.wx) * c
            dy = big * math.sin(a) / (2 * cfg.wy) * c
            cv.create_line(cx - dx, cy - dy, cx + dx, cy + dy, fill=ZONE, dash=(4, 3), tags="zones")

    def _die_at(self, ex, ey):
        if not self.wafer or not self.cell:
            return None
        x, y = (ex - self.ox) // self.cell, (ey - self.oy) // self.cell
        w, h = self.wafer.size
        return (int(x), int(y)) if 0 <= x < w and 0 <= y < h else None

    def _map_hover(self, e):
        if self.painting is not None:
            return
        d = self._die_at(e.x, e.y)
        w, r = self.wafer, self.run
        if d is None or not w.valid[d[1], d[0]] or r is None:
            self._status("")
            return
        x, y = d
        cfg = r.cfg
        ring, sect = zone_of(x, y, cfg.cx2, cfg.cy2, cfg.wx, cfg.wy, cfg.thr)
        ev = int(self.event_of[y, x])
        state = ("not probed yet" if ev >= self.pos else ("fail" if self.run_fail[y, x] else "pass"))
        site = f", site {int(r.dies[ev, 3])}" if r.cfg.n_sites else ""
        self._status(f"Die ({x}, {y}): {state}. Probed as die {int(self.rank_of[y, x]):,} of {r.n_total:,}{site}. "
                     f"Ring {ring + 1} of 5 (1 is the centre), sector {sect + 1} of 8.")

    def _paint_start(self, e, button):
        self.map_cv.focus_set()         # so Space plays instead of typing into the editor
        w = self.wafer
        if not w:
            return
        if self.input_error:
            self._status("Fix the error in the input first. Painting would replace the text you are editing.")
            return
        if w.kind != "map":
            self._status("Painting works on map input. This input is a die list: change its fail column instead.")
            return
        self.pause()
        self.painting = (button == 1) == (self.brush.get() == "fail")
        self.edit_fail = w.fail.copy()
        self.last_die = None
        self._render_map()
        self._paint_move(e)

    def _paint_move(self, e):
        if self.painting is None:
            return
        d = self._die_at(e.x, e.y)
        if d is None:
            self.last_die = None
            return
        x1, y1 = d
        x0, y0 = self.last_die or d
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        size = int(self.brush_size.get())
        r = (size - 1) / 2
        reach = int(math.ceil(r))
        w = self.wafer
        h, wd = w.valid.shape
        for t in range(steps + 1):
            cx = round(x0 + (x1 - x0) * t / steps)
            cy = round(y0 + (y1 - y0) * t / steps)
            for dy in range(-reach, reach + 1):
                for dx in range(-reach, reach + 1):
                    if dx * dx + dy * dy > r * r + r:
                        continue
                    x, y = cx + dx, cy + dy
                    if 0 <= x < wd and 0 <= y < h and w.valid[y, x] and self.edit_fail[y, x] != self.painting:
                        self.edit_fail[y, x] = self.painting
                        self._put(x, y, 3 if self.painting else 2)
        self.last_die = d
        n = int((self.edit_fail & w.valid).sum())
        self._status(f"Painting {'fails' if self.painting else 'passes'}: {n:,} fail dies now. "
                     f"Let go of the button to run the engine.")

    def _paint_end(self, _=None):
        if self.painting is None:
            return
        self.painting = None
        w = self.wafer
        if not (self.edit_fail != w.fail).any():
            self._render_map()
            return
        comments = [c for c in w.comments if not c.lstrip("#").strip().lower().startswith(("label:", "title:"))]
        title = w.title if w.title.endswith("(edited)") else f"{w.title} (edited)"
        self.sample_box.set("")
        self.set_text(wf.map_text(w.valid, self.edit_fail, [f"# title: {title}"] + comments), title,
                      keep_view=True)
        self._status("")

    def new_blank(self):
        win = tk.Toplevel(self.root, bg=CARD, padx=self.px(16), pady=self.px(14))
        win.title("New blank wafer")
        win.transient(self.root)
        win.resizable(False, False)
        tk.Label(win, text="Dies across the wafer", bg=CARD, fg=INK).grid(row=0, column=0, sticky="w")
        val = tk.StringVar(value="40")
        sp = ttk.Spinbox(win, from_=6, to=256, increment=2, width=6, textvariable=val)
        sp.grid(row=0, column=1, sticky="w", padx=(self.px(8), 0))
        tk.Label(win, text="Random fails (%)", bg=CARD, fg=INK).grid(row=1, column=0, sticky="w",
                                                                    pady=(self.px(6), 0))
        noise = tk.StringVar(value="8")
        ttk.Spinbox(win, from_=0, to=50, increment=1, width=6, textvariable=noise).grid(
            row=1, column=1, sticky="w", padx=(self.px(8), 0), pady=(self.px(6), 0))
        hint = ("Real wafers always have some scattered fails, a normal one in WM-811K has about 10%. "
                "Paint your pattern on top with the mouse.")
        err = tk.Label(win, text=hint, bg=CARD, fg=INK3, justify="left", wraplength=self.px(280))
        err.grid(row=2, column=0, columnspan=2, sticky="w", pady=(self.px(8), 0))

        def ok(_=None):
            try:
                n = int(val.get())
                share = float(noise.get())
            except ValueError:
                bad = val.get() if not val.get().strip().isdigit() else noise.get()
                err.configure(text=f'"{bad}" is not a number.', fg=BAD)
                return
            if not 6 <= n <= 256:
                err.configure(text=f"{n} dies across is out of range. Use 6 to 256.", fg=BAD)
                return
            if not 0 <= share <= 50:
                err.configure(text=f"{noise.get()}% random fails is out of range. Use 0 to 50%.", fg=BAD)
                return
            win.destroy()
            self.sample_box.set("")
            self.set_text(wf.blank_wafer(n, share / 100), "Blank wafer")
            self._auto_speed()

        bt = tk.Frame(win, bg=CARD)
        bt.grid(row=3, column=0, columnspan=2, sticky="e", pady=(self.px(12), 0))
        ttk.Button(bt, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(bt, text="Make it", style="Accent.TButton", command=ok).pack(side="right", padx=(0, self.px(6)))
        win.bind("<Return>", ok)
        win.bind("<Escape>", lambda e: win.destroy())
        sp.focus_set()
        sp.selection_range(0, "end")

    def clear_fails(self):
        w = self.wafer
        if not w or self.input_error:
            self._status("Nothing to clear: the input has an error or is empty.")
            return
        if w.kind != "map":
            self._status("Clear fails works on map input. This input is a die list.")
            return
        comments = [c for c in w.comments if not c.lstrip("#").strip().lower().startswith(("label:", "title:"))]
        title = w.title if w.title.endswith("(edited)") else f"{w.title} (edited)"
        self.sample_box.set("")
        self.set_text(wf.map_text(w.valid, np.zeros_like(w.fail), [f"# title: {title}"] + comments), title,
                      keep_view=True)

    def toggle_play(self):
        if not self.run:
            return
        if self.playing:
            self.pause()
            return
        if self.pos >= self.run.n_events:
            self.seek(0)
        self.playing = True
        self.play_btn.configure(text="Pause")
        self._t = time.perf_counter()
        self._acc = 0.0
        self._tick()

    def pause(self):
        if self.playing:
            self.playing = False
            self.play_btn.configure(text="Play")

    def _tick(self):
        if not self.playing or not self.run:
            return
        now = time.perf_counter()
        self._acc += 10 ** self.speed.get() * min(now - self._t, 0.1)
        self._t = now
        n = int(self._acc)
        if n:
            self._acc -= n
            self._advance(n)
        if self.pos >= self.run.n_events:
            self.pause()
            return
        self.root.after(15, self._tick)

    def _advance(self, n):
        r = self.run
        a, b = self.pos, min(r.n_events, self.pos + n)
        for i in range(a, b):
            if r.first[i]:
                self._put(int(r.dies[i, 0]), int(r.dies[i, 1]), 3 if r.dies[i, 2] else 2)
        self.pos = b
        self._draw_cursor()
        self._draw_timeline(light=True)
        self._update_output()

    def seek(self, pos):
        if not self.run:
            return
        self.pos = max(0, min(int(pos), self.run.n_events))
        self._render_map()
        self._draw_timeline()
        self._update_output(force=True)

    def restart(self):
        self.pause()
        self.seek(0)

    def to_end(self):
        self.pause()
        if self.run:
            self.seek(self.run.n_events)

    def step(self, d):
        r = self.run
        if not r:
            return
        self.pause()
        j = r.ck_before(self.pos)
        if d > 0:
            target = r.ck_event[j + 1] + 1 if j + 1 < len(r.ck_event) else r.n_events
        else:
            target = r.ck_event[j - 1] + 1 if j >= 1 else 0
        self.seek(target)

    def _speed_label(self):
        self.speed_lbl.configure(text=f"{10 ** self.speed.get():,.0f} dies a second")

    def _tl_geom(self):
        """left, right, top, bottom of the strip"""
        W, H = self.tl_cv.winfo_width(), self.tl_cv.winfo_height()
        P = self.px
        return P(14), W - P(14), P(18), min(P(78), max(P(38), H - P(28)))

    def _draw_timeline(self, light=False):
        cv = self.tl_cv
        r = self.run
        j = r.ck_before(self.pos) if r else -1
        if light and j == self.last_ck:
            self._draw_playhead()
            return
        self.last_ck = j
        cv.delete("all")
        L, R, y0, y1 = self._tl_geom()
        P = self.px
        cv.create_line(L, y1 + P(3), R, y1 + P(3), fill=LINE)
        for f in (0, 0.25, 0.5, 0.75, 1.0):
            x = L + (R - L) * f
            cv.create_line(x, y1 + P(3), x, y1 + P(7), fill=INK3)
            cv.create_text(x, y1 + P(9), text="100% of the wafer tested" if f == 1 else f"{f * 100:.0f}%",
                           anchor="nw" if f == 0 else "ne" if f == 1 else "n", fill=INK3, font=self.f_small)
        if not r:
            cv.create_text((L + R) / 2, (y0 + y1) / 2, text="no wafer", fill=INK3, font=self.f_small)
            return
        N, cfg = r.n_total, r.cfg
        X = lambda n: L + (R - L) * n / N    # noqa: E731
        runs = []                             # [top, ok, from, to]
        prev = 0
        for rec in r.recs[:j + 1]:
            top = rec["top"]
            ok = top != 0 and rec["margin"] >= cfg.margin[top] and rec["f_frac"] >= cfg.min_frac
            if runs and runs[-1][0] == top and runs[-1][1] == ok:
                runs[-1][3] = rec["ndies"]
            else:
                runs.append([top, ok, prev, rec["ndies"]])
            prev = rec["ndies"]
        for top, ok, a, b in runs:
            color = CLASS_COLOR[top] if ok or top == 0 else tint(CLASS_COLOR[top], 0.6)
            cv.create_rectangle(X(a), y0, max(X(b), X(a) + 1), y1, fill=color, outline="")
        # class names inside the strip where they fit
        merged = []
        for top, ok, a, b in runs:
            if merged and merged[-1][0] == top:
                merged[-1][2] = b
            else:
                merged.append([top, a, b])
        jc = r.first_called()
        call_x = X(r.recs[jc]["call_ndies"]) if jc is not None and jc <= j else None
        for top, a, b in merged:
            name = CLASSES[top]
            tw = self.f_small.measure(name)
            xa, xb = X(a), X(b)
            xm = (xa + xb) / 2
            if call_x is not None and abs(xm - call_x) < tw / 2 + P(6):
                xm = call_x + tw / 2 + P(8)           # keep the name clear of the call line
            if xm - tw / 2 - P(4) < xa or xm + tw / 2 + P(4) > xb:
                continue
            fill = CLASS_COLOR[top] if top == 0 else tint(CLASS_COLOR[top], 0.6)
            cv.create_text(xm, (y0 + y1) / 2, text=name, fill=text_on(fill), font=self.f_small)
        if call_x is not None:
            c = r.recs[jc]
            x = call_x
            cv.create_line(x, y0 - P(4), x, y1 + P(3), fill=INK, width=2)
            label = f"named {CLASSES[c['call_cls']]} at {pct(c['call_ndies'] / N)}"
            anchor = "w" if x + self.f_small.measure(label) + P(8) < R else "e"
            cv.create_text(x + (P(5) if anchor == "w" else -P(5)), y0 - P(4), text=label, anchor=f"s{anchor}",
                           fill=INK, font=self.f_bold)
        self._draw_playhead()

    def _draw_playhead(self):
        cv = self.tl_cv
        cv.delete("head")
        r = self.run
        if not r:
            return
        L, R, y0, y1 = self._tl_geom()
        n = int(np.count_nonzero(r.first[:self.pos]))
        x = L + (R - L) * n / r.n_total
        cv.create_line(x, y0 - self.px(2), x, y1 + self.px(2), fill=ACCENT, width=2, tags="head")

    def _tl_index(self, ex):
        r = self.run
        if not r:
            return None
        L, R, _, _ = self._tl_geom()
        n = (min(max(ex, L), R) - L) / max(R - L, 1) * r.n_total
        return n, min(int(np.searchsorted([c["ndies"] for c in r.recs], n, side="left")), len(r.recs) - 1)

    def _tl_hover(self, e):
        hit = self._tl_index(e.x)
        if not hit:
            return
        n, j = hit
        r = self.run
        c = r.recs[j]
        top = c["top"]
        need = r.cfg.margin[top]
        lead = (f"lead {c['margin']} (needs {need})" if top and need < 65535 else
                "never called early" if top else "none is never called")
        self._status(f"Checkpoint after {c['ndies']:,} dies ({pct(c['ndies'] / r.n_total)}): {CLASSES[top]} on top, "
                     f"{lead}, {c['run_len']} in a row. Click to jump there.")

    def _tl_seek(self, e):
        self.tl_cv.focus_set()
        hit = self._tl_index(e.x)
        if not hit:
            return
        self.pause()
        self.seek(self.run.events_for_dies(round(hit[0])))

    def _update_output(self, force=False):
        r = self.run
        j = r.ck_before(self.pos) if r else -1
        self._update_result(j)
        self._update_sites()
        self.pos_lbl.configure(text=(f"{int(np.count_nonzero(r.first[:self.pos])):,} of {r.n_total:,} dies tested"
                                     if r else ""))
        if not force and getattr(self, "_shown_ck", None) == (id(r), j):
            return
        self._shown_ck = (id(r), j)
        rec = r.recs[j] if r and j >= 0 else None
        self._draw_scores(rec)
        self._update_rule(rec)

    def _update_result(self, j):
        r, w = self.run, self.wafer
        stale = "The input has an error. This is still the result of the last input that worked." \
            if self.input_error and r else ""
        if not r:
            big, sub, more, bar = "No result", "Give the app a wafer on the left.", "", LINE
            if self.input_error:
                sub = "The input has an error, see the message under the editor."
        else:
            N, E = r.n_total, r.n_events
            tested = int(np.count_nonzero(r.first[:self.pos]))
            jc = r.first_called()
            last = r.recs[-1]
            at_end = self.pos >= E
            if jc is not None and j >= jc:
                c = r.recs[jc]
                big, bar = CLASSES[c["call_cls"]], CLASS_COLOR[c["call_cls"]]
                sub = f"Named early, after {c['call_ndies']:,} of {N:,} dies ({pct(c['call_ndies'] / N)} of the wafer)."
            elif at_end:
                bar = LINE
                if last["top"] == 0:
                    big, sub = "No pattern", "No early call. At the end the engine sees no pattern on this wafer."
                else:
                    big = "No early call"
                    sub = ("No class kept a big enough lead for 3 checkpoints in a row, so the pattern is only "
                           "named at the end.")
            else:
                big, bar = "Probing...", LINE
                sub = f"{tested:,} of {N:,} dies tested ({pct(tested / N)}). No call yet."
            lines = []
            if at_end:
                full = CLASSES[last["top"]]
                if jc is not None:
                    same = r.recs[jc]["call_cls"] == last["top"]
                    lines.append(f"Full wafer: {full}" + (", same as the early call." if same else
                                                         ", not the same as the early call."))
                else:
                    lines.append(f"Full wafer: {full}.")
                if w.label:
                    lines.append(f"Label in the input: {w.label}. " +
                                 ("The engine agrees." if w.label == full else "The engine does not agree."))
            elif w.label:
                lines.append(f"Label in the input: {w.label}.")
            more = "\n".join(lines)
        self.res_big.configure(text=big)
        self.res_sub.configure(text=sub)
        self.res_more.configure(text=more)
        self.res_stale.configure(text=stale)
        if stale:
            self.res_stale.grid()
        else:
            self.res_stale.grid_remove()
        self.res_bar.configure(bg=bar)

    def _draw_scores(self, rec):
        cv = self.score_cv
        cv.delete("all")
        P = self.px
        W = max(cv.winfo_width(), P(200))
        rh = P(20)
        x_bar, x_end = P(96), W - P(44)
        probs = wf.probabilities(rec) if rec else None
        top = rec["top"] if rec else -1
        self.score_note.configure(text="shown as percentages" if rec else
                                  "first checkpoint after 16 dies")
        for k, name in enumerate(CLASSES):
            ym = P(2) + k * rh + rh / 2
            cv.create_rectangle(0, ym - P(4), P(8), ym + P(4), fill=CLASS_COLOR[k], outline="")
            cv.create_text(P(14), ym, text=name, anchor="w", fill=INK if k == top else INK2,
                           font=self.f_bold if k == top else "TkDefaultFont")
            cv.create_rectangle(x_bar, ym - P(4), x_end, ym + P(4), fill=TRACK, outline="")
            if probs is None:
                continue
            p = float(probs[k])
            if p >= 0.005:
                cv.create_rectangle(x_bar, ym - P(4), x_bar + max(P(2), (x_end - x_bar) * p), ym + P(4),
                                    fill=CLASS_COLOR[k], outline="")
            cv.create_text(W, ym, text=f"{p * 100:.0f}%" if p >= 0.01 else "<1%", anchor="e",
                           fill=INK if k == top else INK2, font=self.f_bold if k == top else "TkDefaultFont")

    def _update_rule(self, rec):
        r = self.run
        if not rec:
            for v in self.rule.values():
                v.configure(text="-", fg=INK3)
            return
        cfg = r.cfg
        top = rec["top"]
        need = cfg.margin[top]
        self.rule["top"].configure(text=CLASSES[top] + (" (no pattern)" if top == 0 else ""), fg=INK)
        if top == 0:
            self.rule["lead"].configure(text=f"{rec['margin']}, none is never called", fg=INK2)
        elif need >= 65535:
            self.rule["lead"].configure(text=f"{rec['margin']}, {CLASSES[top]} is never called early", fg=INK2)
        else:
            self._rule_line("lead", f"{rec['margin']}", f"{need}", rec["margin"] >= need)
        n = rec["run_len"]
        self._rule_line("row", f"{n}{'+' if n >= RUN_MAX else ''} checkpoint{'' if n == 1 else 's'}",
                        f"{cfg.persist}", n >= cfg.persist)
        self._rule_line("tested", pct(rec["f_frac"] / 4096), pct(cfg.min_frac / 4096), rec["f_frac"] >= cfg.min_frac)

    def _rule_line(self, key, have, need, ok):
        self.rule[key].configure(text=f"{have}  (needs {need})  " + ("✓" if ok else "not yet"),
                                 fg=GOOD if ok else INK)

    def _update_sites(self):
        cv = self.site_cv
        cv.delete("all")
        r = self.run
        P = self.px
        if not r or r.cfg.n_sites == 0:
            self.site_lbl.configure(text="Off. With one probe site there is nothing to compare. Pick 2 x 2 sites "
                                         "to turn the check on." if r else "")
            cv.configure(height=1)
            return
        n = r.cfg.n_sites
        sel = r.first[:self.pos]
        d = r.dies[:self.pos][sel]
        ts = np.bincount(d[:, 3], minlength=n)[:n]
        fs = np.bincount(d[:, 3], weights=d[:, 2], minlength=n)[:n]
        j = r.ck_before(self.pos)
        rec = r.recs[j] if j >= 0 else None
        flagged = rec["site_id"] if rec and rec["site_flag"] else None
        if flagged is not None:
            text = (f"Site {flagged} flagged after {pct(rec['site_ndies'] / r.n_total)} of the wafer. It fails a "
                    f"lot more dies than the other sites, so this looks like the probe card, not the wafer.")
        elif self.pos >= r.n_events:
            text = "No site flagged. All sites fail at about the same rate."
        else:
            text = "Comparing the fail rate of each site. No site flagged so far."
        self.site_lbl.configure(text=text)
        W = max(cv.winfo_width(), P(200))
        rh = P(18)
        cv.configure(height=n * rh + P(2))
        x_bar, x_end = P(52), W - P(96)
        for s in range(n):
            ym = s * rh + rh / 2
            bad = s == flagged
            cv.create_text(0, ym, text=f"Site {s}", anchor="w", fill=BAD if bad else INK2,
                           font=self.f_bold if bad else "TkDefaultFont")
            cv.create_rectangle(x_bar, ym - P(4), x_end, ym + P(4), fill=TRACK, outline="")
            rate = fs[s] / ts[s] if ts[s] else 0.0
            if rate > 0:
                cv.create_rectangle(x_bar, ym - P(4), x_bar + max(P(2), (x_end - x_bar) * rate), ym + P(4),
                                    fill=BAD if bad else "#8e8d86", outline="")
            cv.create_text(W, ym, text=(f"{rate * 100:.0f}% of {int(ts[s]):,}" + ("  flagged" if bad else "")),
                           anchor="e", fill=BAD if bad else INK2, font=self.f_small)

    def _status(self, text):
        self.status.configure(text=text)

    def _text_window(self, title, text):
        win = tk.Toplevel(self.root, bg=CARD)
        win.title(title)
        win.transient(self.root)
        win.geometry(f"{self.px(560)}x{self.px(560)}")
        ttk.Button(win, text="Close", command=win.destroy).pack(side="bottom", pady=self.px(10))
        bar = tk.Scrollbar(win, orient="vertical")
        bar.pack(side="right", fill="y")
        t = tk.Text(win, wrap="word", font="TkDefaultFont", bg=CARD, fg=INK, relief="flat", padx=self.px(18),
                    pady=self.px(14), spacing2=self.px(2), spacing3=self.px(2), yscrollcommand=bar.set)
        t.pack(side="left", fill="both", expand=True)
        bar.configure(command=t.yview)
        t.tag_configure("h", font=self.f_big, spacing3=self.px(6))
        t.tag_configure("code", font=self.f_mono, foreground=INK2, lmargin1=self.px(14), spacing3=0)
        first, rest = text.split("\n", 1)
        t.insert("end", first + "\n", "h")
        for line in rest.split("\n"):
            t.insert("end", line + "\n", "code" if line.startswith("    ") else ())
        t.configure(state="disabled")
        win.bind("<Escape>", lambda e: win.destroy())

    def about(self):
        win = tk.Toplevel(self.root, bg=CARD, padx=self.px(20), pady=self.px(16))
        win.title("About")
        win.transient(self.root)
        win.resizable(False, False)
        tk.Label(win, text="Wafer Pattern Engine", font=self.f_big, bg=CARD, fg=INK).pack(anchor="w")
        tk.Label(win, text=f"Desktop app {VERSION}, Manan Chotaliya, 2026", bg=CARD, fg=INK2).pack(anchor="w")
        tk.Label(win, text=("An FPGA engine (Verilog, AMD Vivado) that names the defect pattern on a wafer map "
                            "while the wafer is still being probed. This app runs its Python model, which gives "
                            "the same results as the hardware. Trained and tested on WM-811K."),
                 bg=CARD, fg=INK, justify="left", wraplength=self.px(380)).pack(anchor="w", pady=(self.px(10), 0))
        bt = tk.Frame(win, bg=CARD)
        bt.pack(fill="x", pady=(self.px(14), 0))
        ttk.Button(bt, text="Close", command=win.destroy).pack(side="right")
        ttk.Button(bt, text="Open on GitHub", command=lambda: webbrowser.open(REPO)).pack(
            side="right", padx=(0, self.px(6)))

    def _crash(self, exc, val, tb):
        detail = "".join(traceback.format_exception(exc, val, tb))[-1500:]
        self.pause()
        messagebox.showerror("Something went wrong",
                             f"The app hit an error it did not expect:\n\n{val}\n\nIt keeps running, but the last "
                             f"action may not have finished.\n\n{detail}", parent=self.root)


def main():
    ap = argparse.ArgumentParser(description="Wafer pattern engine, desktop app")
    ap.add_argument("file", nargs="?", help="wafer map or die list to open")
    ap.add_argument("--sample", help="open the sample whose title contains this text")
    ap.add_argument("--order", choices=list(wf.ORDERS))
    ap.add_argument("--bad-site", type=int)
    ap.add_argument("--bad-share", type=int, help="percent of good dies the damaged site fails")
    ap.add_argument("--at", type=float, help="show the wafer at this share tested (0 to 1)")
    ap.add_argument("--zones", action="store_true", help="draw the rings and sectors")
    ap.add_argument("--play", action="store_true", help="start playing right away")
    args = ap.parse_args()
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    App(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
#
# Plot Bridge — insert sympy/matplotlib plots into LibreOffice at the cursor.
# Copyright (C) 2026 mdoksa76
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <https://www.gnu.org/licenses/>.
"""
Plot Bridge — LibreOffice UNO component.

Thin UNO wrapper around an external system Python that runs plotbuilder.py
(sympy + matplotlib). Builds a dialog in code, calls the builder as a
subprocess, inserts the resulting SVG at the cursor, and stores the JSON
spec on the image so the plot can be re-edited (Replot) later.

All user-facing strings are English.
"""

import os
import sys
import json
import tempfile
import subprocess

import uno
import unohelper
from com.sun.star.task import XJobExecutor
from com.sun.star.awt import XItemListener

# ---- configuration -------------------------------------------------------
# Where to find the system Python and the builder. Kept as module constants
# so they are easy to override during development. On the target machine these
# are confirmed/adjusted once (LibreOffice subprocesses do not always inherit
# the shell PATH, hence an absolute python path).
# Windows: ostavi None za automatsko traženje, ili upiši punu putanju, npr.
#   r"C:\Users\Ime\AppData\Local\Programs\Python\Python312\python.exe"
# Može se zadati i preko varijable okoline PLOTBRIDGE_PYTHON.
WINDOWS_PYTHON = None


REQUIRED_MODULES = ("sympy", "numpy", "matplotlib")


def _has_modules(cmd, timeout=12):
    """True ako interpreter 'cmd' (str ili lista argv) ima sve tražene module.
    Tiho provjeri kratkim importom; bez prozora na Windowsu."""
    import subprocess as _sp
    argv = cmd if isinstance(cmd, (list, tuple)) else [cmd]
    check = "import %s" % ", ".join(REQUIRED_MODULES)
    kw = {}
    if sys.platform.startswith("win"):
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        r = _sp.run(list(argv) + ["-c", check],
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                    timeout=timeout, **kw)
        return r.returncode == 0
    except Exception:
        return False


def _windows_candidates():
    """Uredno poredani kandidati za Python na Windowsu (prioritet prvi)."""
    import glob
    import shutil
    cands = []
    env = os.environ.get("PLOTBRIDGE_PYTHON")
    if env:
        cands.append(env)
    if WINDOWS_PYTHON:
        cands.append(WINDOWS_PYTHON)
    # službeni py launcher — najpouzdaniji na Windowsu
    py = shutil.which("py")
    if py:
        cands.append([py, "-3"])
    # česte instalacijske lokacije (noviji prvi)
    local = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    for pat in (os.path.join(local, "Programs", "Python", "Python3*", "python.exe"),
                os.path.join(pf, "Python3*", "python.exe"),
                r"C:\Python3*\python.exe"):
        cands.extend(sorted(glob.glob(pat), reverse=True))
    # PATH (preskoči Microsoft Store stub)
    for name in ("python.exe", "python3.exe"):
        w = shutil.which(name)
        if w and "WindowsApps" not in w:
            cands.append(w)
    return cands


def _is_libreoffice_python(cmd):
    p = cmd[0] if isinstance(cmd, (list, tuple)) else cmd
    return "libreoffice" in str(p).lower()


def _find_windows_python():
    """Nađi Python koji STVARNO ima potrebne module (ne samo koji postoji).
    Vrati str ili listu argv (npr. ['py','-3'])."""
    cands = _windows_candidates()
    first_existing = None
    for c in cands:
        if _is_libreoffice_python(c):
            continue
        p = c[0] if isinstance(c, (list, tuple)) else c
        exists = isinstance(c, (list, tuple)) or os.path.isfile(p)
        if not exists:
            continue
        if first_existing is None:
            first_existing = c
        if _has_modules(c):
            return c  # prvi s modulima — to je pravi
    # nijedan s modulima: vrati prvi koji postoji (builder će javiti ImportError
    # s jasnom porukom), pa fallback na goli 'python'/'py'
    if first_existing is not None:
        return first_existing
    import shutil
    return (["py", "-3"] if shutil.which("py") else
            (shutil.which("python") or "python"))


if sys.platform.startswith("win"):
    SYSTEM_PYTHON = _find_windows_python()
else:
    SYSTEM_PYTHON = "/usr/bin/python3"

# plotbuilder.py is shipped inside the extension (python/ dir) next to this file.
_HERE = os.path.dirname(os.path.abspath(__file__))
PLOTBUILDER = os.path.join(_HERE, "plotbuilder.py")

# Property name under which we stash the spec JSON on the inserted image,
# so Replot can read it back.
SPEC_PROP = "PlotBridgeSpec"

# dropdown order; index in ListBox == index here
KIND_ORDER = ("2d", "parametric", "polar", "implicit",
              "3d", "cylindrical", "spherical", "param_surface")

# 3D surface colormaps (matplotlib names). Easy to extend.
CMAPS = ["viridis", "plasma", "inferno", "coolwarm", "ocean",
         "terrain", "gray", "cividis", "jet"]

# Built-in examples per plot type. Each is (label, fields-dict). Fields map to
# the dialog controls that example should fill. Easy to extend.
EXAMPLES = {
    "2d": [
        ("Linear  2x+1", {"func": ["2x+1"]}),
        ("Quadratic  x²-3", {"func": ["x^2-3"]}),
        ("Cubic  x³-3x", {"func": ["x^3-3x"]}),
        ("Sine  3sin(x)", {"func": ["3sin(x)"]}),
        ("Reciprocal  1/x", {"func": ["1/x"]}),
    ],
    "parametric": [
        ("Circle", {"pairs": [("cos(t)", "sin(t)")], "t": ("0", "2pi")}),
        ("Ellipse", {"pairs": [("2cos(t)", "sin(t)")], "t": ("0", "2pi")}),
        ("Lissajous", {"pairs": [("sin(3t)", "sin(4t)")], "t": ("0", "2pi")}),
        ("Cycloid", {"pairs": [("t-sin(t)", "1-cos(t)")], "t": ("0", "6pi")}),
        ("Astroid", {"pairs": [("cos(t)^3", "sin(t)^3")], "t": ("0", "2pi")}),
    ],
    "polar": [
        ("Rose  cos(4θ)", {"func": ["cos(4*theta)"], "t": ("0", "2pi")}),
        ("Cardioid", {"func": ["1+cos(theta)"], "t": ("0", "2pi")}),
        ("Archimedean spiral", {"func": ["theta"], "t": ("0", "6pi")}),
        ("Lemniscate", {"func": ["sqrt(abs(cos(2*theta)))*3"],
                        "t": ("0", "2pi")}),
        ("Limaçon", {"func": ["1+2*cos(theta)"], "t": ("0", "2pi")}),
    ],
    "implicit": [
        ("Circle", {"func": ["x^2+y^2=9"], "x": ("-5", "5")}),
        ("Ellipse", {"func": ["x^2/9+y^2/4=1"], "x": ("-5", "5")}),
        ("Hyperbola", {"func": ["x^2-y^2=1"], "x": ("-5", "5")}),
        ("Descartes' folium", {"func": ["x^3+y^3-3*x*y"], "x": ("-3", "3")}),
        ("Lemniscate", {"func": ["(x^2+y^2)^2-4*(x^2-y^2)"], "x": ("-3", "3")}),
    ],
    "3d": [
        ("Linear  x+y", {"f3": "x+y", "x": ("-5", "5")}),
        ("Quadratic  x²+y²", {"f3": "x^2+y^2", "x": ("-3", "3")}),
        ("Monkey saddle  x³-3xy²", {"f3": "x^3-3*x*y^2",
                                           "x": ("-2", "2")}),
        ("Sine  sin(√(x²+y²))", {"f3": "sin(sqrt(x^2+y^2))",
                                              "x": ("-8", "8")}),
        ("Gaussian  e^-(x²+y²)", {"f3": "exp(-(x^2+y^2))",
                                           "x": ("-3", "3")}),
    ],
    "cylindrical": [
        ("Ripples  sin(2r)", {"f3": "sin(2*r)", "x": ("0", "5"),
                              "y": ("0", "2pi")}),
        ("Funnel  r²", {"f3": "r^2", "x": ("0", "4"), "y": ("0", "2pi")}),
        ("Cone  r", {"f3": "r", "x": ("0", "5"), "y": ("0", "2pi")}),
        ("Spiral wave  sin(3θ+r)", {"f3": "sin(3*theta+r)",
                                        "x": ("0", "6"), "y": ("0", "2pi")}),
        ("Petals  cos(4θ)*r", {"f3": "cos(4*theta)*r", "x": ("0", "4"),
                                   "y": ("0", "2pi")}),
    ],
    "spherical": [
        ("Sphere  1", {"f3": "1", "x": ("0", "pi"), "y": ("0", "2pi")}),
        ("Bumpy  1+0.3sin(4θ)sin(4φ)",
         {"f3": "1+0.3*sin(4*theta)*sin(4*phi)", "x": ("0", "pi"),
          "y": ("0", "2pi")}),
        ("Harmonic  1+0.4cos(3φ)",
         {"f3": "1+0.4*cos(3*phi)*sin(theta)", "x": ("0", "pi"),
          "y": ("0", "2pi")}),
        ("Pear  1+0.5cos(θ)",
         {"f3": "1+0.5*cos(theta)", "x": ("0", "pi"), "y": ("0", "2pi")}),
        ("Flower  1+0.3sin(6φ)",
         {"f3": "1+0.3*sin(6*phi)*sin(theta)^2", "x": ("0", "pi"),
          "y": ("0", "2pi")}),
    ],
    "param_surface": [
        ("Torus", {"uv": ("(3+cos(v))*cos(u)", "(3+cos(v))*sin(u)", "sin(v)"),
                   "x": ("0", "2pi"), "y": ("0", "2pi")}),
        ("Möbius strip",
         {"uv": ("(1+v/2*cos(u/2))*cos(u)", "(1+v/2*cos(u/2))*sin(u)",
                 "v/2*sin(u/2)"), "x": ("0", "2pi"), "y": ("-1", "1")}),
        ("Helicoid", {"uv": ("v*cos(u)", "v*sin(u)", "u"),
                      "x": ("0", "4pi"), "y": ("0", "2")}),
        ("Sphere", {"uv": ("sin(u)*cos(v)", "sin(u)*sin(v)", "cos(u)"),
                    "x": ("0", "pi"), "y": ("0", "2pi")}),
        ("Seashell",
         {"uv": ("(1-u/(2pi))*cos(6u)*(1+cos(v))+0.2*cos(6u)",
                 "(1-u/(2pi))*sin(6u)*(1+cos(v))+0.2*sin(6u)",
                 "u/(2pi)*3+(1-u/(2pi))*sin(v)"),
          "x": ("0", "2pi"), "y": ("0", "2pi")}),
    ],
}

IMPL_NAME = "org.mdoksa.plotbridge"

import math as _math
import re as _re


def _parse_num(s):
    """Parse a range/step value that may use pi and e:
    '2pi', '7*pi', 'pi/2', '-3pi', '2e', '3.5', '1/2' → float, else None.
    Safe: only digits, + - * / ( ) . and the names pi/e are allowed."""
    if s is None:
        return None
    t = str(s).strip().lower().replace(",", ".")
    if t == "":
        return None
    # plain number fast path
    try:
        return float(t)
    except Exception:
        pass
    # implicit multiplication: 2pi → 2*pi, 3e → 3*e, )( → )*(
    t = _re.sub(r"(\d)\s*(pi|e|\()", r"\1*\2", t)
    t = _re.sub(r"(pi|e)\s*(\d|\()", r"\1*\2", t)
    t = _re.sub(r"\)\s*(\d|pi|e|\()", r")*\1", t)
    if not _re.fullmatch(r"[0-9+\-*/().\sepi]*", t):
        return None
    try:
        val = eval(t, {"__builtins__": {}}, {"pi": _math.pi, "e": _math.e})
        return float(val)
    except Exception:
        return None


# ---- small UNO helpers ---------------------------------------------------
def _ctx():
    return uno.getComponentContext()


def _smgr():
    return _ctx().ServiceManager


def _create(service, *args):
    sm = _smgr()
    if args:
        return sm.createInstanceWithArgumentsAndContext(service, args, _ctx())
    return sm.createInstanceWithContext(service, _ctx())


def _desktop():
    return _create("com.sun.star.frame.Desktop")


def _msgbox(parent_peer, message, title="Plot Bridge", kind="errorbox"):
    tk = _create("com.sun.star.awt.Toolkit")
    from com.sun.star.awt.MessageBoxButtons import BUTTONS_OK
    try:
        from com.sun.star.awt.MessageBoxType import ERRORBOX, INFOBOX
        mbtype = ERRORBOX if kind == "errorbox" else INFOBOX
        box = tk.createMessageBox(parent_peer, mbtype, BUTTONS_OK, title, message)
    except Exception:
        # older API fallback
        box = tk.createMessageBox(parent_peer, uno.Enum(
            "com.sun.star.awt.MessageBoxType",
            "ERRORBOX" if kind == "errorbox" else "INFOBOX"),
            BUTTONS_OK, title, message)
    return box.execute()


# ---- dialog construction (built in code, no .xdl) ------------------------
def _add(model, ctrl_type, name, props):
    c = model.createInstance("com.sun.star.awt.UnoControl%sModel" % ctrl_type)
    for k, v in props.items():
        setattr(c, k, v)
    model.insertByName(name, c)
    return c


def _build_dialog(spec=None):
    """Create the dialog model+control. If spec is given, prefill fields
    (used by Replot). Returns (dialog, model)."""
    spec = spec or {}
    grid = spec.get("grid") or {}

    dmodel = _create("com.sun.star.awt.UnoControlDialogModel")
    dmodel.Title = "Plot Bridge"
    dmodel.Width = 230
    dmodel.Height = 285

    kind = (spec.get("kind") or "2d").lower()

    y = 6

    # --- plot type selector ---
    _add(dmodel, "FixedText", "lblKind", dict(
        PositionX=6, PositionY=y + 2, Width=60, Height=10,
        Label="Plot type:"))
    lb = _add(dmodel, "ListBox", "lstKind", dict(
        PositionX=68, PositionY=y, Width=156, Height=14,
        Dropdown=True, MultiSelection=False))
    # dropdown order == KIND_ORDER index
    lb.StringItemList = ("2D function", "2D parametric  x(t), y(t)",
                         "2D polar  r(θ)", "2D implicit  F(x, y) = 0",
                         "3D surface  z = f(x, y)",
                         "3D cylindrical  z = f(r, θ)",
                         "3D spherical  r = f(θ, φ)",
                         "3D parametric  x,y,z (u, v)")
    lb.SelectedItems = (KIND_ORDER.index(kind) if kind in KIND_ORDER else 0,)
    y += 20

    # --- functions / surface input (label text set by type) ---
    _add(dmodel, "FixedText", "lblFunc", dict(
        PositionX=6, PositionY=y, Width=200, Height=10,
        Label=("Surface z = f(x, y):" if kind == "3d"
               else "Functions of x (one per line):")))
    y += 12
    funcs = spec.get("functions") or []
    if isinstance(funcs, str):
        funcs = [funcs]
    if kind == "3d" and not funcs:
        f3 = spec.get("function3d")
        if f3:
            funcs = [f3]
    _add(dmodel, "Edit", "txtFunc", dict(
        PositionX=6, PositionY=y, Width=218, Height=44,
        MultiLine=True, VScroll=True,
        Text="\n".join(funcs)))

    # 3D uses a dedicated SINGLE-LINE field (no way to enter a 2nd surface).
    # Occupies the same band as txtFunc; listener shows one or the other.
    f3val = spec.get("function3d", "")
    if not f3val and kind == "3d" and funcs:
        f3val = funcs[0]
    _add(dmodel, "Edit", "txtFunc3", dict(
        PositionX=6, PositionY=y, Width=218, Height=12, Text=f3val))

    # parametric-only: up to 3 curves, x(t) and y(t) on one row each.
    # Occupies the same vertical band as txtFunc (toggled by the listener).
    # Row layout:  N  x(t)=[ ...... ]  y(t)=[ ...... ]
    _pairs = spec.get("pairs") or []
    # back-compat: single xfunc/yfunc fills row 1
    if not _pairs and (spec.get("xfunc") or spec.get("yfunc")):
        _pairs = [[spec.get("xfunc", ""), spec.get("yfunc", "")]]
    for i in range(3):
        ry = y + i * 15
        xv = _pairs[i][0] if i < len(_pairs) else ""
        yv = _pairs[i][1] if i < len(_pairs) else ""
        _add(dmodel, "FixedText", "lblPt%d" % i, dict(
            PositionX=6, PositionY=ry + 2, Width=16, Height=10,
            Label="(%d)" % (i + 1)))
        _add(dmodel, "FixedText", "lblPtx%d" % i, dict(
            PositionX=24, PositionY=ry + 2, Width=8, Height=10, Label="x"))
        _add(dmodel, "Edit", "txtXt%d" % i, dict(
            PositionX=34, PositionY=ry, Width=80, Height=12, Text=xv))
        _add(dmodel, "FixedText", "lblPty%d" % i, dict(
            PositionX=118, PositionY=ry + 2, Width=8, Height=10, Label="y"))
        _add(dmodel, "Edit", "txtYt%d" % i, dict(
            PositionX=128, PositionY=ry, Width=96, Height=12, Text=yv))

    # param-surface-only: three single-line fields x(u,v), y(u,v), z(u,v)
    # (one surface). Same band as txtFunc; listener toggles.
    for idx, (fld, lab, val) in enumerate((
            ("txtUVx", "x", spec.get("xfunc", "")),
            ("txtUVy", "y", spec.get("yfunc", "")),
            ("txtUVz", "z", spec.get("zfunc", "")))):
        ry = y + idx * 15
        _add(dmodel, "FixedText", "lblUV%d" % idx, dict(
            PositionX=6, PositionY=ry + 2, Width=22, Height=10,
            Label="%s(u,v)" % lab))
        _add(dmodel, "Edit", fld, dict(
            PositionX=36, PositionY=ry, Width=188, Height=12, Text=val))
    y += 50

    def _s(v):
        return "" if v in (None, "") else str(v)

    # parameter range (t or theta) — shown for parametric & polar
    tr = spec.get("trange")
    _add(dmodel, "FixedText", "lblParam", dict(
        PositionX=6, PositionY=y + 2, Width=30, Height=10, Label="t:"))
    _add(dmodel, "Edit", "txtTmin", dict(
        PositionX=36, PositionY=y, Width=44, Height=12,
        Text="" if not tr else _s(tr[0])))
    _add(dmodel, "Edit", "txtTmax", dict(
        PositionX=84, PositionY=y, Width=44, Height=12,
        Text="" if not tr else _s(tr[1])))
    _add(dmodel, "FixedText", "hintT", dict(
        PositionX=134, PositionY=y + 2, Width=90, Height=9,
        Label="(empty=0..2π; pi ok)"))
    y += 16

    xr = spec.get("xrange") or [-10, 10]
    yr = spec.get("yrange")
    zr = spec.get("zrange")

    # Title
    _add(dmodel, "FixedText", "lblTitle", dict(
        PositionX=6, PositionY=y + 2, Width=30, Height=10, Label="Title:"))
    _add(dmodel, "Edit", "txtTitle", dict(
        PositionX=40, PositionY=y, Width=184, Height=12,
        Text=spec.get("title", "")))
    y += 18

    # --- one row PER AXIS: label | name | min | max | major | minor ---
    # columns (fixed for all axes so everything lines up):
    #   axis letter   6..16
    #   name field   18..54   (axis title text, e.g. "x")
    #   min          58..92
    #   max          94..128
    #   major       150..184  (manual major step; empty = auto)
    #   minor       188..222  (manual minor step; empty = auto)
    axis_y0 = y  # polar block reuses this vertical band (listener toggles)
    # header row for the step columns
    _add(dmodel, "FixedText", "hdrName", dict(
        PositionX=18, PositionY=y, Width=36, Height=9, Label="name"))
    _add(dmodel, "FixedText", "hdrMin", dict(
        PositionX=58, PositionY=y, Width=34, Height=9, Label="min"))
    _add(dmodel, "FixedText", "hdrMax", dict(
        PositionX=94, PositionY=y, Width=34, Height=9, Label="max"))
    _add(dmodel, "FixedText", "hdrMaj", dict(
        PositionX=150, PositionY=y, Width=34, Height=9, Label="major"))
    _add(dmodel, "FixedText", "hdrMin2", dict(
        PositionX=188, PositionY=y, Width=34, Height=9, Label="minor"))
    y += 10

    def _axis_row(letter, name_field, name_val, minf, maxf, minv, maxv,
                  majf, majv, minrf, minrv):
        _add(dmodel, "FixedText", "lbl" + letter, dict(
            PositionX=6, PositionY=y + 2, Width=10, Height=10, Label=letter))
        _add(dmodel, "Edit", name_field, dict(
            PositionX=18, PositionY=y, Width=36, Height=12, Text=name_val))
        _add(dmodel, "Edit", minf, dict(
            PositionX=58, PositionY=y, Width=34, Height=12, Text=minv))
        _add(dmodel, "Edit", maxf, dict(
            PositionX=94, PositionY=y, Width=34, Height=12, Text=maxv))
        _add(dmodel, "Edit", majf, dict(
            PositionX=150, PositionY=y, Width=34, Height=12, Text=majv))
        _add(dmodel, "Edit", minrf, dict(
            PositionX=188, PositionY=y, Width=34, Height=12, Text=minrv))

    # X row
    _axis_row("X", "txtXL", spec.get("xlabel", "x"),
              "txtXmin", "txtXmax", _s(xr[0]), _s(xr[1]),
              "txtXMaj", _s(grid.get("x_major")),
              "txtXMin", _s(grid.get("x_minor")))
    y += 15
    # Y row
    _axis_row("Y", "txtYL", spec.get("ylabel", "y"),
              "txtYmin", "txtYmax",
              "" if not yr else _s(yr[0]), "" if not yr else _s(yr[1]),
              "txtYMaj", _s(grid.get("y_major")),
              "txtYMin", _s(grid.get("y_minor")))
    y += 15
    # Z row (3D only — visibility toggled by listener)
    _axis_row("Z", "txtZL", spec.get("zlabel", "z"),
              "txtZmin", "txtZmax",
              "" if not zr else _s(zr[0]), "" if not zr else _s(zr[1]),
              "txtZMaj", _s(grid.get("z_major")),
              "txtZMin", _s(grid.get("z_minor")))
    y += 15

    # --- POLAR block: occupies the same vertical band as the cartesian axis
    # rows above (listener shows one or the other, so no empty gap). ---
    py = axis_y0
    # row 1: r max | r step | theta step
    _add(dmodel, "FixedText", "lblRmax", dict(
        PositionX=6, PositionY=py + 2, Width=36, Height=10, Label="r max"))
    _add(dmodel, "Edit", "txtRmax", dict(
        PositionX=42, PositionY=py, Width=40, Height=12,
        Text=_s(spec.get("rmax"))))
    _add(dmodel, "FixedText", "lblRstep", dict(
        PositionX=90, PositionY=py + 2, Width=34, Height=10, Label="r step"))
    _add(dmodel, "Edit", "txtRstep", dict(
        PositionX=124, PositionY=py, Width=36, Height=12,
        Text=_s(spec.get("rstep"))))
    _add(dmodel, "FixedText", "lblThStep", dict(
        PositionX=166, PositionY=py + 2, Width=22, Height=10, Label="θ°"))
    _add(dmodel, "Edit", "txtThStep", dict(
        PositionX=188, PositionY=py, Width=36, Height=12,
        Text=_s(spec.get("theta_step"))))
    py += 18
    # row 2: theta=0 location | direction
    _add(dmodel, "FixedText", "lblZeroAt", dict(
        PositionX=6, PositionY=py + 2, Width=38, Height=10, Label="θ=0 at"))
    z0 = _add(dmodel, "ListBox", "lstZeroAt", dict(
        PositionX=46, PositionY=py, Width=56, Height=14,
        Dropdown=True, MultiSelection=False))
    z0.StringItemList = ("E (right)", "N (top)", "W (left)", "S (bottom)")
    _zmap = {"E": 0, "N": 1, "W": 2, "S": 3}
    z0.SelectedItems = (_zmap.get((spec.get("theta_zero") or "E").upper(), 0),)
    _add(dmodel, "FixedText", "lblDir", dict(
        PositionX=112, PositionY=py + 2, Width=40, Height=10,
        Label="direction"))
    dirb = _add(dmodel, "ListBox", "lstDir", dict(
        PositionX=152, PositionY=py, Width=72, Height=14,
        Dropdown=True, MultiSelection=False))
    dirb.StringItemList = ("CCW (math)", "CW (clock)")
    dirb.SelectedItems = (1 if (spec.get("theta_dir") or "ccw").lower() == "cw"
                          else 0,)
    # note: y stays at the cartesian flow; polar rows are overlaid in that band

    _add(dmodel, "FixedText", "hintAuto", dict(
        PositionX=6, PositionY=y, Width=218, Height=9,
        Label="Range/step: empty = auto"))
    y += 13

    # --- grid on/off + legend ---
    _add(dmodel, "CheckBox", "chkMajor", dict(
        PositionX=6, PositionY=y, Width=66, Height=10,
        Label="Major grid", State=1 if grid.get("major", True) else 0))
    _add(dmodel, "CheckBox", "chkMinor", dict(
        PositionX=76, PositionY=y, Width=64, Height=10,
        Label="Minor grid", State=1 if grid.get("minor", False) else 0))
    _add(dmodel, "CheckBox", "chkLegend", dict(
        PositionX=146, PositionY=y, Width=78, Height=10,
        Label="Show legend",
        State=1 if spec.get("legend", True) else 0))
    y += 16

    # --- output format: SVG (vector) vs PNG (raster) ---
    fmt_spec = (spec.get("format") or "").lower()
    want_png = (fmt_spec == "png") or (kind == "3d" and fmt_spec == "")
    _add(dmodel, "FixedText", "lblFmt", dict(
        PositionX=6, PositionY=y + 1, Width=36, Height=10, Label="Format:"))
    _add(dmodel, "RadioButton", "optSVG", dict(
        PositionX=44, PositionY=y, Width=42, Height=10,
        Label="SVG", State=0 if want_png else 1))
    _add(dmodel, "RadioButton", "optPNG", dict(
        PositionX=90, PositionY=y, Width=42, Height=10,
        Label="PNG", State=1 if want_png else 0))
    # --- 3D view angles (same row, right side; 3D only) ---
    _add(dmodel, "FixedText", "lblView", dict(
        PositionX=136, PositionY=y + 1, Width=22, Height=10, Label="view"))
    _add(dmodel, "Edit", "txtElev", dict(
        PositionX=158, PositionY=y, Width=28, Height=12,
        Text=str(spec.get("elev", 30))))
    _add(dmodel, "Edit", "txtAzim", dict(
        PositionX=190, PositionY=y, Width=28, Height=12,
        Text=str(spec.get("azim", -60))))
    y += 18

    # --- 3D colormap (3D only; visibility toggled by listener) ---
    _add(dmodel, "FixedText", "lblCmap", dict(
        PositionX=6, PositionY=y + 2, Width=50, Height=10, Label="Colormap:"))
    cm = _add(dmodel, "ListBox", "lstCmap", dict(
        PositionX=58, PositionY=y, Width=166, Height=14,
        Dropdown=True, MultiSelection=False))
    cm.StringItemList = tuple(CMAPS)
    cur_cm = (spec.get("cmap") or "viridis")
    cm.SelectedItems = (CMAPS.index(cur_cm) if cur_cm in CMAPS else 0,)
    y += 18

    # --- examples dropdown (list filled per type by the listener) ---
    _add(dmodel, "FixedText", "lblEx", dict(
        PositionX=6, PositionY=y + 2, Width=50, Height=10, Label="Example:"))
    exlb = _add(dmodel, "ListBox", "lstEx", dict(
        PositionX=58, PositionY=y, Width=166, Height=14,
        Dropdown=True, MultiSelection=False))
    y += 18

    # buttons
    from com.sun.star.awt.PushButtonType import OK as BTN_OK, CANCEL as BTN_CANCEL
    _add(dmodel, "Button", "btnInsert", dict(
        PositionX=96, PositionY=y, Width=60, Height=14,
        Label="Insert", PushButtonType=BTN_OK, DefaultButton=True))
    _add(dmodel, "Button", "btnCancel", dict(
        PositionX=162, PositionY=y, Width=60, Height=14,
        Label="Cancel", PushButtonType=BTN_CANCEL))

    dialog = _create("com.sun.star.awt.UnoControlDialog")
    dialog.setModel(dmodel)
    tk = _create("com.sun.star.awt.Toolkit")
    dialog.createPeer(tk, None)

    def _selected_kind():
        """Current plot kind from the dropdown, read robustly."""
        try:
            c = dialog.getControl("lstKind")
            try:
                pos = c.getSelectedItemPos()
            except Exception:
                pos = None
            if pos is None:
                si = c.getModel().SelectedItems
                pos = si[0] if si else 0
            pos = int(pos)
            if 0 <= pos < len(KIND_ORDER):
                return KIND_ORDER[pos]
        except Exception:
            pass
        return "2d"

    # --- type switching: show/hide controls by selected plot type ---
    def _apply_kind_visibility(k):
        def vis(name, visible):
            try:
                dialog.getControl(name).setVisible(bool(visible))
            except Exception:
                pass
        is_3d = (k == "3d")
        is_cyl = (k == "cylindrical")
        is_sph = (k == "spherical")
        is_psurf = (k == "param_surface")
        is_surf = is_3d or is_cyl or is_sph or is_psurf  # any 3D surface
        is_single_expr_surf = is_3d or is_cyl or is_sph   # one expr in txtFunc3
        is_param = (k == "parametric")
        is_polar = (k == "polar")
        is_implicit = (k == "implicit")
        uses_param_range = is_param or is_polar  # t / theta

        # input label text (for the multiline/functions field)
        labels = {
            "2d": "Functions of x (one per line):",
            "polar": "r(θ)  (one per line):",
            "implicit": "F(x, y)  (curve is F = 0; one per line):",
            "3d": "Surface z = f(x, y):",
            "cylindrical": "Surface z = f(r, θ):",
            "spherical": "Radius r = f(θ, φ):",
        }
        try:
            if not (is_param or is_psurf):
                dialog.getControl("lblFunc").getModel().Label = \
                    labels.get(k, "Functions:")
        except Exception:
            pass

        # input fields per type:
        #  - 2d / polar / implicit     → multiline txtFunc
        #  - parametric                → x(t)/y(t) rows
        #  - 3d/cylindrical/spherical  → single-line txtFunc3 (one expr)
        #  - param_surface             → x/y/z(u,v) rows
        vis("lblFunc", not (is_param or is_psurf))
        vis("txtFunc", not (is_param or is_psurf or is_single_expr_surf))
        vis("txtFunc3", is_single_expr_surf)
        for i in range(3):
            for n in ("lblPt%d" % i, "lblPtx%d" % i, "txtXt%d" % i,
                      "lblPty%d" % i, "txtYt%d" % i):
                vis(n, is_param)
        for idx in range(3):
            vis("lblUV%d" % idx, is_psurf)
        for n in ("txtUVx", "txtUVy", "txtUVz"):
            vis(n, is_psurf)

        # parameter range (t / theta) — parametric & polar only
        for n in ("lblParam", "txtTmin", "txtTmax", "hintT"):
            vis(n, uses_param_range)
        try:
            dialog.getControl("lblParam").getModel().Label = \
                ("θ:" if is_polar else "t:")
        except Exception:
            pass

        # view angles + colormap — every 3D surface
        for n in ("lblView", "txtElev", "txtAzim", "lblCmap", "lstCmap"):
            vis(n, is_surf)
        # Z axis row — only cartesian 3D surface (others have no z label/range
        # in the cartesian sense; z is derived)
        for n in ("lblZ", "txtZL", "txtZmin", "txtZmax", "txtZMaj", "txtZMin"):
            vis(n, is_3d)

        # X/Y axis rows serve as the two parameter ranges for 3D surfaces too.
        # Hidden only for polar (its own block) — shown for everything else.
        cart = not is_polar
        # relabel the two range rows for the surface types (domain meaning)
        pair_labels = {
            "cylindrical": ("r", "θ"),
            "spherical": ("θ", "φ"),
            "param_surface": ("u", "v"),
        }
        pl = pair_labels.get(k)
        try:
            dialog.getControl("lblX").getModel().Label = pl[0] if pl else "X"
            dialog.getControl("lblY").getModel().Label = pl[1] if pl else "Y"
        except Exception:
            pass
        # name/major/minor columns only make sense for true cartesian plots
        show_axis_extras = cart and (k in ("2d", "implicit", "3d"))
        for n in ("lblX", "txtXmin", "txtXmax",
                  "lblY", "txtYmin", "txtYmax", "hintAuto"):
            vis(n, cart)
        for n in ("txtXL", "txtXMaj", "txtXMin",
                  "txtYL", "txtYMaj", "txtYMin",
                  "hdrName", "hdrMin", "hdrMax", "hdrMaj", "hdrMin2"):
            vis(n, show_axis_extras)
        for n in ("lblRmax", "txtRmax", "lblRstep", "txtRstep",
                  "lblThStep", "txtThStep", "lblZeroAt", "lstZeroAt",
                  "lblDir", "lstDir"):
            vis(n, is_polar)

        # legend: off for single 3D surface, on otherwise
        # legend is useful for every type now (3D shows the function name)
        vis("chkLegend", True)

        # default format: PNG for any 3D surface (vector surfaces huge)
        if is_surf:
            try:
                dialog.getControl("optPNG").getModel().State = 1
                dialog.getControl("optSVG").getModel().State = 0
            except Exception:
                pass

    # --- per-type field memory: switching types saves the old type's fields
    # and restores the new type's, so returning never loses what was typed.
    _TYPED_TEXT = ("txtFunc", "txtFunc3", "txtUVx", "txtUVy", "txtUVz",
                   "txtXt0", "txtYt0", "txtXt1", "txtYt1", "txtXt2", "txtYt2",
                   "txtTmin", "txtTmax",
                   "txtXmin", "txtXmax", "txtYmin", "txtYmax",
                   "txtZmin", "txtZmax", "txtXL", "txtYL", "txtZL",
                   "txtXMaj", "txtXMin", "txtYMaj", "txtYMin",
                   "txtZMaj", "txtZMin", "txtElev", "txtAzim",
                   "txtRmax", "txtRstep", "txtThStep")

    def _defaults(k):
        d = {n: "" for n in _TYPED_TEXT}
        d.update({"txtXL": "x", "txtYL": "y", "txtZL": "z",
                  "txtElev": "30", "txtAzim": "-60"})
        if k == "3d":
            d["txtXmin"], d["txtXmax"] = "-5", "5"
        elif k in ("parametric", "polar"):
            pass  # t/theta range empty = 0..2π; no cartesian defaults needed
        else:  # 2d, implicit
            d["txtXmin"], d["txtXmax"] = "-10", "10"
        return d

    def _snapshot():
        return {n: (lambda c: c.getModel().Text if c else "")(
                    _safe_ctrl(n)) for n in _TYPED_TEXT}

    def _safe_ctrl(name):
        try:
            return dialog.getControl(name)
        except Exception:
            return None

    def _restore(snap):
        for n in _TYPED_TEXT:
            try:
                dialog.getControl(n).getModel().Text = snap.get(n, "")
            except Exception:
                pass

    _mem = {k: None for k in KIND_ORDER}
    _state = {"cur": kind if kind in KIND_ORDER else "2d"}
    _mem[_state["cur"]] = _snapshot()

    # --- examples: fill the Example dropdown for the current type ---
    def _fill_examples(k):
        try:
            lst = dialog.getControl("lstEx")
            items = EXAMPLES.get(k, [])
            lst.getModel().StringItemList = tuple(
                ["(choose example…)"] + [lab for (lab, _f) in items])
            lst.getModel().SelectedItems = (0,)
        except Exception:
            pass

    def _apply_example(k, idx):
        """Fill the dialog fields from EXAMPLES[k][idx-1] (idx 0 = none)."""
        items = EXAMPLES.get(k, [])
        if idx <= 0 or idx > len(items):
            return
        fields = items[idx - 1][1]

        def st(name, val):
            try:
                dialog.getControl(name).getModel().Text = val
            except Exception:
                pass
        # function input per type
        if "func" in fields:   # multiline list
            st("txtFunc", "\n".join(fields["func"]))
        if "f3" in fields:     # single surface
            st("txtFunc3", fields["f3"])
        if "pairs" in fields:  # parametric curves
            for i in range(3):
                xv = fields["pairs"][i][0] if i < len(fields["pairs"]) else ""
                yv = fields["pairs"][i][1] if i < len(fields["pairs"]) else ""
                st("txtXt%d" % i, xv)
                st("txtYt%d" % i, yv)
        if "uv" in fields:     # parametric surface
            st("txtUVx", fields["uv"][0])
            st("txtUVy", fields["uv"][1])
            st("txtUVz", fields["uv"][2])
        # ranges
        if "x" in fields:
            st("txtXmin", fields["x"][0]); st("txtXmax", fields["x"][1])
        if "y" in fields:
            st("txtYmin", fields["y"][0]); st("txtYmax", fields["y"][1])
        if "t" in fields:
            st("txtTmin", fields["t"][0]); st("txtTmax", fields["t"][1])

    class _KindListener(unohelper.Base, XItemListener):
        def itemStateChanged(self, ev):
            try:
                new_k = _selected_kind()
                old_k = _state["cur"]
                if new_k == old_k:
                    return
                _mem[old_k] = _snapshot()
                _restore(_mem[new_k] if _mem[new_k] is not None
                         else _defaults(new_k))
                _state["cur"] = new_k
                _apply_kind_visibility(new_k)
                _fill_examples(new_k)
            except Exception:
                pass

        def disposing(self, ev):
            pass

    class _ExListener(unohelper.Base, XItemListener):
        def itemStateChanged(self, ev):
            try:
                lst = dialog.getControl("lstEx")
                sel = lst.getSelectedItemPos()
                _apply_example(_state["cur"], int(sel))
            except Exception:
                pass

        def disposing(self, ev):
            pass

    try:
        dialog.getControl("lstKind").addItemListener(_KindListener())
    except Exception:
        pass
    try:
        dialog.getControl("lstEx").addItemListener(_ExListener())
    except Exception:
        pass
    _apply_kind_visibility(kind if kind in KIND_ORDER else "2d")
    _fill_examples(kind if kind in KIND_ORDER else "2d")

    return dialog, dmodel


def _read_dialog(dialog):
    """Collect the dialog fields into a spec dict."""
    def g(name):
        return dialog.getControl(name).getModel().Text

    def gnum(name, default=None):
        t = g(name).strip().replace(",", ".")
        if t == "":
            return default
        v = _parse_num(t)
        return default if v is None else v

    def gchk(name):
        return bool(dialog.getControl(name).getModel().State)

    def chosen_format():
        try:
            if bool(dialog.getControl("optPNG").getModel().State):
                return "png"
        except Exception:
            pass
        return "svg"

    def _selected_cmap():
        try:
            c = dialog.getControl("lstCmap")
            it = c.getSelectedItem()
            if it:
                return it
            pos = c.getSelectedItemPos()
            if 0 <= int(pos) < len(CMAPS):
                return CMAPS[int(pos)]
        except Exception:
            pass
        return "viridis"

    # plot type from dropdown — read robustly (index in KIND_ORDER, text check)
    kind = "2d"
    try:
        ctrl = dialog.getControl("lstKind")
        sel = None
        try:
            sel = ctrl.getSelectedItemPos()
        except Exception:
            sel = None
        if sel is None:
            si = ctrl.getModel().SelectedItems
            sel = si[0] if si else 0
        sel = int(sel)
        if 0 <= sel < len(KIND_ORDER):
            kind = KIND_ORDER[sel]
    except Exception:
        kind = "2d"

    funcs = [ln.strip() for ln in g("txtFunc").splitlines() if ln.strip()]
    xmin = gnum("txtXmin", None)
    xmax = gnum("txtXmax", None)
    ymin = gnum("txtYmin", None)
    ymax = gnum("txtYmax", None)
    tmin = gnum("txtTmin", None)
    tmax = gnum("txtTmax", None)
    trange = [tmin, tmax] if (tmin is not None and tmax is not None) else None

    common = {
        "title": g("txtTitle"),
        "xlabel": g("txtXL"),
        "ylabel": g("txtYL"),
        "grid": {
            "major": gchk("chkMajor"),
            "minor": gchk("chkMinor"),
            "x_major": gnum("txtXMaj", None),
            "x_minor": gnum("txtXMin", None),
            "y_major": gnum("txtYMaj", None),
            "y_minor": gnum("txtYMin", None),
            "z_major": gnum("txtZMaj", None),
            "z_minor": gnum("txtZMin", None),
        },
        "format": chosen_format(),
    }

    xr2 = [xmin, xmax] if (xmin is not None and xmax is not None) else None
    yr2 = [ymin, ymax] if (ymin is not None and ymax is not None) else None

    if kind in ("3d", "cylindrical", "spherical", "param_surface"):
        spec = dict(common)
        spec.update({
            "kind": kind,
            "xrange": xr2, "yrange": yr2,
            "elev": gnum("txtElev", 30), "azim": gnum("txtAzim", -60),
            "legend": gchk("chkLegend"),
            "cmap": _selected_cmap(),
            "dpi": 120,
        })
        if kind == "param_surface":
            spec.update({
                "xfunc": g("txtUVx").strip(),
                "yfunc": g("txtUVy").strip(),
                "zfunc": g("txtUVz").strip(),
            })
        else:
            spec["function3d"] = g("txtFunc3").strip()
        if kind == "3d":
            zmin = gnum("txtZmin", None)
            zmax = gnum("txtZmax", None)
            spec["xrange"] = xr2 or [-5.0, 5.0]
            spec["zrange"] = [zmin, zmax] if (zmin is not None
                                              and zmax is not None) else None
            spec["zlabel"] = g("txtZL")
        return spec

    if kind == "parametric":
        pairs = []
        for i in range(3):
            xv = g("txtXt%d" % i).strip()
            yv = g("txtYt%d" % i).strip()
            if xv and yv:
                pairs.append([xv, yv])
        spec = dict(common)
        spec.update({
            "kind": "parametric",
            "pairs": pairs,
            "trange": trange,
            "xrange": xr2, "yrange": yr2,
            "legend": gchk("chkLegend"),
        })
        return spec

    if kind == "polar":
        # orientation dropdowns
        zmap = ["E", "N", "W", "S"]
        try:
            zp = dialog.getControl("lstZeroAt").getSelectedItemPos()
            theta_zero = zmap[int(zp)] if 0 <= int(zp) < 4 else "E"
        except Exception:
            theta_zero = "E"
        try:
            dp = dialog.getControl("lstDir").getSelectedItemPos()
            theta_dir = "cw" if int(dp) == 1 else "ccw"
        except Exception:
            theta_dir = "ccw"
        spec = dict(common)
        spec.update({
            "kind": "polar",
            "functions": funcs,
            "trange": trange,
            "rmax": gnum("txtRmax", None),
            "rstep": gnum("txtRstep", None),
            "theta_step": gnum("txtThStep", None),
            "theta_zero": theta_zero,
            "theta_dir": theta_dir,
            "legend": gchk("chkLegend"),
        })
        return spec

    if kind == "implicit":
        spec = dict(common)
        spec.update({
            "kind": "implicit",
            "functions": funcs,
            "xrange": xr2 or [-10.0, 10.0],
            "yrange": yr2,
            "legend": gchk("chkLegend"),
        })
        return spec

    # 2D function (default)
    spec = dict(common)
    spec.update({
        "kind": "2d",
        "functions": funcs,
        "xrange": xr2 or [-10.0, 10.0],
        "yrange": yr2,
        "legend": gchk("chkLegend"),
    })
    return spec


# ---- builder invocation --------------------------------------------------
def _run_builder(spec):
    """Call external python3 plotbuilder.py with spec on stdin. Returns the
    report dict. Raises RuntimeError with a readable message on failure."""
    tmpdir = tempfile.mkdtemp(prefix="plotbridge_")
    out_base = os.path.join(tmpdir, "plot")
    spec = dict(spec)
    spec.pop("n_lines", None)   # UI-only helper, not for the builder
    spec["out"] = out_base

    try:
        kw = {}
        # LibreOffice postavlja PYTHONHOME/PYTHONPATH na svoj ugrađeni Python;
        # vanjski Python to ne smije naslijediti ("SRE module mismatch").
        env = dict(os.environ)
        for k in list(env):
            if k.upper() in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP",
                             "PYTHONEXECUTABLE", "PYTHONUSERBASE",
                             "__PYVENV_LAUNCHER__"):
                env.pop(k, None)
        kw["env"] = env
        if sys.platform.startswith("win"):
            kw["creationflags"] = 0x08000000   # CREATE_NO_WINDOW
        # SYSTEM_PYTHON može biti str ("/usr/bin/python3") ili argv lista
        # (npr. ["py", "-3"] na Windowsu) — izgradi poziv za oba slučaja.
        py = SYSTEM_PYTHON if isinstance(SYSTEM_PYTHON, (list, tuple)) \
            else [SYSTEM_PYTHON]
        argv = list(py) + [PLOTBUILDER]
        proc = subprocess.run(
            argv,
            input=json.dumps(spec).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=120, **kw,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "System Python not found (%s). Set PLOTBRIDGE_PYTHON or WINDOWS_PYTHON in plotbridge.py, "
            "and make sure it has sympy, numpy and matplotlib installed."
            % (SYSTEM_PYTHON,))
    except subprocess.TimeoutExpired:
        raise RuntimeError("Plot builder timed out (120 s).")

    out = (proc.stdout or b"").decode("utf-8", "replace").strip()
    err = (proc.stderr or b"").decode("utf-8", "replace").strip()
    if not out:
        raise RuntimeError("Builder produced no output.\n%s" % err)
    try:
        report = json.loads(out.splitlines()[-1])
    except Exception:
        raise RuntimeError("Could not parse builder output:\n%s\n%s" % (out, err))
    if not report.get("ok"):
        raise RuntimeError(report.get("error", "Unknown builder error."))
    return report


# ---- plot identification --------------------------------------------------
PLOT_TITLE = "PlotBridge"


def _shape_spec(shape):
    """If the shape is one of our plots, return its stored spec dict, else None."""
    try:
        if (getattr(shape, "Title", "") or "") != PLOT_TITLE:
            return None
        desc = getattr(shape, "Description", "") or ""
        if not desc:
            return None
        return json.loads(desc)
    except Exception:
        return None


def _selected_plot(doc):
    """Return (shape, spec) for a selected Plot Bridge image, else (None, None)."""
    try:
        sel = doc.CurrentController.Selection
    except Exception:
        return None, None
    if sel is None:
        return None, None
    # selection may be a shape collection or a single shape
    shapes = []
    try:
        if hasattr(sel, "getCount") and hasattr(sel, "getByIndex"):
            for i in range(sel.getCount()):
                shapes.append(sel.getByIndex(i))
        else:
            shapes.append(sel)
    except Exception:
        try:
            shapes.append(sel)
        except Exception:
            return None, None
    for sh in shapes:
        spec = _shape_spec(sh)
        if spec is not None:
            return sh, spec
    return None, None


# ---- image insertion -----------------------------------------------------
def _insert_svg(doc, svg_path, spec, replace_shape=None):
    """Insert the SVG as a GraphicObject and stash the spec JSON on the shape
    for later re-edit. If replace_shape is given (an existing Plot Bridge image),
    the new plot takes its position/size and the old one is removed."""
    url = unohelper.systemPathToFileUrl(os.path.abspath(svg_path))

    gp = _create("com.sun.star.graphic.GraphicProvider")
    from com.sun.star.beans import PropertyValue
    pv = PropertyValue()
    pv.Name = "URL"
    pv.Value = url
    graphic = gp.queryGraphic((pv,))

    from com.sun.star.awt import Size as AwtSize
    # native size (100th mm); if replacing, prefer the old shape's size
    sz = None
    if replace_shape is not None:
        try:
            old = replace_shape.Size
            sz = AwtSize(old.Width, old.Height)
        except Exception:
            sz = None
    if sz is None:
        try:
            size100 = graphic.getPropertyValue("Size100thMM")
            sz = AwtSize(size100.Width or 16000, size100.Height or 10000)
        except Exception:
            sz = AwtSize(16000, 10000)

    _store = dict(spec)
    _store.pop("n_lines", None)   # UI-only helper, don't persist on the image
    spec_json = json.dumps(_store, ensure_ascii=False)

    if _is_text(doc):
        img = doc.createInstance("com.sun.star.text.GraphicObject")
        img.Graphic = graphic
        img.Width = sz.Width
        img.Height = sz.Height
        try:
            img.Title = PLOT_TITLE
            img.Description = spec_json
        except Exception:
            pass

        def _text_insert_pos():
            """Return a usable text position for insertion. The ViewCursor is
            not in the text flow when a graphic/caption is selected (then
            insertTextContent raises "text interface and cursor not related"),
            so fall back to a text cursor at the current range, else at the
            document end."""
            # 1) try the view cursor's text range (works when caret is in text)
            try:
                vc = doc.CurrentController.ViewCursor
                rng = vc.getText().createTextCursorByRange(vc)
                # sanity: the range's text must be the document body text
                if rng.getText() is doc.Text or rng.getText() == doc.Text:
                    return rng
            except Exception:
                pass
            # 2) fall back to the end of the document body
            try:
                c = doc.Text.createTextCursor()
                c.gotoEnd(False)
                return c
            except Exception:
                return None

        if replace_shape is not None:
            # copy anchor, then insert next to the old one and remove it
            try:
                img.AnchorType = replace_shape.AnchorType
            except Exception:
                img.AnchorType = uno.Enum(
                    "com.sun.star.text.TextContentAnchorType", "AS_CHARACTER")
            inserted = False
            try:
                anchor = replace_shape.Anchor  # text range of the old image
                doc.Text.insertTextContent(anchor, img, False)
                inserted = True
            except Exception:
                pos = _text_insert_pos()
                if pos is not None:
                    doc.Text.insertTextContent(pos, img, False)
                    inserted = True
            if inserted:
                try:
                    doc.Text.removeTextContent(replace_shape)
                except Exception:
                    pass
        else:
            img.AnchorType = uno.Enum(
                "com.sun.star.text.TextContentAnchorType", "AS_CHARACTER")
            pos = _text_insert_pos()
            if pos is None:
                raise RuntimeError(
                    "Could not find a text position to insert at. "
                    "Click in the document text (not on an image) and try again.")
            doc.Text.insertTextContent(pos, img, False)
    else:
        # Draw/Impress/Calc drawing layer
        img = doc.createInstance("com.sun.star.drawing.GraphicObjectShape")
        img.Graphic = graphic
        img.Size = sz
        try:
            img.Title = PLOT_TITLE
            img.Description = spec_json
        except Exception:
            pass
        page = doc.CurrentController.CurrentPage \
            if hasattr(doc.CurrentController, "CurrentPage") else \
            doc.DrawPages.getByIndex(0)
        if replace_shape is not None:
            try:
                img.Position = replace_shape.Position
            except Exception:
                pass
        page.add(img)
        if replace_shape is not None:
            try:
                page.remove(replace_shape)
            except Exception:
                pass


def _is_text(doc):
    return doc.supportsService("com.sun.star.text.TextDocument")


# ---- main action ---------------------------------------------------------
def _do_insert():
    """Single entry point. If a Plot Bridge image is selected, prefill the
    dialog from its stored spec and replace it on Insert (edit mode).
    Otherwise open an empty dialog and insert a new plot at the cursor."""
    doc = _desktop().getCurrentComponent()
    if doc is None:
        return
    parent_peer = doc.CurrentController.Frame.ContainerWindow

    # edit mode? (selected existing plot)
    replace_shape, prefill_spec = _selected_plot(doc)

    dialog, _dmodel = _build_dialog(prefill_spec)
    try:
        ok = dialog.execute()  # 1 = OK/Insert, 0 = Cancel
        if ok != 1:
            return
        spec = _read_dialog(dialog)
    finally:
        dialog.dispose()

    # validate the right input field per plot type
    k = (spec.get("kind") or "2d").lower()
    if k in ("3d", "cylindrical", "spherical"):
        if not (spec.get("function3d") or "").strip():
            msgs = {"3d": "Please enter a surface z = f(x, y).",
                    "cylindrical": "Please enter a surface z = f(r, θ).",
                    "spherical": "Please enter a radius r = f(θ, φ)."}
            _msgbox(parent_peer, msgs.get(k, "Please enter the surface."))
            return
    elif k == "param_surface":
        if not (spec.get("xfunc") and spec.get("yfunc") and spec.get("zfunc")):
            _msgbox(parent_peer,
                    "Please enter all three: x(u,v), y(u,v) and z(u,v).")
            return
    elif k == "parametric":
        if not spec.get("pairs"):
            _msgbox(parent_peer,
                    "Please enter at least one curve: both x(t) and y(t).")
            return
    elif k == "polar":
        if not spec.get("functions"):
            _msgbox(parent_peer, "Please enter r(θ).")
            return
    elif k == "implicit":
        if not spec.get("functions"):
            _msgbox(parent_peer, "Please enter F(x, y)  (the curve is F = 0).")
            return
    else:
        if not spec.get("functions"):
            _msgbox(parent_peer, "Please enter at least one function of x.")
            return

    try:
        report = _run_builder(spec)
    except Exception as e:
        _msgbox(parent_peer, str(e))
        return

    try:
        _insert_svg(doc, report["path"], spec, replace_shape=replace_shape)
    except Exception as e:
        _msgbox(parent_peer, "Insert failed:\n%s" % e)
        return


# ---- UNO component plumbing ----------------------------------------------
class PlotBridge(unohelper.Base, XJobExecutor):
    def __init__(self, ctx):
        self.ctx = ctx

    def trigger(self, arg):
        # arg is the part after "?": "insert" handles both new and edit modes
        try:
            _do_insert()
        except Exception as e:
            try:
                doc = _desktop().getCurrentComponent()
                peer = doc.CurrentController.Frame.ContainerWindow
                _msgbox(peer, "Plot Bridge error:\n%s" % e)
            except Exception:
                pass


# registration
g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    PlotBridge, IMPL_NAME, (IMPL_NAME,),)

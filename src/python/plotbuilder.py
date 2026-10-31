#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# Plot Bridge — plotbuilder (external builder).
# Copyright (C) 2026 mdoksa76
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See <https://www.gnu.org/licenses/>.
"""
plotbuilder.py — plot builder za LibreOffice "plot most".

Koristi symplot jezgru (sympy parser s implicitnim množenjem, singulariteti,
derivacije, nultočke, ekstremi) za obradu izraza, a crta kroz matplotlib
u SVG/PNG. Ne ovisi o LibreOfficeu — poziva se kao vanjski proces iz .oxt
omotača, a može se i samostalno testirati iz terminala.

Struktura: dvije "jezgre" dijele zajednička svojstva unutar svoje dimenzije,
a svaki tip (kind) je tanka nadogradnja na pripadnu jezgru:
    core2d() — zajedničko za sve 2D tipove (osi, grid, naslov, legenda)
    core3d() — zajedničko za sve 3D tipove (tri osi, pogled, naslov)
    build_2d()      — 2D funkcije (kind "2d", default)
    build_3d()      — 3D ploha z = f(x, y) (kind "3d")
Dispatcher po spec["kind"] bira tip.

ULAZ:  JSON spec (stdin ili --spec datoteka)
IZLAZ: SVG/PNG datoteka + JSON izvještaj na stdout

Spec (JSON) — zajednička polja (sva s defaultima):
{
  "kind": "2d",                   # "2d" | "3d"
  "title": "", "format": "svg",   # "svg" | "png"
  "width": 16.0, "height": 10.0,  # cm
  "dpi": 150,
  "grid": { "major": true, "minor": false,
            "x_major": null, "x_minor": null,
            "y_major": null, "y_minor": null },
  "out": "/putanja/bez/ekstenzije"
}

2D dodatno:
  "functions": ["sin(x)", "cos(x)"],   # 1+ izraza u x
  "xrange": [-10, 10], "yrange": null, # null = auto
  "samples": 800, "xlabel": "x", "ylabel": "y", "legend": true,
  "analyze": { "roots":false,"extrema":false,"singularities":false,"derivatives":0 }

3D dodatno:
  "function3d": "sin(sqrt(x^2+y^2))",  # jedan izraz u x i y
  "xrange": [-5,5], "yrange": [-5,5],  # domena (obje potrebne)
  "samples": 60,                       # mreža samples x samples
  "xlabel":"x","ylabel":"y","zlabel":"z",
  "cmap": "viridis",                   # matplotlib colormap
  "elev": 30, "azim": -60,             # kut gledanja
  "colorbar": true

REPORT (stdout JSON): {"ok": true, "path": "...", "format": "svg", "n": N}
                      {"ok": false, "error": "..."}
"""

import sys
import os
import json
import argparse

import numpy as np

# matplotlib bez GUI backenda
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator, AutoMinorLocator
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  (registrira 3d projekciju)

# ---- symplot jezgra ------------------------------------------------------
_SYMPLOT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SYMPLOT_DIR not in sys.path:
    sys.path.insert(0, _SYMPLOT_DIR)

try:
    import sympy as sp
    HAVE_SYMPY = True
except Exception:
    HAVE_SYMPY = False

_sym = None
try:
    import symplot as _sym  # noqa: F401
except Exception:
    _sym = None


if HAVE_SYMPY:
    from sympy.parsing.sympy_parser import (
        parse_expr, standard_transformations,
        implicit_multiplication_application, convert_xor,
    )
    _TRANSFORMS = standard_transformations + (
        implicit_multiplication_application, convert_xor,
    )
    _X = sp.Symbol("x", real=True)
    _Y = sp.Symbol("y", real=True)
    _T = sp.Symbol("t", real=True)      # parametar za parametarske krivulje
    _TH = sp.Symbol("theta", real=True)  # kut za polarne (alias: t)
    _R = sp.Symbol("r", real=True)       # radijus (cilindrični)
    _PHI = sp.Symbol("phi", real=True)   # azimutalni kut (sferni)
    _U = sp.Symbol("u", real=True)       # parametri parametarske plohe
    _V = sp.Symbol("v", real=True)


def _symbol_locals():
    """Lokalni simboli za parser (x, y, t, theta + standardne funkcije)."""
    local = {"x": _X, "y": _Y, "t": _T, "theta": _TH, "r": _R, "phi": _PHI,
             "u": _U, "v": _V}
    for nm in ("pi", "E", "oo", "I"):
        if hasattr(sp, nm):
            local[nm] = getattr(sp, nm)
    for nm in ("sin", "cos", "tan", "asin", "acos", "atan", "atan2",
               "sinh", "cosh", "tanh", "asinh", "acosh", "atanh",
               "exp", "log", "sqrt", "Abs", "sign", "floor", "ceiling",
               "gamma", "zeta", "erf", "erfc", "besselj", "bessely",
               "lambertw", "factorial", "binomial", "Min", "Max",
               "re", "im", "conjugate", "sec", "csc", "cot"):
        if hasattr(sp, nm):
            local[nm] = getattr(sp, nm)
    local["abs"] = sp.Abs
    local["ln"] = sp.log
    local["log10"] = lambda v: sp.log(v) / sp.log(10)
    return local


def _parse(expr_str, allowed):
    """Parse izraz, dopusti samo simbole iz 'allowed' (set sympy simbola)."""
    if not HAVE_SYMPY:
        raise RuntimeError("sympy is required but not available")
    # za 1D x koristi symplot.sym_parse ako postoji (implicitno množenje itd.)
    if allowed == {_X} and _sym is not None and hasattr(_sym, "sym_parse"):
        e = _sym.sym_parse(expr_str)
        if e is None:
            raise ValueError("Cannot parse expression: %s" % expr_str)
    else:
        e = parse_expr(expr_str, local_dict=_symbol_locals(),
                       transformations=_TRANSFORMS, evaluate=True)
    free = e.free_symbols - allowed
    if free:
        names = ", ".join(sorted(str(f) for f in free))
        allow = ", ".join(sorted(str(a) for a in allowed))
        raise ValueError("Unknown symbol(s): %s (allowed: %s)" % (names, allow))
    return e


# --- analiza (temelj za buduće značajke, samo 2D) -------------------------
def analyze_expr(expr, xmin, xmax, opts):
    info = {}
    if not HAVE_SYMPY:
        return info
    want_d = int(opts.get("derivatives", 0) or 0)
    if want_d >= 1:
        try:
            info["d1"] = sp.sstr(sp.simplify(sp.diff(expr, _X)))
        except Exception:
            pass
    if want_d >= 2:
        try:
            info["d2"] = sp.sstr(sp.simplify(sp.diff(expr, _X, 2)))
        except Exception:
            pass
    for key, fn in (("roots", "sym_roots"),
                    ("extrema", "sym_extrema"),
                    ("singularities", "sym_singularities")):
        if opts.get(key):
            try:
                if _sym is not None and hasattr(_sym, fn):
                    info[key] = getattr(_sym, fn)(expr, xmin, xmax)
                else:
                    info[key] = []
            except Exception:
                info[key] = []
    return info


# ==========================================================================
# ZAJEDNIČKE JEZGRE
# ==========================================================================
def _figsize(spec):
    return (float(spec.get("width", 16.0)) / 2.54,
            float(spec.get("height", 10.0)) / 2.54)


def _out_path(spec, fmt):
    out = spec.get("out")
    if not out:
        raise ValueError("Missing 'out' (output path without extension).")
    return out if out.lower().endswith("." + fmt) else out + "." + fmt


def _save(fig, spec, fmt):
    save_kw = {"format": fmt}
    if fmt == "png":
        save_kw["dpi"] = int(spec.get("dpi", 150))
    path = _out_path(spec, fmt)
    fig.savefig(path, **save_kw)
    plt.close(fig)
    return path


def core2d(ax, spec):
    """Zajedničko za sve 2D tipove: naslov, osi-labele, grid, osi kroz ishodište."""
    title = spec.get("title") or ""
    if title:
        ax.set_title(title)
    xlabel = spec.get("xlabel", "x")
    ylabel = spec.get("ylabel", "y")
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)

    grid = spec.get("grid") or {}
    major = bool(grid.get("major", True))
    minor = bool(grid.get("minor", False))
    xmaj, xmin_ = grid.get("x_major"), grid.get("x_minor")
    ymaj, ymin_ = grid.get("y_major"), grid.get("y_minor")
    if xmaj:
        ax.xaxis.set_major_locator(MultipleLocator(float(xmaj)))
    if ymaj:
        ax.yaxis.set_major_locator(MultipleLocator(float(ymaj)))
    if minor:
        ax.xaxis.set_minor_locator(
            MultipleLocator(float(xmin_)) if xmin_ else AutoMinorLocator())
        ax.yaxis.set_minor_locator(
            MultipleLocator(float(ymin_)) if ymin_ else AutoMinorLocator())

    # grid i osi IZA krivulja (da linije nikad ne izgledaju "rezane")
    ax.set_axisbelow(True)
    if major:
        ax.grid(which="major", linewidth=0.6, alpha=0.65, color="#888")
    if minor:
        # minor must be a touch more visible than before, and dashed so it
        # reads clearly as the finer grid even on screen
        ax.grid(which="minor", linewidth=0.4, alpha=0.5,
                color="#aaa", linestyle=":")

    ax.axhline(0, color="black", linewidth=0.8)
    ax.axvline(0, color="black", linewidth=0.8)


def core3d(ax, spec):
    """Zajedničko za sve 3D tipove: naslov, tri osi-labele, grid, kut gledanja."""
    title = spec.get("title") or ""
    if title:
        ax.set_title(title)
    ax.set_xlabel(spec.get("xlabel", "x"))
    ax.set_ylabel(spec.get("ylabel", "y"))
    ax.set_zlabel(spec.get("zlabel", "z"))

    # major/minor grid na sve tri osi (3D podržava major; minor preko locatora)
    grid = spec.get("grid") or {}
    major = bool(grid.get("major", True))
    minor = bool(grid.get("minor", False))
    for axis, mj, mn in ((ax.xaxis, grid.get("x_major"), grid.get("x_minor")),
                         (ax.yaxis, grid.get("y_major"), grid.get("y_minor")),
                         (ax.zaxis, grid.get("z_major"), grid.get("z_minor"))):
        if mj:
            axis.set_major_locator(MultipleLocator(float(mj)))
        if minor:
            axis.set_minor_locator(
                MultipleLocator(float(mn)) if mn else AutoMinorLocator())
    ax.grid(bool(major or minor))

    elev = spec.get("elev", 30)
    azim = spec.get("azim", -60)
    try:
        ax.view_init(elev=float(elev), azim=float(azim))
    except Exception:
        pass


# ==========================================================================
# TIPOVI (tanke nadogradnje na jezgru)
# ==========================================================================
def _break_asymptotes(xs, ys):
    """Insert NaN where the curve jumps across a vertical asymptote, so the
    plot doesn't draw a spurious near-vertical connecting line (1/x, tan x…).
    Heuristic: a sign change in y combined with a very steep local slope."""
    ys = np.asarray(ys, dtype=float).copy()
    if ys.size < 3:
        return ys
    dy = np.abs(np.diff(ys))
    finite = dy[np.isfinite(dy)]
    if finite.size == 0:
        return ys
    # threshold: much larger than the typical step
    med = np.median(finite[finite > 0]) if np.any(finite > 0) else 0.0
    thr = max(med * 50.0, (np.nanmax(ys) - np.nanmin(ys)) * 0.5 if
              np.isfinite(np.nanmax(ys)) and np.isfinite(np.nanmin(ys)) else 0.0)
    if thr <= 0:
        return ys
    for i in range(ys.size - 1):
        a, b = ys[i], ys[i + 1]
        if np.isfinite(a) and np.isfinite(b):
            # straddles a pole: opposite signs and a huge jump
            if a * b < 0 and abs(b - a) > thr:
                ys[i + 1] = np.nan
    return ys


def build_2d(spec):
    funcs = spec.get("functions") or []
    if isinstance(funcs, str):
        funcs = [funcs]
    funcs = [s for s in (f.strip() for f in funcs) if s]
    if not funcs:
        raise ValueError("No expression to plot (functions is empty).")

    xr = spec.get("xrange") or [-10.0, 10.0]
    xmin, xmax = float(xr[0]), float(xr[1])
    if not (xmax > xmin):
        raise ValueError("Invalid X range: xmax must be greater than xmin.")
    yr = spec.get("yrange")
    samples = max(50, min(int(spec.get("samples", 800)), 20000))
    legend = bool(spec.get("legend", True))
    analyze = spec.get("analyze") or {}
    fmt = (spec.get("format") or "svg").lower()
    if fmt not in ("svg", "png"):
        raise ValueError("format must be 'svg' or 'png'.")

    fig, ax = plt.subplots(figsize=_figsize(spec))
    xs = np.linspace(xmin, xmax, samples)

    analysis = {}
    n_ok = 0
    for s in funcs:
        expr = _parse(s, {_X})
        f = sp.lambdify(_X, expr, modules=["numpy"])
        with np.errstate(all="ignore"):
            ys = np.asarray(f(xs), dtype=float)
            ys = np.where(np.isfinite(ys), ys, np.nan)
        # break the line across vertical asymptotes (e.g. 1/x, tan x):
        # where the slope between neighbours is huge AND the values straddle
        # a pole, insert NaN so matplotlib doesn't draw a fake vertical line
        ys = _break_asymptotes(xs, ys)
        ax.plot(xs, ys, label=s, linewidth=1.6)
        n_ok += 1
        if any(analyze.get(k) for k in ("roots", "extrema", "singularities")) \
                or analyze.get("derivatives"):
            analysis[s] = analyze_expr(expr, xmin, xmax, analyze)

    ax.set_xlim(xmin, xmax)
    if yr:
        ax.set_ylim(float(yr[0]), float(yr[1]))

    core2d(ax, spec)
    if legend and n_ok > 0:
        ax.legend(loc="best", fontsize="small")

    fig.tight_layout()
    path = _save(fig, spec, fmt)
    report = {"ok": True, "path": path, "format": fmt, "n": n_ok}
    if analysis:
        report["analysis"] = analysis
    return report


def _trange(spec, default):
    """Parametarski raspon (t ili theta) iz spec['trange']; default ako prazno."""
    tr = spec.get("trange")
    if tr and tr[0] is not None and tr[1] is not None:
        tmin, tmax = float(tr[0]), float(tr[1])
    else:
        tmin, tmax = default
    if not (tmax > tmin):
        raise ValueError("Invalid parameter range: max must be greater than min.")
    return tmin, tmax


def build_parametric(spec):
    """2D parametarska krivulja x(t), y(t). Prima parove u 'functions' kao
    redove "x_expr ; y_expr" (ili dva odvojena polja preko 'xfunc'/'yfunc')."""
    pairs = []
    # preferirano: lista parova [[x,y], ...]
    for p in (spec.get("pairs") or []):
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            a, b = str(p[0]).strip(), str(p[1]).strip()
            if a and b:
                pairs.append((a, b))
    # back-compat: jedan xfunc/yfunc
    xf = (spec.get("xfunc") or "").strip()
    yf = (spec.get("yfunc") or "").strip()
    if xf and yf:
        pairs.append((xf, yf))
    # back-compat: "x ; y" redovi u functions
    for ln in (spec.get("functions") or []):
        if isinstance(ln, str) and ";" in ln:
            a, b = ln.split(";", 1)
            if a.strip() and b.strip():
                pairs.append((a.strip(), b.strip()))
    if not pairs:
        raise ValueError("Enter at least one curve: both x(t) and y(t).")

    tmin, tmax = _trange(spec, (0.0, 2 * np.pi))
    samples = max(50, min(int(spec.get("samples", 1000)), 40000))
    legend = bool(spec.get("legend", True))
    fmt = (spec.get("format") or "svg").lower()
    if fmt not in ("svg", "png"):
        raise ValueError("format must be 'svg' or 'png'.")

    ts = np.linspace(tmin, tmax, samples)
    fig, ax = plt.subplots(figsize=_figsize(spec))
    n_ok = 0
    for (xs_expr, ys_expr) in pairs:
        ex = _parse(xs_expr, {_T})
        ey = _parse(ys_expr, {_T})
        fx = sp.lambdify(_T, ex, modules=["numpy"])
        fy = sp.lambdify(_T, ey, modules=["numpy"])
        with np.errstate(all="ignore"):
            xv = np.broadcast_to(np.asarray(fx(ts), float), ts.shape)
            yv = np.broadcast_to(np.asarray(fy(ts), float), ts.shape)
            xv = np.where(np.isfinite(xv), xv, np.nan)
            yv = np.where(np.isfinite(yv), yv, np.nan)
        ax.plot(xv, yv, label="(%s, %s)" % (xs_expr, ys_expr), linewidth=1.6)
        n_ok += 1

    xr = spec.get("xrange")
    yr = spec.get("yrange")
    if xr and xr[0] is not None and xr[1] is not None:
        ax.set_xlim(float(xr[0]), float(xr[1]))
    if yr and yr[0] is not None and yr[1] is not None:
        ax.set_ylim(float(yr[0]), float(yr[1]))
    ax.set_aspect("equal", adjustable="datalim")  # krivulje bez izobličenja

    core2d(ax, spec)
    if legend and n_ok > 0:
        ax.legend(loc="best", fontsize="small")
    fig.tight_layout()
    path = _save(fig, spec, fmt)
    return {"ok": True, "path": path, "format": fmt, "n": n_ok}


def build_polar(spec):
    """2D polarna krivulja r(theta). Prima izraze u 'functions' (jedan po redu),
    varijabla theta (alias t). Crta se na polarnoj projekciji."""
    funcs = spec.get("functions") or []
    if isinstance(funcs, str):
        funcs = [funcs]
    funcs = [s for s in (f.strip() for f in funcs) if s]
    if not funcs:
        raise ValueError("Enter r(theta).")

    tmin, tmax = _trange(spec, (0.0, 2 * np.pi))
    samples = max(100, min(int(spec.get("samples", 1500)), 40000))
    legend = bool(spec.get("legend", True))
    fmt = (spec.get("format") or "svg").lower()
    if fmt not in ("svg", "png"):
        raise ValueError("format must be 'svg' or 'png'.")

    th = np.linspace(tmin, tmax, samples)
    fig = plt.figure(figsize=_figsize(spec))
    ax = fig.add_subplot(111, projection="polar")
    n_ok = 0
    for s in funcs:
        # dopusti i 'theta' i 't' kao varijablu kuta
        expr = _parse(s, {_TH, _T})
        var = _TH if _TH in expr.free_symbols or not expr.free_symbols else \
            (_T if _T in expr.free_symbols else _TH)
        f = sp.lambdify(var, expr, modules=["numpy"])
        with np.errstate(all="ignore"):
            r = np.broadcast_to(np.asarray(f(th), float), th.shape)
            r = np.where(np.isfinite(r), r, np.nan)
        ax.plot(th, r, label=s, linewidth=1.6)
        n_ok += 1

    # --- orientation (default: 0 at right/East, counter-clockwise) ---
    zero_loc = (spec.get("theta_zero") or "E").upper()
    if zero_loc in ("E", "N", "W", "S"):
        try:
            ax.set_theta_zero_location(zero_loc)
        except Exception:
            pass
    direction = (spec.get("theta_dir") or "ccw").lower()
    try:
        ax.set_theta_direction(-1 if direction == "cw" else 1)
    except Exception:
        pass

    # --- radial scale control (avoid an over-cramped scale) ---
    rmax = spec.get("rmax")
    if rmax not in (None, ""):
        try:
            ax.set_rmax(float(rmax))
        except Exception:
            pass
    rstep = spec.get("rstep")
    if rstep not in (None, ""):
        try:
            top = float(rmax) if rmax not in (None, "") else ax.get_rmax()
            ticks = np.arange(float(rstep), top + 1e-9, float(rstep))
            if len(ticks) > 0:
                ax.set_rticks(ticks)
        except Exception:
            pass
    thstep = spec.get("theta_step")  # degrees between angular labels
    if thstep not in (None, ""):
        try:
            ax.set_thetagrids(np.arange(0, 360, float(thstep)))
        except Exception:
            pass

    title = spec.get("title") or ""
    if title:
        ax.set_title(title)
    grid = spec.get("grid") or {}
    ax.grid(bool(grid.get("major", True)))
    if legend and n_ok > 0:
        # place the legend OUTSIDE the circle (to the right) so it never
        # covers the curves — polar plots have no free corner inside
        ax.legend(loc="center left", bbox_to_anchor=(1.08, 0.5),
                  fontsize="small", framealpha=0.9)
    fig.tight_layout()
    path = _save(fig, spec, fmt)
    return {"ok": True, "path": path, "format": fmt, "n": n_ok}


def build_implicit(spec):
    """2D implicitna krivulja F(x, y) = 0, crtana kao contour na razini 0."""
    funcs = spec.get("functions") or []
    if isinstance(funcs, str):
        funcs = [funcs]
    funcs = [s for s in (f.strip() for f in funcs) if s]
    if not funcs:
        raise ValueError("Enter F(x, y)  (the curve is F = 0).")

    xr = spec.get("xrange") or [-10.0, 10.0]
    xmin, xmax = float(xr[0]), float(xr[1])
    yr = spec.get("yrange")
    if yr and yr[0] is not None and yr[1] is not None:
        ymin, ymax = float(yr[0]), float(yr[1])
    else:
        ymin, ymax = xmin, xmax
    if not (xmax > xmin and ymax > ymin):
        raise ValueError("Invalid range: max must be greater than min.")
    # gusta mreža za glatke implicitne krivulje
    samples = max(100, min(int(spec.get("samples", 400)), 1000))
    fmt = (spec.get("format") or "svg").lower()
    if fmt not in ("svg", "png"):
        raise ValueError("format must be 'svg' or 'png'.")

    legend = bool(spec.get("legend", True))
    xs = np.linspace(xmin, xmax, samples)
    ys = np.linspace(ymin, ymax, samples)
    X, Y = np.meshgrid(xs, ys)
    fig, ax = plt.subplots(figsize=_figsize(spec))

    # colour each curve from the standard matplotlib cycle (contour doesn't
    # cycle colours on its own), and build a proxy legend (contour has no
    # legend handles of its own).
    from matplotlib.lines import Line2D
    cycle = plt.rcParams["axes.prop_cycle"].by_key().get("color",
            ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"])
    proxies, labels = [], []
    n_ok = 0
    for i, s in enumerate(funcs):
        # dopusti "lhs = rhs" → F = lhs - rhs
        if "=" in s and "==" not in s:
            lhs, rhs = s.split("=", 1)
            expr = _parse(lhs, {_X, _Y}) - _parse(rhs, {_X, _Y})
        else:
            expr = _parse(s, {_X, _Y})
        f = sp.lambdify((_X, _Y), expr, modules=["numpy"])
        with np.errstate(all="ignore"):
            Z = np.broadcast_to(np.asarray(f(X, Y), float), X.shape)
        col = cycle[i % len(cycle)]
        ax.contour(X, Y, Z, levels=[0], linewidths=1.6, colors=[col])
        proxies.append(Line2D([0], [0], color=col, linewidth=1.6))
        labels.append(s)
        n_ok += 1

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal", adjustable="box")
    core2d(ax, spec)
    if legend and proxies:
        # place outside (right) so it never covers the curves
        ax.legend(proxies, labels, loc="center left",
                  bbox_to_anchor=(1.02, 0.5), fontsize="small", framealpha=0.9)
    fig.tight_layout()
    path = _save(fig, spec, fmt)
    return {"ok": True, "path": path, "format": fmt, "n": n_ok}


def build_3d(spec):
    expr_str = (spec.get("function3d") or "").strip()
    if not expr_str:
        # dopuštamo i da 3D stigne kroz "functions" (prvi izraz)
        funcs = spec.get("functions") or []
        if isinstance(funcs, str):
            funcs = [funcs]
        funcs = [s for s in (f.strip() for f in funcs) if s]
        expr_str = funcs[0] if funcs else ""
    if not expr_str:
        raise ValueError("No surface expression z = f(x, y) given.")

    xr = spec.get("xrange") or [-5.0, 5.0]
    xmin, xmax = float(xr[0]), float(xr[1])
    # ako y raspon nije zadan, preslikaj x raspon (ista duljina/centar)
    yr = spec.get("yrange")
    if yr:
        ymin, ymax = float(yr[0]), float(yr[1])
    else:
        ymin, ymax = xmin, xmax
    if not (xmax > xmin and ymax > ymin):
        raise ValueError("Invalid range: max must be greater than min on both axes.")
    # 3D mreža je samples^2 — drži je razumnom
    samples = max(10, min(int(spec.get("samples", 60)), 200))
    cmap = spec.get("cmap", "viridis")
    colorbar = bool(spec.get("colorbar", True))
    fmt = (spec.get("format") or "svg").lower()
    if fmt not in ("svg", "png"):
        raise ValueError("format must be 'svg' or 'png'.")

    # dopusti izraz koji ovisi samo o x, samo o y, ili o oboje
    expr = _parse(expr_str, {_X, _Y})
    f = sp.lambdify((_X, _Y), expr, modules=["numpy"])

    xs = np.linspace(xmin, xmax, samples)
    ys = np.linspace(ymin, ymax, samples)
    X, Y = np.meshgrid(xs, ys)
    with np.errstate(all="ignore"):
        Zr = f(X, Y)
        # ako izraz ne ovisi o jednoj varijabli, lambdify može vratiti skalar
        # ili 1D niz — broadcastaj na punu 2D mrežu da plot_surface radi
        Z = np.broadcast_to(np.asarray(Zr, dtype=float), X.shape).astype(float)
        Z = np.where(np.isfinite(Z), Z, np.nan)

    return _render_surface3d(spec, X, Y, Z, "z = " + expr_str, cmap, colorbar, fmt)


def _render_surface3d(spec, X, Y, Z, label, cmap, colorbar, fmt):
    """Shared 3D surface rendering: surface + colorbar + name legend + Z range
    + common axes/view. Used by all 3D surface kinds (cartesian, cylindrical,
    spherical, parametric)."""
    fig = plt.figure(figsize=_figsize(spec))
    ax = fig.add_subplot(111, projection="3d")
    surf = ax.plot_surface(X, Y, Z, cmap=cmap, linewidth=0,
                           antialiased=True, rstride=1, cstride=1)
    if colorbar:
        fig.colorbar(surf, ax=ax, shrink=0.6, aspect=12, pad=0.1)

    # name legend (colorbar still shows the value range by colour)
    if bool(spec.get("legend", True)) and label:
        from matplotlib.lines import Line2D
        proxy = Line2D([0], [0], linestyle="none", marker="s",
                       markersize=9, markerfacecolor=plt.get_cmap(cmap)(0.6),
                       markeredgecolor="none")
        ax.legend([proxy], [label], loc="upper right",
                  bbox_to_anchor=(1.18, 1.02), fontsize="small",
                  framealpha=0.85)

    zrange = spec.get("zrange")
    if zrange:
        try:
            ax.set_zlim(float(zrange[0]), float(zrange[1]))
        except Exception:
            pass

    core3d(ax, spec)
    fig.tight_layout()
    path = _save(fig, spec, fmt)
    return {"ok": True, "path": path, "format": fmt, "n": 1}


def _two_ranges(spec, da, db):
    """Return ((amin,amax),(bmin,bmax)) from xrange/yrange, with defaults."""
    xr = spec.get("xrange")
    yr = spec.get("yrange")
    a = (float(xr[0]), float(xr[1])) if (xr and xr[0] is not None
                                         and xr[1] is not None) else da
    b = (float(yr[0]), float(yr[1])) if (yr and yr[0] is not None
                                         and yr[1] is not None) else db
    if not (a[1] > a[0] and b[1] > b[0]):
        raise ValueError("Invalid range: max must be greater than min.")
    return a, b


def build_cylindrical(spec):
    """Cylindrical surface z = f(r, theta):
       x = r cos θ,  y = r sin θ,  z = f(r, θ).  Params: r, θ."""
    s = (spec.get("function3d") or "").strip()
    if not s:
        raise ValueError("Enter a surface z = f(r, theta).")
    (rmin, rmax), (thmin, thmax) = _two_ranges(spec, (0.0, 5.0), (0.0, 2 * np.pi))
    n = max(10, min(int(spec.get("samples", 80)), 200))
    cmap = spec.get("cmap", "viridis")
    fmt = (spec.get("format") or "png").lower()
    if fmt not in ("svg", "png"):
        raise ValueError("format must be 'svg' or 'png'.")

    expr = _parse(s, {_R, _TH})
    fz = sp.lambdify((_R, _TH), expr, modules=["numpy"])
    rr = np.linspace(rmin, rmax, n)
    th = np.linspace(thmin, thmax, n)
    R, TH = np.meshgrid(rr, th)
    X = R * np.cos(TH)
    Y = R * np.sin(TH)
    with np.errstate(all="ignore"):
        Z = np.broadcast_to(np.asarray(fz(R, TH), float), R.shape).astype(float)
        Z = np.where(np.isfinite(Z), Z, np.nan)
    return _render_surface3d(spec, X, Y, Z, "z = " + s, cmap,
                             bool(spec.get("colorbar", True)), fmt)


def build_spherical(spec):
    """Spherical surface r = f(theta, phi):
       x = r sinθ cosφ, y = r sinθ sinφ, z = r cosθ.
       θ polar ∈[0,π], φ azimuth ∈[0,2π]."""
    s = (spec.get("function3d") or "").strip()
    if not s:
        raise ValueError("Enter a radius r = f(theta, phi).")
    (thmin, thmax), (phmin, phmax) = _two_ranges(spec, (0.0, np.pi),
                                                 (0.0, 2 * np.pi))
    n = max(10, min(int(spec.get("samples", 80)), 200))
    cmap = spec.get("cmap", "viridis")
    fmt = (spec.get("format") or "png").lower()
    if fmt not in ("svg", "png"):
        raise ValueError("format must be 'svg' or 'png'.")

    expr = _parse(s, {_TH, _PHI})
    fr = sp.lambdify((_TH, _PHI), expr, modules=["numpy"])
    th = np.linspace(thmin, thmax, n)
    ph = np.linspace(phmin, phmax, n)
    TH, PH = np.meshgrid(th, ph)
    with np.errstate(all="ignore"):
        Rr = np.broadcast_to(np.asarray(fr(TH, PH), float), TH.shape).astype(float)
    X = Rr * np.sin(TH) * np.cos(PH)
    Y = Rr * np.sin(TH) * np.sin(PH)
    Z = Rr * np.cos(TH)
    for A in (X, Y, Z):
        A[~np.isfinite(A)] = np.nan
    return _render_surface3d(spec, X, Y, Z, "r = " + s, cmap,
                             bool(spec.get("colorbar", True)), fmt)


def build_param_surface(spec):
    """Parametric surface x(u,v), y(u,v), z(u,v).  Params: u, v.
    Enables tori, Möbius strips, minimal surfaces, etc."""
    xs_ = (spec.get("xfunc") or "").strip()
    ys_ = (spec.get("yfunc") or "").strip()
    zs_ = (spec.get("zfunc") or "").strip()
    if not (xs_ and ys_ and zs_):
        raise ValueError("Enter all three: x(u,v), y(u,v), z(u,v).")
    (umin, umax), (vmin, vmax) = _two_ranges(spec, (0.0, 2 * np.pi),
                                             (0.0, 2 * np.pi))
    n = max(10, min(int(spec.get("samples", 80)), 200))
    cmap = spec.get("cmap", "viridis")
    fmt = (spec.get("format") or "png").lower()
    if fmt not in ("svg", "png"):
        raise ValueError("format must be 'svg' or 'png'.")

    ex = _parse(xs_, {_U, _V})
    ey = _parse(ys_, {_U, _V})
    ez = _parse(zs_, {_U, _V})
    fx = sp.lambdify((_U, _V), ex, modules=["numpy"])
    fy = sp.lambdify((_U, _V), ey, modules=["numpy"])
    fz = sp.lambdify((_U, _V), ez, modules=["numpy"])
    uu = np.linspace(umin, umax, n)
    vv = np.linspace(vmin, vmax, n)
    U, V = np.meshgrid(uu, vv)
    with np.errstate(all="ignore"):
        X = np.broadcast_to(np.asarray(fx(U, V), float), U.shape).astype(float)
        Y = np.broadcast_to(np.asarray(fy(U, V), float), U.shape).astype(float)
        Z = np.broadcast_to(np.asarray(fz(U, V), float), U.shape).astype(float)
    for A in (X, Y, Z):
        A[~np.isfinite(A)] = np.nan
    return _render_surface3d(spec, X, Y, Z, "(x,y,z)(u,v)", cmap,
                             bool(spec.get("colorbar", True)), fmt)


# ==========================================================================
# DISPATCHER
# ==========================================================================
_BUILDERS = {
    "2d": build_2d,
    "parametric": build_parametric,
    "polar": build_polar,
    "implicit": build_implicit,
    "3d": build_3d,
    "cylindrical": build_cylindrical,
    "spherical": build_spherical,
    "param_surface": build_param_surface,
}


def build(spec):
    if not HAVE_SYMPY:
        raise RuntimeError("sympy is required but not available")
    kind = (spec.get("kind") or "2d").lower()
    builder = _BUILDERS.get(kind)
    if builder is None:
        raise ValueError("Unknown plot kind: %s" % kind)
    return builder(spec)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Plot builder (symplot core + matplotlib)")
    ap.add_argument("--spec", help="JSON spec file (instead of stdin)")
    args = ap.parse_args(argv)
    try:
        if args.spec:
            with open(args.spec, "r", encoding="utf-8") as fh:
                spec = json.load(fh)
        else:
            spec = json.load(sys.stdin)
        result = build(spec)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

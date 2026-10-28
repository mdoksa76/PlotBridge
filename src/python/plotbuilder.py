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
plotbuilder.py — 2D plot builder za LibreOffice "plot most".

Koristi symplot jezgru (sympy parser s implicitnim množenjem, singulariteti,
derivacije, nultočke, ekstremi) za obradu izraza, a crta kroz matplotlib
u SVG/PNG. Ne ovisi o LibreOfficeu — poziva se kao vanjski proces iz .oxt
omotača, a može se i samostalno testirati iz terminala.

ULAZ:  JSON spec (stdin ili --spec datoteka)
IZLAZ: SVG/PNG datoteka + JSON izvještaj na stdout

Spec (JSON) — sva polja osim 'functions' i 'out' imaju razumne defaulte:
{
  "functions": ["sin(x)", "cos(x)*exp(-x/5)"],   # 1+ izraza u x (implicitno * ok)
  "xrange": [-10, 10],
  "yrange": null,                 # null = auto (po vrijednostima funkcija)
  "samples": 800,
  "title": "",
  "xlabel": "x",
  "ylabel": "y",
  "legend": true,
  "format": "svg",                # "svg" | "png"
  "width": 16.0, "height": 10.0,  # cm
  "dpi": 150,                      # za PNG

  "grid": {                       # major/minor grid po osima
    "major": true,                # major grid on/off
    "minor": false,               # minor grid on/off
    "x_major": null,              # ručni korak; null = auto
    "x_minor": null,
    "y_major": null,
    "y_minor": null
  },

  # --- TEMELJ za buduće značajke (izračuna se i vrati u report; zasad se NE crta) ---
  "analyze": {                    # što izračunati (ne mijenja izgled dok show_* ne upale)
    "roots": false,
    "extrema": false,
    "singularities": false,
    "derivatives": 0              # 0/1/2 — koliko derivacija simbolički izvesti
  },
  "show": {                       # buduće iscrtavanje; danas ignorirano osim ako true
    "roots": false,
    "extrema": false,
    "singularities": false,
    "derivatives": false
  },

  "out": "/putanja/bez/ekstenzije"
}

REPORT (stdout JSON):
{
  "ok": true,
  "path": "...", "format": "svg", "n": 2,
  "analysis": {                   # po funkciji, ako je analyze tražio
    "sin(x)": {"roots":[...], "extrema":[[x,y],...], "singularities":[...],
               "d1":"cos(x)", "d2":"-sin(x)"}
  }
}
ili {"ok": false, "error": "..."}
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

# ---- symplot jezgra ------------------------------------------------------
# Adapter ovisi o funkcijama iz symplota. Držimo ga importabilnim: ako je
# symplot uz nas kao modul, koristimo njega; inače minimalni interni fallback
# parser (isti skup funkcija) da builder radi i samostalno.
_SYMPLOT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SYMPLOT_DIR not in sys.path:
    sys.path.insert(0, _SYMPLOT_DIR)

try:
    import sympy as sp
    HAVE_SYMPY = True
except Exception:
    HAVE_SYMPY = False

# Preferiraj prave symplot funkcije ako je modul dostupan (symplot.py uz nas).
_sym = None
try:
    import symplot as _sym  # noqa: F401
except Exception:
    _sym = None


# --- parser izraza: koristi symplot.sym_parse ako postoji, inače lokalni ---
if HAVE_SYMPY:
    from sympy.parsing.sympy_parser import (
        parse_expr, standard_transformations,
        implicit_multiplication_application, convert_xor,
    )
    _TRANSFORMS = standard_transformations + (
        implicit_multiplication_application, convert_xor,
    )
    _X = sp.Symbol("x", real=True)


def _local_parse(expr):
    """Fallback parser (ekvivalent symplot.sym_parse): x + standardne funkcije,
    implicitno množenje, ^ kao potenciju."""
    if not HAVE_SYMPY:
        raise RuntimeError("sympy nije dostupan")
    local = {"x": _X}
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
    return parse_expr(expr, local_dict=local, transformations=_TRANSFORMS,
                      evaluate=True)


def parse_expr_str(s):
    """Vrati sympy izraz za string, preko symplot jezgre ako je ima."""
    if _sym is not None and hasattr(_sym, "sym_parse"):
        e = _sym.sym_parse(s)
        if e is None:
            raise ValueError("Cannot parse expression: %s" % s)
        return e
    e = _local_parse(s)
    if e is None:
        raise ValueError("Cannot parse expression: %s" % s)
    return e


def numeric_fn(expr):
    """Numerička funkcija nad numpy poljem iz sympy izraza."""
    return sp.lambdify(_X, expr, modules=["numpy"])


# --- analiza (temelj za buduće značajke) ----------------------------------
def analyze_expr(expr, s, xmin, xmax, opts):
    """Vrati dict s roots/extrema/singularities/derivacije ako su traženi.
    Koristi symplot funkcije kad postoje; inače sympy direktno. Čisto računanje,
    ništa se ne crta — to je temelj za kasnije show_*."""
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

    if opts.get("roots"):
        try:
            if _sym is not None and hasattr(_sym, "sym_roots"):
                info["roots"] = _sym.sym_roots(expr, xmin, xmax)
            else:
                info["roots"] = []
        except Exception:
            info["roots"] = []
    if opts.get("extrema"):
        try:
            if _sym is not None and hasattr(_sym, "sym_extrema"):
                info["extrema"] = _sym.sym_extrema(expr, xmin, xmax)
            else:
                info["extrema"] = []
        except Exception:
            info["extrema"] = []
    if opts.get("singularities"):
        try:
            if _sym is not None and hasattr(_sym, "sym_singularities"):
                info["singularities"] = _sym.sym_singularities(expr, xmin, xmax)
            else:
                info["singularities"] = []
        except Exception:
            info["singularities"] = []
    return info


# --- grid ------------------------------------------------------------------
def _apply_grid(ax, grid):
    """Major/minor grid po osima: auto (MultipleLocator po želji) + on/off."""
    major = bool(grid.get("major", True))
    minor = bool(grid.get("minor", False))

    # ručni koraci (null = auto)
    xmaj, xmin_ = grid.get("x_major"), grid.get("x_minor")
    ymaj, ymin_ = grid.get("y_major"), grid.get("y_minor")

    if xmaj:
        ax.xaxis.set_major_locator(MultipleLocator(float(xmaj)))
    if ymaj:
        ax.yaxis.set_major_locator(MultipleLocator(float(ymaj)))

    if minor:
        if xmin_:
            ax.xaxis.set_minor_locator(MultipleLocator(float(xmin_)))
        else:
            ax.xaxis.set_minor_locator(AutoMinorLocator())
        if ymin_:
            ax.yaxis.set_minor_locator(MultipleLocator(float(ymin_)))
        else:
            ax.yaxis.set_minor_locator(AutoMinorLocator())

    if major:
        ax.grid(which="major", linewidth=0.5, alpha=0.6)
    if minor:
        ax.grid(which="minor", linewidth=0.3, alpha=0.35)


# --- glavni build ----------------------------------------------------------
def build(spec):
    if not HAVE_SYMPY:
        raise RuntimeError("sympy is required but not available")

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

    title = spec.get("title") or ""
    xlabel = spec.get("xlabel", "x")
    ylabel = spec.get("ylabel", "y")
    legend = bool(spec.get("legend", True))

    grid = spec.get("grid") or {}
    fmt = (spec.get("format") or "svg").lower()
    if fmt not in ("svg", "png"):
        raise ValueError("format must be 'svg' or 'png'.")

    w_cm = float(spec.get("width", 16.0))
    h_cm = float(spec.get("height", 10.0))
    dpi = int(spec.get("dpi", 150))

    analyze = spec.get("analyze") or {}
    out = spec.get("out")
    if not out:
        raise ValueError("Missing 'out' (output path without extension).")
    out_path = out if out.lower().endswith("." + fmt) else out + "." + fmt

    fig, ax = plt.subplots(figsize=(w_cm / 2.54, h_cm / 2.54))
    xs = np.linspace(xmin, xmax, samples)

    analysis = {}
    n_ok = 0
    for s in funcs:
        expr = parse_expr_str(s)
        # zabrani slobodne simbole osim x
        free = expr.free_symbols - {_X}
        if free:
            raise ValueError(
                "Unknown symbol(s): %s (only variable x is allowed)"
                % ", ".join(sorted(str(f) for f in free)))
        f = numeric_fn(expr)
        with np.errstate(all="ignore"):
            ys = np.asarray(f(xs), dtype=float)
            ys = np.where(np.isfinite(ys), ys, np.nan)
        ax.plot(xs, ys, label=s, linewidth=1.6)
        n_ok += 1

        # analiza (samo računanje — temelj; ništa se zasad ne crta)
        if any(analyze.get(k) for k in ("roots", "extrema", "singularities")) \
                or analyze.get("derivatives"):
            analysis[s] = analyze_expr(expr, s, xmin, xmax, analyze)

    ax.set_xlim(xmin, xmax)
    if yr:
        ax.set_ylim(float(yr[0]), float(yr[1]))

    if title:
        ax.set_title(title)
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)

    _apply_grid(ax, grid)

    # osi kroz ishodište
    ax.axhline(0, color="black", linewidth=0.8)
    ax.axvline(0, color="black", linewidth=0.8)
    if legend and n_ok > 0:
        ax.legend(loc="best", fontsize="small")

    fig.tight_layout()
    save_kw = {"format": fmt}
    if fmt == "png":
        save_kw["dpi"] = dpi
    fig.savefig(out_path, **save_kw)
    plt.close(fig)

    report = {"ok": True, "path": out_path, "format": fmt, "n": n_ok}
    if analysis:
        report["analysis"] = analysis
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="2D plot builder (symplot core + matplotlib)")
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

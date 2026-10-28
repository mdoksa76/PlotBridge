#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
symplot — simbolički/visoko-preczizni ASCII + SVG plotter (analog "plot")

Ideja:
  * "plot"    -> samo math (float, brzo)
  * "symplot" -> sympy (simbolička obrada izraza) + mpmath (visoka preciznost)
                 + math (fallback)

Redoslijed rada po izrazu:
  1. sympy parsira izraz u simbolički oblik (sympify) -> normalizacija,
     pojednostavljenje, detekcija domena, derivacija, limes, asimptote.
  2. Numeričko uzorkovanje:
       - mpmath (mp) za visoku preciznost i složene funkcije
         (gama, zeta, bessel, lambertw, erf, ...), detekcija singulariteta
       - math za brzinu kad je izraz jednostavan i mpmath nije potreban
  3. Ispis: ASCII (isti stil kao plot) + opcionalno SVG/TXT/PNG.

Dodatne opcije (osim plot-ovih):
  --sym            simbolički sažetak (pojednostavljenje, derivacija, limes)
  --d1 / --d2      prikaži 1. / 2. derivaciju (simbolički, pa numerički)
  --prec N         mpmath preciznost u bitovima (default 100)
  --points N       broj uzoraka za SVG (default: sirina/2)
  --no-sym         preskoči sympy (čisti mpmath/math)
  --asym           ispiši asimptote / singularitete
  --roots          ispiši nultočke u x-rasponu (numerički)
  --extrema        ispiši lokalne ekstreme u x-rasponu
  --table          ispiši tablicu (x, f(x)) za uzorke
  --png <ime>      spremi PNG (preko SVG -> Inkscape/rsvg ako postoje)

Sintaksa izraza (sympy):
  sin cos tan asin acos atan sinh cosh tanh exp log ln sqrt abs
  gamma zeta erf erfc besselj bessely lambertw pi E oo
  ^ ili ** za potenciju, implicitno množenje (2x, 3sin(x)) je dozvoljeno
"""

import math
import os
import re
import sys

# ---- opcionalni importi --------------------------------------------------
try:
    import sympy as sp
    from sympy.parsing.sympy_parser import (
        parse_expr, standard_transformations,
        implicit_multiplication_application, convert_xor,
    )
    HAVE_SYMPY = True
except Exception:
    HAVE_SYMPY = False

try:
    import mpmath as mp
    HAVE_MPMATH = True
except Exception:
    HAVE_MPMATH = False

SIRINA = 61
VISINA = 25

# ==========================================================================
# 1. SIMBOLIČKI SLOJ
# ==========================================================================

_TRANSFORMS = None
if HAVE_SYMPY:
    _TRANSFORMS = standard_transformations + (
        implicit_multiplication_application,
        convert_xor,
    )

# mapa korisničkih imena -> sympy (dopuna za češće nazive)
_SYM_ALIASES = {
    "ln": "log",
    "log10": "log",   # sympy log je prirodni; log10 se rješava posebno
    "oo": "oo",
    "inf": "oo",
}


def sym_parse(expr):
    """Vrati sympy izraz ili None."""
    if not HAVE_SYMPY:
        return None
    x = sp.Symbol("x", real=True)
    local = {"x": x}
    # dopuni lokalne simbole iz sympy imenskog prostora
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
    try:
        return parse_expr(expr, local_dict=local, transformations=_TRANSFORMS,
                          evaluate=True)
    except Exception:
        return None


def sym_summary(expr_str):
    """Simbolički sažetak: pojednostavljenje, derivacije, limes, domena."""
    e = sym_parse(expr_str)
    if e is None:
        return None
    out = {}
    x = sp.Symbol("x", real=True)
    try:
        out["raw"] = sp.sstr(e)
        out["simplified"] = sp.sstr(sp.simplify(e))
        out["expanded"] = sp.sstr(sp.expand(e))
    except Exception:
        pass
    try:
        d1 = sp.diff(e, x)
        out["d1"] = sp.sstr(sp.simplify(d1))
    except Exception:
        d1 = None
    try:
        d2 = sp.diff(e, x, 2)
        out["d2"] = sp.sstr(sp.simplify(d2))
    except Exception:
        d2 = None
    try:
        out["limit0"] = sp.sstr(sp.limit(e, x, 0))
    except Exception:
        pass
    try:
        out["limit_inf"] = sp.sstr(sp.limit(e, x, sp.oo))
    except Exception:
        pass
    try:
        out["limit_-inf"] = sp.sstr(sp.limit(e, x, -sp.oo))
    except Exception:
        pass
    try:
        out["is_even"] = sp.sstr(sp.simplify(e.subs(x, -x) - e) == 0)
        out["is_odd"] = sp.sstr(sp.simplify(e.subs(x, -x) + e) == 0)
    except Exception:
        pass
    return out, e, d1, d2


def sym_singularities(e, xmin=None, xmax=None):
    """Realne točke gdje funkcija eksplodira: polovi (solve nazivnika) +
       numerička detekcija skokova (npr. tan)."""
    if not HAVE_SYMPY or e is None:
        return []
    x = sp.Symbol("x", real=True)
    res = []

    def _ok(r):
        try:
            v = float(sp.N(r, 20))
        except Exception:
            return None
        if v != v or abs(v) == float("inf"):
            return None
        if (xmin is not None and xmax is not None
                and not (xmin <= v <= xmax)):
            return None
        return v

    # 1) eksplicitni polovi iz nazivnika
    try:
        den = sp.denom(sp.together(e))
        if den != 1:
            for r in sp.solve(den, x):
                v = _ok(r)
                if v is not None:
                    res.append(v)
    except Exception:
        pass

    # 2) numerička detekcija: |f| raste prema ogromnim vrijednostima
    if xmin is not None and xmax is not None:
        try:
            f = sp.lambdify(x, e, "math")
        except Exception:
            f = None
        if f is not None:
            n = 4000
            prev = None
            pa = None
            for i in range(n + 1):
                xx = xmin + (xmax - xmin) * i / n
                try:
                    yy = float(f(xx))
                    if yy != yy or abs(yy) == float("inf"):
                        yy = None
                except Exception:
                    yy = None
                if yy is not None and abs(yy) > 1e2:
                    if prev is not None and prev * yy < 0:
                        # dva velika suprotnog predznaka -> pol između pa i xx;
                        # bisekcija po |f| -> minimum |f| je najbliže polu
                        a, b = pa, xx
                        for _ in range(90):
                            m1 = a + (b - a) / 3.0
                            m2 = b - (b - a) / 3.0
                            try:
                                v1 = abs(float(f(m1)))
                                v2 = abs(float(f(m2)))
                            except Exception:
                                break
                            if v1 < v2:
                                b = m2
                            else:
                                a = m1
                            if b - a < 1e-15 * max(1.0, abs(b)):
                                break
                        res.append(0.5 * (a + b))
                prev = yy
                pa = xx
    # dedup + sort
    out = []
    for r in sorted(res):
        if not any(abs(r - q) < 1e-6 * max(1.0, abs(r)) for q in out):
            out.append(r)
    return out


def sym_roots(e, xmin, xmax):
    """Numeričke nultočke u [xmin,xmax] preko sympy nsolve na gruboj mreži."""
    if not HAVE_SYMPY or e is None:
        return []
    x = sp.Symbol("x", real=True)
    out = []
    try:
        f = sp.lambdify(x, e, "math")
    except Exception:
        return []
    n = 400
    prev_x = None
    prev_y = None
    for i in range(n + 1):
        xx = xmin + (xmax - xmin) * i / n
        try:
            yy = f(xx)
            if yy is None or (isinstance(yy, complex)):
                prev_x = prev_y = None
                continue
            yy = float(yy)
            if yy != yy or abs(yy) == float("inf"):
                prev_x = prev_y = None
                continue
        except Exception:
            prev_x = prev_y = None
            continue
        if prev_y is not None and prev_y * yy < 0:
            a, b = prev_x, xx
            fa = prev_y
            for _ in range(60):
                m = 0.5 * (a + b)
                try:
                    fm = float(f(m))
                except Exception:
                    break
                if fa * fm <= 0:
                    b = m
                else:
                    a, fa = m, fm
                if b - a < 1e-12 * max(1.0, abs(b)):
                    break
            out.append(0.5 * (a + b))
        elif yy == 0.0:
            out.append(xx)
        prev_x, prev_y = xx, yy
    # dedup
    ded = []
    for r in out:
        if not any(abs(r - q) < 1e-7 * max(1.0, abs(r)) for q in ded):
            ded.append(r)
    return ded


def sym_extrema(e, xmin, xmax):
    """Lokalni ekstremi preko d1=0 (numerički)."""
    if not HAVE_SYMPY or e is None:
        return []
    x = sp.Symbol("x", real=True)
    try:
        d1 = sp.diff(e, x)
        f1 = sp.lambdify(x, d1, "math")
        f0 = sp.lambdify(x, e, "math")
    except Exception:
        return []
    out = []
    n = 400
    px = py = None
    for i in range(n + 1):
        xx = xmin + (xmax - xmin) * i / n
        try:
            yy = float(f1(xx))
            if yy != yy or abs(yy) == float("inf"):
                px = py = None
                continue
        except Exception:
            px = py = None
            continue
        if py is not None and py * yy < 0:
            a, b, fa = px, xx, py
            for _ in range(60):
                m = 0.5 * (a + b)
                try:
                    fm = float(f1(m))
                except Exception:
                    break
                if fa * fm <= 0:
                    b = m
                else:
                    a, fa = m, fm
                if b - a < 1e-12 * max(1.0, abs(b)):
                    break
            xr = 0.5 * (a + b)
            try:
                out.append((xr, float(f0(xr))))
            except Exception:
                pass
        px, py = xx, yy
    return out


# ==========================================================================
# 2. NUMERIČKI SLOJ (mpmath + math fallback)
# ==========================================================================

def _mathify(expr):
    """Izraz -> čisti Python za math-only eval:
       ^ -> **, implicitno množenje (2x, 3sin(x), 2(x+1), )( ).
       Napomena: u ovom fallbacku izbjegavaj 1e5 zapis (piši 1*10**5)."""
    s = expr.replace("^", "**")
    s = re.sub(r"\)(\s*)(\(|\w)", r")*\2", s)         # )x )( )2 -> )*...
    s = re.sub(r"(\d)(\s*)([A-Za-z(])", r"\1*\3", s)  # 2x 2( 2pi -> 2*...
    return s


class Evaluator:
    """Evaluator izraza: sympy (ako je moguće) -> mpmath -> math."""

    def __init__(self, expr, prec_bits=100, use_sym=True):
        self.expr = expr
        self.prec = prec_bits
        self.mode = None
        self.f = None
        self.sym = None

        if USE_MP:
            mp.mp.dps = max(15, int(prec_bits * 0.30103) + 5)

        # 1) sympy
        if use_sym and HAVE_SYMPY:
            e = sym_parse(expr)
            if e is not None:
                self.sym = e
                try:
                    self.f = sp.lambdify(sp.Symbol("x", real=True), e, "mpmath")
                    self.mode = "sympy+mpmath"
                except Exception:
                    try:
                        self.f = sp.lambdify(sp.Symbol("x", real=True), e, "math")
                        self.mode = "sympy+math"
                    except Exception:
                        self.f = None
        # 2) math fallback
        if self.f is None:
            env = {"__builtins__": {}}
            for nm in dir(math):
                if not nm.startswith("_"):
                    env[nm] = getattr(math, nm)
            env["pi"] = math.pi
            env["e"] = math.e
            env["E"] = math.e
            env["abs"] = abs
            env["ln"] = math.log
            env["log10"] = math.log10
            env["x"] = 0.0
            self._env = env
            self._math_expr = _mathify(self.expr)
            self.mode = "math"
            self.f = "math"

    def __call__(self, x):
        """Vrati float ili None (NaN/inf/greška)."""
        try:
            if self.f == "math":
                self._env["x"] = x
                v = eval(self._math_expr, self._env)
            else:
                v = self.f(x)
            if USE_MP and isinstance(v, mp.mpf):
                if not mp.isfinite(v):
                    return None
                return float(v)
            if isinstance(v, complex):
                if abs(v.imag) > 1e-12:
                    return None
                v = v.real
            v = float(v)
            if v != v or v in (float("inf"), float("-inf")):
                return None
            return v
        except Exception:
            return None


USE_MP = HAVE_MPMATH


# ==========================================================================
# 3. RENDERING (identičan stil kao "plot")
# ==========================================================================

def _col(x, xmin, xmax):
    if xmax == xmin:
        return 0
    return round((x - xmin) / (xmax - xmin) * (SIRINA - 1))


def _row(y, ymin, ymax):
    if ymax == ymin:
        return VISINA - 1
    return round((ymax - y) / (ymax - ymin) * (VISINA - 1))


def _fmt(v):
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return "%.1f" % v


def _fmt_hdr(v):
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return "%.3f" % v


def draw_ascii(f, xmin, xmax, ymin, ymax):
    pts = []
    for c in range(SIRINA):
        x = xmin + (xmax - xmin) * c / (SIRINA - 1)
        pts.append((c, x, f(x)))

    grid = [[" "] * SIRINA for _ in range(VISINA)]

    c0 = _col(0, xmin, xmax)
    r0 = _row(0, ymin, ymax)
    if 0 <= r0 < VISINA:
        for c in range(SIRINA):
            grid[r0][c] = "-"
    if 0 <= c0 < SIRINA:
        for r in range(VISINA):
            if grid[r][c0] == " ":
                grid[r][c0] = "|"
    if 0 <= r0 < VISINA and 0 <= c0 < SIRINA:
        grid[r0][c0] = "+"

    for (c, x, y) in pts:
        if y is None:
            continue
        r = _row(y, ymin, ymax)
        if 0 <= r < VISINA and 0 <= c < SIRINA:
            grid[r][c] = "*"

    def _put(row, col, text):
        for k, ch in enumerate(text):
            cc = col + k
            if 0 <= cc < SIRINA:
                row[cc] = ch

    if 0 <= c0 < SIRINA:
        _put(grid[0], c0 + 1, _fmt(ymax))
        _put(grid[VISINA - 1], c0 + 1, _fmt(ymin))

    lo = _fmt(xmin)
    hi = _fmt(xmax)
    pad = " " * len(lo)

    lines = []
    for ri, row in enumerate(grid):
        if ri == r0 and 0 <= r0 < VISINA:
            lines.append(lo + "".join(row) + " " + hi)
        else:
            lines.append(pad + "".join(row))

    w = max(len(l) for l in lines)
    lines = [l + " " * (w - len(l)) for l in lines]

    out = ["+" + "-" * (w + 2) + "+"]
    for l in lines:
        out.append("| " + l + " |")
    out.append("+" + "-" * (w + 2) + "+")
    return out


def save_txt(path, lines):
    with open(path, "w") as f:
        for l in lines:
            f.write(l + "\n")


def save_svg(path, f, expr, xmin, xmax, ymin, ymax, steps=None):
    W, H = 640, 400
    ML, MR, MT, MB = 50, 20, 20, 40
    pw = W - ML - MR
    ph = H - MT - MB
    if steps is None:
        steps = max(200, pw // 2)

    def sx(x):
        return ML + (x - xmin) / (xmax - xmin) * pw

    def sy(y):
        return MT + (ymax - y) / (ymax - ymin) * ph

    with open(path, "w") as fo:
        fo.write('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                 'viewBox="0 0 %d %d">\n' % (W, H, W, H))
        fo.write('<rect x="0" y="0" width="%d" height="%d" fill="white"/>\n' % (W, H))
        fo.write('<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="#999"/>\n'
                 % (ML, MT, pw, ph))
        if xmin <= 0 <= xmax:
            x0 = sx(0)
            fo.write('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#bbb"/>\n'
                     % (x0, MT, x0, MT + ph))
        if ymin <= 0 <= ymax:
            y0 = sy(0)
            fo.write('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#bbb"/>\n'
                     % (ML, y0, ML + pw, y0))

        open_pl = False
        for i in range(steps + 1):
            x = xmin + (xmax - xmin) * i / steps
            y = f(x)
            if y is None or y < ymin or y > ymax:
                if open_pl:
                    fo.write('"/>\n')
                    open_pl = False
                continue
            pt = "%.1f,%.1f " % (sx(x), sy(y))
            if not open_pl:
                fo.write('<polyline fill="none" stroke="#c00" stroke-width="1.6" points="')
                open_pl = True
            fo.write(pt)
        if open_pl:
            fo.write('"/>\n')

        def t(x, y, s, anch):
            s = (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            fo.write('<text x="%.1f" y="%.1f" font-family="monospace" font-size="12" '
                     'fill="#333" text-anchor="%s">%s</text>\n' % (x, y, anch, s))

        t(ML, H - 12, _fmt_hdr(xmin), "start")
        t(ML + pw, H - 12, _fmt_hdr(xmax), "end")
        t(ML - 6, MT + 10, _fmt_hdr(ymax), "end")
        t(ML - 6, MT + ph, _fmt_hdr(ymin), "end")
        t(W // 2, 14, "f(x) = " + expr, "middle")
        fo.write('</svg>\n')


def svg_to_png(svg_path, png_path):
    """Konverzija preko Inkscape / rsvg-convert / cairosvg ako postoje."""
    import shutil
    import subprocess
    cands = []
    if shutil.which("inkscape"):
        cands.append(["inkscape", svg_path, "--export-filename=" + png_path])
    if shutil.which("rsvg-convert"):
        cands.append(["rsvg-convert", "-o", png_path, svg_path])
    if shutil.which("convert"):
        cands.append(["convert", svg_path, png_path])
    for cmd in cands:
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            return True
        except Exception:
            continue
    try:
        import cairosvg
        cairosvg.svg2png(url=svg_path, write_to=png_path)
        return True
    except Exception:
        return False


# ==========================================================================
# 4. CLI
# ==========================================================================

def _num(tok):
    """Broj iz tokena; dozvoli pi, -pi, 2*pi, sympy izraze."""
    if HAVE_SYMPY:
        e = sym_parse(tok)
        if e is not None:
            try:
                v = float(sp.N(e, 20))
                return v
            except Exception:
                pass
    try:
        return float(eval(tok, {"__builtins__": {}, "pi": math.pi,
                                "e": math.e, "E": math.e,
                                "sqrt": math.sqrt}))
    except Exception:
        return None


def help_full():
    print("SYMPLOT — simbolički ASCII/SVG plotter (sympy + mpmath + math)")
    print("=" * 62)
    print("")
    print("SINTAKSA:")
    print("  symplot <izraz> [opcije]")
    print("")
    print("RASPON:")
    print("  x <min> <max>        x-raspon (pi, E, 2*pi dozvoljeni)")
    print("  y <min> <max>        y-raspon")
    print("  yauto                y prema min/max funkcije")
    print("")
    print("IZLAZ:")
    print("  txt <ime>            ASCII u .txt (u dir skripte)")
    print("  svg <ime>            vektor u .svg")
    print("  png <ime>            PNG (SVG -> Inkscape/rsvg/cairosvg)")
    print("")
    print("SIMBOLIČKI / NUMERIČKI:")
    print("  --sym                simbolički sažetak (simplify, derivacije, limes)")
    print("  --d1                 prikaži 1. derivaciju")
    print("  --d2                 prikaži 2. derivaciju")
    print("  --asym               singulariteti / polovi")
    print("  --roots              nultočke u rasponu")
    print("  --extrema            lokalni ekstremi u rasponu")
    print("  --table              tablica (x, f(x))")
    print("  --prec N             mpmath preciznost u bitovima (default 100)")
    print("  --points N           broj uzoraka za SVG (default 200+)")
    print("  --no-sym             preskoči sympy (mpmath/math only)")
    print("")
    print("PRIMJERI:")
    print("  symplot sin(x)/x x -15 15 yauto --sym")
    print("  symplot gamma(x) x -4 5 y -10 10 --asym")
    print("  symplot zeta(x) x 0 10 yauto --sym")
    print("  symplot exp(-x**2) x -3 3 yauto svg gauss.svg png gauss.png")
    print("  symplot x**3-3x yauto --roots --extrema --d1")
    print("  symplot besselj(0,x) x 0 20 yauto --prec 200")
    print("  symplot tan(x) x -4.8 4.8 y -6 6 --asym")
    print("")
    print("DOSTUPNO: sympy=%s  mpmath=%s  math=da"
          % ("da" if HAVE_SYMPY else "NE", "da" if HAVE_MPMATH else "NE"))


def main(argv):
    if not argv or argv[0] in ("help", "-h", "--help"):
        help_full()
        return 0

    expr = argv[0]
    xmin, xmax = -10.0, 10.0
    ymin, ymax = -10.0, 10.0
    yauto = False
    txt = svg = png = None
    prec = 100
    points = None
    do_sym = do_d1 = do_d2 = do_asym = do_roots = do_extrema = do_table = False
    use_sym = True

    i = 1
    n = len(argv)
    while i < n:
        k = argv[i]
        if k == "x" and i + 2 < n:
            a, b = _num(argv[i + 1]), _num(argv[i + 2])
            if a is not None and b is not None:
                xmin, xmax = a, b
            i += 3
        elif k == "y" and i + 2 < n:
            a, b = _num(argv[i + 1]), _num(argv[i + 2])
            if a is not None and b is not None:
                ymin, ymax = a, b
            i += 3
        elif k == "yauto":
            yauto = True; i += 1
        elif k == "txt" and i + 1 < n:
            txt = argv[i + 1]; i += 2
        elif k == "svg" and i + 1 < n:
            svg = argv[i + 1]; i += 2
        elif k == "png" and i + 1 < n:
            png = argv[i + 1]; i += 2
        elif k == "--prec" and i + 1 < n:
            try:
                prec = int(argv[i + 1])
            except Exception:
                pass
            i += 2
        elif k == "--points" and i + 1 < n:
            try:
                points = int(argv[i + 1])
            except Exception:
                pass
            i += 2
        elif k == "--sym":
            do_sym = True; i += 1
        elif k == "--d1":
            do_d1 = True; i += 1
        elif k == "--d2":
            do_d2 = True; i += 1
        elif k == "--asym":
            do_asym = True; i += 1
        elif k == "--roots":
            do_roots = True; i += 1
        elif k == "--extrema":
            do_extrema = True; i += 1
        elif k == "--table":
            do_table = True; i += 1
        elif k == "--no-sym":
            use_sym = False; i += 1
        else:
            i += 1

    if xmax <= xmin:
        print("greska: xmax mora biti > xmin")
        return 2

    ev = Evaluator(expr, prec_bits=prec, use_sym=use_sym)

    # --- simbolički sažetak ------------------------------------------------
    sym_info = None
    e_sym = None
    d1_expr = d2_expr = None
    if HAVE_SYMPY and use_sym:
        got = sym_summary(expr)
        if got:
            sym_info, e_sym, d1_expr, d2_expr = got

    if do_sym and sym_info:
        print("SIMBOLIČKI SAŽETAK  (%s)" % ev.mode)
        print("-" * 62)
        for key, label in (("raw", "izraz"), ("simplified", "pojednostavljeno"),
                           ("expanded", "razvijeno")):
            if key in sym_info:
                print("  %-18s %s" % (label + ":", sym_info[key]))
        for key, label in (("d1", "f'(x)"), ("d2", "f''(x)"),
                           ("limit0", "lim x->0"), ("limit_inf", "lim x->+oo"),
                           ("limit_-inf", "lim x->-oo")):
            if key in sym_info:
                print("  %-18s %s" % (label + ":", sym_info[key]))
        if "is_even" in sym_info:
            print("  %-18s parna=%s  neparna=%s"
                  % ("simetrija:", sym_info.get("is_even"), sym_info.get("is_odd")))
        print("")

    if do_asym and e_sym is not None:
        sings = sym_singularities(e_sym, xmin, xmax)
        print("SINGULARITETI (polovi):")
        if sings:
            for s in sings:
                print("  x = %s" % sp.sstr(s))
        else:
            print("  (nema pronađenih u realnoj domeni)")
        print("")

    if do_roots and e_sym is not None:
        rs = sym_roots(e_sym, xmin, xmax)
        print("NULTOČKE u [%s, %s]:" % (_fmt_hdr(xmin), _fmt_hdr(xmax)))
        if rs:
            for r in rs:
                print("  x = %.10g" % r)
        else:
            print("  (nema)")
        print("")

    if do_extrema and e_sym is not None:
        ex = sym_extrema(e_sym, xmin, xmax)
        print("LOKALNI EKSTREMI u [%s, %s]:" % (_fmt_hdr(xmin), _fmt_hdr(xmax)))
        if ex:
            for (xv, yv) in ex:
                print("  x = %.10g   f(x) = %.10g" % (xv, yv))
        else:
            print("  (nema)")
        print("")

    # --- yauto -------------------------------------------------------------
    if yauto:
        ys = []
        for c in range(SIRINA):
            x = xmin + (xmax - xmin) * c / (SIRINA - 1)
            y = ev(x)
            if y is not None:
                ys.append(y)
        if len(ys) >= 2:
            ymin, ymax = min(ys), max(ys)
            if ymax == ymin:
                ymin -= 1; ymax += 1
            else:
                m = (ymax - ymin) * 0.1
                ymin -= m; ymax += m
        else:
            print("greska: funkcija nema vrijednosti u tom rasponu")
            return 2

    header = ("f(x) = %s   x:[%s,%s]  y:[%s,%s]  [%s]"
              % (expr, _fmt_hdr(xmin), _fmt_hdr(xmax),
                 _fmt_hdr(ymin), _fmt_hdr(ymax), ev.mode))
    lines = [header] + draw_ascii(ev, xmin, xmax, ymin, ymax)
    for l in lines:
        print(l)

    # --- derivacije kao zasebni mini-plotovi ------------------------------
    for flag, dexpr, label in ((do_d1, d1_expr, "f'(x)"),
                               (do_d2, d2_expr, "f''(x)")):
        if not flag or dexpr is None:
            continue
        dev = Evaluator(sp.sstr(dexpr), prec_bits=prec, use_sym=True)
        ys = []
        for c in range(SIRINA):
            x = xmin + (xmax - xmin) * c / (SIRINA - 1)
            y = dev(x)
            if y is not None:
                ys.append(y)
        if len(ys) < 2:
            continue
        a, b = min(ys), max(ys)
        m = (b - a) * 0.1 or 1.0
        print("")
        print("%s = %s   y:[%s,%s]" % (label, sp.sstr(dexpr),
                                       _fmt_hdr(a - m), _fmt_hdr(b + m)))
        for l in draw_ascii(dev, xmin, xmax, a - m, b + m):
            print(l)

    # --- tablica -----------------------------------------------------------
    if do_table:
        print("")
        print("TABLICA (x, f(x)):")
        n_tab = 21
        for j in range(n_tab):
            x = xmin + (xmax - xmin) * j / (n_tab - 1)
            y = ev(x)
            print("  x = %12.6g   f(x) = %s"
                  % (x, "—" if y is None else "%.10g" % y))

    # --- spremanje ---------------------------------------------------------
    d = os.path.dirname(os.path.abspath(__file__))
    if txt:
        p = os.path.join(d, txt)
        try:
            save_txt(p, lines)
            print("spremljeno: " + p)
        except Exception as e:
            print("txt greska: %s" % e)
    svg_path = None
    if svg:
        svg_path = os.path.join(d, svg)
        try:
            save_svg(svg_path, ev, expr, xmin, xmax, ymin, ymax, steps=points)
            print("spremljeno: " + svg_path)
        except Exception as e:
            print("svg greska: %s" % e)
    if png:
        p = os.path.join(d, png)
        tmp_svg = svg_path or os.path.join(d, ".symplot_tmp.svg")
        try:
            if not svg_path:
                save_svg(tmp_svg, ev, expr, xmin, xmax, ymin, ymax, steps=points)
            if svg_to_png(tmp_svg, p):
                print("spremljeno: " + p)
            else:
                print("png greska: nema konvertera (inkscape/rsvg/cairosvg)")
        except Exception as e:
            print("png greska: %s" % e)
        finally:
            if not svg_path and os.path.exists(tmp_svg):
                os.remove(tmp_svg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

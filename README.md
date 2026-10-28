# Plot Bridge

Insert high-quality mathematical plots directly at the cursor in LibreOffice
(Writer, Calc, Impress). You type one or more functions of *x*, set the ranges,
grid and labels, and a vector (SVG) plot is inserted into your document. Plots
are re-editable: select an inserted plot, open the dialog again, change anything
(including adding or removing a function), and it is replaced in place.

Plotting is done by a small external Python builder using
[SymPy](https://www.sympy.org) (symbolic parsing, implicit multiplication),
[NumPy](https://numpy.org) and [Matplotlib](https://matplotlib.org).

> **Status: early release (0.1).** 2D function plotting works end to end.
> More plot types (3D, parametric, derivatives/integrals), CSV data input and
> on-graph markers (roots, extrema, asymptotes) are planned — the builder
> already computes the analysis data, it is just not drawn yet.

## Requirements

Plot Bridge is a thin LibreOffice wrapper; the actual plotting runs in your
**system Python 3**, which must have the scientific stack installed:

```
python3 -m pip install sympy numpy matplotlib
```

- **Linux:** works out of the box once the modules above are installed.
- **Windows / macOS:** you need a system Python 3 on the machine with those
  modules, and the path to it configured (see *Configuration*). This release is
  Linux-first; other platforms are untested.

The extension calls `/usr/bin/python3` by default.

## Install

1. Download `PlotBridge.oxt` from the
   [releases page](https://github.com/mdoksa76/plotbridge/releases).
2. In LibreOffice: **Tools → Extension Manager → Add…**, pick the `.oxt`,
   then restart LibreOffice.

Or from the command line:

```bash
unopkg add -f PlotBridge.oxt
```

## Use

Put the cursor where you want the plot, then **Tools → Plot Bridge…**

- **Functions of x** — one per line, e.g.

  ```
  sin(x)
  cos(x)*exp(-x/5)
  x^2/20
  ```

  Implicit multiplication (`3sin(x)`, `2x`) and `^` for powers are supported,
  as well as the usual functions: `sin cos tan exp log ln sqrt abs gamma
  zeta erf besselj …`.
- **X / Y range** — leave **Y empty** for automatic range.
- **Title, X label, Y label**.
- **Major grid / Minor grid** — with optional manual step sizes per axis
  (leave empty for automatic).
- **Show legend**.

Click **Insert**.

### Re-editing a plot (Replot)

Select an existing Plot Bridge plot, then open **Tools → Plot Bridge…** again.
The dialog is pre-filled with that plot's settings. Change whatever you like and
click **Insert** — the old plot is replaced in place. With nothing selected, a
new plot is inserted at the cursor instead.

## Configuration

If your system Python is not at `/usr/bin/python3`, edit the constant near the
top of `python/plotbridge.py` inside the extension:

```python
SYSTEM_PYTHON = "/usr/bin/python3"
```

Find your path with `which python3` (Linux/macOS) or `where python` (Windows),
and make sure that interpreter has sympy, numpy and matplotlib.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE). Free software: if you build on it,
your changes stay free too.

## Author

[mdoksa76](https://github.com/mdoksa76)

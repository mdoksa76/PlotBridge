# Plot Bridge

Insert high-quality mathematical plots directly at the cursor in LibreOffice
(Writer, Calc, Impress, Draw). You choose a plot type, enter the function(s),
set the ranges, grid and labels, and the plot is inserted into your document.
Plots are re-editable: select an inserted plot, open the dialog again, change
anything (including adding or removing a function), and it is replaced in place.

Plotting is done by a small external Python builder using
[SymPy](https://www.sympy.org) (symbolic parsing, implicit multiplication),
[NumPy](https://numpy.org) and [Matplotlib](https://matplotlib.org).

## Plot types

- **2D function** y = f(x) — multiple functions on one graph, automatic
  asymptote breaks (1/x, tan …)
- **2D parametric** x(t), y(t)
- **Polar** r(θ) — with orientation and scale controls
- **Implicit** F(x, y) = 0
- **3D surface** z = f(x, y)
- **3D cylindrical** z = f(r, θ)
- **3D spherical** r = f(θ, φ)
- **3D parametric surface** x, y, z (u, v) — tori, Möbius strips, helicoids …

Plus: an **Examples** dropdown (up to 5 ready-made examples per type that fill
the fields), **colormap** selection for 3D surfaces, and **π input** in ranges
(`pi`, `2pi`, `pi/2` …).

## Requirements

Plot Bridge is a thin LibreOffice wrapper; the actual plotting runs in your
**system Python 3**, which must have the scientific stack installed:

```
python3 -m pip install sympy numpy matplotlib
```

- **Linux:** works out of the box once the modules above are installed.
- **Windows:** needs a system Python 3 with those modules; the extension
  detects common install locations automatically. Supported and tested.

The extension calls `/usr/bin/python3` on Linux by default.

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

1. Pick a **plot type** from the dropdown.
2. Enter the function(s) — one per line for 2D functions, e.g.

   ```
   sin(x)
   cos(x)*exp(-x/5)
   x^2/20
   ```

   Implicit multiplication (`3sin(x)`, `2x`) and `^` for powers are supported,
   as well as the usual functions: `sin cos tan exp log ln sqrt abs gamma
   zeta erf besselj …`.
3. Set the **ranges** (leave empty for automatic where available; `pi`, `2pi`,
   `pi/2` are accepted), **title**, **axis labels**, **grid** (major/minor with
   optional manual step sizes), **legend**, and — for 3D — a **colormap**.
4. Or pick an entry from the **Examples** dropdown to fill the fields for you.

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

Find your path with `which python3` (Linux) or `where python` (Windows), and
make sure that interpreter has sympy, numpy and matplotlib.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE). Free software: if you build on it,
your changes stay free too.

## Author

[mdoksa76](https://github.com/mdoksa76)

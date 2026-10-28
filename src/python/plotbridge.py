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

# ---- configuration -------------------------------------------------------
# Where to find the system Python and the builder. Kept as module constants
# so they are easy to override during development. On the target machine these
# are confirmed/adjusted once (LibreOffice subprocesses do not always inherit
# the shell PATH, hence an absolute python path).
SYSTEM_PYTHON = "/usr/bin/python3"

# plotbuilder.py is shipped inside the extension (python/ dir) next to this file.
_HERE = os.path.dirname(os.path.abspath(__file__))
PLOTBUILDER = os.path.join(_HERE, "plotbuilder.py")

# Property name under which we stash the spec JSON on the inserted image,
# so Replot can read it back.
SPEC_PROP = "PlotBridgeSpec"

IMPL_NAME = "org.mdoksa.plotbridge"


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
    dmodel.Height = 250

    y = 6

    _add(dmodel, "FixedText", "lblFunc", dict(
        PositionX=6, PositionY=y, Width=120, Height=10,
        Label="Functions of x (one per line):"))
    y += 12
    funcs = spec.get("functions") or []
    if isinstance(funcs, str):
        funcs = [funcs]
    _add(dmodel, "Edit", "txtFunc", dict(
        PositionX=6, PositionY=y, Width=218, Height=44,
        MultiLine=True, VScroll=True,
        Text="\n".join(funcs)))
    y += 50

    # ranges
    xr = spec.get("xrange") or [-10, 10]
    yr = spec.get("yrange")
    _add(dmodel, "FixedText", "lblX", dict(
        PositionX=6, PositionY=y + 2, Width=20, Height=10, Label="X:"))
    _add(dmodel, "Edit", "txtXmin", dict(
        PositionX=28, PositionY=y, Width=40, Height=12, Text=str(xr[0])))
    _add(dmodel, "Edit", "txtXmax", dict(
        PositionX=72, PositionY=y, Width=40, Height=12, Text=str(xr[1])))
    _add(dmodel, "FixedText", "lblY", dict(
        PositionX=120, PositionY=y + 2, Width=20, Height=10, Label="Y:"))
    _add(dmodel, "Edit", "txtYmin", dict(
        PositionX=142, PositionY=y, Width=38, Height=12,
        Text="" if not yr else str(yr[0])))
    _add(dmodel, "Edit", "txtYmax", dict(
        PositionX=184, PositionY=y, Width=38, Height=12,
        Text="" if not yr else str(yr[1])))
    y += 16
    _add(dmodel, "FixedText", "hintY", dict(
        PositionX=120, PositionY=y, Width=104, Height=9,
        Label="(leave Y empty = auto)"))
    y += 12

    # labels
    _add(dmodel, "FixedText", "lblTitle", dict(
        PositionX=6, PositionY=y + 2, Width=30, Height=10, Label="Title:"))
    _add(dmodel, "Edit", "txtTitle", dict(
        PositionX=40, PositionY=y, Width=184, Height=12,
        Text=spec.get("title", "")))
    y += 16
    _add(dmodel, "FixedText", "lblXL", dict(
        PositionX=6, PositionY=y + 2, Width=40, Height=10, Label="X label:"))
    _add(dmodel, "Edit", "txtXL", dict(
        PositionX=46, PositionY=y, Width=66, Height=12,
        Text=spec.get("xlabel", "x")))
    _add(dmodel, "FixedText", "lblYL", dict(
        PositionX=120, PositionY=y + 2, Width=40, Height=10, Label="Y label:"))
    _add(dmodel, "Edit", "txtYL", dict(
        PositionX=160, PositionY=y, Width=64, Height=12,
        Text=spec.get("ylabel", "y")))
    y += 18

    # grid: major/minor checkboxes + optional manual steps
    _add(dmodel, "CheckBox", "chkMajor", dict(
        PositionX=6, PositionY=y, Width=70, Height=10,
        Label="Major grid", State=1 if grid.get("major", True) else 0))
    _add(dmodel, "CheckBox", "chkMinor", dict(
        PositionX=80, PositionY=y, Width=70, Height=10,
        Label="Minor grid", State=1 if grid.get("minor", False) else 0))
    y += 14
    _add(dmodel, "FixedText", "lblStep", dict(
        PositionX=6, PositionY=y, Width=218, Height=9,
        Label="Manual steps (optional): X maj / X min / Y maj / Y min"))
    y += 11

    def _s(v):
        return "" if v in (None, "") else str(v)
    _add(dmodel, "Edit", "txtXMaj", dict(
        PositionX=6, PositionY=y, Width=52, Height=12, Text=_s(grid.get("x_major"))))
    _add(dmodel, "Edit", "txtXMin", dict(
        PositionX=62, PositionY=y, Width=52, Height=12, Text=_s(grid.get("x_minor"))))
    _add(dmodel, "Edit", "txtYMaj", dict(
        PositionX=118, PositionY=y, Width=52, Height=12, Text=_s(grid.get("y_major"))))
    _add(dmodel, "Edit", "txtYMin", dict(
        PositionX=174, PositionY=y, Width=50, Height=12, Text=_s(grid.get("y_minor"))))
    y += 18

    _add(dmodel, "CheckBox", "chkLegend", dict(
        PositionX=6, PositionY=y, Width=90, Height=10,
        Label="Show legend",
        State=1 if spec.get("legend", True) else 0))
    y += 16

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
    return dialog, dmodel


def _read_dialog(dialog):
    """Collect the dialog fields into a spec dict."""
    def g(name):
        return dialog.getControl(name).getModel().Text

    def gnum(name, default=None):
        t = g(name).strip().replace(",", ".")
        if t == "":
            return default
        try:
            return float(t)
        except Exception:
            return default

    def gchk(name):
        return bool(dialog.getControl(name).getModel().State)

    funcs = [ln.strip() for ln in g("txtFunc").splitlines() if ln.strip()]

    xmin = gnum("txtXmin", -10.0)
    xmax = gnum("txtXmax", 10.0)
    ymin = gnum("txtYmin", None)
    ymax = gnum("txtYmax", None)
    yrange = [ymin, ymax] if (ymin is not None and ymax is not None) else None

    spec = {
        "functions": funcs,
        "xrange": [xmin, xmax],
        "yrange": yrange,
        "title": g("txtTitle"),
        "xlabel": g("txtXL"),
        "ylabel": g("txtYL"),
        "legend": gchk("chkLegend"),
        "grid": {
            "major": gchk("chkMajor"),
            "minor": gchk("chkMinor"),
            "x_major": gnum("txtXMaj", None),
            "x_minor": gnum("txtXMin", None),
            "y_major": gnum("txtYMaj", None),
            "y_minor": gnum("txtYMin", None),
        },
        "format": "svg",
    }
    return spec


# ---- builder invocation --------------------------------------------------
def _run_builder(spec):
    """Call external python3 plotbuilder.py with spec on stdin. Returns the
    report dict. Raises RuntimeError with a readable message on failure."""
    tmpdir = tempfile.mkdtemp(prefix="plotbridge_")
    out_base = os.path.join(tmpdir, "plot")
    spec = dict(spec)
    spec["out"] = out_base

    try:
        proc = subprocess.run(
            [SYSTEM_PYTHON, PLOTBUILDER],
            input=json.dumps(spec).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=60,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "System Python not found at %s. Edit SYSTEM_PYTHON in the extension."
            % SYSTEM_PYTHON)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Plot builder timed out (60 s).")

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

    spec_json = json.dumps(spec, ensure_ascii=False)

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

        if replace_shape is not None:
            # copy anchor, then insert next to the old one and remove it
            try:
                img.AnchorType = replace_shape.AnchorType
            except Exception:
                img.AnchorType = uno.Enum(
                    "com.sun.star.text.TextContentAnchorType", "AS_CHARACTER")
            try:
                anchor = replace_shape.Anchor  # text range of the old image
                doc.Text.insertTextContent(anchor, img, False)
            except Exception:
                vc = doc.CurrentController.ViewCursor
                doc.Text.insertTextContent(vc, img, False)
            try:
                doc.Text.removeTextContent(replace_shape)
            except Exception:
                pass
        else:
            img.AnchorType = uno.Enum(
                "com.sun.star.text.TextContentAnchorType", "AS_CHARACTER")
            vc = doc.CurrentController.ViewCursor
            doc.Text.insertTextContent(vc, img, False)
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

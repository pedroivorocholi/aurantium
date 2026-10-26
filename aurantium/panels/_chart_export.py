"""The chart as an image: what is on screen, at twice the pixels.

The old export was ``panel.grab()`` — a screenshot of the whole panel at screen
resolution, range chips and indicator row included, with no record of what
was plotted beyond the chips' own text. This renders the plot area only (the
price pane, the events lane, every indicator sub-pane, their axes) and puts a
header over it that says what it is: symbol, interval, range, and a legend
entry in its plotted colour for every series on it, and the logo mark at
its top right.

Crisp, not upscaled: each pane's scene is re-rendered into the image through
pyqtgraph's export mode (the path its own ImageExporter takes), so lines and
axis text are drawn at 2x rather than stretched from 1x.

Either theme, whichever one is on screen: the theme only switches on a
restart, so an export in the other one recolours the plot items in place —
every colour the chart took from the palette becomes the other palette's
colour for the same role — renders, and puts them back.

Nothing on screen changes. The render is synchronous — no event loop turns
while it runs — and everything it touches (the crosshair it hides, a pointer
artifact rather than part of the chart; the colours it swaps for the other
theme) is restored before it returns, so the screen never repaints in between.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import pyqtgraph as pg
from pyqtgraph.exporters import ImageExporter
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBitmap, QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPen, QRegion,
)

from .. import theme
from ..color import contrast
from ..paths import ASSETS_DIR

#: The export is this many times the on-screen pixel size.
SCALE = 2
#: Logical layout of the frame around the plots, before scaling.
PAD = 10
TITLE_H = 20
LEGEND_ROW_H = 16
FOOTER_H = 6  # a margin under the time axis, nothing in it
SWATCH_W = 14
LEGEND_GAP = 14
#: Height of the logo mark at the header's top right: the title row and the
#: first legend row, so it sits level with the symbol.
MARK_H = TITLE_H + LEGEND_ROW_H - 4
#: The mark (the A and its bars) is the left part of the logo lockup, before
#: the divider at ~26% of its width; the wordmark follows.
_MARK_FRACTION = 0.25
_LOGO_FILES = {"dark": "aurantium_logo_ondark.png", "light": "aurantium_logo.png"}


@dataclass
class LegendEntry:
    label: str
    colors: tuple  # one colour, or two for a series painted up/down
    dashed: bool = False


def plot_panes(chart) -> list:
    """The visible plot widgets, top to bottom, as laid out on screen."""
    layout = chart.content_layout
    panes = []
    for i in range(layout.count()):
        w = layout.itemAt(i).widget()
        if isinstance(w, pg.PlotWidget) and w.isVisible():
            panes.append(w)
    return panes


def caption(chart) -> tuple[str, str]:
    """(symbol, "interval · range [· log]") for the header."""
    from .chart import lookback_label

    rng = chart._range
    if "preset" in rng:
        span = rng["preset"]
    elif "lookback" in rng:
        span = lookback_label(rng["lookback"])
    else:
        span = f"{rng.get('start')} → {rng.get('end')}"
    parts = [chart._interval, span]
    if chart._log_on:
        parts.append("log")
    return chart.current_symbol or "", "  ·  ".join(parts)


def _plotted(inst) -> bool:
    """Whether the indicator drew anything. An SMA 200 over 130 intraday bars
    has nothing to draw, and a legend entry for a line that is not there
    would send the reader looking for it."""
    for item in list(inst.items) + list(inst.pane_items):
        if isinstance(item, pg.PlotDataItem):
            ys = item.yData
            if ys is not None and len(ys) and not all(y != y for y in ys):  # not all NaN
                return True
        elif isinstance(item, pg.BarGraphItem):
            if len(item.opts.get("height") if item.opts.get("height") is not None else []):
                return True
    return False


def legend(chart) -> list[LegendEntry]:
    """Every visible series, in the colour it is plotted in."""
    colors = chart._colors
    entries = []
    if chart._chart_type in ("line", "area"):
        entries.append(LegendEntry(chart.current_symbol or "price", (colors["line"],)))
    else:
        entries.append(LegendEntry(chart.current_symbol or "price", (colors["up"], colors["down"])))
    for inst in chart._indicators:
        if not inst.on or not _plotted(inst):
            continue
        if inst.kind == "volume":
            entries.append(LegendEntry(inst.label(), (colors["up"], colors["down"])))
        elif inst.kind == "macd":
            entries.append(LegendEntry(inst.label(), (inst.color,)))
            entries.append(LegendEntry("Signal", (theme.FG_DIM,)))
        else:
            entries.append(LegendEntry(inst.label(), (inst.color,)))
    for cmp in chart._compares:
        if cmp.on and cmp.curve is not None:
            text = cmp.chip.text() if cmp.chip is not None else f"vs {cmp.symbol}"
            entries.append(LegendEntry(text, (cmp.color,), dashed=True))
    return entries


@lru_cache(maxsize=8)
def logo_mark(theme_name: str, height: int) -> QImage | None:
    """The logo mark (the A and its bars, no wordmark) for ``theme_name``'s
    background, trimmed to its ink and ``height`` device pixels tall; None if
    the asset is missing."""
    image = QImage(str(ASSETS_DIR / "images" / _LOGO_FILES[theme_name]))
    if image.isNull():
        return None
    image = image.copy(0, 0, round(image.width() * _MARK_FRACTION), image.height())
    ink = QRegion(QBitmap.fromImage(image.createAlphaMask())).boundingRect()
    if not ink.isEmpty():
        image = image.copy(ink)
    return image.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)


def _font(size: int, bold: bool = False, mono: bool = False, spacing: float = 0.0) -> QFont:
    f = QFont(theme.MONO_FONT if mono else theme.UI_FONT)
    f.setPixelSize(size)
    if bold:
        f.setWeight(QFont.Weight.Bold)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def _legend_rows(entries: list[LegendEntry], width: float) -> list[list]:
    """Flow the entries into rows that fit ``width``; each item is
    (entry, x, text_width)."""
    fm = QFontMetricsF(_font(theme.FONT_MD))
    rows: list[list] = [[]]
    x = 0.0
    for e in entries:
        tw = fm.horizontalAdvance(e.label)
        w = SWATCH_W + 5 + tw
        if rows[-1] and x + w > width:
            rows.append([])
            x = 0.0
        rows[-1].append((e, x, tw))
        x += w + LEGEND_GAP
    return rows


def _swatch(p: QPainter, x: float, y: float, entry: LegendEntry) -> None:
    if len(entry.colors) == 2:  # a candle pair: up, then down
        half = SWATCH_W / 2 - 1
        p.fillRect(QRectF(x, y - 4, half, 8), QColor(entry.colors[0]))
        p.fillRect(QRectF(x + half + 2, y - 4, half, 8), QColor(entry.colors[1]))
        return
    pen = QPen(QColor(entry.colors[0]), 2.5)
    if entry.dashed:  # dashed on the chart, so dashed in the legend
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setDashPattern([1.6, 1.2])
    else:
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(x + 1, y), QPointF(x + SWATCH_W - 1, y))


# -- the other theme ---------------------------------------------------------

#: The palette roles a chart draws in. Only these are carried across: a colour
#: the chart borrows from nowhere in the palette (a drawing's blue) is kept.
_CHART_ROLES = ("BG", "FG", "FG_DIM", "FG_MUTED", "ACCENT", "UP", "DOWN")
#: chart._colors key -> the palette role its default comes from
_COLOR_ROLES = {"up": "UP", "down": "DOWN", "line": "ACCENT", "grid": "FG_DIM", "bg": "BG"}


def color_map(chart, target: str) -> dict[str, str]:
    """On-screen colour -> its colour in ``target`` (lowercase #rrggbb keys).

    Palette colours follow their role, series colours their slot. A colour the
    user picked is kept when it still reads on the target canvas (3:1) and
    otherwise falls back to the role or slot it stands in for, the same rule a
    restored layout follows (see ``_restored_indicator_color`` in chart.py).
    """
    here = theme.active_theme()
    if target == here:
        return {}
    src, dst = theme.palette_colors(here), theme.palette_colors(target)
    cmap = {src[role].lower(): dst[role] for role in _CHART_ROLES}
    for a, b in zip(theme.series_palette(here), theme.series_palette(target)):
        cmap[a.lower()] = b
    bg = dst["BG"]

    def _keep_or(color: str, fallback: str) -> None:
        key = QColor(color).name()
        if key not in cmap and contrast(key, bg) < 3.0:
            cmap[key] = fallback

    for key, role in _COLOR_ROLES.items():
        color = QColor(chart._colors[key]).name()
        if key == "bg":
            cmap[color] = bg  # the canvas always takes the theme's
        else:
            _keep_or(color, dst[role])
    for i, inst in enumerate(chart._indicators):
        _keep_or(inst.color, theme.series_color(i, target))
    for i, cmp in enumerate(chart._compares):
        _keep_or(cmp.color, theme.series_color(i, target))
    return cmap


def _color(color: QColor, cmap: dict) -> QColor | None:
    to = cmap.get(color.name())
    if to is None:
        return None
    out = QColor(to)
    out.setAlpha(color.alpha())  # volume bars, band fills: keep their alpha
    return out


def _pen(pen, cmap: dict) -> QPen | None:
    """A recoloured copy of ``pen``, or None when it keeps its colour."""
    if pen is None:
        return None
    pen = pg.mkPen(pen)
    c = _color(pen.color(), cmap)
    if c is None:
        return None
    pen = QPen(pen)
    pen.setColor(c)
    return pen


def _brush(brush, cmap: dict) -> QBrush | None:
    if brush is None:
        return None
    brush = pg.mkBrush(brush)
    c = _color(brush.color(), cmap)
    if c is None:
        return None
    brush = QBrush(brush)
    brush.setColor(c)
    return brush


def _recolor(panes: list, cmap: dict, undo: list) -> None:
    """Swap every mapped colour in ``panes``, appending a step to ``undo`` for
    each — as it goes, so a failure part-way still leaves the way back."""
    if not cmap:
        return

    def swap(setter, old, new) -> None:
        if new is not None:
            setter(new)
            undo.append(lambda: setter(old))

    for pane in panes:
        bg = QBrush(pane.backgroundBrush())
        swap(pane.setBackground, bg, _brush(bg, cmap))
        for item in pane.scene().items():
            if isinstance(item.parentItem(), pg.PlotDataItem):
                continue  # its curve and symbols: recoloured through it
            if isinstance(item, pg.AxisItem):  # the axis pen also draws the grid
                old = QPen(item.pen())
                swap(item.setPen, old, _pen(old, cmap))
                old = QPen(item.textPen())
                swap(item.setTextPen, old, _pen(old, cmap))
            elif isinstance(item, pg.PlotDataItem):
                for opt, setter in (("pen", item.setPen), ("shadowPen", item.setShadowPen),
                                    ("symbolPen", item.setSymbolPen)):
                    old = item.opts.get(opt)
                    swap(setter, old, _pen(old, cmap))
                for opt, setter in (("fillBrush", item.setFillBrush),
                                    ("symbolBrush", item.setSymbolBrush)):
                    old = item.opts.get(opt)
                    swap(setter, old, _brush(old, cmap))
            elif isinstance(item, pg.BarGraphItem):
                old, new = {}, {}
                for opt, fn in (("pen", _pen), ("brush", _brush)):
                    if (c := fn(item.opts.get(opt), cmap)) is not None:
                        old[opt], new[opt] = item.opts[opt], c
                for opt, fn in (("pens", _pen), ("brushes", _brush)):
                    seq = item.opts.get(opt)
                    if seq is None:
                        continue
                    mapped = [fn(v, cmap) for v in seq]
                    if any(m is not None for m in mapped):
                        old[opt] = list(seq)
                        new[opt] = [m if m is not None else v for m, v in zip(mapped, seq)]
                if new:
                    item.setOpts(**new)
                    undo.append(lambda item=item, old=old: item.setOpts(**old))
            elif isinstance(item, pg.ScatterPlotItem):  # the events lane's marks
                seq = list(item.data["brush"])
                mapped = [_brush(b, cmap) for b in seq]
                if any(m is not None for m in mapped):
                    swap(item.setBrush, seq,
                         [m if m is not None else b for m, b in zip(mapped, seq)])
            elif isinstance(item, pg.InfiniteLine):
                swap(item.setPen, item.pen, _pen(item.pen, cmap))
            elif isinstance(item, pg.FillBetweenItem):
                old = QBrush(item.brush())
                swap(item.setBrush, old, _brush(old, cmap))
            elif isinstance(item, pg.TextItem):
                old = QColor(item.color)
                swap(item.setColor, old, _color(old, cmap))
            elif hasattr(item, "set_colors"):  # the candle and OHLC bar items
                up, down = item._up, item._down
                to_up = cmap.get(QColor(up).name(), up)
                to_down = cmap.get(QColor(down).name(), down)
                if (to_up, to_down) != (up, down):
                    item.set_colors(to_up, to_down)
                    undo.append(lambda item=item, c=(up, down): item.set_colors(*c))


def _render_pane(p: QPainter, pane, target: QRectF, scale: int) -> None:
    """Re-render one pane's scene into ``target`` (device pixels)."""
    p.fillRect(target, pane.backgroundBrush())
    scene = pane.scene()
    exporter = ImageExporter(scene)
    source = exporter.getSourceRect()
    p.save()
    try:
        exporter.setExportMode(True, {
            "antialias": True,
            "background": pane.backgroundBrush().color(),
            "painter": p,
            "resolutionScale": scale,
        })
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        scene.render(p, target, QRectF(source))
    finally:
        exporter.setExportMode(False)
        p.restore()


def render(chart, scale: int = SCALE, theme_name: str | None = None) -> QImage | None:
    """The chart's current state as an image, ``scale`` times its on-screen
    size, in ``theme_name`` (default: the one on screen). None when there is
    nothing plotted."""
    panes = plot_panes(chart)
    if not panes or not chart._hist_t:
        return None
    theme_name = theme_name if theme_name in theme.THEMES else theme.active_theme()
    pal = theme.palette_colors(theme_name)
    cmap = color_map(chart, theme_name)
    top = panes[0].mapTo(chart, panes[0].rect().topLeft()).y()
    bottom = max(w.mapTo(chart, w.rect().bottomLeft()).y() + 1 for w in panes)
    left = min(w.mapTo(chart, w.rect().topLeft()).x() for w in panes)
    plot_w = max(w.width() for w in panes)
    plot_h = bottom - top

    entries = [
        LegendEntry(e.label, tuple(cmap.get(QColor(c).name(), c) for c in e.colors), e.dashed)
        for e in legend(chart)
    ]
    mark = logo_mark(theme_name, round(MARK_H * scale))
    mark_room = mark.width() / scale + PAD if mark is not None else 0
    rows = _legend_rows(entries, plot_w - 2 * PAD - mark_room)  # never under the mark
    header_h = PAD + TITLE_H + LEGEND_ROW_H * len(rows) + PAD // 2
    total_h = header_h + plot_h + FOOTER_H

    image = QImage(plot_w * scale, total_h * scale, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(1.0)
    bg = QColor(cmap.get(QColor(chart._colors["bg"]).name(), chart._colors["bg"]))
    image.fill(bg)

    crosshair = [i for i in (chart._cross_v, chart._cross_h, chart._cross_text) if i.isVisible()]
    for item in crosshair:
        item.setVisible(False)
    undo: list = []
    p = QPainter(image)
    try:
        _recolor(panes, cmap, undo)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        # -- plots, at their on-screen offsets so the panes keep their gaps
        for w in panes:
            at = w.mapTo(chart, w.rect().topLeft())
            target = QRectF(
                (at.x() - left) * scale,
                (header_h + at.y() - top) * scale,
                w.width() * scale,
                w.height() * scale,
            )
            _render_pane(p, w, target, scale)

        # -- header, in logical units
        p.save()
        p.scale(scale, scale)
        symbol, rest = caption(chart)
        p.setFont(_font(theme.FONT_LG, bold=True, mono=True))
        p.setPen(QColor(pal["LABEL"]))
        baseline = PAD + TITLE_H - 6
        p.drawText(QPointF(PAD, baseline), symbol)
        sym_w = QFontMetricsF(p.font()).horizontalAdvance(symbol)
        p.setFont(_font(theme.FONT_MD, mono=True))
        p.setPen(QColor(pal["FG_DIM"]))
        p.drawText(QPointF(PAD + sym_w + (10 if symbol else 0), baseline), rest)

        p.setFont(_font(theme.FONT_MD))
        fm = QFontMetricsF(p.font())
        for r, row in enumerate(rows):
            mid = PAD + TITLE_H + LEGEND_ROW_H * r + LEGEND_ROW_H / 2
            for entry, x, _tw in row:
                _swatch(p, PAD + x, mid, entry)
                p.setPen(QColor(pal["FG"]))
                p.drawText(
                    QPointF(PAD + x + SWATCH_W + 5, mid + (fm.ascent() - fm.descent()) / 2),
                    entry.label,
                )

        p.restore()

        # -- the logo mark at the top right, in device pixels from a 2x-ready bitmap
        if mark is not None:
            p.drawImage(QPointF((plot_w - PAD) * scale - mark.width(), PAD * scale), mark)
    finally:
        p.end()
        for step in reversed(undo):
            step()
        for item in crosshair:
            item.setVisible(True)
    return image


def plot_size(chart) -> tuple[int, int]:
    """The on-screen size of the plot area the export reproduces (for tests
    and the status line)."""
    panes = plot_panes(chart)
    if not panes:
        return (0, 0)
    top = panes[0].mapTo(chart, panes[0].rect().topLeft()).y()
    bottom = max(w.mapTo(chart, w.rect().bottomLeft()).y() + 1 for w in panes)
    return (max(w.width() for w in panes), bottom - top)

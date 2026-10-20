"""The chart as an image: what is on screen, at twice the pixels.

The old export was ``panel.grab()`` — a screenshot of the whole panel at screen
resolution, range chips and indicator row included, with no record of what
was plotted beyond the chips' own text. This renders the plot area only (the
price pane, the events lane, every indicator sub-pane, their axes) and puts a
header over it that says what it is: symbol, interval, range, and a legend
entry in its plotted colour for every series on it. A small wordmark sits
under it.

Crisp, not upscaled: each pane's scene is re-rendered into the image through
pyqtgraph's export mode (the path its own ImageExporter takes), so lines and
axis text are drawn at 2x rather than stretched from 1x.

Nothing on screen changes. The render is synchronous — no event loop turns
while it runs — and the one thing it hides (the crosshair, a pointer artifact
rather than part of the chart) is restored before it returns, so the screen
never repaints in between.
"""

from __future__ import annotations

from dataclasses import dataclass

import pyqtgraph as pg
from pyqtgraph.exporters import ImageExporter
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen

from .. import theme

#: The export is this many times the on-screen pixel size.
SCALE = 2
#: Logical layout of the frame around the plots, before scaling.
PAD = 10
TITLE_H = 20
LEGEND_ROW_H = 16
FOOTER_H = 18
SWATCH_W = 14
LEGEND_GAP = 14
WORDMARK = "AURANTIUM"


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


def render(chart, scale: int = SCALE) -> QImage | None:
    """The chart's current state as an image, ``scale`` times its on-screen
    size. None when there is nothing plotted."""
    panes = plot_panes(chart)
    if not panes or not chart._hist_t:
        return None
    top = panes[0].mapTo(chart, panes[0].rect().topLeft()).y()
    bottom = max(w.mapTo(chart, w.rect().bottomLeft()).y() + 1 for w in panes)
    left = min(w.mapTo(chart, w.rect().topLeft()).x() for w in panes)
    plot_w = max(w.width() for w in panes)
    plot_h = bottom - top

    entries = legend(chart)
    rows = _legend_rows(entries, plot_w - 2 * PAD)
    header_h = PAD + TITLE_H + LEGEND_ROW_H * len(rows) + PAD // 2
    total_h = header_h + plot_h + FOOTER_H

    image = QImage(plot_w * scale, total_h * scale, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(1.0)
    bg = QColor(chart._colors["bg"])
    image.fill(bg)

    crosshair = [i for i in (chart._cross_v, chart._cross_h, chart._cross_text) if i.isVisible()]
    for item in crosshair:
        item.setVisible(False)
    p = QPainter(image)
    try:
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

        # -- header and footer, in logical units
        p.save()
        p.scale(scale, scale)
        symbol, rest = caption(chart)
        p.setFont(_font(theme.FONT_LG, bold=True, mono=True))
        p.setPen(QColor(theme.LABEL))
        baseline = PAD + TITLE_H - 6
        p.drawText(QPointF(PAD, baseline), symbol)
        sym_w = QFontMetricsF(p.font()).horizontalAdvance(symbol)
        p.setFont(_font(theme.FONT_MD, mono=True))
        p.setPen(QColor(theme.FG_DIM))
        p.drawText(QPointF(PAD + sym_w + (10 if symbol else 0), baseline), rest)

        p.setFont(_font(theme.FONT_MD))
        fm = QFontMetricsF(p.font())
        for r, row in enumerate(rows):
            mid = PAD + TITLE_H + LEGEND_ROW_H * r + LEGEND_ROW_H / 2
            for entry, x, _tw in row:
                _swatch(p, PAD + x, mid, entry)
                p.setPen(QColor(theme.FG))
                p.drawText(
                    QPointF(PAD + x + SWATCH_W + 5, mid + (fm.ascent() - fm.descent()) / 2),
                    entry.label,
                )

        p.setFont(_font(theme.FONT_SM, bold=True, spacing=2.0))
        p.setPen(QColor(theme.FG_MUTED))
        fm = QFontMetricsF(p.font())
        mark_w = fm.horizontalAdvance(WORDMARK)
        p.drawText(
            QPointF(plot_w - PAD - mark_w, header_h + plot_h + FOOTER_H / 2 + (fm.ascent() - fm.descent()) / 2),
            WORDMARK,
        )
        p.restore()
    finally:
        p.end()
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

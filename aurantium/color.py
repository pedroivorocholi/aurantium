"""Colour maths — sRGB, OKLab, colour-vision deficiency, WCAG contrast.

These are published transforms, not judgement calls: sRGB→linear, Björn
Ottosson's OKLab, the Machado/Oliveira/Fernandes (2009) CVD matrices at
severity 1.0, ΔE as Euclidean distance in OKLab ×100, and the WCAG contrast
ratio. **Do not "tune" any constant here to make a colour pass.**

This module lives in the app rather than in ``tests/`` because runtime code
needs it. The palette guard has had these transforms since the chart palette
was fixed, but they sat in ``tests/palette_checks.py``, where the app could not
reach them — so every colour decision made at *runtime* went on being made by
eye. The sector heatmap interpolated its ramp in gamma-encoded sRGB and painted
hardcoded near-white labels over it; the options chain hardcoded two dark-only
tints. Both are the same failure: maths that existed, one import away, in a
place the code that needed it could not import from.

``tests/palette_checks.py`` now re-exports from here and keeps only the
*thresholds* and the ``check()`` orchestrator. That seam is deliberate —
transforms are app code, thresholds are test policy — and
``test_chart_palette.py::test_the_port_reproduces_the_reference_validator``
still pins the numbers against the reference implementation, so it doubles as
the guard that moving them did not drift them.

Deliberately dependency-free (stdlib ``math`` only): no Qt, no app imports.
That keeps it importable from a test with no QApplication, and keeps it usable
from ``theme.py`` at import time, before anything Qt-shaped exists.
"""

from __future__ import annotations

import math

# Machado, Oliveira & Fernandes (2009), severity 1.0, applied in linear RGB.
MACHADO = {
    "protan": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deutan": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritan": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}


# -- sRGB ------------------------------------------------------------------

def _srgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.strip().lstrip("#")
    if len(h) == 3:  # #abc → #aabbcc
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _s2lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lin2s(c: float) -> float:
    c = min(1.0, max(0.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def linear(hex_color: str) -> tuple[float, float, float]:
    return tuple(_s2lin(c) for c in _srgb(hex_color))


def to_hex(rgb_linear) -> str:
    """Linear RGB back to an ``#rrggbb`` string, clamped into gamut."""
    return "#" + "".join(f"{round(_lin2s(c) * 255):02x}" for c in rgb_linear)


# -- WCAG ------------------------------------------------------------------

def rel_luminance(hex_color: str) -> float:
    r, g, b = linear(hex_color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((rel_luminance(a), rel_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# -- OKLab -----------------------------------------------------------------

def _oklab_from_linear(rgb) -> tuple[float, float, float]:
    r, g, b = rgb
    l = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    )


def _linear_from_oklab(lab) -> tuple[float, float, float]:
    """Inverse of :func:`_oklab_from_linear`. Needed to interpolate *in* OKLab
    and come back out to something Qt can paint."""
    L, a, b = lab
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (
        +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )


def oklab(hex_color: str) -> tuple[float, float, float]:
    return _oklab_from_linear(linear(hex_color))


def oklch(hex_color: str) -> tuple[float, float]:
    """(lightness, chroma)."""
    L, a, b = oklab(hex_color)
    return L, math.hypot(a, b)


# -- differences -----------------------------------------------------------

def _simulate(hex_color: str, kind: str):
    r, g, b = linear(hex_color)
    M = MACHADO[kind]
    return tuple(
        min(1.0, max(0.0, M[i][0] * r + M[i][1] * g + M[i][2] * b))
        for i in range(3)
    )


def delta_e(c1: str, c2: str, kind: str | None = None) -> float:
    """Euclidean distance in OKLab ×100. ``kind=None`` is normal vision."""
    a = _oklab_from_linear(_simulate(c1, kind) if kind else linear(c1))
    b = _oklab_from_linear(_simulate(c2, kind) if kind else linear(c2))
    return 100 * math.dist(a, b)


def cvd_separation(c1: str, c2: str) -> float:
    """The worst of the two red-green deficiencies — the number the CVD check
    gates on."""
    return min(delta_e(c1, c2, "protan"), delta_e(c1, c2, "deutan"))


# -- what the app actually calls -------------------------------------------

def mix_oklab(a: str, b: str, t: float) -> str:
    """Blend ``a``→``b`` by ``t`` (0..1), interpolating in OKLab.

    The obvious version — averaging the sRGB bytes, which is what the sector
    heatmap did — is *slightly* non-uniform, not badly broken: measured across
    the light theme's neutral→UP ramp in eleven steps, the two differ by at most
    ΔE 2.1 (mean 1.2), and the ratio between the largest and smallest perceived
    lightness step improves from 1.3× to 1.1×. Worth having, and free, but the
    honest claim is "measurably more even", not "fixes a dead zone".

    Use it because it is correct by construction on ramps where the endpoints
    are further apart in lightness than that pair, and because a ramp encoding
    magnitude should not have its evenness depend on which two colours it runs
    between. The large win on the heatmap is :func:`readable_on`, not this.
    """
    t = min(1.0, max(0.0, float(t)))
    la, lb = oklab(a), oklab(b)
    return to_hex(_linear_from_oklab(tuple(x + (y - x) * t for x, y in zip(la, lb))))


def readable_on(fill: str, candidates: tuple[str, ...] = ("#ffffff", "#0b0c0e")) -> str:
    """Whichever candidate ink has the most WCAG contrast against ``fill``.

    For text painted on a computed surface — a heat tile, a tinted row — where
    no palette constant can be right for every value the surface takes. The
    heatmap's labels were hardcoded near-white, which is correct at the
    saturated end of the dark ramp and invisible at the pale end of the light
    one; this picks per value instead.

    Defaults to ink/paper rather than ``FG``/``BG`` on purpose: a heat tile is
    its own surface, not the panel's, so the theme's foreground has no claim on
    it.
    """
    return max(candidates, key=lambda ink: contrast(ink, fill))

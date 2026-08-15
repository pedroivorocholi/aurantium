"""The computable colour checks, ported so the palette guard runs in CI.

A faithful port of the data-viz validator's maths — sRGB→linear→OKLab, the
Machado/Oliveira/Fernandes (2009) colour-vision-deficiency transforms at
severity 1.0, ΔE as Euclidean distance in OKLab ×100, and WCAG contrast. Ported
rather than shelled out to so the guard has no Node dependency; the numbers are
cross-checked against the reference implementation in
``test_chart_palette.py::test_the_port_reproduces_the_reference_validator``.

None of these values are judgement calls — they are the published transforms.
Do not "tune" them to make a palette pass.
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

#: OKLCH lightness band per mode.
BAND = {"light": (0.43, 0.77), "dark": (0.48, 0.67)}
CHROMA_FLOOR = 0.10
CVD_TARGET = 8.0    # ΔE, min(protan, deutan)
CVD_FLOOR = 6.0     # legal only with secondary encoding (a labelled legend chip)
NORMAL_FLOOR = 15.0  # hard gate, unsimulated vision
CONTRAST_MIN = 3.0   # vs the chart surface


def _srgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.strip().lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _s2lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear(hex_color: str) -> tuple[float, float, float]:
    return tuple(_s2lin(c) for c in _srgb(hex_color))


def rel_luminance(hex_color: str) -> float:
    r, g, b = linear(hex_color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((rel_luminance(a), rel_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


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


def oklch(hex_color: str) -> tuple[float, float]:
    """(lightness, chroma)."""
    L, a, b = _oklab_from_linear(linear(hex_color))
    return L, math.hypot(a, b)


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


def _pairs(palette, mode: str):
    if mode == "all":
        return [
            (palette[i], palette[j])
            for i in range(len(palette))
            for j in range(i + 1, len(palette))
        ]
    return list(zip(palette, palette[1:]))


def check(palette, *, mode: str, surface: str, pairs: str = "adjacent") -> dict:
    """Run every computable check. Returns a report dict; ``failures`` is empty
    when the palette is legal."""
    lo, hi = BAND[mode]
    failures: list[str] = []
    warnings: list[str] = []

    offband = [c for c in palette if not (lo <= oklch(c)[0] <= hi)]
    if offband:
        failures.append(
            f"lightness band {lo}–{hi}: "
            + ", ".join(f"{c} L={oklch(c)[0]:.3f}" for c in offband)
        )

    low_chroma = [c for c in palette if oklch(c)[1] < CHROMA_FLOOR]
    if low_chroma:
        failures.append(
            "chroma floor: " + ", ".join(f"{c} C={oklch(c)[1]:.3f}" for c in low_chroma)
        )

    pairlist = _pairs(list(palette), pairs)
    worst_cvd = min(
        ((cvd_separation(a, b), a, b) for a, b in pairlist), default=(99.0, "", "")
    )
    if worst_cvd[0] < CVD_FLOOR:
        failures.append(
            f"CVD separation: {worst_cvd[1]}↔{worst_cvd[2]} ΔE {worst_cvd[0]:.1f}"
            f" (floor {CVD_FLOOR})"
        )
    elif worst_cvd[0] < CVD_TARGET:
        warnings.append(
            f"CVD separation in the floor band: {worst_cvd[1]}↔{worst_cvd[2]}"
            f" ΔE {worst_cvd[0]:.1f} — legal only with secondary encoding"
        )

    worst_normal = min(
        ((delta_e(a, b), a, b) for a, b in pairlist), default=(99.0, "", "")
    )
    if worst_normal[0] < NORMAL_FLOOR:
        failures.append(
            f"normal-vision floor: {worst_normal[1]}↔{worst_normal[2]}"
            f" ΔE {worst_normal[0]:.1f} (floor {NORMAL_FLOOR})"
        )

    low_contrast = [
        (c, contrast(c, surface)) for c in palette if contrast(c, surface) < CONTRAST_MIN
    ]
    if low_contrast:
        warnings.append(
            "contrast vs surface below 3:1: "
            + ", ".join(f"{c} {r:.2f}:1" for c, r in low_contrast)
        )

    return {
        "failures": failures,
        "warnings": warnings,
        "worst_cvd": worst_cvd,
        "worst_normal": worst_normal,
    }

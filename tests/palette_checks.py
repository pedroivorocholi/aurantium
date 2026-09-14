"""The computable colour checks: thresholds and the orchestrator.

The *transforms* — sRGB, OKLab, the Machado/Oliveira/Fernandes CVD matrices,
ΔE, WCAG contrast — now live in ``aurantium.color``, because runtime code needs
them too (the sector heatmap interpolates its ramp and picks its label ink with
them). They are re-exported here so every existing ``from palette_checks import
...`` keeps working unchanged, and so
``test_the_port_reproduces_the_reference_validator`` keeps pinning them against
the reference implementation — which makes it the guard that moving them did
not drift them.

What stays here is *test policy*: the thresholds and ``check()``. That is the
right seam. Ported rather than shelled out to Node so the guard has no Node
dependency; the numbers are cross-checked against the reference implementation.

None of these values are judgement calls — they are the published transforms.
Do not "tune" them to make a palette pass.
"""

from __future__ import annotations

from aurantium.color import (  # noqa: F401  (re-exported for the test modules)
    MACHADO,
    _oklab_from_linear,
    _simulate,
    _srgb,
    _s2lin,
    contrast,
    cvd_separation,
    delta_e,
    linear,
    oklch,
    rel_luminance,
)

#: OKLCH lightness band per mode.
BAND = {"light": (0.43, 0.77), "dark": (0.48, 0.67)}
CHROMA_FLOOR = 0.10
CVD_TARGET = 8.0    # ΔE, min(protan, deutan)
CVD_FLOOR = 6.0     # legal only with secondary encoding (a labelled legend chip)
NORMAL_FLOOR = 15.0  # hard gate, unsimulated vision
CONTRAST_MIN = 3.0   # vs the chart surface


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

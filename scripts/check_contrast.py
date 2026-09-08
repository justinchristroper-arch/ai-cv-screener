#!/usr/bin/env python3
"""Check the UI palette against WCAG AA contrast minimums.

"Contrast meets WCAG AA" is the kind of claim that is easy to make and easy to
stop being true. This reads the actual custom properties out of
`frontend/src/index.css` — both the light palette on `:root` and the dark one in
the `prefers-color-scheme` block — and computes the real ratio for every
foreground/background pair the interface actually renders.

Two thresholds, because WCAG has two:

* **4.5:1** for normal-size text (1.4.3 Contrast (Minimum), level AA).
* **3:1** for the visual boundary of a user-interface component (1.4.11
  Non-text Contrast, level AA). Input borders live here, which is what caught a
  real failure: the original `--border-strong` managed 1.63:1 against white.

The pair list is maintained by hand rather than derived, because only a person
knows which token is drawn on which. That is this tool's main limitation: it
checks the pairs it is told about, and a new component with a new combination
has to be added here. It also cannot see opacity, gradients, or text drawn over
an image — none of which this interface uses.

Usage:
    python scripts/check_contrast.py        # check
    python scripts/check_contrast.py -v     # print every pair, not only failures

Exit code is 0 when every pair meets its minimum, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "frontend" / "src" / "index.css"

#: (label, foreground token, background token, minimum ratio).
#: 4.5 for text, 3.0 for the boundary of something you can click or type into.
PAIRS: tuple[tuple[str, str, str, float], ...] = (
    ("body text on the page", "fg", "bg", 4.5),
    ("body text on a panel", "fg", "surface", 4.5),
    ("body text on an alt surface", "fg", "surface-alt", 4.5),
    ("muted text on the page", "muted", "bg", 4.5),
    ("muted text on a panel", "muted", "surface", 4.5),
    ("muted text on an alt surface", "muted", "surface-alt", 4.5),
    ("link text on the page", "accent", "bg", 4.5),
    ("link text on a panel", "accent", "surface", 4.5),
    ("primary button label", "accent-fg", "accent", 4.5),
    ("accent pill text", "accent", "accent-soft", 4.5),
    ("ok pill and callout text", "ok", "ok-bg", 4.5),
    ("warn pill and callout text", "warn", "warn-bg", 4.5),
    ("danger pill and callout text", "danger", "danger-bg", 4.5),
    ("callout text on info background", "fg", "info-bg", 4.5),
    # 1.4.11: --border-strong is the border of every input, textarea, select,
    # quiet button and the file drop zone.
    ("control border on a panel", "border-strong", "surface", 3.0),
    ("control border on the page", "border-strong", "bg", 3.0),
    ("control border on an alt surface", "border-strong", "surface-alt", 3.0),
)


def _channel(value: int) -> float:
    x = value / 255
    return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_colour: str) -> float:
    digits = hex_colour.lstrip("#")
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    r, g, b = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(first: str, second: str) -> float:
    a, b = relative_luminance(first), relative_luminance(second)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


_TOKEN = re.compile(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{3,8})\s*;")
_DARK_BLOCK = re.compile(r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{(.*?)\n\}", re.S | re.I)


def read_palettes(css_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """The light palette, and the dark one with its overrides applied.

    Parsed rather than duplicated here, so this tool cannot describe a palette
    the stylesheet no longer has.
    """
    css = css_path.read_text(encoding="utf-8")

    dark_match = _DARK_BLOCK.search(css)
    if dark_match is None:
        raise SystemExit(f"{css_path}: no prefers-color-scheme: dark block found")
    dark_source = dark_match.group(1)

    # Everything outside the dark block is the light palette.
    light_source = css[: dark_match.start()] + css[dark_match.end() :]

    light = dict(_TOKEN.findall(light_source))
    dark = dict(light)
    dark.update(_TOKEN.findall(dark_source))
    return light, dark


def check(palette: dict[str, str], name: str, verbose: bool) -> list[str]:
    failures: list[str] = []
    for label, fg, bg, minimum in PAIRS:
        missing = [token for token in (fg, bg) if token not in palette]
        if missing:
            failures.append(f"{name}: {label} — no such token: {', '.join(missing)}")
            continue
        ratio = contrast_ratio(palette[fg], palette[bg])
        if ratio < minimum:
            failures.append(
                f"{name}: {label} — {ratio:.2f}:1, needs {minimum}:1 "
                f"({palette[fg]} on {palette[bg]})"
            )
        elif verbose:
            print(f"  ok   {ratio:5.2f}:1  (needs {minimum})  {name}: {label}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true", help="print every pair")
    parser.add_argument("--css", type=Path, default=CSS, help="stylesheet to read")
    args = parser.parse_args(argv)

    light, dark = read_palettes(args.css)
    failures = check(light, "light", args.verbose) + check(dark, "dark", args.verbose)

    print()
    if failures:
        print(f"FAILED - {len(failures)} pair(s) below WCAG AA:\n")
        for failure in failures:
            print(f"  {failure}")
        return 1

    print(f"OK - {len(PAIRS) * 2} colour pair(s) meet WCAG AA in both themes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

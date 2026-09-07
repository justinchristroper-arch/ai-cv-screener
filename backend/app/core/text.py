"""Text normalization for extracted document text.

This is the substrate every evidence span is later verified against, so two
properties matter more than anything else:

* **Determinism.** The same input always produces the same output. No locale,
  no clock, no randomness.
* **Faithfulness.** Nothing is summarized, rewritten, inferred, reordered, or
  removed for being uninteresting. The transformations below only touch
  whitespace, line endings, and characters that carry no meaning in extracted
  text.

`NORMALIZATION_VERSION` is stored on every `parsed_document` and every
`evidence_span`. If these rules change, the version must change with them:
previously verified spans may stop matching, and that has to be a visible,
explainable event rather than a mysterious drop in evidence validity
(docs/data-model.md section 4.9).
"""

from __future__ import annotations

import re
import unicodedata

#: Bump this whenever any rule below changes.
NORMALIZATION_VERSION = "text-normalize-v1"

#: Control characters carry no meaning in extracted text and break comparisons.
#: Tab and newline are deliberately excluded — they are real layout.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: Unicode spaces that should read as an ordinary space: non-breaking space,
#: the en/em/thin space family, narrow and medium mathematical space, and the
#: ideographic space. Written as escapes, not literals, so the rule is
#: readable in a diff instead of being a row of invisible characters.
_UNICODE_SPACES = re.compile("[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]")

#: Zero-width characters: the joiners and the byte-order mark. Deleted rather
#: than turned into spaces, because they occupy no width in the source either.
_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\ufeff]")

#: Runs of spaces and tabs collapse to one space. PDF extraction routinely
#: emits long runs where the original had column alignment; that alignment is
#: already lost by the time text comes out, so preserving the runs preserves
#: noise rather than meaning.
_HORIZONTAL_RUNS = re.compile(r"[ \t]+")

#: Three or more blank lines collapse to one blank line. Paragraph structure is
#: kept; arbitrary vertical padding is not.
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")

_TRAILING_SPACE_PER_LINE = re.compile(r"[ \t]+$", re.MULTILINE)


def normalize_text(text: str) -> str:
    """Normalize extracted text without changing what it says.

    Applied in this order:

    1. Unicode NFC composition, so visually identical text compares equal.
    2. Line endings unified to ``\\n``.
    3. Zero-width characters removed; other Unicode spaces become a plain space.
    4. Remaining control characters removed.
    5. Runs of spaces and tabs collapsed to one space.
    6. Trailing whitespace stripped from every line.
    7. Three or more consecutive newlines collapsed to two.
    8. Leading and trailing whitespace stripped from the whole string.
    """
    # NFC first: composing characters before stripping means a combining accent
    # is folded into its base letter rather than orphaned by a later step.
    text = unicodedata.normalize("NFC", text)

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    text = _ZERO_WIDTH.sub("", text)
    text = _UNICODE_SPACES.sub(" ", text)
    text = _CONTROL_CHARACTERS.sub("", text)

    text = _HORIZONTAL_RUNS.sub(" ", text)
    text = _TRAILING_SPACE_PER_LINE.sub("", text)
    text = _EXCESS_BLANK_LINES.sub("\n\n", text)

    return text.strip()


def has_meaningful_text(text: str) -> bool:
    """True when the text contains anything beyond whitespace.

    Used to tell a text-layer PDF from a scanned image. Deliberately simple:
    anything stricter would start guessing at what counts as a "real" CV, and
    guessing is how a system ends up silently discarding a valid document.
    """
    return bool(text.strip())

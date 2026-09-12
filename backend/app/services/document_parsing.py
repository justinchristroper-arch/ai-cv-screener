"""Stage 6: PDF bytes -> text, with the location information evidence needs.

Deterministic throughout. No LLM is involved in this stage and none should be:
turning a PDF into text is a mechanical operation, and making it mechanical is
what lets every later evidence span be checked against a stable substrate.

**There is no OCR.** A scanned or image-only PDF has no text layer, and this
module reports that as a failure rather than producing an empty document that
would later look like a candidate with no qualifications. Fabricating text is
the one outcome that would be worse than failing.

The split between "rejected" and "failed" is deliberate:

* **Rejected** (`PdfRejection`, no candidate row created) — structural problems
  knowable before committing to store anything: not a PDF, empty, oversized,
  unreadable as a PDF at all, or more pages than the configured limit.
* **Failed** (candidate row created, `status = FAILED` with a reason) — the file
  is a readable PDF but its *content* cannot be used: encrypted, extraction
  raised, or no text layer. These are worth showing to a recruiter, who
  uploaded the file believing it was a usable CV.

docs/architecture.md section 8 places "wrong type / oversized / too many pages"
at the API boundary and "corrupt or unparseable" at candidate level. Page count
cannot be known without opening the file, so the boundary check opens it; that
makes "opens at all" a structural question and "yields usable text" a content
question, which is the cleanest reading of both rules.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from io import BytesIO

import pypdf
from pypdf.errors import PyPdfError

from app.core.enums import CandidateFailureReason
from app.core.hashing import sha256_text
from app.core.text import NORMALIZATION_VERSION, has_meaningful_text, normalize_text

logger = logging.getLogger(__name__)

PARSER_NAME = "pypdf"
PARSER_VERSION = pypdf.__version__

#: Every PDF begins with this signature. Checked at offset 0 rather than
#: searched for: a file with leading junk before the header is not something
#: this application should be lenient about.
PDF_MAGIC = b"%PDF-"

#: Separator inserted between pages in `full_text`. Page ranges in
#: `page_offsets` cover only the page text, never the separator, so
#: `full_text[start:end]` returns exactly one page.
PAGE_SEPARATOR = "\n\n"

#: Wall-clock budget for extracting one document, checked between pages. A
#: crude control, and an honest one: it bounds a many-page document but cannot
#: interrupt a single pathological page mid-extraction. A hard limit needs
#: process isolation, which is deferred to the Phase 15 security review.
PARSE_TIME_BUDGET_SECONDS = 30.0

#: Text that reads as an instruction rather than as CV content. Flagged and
#: surfaced to the recruiter, never stripped: removing it would hide an attempt
#: and silently alter the text that evidence is later verified against
#: (docs/architecture.md section 5).
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_previous_instructions",
        re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions", re.I),
    ),
    (
        "disregard_instructions",
        re.compile(
            r"disregard\s+(?:all\s+)?(?:previous|prior|the)\s+(?:instructions|prompt)", re.I
        ),
    ),
    ("system_prompt_reference", re.compile(r"\bsystem\s+prompt\b", re.I)),
    ("role_reassignment", re.compile(r"you\s+are\s+now\s+(?:a|an|in)\b", re.I)),
    (
        "scoring_instruction",
        re.compile(r"\b(?:mark|rate|score|rank)\s+(?:this\s+)?candidate\b", re.I),
    ),
    (
        "forced_verdict",
        re.compile(r"\b(?:fully\s+qualified|perfect\s+match|hire\s+this\s+candidate)\b", re.I),
    ),
)

#: How much surrounding text to keep with a flag. Enough to see what was found,
#: short enough that the flag is not a second copy of the document.
_FLAG_EXCERPT_CHARS = 160


class PdfRejection(Exception):
    """The upload is not a processable PDF. No candidate row should be created."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PdfExtractionError(Exception):
    """The file is a readable PDF whose content could not be extracted."""

    def __init__(self, reason: CandidateFailureReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ParsedText:
    """Everything `parsed_document` needs, computed and ready to persist."""

    full_text: str
    page_offsets: list[dict]
    page_count: int
    char_count: int
    has_text_layer: bool
    text_sha256: str
    normalization_version: str
    parser_name: str
    parser_version: str
    injection_flags: list[dict] = field(default_factory=list)
    #: Pages whose text sits in two or more separated columns, so the
    #: flattened reading order may interleave unrelated sections.
    multi_column_pages: list[int] = field(default_factory=list)


def validate_upload(
    *,
    filename: str,
    data: bytes,
    max_size_bytes: int,
    max_pages: int,
) -> int:
    """Structural validation. Returns the page count, or raises `PdfRejection`.

    Order matters: the cheap checks run first so a 2 GB upload is rejected on
    size before anything tries to parse it.

    The client's `Content-Type` header is deliberately not consulted anywhere.
    It is trivially forged and says nothing about the bytes actually received;
    the magic-byte check is the authoritative one.
    """
    if not filename or not filename.strip():
        raise PdfRejection("missing_filename", "The upload has no filename.")

    if not filename.lower().endswith(".pdf"):
        raise PdfRejection(
            "unsupported_file_type",
            "Only PDF files are accepted. This file does not have a .pdf extension.",
        )

    if not data:
        raise PdfRejection("empty_file", "The uploaded file is empty.")

    if len(data) > max_size_bytes:
        limit_mb = max_size_bytes / (1024 * 1024)
        raise PdfRejection(
            "file_too_large",
            f"The file is larger than the {limit_mb:.0f} MB limit.",
        )

    if not data.startswith(PDF_MAGIC):
        # A .pdf name proves nothing. This is the check that actually decides.
        raise PdfRejection(
            "not_a_pdf",
            "The file is named .pdf but its contents are not a PDF.",
        )

    try:
        reader = pypdf.PdfReader(BytesIO(data), strict=False)
        page_count = len(reader.pages)
    except (PyPdfError, ValueError, OSError, RecursionError) as exc:
        # Deliberately not forwarding the library's message: it can quote file
        # content back to the caller.
        logger.info("Rejected an unreadable PDF: %s", type(exc).__name__)
        raise PdfRejection("malformed_pdf", "The file could not be read as a PDF.") from exc

    if page_count == 0:
        raise PdfRejection("malformed_pdf", "The PDF contains no pages.")

    if page_count > max_pages:
        raise PdfRejection(
            "too_many_pages",
            f"The PDF has {page_count} pages; the limit is {max_pages}.",
        )

    return page_count


#: How wide a horizontal gap between text columns has to be, as a share of the
#: page, before it counts as a gutter. A fifth of a page is far wider than any
#: paragraph indent.
COLUMN_GAP_SHARE = 0.2

#: How much of a page's text each side of that gutter must carry. One stray run
#: in the margin is not a column.
COLUMN_MIN_SHARE = 0.15

#: How much of the shorter side's vertical extent the two sides must share.
#: Columns run *beside* each other; a centred name block sits *above* the body
#: and shares none of its vertical range.
COLUMN_MIN_VERTICAL_OVERLAP = 0.5

#: What share of the right side's lines must sit on baselines of their own.
#: A column has its own line rhythm. A right-aligned date column has one run
#: per employer line and shares every baseline with the text beside it.
COLUMN_MIN_OWN_BASELINES = 0.25

#: How far apart two baselines can be and still be the same visual line.
BASELINE_TOLERANCE_POINTS = 2.0


def detect_columns(page: pypdf.PageObject) -> bool:
    """Whether this page's text sits in two or more separated columns.

    Extraction flattens a page into one stream of lines, and for a two-column
    CV that stream interleaves two unrelated narratives. On one real CV it put
    an education line from the left column after the experience heading from
    the right, and the screener counted a school stream as a job.

    The README listed multi-column layout as a known limitation. It is not a
    footnote: Indonesian CVs very often use a two-column template, and a wrong
    reading order produces a wrong screening result silently. This project
    already has the right pattern for input it cannot read -- a scan fails as
    `NO_TEXT_LAYER` rather than being scored as an empty CV -- so a layout that
    may have been misread is flagged and shown to the recruiter.

    Read from the text's own geometry, never from its content: the origin of
    every text run is collected, the widest gap between neighbouring x
    positions is taken as a candidate gutter, and four things have to hold at
    once for the page to be called two columns.

    Each of the last three exists because of a layout that passes the others.
    A CV with **right-aligned dates** clears the gutter test easily -- the white
    space between the bullet text and the dates is most of the page -- and a
    terse one clears the share test too, because with few bullets the dates are
    a fifth of the runs. What it cannot do is put a date on a line of its own:
    every date shares a baseline with the employer beside it. A CV with a
    **centred name block** above left-aligned body text clears the gutter and
    share tests as well, and its header lines do sit on baselines of their own
    -- but the header is stacked above the body, not beside it, so the two
    sides share none of their vertical range. Both layouts are common enough
    that flagging them would make the warning worthless, which is the real
    failure mode for a caution a recruiter is asked to act on.

    A thin sidebar -- four contact lines beside a full page of prose -- is
    missed by the share test. That is the deliberate direction to err in: a
    warning that fires on ordinary CVs gets ignored, and then it protects
    nobody.
    """
    runs: list[tuple[float, float]] = []

    def visitor(text: str, cm: list[float], tm: list[float], *_: object) -> None:
        if text.strip():
            # The run's origin in page space: the text matrix composed with the
            # graphics matrix of the text object it sits inside.
            runs.append(
                (
                    cm[0] * tm[4] + cm[2] * tm[5] + cm[4],
                    cm[1] * tm[4] + cm[3] * tm[5] + cm[5],
                )
            )

    try:
        page.extract_text(visitor_text=visitor)
        width = float(page.mediabox.width)
    except (PyPdfError, ValueError, KeyError, TypeError, RecursionError, AttributeError):
        # Position data is a bonus, never a requirement. A page whose geometry
        # cannot be read is simply not flagged.
        return False

    if len(runs) < 8 or width <= 0:
        return False

    positions = sorted({round(x) for x, _ in runs})
    if len(positions) < 2:
        return False

    gap, boundary = max(
        (positions[i + 1] - positions[i], positions[i]) for i in range(len(positions) - 1)
    )
    if gap / width < COLUMN_GAP_SHARE:
        return False

    left = [y for x, y in runs if x <= boundary]
    right = [y for x, y in runs if x > boundary]
    minimum = COLUMN_MIN_SHARE * len(runs)
    if len(left) < minimum or len(right) < minimum:
        return False

    shared = min(max(left), max(right)) - max(min(left), min(right))
    extent = min(max(left) - min(left), max(right) - min(right))
    if extent <= 0 or shared / extent < COLUMN_MIN_VERTICAL_OVERLAP:
        return False

    lines = sorted({round(y) for y in right})
    own = sum(
        1 for y in lines if not any(abs(y - other) <= BASELINE_TOLERANCE_POINTS for other in left)
    )
    return own / len(lines) >= COLUMN_MIN_OWN_BASELINES


def extract_text(data: bytes) -> ParsedText:
    """Extract text page by page, recording where each page lands.

    Raises `PdfExtractionError` when the document is readable but unusable.
    Never returns fabricated or partial-but-unmarked text.
    """
    started = time.monotonic()

    try:
        reader = pypdf.PdfReader(BytesIO(data), strict=False)
    except (PyPdfError, ValueError, OSError, RecursionError) as exc:
        raise PdfExtractionError(
            CandidateFailureReason.CORRUPT_FILE,
            "The file could not be opened as a PDF.",
        ) from exc

    if reader.is_encrypted:
        # Try the empty password: many PDFs are "encrypted" with no password
        # purely to set permissions, and those extract fine.
        try:
            opened = reader.decrypt("")
        except (PyPdfError, NotImplementedError) as exc:
            raise PdfExtractionError(
                CandidateFailureReason.CORRUPT_FILE,
                "The PDF is encrypted and could not be opened.",
            ) from exc
        if not opened:
            raise PdfExtractionError(
                CandidateFailureReason.CORRUPT_FILE,
                "The PDF is password-protected. Upload an unprotected copy.",
            )

    page_texts: list[str] = []
    multi_column: list[int] = []
    for index, page in enumerate(reader.pages):
        if time.monotonic() - started > PARSE_TIME_BUDGET_SECONDS:
            raise PdfExtractionError(
                CandidateFailureReason.PARSE_TIMEOUT,
                f"Extraction exceeded {PARSE_TIME_BUDGET_SECONDS:.0f} seconds "
                f"after {index} of {len(reader.pages)} pages.",
            )
        if detect_columns(page):
            multi_column.append(index + 1)

        try:
            raw = page.extract_text() or ""
        except (PyPdfError, ValueError, KeyError, TypeError, RecursionError) as exc:
            # One unreadable page fails the document. Silently substituting an
            # empty page would misrepresent the CV as shorter than it is, and
            # every later evidence offset would be computed against text that
            # is missing a chunk of the source.
            raise PdfExtractionError(
                CandidateFailureReason.CORRUPT_FILE,
                f"Page {index + 1} could not be read ({type(exc).__name__}).",
            ) from exc
        page_texts.append(normalize_text(raw))

    full_text, page_offsets = _assemble(page_texts)

    if not has_meaningful_text(full_text):
        # The honest outcome for a scanned CV. There is no OCR in this project,
        # so there is no text to recover, and inventing some is not an option.
        raise PdfExtractionError(
            CandidateFailureReason.NO_TEXT_LAYER,
            "The PDF contains no extractable text. It is most likely a scan or "
            "an image-only export; this system does not perform OCR.",
        )

    return ParsedText(
        full_text=full_text,
        page_offsets=page_offsets,
        page_count=len(page_texts),
        char_count=len(full_text),
        has_text_layer=True,
        text_sha256=sha256_text(full_text),
        normalization_version=NORMALIZATION_VERSION,
        parser_name=PARSER_NAME,
        parser_version=PARSER_VERSION,
        injection_flags=scan_for_injection(full_text),
        multi_column_pages=multi_column,
    )


def _assemble(page_texts: list[str]) -> tuple[str, list[dict]]:
    """Join normalized pages and record each page's character range.

    The invariant every later evidence citation depends on:
    ``full_text[entry["start"]:entry["end"]] == page_texts[entry["page"] - 1]``.

    Pages are normalized individually *before* joining. Normalizing the joined
    string instead would shift every offset computed before it.
    """
    parts: list[str] = []
    offsets: list[dict] = []
    cursor = 0

    for page_number, text in enumerate(page_texts, start=1):
        if parts:
            parts.append(PAGE_SEPARATOR)
            cursor += len(PAGE_SEPARATOR)
        start = cursor
        parts.append(text)
        cursor += len(text)
        offsets.append({"page": page_number, "start": start, "end": cursor})

    return "".join(parts), offsets


def scan_for_injection(text: str) -> list[dict]:
    """Flag text that reads as an instruction rather than as CV content.

    Flagged, never removed. Stripping would hide an attempted attack from the
    recruiter and would alter the very text that evidence spans are verified
    against, so a later verification failure would point at the wrong cause.

    This is a coarse signal for a human to look at, not a filter. It will miss
    obfuscated attempts and will occasionally flag innocent prose — a CV for a
    prompt-engineering role could legitimately mention a system prompt. The
    control that actually holds is structural: extracted CV text is confined to
    a data channel and every model claim must cite verifiable evidence.
    """
    flags: list[dict] = []
    for name, pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        start = max(0, match.start() - _FLAG_EXCERPT_CHARS // 2)
        end = min(len(text), match.end() + _FLAG_EXCERPT_CHARS // 2)
        flags.append(
            {
                "pattern": name,
                "offset": match.start(),
                "excerpt": text[start:end],
            }
        )
    return flags

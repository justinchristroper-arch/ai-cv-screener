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
    for index, page in enumerate(reader.pages):
        if time.monotonic() - started > PARSE_TIME_BUDGET_SECONDS:
            raise PdfExtractionError(
                CandidateFailureReason.PARSE_TIMEOUT,
                f"Extraction exceeded {PARSE_TIME_BUDGET_SECONDS:.0f} seconds "
                f"after {index} of {len(reader.pages)} pages.",
            )
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

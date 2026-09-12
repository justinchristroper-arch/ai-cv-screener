"""HTTP contracts for CV upload and parsing results.

Nothing here exposes a filesystem path. `candidate_document.stored_path` is an
internal detail; a client gets the original filename and a size, which is what
a recruiter actually needs to identify a file.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import CandidateFailureReason, CandidateStatus


class UploadOutcomeResponse(BaseModel):
    """What happened to one file in a batch upload.

    A batch is not all-or-nothing: each file gets its own outcome, so three bad
    files among twenty-five do not cost you the other twenty-two.
    """

    filename: str = Field(description="The sanitized original filename.")
    accepted: bool = Field(
        description="False when the file was rejected before any record was created."
    )
    candidate_id: uuid.UUID | None = Field(
        default=None, description="Present only for accepted files."
    )
    status: CandidateStatus | None = None
    failure_reason: CandidateFailureReason | None = Field(
        default=None, description="Why parsing failed, for an accepted-but-failed file."
    )
    rejection_code: str | None = Field(
        default=None,
        description=(
            "Why the file was rejected outright: unsupported_file_type, empty_file, "
            "file_too_large, not_a_pdf, malformed_pdf, too_many_pages, missing_filename."
        ),
    )
    detail: str | None = Field(default=None, description="Human-readable explanation.")


class UploadBatchResponse(BaseModel):
    """The result of one multi-file upload."""

    job_id: uuid.UUID
    uploaded: int = Field(description="Files that became candidates, parsed or failed.")
    rejected: int = Field(description="Files refused before any record was created.")
    results: list[UploadOutcomeResponse]


class DocumentSummary(BaseModel):
    """The uploaded file's metadata. Never its storage location."""

    model_config = ConfigDict(from_attributes=True)

    original_filename: str
    content_type: str
    size_bytes: int
    page_count: int | None
    #: Hash of the bytes exactly as uploaded — integrity and duplicate spotting.
    file_sha256: str
    uploaded_at: datetime


class ParsedDocumentSummary(BaseModel):
    """Facts about the extracted text, without the text itself.

    The text can be large and is personal data; it is served only from the
    dedicated text endpoint, so listing candidates never ships every CV.
    """

    model_config = ConfigDict(from_attributes=True)

    page_count: int
    char_count: int
    has_text_layer: bool
    normalization_version: str
    parser_name: str
    parser_version: str
    text_sha256: str
    language_detected: str | None = Field(
        default=None,
        description="Always null in this phase: language detection is not implemented.",
    )
    injection_flag_count: int = Field(
        default=0,
        description=(
            "Passages that read as instructions rather than CV content. Flagged for "
            "a human, never removed, and never acted on."
        ),
    )
    multi_column_pages: list[int] = Field(
        default_factory=list,
        description=(
            "Pages whose text sits in two or more separated columns. Extraction "
            "flattens a page into one stream of lines, so for such a page that "
            "stream may interleave unrelated sections and the reading order "
            "cannot be relied on. Reported rather than corrected: the recruiter "
            "is told what the reader was unsure of."
        ),
    )


class CandidateResponse(BaseModel):
    """A candidate and the state of its document."""

    id: uuid.UUID
    job_id: uuid.UUID
    #: Null until Phase 6 extracts a name from the CV. Use the filename for display.
    display_name: str | None
    status: CandidateStatus
    failure_reason: CandidateFailureReason | None
    failure_detail: str | None
    created_at: datetime
    document: DocumentSummary | None = None
    parsed: ParsedDocumentSummary | None = None


class CandidateListResponse(BaseModel):
    job_id: uuid.UUID
    candidates: list[CandidateResponse]


class PageRange(BaseModel):
    """Where one page's text sits inside `text`.

    `text[start:end]` is exactly that page. These offsets are what lets a later
    evidence quote be traced back to a page without asking a model for a
    location.
    """

    page: int
    start: int
    end: int


class CandidateTextResponse(BaseModel):
    """The extracted text, with its page map.

    Intended for development and debugging, and for the Phase 11 UI to show a
    recruiter the text a finding was drawn from.
    """

    candidate_id: uuid.UUID
    normalization_version: str
    page_count: int
    char_count: int
    text: str
    pages: list[PageRange]
    injection_flags: list[dict] = Field(
        default_factory=list,
        description="Flagged passages, verbatim. Surfaced, not removed.",
    )

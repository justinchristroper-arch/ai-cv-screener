"""Content hashing.

One helper, one definition. Every ``*_sha256`` column in the data model is
produced by this function so that a hash computed in one place matches a hash
computed in another — the cache keys and the fixture keys both depend on it.
"""

from __future__ import annotations

import hashlib


def sha256_text(text: str) -> str:
    """Lowercase hex SHA-256 of ``text``, encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Lowercase hex SHA-256 of raw bytes.

    Used for `candidate_document.file_sha256`: the hash of the uploaded file
    exactly as received, before any parsing or normalization touches it.
    """
    return hashlib.sha256(data).hexdigest()

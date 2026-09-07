"""Document storage: a client filename must never reach the filesystem.

The property these tests defend is narrow and important — the path a byte is
written to is derived from a server-generated UUID and from nothing else. A
hostile filename can be strange, long, or full of traversal sequences; none of
that may influence where the file lands or let a read escape the storage root.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.storage import DocumentStorage, StoragePathError
from app.services.candidates import sanitize_filename

# --------------------------------------------------------------------------
# Path derivation
# --------------------------------------------------------------------------


def test_the_stored_path_comes_only_from_the_document_id(storage: DocumentStorage) -> None:
    document_id = uuid.uuid4()

    relative = storage.build_relative_path(document_id)

    assert document_id.hex in relative
    assert relative.endswith(".pdf")
    assert relative.startswith(document_id.hex[:2] + "/")


def test_the_stored_path_is_relative(storage: DocumentStorage) -> None:
    """The database must not record the server's filesystem layout."""
    relative = storage.save(uuid.uuid4(), b"%PDF-1.7 content")

    assert not relative.startswith("/")
    assert ":" not in relative, "must not be an absolute Windows path"
    assert str(storage.root) not in relative


def test_saved_bytes_round_trip(storage: DocumentStorage) -> None:
    data = b"%PDF-1.7 some bytes"
    relative = storage.save(uuid.uuid4(), data)

    assert storage.read(relative) == data
    assert storage.exists(relative)


def test_files_land_inside_the_storage_root(storage: DocumentStorage) -> None:
    relative = storage.save(uuid.uuid4(), b"%PDF-1.7")

    resolved = storage.resolve(relative)

    assert storage.root in resolved.parents


def test_delete_removes_the_file_and_tolerates_a_missing_one(
    storage: DocumentStorage,
) -> None:
    relative = storage.save(uuid.uuid4(), b"%PDF-1.7")

    storage.delete(relative)
    storage.delete(relative)  # deleting again is not an error

    assert not storage.exists(relative)


# --------------------------------------------------------------------------
# G. Traversal
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "../escaped.pdf",
        "../../etc/passwd",
        "..\\..\\windows\\system32\\config\\sam",
        "a/../../../outside.pdf",
    ],
)
def test_a_traversal_path_cannot_escape_the_storage_root(
    storage: DocumentStorage, hostile: str
) -> None:
    """Paths are generated internally, so this defends against a tampered row."""
    with pytest.raises(StoragePathError):
        storage.resolve(hostile)


def test_a_traversal_filename_cannot_reach_the_filesystem(
    storage: DocumentStorage, tmp_path
) -> None:
    """The end-to-end property: a hostile name changes nothing about the path."""
    hostile_name = "../../../../../../tmp/pwned.pdf"
    document_id = uuid.uuid4()

    # The name is not even an input to `save`; only the id is.
    relative = storage.save(document_id, b"%PDF-1.7 payload")

    assert "pwned" not in relative
    assert ".." not in relative
    assert not (tmp_path / "pwned.pdf").exists()
    assert storage.resolve(relative).read_bytes() == b"%PDF-1.7 payload"
    # And the hostile string, if it ever reached storage, would be refused.
    with pytest.raises(StoragePathError):
        storage.resolve(hostile_name)


def test_two_documents_never_collide(storage: DocumentStorage) -> None:
    first = storage.save(uuid.uuid4(), b"%PDF-1.7 one")
    second = storage.save(uuid.uuid4(), b"%PDF-1.7 two")

    assert first != second
    assert storage.read(first) != storage.read(second)


# --------------------------------------------------------------------------
# Filename sanitization (display metadata only)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\cmd.exe", "cmd.exe"),
        ("/absolute/path/cv.pdf", "cv.pdf"),
        ("cv.pdf", "cv.pdf"),
        ("  spaced.pdf  ", "spaced.pdf"),
    ],
)
def test_sanitize_filename_keeps_only_the_final_component(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_strips_control_characters() -> None:
    assert sanitize_filename("cv\x00\x1b.pdf") == "cv.pdf"


def test_sanitize_filename_preserves_a_legitimate_international_name() -> None:
    """A CV called `Résumé (final).pdf` should keep its name."""
    assert sanitize_filename("Résumé (final).pdf") == "Résumé (final).pdf"


def test_sanitize_filename_bounds_length() -> None:
    assert len(sanitize_filename("x" * 5000 + ".pdf")) <= 255


def test_sanitize_filename_never_returns_empty() -> None:
    for degenerate in ("", "   ", "...", "/", "\\"):
        assert sanitize_filename(degenerate) == "unnamed.pdf"

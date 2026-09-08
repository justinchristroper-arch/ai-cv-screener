"""Detect screening criteria that ask about a protected personal characteristic.

## Why this exists at all

Until requirements could only come from a formal job description, this was a
theoretical problem. Now a recruiter types their criteria in free text, in their
own words, and free text can say "wanita, maksimal 25 tahun". Nothing else in
the system would stop that: extraction would produce a requirement, confirmation
would freeze it, and matching would look for evidence of it in a CV — which some
CVs print, because "Gender: Female" is a normal line in an Indonesian CV.

ADR-0003 keeps sensitive attributes out of the candidate *profile*. That control
protects the profile, not the requirement set, and the requirement set is the
other half of the same question.

## What this module does, and what it deliberately does not

It reports which of a set of criteria name a protected characteristic. It never
edits anyone's text, never deletes a requirement, and never decides anything
about a candidate. The one enforcement built on it is in
`requirements.confirm_requirements`: a requirement set containing a flagged
requirement cannot be confirmed, and since every screening stage goes through the
confirmation gate (ADR-0004), such a requirement can never reach a candidate.

Refusing at the gate rather than quietly ignoring the requirement later is the
honest option. A recruiter who is told "this cannot be a screening criterion,
here is which line and why" can rewrite it in ten seconds. A recruiter whose
criterion is silently scored as "no evidence" for everybody learns nothing and
believes their filter is working.

## The patterns are deliberately narrow

A false positive costs a recruiter one edit; it does not lose their work, and the
message names the exact requirement. Even so, every pattern here is anchored to a
word that only appears when the characteristic itself is the subject:

* `usia`/`umur`/`age`, never a bare "tahun" — "minimal 2 tahun pengalaman" is a
  duration requirement and must pass untouched.
* `jenis kelamin`, `pria`, `wanita`, `male`, `female` — the words themselves,
  not any sentence that mentions people.
* A **language** is never a protected characteristic here. "Bisa bahasa
  Inggris" and "fluent in English" are job-relevant skills, and the nationality
  pattern is written so it cannot swallow them.

This is a coarse signal for a human, not a compliance control, and it is not a
substitute for one. It reads Indonesian and English, which is what this build
was tested against; a criterion written another way will not be caught. It is
also not legal advice — the underlying rule is a product decision recorded in
docs/product-spec.md section 13.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: How much surrounding text a flag carries, so a recruiter can see the phrase
#: in context without the flag quoting a whole paragraph back at them.
_EXCERPT_CHARS = 80


@dataclass(frozen=True)
class ProtectedAttributeFlag:
    """One passage that names a protected characteristic."""

    #: Which characteristic, as a stable machine-readable name.
    attribute: str
    #: A short, human-readable name for the same thing, for the UI.
    label: str
    offset: int
    excerpt: str

    def as_dict(self) -> dict:
        return {
            "attribute": self.attribute,
            "label": self.label,
            "offset": self.offset,
            "excerpt": self.excerpt,
        }


#: `(attribute, label, pattern)`. Indonesian and English, in one table rather
#: than two, because a single criterion routinely mixes both.
_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "gender",
        "gender",
        re.compile(
            r"\b(?:jenis\s+kelamin|gender|pria|wanita|laki-?laki|perempuan|cowok|cewek"
            r"|males?|females?|\bmen\b|\bwomen\b)\b",
            re.I,
        ),
    ),
    (
        "age",
        "age or date of birth",
        re.compile(
            r"\b(?:usia|umur|berusia|tanggal\s+lahir|tgl\s+lahir|date\s+of\s+birth|dob"
            r"|age(?:d)?\s*(?:limit|range|max|min|under|over|below|above|:|\d)"
            r"|maks(?:imal)?\s+\d{2}\s*(?:th|thn|tahun)|under\s+\d{2}\s*years?\s*old"
            r"|\d{2}\s*years?\s*old|fresh\s+graduate\s+maks)\b",
            re.I,
        ),
    ),
    (
        "marital_status",
        "marital or family status",
        re.compile(
            r"\b(?:status\s+(?:pernikahan|perkawinan)|belum\s+menikah|sudah\s+menikah"
            r"|marital\s+status|single\s+only|unmarried|tidak\s+sedang\s+hamil|hamil"
            r"|pregnan(?:t|cy)|punya\s+anak|no\s+children)\b",
            re.I,
        ),
    ),
    (
        "religion",
        "religion",
        re.compile(
            r"\b(?:agama|beragama|muslim|muslimah|non[- ]?muslim|kristen|katolik|hindu"
            r"|buddha|religion|religious\s+affiliation)\b",
            re.I,
        ),
    ),
    (
        "ethnicity",
        "ethnicity or race",
        re.compile(
            # "race" excludes "race condition": a concurrency bug is a
            # legitimate thing to ask a backend engineer about, and this pattern
            # flagged it before the exclusion was added.
            r"\b(?:suku|etnis|ras\b|pribumi|keturunan\s+(?:tionghoa|arab|india)"
            r"|ethnicity|race(?!\s+conditions?\b)|racial)\b",
            re.I,
        ),
    ),
    (
        "nationality",
        "nationality or citizenship",
        re.compile(
            r"\b(?:kewarganegaraan|warga\s+negara|wni\b|wna\b|nationality|citizenship"
            r"|passport\s+holder|must\s+be\s+(?:a\s+)?citizen)\b",
            re.I,
        ),
    ),
    (
        "appearance",
        "physical appearance or photograph",
        re.compile(
            r"\b(?:berpenampilan\s+menarik|penampilan\s+menarik|good\s+looking"
            r"|attractive\s+appearance|tinggi\s+badan|berat\s+badan"
            r"|lampirkan\s+foto|sertakan\s+foto|attach\s+(?:a\s+)?photo"
            r"|photograph\s+required|with\s+photo)\b",
            re.I,
        ),
    ),
    (
        "health",
        "health or disability status",
        re.compile(
            r"\b(?:tidak\s+(?:buta\s+warna|cacat)|bebas\s+narkoba|sehat\s+jasmani"
            r"|disabilit(?:y|ies)|no\s+medical\s+conditions|medically\s+fit)\b",
            re.I,
        ),
    ),
)


def scan(text: str) -> list[ProtectedAttributeFlag]:
    """Every protected characteristic named in `text`, at most one flag each.

    One flag per characteristic, not per occurrence: a recruiter needs to know
    *that* their criteria ask about age, not every place the word appears.
    """
    flags: list[ProtectedAttributeFlag] = []
    for attribute, label, pattern in _PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        start = max(0, match.start() - _EXCERPT_CHARS // 2)
        end = min(len(text), match.end() + _EXCERPT_CHARS // 2)
        flags.append(
            ProtectedAttributeFlag(
                attribute=attribute,
                label=label,
                offset=match.start(),
                excerpt=text[start:end],
            )
        )
    return flags


def names_a_protected_attribute(text: str) -> bool:
    """Whether `text` names any protected characteristic at all."""
    return any(pattern.search(text) for _, _, pattern in _PATTERNS)


__all__ = [
    "ProtectedAttributeFlag",
    "names_a_protected_attribute",
    "scan",
]

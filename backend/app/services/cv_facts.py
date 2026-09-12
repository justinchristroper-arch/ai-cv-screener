"""Deterministic facts about one CV, each carrying the text it came from.

No model, no network, no database, no clock. Given the same text this module
returns the same facts, byte for byte, which is what lets a score computed from
them be reproduced later (ADR-0008) and what makes the whole path portable to a
browser without a download (ADR-0012).

## Why this is not a keyword search

Finding "Python" in a document proves almost nothing. The same five characters
appear in a denial, in a pasted job advert, in an aspiration, and in a genuine
claim, and only the last one is evidence:

    "I have no Python experience."          -> denied
    "Python is required for this role."     -> the advert, not the person
    "Looking for Python developers."        -> the advert, not the person
    "Familiar with Python"                  -> exposure, not depth
    "Developed services in Python"          -> evidence

So every candidate mention is read together with the line it sits on and the
section it sits under, and is discarded unless the surrounding text supports the
reading that *this person did this thing*. The rules are deliberately blunt and
few: each one is a pattern a recruiter would recognise, not a special case for a
particular document.

## What comes out

`CvFacts` -- skills, education, dated roles, GPA, internships, languages -- with
every item carrying the exact line it was read from. Nothing is inferred from a
name, a photo, an address or an employer's identity (ADR-0003).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.core.protected_attributes import names_a_protected_attribute
from app.core.text import find_token, normalize_text
from app.services.skill_taxonomy import (  # noqa: F401 - COMMON_WORD_TERMS used below
    COMMON_WORD_TERMS,
    LANGUAGES,
    SHORT_AMBIGUOUS_TERMS,
    alias_index,
    family_of,
)

# --------------------------------------------------------------------------
# Context vocabularies. These are the whole difference between an extractor and
# a `str.find`, so each one says what it is for.
# --------------------------------------------------------------------------

#: The mention is denied. Matched against the text *before* the mention, so
#: "no Python" is caught and "Python, no problem" is not.
NEGATION_CUES = (
    "no ",
    "not ",
    "never ",
    "without ",
    "lack of ",
    "lacking ",
    "none ",
    "tidak ",
    "belum ",
    "tanpa ",
    "kurang ",
)

#: The sentence says what the ROLE needs. A CV that quotes the advert, or a
#: cover letter pasted above it, contains these; crediting them would let a
#: candidate match every requirement by echoing it back.
DEMAND_CUES = (
    "is required",
    "are required",
    "required for",
    "requirement",
    "we need",
    "we are looking for",
    "looking for",
    "seeking",
    "must have",
    "should have",
    "candidate should",
    "applicants",
    "wajib",
    "harus memiliki",
    "dibutuhkan",
    "dicari",
    "kualifikasi",
)

#: Verbs of building and running things. Strong evidence the subject is the
#: candidate rather than the employer's wish list.
DOING_CUES = (
    "developed",
    "built",
    "designed",
    "implemented",
    "maintained",
    "operated",
    "created",
    "delivered",
    "shipped",
    "owned",
    "led",
    "managed",
    "migrated",
    "deployed",
    "automated",
    "optimised",
    "optimized",
    "wrote",
    "engineered",
    "used",
    "using",
    "membangun",
    "mengembangkan",
    "membuat",
    "merancang",
    "mengelola",
    "memakai",
    "menggunakan",
)

#: Exposure without depth. Caps a skill at PARTIAL rather than discarding it:
#: "familiar with Python" is a real thing the document says, just not the same
#: thing as having built with it.
SHALLOW_CUES = (
    "familiar with",
    "familiarity",
    "exposure to",
    "basic knowledge",
    "course",
    "coursework",
    "bootcamp",
    "certificate",
    "certification",
    "self-taught",
    "beginner",
    "kursus",
    "pelatihan",
    "sertifikat",
    "sedang belajar",
    # "learning" and "training" were single words here until the structured
    # evaluation caught what that cost. Both are ordinary vocabulary in this
    # industry: "MSc Machine Learning" is a degree title and "built training
    # pipelines" is a job, and either within fifty characters of a skill
    # downgraded it to exposure. On one CV that marked Python, Docker and SQL
    # as things the candidate had merely been taught.
    #
    # The phrases below mean what the cue was reaching for -- not yet
    # proficient -- and cannot be satisfied by naming a field of study.
    "currently learning",
    "still learning",
    "learning about",
    "self-learning",
    "training course",
    "attended training",
    "completed training",
    "in training",
)

#: Section headers, in both languages. A bare token under SKILLS is a claim;
#: the same token in a paragraph is not.
SECTION_HEADERS: dict[str, str] = {
    "skills": "skills",
    "technical skills": "skills",
    "keahlian": "skills",
    "experience": "experience",
    "work experience": "experience",
    "professional experience": "experience",
    "employment": "experience",
    "pengalaman": "experience",
    "pengalaman kerja": "experience",
    "education": "education",
    "pendidikan": "education",
    "projects": "projects",
    "proyek": "projects",
    "certifications": "certifications",
    "sertifikasi": "certifications",
    "sertifikat": "certifications",
    "languages": "languages",
    "bahasa": "languages",
    # Sections that must CLOSE the experience section rather than be absorbed
    # into it. Without these, a dated line under "PRESTASI" or a stray
    # education line at the foot of a two-column CV falls inside `experience`
    # and is counted as employment -- which is exactly how one real CV came
    # back with 58 months of "professional experience" out of ~14.
    #
    # "pengalaman organisasi" has to appear before the bare "pengalaman" key
    # would ever be consulted; the lookup is on the whole stripped line, so the
    # two cannot collide.
    "achievements": "achievements",
    "prestasi": "achievements",
    "penghargaan": "achievements",
    "awards": "achievements",
    "organisasi": "organisations",
    "pengalaman organisasi": "organisations",
    "organizational experience": "organisations",
    "kegiatan": "organisations",
    "activities": "organisations",
    "kontak": "contact",
    "contact": "contact",
}

#: Titles that make a role an internship. Token-matched, so "internal tooling"
#: does not become an internship -- a false positive that would otherwise credit
#: a great many backend CVs with an internship they never did.
INTERNSHIP_TITLE_TERMS = (
    "intern",
    "interns",
    "internship",
    "trainee",
    "apprentice",
    "apprenticeship",
    "magang",
    "pemagangan",
)

#: A dated line that says this plainly is **not employment**, however it is
#: filed. Indonesian CVs routinely list school-society and volunteer activity
#: under the same "PENGALAMAN" heading as paid work, and label it themselves --
#: "Memiliki pengalaman berorganisasi yakni sebagai anggota aktif OSIS".
#: Counting those as professional experience inflated one real CV from about 14
#: months to 58, which is the difference between a candidate who has worked and
#: a candidate who has not.
#:
#: A denial list rather than a required employment cue, deliberately: an
#: ordinary entry is "Northwind Analytics - Backend Engineer, 2022-2026", which
#: contains no verb at all, so demanding "worked as" would discard the majority
#: of real roles to catch a minority of fake ones.
#:
#: Each term is token-matched and chosen to be specific. "sekolah minggu" and
#: "jurusan" are here; bare "sekolah" and "school" are not, because a school is
#: also an employer and "guru di Sekolah Cikal, 2020-2023" is a real job.
NON_EMPLOYMENT_CUES = (
    # organisational and voluntary
    "berorganisasi",
    "keorganisasian",
    "organisasi",
    "osis",
    "panitia",
    "kepanitiaan",
    "anggota aktif",
    "himpunan",
    "ekstrakurikuler",
    "ekskul",
    "karang taruna",
    "sekolah minggu",
    "volunteer",
    "volunteering",
    "relawan",
    "sukarelawan",
    "kepanduan",
    "pramuka",
    # education, which belongs to `extract_education` and not here
    "jurusan",
    "fakultas",
    "semester",
    "sma",
    "smp",
    "smk",
    "mahasiswa",
    "siswa",
    "kelas",
)


def _is_employment(line: str) -> bool:
    """Whether a dated line reads as a job rather than as something else.

    Also the reason a religious affiliation stopped reaching the evidence
    record. "asisten panitia sekolah minggu di wihara bodhi (2018-2019)" was
    being stored as a role, so the quotation displayed beside an experience
    verdict named someone's place of worship (ADR-0003). The fix is not to
    detect religions in role lines -- an employer genuinely may be a temple or
    a church, and citing a real employer is correct -- it is that Sunday-school
    volunteering is not employment, so it never becomes a role at all.
    """
    lowered = line.lower()
    return not any(find_token(cue, lowered) != -1 for cue in NON_EMPLOYMENT_CUES)


# --------------------------------------------------------------------------
# Fact types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    """A line of the CV, and where it starts. The unit of evidence."""

    text: str
    start: int
    end: int


@dataclass(frozen=True)
class SkillFact:
    name: str
    family: str
    span: Span
    section: str
    #: False when the only support is exposure ("familiar with", "course").
    substantive: bool


@dataclass(frozen=True)
class RoleFact:
    span: Span
    start_month: int  # months since year 0, for interval arithmetic
    end_month: int
    is_internship: bool
    #: Where this entry's text ends: the start of the next dated role, or the
    #: end of its section. A CV states the field it worked in underneath the
    #: title far more often than inside it -- "Staff Finance" is rarer than a
    #: bullet list mentioning rekonsiliasi and faktur pajak -- so a criterion
    #: about experience *in a field* has to read the whole entry, not the one
    #: line carrying the dates. Defaults to the line's own end, so a role built
    #: without a block behaves as it always did.
    block_end: int = 0

    def __post_init__(self) -> None:
        if self.block_end < self.span.end:
            object.__setattr__(self, "block_end", self.span.end)

    def evidences(self, skills: list[SkillFact], name: str) -> bool:
        """Whether a skill of this name was claimed inside this entry.

        Delegates to the skills the extractor already accepted, rather than
        searching the block for the word: that way this inherits every gate
        `extract_skills` applies -- the denial, the pasted advert, the
        requirement-shaped sentence -- instead of quietly reintroducing the
        keyword matching those gates exist to prevent.
        """
        return any(
            item.name == name and self.span.start <= item.span.start < self.block_end
            for item in skills
        )


@dataclass(frozen=True)
class EducationFact:
    level: int
    span: Span


@dataclass(frozen=True)
class GpaFact:
    value: Decimal
    #: None when the CV states a grade but not the scale it is out of. The
    #: engine refuses to assume 4.0: a 3.2 is strong out of 4 and weak out of 5.
    scale: Decimal | None
    span: Span


@dataclass(frozen=True)
class LanguageFact:
    name: str
    span: Span


@dataclass
class CvFacts:
    skills: list[SkillFact] = field(default_factory=list)
    roles: list[RoleFact] = field(default_factory=list)
    education: list[EducationFact] = field(default_factory=list)
    gpa: list[GpaFact] = field(default_factory=list)
    languages: list[LanguageFact] = field(default_factory=list)

    @property
    def highest_education(self) -> EducationFact | None:
        return max(self.education, key=lambda e: e.level, default=None)


# --------------------------------------------------------------------------
# Text geography
# --------------------------------------------------------------------------


def _sections(text: str) -> list[tuple[int, int, str]]:
    ranges: list[tuple[int, int, str]] = []
    pos = 0
    current = "header"
    start = 0
    for line in text.split("\n"):
        key = line.strip().lower().rstrip(":")
        if key in SECTION_HEADERS:
            if pos > start:
                ranges.append((start, pos, current))
            current = SECTION_HEADERS[key]
            start = pos
        pos += len(line) + 1
    ranges.append((start, max(pos, start), current))
    return ranges


def _section_at(offset: int, ranges: list[tuple[int, int, str]]) -> str:
    for begin, end, name in ranges:
        if begin <= offset < end:
            return name
    return "header"


def line_at(text: str, offset: int) -> Span:
    """The whole line containing `offset`. Evidence is always a whole line."""
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    if end == -1:
        end = len(text)
    return Span(text[start:end], start, end)


#: A line carrying nothing but a date, once punctuation is stripped. A wrapped
#: CV entry produces these: the title and employer land on one line and the
#: dates on the next.
_DATE_ONLY = re.compile(
    r"^[\s(\[\-–—,.:]*[A-Za-z]{0,9}\.?\s*[-–—]?\s*[A-Za-z]{0,9}\.?[\s\d(),.\[\]/–—-]*$"
)


def entry_at(text: str, offset: int) -> Span:
    """The line containing `offset`, joined with the one above when it is bare.

    `line_at` is the right unit for a skill or a grade, which are stated inline.
    A role is different: a two-column CV flattens "sales di toko League Pekan
    Raya Jakarta" and "(Juni-July 2024)" onto separate lines, and citing only
    the second gives a recruiter the quotation "(Juni-July 2024)" beside the
    verdict -- true, verifiable, and useless. Joining the line above restores
    the entry the dates belong to.

    Only ever joins upward, and only when the dated line has no words of its
    own, so an ordinary one-line entry is returned unchanged.
    """
    span = line_at(text, offset)
    if not _DATE_ONLY.match(span.text):
        return span
    above_end = span.start - 1
    if above_end <= 0:
        return span
    above = line_at(text, text.rfind("\n", 0, above_end) + 1)
    if not above.text.strip() or _DATE_ONLY.match(above.text):
        return span
    return Span(f"{above.text.strip()} {span.text.strip()}", above.start, span.end)


def _before(text: str, offset: int, width: int = 60) -> str:
    return text[max(0, offset - width) : offset].lower()


def _around(text: str, offset: int, length: int = 0) -> str:
    return text[max(0, offset - 70) : offset + length + 50].lower()


def _mentions(cues: tuple[str, ...], blob: str) -> bool:
    return any(cue in blob for cue in cues)


# --------------------------------------------------------------------------
# Skills
# --------------------------------------------------------------------------


def _skill_is_claimed(text: str, offset: int, alias: str, section: str) -> bool:
    """Whether this mention reads as the candidate's own, not the advert's."""
    before = _before(text, offset)
    around = _around(text, offset, len(alias))

    if _mentions(NEGATION_CUES, before):
        return False

    # A bare list under SKILLS is never an advert, so demand cues are only
    # consulted outside it.
    if section != "skills" and _mentions(DEMAND_CUES, around):
        return False

    # Outside the sections where a bare mention is itself a claim, require a
    # verb of doing. A technology named in a summary paragraph, with no verb
    # attaching it to the person, is not evidence they used it.
    if section in ("header", "education", "certifications", "languages"):
        return _mentions(DOING_CUES, around)

    # Names that are also ordinary words -- "excel", "go", "spring", "react" --
    # need the same treatment inside prose, because the word appears there for
    # its ordinary meaning far more often than as a product. "Asked to excel
    # under pressure" is not a spreadsheet; "Built dashboards using Excel" is.
    # A list-like section is exempt: a delimited list is never a sentence.
    if alias in COMMON_WORD_TERMS and section not in ("skills", "certifications"):
        return _mentions(DOING_CUES, around)

    return True


def extract_skills(text: str, ranges: list[tuple[int, int, str]]) -> list[SkillFact]:
    facts: list[SkillFact] = []
    lower = text.lower()
    seen: set[tuple[str, int]] = set()

    for alias, canonical in alias_index():
        cursor = 0
        while True:
            found = find_token(alias, lower[cursor:])
            if found == -1:
                break
            offset = cursor + found
            cursor = offset + 1

            section = _section_at(offset, ranges)

            # Two- and one-letter names ("Go", "R", "C") occur constantly inside
            # ordinary prose. Only a list-like section makes a bare one a claim.
            if alias in SHORT_AMBIGUOUS_TERMS and section not in ("skills", "certifications"):
                continue

            if not _skill_is_claimed(text, offset, alias, section):
                continue

            span = line_at(text, offset)
            key = (canonical, span.start)
            if key in seen:
                continue
            seen.add(key)

            substantive = not _mentions(SHALLOW_CUES, _around(text, offset, len(alias)))
            facts.append(SkillFact(canonical, family_of(canonical), span, section, substantive))
    return facts


# --------------------------------------------------------------------------
# Education
# --------------------------------------------------------------------------

EDUCATION_LEVELS: dict[str, int] = {
    "diploma": 2,
    "bachelor": 3,
    "master": 4,
    "doctorate": 5,
}

#: Ordered so the most specific alternation is tried first. Indonesian S1/S2/S3
#: are first-class: the recruiter and the candidate both write them.
EDUCATION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bs3\b|\bdoctorate\b|\bph\.?\s?d\b|\bdoktor\b", "doctorate"),
    (
        r"\bs2\b|\bmaster'?s?\b|\bm\.?sc\b|\bm\.?eng\b|\bm\.?kom\b|\bmagister\b|\bpostgraduate\b",
        "master",
    ),
    (
        r"\bs1\b|\bbachelor'?s?\b|\bb\.?sc\b|\bb\.?eng\b|\bs\.?kom\b|\bsarjana\b|\bundergraduate\b",
        "bachelor",
    ),
    (r"\bd3\b|\bdiploma\b", "diploma"),
)

#: A degree being read towards is not a degree held. Kept separate from the
#: negation list because the sentence is not a denial -- it is a different fact.
IN_PROGRESS_CUES = (
    "currently pursuing",
    "pursuing",
    "in progress",
    "expected",
    "candidate for",
    "studying",
    "enrolled",
    "sedang menempuh",
    "sedang kuliah",
)


def extract_education(text: str, ranges: list[tuple[int, int, str]]) -> list[EducationFact]:
    facts: list[EducationFact] = []
    for pattern, level_name in EDUCATION_PATTERNS:
        for match in re.finditer(pattern, text, re.I):
            around = _around(text, match.start(), len(match.group(0)))
            if _mentions(NEGATION_CUES, _before(text, match.start())):
                continue
            if _mentions(DEMAND_CUES, around):
                continue
            if _mentions(IN_PROGRESS_CUES, around):
                continue
            facts.append(EducationFact(EDUCATION_LEVELS[level_name], line_at(text, match.start())))
    return facts


# --------------------------------------------------------------------------
# GPA
# --------------------------------------------------------------------------

#: A grade is only read where the document says it is one. Bare decimals are
#: never harvested: "Python 3.12", "version 3.5" and "rating 3.5" would all
#: otherwise become grade point averages.
_GPA = re.compile(
    r"\b(?:gpa|ipk|grade\s+point\s+average|cumulative\s+gpa)\b\s*[:=]?\s*"
    r"(?P<value>[0-5](?:[.,]\d{1,2})?)"
    r"(?:\s*/\s*(?P<scale>[45](?:[.,]0{1,2})?))?",
    re.I,
)


def _decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return None


def extract_gpa(text: str) -> list[GpaFact]:
    facts: list[GpaFact] = []
    for match in _GPA.finditer(text):
        if _mentions(DEMAND_CUES, _around(text, match.start(), len(match.group(0)))):
            continue
        value = _decimal(match.group("value"))
        if value is None:
            continue
        raw_scale = match.group("scale")
        scale = _decimal(raw_scale) if raw_scale else None
        facts.append(GpaFact(value, scale, line_at(text, match.start())))
    return facts


# --------------------------------------------------------------------------
# Roles, durations and internships
# --------------------------------------------------------------------------

_MONTH_NAMES = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "januari": 1,
    "februari": 2,
    "maret": 3,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "agustus": 8,
    "oktober": 10,
    "desember": 12,
}
_MONTH_NAMES.update({name[:3]: number for name, number in list(_MONTH_NAMES.items())})

_OPEN_ENDED = ("present", "current", "now", "sekarang", "ongoing", "kini")

_RANGE = re.compile(
    r"(?P<m1>[A-Za-z]{3,9})?\.?\s*(?P<y1>(?:19|20)\d{2})\s*"
    r"(?:-|–|—|to|until|s/d|sampai)\s*"
    r"(?P<m2>[A-Za-z]{3,9})?\.?\s*(?P<y2>(?:19|20)\d{2}|" + "|".join(_OPEN_ENDED) + r")",
    re.I,
)

#: Two months sharing one year: "Juni-July 2024", "Jan - Mar 2023". This is how
#: a short engagement is normally written, and `_RANGE` cannot see it -- it
#: needs a year immediately after the first month. A two-month sales job went
#: undetected on a real CV for exactly this reason, while three non-jobs on the
#: same CV were counted.
#:
#: Both month names are checked against `_MONTH_NAMES` before the match is
#: believed, so "Pekan-Raya 2024" is not a date.
_RANGE_WITHIN_YEAR = re.compile(
    r"(?P<m1>[A-Za-z]{3,9})\.?\s*(?:-|–|—|to|until|s/d|sampai)\s*"
    r"(?P<m2>[A-Za-z]{3,9})\.?\s*(?P<y>(?:19|20)\d{2})",
    re.I,
)


def _is_month(name: str) -> bool:
    key = name.strip().lower()
    return key in _MONTH_NAMES or key[:3] in _MONTH_NAMES


def _month_index(name: str | None, default: int) -> int:
    if not name:
        return default
    key = name.strip().lower()
    return _MONTH_NAMES.get(key) or _MONTH_NAMES.get(key[:3]) or default


def extract_roles(
    text: str, ranges: list[tuple[int, int, str]], *, as_of_year: int, as_of_month: int
) -> list[RoleFact]:
    """Dated ranges inside the experience section.

    `as_of` is passed in rather than read from a clock: a score that changes
    because a day passed would not be reproducible (ADR-0008).
    """
    facts: list[RoleFact] = []
    seen: set[int] = set()
    # Nobody has worked a month that has not happened. A CV written "2023 -
    # 2026" states a year range whose end is December, and counting to December
    # credited months still in the future -- over-crediting, in the direction
    # that costs most. The stated end is kept when it is in the past; otherwise
    # it is read as "until now", which is what such an entry means.
    today = as_of_year * 12 + (as_of_month - 1)

    def accept(offset: int, begin: int, finish: int) -> None:
        finish = min(finish, today)
        if finish < begin:
            return
        span = entry_at(text, offset)
        # One role per line. Two date-shaped strings on one line describe one
        # engagement, not two, and counting both would inflate the total.
        if span.start in seen:
            return
        if not _is_employment(span.text):
            return
        seen.add(span.start)
        lowered = span.text.lower()
        internship = any(find_token(term, lowered) != -1 for term in INTERNSHIP_TITLE_TERMS)
        facts.append(RoleFact(span, begin, finish, internship))

    for match in _RANGE.finditer(text):
        if _section_at(match.start(), ranges) not in ("experience", "projects"):
            continue
        start_year = int(match.group("y1"))
        raw_end = match.group("y2").lower()
        open_ended = raw_end in _OPEN_ENDED
        end_year = as_of_year if open_ended else int(raw_end)
        start_month = _month_index(match.group("m1"), 1)
        end_month = as_of_month if open_ended else _month_index(match.group("m2"), 12)
        accept(
            match.start(),
            start_year * 12 + (start_month - 1),
            end_year * 12 + (end_month - 1),
        )

    for match in _RANGE_WITHIN_YEAR.finditer(text):
        if _section_at(match.start(), ranges) not in ("experience", "projects"):
            continue
        if not (_is_month(match.group("m1")) and _is_month(match.group("m2"))):
            continue
        year = int(match.group("y"))
        begin_month = _month_index(match.group("m1"), 1)
        end_month = _month_index(match.group("m2"), 12)
        accept(
            match.start(),
            year * 12 + (begin_month - 1),
            year * 12 + (end_month - 1),
        )

    facts.sort(key=lambda role: role.span.start)

    # Each entry owns the text from its own line up to the next dated role, or
    # to the end of the section it sits in. That block is what a field-scoped
    # duration criterion reads, because a CV names the field it worked in
    # underneath the title much more often than inside it.
    blocked: list[RoleFact] = []
    for index, role in enumerate(facts):
        if index + 1 < len(facts):
            end = facts[index + 1].span.start
        else:
            end = next(
                (stop for begin, stop, _ in ranges if begin <= role.span.start < stop),
                len(text),
            )
        blocked.append(
            RoleFact(role.span, role.start_month, role.end_month, role.is_internship, end)
        )
    return blocked


def total_months(roles: list[RoleFact]) -> int:
    """Union of the dated intervals, in months, counting both endpoints.

    Summing each role separately double-counts a promotion or a concurrent
    contract and can turn two overlapping years into four. The union is what a
    person means by "how long have they been working".

    **Inclusive of both endpoints.** This counted the gap between the endpoints
    until an audit found what that cost: a CV reading "Jan 2023 - Dec 2023"
    came back as 11 months, a full year "2021-2022" as 23, and a job lasting a
    single month as **zero** -- so a criterion of "at least 1 month" reported
    "no evidence of 1 months of experience" about a candidate whose CV plainly
    stated a month of work. A claim about the document that the document
    contradicts is the one thing this project must not produce.

    Somebody who worked January through December worked twelve months, not
    eleven, and that is the reading a recruiter and a candidate both expect.
    """
    if not roles:
        return 0
    intervals = sorted((role.start_month, role.end_month) for role in roles)
    merged: list[list[int]] = [list(intervals[0])]
    for begin, finish in intervals[1:]:
        if begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], finish)
        else:
            merged.append([begin, finish])
    return sum(finish - begin + 1 for begin, finish in merged)


# --------------------------------------------------------------------------
# Languages
# --------------------------------------------------------------------------


def extract_languages(text: str, ranges: list[tuple[int, int, str]]) -> list[LanguageFact]:
    """Named languages. Presence only -- never a proficiency level.

    A level is not deterministically readable: most CVs do not state one, and
    inferring it from the language the CV happens to be written in, or from how
    well it reads, is guessing about a person (ADR-0012).
    """
    facts: list[LanguageFact] = []
    lower = text.lower()
    seen: set[tuple[str, int]] = set()
    for canonical, aliases in LANGUAGES.items():
        for alias in aliases:
            cursor = 0
            while True:
                found = find_token(alias, lower[cursor:])
                if found == -1:
                    break
                offset = cursor + found
                cursor = offset + 1
                around = _around(text, offset, len(alias))
                if _mentions(NEGATION_CUES, _before(text, offset)):
                    continue
                if _mentions(DEMAND_CUES, around):
                    continue
                span = line_at(text, offset)
                # A language name and a nationality are frequently the same
                # word, and CVs print "Nationality: Indonesian, Religion: Islam"
                # as one comma-separated line -- which the list heuristic below
                # would otherwise read as a language list. Accepting it would
                # make the evidence span for a language criterion a quotation of
                # someone's religion and nationality, which ADR-0003 forbids
                # anywhere in the pipeline. Checked before the section gate so
                # it also holds for such a line sitting inside a languages
                # section.
                if names_a_protected_attribute(span.text):
                    continue
                # "English Premier League" names a competition, not a language.
                # Outside a languages section, require the line to look like a
                # language entry rather than prose that happens to contain a
                # demonym.
                section = _section_at(offset, ranges)
                if section != "languages" and not _looks_like_language_entry(span.text, alias):
                    continue
                key = (canonical, span.start)
                if key in seen:
                    continue
                seen.add(key)
                facts.append(LanguageFact(canonical, span))
    return facts


#: Corroborating tokens that make a line a language entry rather than prose.
_LANGUAGE_CONTEXT = (
    "language",
    "languages",
    "bahasa",
    "fluent",
    "native",
    "bilingual",
    "proficiency",
    "mother tongue",
    "toefl",
    "ielts",
    "toeic",
    "jlpt",
    "hsk",
    "speaking",
    "written",
    "spoken",
    "lisan",
    "tulisan",
)


def _looks_like_language_entry(line: str, alias: str) -> bool:
    lowered = line.lower()
    if any(token in lowered for token in _LANGUAGE_CONTEXT):
        return True
    # A short delimited list -- "English, Indonesian, Japanese" -- is a language
    # list even without a header. Long prose lines are not.
    stripped = lowered.strip()
    return len(stripped) <= 60 and ("," in stripped or stripped == alias)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def extract_facts(cv_text: str, *, as_of_year: int, as_of_month: int = 12) -> CvFacts:
    """Every deterministic fact this engine can read from one CV."""
    text = normalize_text(cv_text)
    ranges = _sections(text)
    return CvFacts(
        skills=extract_skills(text, ranges),
        roles=extract_roles(text, ranges, as_of_year=as_of_year, as_of_month=as_of_month),
        education=extract_education(text, ranges),
        gpa=extract_gpa(text),
        languages=extract_languages(text, ranges),
    )


__all__ = [
    "CvFacts",
    "EducationFact",
    "GpaFact",
    "LanguageFact",
    "RoleFact",
    "SkillFact",
    "Span",
    "extract_facts",
    "line_at",
    "total_months",
]

# ADR-0012: Screening criteria are structured, not free text

**Status:** Accepted. **Supersedes [ADR-0009](0009-natural-language-screening-criteria.md).**

## Context

ADR-0009 made the recruiter's criteria a free-text box in whatever language they
think in, and a language model turned that prose into structured requirements.
That was the project's most distinctive claim. It is now retired, on evidence.

A deterministic recruitment information-extraction engine was built and measured
against the same 89 labelled `(candidate, requirement)` pairs the evaluation
harness already used. The two halves of the task behaved completely differently.

**Matching a CV against an already-structured requirement — deterministic code
won:**

| | Deterministic engine | Qwen2.5-7B incumbent |
|---|---:|---:|
| Verdict agreement | **88.8%** | 82.0% |
| Over-crediting | **0** | 2 |
| Evidence validity | **100%** | 88% |
| Reproducibility | **100%** | not bit-stable |
| Time per CV | **5.5 ms** | ~14,900 ms |
| Model download | **0 bytes** | 4.7 GB |

**Turning free text into requirements — deterministic code lost badly.** Given
the same formal job description the engine emitted **26 "requirements"** against
the model's 12, including `About the role` and
`We review every application ourselves.` On the informal briefs it could not
scope must-have and preferred markers across a compound clause: from
*"minimal 2 tahun pengalaman, kalau pernah AI/ML lebih bagus"* it marked every
extracted item must-have, and from *"python + postgres, 2+ yrs, aws would be
nice"* it marked every item preferred. It also missed whole requirements that
its vocabulary did not cover — GPA thresholds, language ability, university
tier.

Those two results point the same way: the weakness was never the *judging*, it
was the *intake*.

## Decision

**The recruiter chooses from a fixed vocabulary of six criterion types and
supplies structured values.** There is no free-text criteria box.

1. `EDUCATION_MIN` — minimum degree (S1/S2/S3, Bachelor/Master/Doctorate)
2. `GPA_MIN` — minimum grade, on a recruiter-stated scale
3. `EXPERIENCE_MIN` — minimum professional experience
4. `SKILL` — one skill from a controlled taxonomy, required or preferred
5. `INTERNSHIP_MIN` — minimum internship duration, or presence
6. `LANGUAGE_PRESENT` — the CV evidences a named language

**No language model is on the screening path.** Extraction, matching, scoring
and ranking are ordinary code.

### The line that decided the vocabulary

A criterion ships when a wrong answer is **a fact you can point at** — the
extractor missed `IPK 3,62`. It waits when a wrong answer is **a judgement you
would have to invent a label for** — is this project *substantial*?

That is why certifications and project experience are not in V1 even though a
regex could be written for both: `evaluation/README.md` already warns that
hand-writing both a fixture and its expected answer measures the author's
consistency and nothing else. That warning bites on judgement and barely at all
on "does a role title contain *intern*".

### Deliberately rejected

- **University / university tier.** A socioeconomic proxy, which is the class
  [ADR-0003](0003-sensitive-attribute-exclusion.md) exists to keep out of
  scoring. It would also require a contested, country-specific ranking list to
  live in this repository as a maintained political artifact. An institution may
  be *displayed*; it is never scored.
- **Language proficiency level.** CVs rarely state CEFR. Inferring "intermediate"
  from prose, or from the language the CV happens to be written in, is guessing,
  and guessing is what [ADR-0002](0002-evidence-first-evaluation.md) forbids.
- **Industry experience.** Needs employer-to-sector knowledge that is not in the
  document.
- **AI/ML as its own criterion type.** It is *"does the CV evidence TensorFlow,
  PyTorch, scikit-learn or machine learning"* — already a set of `SKILL` rows.
  A separate type would be duplicate machinery for the same question.

### A fourth verdict

`NEEDS_REVIEW` joins `MATCHED` / `PARTIAL` / `NO_EVIDENCE`. It means *the CV
says something here and the engine cannot safely resolve it* — a GPA on an
unstated scale, for instance. It is **not** a synonym for absence, and
conflating the two would be the exact failure this project spent four ADRs
avoiding: reporting a gap in our own reading as a fact about the person.

It is excluded from both sides of the score (see
[ADR-0008](0008-deterministic-scoring.md) for why an undefined quantity is not
zero), and it never trips the must-have guard, which exists for *missing*
evidence. It does cap the displayed band at `REVIEW` and raise a visible flag,
because "we could not tell" must not silently read as "fine".

## Consequences

**Gained.** No model on the screening path: no 4.7 GB download, no GPU, no API
key, no per-request cost, no provider outage, 5.5 ms per CV, and results that
are bit-identical run to run. The engine is a pure function of text, which makes
the browser-local goal a matter of porting rather than of model feasibility.

**Lost, and worth naming.** The product no longer accepts a criterion nobody
anticipated. A recruiter who wants *"experience with microservices at scale"*
cannot express it, and the interface says so rather than pretending. The
distinctive claim moves from *how criteria are entered* to **what comes back**:
every verdict quotes the document it came from, unverifiable quotes are refused,
and absence is reported as absence of evidence. That was always the stronger
half.

**A new maintenance cost.** The skill taxonomy — 53 skills, 153 surface forms —
is now load-bearing and hand-maintained. Every new framework is a code change.
The controlled dropdown at least makes that boundary *visible*: an unsupported
skill is shown as unsupported instead of silently returning "no evidence", which
is what the prototype did.

**Retained.** The LLM path (`profile-extraction-v1`, `semantic-match-v2`, Ollama,
the fixtures and the audit trail) is kept as optional infrastructure. It is off
the screening path, not deleted: the deterministic engine abstained on roughly a
third of the evaluation pairs, and discarding a working fallback on that evidence
would be premature.

**See also:** [ADR-0001](0001-ai-deterministic-boundary.md),
[ADR-0002](0002-evidence-first-evaluation.md),
[ADR-0003](0003-sensitive-attribute-exclusion.md),
[ADR-0008](0008-deterministic-scoring.md).

---

## Amendment: a seventh type, `EXPERIENCE_IN_FIELD`

*Added after this ADR was accepted, on evidence from a real CV. The six types
above are unchanged; this is an addition, not a revision.*

**What went wrong.** `EXPERIENCE_MIN` asks only how long somebody has worked.
On an accounting vacancy, four years of retail answered it exactly as well as
four years of accounting — the criterion has no way to tell them apart, because
duration is the whole of what it measures. A recruiter looking at the result
saw a full match on "at least 2 years of experience" for a candidate whose work
had no bearing on the role.

**Why this is not the industry criterion this ADR rejected.** The rejection
above stands: "industry experience" is unreadable from a CV without deciding
what counts as an industry, and that decision is exactly the kind of judgement
this project keeps out of code. `EXPERIENCE_IN_FIELD` asks a different and
answerable question — *does the CV's own entry for this dated role claim a
skill from the supported list?* The field is named from the same 85-term
vocabulary every skill criterion uses, and "claims" is decided by
`extract_skills`, so a denial, a pasted advert or a requirement-shaped sentence
inside the entry does not count. Nothing is inferred about a sector.

**The shape.** A subject *and* a threshold, which no earlier type carried. Both
are required: a field with no duration is already the `SKILL` criterion, and a
duration with no field is already `EXPERIENCE_MIN`. This forced three changes
that had assumed the two were mutually exclusive — the request validator, the
database CHECK constraint (migration `e7c95a2f1b08`), and the test that pairs
the published vocabulary against the accepting endpoint.

**Three outcomes, kept apart.** No dated work at all is absence of experience.
Dated work that never mentions the field is absence of experience *in that
field*, which is what was asked and is a real answer rather than an evasion.
Dated work in the field but short of the bar is `PARTIAL`, like any duration. A
field outside the vocabulary is `NEEDS_REVIEW`, because our vocabulary's limit
is not the candidate's gap.

**What it reads.** The role's whole entry — its own line down to the next dated
role or the end of the section — not just the line carrying the dates. A CV
names the field it worked in underneath the title far more often than inside it:
"Staff Administrasi" followed by a bullet about `rekonsiliasi bank` is ordinary,
and reading only the title would miss it.

**Known limit.** Where one entry's bullets run into the next entry without a
dated line between them, the block boundary is approximate and a skill could be
attributed to the wrong role. The block ends at the next *dated* role, so an
undated entry in between is absorbed.

**Refined while building its evaluation.** Designing the labelled set for
`EXPERIENCE_IN_FIELD` exposed a flaw in its first version. Most CVs list skills
in a `SKILLS` block and dated roles in an `EXPERIENCE` block, and never say
which role used which skill. The first version answered `NO_EVIDENCE` for those
— reporting a gap in the CV's *formatting* as a gap in the candidate, which is
the exact confusion this ADR added `NEEDS_REVIEW` to prevent.

Two documents are now told apart. A CV that never mentions the skill shows
nothing, and absence is honest. A CV that claims the skill and lists dated roles
without joining them up is `NEEDS_REVIEW`, citing the line that made the claim —
so it leaves the score entirely rather than counting as a zero, and a person is
asked to read it. `MATCHED` still requires the CV's own entry for a dated role
to evidence the skill.

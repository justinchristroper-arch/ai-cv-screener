# ADR-0010: A requirement naming a protected characteristic cannot be confirmed

**Status:** Accepted

## Context

[ADR-0003](0003-sensitive-attribute-exclusion.md) keeps sensitive attributes out of the candidate profile by construction: there is no column for age, gender, marital status, religion, ethnicity, nationality, appearance or health anywhere in the schema, so the scoring stage cannot receive them. That protects one half of the question — what the system knows about a candidate.

[ADR-0009](0009-natural-language-screening-criteria.md) opened the other half. When requirements could only come from a formal job posting, a criterion about a person rather than about their work was a remote possibility. Now the recruiter types free text, and free text says things like *"wanita, maksimal 25 tahun"*. Someone will type it, because in some markets it is a normal line in a job advertisement.

Nothing in the pipeline stopped it. Extraction would produce a requirement, confirmation would freeze it, and matching would go looking for evidence — and it would sometimes find some, because a CV can print `Gender: Female` and the semantic matcher reads the CV text, not the profile. A verdict backed by a verbatim quote from the document would look exactly as legitimate as any other.

## Decision

A deterministic scanner (`app/core/protected_attributes.py`) reports which protected characteristics a piece of text names. It is used in three places:

1. **The confirmation gate refuses.** `requirements.confirm_requirements` raises a `ConflictError` naming every offending requirement and which characteristics it found. Because confirmation is the single gate every screening stage passes through (ADR-0004), a requirement that cannot be confirmed can never reach a candidate. This is the guarantee; the other two are so that a person meets it early rather than as a 409.
2. **The requirement row is flagged** in the API and the UI, in words as well as colour, with what will happen and what to do about it.
3. **The criteria text is flagged** as soon as it is saved, before extraction, so the problem is visible while the person is still looking at what they wrote.

The extraction prompt is also told not to emit such a requirement. That is a courtesy, not a control: it is model-dependent, so the code rule stands behind it.

Three things this deliberately does **not** do:

- **It never edits anyone's text.** The criteria are stored byte for byte as typed. This tool does not quietly rewrite a recruiter's words.
- **It never decides anything about a candidate.** No one is filtered, rejected, or scored differently. The refusal is about what the *system* will screen on.
- **It does not hard-block anything the recruiter cannot undo.** Removing or rewording one line clears it, and that is a step they are already taking — they are in the review screen when it happens.

The patterns are deliberately narrow and anchored to words that only appear when the characteristic itself is the subject: `usia`/`umur`/`age`, never a bare `tahun`, so *"minimal 2 tahun pengalaman"* passes untouched; `race` excludes `race condition`. A language a person can use (*"bisa bahasa Inggris"*) is a job-relevant skill and is never flagged.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Let it be confirmed, then neutralise it at screening — force `NO_EVIDENCE` and exclude it from the score | Silently produces "no evidence" for every candidate on a criterion the recruiter believes is working. They learn nothing, and the system has quietly overruled them without saying so. It also means touching matching and scoring, for a worse outcome. |
| Warn only, and confirm anyway | Then the guarantee is a suggestion. The one thing the system must not do is screen someone on their age; a warning does not prevent it. |
| Rely on the extraction prompt alone | Model-dependent, unversioned in effect, and silently bypassed by a hand-added requirement — which is a button in the UI. |
| Strip the offending phrase from the criteria text | Editing what someone wrote, invisibly. The project's standing rule for untrusted or suspicious text (architecture §5) is to flag and surface, never to strip. |
| A broader pattern set, erring toward catching more | A false positive costs a recruiter one edit but the workflow stops until they make it, so the trade favours narrowness. Missing an unusual phrasing leaves the system where it already was; false-positiving on "minimal 2 tahun" would break the ordinary path for every Indonesian user. |

## Consequences

**Benefits:** "no candidate is ever screened on a protected characteristic" becomes a property of the code, enforced at a gate that cannot be routed around, and it is tested. The recruiter is told what is wrong, where, and why, in language that does not lecture them.

**Costs and limits, stated plainly:**

- **It reads Indonesian and English.** A criterion written another way will not be caught. This is a coarse signal for a human, not a compliance control, and it is not legal advice.
- **It is pattern matching.** It will miss paraphrase (*"we want someone young and energetic"*) and it can false-positive on unusual wording, which costs an edit.
- **It does nothing about proxies.** University, employer, career gaps and name all survive, and all carry demographic signal. This guard narrows one specific hole; it does not make the system unbiased, and [`README.md`](../../README.md) says so.
- **The scanner runs on read, never stored.** The text is the whole input, so nothing can go stale, and a pattern added later covers requirements written before it existed. The cost is one regex pass per read, which is negligible against a database round trip.

**See also:** [ADR-0003](0003-sensitive-attribute-exclusion.md), [ADR-0004](0004-human-confirmation-gate.md), [ADR-0009](0009-natural-language-screening-criteria.md), [`docs/product-spec.md` §13](../product-spec.md#13-bias-and-fairness-principles).

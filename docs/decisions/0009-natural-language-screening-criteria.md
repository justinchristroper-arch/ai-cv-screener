# ADR-0009: Screening criteria are free text, not a job description

**Status:** Accepted

## Context

Until this decision, stage 2 of the pipeline was called "job description extraction" and its prompt asked for exactly that: a formal posting, in English, with the structure a posting has. That assumption was inherited from the specification's happy path and never questioned.

It does not survive contact with the intended user. A recruiter screening a shortlist frequently has no posting to hand — what they have is a few lines about who they want, typed in the language they think in, in whatever register they think in: *"minimal S1, IPK di atas 3, bisa bahasa Inggris, pengalaman Python minimal 2 tahun"*. The first user walkthrough of this project hit exactly that, and the product's answer was an error message about a missing recording.

Requiring a formal document before the product will do anything is a barrier with nothing behind it. The rest of the pipeline — evidence verification, deterministic matching, scoring, ranking — never cared what shape the input text had; only the prompt did.

## Decision

The first stage accepts **whatever the recruiter wrote**. A full job posting, a bulleted list, or two informal sentences are all valid input, in any language, mixing languages, with local abbreviations and non-standard spelling.

Concretely, in `jd-extraction-v2`:

- The prompt is framed around *screening criteria*, not a job description, and says explicitly that a short brief must not be padded out with requirements a role like this "usually" has. A recruiter who wrote three lines meant three lines.
- Each requirement is returned **in the language the recruiter used**, in their vocabulary, keeping technology and institution names exactly as written. They have to read, check and correct the list; a list written in a language they did not use is a list they cannot check.
- Category and must-have guidance covers both languages this build was exercised against — `harus`, `wajib`, `minimal` are hard; `diutamakan`, `lebih bagus`, `kalau ada` are not.
- `semantic-match-v2` gains the corresponding rule for the other side of the pipeline: the `evidence_quote` is always in the **CV's** language, copied verbatim, because a translated quote is not in the document and the verifier will reject it; the `reason` is in the **requirement's** language, because the recruiter reads it.

Nothing else changed. The confirmation gate, evidence verification, the deterministic/LLM boundary, scoring and ranking are untouched — the input got wider, not the model's authority.

The stored entity is still `job_description` and the service is still `jd_extraction`. Renaming them would be a migration and a wide refactor bought for a word; the user-facing copy and the prompt carry the reframing, which is where it matters.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Keep requiring a job description, and tell users to write one | Solves the product's problem by giving it to the user. The pipeline never needed the structure. |
| Translate every requirement into English internally, keeping one working language | The recruiter loses the ability to check the list, which is the whole point of the confirmation gate (ADR-0004). It also introduces a translation step whose errors would be invisible and unattributable. |
| Store both the original and an English translation per requirement | Twice the surface for the same information, a new column to keep in sync, and a second thing that can be wrong. The semantic matcher reads across languages without it. |
| Detect the language and switch prompts | Two prompts to version and keep in step, and it fails exactly where informal input is hardest — a line that mixes both languages. One prompt that is told to follow the input's language handles the mixed case by construction. |

## Consequences

**Benefits:** the product works for the user it was built for, without asking them to produce a document first. Demo mode demonstrates it: four sample briefs — a formal posting, informal Indonesian, mixed Indonesian/English, and informal English — each with recordings for the whole pipeline, so the feature can be walked end to end with no API key.

**Costs:** the prompt version bumped to `jd-extraction-v2` and `semantic-match-v2`, which re-keys every recording (the fixture loader recomputes hashes, so this is mechanical, and each re-keyed file says in its description that it was re-keyed rather than re-recorded). More importantly, **multilingual quality is not measured**. The fixtures show what this application does with such input; they say nothing about how well a live model reads Indonesian, because the same person wrote the recording and the expectation. That question needs a live provider — `scripts/check_llm.py` is the path — and until it is run, no claim about multilingual accuracy is supported.

**A new risk, addressed separately:** free text can ask for things a screening system must not screen on. See [ADR-0010](0010-protected-attribute-guard.md).

**See also:** [`docs/architecture.md` §5](../architecture.md#5-trust-boundary), [ADR-0001](0001-ai-deterministic-boundary.md), [ADR-0004](0004-human-confirmation-gate.md), [`evaluation/RESULTS.md`](../../evaluation/RESULTS.md).

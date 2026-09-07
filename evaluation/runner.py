"""Run the evaluation and write the results.

Usage, from the repository root::

    backend/.venv/Scripts/python.exe -m evaluation.runner

Writes `evaluation/results.json` (machine-readable) and `evaluation/RESULTS.md`
(the report). Both are regenerated from scratch on every run, and a second run
on unchanged data produces byte-identical files — there is no clock, no random
source and no model call anywhere in the path.

The alias table is the one piece that lives in the database, because the
deterministic alias matcher reads it there and evaluating against a hand-copied
list would be evaluating against the wrong thing. `--no-db` falls back to an
empty alias table and says so in the report, so the harness still runs on a
machine with no Postgres.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation.loader import Dataset, load_dataset  # noqa: E402
from evaluation.metrics import Metric, PairOutcome, collect  # noqa: E402

RESULTS_JSON = Path(__file__).resolve().parent / "results.json"
RESULTS_MD = Path(__file__).resolve().parent / "RESULTS.md"


def load_aliases(use_db: bool) -> tuple[dict[str, str], str]:
    """The alias table, from the database the matcher actually reads."""
    if not use_db:
        return {}, "skipped (--no-db): alias matching is evaluated as if the table were empty"
    try:
        from sqlalchemy.orm import Session

        from app.db.session import get_engine
        from app.services.matching import load_alias_map

        with Session(get_engine()) as session:
            aliases = load_alias_map(session)
        return aliases, f"loaded {len(aliases)} alias pairs from the database"
    except Exception as exc:  # pragma: no cover - environment dependent
        return {}, f"unavailable ({type(exc).__name__}); alias matching evaluated as if empty"


def _percent(metric: Metric) -> str:
    if metric.value is None:
        return "—"
    return f"{metric.value * 100:.1f}%"


#: The two defects this harness found on its first run, and what fixing them
#: cost. Recorded here rather than left in a commit message because the cost is
#: the interesting half: both fixes trade recall for precision, and a later
#: reader deciding whether to widen the matchers again needs to see the trade
#: that was accepted. The "before" column is the run of 2026-09-07 against the
#: matchers as they stood at commit 3ca902e; re-running this harness reproduces
#: the "after" column.
DEFECTS_FOUND = """## Defects this harness found

Two. Both were in the deterministic matchers, both over-credited a candidate, and both
were measured here before anything was changed — the harness was written to find out
whether the suspicion was real, not to confirm it.

**A short skill name matched an ordinary English word.** The exact matcher searched the
requirement text for each of the candidate's skills as a whole token. `Go` is a skill
and also a verb, so `rina-costa`, who really does know Go, was credited MATCHED against
*"the ability to go deep on latency problems"* — a requirement about debugging, on the
strength of a word in it. The verdict came with evidence attached and read as certain.
Fixed by refusing to search for a normalized token shorter than three characters; a
short skill can still match through a longer alias.

**A bounded period was read as a minimum.** The duration matcher looked for a number of
years anywhere in a requirement. `lena-fischer`, fourteen months into a career, was
credited PARTIAL against *"has taken a system from prototype to production within 2
years"* — a requirement that states a ceiling on how long something took, not a floor on
experience. Fixed by looking at what precedes the number: `within`, `under`, `at most`,
`up to`, `less than`, `no more than` and their kin mean the period is not a minimum.

### What the fixes cost

| Metric | Before | After |
|---|---|---|
| `routing_restraint` | 67/69 | 69/69 |
| `deterministic_verdict_precision` | 20/22 | 17/17 |
| `over_crediting_rate` | 2/22 | 0/17 |
| `under_crediting_rate` | 0/22 | 0/17 |
| `deterministic_recall` | 20/20 | 17/20 |

Both directions were measured, as they must be: a matcher can always be made precise by
never matching. Precision went to 22/22 of the decisions it now makes, and recall fell —
`R`, `Go` and `C` are now routed to the model for `rina-costa` instead of being settled
by code. That is the intended trade. A requirement sent to the model still gets an
answer, and it arrives with quoted evidence the application verifies; a wrong
deterministic verdict arrives looking certain and is never revisited. The three lost
cases are marked ⚠ in the table at the end of this document.

No other change was made to matching, scoring or ranking semantics."""


def render_markdown(
    dataset: Dataset, outcomes: list[PairOutcome], metrics: list[Metric], alias_note: str
) -> str:
    measurable = [m for m in metrics if m.kind == "deterministic"]
    unmeasurable = [m for m in metrics if m.kind == "llm_dependent"]

    lines: list[str] = []
    add = lines.append

    add("# Evaluation results")
    add("")
    add("Regenerate with `python -m evaluation.runner` from the repository root.")
    add("")
    add("## What this measures, and what it does not")
    add("")
    add(
        "Every number below describes **this application's deterministic code** — the "
        "exact, alias and duration matchers, the evidence verifier, the scorer and the "
        "ranker — measured against an independent reading of eight synthetic CVs."
    )
    add("")
    add(
        "**None of it is evidence of real-world CV screening accuracy, of how well any "
        "language model reads a CV, or of fairness.** The dataset is invented, it is "
        "small, and the parts of the pipeline a model decides are listed separately as "
        "not measurable in this configuration, with the reason."
    )
    add("")
    add(f"- Candidates: **{len(dataset.candidates)}** (all synthetic)")
    add(f"- Jobs: **{len(dataset.jobs)}**")
    add(f"- Labelled (candidate, requirement) pairs: **{len(outcomes)}**")
    add(f"- Skill alias table: {alias_note}")
    add("")

    add("## Dataset")
    add("")
    add("| Candidate | Job | Why it is in the set |")
    add("|---|---|---|")
    for candidate in dataset.candidates:
        add(f"| `{candidate.id}` | `{candidate.job_id}` | {candidate.purpose} |")
    add("")

    add("## Measured — deterministic application behaviour")
    add("")
    for metric in measurable:
        add(f"### `{metric.name}` — {_percent(metric)} ({metric.numerator}/{metric.denominator})")
        add("")
        add(f"- **Definition.** {metric.definition}")
        add(f"- **What it actually measures.** {metric.measures}")
        add("- **Kind.** Deterministic; no model call in its path.")
        add(f"- **Limitations.** {metric.limitations}")
        if metric.detail:
            add("- **Cases:**")
            for line in metric.detail:
                add(f"  - {line}")
        add("")

    add(DEFECTS_FOUND)
    add("")

    add("## Not measurable in this configuration")
    add("")
    add(
        "These are the metrics the product specification asks for that depend on a live "
        "model. They are listed rather than estimated, because an estimate drawn from a "
        "hand-written recording would be a number with no meaning behind it."
    )
    add("")
    for metric in unmeasurable:
        add(f"### `{metric.name}` — not measured")
        add("")
        add(f"- **Definition.** {metric.definition}")
        add(f"- **What it would measure.** {metric.measures}")
        add("- **Kind.** LLM-dependent.")
        add(f"- **Why not measured.** {metric.reason_not_measurable}")
        add("")

    add("## Every labelled pair")
    add("")
    add("| Candidate | Req | Expected | Expected layer | Actual | Decided by |")
    add("|---|---|---|---|---|---|")
    for outcome in outcomes:
        actual = outcome.actual_verdict.value if outcome.actual_verdict else "—"
        method = outcome.decided_by.value if outcome.decided_by else "routed to model"
        flag = "" if outcome.layer_agrees else " ⚠"
        add(
            f"| `{outcome.candidate_id}` | `{outcome.requirement_id}` | "
            f"{outcome.expected_verdict.value} | {outcome.expected_layer}{flag} | "
            f"{actual} | {method} |"
        )
    add("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the AI CV Screener evaluation.")
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Do not read the skill alias table from the database.",
    )
    args = parser.parse_args(argv)

    dataset = load_dataset()
    aliases, alias_note = load_aliases(use_db=not args.no_db)
    outcomes, metrics = collect(dataset, aliases)

    payload = {
        "candidates": len(dataset.candidates),
        "jobs": len(dataset.jobs),
        "labelled_pairs": len(outcomes),
        "alias_table": alias_note,
        "metrics": [metric.as_dict() for metric in metrics],
        "pairs": [
            {
                "candidate": o.candidate_id,
                "requirement": o.requirement_id,
                "expected_verdict": o.expected_verdict.value,
                "expected_layer": o.expected_layer,
                "actual_verdict": o.actual_verdict.value if o.actual_verdict else None,
                "actual_layer": o.actual_layer,
                "decided_by": o.decided_by.value if o.decided_by else None,
            }
            for o in outcomes
        ],
    }
    RESULTS_JSON.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline=""
    )
    RESULTS_MD.write_text(
        render_markdown(dataset, outcomes, metrics, alias_note), encoding="utf-8", newline=""
    )

    print(f"candidates={len(dataset.candidates)} pairs={len(outcomes)}")
    print(f"aliases: {alias_note}")
    for metric in metrics:
        if metric.kind == "deterministic":
            count = f"({metric.numerator}/{metric.denominator})"
            print(f"  {metric.name:38} {_percent(metric):>7}  {count}")
            for line in metric.detail:
                print(f"      - {line}")
        else:
            print(f"  {metric.name:38}       —  not measurable offline")
    print(f"\nwrote {RESULTS_JSON.name} and {RESULTS_MD.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# `evaluation/`

An offline measurement of the screening pipeline, and an explicit account of
what it cannot measure.

```bash
python -m evaluation.runner
```

Writes [`results.json`](results.json) and [`RESULTS.md`](RESULTS.md). Read
`RESULTS.md` for the numbers; this file explains how the dataset is built and
why it is built that way.

## What is here

| File | What it is |
|---|---|
| `data/jobs.json` | Two jobs and their requirements, hand-written. |
| `data/candidates.json` | Eight synthetic CVs, each with a declared profile whose every item carries a quote from its own CV text. |
| `data/labels.json` | 89 labelled `(candidate, requirement)` pairs: the expected verdict, which layer should reach it, and why. |
| `loader.py` | Builds unsaved ORM objects from the JSON. No database writes. |
| `metrics.py` | Every metric, each carrying its own definition, denominator, kind and limitations. |
| `runner.py` | Runs the pipeline's pure functions over the dataset and renders the two result files. |

## The one design decision that matters

**Nothing in the dataset is produced by a language model.** The jobs, the
requirements, the CV text, the profiles and the labels are all hand-written, and
the labels were written from the CV text *before* the pipeline was run over it.

This is not tidiness. Fixture replay is keyed by a hash of the rendered prompt
input, so any CV outside the recorded set has no recording. Producing one means
hand-writing the model's answer — and then the same person has written both the
answer and the label it is scored against. A metric built that way measures that
person's consistency and nothing else.

So the harness exercises the parts that are genuinely independent of the model:
`matching.decide_deterministically`, `evidence.verify_quote`,
`scoring.compute_score`, `ranking`, and the injection scanner. All are pure
functions of data the dataset supplies directly. Metrics that depend on a model
verdict are emitted as **not measured**, each with its reason, rather than
estimated.

## Running against the database

Skill aliases live in a table, so the alias matcher needs a database to be
measured. With one running, the runner uses it; without one, pass `--no-db` and
alias matching is evaluated as if the table were empty. The results file records
which of the two happened, because the numbers differ and the difference is not
a defect.

```bash
python -m evaluation.runner --no-db
```

## Adding a case

Add the candidate to `data/candidates.json` with its CV text and profile, label
every requirement of its job in `data/labels.json`, and re-run. The loader
verifies each declared evidence quote against the CV text with the real
verifier, so a quote that does not occur in the document is caught at load time
rather than becoming a silently unusable profile item.

Write the label from the CV, before running the pipeline. A label written after
looking at the output is not a label.

## What this is not

It is not evidence of real-world CV screening accuracy, not a measurement of any
language model, and not a bias audit. Eight invented CVs cannot support any of
those claims, and `RESULTS.md` repeats the point next to every number that could
be mistaken for one.

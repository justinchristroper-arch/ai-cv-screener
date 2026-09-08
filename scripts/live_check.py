"""Exercise Live AI Mode against the real provider, and report what happened.

Everything else in this repository runs offline against recordings, which is
what makes the test suite free and deterministic. That leaves exactly one class
of question unanswered: *does the live path work, and how well does the model
actually read a recruiter's criteria?* No recording can answer it, because a
recording is written by the same person who would grade it.

This script is that answer. It is opt-in, it is never run by CI, and it costs
money every time.

    python scripts/live_check.py                 # the four bundled sample briefs
    python scripts/live_check.py --stability 3   # same brief three times
    python scripts/live_check.py --text "cari data analyst, bisa SQL, minimal 1 tahun"
    python scripts/live_check.py --json out.json # machine-readable report

Requires `ANTHROPIC_API_KEY` in the environment or in `.env`. It refuses to run
in demo mode, because a "live check" served from a recording would be a lie.

What it reports, per brief: the requirements the model returned, their category
and must-have flag, whether the reply passed this application's own schema
validation, and the tokens and wall-clock time the call took. Nothing here is
written to the database, and no candidate record is created.

Read the numbers as what they are: one run, on a handful of invented briefs,
against whatever model version answered today. That is evidence the live path
works and a sample of how it behaves. It is not a benchmark, and it is not
evidence of real-world screening accuracy. See evaluation/RESULTS.md for the
boundary between what this project measures and what it does not.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from pydantic import ValidationError  # noqa: E402

from app.core.config import ConfigurationError, load_settings  # noqa: E402
from app.core.errors import LlmUnavailableError  # noqa: E402
from app.llm.client import LiveLlmClient, LlmProviderError  # noqa: E402
from app.llm.prompts import jd_extraction as jd_prompt  # noqa: E402
from app.schemas.llm.jd_extraction import RequirementExtractionOutput  # noqa: E402
from app.services.demo import SAMPLE_CRITERIA  # noqa: E402


@dataclass
class CallReport:
    """One live call, and everything worth knowing about it afterwards."""

    label: str
    language: str
    criteria: str
    ok: bool
    model: str = ""
    latency_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    requirements: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def must_have_count(self) -> int:
        return sum(1 for item in self.requirements if item["must_have"])


def _settings():
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        raise SystemExit(f"{exc}\n\nLive check cannot run without valid configuration.") from exc

    if settings.demo_mode:
        raise SystemExit(
            "DEMO_MODE is true, so this process is wired to replay recordings.\n"
            "A live check served from a recording would prove nothing. Set DEMO_MODE=false\n"
            "in .env (or the environment) and make sure ANTHROPIC_API_KEY is a real key."
        )
    if not settings.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set. Live mode needs a real key.")
    return settings


def _extract_once(client: LiveLlmClient, label: str, language: str, text: str) -> CallReport:
    """One live extraction, validated exactly the way the application validates it."""
    report = CallReport(label=label, language=language, criteria=text, ok=False)
    request = jd_prompt.build_request(text)

    started = time.monotonic()
    try:
        response = client.complete(request)
    except (LlmProviderError, LlmUnavailableError) as exc:
        report.error = f"{type(exc).__name__}: {exc}"
        report.latency_ms = int((time.monotonic() - started) * 1000)
        return report

    report.model = response.model
    report.latency_ms = response.latency_ms
    report.input_tokens = response.input_tokens
    report.output_tokens = response.output_tokens

    try:
        payload = json.loads(response.text)
        output = RequirementExtractionOutput.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        # Reported, not retried. The application retries once; this script is
        # here to show what the model actually returns, so a first-attempt
        # failure is the finding rather than something to paper over.
        report.error = f"reply failed this application's own validation: {exc}"
        return report

    report.ok = True
    report.requirements = [
        {"text": item.text, "category": item.category.value, "must_have": item.must_have}
        for item in output.requirements
    ]
    return report


def _print_report(report: CallReport) -> None:
    head = f"{report.label}  [{report.language}]"
    print()
    print("=" * 78)
    print(head)
    print("-" * 78)
    print(f"  criteria: {report.criteria[:200]}{'…' if len(report.criteria) > 200 else ''}")

    if not report.ok:
        print(f"  FAILED after {report.latency_ms} ms")
        print(f"  {report.error}")
        return

    tokens = f"{report.input_tokens} in / {report.output_tokens} out"
    print(f"  {report.model} · {report.latency_ms} ms · {tokens} tokens")
    print(f"  {len(report.requirements)} requirements, {report.must_have_count} must-have")
    print()
    for item in report.requirements:
        flag = "MUST" if item["must_have"] else "nice"
        print(f"    [{flag}] {item['category']:18} {item['text']}")


def _stability(reports: list[CallReport]) -> None:
    """Whether repeated runs on identical input agreed, and where they did not."""
    usable = [r for r in reports if r.ok]
    if len(usable) < 2:
        print("\nStability: not enough successful runs to compare.")
        return

    signatures = [
        tuple(
            sorted(
                (item["text"], item["category"], item["must_have"]) for item in report.requirements
            )
        )
        for report in usable
    ]
    identical = all(signature == signatures[0] for signature in signatures)
    counts = [len(report.requirements) for report in usable]
    print()
    print("=" * 78)
    print(f"Stability over {len(usable)} runs of the same input")
    print("-" * 78)
    print(f"  requirement counts: {counts}")
    print(f"  identical requirement sets: {'yes' if identical else 'no'}")
    if not identical:
        print("  (a model is not required to be deterministic; this records what happened)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--text",
        action="append",
        default=[],
        help="Extra criteria to try, verbatim. Repeatable. Skips the bundled samples.",
    )
    parser.add_argument(
        "--stability",
        type=int,
        default=1,
        metavar="N",
        help="Run the first brief N times and report whether the results agreed (default 1).",
    )
    parser.add_argument("--json", type=Path, help="Also write the full report to this file.")
    parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt. For scripted runs."
    )
    args = parser.parse_args(argv)

    settings = _settings()

    if args.text:
        briefs = [("Custom", "as supplied", text) for text in args.text]
    else:
        briefs = [(item.label, item.language, item.text) for item in SAMPLE_CRITERIA]

    calls = len(briefs) + max(0, args.stability - 1)
    print(f"Live check: {calls} call(s) to {settings.llm_model}. This costs real money.")
    prompt_first = not args.yes and sys.stdin.isatty()
    if prompt_first and input("Continue? [y/N] ").strip().lower() not in {"y", "yes"}:
        print("Nothing was sent.")
        return 1

    client = LiveLlmClient(api_key=str(settings.anthropic_api_key), model=settings.llm_model)

    reports: list[CallReport] = []
    for label, language, text in briefs:
        report = _extract_once(client, label, language, text)
        reports.append(report)
        _print_report(report)

    repeats: list[CallReport] = []
    if args.stability > 1:
        label, language, text = briefs[0]
        repeats = [reports[0]] + [
            _extract_once(client, f"{label} (repeat {n})", language, text)
            for n in range(2, args.stability + 1)
        ]
        _stability(repeats)

    failed = [r for r in reports if not r.ok]
    print()
    print("=" * 78)
    print(f"{len(reports) - len(failed)}/{len(reports)} briefs produced a usable requirement set.")
    if failed:
        for report in failed:
            print(f"  FAILED: {report.label} — {report.error}")
    if len(failed) == len(reports):
        print(
            "\nNo brief succeeded, so nothing here says anything about the model. An HTTP\n"
            "401 means the key was rejected; an HTTP 4xx or a connection error means the\n"
            "request never reached a model at all."
        )
    else:
        print(
            "\nThis is one run against a live model on invented briefs. It shows the live\n"
            "path works and gives a sample of how the model behaves. It is not a benchmark\n"
            "and says nothing about real-world screening accuracy."
        )

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "model": settings.llm_model,
                    "briefs": [asdict(r) for r in reports],
                    "stability_runs": [asdict(r) for r in repeats],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="",
        )
        print(f"\nWrote {args.json}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

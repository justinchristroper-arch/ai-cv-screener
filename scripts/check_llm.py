"""Check the configured AI provider, and report what it actually did.

Everything else in this repository runs offline against recordings, which is
what makes the test suite free and deterministic. That leaves one class of
question unanswered: *does the configured provider work, and how well does this
model read a recruiter's criteria?* No recording can answer it, because a
recording is written by the same person who would grade it.

This script is that answer. It is opt-in and never run by CI.

    python scripts/check_llm.py                  # the four bundled sample briefs
    python scripts/check_llm.py --preflight      # only check the setup, run no model
    python scripts/check_llm.py --stability 3    # same brief three times
    python scripts/check_llm.py --text "cari data analyst, bisa SQL, minimal 1 tahun"
    python scripts/check_llm.py --json out.json  # machine-readable report

It refuses to run in demo mode, because a "provider check" served from a
recording would be a lie.

**Ollama** (the default) needs `ollama serve` running and the configured model
pulled. The preflight names whichever of those is missing and stops before
generating anything, so the common first-run problems cost seconds rather than
a long wait for a timeout.

**DeepSeek** needs `DEEPSEEK_API_KEY` and spends money on every run. Its
preflight costs nothing: one `GET /models`, which proves the key is accepted and
that `DEEPSEEK_MODEL` is a model the key can use, before a token is spent.

**Anthropic** needs `ANTHROPIC_API_KEY` and spends money on every run.

What it reports, per brief: the requirements the model returned, their category
and must-have flag, whether the reply passed this application's own schema
validation, and the tokens and wall-clock time the call took. Nothing is written
to the database, and no candidate record is created.

Read the numbers as what they are: one run, on a handful of invented briefs,
against one model on one machine. That is evidence the provider works and a
sample of how it behaves. It is not a benchmark and not evidence of real-world
screening accuracy. See evaluation/RESULTS.md for the boundary between what
this project measures and what it does not.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from pydantic import ValidationError  # noqa: E402

from app.core.config import ConfigurationError, load_settings  # noqa: E402
from app.core.errors import LlmUnavailableError  # noqa: E402
from app.llm.client import (  # noqa: E402
    AnthropicLlmClient,
    DeepSeekLlmClient,
    LlmProviderError,
    OllamaLlmClient,
)
from app.llm.prompts import jd_extraction as jd_prompt  # noqa: E402
from app.schemas.llm.jd_extraction import RequirementExtractionOutput  # noqa: E402
from app.services.demo import SAMPLE_CRITERIA  # noqa: E402


@dataclass
class CallReport:
    """One call, and everything worth knowing about it afterwards."""

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
        raise SystemExit(f"{exc}\n\nThis check cannot run without valid configuration.") from exc

    if settings.demo_mode:
        raise SystemExit(
            "DEMO_MODE is true, so this process is wired to replay recordings.\n"
            "A provider check served from a recording would prove nothing. Set\n"
            "DEMO_MODE=false in .env and make sure the provider is configured."
        )
    return settings


def _preflight(settings) -> list[str]:
    """Everything checkable before spending a single token.

    Returns a list of problems, empty when the setup is ready. Ordered so the
    first line a developer reads is the thing to fix.
    """
    if settings.llm_provider == "anthropic":
        return [] if settings.anthropic_api_key else ["ANTHROPIC_API_KEY is not set."]

    if settings.llm_provider == "deepseek":
        return _deepseek_preflight(settings)

    tags_url = settings.ollama_base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(tags_url, timeout=10) as response:
            body = json.loads(response.read())
    except urllib.error.URLError:
        return [
            f"Ollama is not answering at {settings.ollama_base_url}.",
            "  Start it with:  ollama serve",
            "  Then re-run this check.",
        ]
    except (json.JSONDecodeError, OSError) as exc:
        return [f"Could not read the model list from {tags_url}: {type(exc).__name__}"]

    installed = [model.get("name", "") for model in body.get("models", [])]
    wanted = settings.ollama_model
    # Ollama reports "name:tag"; an untagged request means ":latest".
    candidates = {wanted, f"{wanted}:latest"}
    if not any(name in candidates or name.split(":")[0] == wanted for name in installed):
        return [
            f"Ollama is running, but {wanted!r} is not installed.",
            f"  Pull it once with:  ollama pull {wanted}",
            f"  Installed: {', '.join(installed) if installed else '(none)'}",
        ]
    return []


def _deepseek_preflight(settings) -> list[str]:
    """The key and the model, checked with one request that costs nothing.

    `GET /models` spends no tokens, so it can prove before anything is spent
    that the key is accepted and that DEEPSEEK_MODEL names a model the key can
    use — the counterpart of the Ollama check that the model is pulled. The key
    goes in the header only, and no line returned here contains it: exception
    texts are reduced to their class name, because Python's HTTP client quotes
    an invalid header value in full.
    """
    base_url = settings.deepseek_base_url.rstrip("/")
    request = urllib.request.Request(
        f"{base_url}/models",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {settings.deepseek_api_key.get_secret_value()}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return [
                "DeepSeek rejected the API key (HTTP 401).",
                "  Check DEEPSEEK_API_KEY in .env, or in the deployment's secret store.",
            ]
        return [f"DeepSeek answered HTTP {exc.code} when asked for its model list."]
    except urllib.error.URLError as exc:
        kind = f" ({type(exc.reason).__name__})" if isinstance(exc.reason, BaseException) else ""
        return [
            f"Could not reach DeepSeek at {base_url}{kind}.",
            "  Check the network connection and DEEPSEEK_BASE_URL.",
        ]
    except (OSError, ValueError) as exc:
        return [f"Could not read the model list from {base_url}/models: {type(exc).__name__}"]

    entries = body.get("data", []) if isinstance(body, dict) else []
    available = [entry.get("id", "") for entry in entries if isinstance(entry, dict)]
    wanted = settings.deepseek_model
    if wanted not in available:
        return [
            f"DeepSeek does not list {wanted!r} among the models this key can use.",
            f"  Available: {', '.join(available) if available else '(none)'}",
            "  Set DEEPSEEK_MODEL to one of those.",
        ]
    return []


def _build_client(settings):
    if settings.llm_provider == "anthropic":
        return AnthropicLlmClient(api_key=str(settings.anthropic_api_key), model=settings.llm_model)
    if settings.llm_provider == "deepseek":
        return DeepSeekLlmClient(
            api_key=settings.deepseek_api_key.get_secret_value(),
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            timeout_seconds=settings.deepseek_timeout_seconds,
        )
    return OllamaLlmClient(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout_seconds=settings.ollama_timeout_seconds,
    )


def _extract_once(client, label: str, language: str, text: str) -> CallReport:
    """One extraction, validated exactly the way the application validates it."""
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
        # Reported, not retried. The application retries once; this script exists
        # to show what the model actually returns, so a first-attempt failure is
        # the finding rather than something to paper over. It is also the number
        # that matters most when comparing a small local model to a large one.
        report.error = f"reply failed this application's own validation: {exc}"
        return report

    report.ok = True
    report.requirements = [
        {"text": item.text, "category": item.category.value, "must_have": item.must_have}
        for item in output.requirements
    ]
    return report


def _print_report(report: CallReport) -> None:
    print()
    print("=" * 78)
    print(f"{report.label}  [{report.language}]")
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
    usable = [report for report in reports if report.ok]
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
    print()
    print("=" * 78)
    print(f"Stability over {len(usable)} runs of the same input")
    print("-" * 78)
    print(f"  requirement counts: {[len(report.requirements) for report in usable]}")
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
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Only check that the provider is reachable and configured; generate nothing.",
    )
    parser.add_argument("--json", type=Path, help="Also write the full report to this file.")
    parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt. For scripted runs."
    )
    args = parser.parse_args(argv)

    settings = _settings()
    local = settings.llm_provider == "ollama"
    target = settings.provider_model

    print(f"Provider: {settings.llm_provider}   Model: {target}")
    if local:
        print(f"Endpoint: {settings.ollama_base_url}")
    elif settings.llm_provider == "deepseek":
        print(f"Endpoint: {settings.deepseek_base_url}")

    problems = _preflight(settings)
    if problems:
        print("\nNot ready:\n")
        for line in problems:
            print(f"  {line}")
        return 1
    print("Preflight: OK")

    if args.preflight:
        return 0

    if args.text:
        briefs = [("Custom", "as supplied", text) for text in args.text]
    else:
        briefs = [(item.label, item.language, item.text) for item in SAMPLE_CRITERIA]

    calls = len(briefs) + max(0, args.stability - 1)
    cost = "This costs real money." if not local else "This uses this machine's CPU or GPU."
    print(f"\n{calls} call(s) to {target}. {cost}")
    prompt_first = not args.yes and sys.stdin.isatty()
    if prompt_first and input("Continue? [y/N] ").strip().lower() not in {"y", "yes"}:
        print("Nothing was sent.")
        return 1

    client = _build_client(settings)

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

    failed = [report for report in reports if not report.ok]
    print()
    print("=" * 78)
    print(f"{len(reports) - len(failed)}/{len(reports)} briefs produced a usable requirement set.")
    for report in failed:
        print(f"  FAILED: {report.label} — {report.error}")

    if len(failed) == len(reports):
        print(
            "\nNo brief succeeded, so nothing here says anything about the model itself.\n"
            "Read the errors above: a connection or HTTP failure means the request never\n"
            "reached a model, while a validation failure means it did and the reply was\n"
            "unusable — which is a finding about the model."
        )
    else:
        print(
            "\nThis is one run against one model on invented briefs. It shows the provider\n"
            "works and gives a sample of how the model behaves. It is not a benchmark and\n"
            "says nothing about real-world screening accuracy."
        )

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "provider": settings.llm_provider,
                    "model": target,
                    "briefs": [asdict(report) for report in reports],
                    "stability_runs": [asdict(report) for report in repeats],
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

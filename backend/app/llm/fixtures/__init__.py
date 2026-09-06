"""Recorded model replies, for demo mode and the offline test suite.

Each `*.json` file in this directory is one recorded reply. Files are keyed on
load by `(purpose, model, prompt_version, input_sha256, attempt)` — the hash is
**computed here**, from the fixture's own input text, using the same renderer
the runtime uses. Nothing is hand-copied, so a fixture cannot drift out of sync
with the prompt that produced it: change the prompt, and the key changes on
both sides at once.

Fixture format::

    {
      "description":    "what this fixture is for (documentation only)",
      "purpose":        "JD_EXTRACTION",
      "model":          "claude-opus-5",
      "prompt_version": "jd-extraction-v1",
      "attempt":        1,
      "jd_text":        ["line", "line"],      // or a plain string
      "response_json":  { ... }                // OR "response_text": "..."
    }

`jd_text` and `response_text` accept a list of lines, joined with newlines, so
multi-line content stays readable in a diff instead of collapsing into one
escaped string. `response_json` is serialized for you and is the right choice
for a well-formed reply; `response_text` is for deliberately malformed replies,
which by definition cannot be expressed as JSON.

These are **recordings**, not live calls. Demo mode never falls back to the
provider when one is missing — it raises. See `app/llm/client.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.enums import LlmPurpose
from app.core.hashing import sha256_text
from app.llm.client import FixtureKey

FIXTURE_DIR = Path(__file__).resolve().parent


def _join_lines(value: Any, field_name: str, source: Path) -> str:
    if isinstance(value, list):
        return "\n".join(str(line) for line in value)
    if isinstance(value, str):
        return value
    raise ValueError(f"{source.name}: '{field_name}' must be a string or list of strings")


def _render_input(purpose: LlmPurpose, fixture: dict[str, Any], source: Path) -> str:
    """Reproduce the exact user-turn text this fixture was recorded against.

    Dispatch by purpose, using the real renderer, so the fixture key is derived
    the same way the runtime derives it.
    """
    if purpose is LlmPurpose.JD_EXTRACTION:
        from app.llm.prompts.jd_extraction import render_user_content

        return render_user_content(_join_lines(fixture["jd_text"], "jd_text", source))

    # Phases 6 and 8 add PROFILE_EXTRACTION and SEMANTIC_MATCH renderers here.
    raise ValueError(f"{source.name}: no fixture renderer for purpose {purpose.value}")


def load_fixtures(directory: Path | None = None) -> dict[FixtureKey, str]:
    """Load every fixture in `directory` into a lookup keyed for replay."""
    directory = directory or FIXTURE_DIR
    fixtures: dict[FixtureKey, str] = {}

    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))

        purpose = LlmPurpose(raw["purpose"])
        rendered_input = _render_input(purpose, raw, path)

        if "response_json" in raw:
            response_text = json.dumps(raw["response_json"], ensure_ascii=False)
        elif "response_text" in raw:
            response_text = _join_lines(raw["response_text"], "response_text", path)
        else:
            raise ValueError(f"{path.name}: needs either 'response_json' or 'response_text'")

        key = FixtureKey(
            purpose=purpose,
            model=raw["model"],
            prompt_version=raw["prompt_version"],
            input_sha256=sha256_text(rendered_input),
            attempt=int(raw.get("attempt", 1)),
        )
        if key in fixtures:
            raise ValueError(f"{path.name}: duplicate fixture key (same input and attempt)")
        fixtures[key] = response_text

    return fixtures


def fixture_jd_text(name: str, directory: Path | None = None) -> str:
    """Return a fixture's job-description text by file stem.

    Tests use this so the JD they send is byte-identical to the one the
    fixture was recorded against — otherwise the hash differs and replay
    correctly reports a missing fixture.
    """
    directory = directory or FIXTURE_DIR
    path = directory / f"{name}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _join_lines(raw["jd_text"], "jd_text", path)

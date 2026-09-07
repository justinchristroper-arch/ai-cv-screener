"""Turning a Pydantic model into a JSON Schema the provider will accept.

Every `schemas/llm` contract derives its provider-side constraint from its own
Pydantic model rather than hand-writing one, so the schema sent to the provider
and the schema enforced locally cannot drift apart. This module holds the one
implementation of that conversion; it was written for `jd_extraction` in
Phase 4 and lifted here when profile extraction and semantic matching needed
exactly the same treatment.

The structured-output API requires a self-contained schema: no `$ref`, and
`additionalProperties: false` on every object. Pydantic emits `$defs`/`$ref`
for nested models, so both are handled below.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def provider_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema for `model`, inlined and closed at every object level."""
    return inline_refs(model.model_json_schema())


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve local `$ref`s against `$defs` and drop the `$defs` block."""
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref: str = node["$ref"]
                name = ref.rsplit("/", 1)[-1]
                target = resolve(dict(defs[name]))
                # Keep any sibling keys (e.g. `description`) alongside the
                # resolved target rather than discarding them.
                extras = {key: resolve(value) for key, value in node.items() if key != "$ref"}
                return {**target, **extras}
            resolved = {key: resolve(value) for key, value in node.items()}
            if resolved.get("type") == "object" and "additionalProperties" not in resolved:
                resolved["additionalProperties"] = False
            return resolved
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)

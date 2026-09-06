"""Versioned prompt templates.

Each module owns a PROMPT_VERSION that is written to LlmCallLog on every call
and participates in the fixture key, so a prompt change invalidates recorded
fixtures loudly instead of silently replaying stale output.
"""

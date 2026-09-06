"""Shared FastAPI dependencies.

Kept thin on purpose: this module is the only place the HTTP layer learns how
settings, sessions and the LLM client are obtained, so the routes stay free of
wiring.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.llm.client import LlmClient, build_llm_client

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_db)]


def get_llm_client(settings: SettingsDep) -> LlmClient:
    """Resolve the live or replay client from configuration.

    Routes depend on the `LlmClient` protocol, never on a provider type, so a
    test can substitute a stub through FastAPI's dependency override without
    touching the network or the SDK.
    """
    return build_llm_client(settings)


LlmClientDep = Annotated[LlmClient, Depends(get_llm_client)]

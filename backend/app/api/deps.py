"""Shared FastAPI dependencies.

Kept thin on purpose: this module is the only place the HTTP layer learns how
settings and sessions are obtained, so the routes stay free of wiring.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_db)]

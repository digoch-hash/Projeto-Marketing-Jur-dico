import json
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from app.config import Settings
from app.db import init_db, make_session_factory

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str):
    return json.loads(fixture_text(name))


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url="sqlite://", secret_key="test-secret", anthropic_api_key=None,
        anthropic_model="test-model", draft_model="test-draft-model", lookback_days=7, user_agent="test", data_dir=str(tmp_path), scheduler_enabled=False,
    )


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    init_db(engine)
    return make_session_factory(engine)

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str
    anthropic_api_key: str | None
    anthropic_model: str
    draft_model: str
    lookback_days: int
    user_agent: str
    data_dir: str


def load_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///data/app.db"),
        secret_key=os.getenv("SECRET_KEY", "dev-only-change-me"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        draft_model=os.getenv("ANTHROPIC_DRAFT_MODEL", "claude-opus-5-5"),
        lookback_days=int(os.getenv("COLLECT_LOOKBACK_DAYS", "7")),
        data_dir=os.getenv("DATA_DIR", "data"),
        user_agent=os.getenv(
            "COLLECTOR_USER_AGENT",
            "Mozilla/5.0 (compatible; HRBioMonitor/0.1; +monitoramento de normas ambientais)",
        ),
    )

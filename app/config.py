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
    public_base_url: str = ""
    cookie_secure: bool = False
    admin_username: str = ""
    admin_password: str = ""
    publish_hour: int = 9
    scheduler_enabled: bool = True
    instagram_api_version: str = "v23.0"


def load_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///data/app.db"),
        secret_key=os.getenv("SECRET_KEY", "dev-only-change-me"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        draft_model=os.getenv("ANTHROPIC_DRAFT_MODEL", "claude-opus-5-5"),
        lookback_days=int(os.getenv("COLLECT_LOOKBACK_DAYS", "7")),
        data_dir=os.getenv("DATA_DIR", "data"),
        public_base_url=(os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/"),
        cookie_secure=os.getenv("COOKIE_SECURE", "0") == "1",
        admin_username=os.getenv("ADMIN_USERNAME", "").strip(),
        admin_password=os.getenv("ADMIN_PASSWORD", ""),
        publish_hour=max(0, min(23, int(os.getenv("PUBLISH_HOUR", "9")))),
        scheduler_enabled=os.getenv("DISABLE_SCHEDULER", "0") != "1",
        instagram_api_version=os.getenv("INSTAGRAM_API_VERSION", "v23.0"),
        user_agent=os.getenv(
            "COLLECTOR_USER_AGENT",
            "Mozilla/5.0 (compatible; HRBioMonitor/0.1; +monitoramento de normas ambientais)",
        ),
    )

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import load_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    url = url or load_settings().database_url
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine) -> None:
    from app import models  # noqa: F401  (registra as tabelas)

    Base.metadata.create_all(engine)

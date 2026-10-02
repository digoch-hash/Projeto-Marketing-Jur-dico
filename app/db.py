from sqlalchemy import create_engine, inspect, text
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
    add_missing_columns(engine)


def add_missing_columns(engine) -> list[str]:
    """Migracao leve: acrescenta colunas novas (sempre anulaveis) a tabelas que ja existem.

    `create_all` cria tabelas que faltam, mas nao altera as existentes. Nao remove nem altera nada.
    """
    added = []
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                if not col.nullable and col.default is None and col.server_default is None:
                    raise RuntimeError(f"Coluna nova {table.name}.{col.name} precisa ser anulavel ou ter padrao")
                ddl = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}'))
                added.append(f"{table.name}.{col.name}")
    return added

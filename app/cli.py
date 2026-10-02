"""Comandos de linha: python -m app.cli collect | create-user | serve"""
from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.config import load_settings
from app.db import init_db, make_engine, make_session_factory
from app.models import User
from app.security import hash_password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="busca novidades nas fontes oficiais")
    c.add_argument("--days", type=int, default=None, help="quantos dias para tras (padrao: COLLECT_LOOKBACK_DAYS)")

    u = sub.add_parser("create-user", help="cria ou atualiza um usuario")
    u.add_argument("username")
    u.add_argument("--password", help="se omitido, pergunta no terminal")

    args = parser.parse_args(argv)
    settings = load_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    Session = make_session_factory(engine)

    if args.cmd == "collect":
        from app.collector import run_default

        with Session() as session:
            for r in run_default(session, settings, args.days):
                status = f"ERRO: {r.error}" if r.error else "ok"
                print(f"{r.source:8} lidos={r.fetched:4} novos={r.created:3}  {status}")
        return 0

    if args.cmd == "create-user":
        password = args.password or getpass.getpass("Senha: ")
        if len(password) < 8:
            print("A senha precisa ter pelo menos 8 caracteres.", file=sys.stderr)
            return 1
        with Session() as session:
            user = session.scalar(select(User).where(User.username == args.username))
            if user:
                user.password_hash = hash_password(password)
            else:
                session.add(User(username=args.username, password_hash=hash_password(password)))
            session.commit()
        print(f"Usuario '{args.username}' salvo.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

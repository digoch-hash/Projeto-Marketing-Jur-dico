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

    dl = sub.add_parser("daily", help="rotina diaria: normas muito relevantes de ontem (JSON, com o texto completo)")
    dl.add_argument("--date", help="AAAA-MM-DD (padrao: ontem, horario de Brasilia)")
    dl.add_argument("--max", type=int, default=3, help="maximo de candidatos (padrao 3)")

    rc = sub.add_parser("render-card", help="gera artes, legenda e .zip a partir do JSON do card")
    rc.add_argument("--item-id", type=int, required=True)
    rc.add_argument("--content", required=True, help="arquivo JSON com o card (formato DraftContent)")
    rc.add_argument("--out", required=True, help="pasta de saida")

    pp = sub.add_parser("publish-pack", help="publica o carrossel de um card JA APROVADO (usa INSTAGRAM_TOKEN e INSTAGRAM_USER_ID)")
    pp.add_argument("--pack-dir", required=True, help="pasta gerada pelo render-card")
    pp.add_argument("--base-url", required=True, help="endereco publico onde os .jpg estarao (sem o nome do arquivo)")
    pp.add_argument("--jpg-dir", required=True, help="pasta onde gerar os .jpg (publique-os em --base-url)")
    pp.add_argument("--prepare-only", action="store_true", help="so gera os .jpg, sem publicar")
    pp.add_argument("--invite-story", action="store_true", help="publica tambem o story-convite (por padrao so o story do resumo)")

    it = sub.add_parser("ig-token", help="troca o token do Explorador da Graph API pelo token da Pagina (nao vence) para usar no publish-pack")
    it.add_argument("--username", default="", help="@ do Instagram da HRBio (obrigatorio se o token enxerga mais de uma conta)")
    it.add_argument("--token", help="token do Explorador; se omitido, pergunta no terminal (nao fica no historico)")

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

    if args.cmd == "daily":
        import json

        from app.dailyrun import run_daily

        print(json.dumps(run_daily(Session, settings, args.date, max_cards=args.max), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "render-card":
        import json
        from pathlib import Path

        from app.brand_assets import BrandAssets
        from app.cardpack import CardError, build_pack
        from app.models import Item

        with Session() as session:
            item = session.get(Item, args.item_id)
            if not item:
                print(f"Item {args.item_id} não encontrado no banco.", file=sys.stderr)
                return 2
            try:
                result = build_pack(item, Path(args.content).read_text(encoding="utf-8"), args.out, BrandAssets(settings.data_dir))
            except (CardError, OSError) as exc:
                print(str(exc), file=sys.stderr)
                return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "publish-pack":
        import json
        import os
        from pathlib import Path

        from app.instagram import FACEBOOK_HOST, HOST, InstagramClient, InstagramError
        from app.postpack import make_jpegs, publish_pack
        from app.sources.base import make_client

        if args.prepare_only:
            print(json.dumps([str(p) for p in make_jpegs(Path(args.pack_dir), Path(args.jpg_dir))], indent=2))
            return 0
        token, user_id = os.environ.get("INSTAGRAM_TOKEN", ""), os.environ.get("INSTAGRAM_USER_ID", "")
        if not token or not user_id:
            print("Faltam INSTAGRAM_TOKEN e INSTAGRAM_USER_ID nas variáveis do ambiente.", file=sys.stderr)
            return 2
        host = FACEBOOK_HOST if os.environ.get("INSTAGRAM_LOGIN", "").strip().lower() == "facebook" else HOST
        client = InstagramClient(token, user_id, make_client(settings.user_agent, timeout=60.0),
                                 settings.instagram_api_version, host=host)
        try:
            print(json.dumps(publish_pack(client, args.pack_dir, args.base_url, args.jpg_dir, invite_story=args.invite_story), ensure_ascii=False, indent=2))
        except (InstagramError, ValueError) as exc:
            print(f"Não publicou: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.cmd == "ig-token":
        import json

        from app import ig_account
        from app.instagram import InstagramClient, InstagramError
        from app.sources.base import make_client

        if not ig_account.uses_facebook_login(settings):
            print("Defina FACEBOOK_APP_ID e FACEBOOK_APP_SECRET nas variáveis do ambiente.", file=sys.stderr)
            return 2
        raw = args.token or getpass.getpass("Token do Explorador da Graph API (cole com o botão direito do mouse; não aparece nada): ")
        # tira espacos, quebras de linha, aspas e caracteres invisiveis (como o ^V de um Ctrl+V que nao colou)
        token = "".join(c for c in raw if c.isprintable() and not c.isspace()).strip("'\"")
        print(f"Recebi {len(token)} caracteres.", file=sys.stderr)
        if len(token) < 100 or not token.startswith("EA"):
            print("Isso não parece o token inteiro: ele costuma ter mais de 150 caracteres e começar com EAA. "
                  "No Explorador, clique no ícone de copiar ao lado do campo \"Token de acesso\" e cole de novo "
                  "(botão direito do mouse nesta janela).", file=sys.stderr)
            return 2
        http, version = make_client(settings.user_agent, timeout=60.0), settings.instagram_api_version
        try:
            long_token = InstagramClient.facebook_long_lived_token(
                token, settings.facebook_app_id, settings.facebook_app_secret, http, version)
            accounts = InstagramClient.facebook_instagram_accounts(long_token, http, version)
        except InstagramError as exc:
            print(f"Não deu certo: {exc}", file=sys.stderr)
            return 2
        wanted = args.username.strip().lstrip("@").lower()
        if wanted:
            accounts = [a for a in accounts if a["username"].lower() == wanted]
        if len(accounts) != 1:
            names = ", ".join(f"@{a['username']}" for a in accounts) or "nenhuma"
            print(f"Preciso de exatamente uma conta; achei: {names}. Use --username @da_hrbio.", file=sys.stderr)
            return 2
        a = accounts[0]
        # Guarde estes dois valores como segredos do ambiente da rotina. Nao os envie a ninguem.
        print(json.dumps({"INSTAGRAM_USER_ID": a["ig_user_id"], "INSTAGRAM_TOKEN": a["page_token"],
                          "INSTAGRAM_LOGIN": "facebook", "conta": f"@{a['username']}"}, ensure_ascii=False, indent=2))
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

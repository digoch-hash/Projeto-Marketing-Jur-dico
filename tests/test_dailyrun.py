import base64
import json
from dataclasses import replace
from datetime import date

import pytest

from app import cli
from app.brand_assets import BrandAssets
from app.cardpack import CardError, build_pack
from app.collector import RunResult
from app.dailyrun import run_daily, target_date
from app.fulltext import Material
from app.models import Item
from tests.test_drafts import VALID

TODAY = date(2026, 10, 5)  # segunda
YESTERDAY = date(2026, 10, 4)


def add(db, n, relevance, day=YESTERDAY, source="doe_rs", title=None):
    item = Item(source=source, external_id=str(n), title=title or f"Norma {n}", url=f"https://x/{n}", relevance=relevance,
                published_at=day, issuer="FEPAM", doc_type="Portarias", themes="Licenciamento ambiental", relevance_reason="razao")
    db.add(item)
    db.commit()
    return item.id


@pytest.fixture
def cfg(settings):
    return replace(settings, alert_min_relevance=60)


def fake_material(item, http):
    return Material(text=f"Texto completo da {item.title}")


# ---------------------------------------------------------------------- data
def test_data_alvo_padrao_e_ontem_e_aceita_data_explicita():
    assert target_date(None, today=TODAY) == YESTERDAY
    assert target_date("2026-09-25") == date(2026, 9, 25)
    with pytest.raises(ValueError):
        target_date("25/09/2026")


# -------------------------------------------------------------------- candidatos
def test_so_o_dia_alvo_e_so_o_muito_relevante_do_mais_para_o_menos(session_factory, cfg):
    with session_factory() as db:
        a, b = add(db, 1, 70), add(db, 2, 90)
        add(db, 3, 59)  # abaixo do corte: so entra em "talvez"
        add(db, 4, 95, day=date(2026, 10, 3))  # outro dia: ja foi tratado pela rotina de ontem
        add(db, 5, 88, day=TODAY)  # de hoje: sera tratado amanha
    calls = []
    out = run_daily(session_factory, cfg, today=TODAY, collect=lambda db, st, days: calls.append(days) or [], material=fake_material)
    assert out["data_alvo"] == "2026-10-04" and out["lidos_no_dia"] == 3
    assert [c["id"] for c in out["candidatos"]] == [b, a]
    assert out["candidatos"][0]["texto"] == "Texto completo da Norma 2" and out["candidatos"][0]["relevancia"] == 90
    assert [t["titulo"] for t in out["talvez_titulos"]] == ["Norma 3"]
    assert calls == [1]  # coletou desde ontem


def test_dia_sem_nada_relevante_devolve_lista_vazia(session_factory, cfg):
    with session_factory() as db:
        add(db, 1, 30)
    out = run_daily(session_factory, cfg, today=TODAY, collect=lambda db, st, d: [], material=fake_material)
    assert out["candidatos"] == [] and out["talvez_titulos"] == []


def test_limite_de_candidatos(session_factory, cfg):
    with session_factory() as db:
        for n in range(1, 8):
            add(db, n, 60 + n)
    out = run_daily(session_factory, cfg, max_cards=3, today=TODAY, collect=lambda db, st, d: [], material=fake_material)
    assert [c["relevancia"] for c in out["candidatos"]] == [67, 66, 65]


def test_pdf_do_consema_vai_para_arquivo_e_falha_de_texto_nao_derruba(session_factory, cfg):
    with session_factory() as db:
        pdf_item = add(db, 1, 80, source="consema")
        bad = add(db, 2, 70)

    def material(item, http):
        if item.id == pdf_item:
            return Material(pdf_b64=base64.b64encode(b"%PDF-1.4 conteudo").decode())
        raise RuntimeError("fonte fora do ar")

    out = run_daily(session_factory, cfg, today=TODAY, collect=lambda db, st, d: [], material=material)
    first, second = out["candidatos"]
    assert first["texto"] == "" and open(first["pdf"], "rb").read().startswith(b"%PDF")
    assert second["texto"] == "" and "fonte fora do ar" in second["erro_texto"]  # a rotina ve o erro e nao inventa o card


def test_erro_de_fonte_aparece_no_resultado(session_factory, cfg):
    out = run_daily(session_factory, cfg, today=TODAY, material=fake_material,
                    collect=lambda db, st, d: [RunResult("fepam", error="FEPAM fora do ar"), RunResult("consema")])
    assert out["fontes_com_erro"] == [{"fonte": "fepam", "erro": "FEPAM fora do ar"}]
    assert [(f["fonte"], f["lidos"]) for f in out["fontes"]] == [("fepam", 0), ("consema", 0)]  # contagem p/ diagnostico


# ------------------------------------------------------------------ pacote do card
def test_pacote_tem_artes_legenda_pontos_e_zip(session_factory, cfg, tmp_path):
    with session_factory() as db:
        item = db.get(Item, add(db, 1, 90, title="Súmula da Diretriz Técnica FEPAM nº 02/2017"))
        result = build_pack(item, json.dumps(VALID), tmp_path / "pack", BrandAssets(cfg.data_dir))
    assert result["slides"] == 6
    assert sorted(result["files"]) == sorted([f"slide_0{i}.png" for i in range(1, 7)] + ["story.png", "legenda.txt"])
    legenda = (tmp_path / "pack" / "legenda.txt").read_text(encoding="utf-8")
    assert "Fonte oficial: Súmula da Diretriz Técnica FEPAM nº 02/2017" in legenda and "https://x/1" in legenda
    assert "PONTOS PARA CONFERIR" in legenda and "Confirmar a data de vigência" in legenda and "TEXTO DO STATUS" in legenda
    import zipfile

    with zipfile.ZipFile(result["zip"]) as z:
        assert len(z.namelist()) == 8


def test_card_fora_do_formato_da_erro_claro(session_factory, cfg, tmp_path):
    with session_factory() as db:
        item = db.get(Item, add(db, 1, 90))
        with pytest.raises(CardError, match="carousel"):
            build_pack(item, json.dumps({**VALID, "carousel": []}), tmp_path / "x", BrandAssets(cfg.data_dir))
        with pytest.raises(CardError):
            build_pack(item, "isso nao e json", tmp_path / "x", BrandAssets(cfg.data_dir))


# ------------------------------------------------------------------------- CLI
def test_cli_render_card_de_ponta_a_ponta(tmp_path, monkeypatch, capsys):
    db_file, data = tmp_path / "r.db", tmp_path / "data"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setenv("DATA_DIR", str(data))
    from app.db import init_db, make_engine, make_session_factory

    engine = make_engine(f"sqlite:///{db_file}")
    init_db(engine)
    with make_session_factory(engine)() as db:
        item_id = add(db, 1, 90)
    content = tmp_path / "card.json"
    content.write_text(json.dumps(VALID), encoding="utf-8")
    assert cli.main(["render-card", "--item-id", str(item_id), "--content", str(content), "--out", str(tmp_path / "out")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["slides"] == 6 and (tmp_path / "out" / "story.png").exists()
    # erros: item inexistente, JSON ruim, arquivo ausente
    assert cli.main(["render-card", "--item-id", "999", "--content", str(content), "--out", str(tmp_path / "o2")]) == 2
    content.write_text("{}", encoding="utf-8")
    assert cli.main(["render-card", "--item-id", str(item_id), "--content", str(content), "--out", str(tmp_path / "o3")]) == 2
    assert cli.main(["render-card", "--item-id", str(item_id), "--content", str(tmp_path / "nao.json"), "--out", str(tmp_path / "o4")]) == 2

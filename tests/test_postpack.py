from PIL import Image

from app.instagram import Published
from app.postpack import CAPTION_END, publish_pack, read_caption


class FakeClient:
    def __init__(self):
        self.calls = []

    def publish_carousel(self, urls, caption):
        self.calls.append((urls, caption))
        return Published("123", "https://instagram.com/p/abc")

    def publish_story(self, url):
        self.stories = getattr(self, "stories", []) + [url]
        if "convite" in url and getattr(self, "fail_convite", False):
            raise RuntimeError("recusado")
        return Published("9")


def _pack(tmp_path):
    pack = tmp_path / "pack"
    pack.mkdir()
    for n in ("slide_01", "slide_02", "story", "story_convite"):
        Image.new("RGB", (1080, 1350), (20, 60, 30)).save(pack / f"{n}.png")
    (pack / "legenda.txt").write_text(
        f"Legenda do post\n\n{CAPTION_END}\nstatus\n\n--- PONTOS PARA CONFERIR ANTES DE POSTAR ---\n- x\n", encoding="utf-8")
    return pack


def test_legenda_nao_leva_status_nem_pontos_de_conferencia(tmp_path):
    assert read_caption(_pack(tmp_path)) == "Legenda do post"


def test_publica_so_os_slides_em_jpeg_e_na_ordem(tmp_path):
    client = FakeClient()
    out = publish_pack(client, _pack(tmp_path), "https://exemplo.test/x/", tmp_path / "jpg")
    urls, caption = client.calls[0]
    assert urls == ["https://exemplo.test/x/slide_01.jpg", "https://exemplo.test/x/slide_02.jpg"]  # sem o story
    assert caption == "Legenda do post"
    assert out["media_id"] == "123" and out["link"] == "https://instagram.com/p/abc" and out["slides"] == 2
    assert client.stories == ["https://exemplo.test/x/story.jpg", "https://exemplo.test/x/story_convite.jpg"]
    assert out["stories"] == ["story", "story_convite"] and out["stories_com_erro"] == []
    assert Image.open(tmp_path / "jpg" / "slide_01.jpg").format == "JPEG"


def test_falha_num_story_nao_derruba_o_post(tmp_path):
    client = FakeClient()
    client.fail_convite = True
    out = publish_pack(client, _pack(tmp_path), "https://exemplo.test/x", tmp_path / "jpg")
    assert out["media_id"] == "123" and out["stories"] == ["story"]
    assert out["stories_com_erro"][0]["story"] == "story_convite"


def test_sem_stories_quando_desligado(tmp_path):
    client = FakeClient()
    out = publish_pack(client, _pack(tmp_path), "https://exemplo.test/x", tmp_path / "jpg", stories=False)
    assert not hasattr(client, "stories") and out["stories"] == []


# ------------------------------------------------------- CLI (login do Facebook)
def _cli_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'c.db'}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FACEBOOK_APP_ID", "123456")
    monkeypatch.setenv("FACEBOOK_APP_SECRET", "segredo-do-app")


def test_cli_ig_token_devolve_o_token_da_pagina_e_o_id(tmp_path, monkeypatch, capsys):
    import json

    from app import cli
    from tests.fake_instagram import FakeInstagram

    _cli_env(monkeypatch, tmp_path)
    fake = FakeInstagram()
    monkeypatch.setattr("app.sources.base.make_client", lambda *a, **k: fake.client())
    assert cli.main(["ig-token", "--token", "EAAB" + "x" * 150, "--username", "@HRBioAmbiental"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["INSTAGRAM_USER_ID"] == "1784140000" and out["INSTAGRAM_TOKEN"].startswith("PAGINA-TOKEN")
    assert out["INSTAGRAM_LOGIN"] == "facebook" and out["conta"] == "@hrbioambiental"


def test_cli_ig_token_nao_escolhe_sozinho_entre_varias_contas(tmp_path, monkeypatch, capsys):
    from app import cli
    from tests.fake_instagram import FakeInstagram

    _cli_env(monkeypatch, tmp_path)
    fake = FakeInstagram()
    fake.pages.append({"name": "Outra", "access_token": "O" * 25,
                       "instagram_business_account": {"id": "999", "username": "outra.conta"}})
    monkeypatch.setattr("app.sources.base.make_client", lambda *a, **k: fake.client())
    assert cli.main(["ig-token", "--token", "EAAB" + "x" * 150]) == 2
    err = capsys.readouterr().err
    assert "@hrbioambiental" in err and "@outra.conta" in err and "--username" in err


def test_cli_ig_token_exige_o_id_e_a_chave_do_app(tmp_path, monkeypatch, capsys):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'c.db'}")
    monkeypatch.delenv("FACEBOOK_APP_ID", raising=False)
    monkeypatch.delenv("FACEBOOK_APP_SECRET", raising=False)
    assert cli.main(["ig-token", "--token", "x" * 30]) == 2
    assert "FACEBOOK_APP_ID" in capsys.readouterr().err


def test_cli_publish_pack_usa_o_host_do_facebook_quando_pedido(tmp_path, monkeypatch, capsys):
    from app import cli
    from tests.fake_instagram import FakeInstagram

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'c.db'}")
    monkeypatch.setenv("INSTAGRAM_TOKEN", "TOKEN-BOM-" + "x" * 20)
    monkeypatch.setenv("INSTAGRAM_USER_ID", "1784140000")
    monkeypatch.setenv("INSTAGRAM_LOGIN", "facebook")
    fake = FakeInstagram()
    monkeypatch.setattr("app.sources.base.make_client", lambda *a, **k: fake.client())
    monkeypatch.setattr("app.instagram.time.sleep", lambda s: None)
    pack = _pack(tmp_path)
    assert cli.main(["publish-pack", "--pack-dir", str(pack), "--base-url", "https://x/y", "--jpg-dir", str(tmp_path / "jpg")]) == 0
    assert fake.calls and {c["host"] for c in fake.calls} == {"graph.facebook.com"}


def test_cli_ig_token_diz_quantos_caracteres_recebeu_e_recusa_o_que_nao_e_token(tmp_path, monkeypatch, capsys):
    from app import cli

    _cli_env(monkeypatch, tmp_path)
    assert cli.main(["ig-token", "--token", "^V"]) == 2
    err = capsys.readouterr().err
    assert "Recebi 2 caracteres" in err and "não parece o token inteiro" in err
    assert cli.main(["ig-token", "--token", "abc" * 60]) == 2  # comprido, mas nao comeca com EA
    assert "não parece o token inteiro" in capsys.readouterr().err


def test_cli_ig_token_limpa_espacos_e_quebras_de_linha_do_token_colado(tmp_path, monkeypatch, capsys):
    import json

    from app import cli
    from tests.fake_instagram import FakeInstagram

    _cli_env(monkeypatch, tmp_path)
    fake = FakeInstagram()
    monkeypatch.setattr("app.sources.base.make_client", lambda *a, **k: fake.client())
    colado = "  \"EAAB" + "x" * 150 + "\r\n"
    assert cli.main(["ig-token", "--token", colado, "--username", "hrbioambiental"]) == 0
    trocou = next(c for c in fake.calls if c["path"].endswith("/oauth/access_token"))
    assert trocou["params"]["fb_exchange_token"] == "EAAB" + "x" * 150
    assert json.loads(capsys.readouterr().out)["conta"] == "@hrbioambiental"

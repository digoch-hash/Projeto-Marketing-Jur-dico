import httpx
import pytest

from app import publicurls, secrets_box
from app.instagram import (
    CAPTION_MAX, InstagramClient, InstagramError, UncertainPublish, check_caption,
)
from tests.fake_instagram import FakeInstagram

URLS = [f"https://app.exemplo.com/pub/1/9999999999/sig/slide_0{i}.jpg" for i in range(1, 4)]


def make(fake=None, token="TOKEN-BOM-" + "x" * 20, **kw):
    fake = fake or FakeInstagram()
    return fake, InstagramClient(token, "1784140000", fake.client(), "v23.0", sleep=lambda s: None, **kw)


# ----------------------------------------------------------------- carrossel
def test_carrossel_segue_o_fluxo_oficial_na_ordem_certa():
    fake, ig = make()
    result = ig.publish_carousel(URLS, "Legenda do post")
    paths = [(c["method"], c["path"].replace("/v23.0/1784140000", "")) for c in fake.calls]
    # 3 filhos, espera de cada um, container do carrossel, espera, publica, permalink
    assert paths[:3] == [("POST", "/media")] * 3
    assert paths[-2][1] == "/media_publish" and paths[-1][1] == "/v23.0/MEDIA5"  # ultima: busca do link
    children = fake.posts("/media")[:3]
    assert all(c["params"]["is_carousel_item"] == "true" and "caption" not in c["params"] for c in children)
    assert [c["params"]["image_url"] for c in children] == URLS
    carousel = fake.posts("/media")[3]["params"]
    assert carousel["media_type"] == "CAROUSEL" and carousel["caption"] == "Legenda do post"
    assert carousel["children"] == "CONT1,CONT2,CONT3"
    assert fake.posts("/media_publish")[0]["params"]["creation_id"] == "CONT4"  # publica o container do carrossel, nao um filho
    assert result.permalink == "https://www.instagram.com/p/ABC123/" and result.media_id.startswith("MEDIA")
    assert all(c["params"]["access_token"].startswith("TOKEN-BOM") for c in fake.calls)  # token em toda chamada
    assert all(c["path"].startswith("/v23.0/") for c in fake.calls)


def test_espera_o_processamento_antes_de_publicar():
    fake, ig = make()
    fake.status_sequence["CONT1"] = ["IN_PROGRESS", "IN_PROGRESS", "FINISHED"]
    sleeps = []
    ig.sleep = sleeps.append
    ig.publish_carousel(URLS, "x")
    assert len(sleeps) == 2  # esperou duas vezes pelo primeiro filho


@pytest.mark.parametrize("status", ["ERROR", "EXPIRED"])
def test_processamento_com_erro_nao_publica(status):
    fake, ig = make()
    fake.status_sequence["CONT2"] = [status]
    with pytest.raises(InstagramError, match=status):
        ig.publish_carousel(URLS, "x")
    assert fake.posts("/media_publish") == []


def test_demora_demais_desiste_sem_publicar():
    fake, ig = make()
    fake.status_sequence["CONT1"] = ["IN_PROGRESS"] * 500
    with pytest.raises(InstagramError, match="demorou demais"):
        ig.wait_ready("CONT1", timeout=9, interval=3)  # 3 esperas de 3 s


def test_uma_imagem_so_vira_post_simples_com_legenda():
    fake, ig = make()
    ig.publish_carousel(URLS[:1], "Oi")
    media = fake.posts("/media")
    assert len(media) == 1 and media[0]["params"]["caption"] == "Oi" and "media_type" not in media[0]["params"]


def test_limites_de_legenda_e_de_imagens_sao_checados_antes_de_qualquer_chamada():
    fake, ig = make()
    with pytest.raises(InstagramError, match=str(CAPTION_MAX)):
        ig.publish_carousel(URLS, "a" * (CAPTION_MAX + 1))
    with pytest.raises(InstagramError, match="hashtags"):
        ig.publish_carousel(URLS, " ".join(f"#t{i}" for i in range(31)))
    with pytest.raises(InstagramError, match="2 a 10"):
        ig.create_carousel_container(["a"] * 11, "x")
    assert fake.calls == []
    check_caption("ok #um #dois")


def test_story_usa_media_type_stories():
    fake, ig = make()
    ig.publish_story("https://app.exemplo.com/pub/1/9/s/story.jpg")
    assert fake.posts("/media")[0]["params"]["media_type"] == "STORIES"
    assert len(fake.posts("/media_publish")) == 1


# --------------------------------------------------------------------- erros
def test_token_vencido_e_reconhecido_pelo_codigo_190():
    _, ig = make(token="TOKEN-VENCIDO")
    with pytest.raises(InstagramError) as exc:
        ig.me()
    assert exc.value.token_invalid and exc.value.code == 190


def test_erro_antes_do_ultimo_passo_e_seguro_para_tentar_de_novo():
    fake, ig = make()
    fake.fail_on["media"] = httpx.Response(400, json={"error": {"message": "URL da imagem inacessível", "code": 9004}})
    with pytest.raises(InstagramError, match="inacessível") as exc:
        ig.publish_carousel(URLS, "x")
    assert not isinstance(exc.value, UncertainPublish) and fake.posts("/media_publish") == []


def test_queda_de_rede_na_ultima_chamada_e_incerta_nao_falha_simples():
    fake, ig = make()
    fake.fail_on["media_publish"] = httpx.ReadTimeout("sem resposta")
    with pytest.raises(UncertainPublish):
        ig.publish_carousel(URLS, "x")


def test_erro_http_na_ultima_chamada_e_falha_clara_nao_incerta():
    fake, ig = make()
    fake.fail_on["media_publish"] = httpx.Response(400, json={"error": {"message": "Limite diário atingido", "code": 4}})
    with pytest.raises(InstagramError, match="Limite") as exc:
        ig.publish_carousel(URLS, "x")
    assert not isinstance(exc.value, UncertainPublish)


def test_resposta_sem_id_na_publicacao_e_incerta():
    _, ig = make()
    ig._request = lambda *a, **k: {}
    with pytest.raises(UncertainPublish):
        ig.publish("CONT1")


def test_permalink_com_erro_nao_derruba_publicacao_que_deu_certo():
    fake, ig = make()
    ig.permalink = lambda media_id: None
    assert ig.publish_carousel(URLS, "x").permalink is None


# --------------------------------------------------------------------- token
def test_renovar_token_chama_o_endpoint_certo():
    fake = FakeInstagram()
    token, seconds = InstagramClient.refresh_token("ANTIGO-" + "x" * 20, fake.client())
    call = fake.calls[0]
    assert call["path"] == "/refresh_access_token" and call["params"]["grant_type"] == "ig_refresh_token"
    assert token.startswith("NOVO-TOKEN") and seconds == 5184000


def test_renovar_token_invalido_levanta_erro():
    with pytest.raises(InstagramError) as exc:
        InstagramClient.refresh_token("TOKEN-VENCIDO", FakeInstagram().client())
    assert exc.value.token_invalid


# --------------------------------------------------------------- links publicos
def test_link_assinado_funciona_so_para_aquele_arquivo_e_ate_vencer():
    url = publicurls.sign("segredo", "https://app.exemplo.com/", 7, "slide_01.jpg", ttl=100, now=1000)
    assert url.startswith("https://app.exemplo.com/pub/7/1100/") and url.endswith("/slide_01.jpg")
    _, _, _, _, item, exp, sig, name = url.split("/")
    assert publicurls.verify("segredo", 7, int(exp), sig, name, now=1099)
    assert not publicurls.verify("segredo", 7, int(exp), sig, name, now=1101)  # venceu
    assert not publicurls.verify("segredo", 8, int(exp), sig, name, now=1000)  # outro item
    assert not publicurls.verify("segredo", 7, int(exp), sig, "slide_02.jpg", now=1000)  # outro arquivo
    assert not publicurls.verify("outra-chave", 7, int(exp), sig, name, now=1000)
    assert not publicurls.verify("segredo", 7, int(exp) + 5000, sig, name, now=1000)  # prazo adulterado
    with pytest.raises(ValueError):
        publicurls.sign("s", "https://x", 1, "../../etc/passwd")


def test_token_guardado_criptografado_e_so_abre_com_a_mesma_chave():
    sealed = secrets_box.seal("chave-1", "IGAA-token-secreto")
    assert "IGAA" not in sealed and secrets_box.unseal("chave-1", sealed) == "IGAA-token-secreto"
    with pytest.raises(secrets_box.SecretBoxError, match="SECRET_KEY"):
        secrets_box.unseal("chave-2", sealed)

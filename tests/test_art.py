import io
import json
import re
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.art import render
from app.art.render import LIME, POST_SIZE, STORY_SIZE, fit_block, fit_kicker, render_set, spaced_width
from app.brand_assets import AssetError, BrandAssets
from app.models import Draft, Item, User, utcnow
from app.security import hash_password
from app.web.main import create_app
from tests.test_drafts import VALID

LONG = ("Texto muito longo sobre licenciamento ambiental e recursos hídricos " * 40).strip()


def png_bytes(size=(64, 48), color=(10, 120, 30), mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, "PNG")
    return buf.getvalue()


def jpg_bytes(size=(3000, 2000), color=(40, 90, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------- texto na caixa
@pytest.mark.parametrize("text", ["Curto", "Um título médio sobre outorga de uso", LONG])
@pytest.mark.parametrize("weight,max_w,max_h,start,minimum", [("ExtraBold", 800, 300, 80, 52), ("Medium", 880, 420, 42, 28)])
def test_texto_nunca_estoura_a_caixa(text, weight, max_w, max_h, start, minimum):
    b = fit_block(text, weight, max_w, max_h, start, minimum)
    assert b.height <= max_h and b.width <= max_w


def test_texto_gigante_vira_reticencias_e_palavra_gigante_nao_trava():
    assert fit_block(LONG, "Medium", 880, 420, 42, 28).lines[-1].endswith("…")
    b = fit_block("A" * 200, "ExtraBold", 800, 300, 80, 52)  # palavra sem espacos
    assert b.height <= 300


def test_kicker_longo_e_truncado_dentro_da_largura():
    text, fnt, tracking = fit_kicker("SÚMULA DO TERMO DE COOPERAÇÃO MATA ATLÂNTICA - SEMA/FEPAM - MUNICÍPIO DE PICADA CAFÉ", 800)
    assert spaced_width(text, fnt, tracking) <= 800


# ------------------------------------------------------------------- imagens
def slides(n=6):
    return [(f"Título {i}", f"Corpo do slide {i}. " * 5) for i in range(1, n + 1)]


def test_conjunto_tem_carrossel_e_story_nos_tamanhos_certos():
    out = render_set(slides(), "Nova regra", "Status curto", "Resolução CONSEMA 554/2026", None, None)
    assert list(out) == [f"slide_0{i}" for i in range(1, 7)] + ["story"]
    assert all(out[f"slide_0{i}"].size == POST_SIZE for i in range(1, 7)) and out["story"].size == STORY_SIZE


def test_ultima_linha_do_titulo_vai_em_verde_limao():
    def lime_pixels(img):
        px = img.load()
        return sum(1 for x in range(0, img.width, 3) for y in range(420, 900, 3) if px[x, y] == LIME)

    multi = render_set([("Irrigou? Mudou o licenciamento ambiental da sua lavoura", "")] * 3, "h", "s", "n", None, None)["slide_01"]
    single = render_set([("Oi", "")] * 3, "h", "s", "n", None, None)["slide_01"]
    assert lime_pixels(multi) > 500 and lime_pixels(single) < 50  # so destaca quando ha mais de uma linha


def test_com_foto_e_com_logo_e_sem_nada_todos_renderizam():
    photo = Image.new("RGB", (900, 1400), (30, 100, 40))
    logo = Image.new("RGBA", (400, 150), (0, 0, 0, 255))
    for p, lg in [(None, None), (photo, None), (photo, logo)]:
        assert render_set(slides(3), "h", "s", "n", p, lg)["slide_01"].size == POST_SIZE


def test_logo_vai_sobre_plaquinha_clara_para_aparecer_em_fundo_escuro():
    chip = render.logo_chip(Image.new("RGBA", (400, 150), (0, 0, 0, 255)))
    corner = chip.getpixel((6, chip.height // 2))
    assert corner[:3] == render.CREAM and corner[3] > 200


def test_slides_diferentes_geram_imagens_diferentes():
    out = render_set(slides(3), "h", "s", "n", None, None)
    assert out["slide_01"].tobytes() != out["slide_02"].tobytes()


# ------------------------------------------------------------------- arquivos
def test_foto_e_normalizada_deduplicada_e_listada(tmp_path):
    a = BrandAssets(tmp_path)
    name = a.save_photo(jpg_bytes())
    assert a.save_photo(jpg_bytes()) == name and a.list_photos() == [name]  # mesma foto nao duplica
    assert max(Image.open(a.photo_path(name)).size) <= 2400
    assert max(Image.open(a.photo_path(name, thumb=True)).size) <= 480


def test_arquivo_que_nao_e_imagem_ou_e_grande_e_recusado(tmp_path):
    a = BrandAssets(tmp_path)
    with pytest.raises(AssetError, match="imagem válida"):
        a.save_photo(b"<html>nao sou imagem</html>")
    with pytest.raises(AssetError, match="grande demais"):
        a.save_photo(b"x" * (25 * 1024 * 1024 + 1))
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "GIF")
    with pytest.raises(AssetError, match="Formato"):
        a.save_photo(buf.getvalue())
    assert a.list_photos() == []


@pytest.mark.parametrize("bad", ["../../etc/passwd", "..%2f..%2fx.jpg", "abc.jpg", "0123456789ab.png", "0123456789ab.jpg/../x"])
def test_nomes_maliciosos_nao_chegam_ao_disco(tmp_path, bad):
    a = BrandAssets(tmp_path)
    assert a.photo_path(bad) is None and a.delete_photo(bad) is False
    assert a.art_path(1, bad) is None


def test_foto_automatica_e_estavel_e_gira(tmp_path):
    a = BrandAssets(tmp_path)
    assert a.pick_photo(5) is None
    n1, n2 = a.save_photo(jpg_bytes(color=(1, 2, 3))), a.save_photo(jpg_bytes(color=(200, 10, 10)))
    assert a.pick_photo(7) == a.pick_photo(7)
    assert {a.pick_photo(0), a.pick_photo(1)} == {n1, n2}


def test_logos_padrao_vem_no_sistema_e_o_enviado_tem_prioridade(tmp_path):
    a = BrandAssets(tmp_path)
    escuro, claro = a.load_logo("escuro"), a.load_logo("claro")
    assert escuro is not None and claro is not None and not a.is_custom_logo("escuro")

    def opaque_right_half(img):
        w, h = img.size
        px = img.load()
        return [px[x, y][:3] for x in range(w // 2 + 50, w, 9) for y in range(0, h, 9) if px[x, y][3] > 250]

    assert opaque_right_half(escuro) and all(min(c) > 235 for c in opaque_right_half(escuro))  # texto branco
    assert opaque_right_half(claro) and sum(max(c) < 90 for c in opaque_right_half(claro)) > 20  # texto escuro

    a.save_logo(png_bytes((3000, 1000), mode="RGBA", color=(0, 0, 0, 0)), "escuro")
    assert a.is_custom_logo("escuro") and a.load_logo("escuro").width == 1600  # o enviado vale mais
    a.reset_logo("escuro")
    assert not a.is_custom_logo("escuro") and a.load_logo("escuro").size == escuro.size
    with pytest.raises(AssetError, match="inválido"):
        a.save_logo(png_bytes(), "../x")
    assert a.load_logo("xyz") is None and a.logo_file("../x") is None


def test_logo_de_texto_branco_vai_direto_e_o_escuro_usa_plaquinha():
    base = Image.new("RGBA", (800, 400), (20, 40, 20, 255))
    white_logo = Image.new("RGBA", (400, 150), (0, 0, 0, 0))  # margem transparente, como o logo real
    white_logo.paste((255, 255, 255, 255), (200, 40, 380, 110))
    dark_logo = Image.new("RGBA", (400, 150), (0, 0, 0, 0))
    dark_logo.paste((0, 0, 0, 255), (200, 40, 380, 110))
    direct, chip = base.copy(), base.copy()
    render.place_logo(direct, (100, 100), white_logo, dark_logo)
    render.place_logo(chip, (100, 100), None, dark_logo)
    assert direct.getpixel((102, 150)) == (20, 40, 20, 255)  # sem plaquinha: o fundo continua aparecendo
    assert chip.getpixel((102, 150))[:3] != (20, 40, 20)  # com plaquinha creme


def test_artes_e_zip(tmp_path):
    a = BrandAssets(tmp_path)
    imgs = {"slide_01": Image.new("RGB", (10, 10)), "story": Image.new("RGB", (10, 20))}
    assert a.save_art(3, imgs) == ["slide_01.png", "story.png"]
    with zipfile.ZipFile(io.BytesIO(a.art_zip(3))) as z:
        assert sorted(z.namelist()) == ["slide_01.png", "story.png"]
    a.clear_art(3)
    assert a.list_art(3) == []


# ---------------------------------------------------------------------- telas
@pytest.fixture
def web(settings, session_factory):
    with session_factory() as db:
        db.add(User(username="rodrigo", password_hash=hash_password("senha-segura-1")))
        item = Item(source="consema", external_id="554/2026", title="Resolução CONSEMA 554/2026",
                    url="https://sema.rs.gov.br/x.pdf", relevance=80)
        db.add(item)
        db.flush()
        db.add(Draft(item_id=item.id, status="draft", content=json.dumps(VALID), approved_at=utcnow()))
        db.commit()
    c = TestClient(create_app(settings, session_factory))
    c.post("/login", data={"username": "rodrigo", "password": "senha-segura-1"})
    return c


def token(c, path="/marca"):
    return re.search(r'name="csrf" value="([^"]+)"', c.get(path).text).group(1)


def test_marca_exige_login_e_csrf(web, settings, session_factory):
    anon = TestClient(create_app(settings, session_factory))
    assert anon.get("/marca", follow_redirects=False).status_code == 303
    assert anon.get("/marca/fotos/0123456789ab.jpg", follow_redirects=False).status_code == 303
    r = web.post("/marca/fotos", data={"csrf": "falso"}, files=[("fotos", ("a.jpg", jpg_bytes(), "image/jpeg"))])
    assert r.status_code == 403


def test_enviar_logo_e_fotos_e_ver_na_tela(web):
    t = token(web)
    assert "Logo padrão do sistema" in web.get("/marca").text
    page = web.post("/marca/logo", data={"csrf": t, "tipo": "escuro"},
                    files={"logo": ("logo.png", png_bytes((400, 150), mode="RGBA"), "image/png")}).text
    assert "Logo atualizado" in page and "Logo enviado por você" in page and "Voltar ao logo padrão" in page
    assert web.get("/marca/logo/escuro").status_code == 200 and web.get("/marca/logo/claro").status_code == 200
    assert web.get("/marca/logo/xyz").status_code == 404
    assert "Voltou para o logo padrão" in web.post("/marca/logo/escuro/restaurar", data={"csrf": t}).text
    page = web.post("/marca/fotos", data={"csrf": t},
                    files=[("fotos", ("a.jpg", jpg_bytes(), "image/jpeg")), ("fotos", ("b.jpg", jpg_bytes(color=(9, 9, 9)), "image/jpeg"))]).text
    assert "2 foto(s) enviada(s)" in page and "Fotos de campo (2)" in page
    thumb = re.search(r'src="(/marca/fotos/[0-9a-f]{12}\.jpg\?thumb=1)"', page).group(1)
    assert web.get(thumb).headers["content-type"] == "image/jpeg"


def test_upload_invalido_mostra_mensagem_sem_derrubar(web):
    t = token(web)
    page = web.post("/marca/fotos", data={"csrf": t}, files=[("fotos", ("x.jpg", b"nao e imagem", "image/jpeg"))]).text
    assert "0 foto(s) enviada(s)" in page and "imagem válida" in page
    page = web.post("/marca/logo", data={"csrf": t, "tipo": "escuro"}, files={"logo": ("x.png", b"lixo", "image/png")}).text
    assert "imagem válida" in page


def test_gerar_artes_baixar_e_editar_invalida(web):
    t = token(web, "/items/1/draft")
    web.post("/marca/fotos", data={"csrf": t}, files=[("fotos", ("a.jpg", jpg_bytes(), "image/jpeg"))])
    page = web.post("/items/1/draft/art", data={"csrf": t, "photo": "auto"}).text
    assert "7 artes geradas" in page and "/items/1/art/slide_01.png" in page and "/items/1/art/story.png" in page
    img = Image.open(io.BytesIO(web.get("/items/1/art/slide_01.png").content))
    assert img.size == POST_SIZE
    with zipfile.ZipFile(io.BytesIO(web.get("/items/1/art.zip").content)) as z:
        assert len(z.namelist()) == 7
    # editar o texto apaga as artes (ficariam desatualizadas)
    web.post("/items/1/draft/save", data={"csrf": t, "headline": "novo gancho"})
    assert web.get("/items/1/art/slide_01.png").status_code == 404
    assert "Gerar artes" in web.get("/items/1/draft").text


def test_artes_so_servem_nomes_validos_e_com_login(web, settings, session_factory):
    t = token(web, "/items/1/draft")
    web.post("/items/1/draft/art", data={"csrf": t})
    assert web.get("/items/1/art/..%2f..%2fsecret.png").status_code == 404
    assert web.get("/items/1/art/slide_99.png").status_code == 404
    assert web.get("/items/1/art/slide_01.jpg").status_code == 404
    anon = TestClient(create_app(settings, session_factory))
    assert anon.get("/items/1/art/slide_01.png", follow_redirects=False).status_code == 303


def test_foto_escolhida_fica_gravada_e_foto_inexistente_cai_no_fundo_verde(web, session_factory):
    t = token(web, "/items/1/draft")
    web.post("/marca/fotos", data={"csrf": t}, files=[("fotos", ("a.jpg", jpg_bytes(), "image/jpeg"))])
    name = re.search(r'/marca/fotos/([0-9a-f]{12}\.jpg)', web.get("/marca").text).group(1)
    web.post("/items/1/draft/art", data={"csrf": t, "photo": name})
    with session_factory() as db:
        assert db.query(Draft).one().photo == name
    r = web.post("/items/1/draft/art", data={"csrf": t, "photo": "0000000000aa.jpg"})  # inexistente
    assert r.status_code == 200 and "7 artes geradas" in r.text


def test_nao_gera_arte_sem_rascunho(web, session_factory):
    t = token(web, "/items/1/draft")
    with session_factory() as db:
        db.query(Draft).delete()
        db.commit()
    assert web.post("/items/1/draft/art", data={"csrf": t}).status_code == 400


def test_gerar_e_salvar_um_conjunto_com_foto_e_rapido(tmp_path):
    """Regressao: PNG com optimize=True levava ~30 s por conjunto com foto e travava a tela."""
    import time

    a = BrandAssets(tmp_path)
    photo = Image.effect_noise((1400, 1360), 60).convert("RGB")  # ruido: pior caso de compressao
    t = time.time()
    a.save_art(1, render_set(slides(7), "h", "s", "n", photo, None))
    assert time.time() - t < 20


# ----------------------------------------------------- fundo de paisagem (sem fotos)
@pytest.mark.parametrize("seed", [0, 1, 2, 3, 7])
def test_paisagem_nao_tem_faixa_preta_entre_ceu_e_colinas(seed):
    """Regressao: o ceu so ia ate o horizonte e, onde a colina descia, aparecia uma faixa preta."""
    img = render.landscape((540, 675), seed).convert("L")
    dark = sum(img.histogram()[:12])
    assert dark / (img.width * img.height) < 0.001


def test_paisagem_e_estavel_por_seed_e_varia_entre_seeds():
    a1, a2, b = render.landscape((300, 375), 5), render.landscape((300, 375), 5), render.landscape((300, 375), 6)
    assert a1.tobytes() == a2.tobytes() and a1.tobytes() != b.tobytes()


def test_sem_foto_o_fundo_e_a_paisagem_e_o_texto_continua_legivel():
    bg = render.make_background(POST_SIZE, None, seed=2)
    assert bg.size == POST_SIZE
    # area onde fica o texto (esquerda, meio): escura o bastante para o texto creme ter contraste
    lum = bg.crop((100, 330, 700, 800)).convert("L")
    from PIL import ImageStat

    assert ImageStat.Stat(lum).mean[0] < 110

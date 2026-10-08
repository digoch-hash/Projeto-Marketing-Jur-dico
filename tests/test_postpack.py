from PIL import Image

from app.instagram import Published
from app.postpack import CAPTION_END, publish_pack, read_caption


class FakeClient:
    def __init__(self):
        self.calls = []

    def publish_carousel(self, urls, caption):
        self.calls.append((urls, caption))
        return Published("123", "https://instagram.com/p/abc")


def _pack(tmp_path):
    pack = tmp_path / "pack"
    pack.mkdir()
    for n in ("slide_01", "slide_02", "story"):
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
    assert out == {"media_id": "123", "link": "https://instagram.com/p/abc", "slides": 2}
    assert Image.open(tmp_path / "jpg" / "slide_01.jpg").format == "JPEG"

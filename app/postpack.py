"""Publica no Instagram o pacote de um card ja aprovado (pasta gerada por `render-card`).

So roda por pedido explicito do dono (depois do OK dele). As imagens precisam estar em enderecos publicos,
porque o Instagram baixa cada uma pelo link.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

CAPTION_END = "--- TEXTO DO STATUS DO WHATSAPP ---"


STORY_NAMES = ("story", "story_convite")  # nesta ordem: resumo e depois o convite para o post


def slide_names(pack_dir: Path) -> list[str]:
    return sorted(p.stem for p in pack_dir.glob("slide_*.png"))


def story_names(pack_dir: Path) -> list[str]:
    return [n for n in STORY_NAMES if (pack_dir / f"{n}.png").exists()]


def read_caption(pack_dir: Path) -> str:
    text = (pack_dir / "legenda.txt").read_text(encoding="utf-8")
    return text.split(CAPTION_END)[0].strip()  # a parte do WhatsApp e os pontos de conferencia nunca vao ao post


def make_jpegs(pack_dir: Path, out_dir: Path) -> list[Path]:
    """O Instagram so aceita JPEG. Devolve os arquivos na ordem dos slides."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in slide_names(pack_dir) + story_names(pack_dir):
        dest = out_dir / f"{name}.jpg"
        Image.open(pack_dir / f"{name}.png").convert("RGB").save(dest, "JPEG", quality=92)
        paths.append(dest)
    return paths


def publish_pack(client, pack_dir: str | Path, base_url: str, jpg_dir: str | Path, *, stories: bool = True,
                 invite_story: bool = False) -> dict:
    """Publica o carrossel e, em seguida, o story da chamada resumida (e, so se `invite_story`, o story-convite).

    Falha num story nao desfaz o post (ja esta no ar).
    """
    pack = Path(pack_dir)
    make_jpegs(pack, Path(jpg_dir))
    slides = slide_names(pack)
    if not slides:
        raise ValueError("A pasta não tem slides (slide_01.png...).")
    base = base_url.rstrip("/")
    result = client.publish_carousel([f"{base}/{n}.jpg" for n in slides], read_caption(pack))
    out = {"media_id": result.media_id, "link": result.permalink, "slides": len(slides), "stories": [], "stories_com_erro": []}
    if stories:
        for name in story_names(pack):
            if name == "story_convite" and not invite_story:  # o convite repete o resumo: so sai se pedido
                continue
            try:
                client.publish_story(f"{base}/{name}.jpg")
                out["stories"].append(name)
            except Exception as exc:  # noqa: BLE001 - o post principal ja saiu; so avisa
                out["stories_com_erro"].append({"story": name, "erro": str(exc)})
    return out

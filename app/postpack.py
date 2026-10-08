"""Publica no Instagram o pacote de um card ja aprovado (pasta gerada por `render-card`).

So roda por pedido explicito do dono (depois do OK dele). As imagens precisam estar em enderecos publicos,
porque o Instagram baixa cada uma pelo link.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

CAPTION_END = "--- TEXTO DO STATUS DO WHATSAPP ---"


def slide_names(pack_dir: Path) -> list[str]:
    return sorted(p.stem for p in pack_dir.glob("slide_*.png"))


def read_caption(pack_dir: Path) -> str:
    text = (pack_dir / "legenda.txt").read_text(encoding="utf-8")
    return text.split(CAPTION_END)[0].strip()  # a parte do WhatsApp e os pontos de conferencia nunca vao ao post


def make_jpegs(pack_dir: Path, out_dir: Path) -> list[Path]:
    """O Instagram so aceita JPEG. Devolve os arquivos na ordem dos slides."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in slide_names(pack_dir):
        dest = out_dir / f"{name}.jpg"
        Image.open(pack_dir / f"{name}.png").convert("RGB").save(dest, "JPEG", quality=92)
        paths.append(dest)
    return paths


def publish_pack(client, pack_dir: str | Path, base_url: str, jpg_dir: str | Path) -> dict:
    pack = Path(pack_dir)
    jpgs = make_jpegs(pack, Path(jpg_dir))
    if not jpgs:
        raise ValueError("A pasta não tem slides (slide_01.png...).")
    urls = [f"{base_url.rstrip('/')}/{p.name}" for p in jpgs]
    result = client.publish_carousel(urls, read_caption(pack))
    return {"media_id": result.media_id, "link": result.permalink, "slides": len(urls)}

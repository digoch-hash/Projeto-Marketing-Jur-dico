"""Do card escrito (JSON) para o pacote de postagem: artes, legenda e .zip."""
from __future__ import annotations

import zipfile
from pathlib import Path

from pydantic import ValidationError

from app.art.render import render_set
from app.brand_assets import BrandAssets
from app.drafts import DraftContent, build_full_caption
from app.models import Item


class CardError(ValueError):
    pass


def build_pack(item: Item, content_json: str, out_dir: str | Path, assets: BrandAssets) -> dict:
    try:
        content = DraftContent.model_validate_json(content_json)
    except ValidationError as exc:
        fields = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:6])
        raise CardError(f"O JSON do card não segue o formato esperado ({fields}).") from exc

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    images = render_set(
        slides=[(s.title, s.body) for s in content.carousel],
        headline=content.headline,
        status_text=content.whatsapp_status,
        norm_label=item.title,
        photo=assets.load_photo(assets.pick_photo(item.id)),
        logo_dark_bg=assets.load_logo("escuro"),
        logo_light_bg=assets.load_logo("claro"),
        seed=item.id,
        invite_story=True,
    )
    files = []
    for name, img in images.items():
        img.save(out / f"{name}.png", "PNG", compress_level=6)
        files.append(f"{name}.png")
    notes = "\n".join(f"- {n}" for n in content.review_notes) or "- (nenhum)"
    (out / "legenda.txt").write_text(
        build_full_caption(item, content)
        + "\n\n--- TEXTO DO STATUS DO WHATSAPP ---\n" + content.whatsapp_status
        + "\n\n--- PONTOS PARA CONFERIR ANTES DE POSTAR ---\n" + notes + "\n",
        encoding="utf-8",
    )
    files.append("legenda.txt")
    zip_path = out.parent / f"card-{item.id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
        for f in files:
            z.write(out / f, f)
    return {"dir": str(out), "zip": str(zip_path), "files": files, "slides": len(content.carousel),
            "pontos_para_conferir": content.review_notes}

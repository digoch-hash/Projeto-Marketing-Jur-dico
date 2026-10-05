"""Gera (e guarda) as artes de um rascunho. Usado pela tela e pelo publicador automatico."""
from __future__ import annotations

from app.art.render import render_set
from app.brand_assets import BrandAssets
from app.drafts import DraftContent
from app.models import Draft, Item


def generate_art(assets: BrandAssets, item: Item, draft: Draft, content: DraftContent, photo: str = "auto") -> list[str]:
    """`photo`: "auto" (gira entre as fotos) ou o nome de uma foto enviada. Atualiza `draft.photo`."""
    chosen = assets.pick_photo(item.id) if photo == "auto" else (photo if assets.photo_path(photo) else None)
    draft.photo = None if photo == "auto" else chosen
    images = render_set(
        slides=[(sl.title, sl.body) for sl in content.carousel],
        headline=content.headline,
        status_text=content.whatsapp_status,
        norm_label=item.title,
        photo=assets.load_photo(chosen),
        logo_dark_bg=assets.load_logo("escuro"),
        logo_light_bg=assets.load_logo("claro"),
        seed=item.id,
    )
    return assets.save_art(item.id, images)


def art_is_complete(assets: BrandAssets, item_id: int, slides: int) -> bool:
    """Todas as imagens (PNG e JPEG) existem? Artes de versoes antigas nao tem o JPEG do Instagram."""
    names = [f"slide_{i:02d}" for i in range(1, slides + 1)] + ["story"]
    return all(assets.art_path(item_id, f"{n}.png") and assets.art_jpg_path(item_id, f"{n}.jpg") for n in names)

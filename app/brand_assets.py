"""Logo, fotos de campo e artes geradas, guardados em DATA_DIR (fora do banco)."""
from __future__ import annotations

import hashlib
import io
import re
import shutil
import zipfile
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

PHOTO_NAME = re.compile(r"^[0-9a-f]{12}\.jpg$")
ART_NAME = re.compile(r"^(slide_\d{2}|story)\.png$")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PHOTO_MAX_SIDE = 2400
THUMB_SIDE = 480
LOGO_MAX_W = 1600

Image.MAX_IMAGE_PIXELS = 80_000_000  # recusa imagens gigantes (protecao contra decompression bomb)


class AssetError(ValueError):
    """Arquivo enviado nao serve; a mensagem e mostrada ao usuario."""


def _open_image(raw: bytes) -> Image.Image:
    if len(raw) > MAX_UPLOAD_BYTES:
        raise AssetError("Arquivo grande demais (limite de 25 MB).")
    try:
        Image.open(io.BytesIO(raw)).verify()
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise AssetError("Isso não parece uma imagem válida (use JPG, PNG ou WEBP).") from exc
    if img.format not in ("JPEG", "PNG", "WEBP"):
        raise AssetError("Formato não aceito. Use JPG, PNG ou WEBP.")
    return img


class BrandAssets:
    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir)
        self.photos = self.root / "photos"
        self.thumbs = self.photos / "thumbs"
        self.logo_path = self.root / "brand" / "logo.png"
        self.art_root = self.root / "art"

    # ----------------------------------------------------------------- fotos
    def list_photos(self) -> list[str]:
        if not self.photos.exists():
            return []
        return sorted(p.name for p in self.photos.iterdir() if PHOTO_NAME.match(p.name))

    def save_photo(self, raw: bytes) -> str:
        img = ImageOps.exif_transpose(_open_image(raw)).convert("RGB")
        img.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88, optimize=True)
        name = hashlib.sha1(buf.getvalue()).hexdigest()[:12] + ".jpg"  # a mesma foto nao duplica
        self.thumbs.mkdir(parents=True, exist_ok=True)
        (self.photos / name).write_bytes(buf.getvalue())
        thumb = img.copy()
        thumb.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.LANCZOS)
        thumb.save(self.thumbs / name, "JPEG", quality=80)
        return name

    def photo_path(self, name: str, thumb: bool = False) -> Path | None:
        if not PHOTO_NAME.match(name):
            return None
        path = (self.thumbs if thumb else self.photos) / name
        return path if path.is_file() else None

    def delete_photo(self, name: str) -> bool:
        main, thumb = self.photo_path(name), self.photo_path(name, thumb=True)
        for p in (main, thumb):
            if p:
                p.unlink()
        return bool(main)

    def load_photo(self, name: str | None) -> Image.Image | None:
        path = self.photo_path(name) if name else None
        return Image.open(path).convert("RGB") if path else None

    def pick_photo(self, seed: int) -> str | None:
        """Escolha automatica e estavel: o mesmo item sempre recebe a mesma foto (gire pelas fotos)."""
        photos = self.list_photos()
        return photos[seed % len(photos)] if photos else None

    # ------------------------------------------------------------------ logo
    def save_logo(self, raw: bytes) -> None:
        img = _open_image(raw).convert("RGBA")
        if img.width > LOGO_MAX_W:
            img = img.resize((LOGO_MAX_W, int(img.height * LOGO_MAX_W / img.width)), Image.LANCZOS)
        self.logo_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(self.logo_path, "PNG")

    def load_logo(self) -> Image.Image | None:
        return Image.open(self.logo_path).convert("RGBA") if self.logo_path.is_file() else None

    # ------------------------------------------------------------------ artes
    def art_dir(self, item_id: int) -> Path:
        return self.art_root / str(int(item_id))

    def clear_art(self, item_id: int) -> None:
        shutil.rmtree(self.art_dir(item_id), ignore_errors=True)

    def save_art(self, item_id: int, images: dict[str, Image.Image]) -> list[str]:
        self.clear_art(item_id)
        folder = self.art_dir(item_id)
        folder.mkdir(parents=True, exist_ok=True)
        for name, img in images.items():
            img.save(folder / f"{name}.png", "PNG", compress_level=6)  # optimize=True leva ~30 s por conjunto com foto
        return self.list_art(item_id)

    def list_art(self, item_id: int) -> list[str]:
        folder = self.art_dir(item_id)
        return sorted(p.name for p in folder.iterdir() if ART_NAME.match(p.name)) if folder.exists() else []

    def art_path(self, item_id: int, name: str) -> Path | None:
        if not ART_NAME.match(name):
            return None
        path = self.art_dir(item_id) / name
        return path if path.is_file() else None

    def art_zip(self, item_id: int) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:  # PNG ja e comprimido
            for name in self.list_art(item_id):
                z.write(self.art_dir(item_id) / name, name)
        return buf.getvalue()

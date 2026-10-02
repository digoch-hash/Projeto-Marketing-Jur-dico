"""Artes no estilo da HRBio: carrossel (1080x1350) e story/Status do WhatsApp (1080x1920).

Identidade medida nos stories da marca: foto de natureza escurecida, titulo grande em Montserrat
extranegrito com a ultima linha em verde-limao, traco fino vertical, paineis verde-escuros arredondados.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

FONT_DIR = Path(__file__).parent / "fonts"

POST_SIZE = (1080, 1350)
STORY_SIZE = (1080, 1920)

INK = (20, 32, 12)
GREEN_DEEP = (28, 49, 20)
LIME = (156, 204, 84)
OLIVE = (110, 130, 34)
CREAM = (247, 244, 230)
LINE = (164, 169, 125)

MARGIN_X = 104


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / f"Montserrat-{weight}.ttf"), size)


# ------------------------------------------------------------------------- texto
def wrap(text: str, fnt: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split():
            trial = f"{current} {word}".strip()
            if fnt.getlength(trial) <= max_w or not current:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return [ln for ln in lines if ln != ""] or [""]


@dataclass
class Block:
    lines: list[str]
    fnt: ImageFont.FreeTypeFont
    line_h: int

    @property
    def height(self) -> int:
        return self.line_h * len(self.lines)

    @property
    def width(self) -> int:
        return int(max((self.fnt.getlength(ln) for ln in self.lines), default=0))


def fit_block(text: str, weight: str, max_w: int, max_h: int, start: int, minimum: int, spacing: float = 1.2) -> Block:
    """Maior tamanho de fonte (entre `start` e `minimum`) em que o texto cabe na caixa.
    Se nem no minimo couber, corta no fim da frase e poe reticencias: nunca estoura a caixa."""
    size = start
    while True:
        fnt = font(weight, size)
        lines = wrap(text, fnt, max_w)
        line_h = int(size * spacing)
        if len(lines) * line_h <= max_h:
            return Block(lines, fnt, line_h)
        if size <= minimum:
            break
        size -= 2
    fnt = font(weight, minimum)
    line_h = int(minimum * spacing)
    max_lines = max(1, max_h // line_h)
    lines = wrap(text, fnt, max_w)[:max_lines]
    last = lines[-1].rstrip(" ,;:.")
    while last and fnt.getlength(last + "…") > max_w:
        last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
    lines[-1] = last + "…"
    return Block(lines, fnt, line_h)


def spaced_width(text: str, fnt: ImageFont.FreeTypeFont, tracking: int) -> float:
    return sum(fnt.getlength(ch) for ch in text) + tracking * max(0, len(text) - 1)


def draw_spaced(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fnt, fill, tracking: int) -> None:
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += fnt.getlength(ch) + tracking


def fit_kicker(text: str, max_w: int, size: int = 30, tracking: int = 5) -> tuple[str, ImageFont.FreeTypeFont, int]:
    text = text.upper()
    while size > 20 and spaced_width(text, font("SemiBold", size), tracking) > max_w:
        size -= 1
    fnt = font("SemiBold", size)
    while text and spaced_width(text, fnt, tracking) > max_w:
        text = text[:-2].rstrip() + "…"
    return text, fnt, tracking


# ---------------------------------------------------------------------- fundo
def _vertical_gradient(size: tuple[int, int], top: int, bottom: int) -> Image.Image:
    """Mascara 'L' que vai de `top` a `bottom` (0-255) de cima para baixo."""
    grad = Image.linear_gradient("L").resize(size)  # 0 em cima -> 255 embaixo
    return grad.point(lambda v: int(top + (bottom - top) * v / 255))


def make_background(size: tuple[int, int], photo: Image.Image | None, seed: int = 0) -> Image.Image:
    if photo is not None:
        bg = ImageOps.fit(photo.convert("RGB"), size, Image.LANCZOS, centering=(0.5, 0.45))
        tint = Image.new("RGB", size, GREEN_DEEP)
        bg = Image.blend(bg, tint, 0.28)
    else:
        bg = Image.new("RGB", size, GREEN_DEEP)
        rnd = random.Random(seed)
        blobs = Image.new("RGB", size, GREEN_DEEP)
        d = ImageDraw.Draw(blobs)
        for _ in range(7):
            r = rnd.randint(size[0] // 5, size[0] // 2)
            cx, cy = rnd.randint(0, size[0]), rnd.randint(0, size[1])
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=rnd.choice([(40, 68, 28), (52, 82, 30), (34, 58, 24)]))
        bg = Image.blend(blobs.filter(ImageFilter.GaussianBlur(120)), bg, 0.2)
    shade = Image.new("RGB", size, INK)
    # escurece mais embaixo, onde ficam o texto e o rodape
    return Image.composite(shade, bg, _vertical_gradient(size, 110, 225))


# ----------------------------------------------------------------------- logo
def logo_chip(logo: Image.Image | None, height: int = 96) -> Image.Image:
    """Logo sobre uma plaquinha creme: o logo original tem texto escuro e precisa de fundo claro."""
    pad = 22
    if logo is not None:
        mark = logo.convert("RGBA")
        w = int(mark.width * (height / mark.height))
        mark = mark.resize((w, height), Image.LANCZOS)
    else:  # sem arquivo de logo: nome em texto
        f1, f2 = font("ExtraBold", 40), font("Medium", 26)
        w = int(max(f1.getlength("HRBio"), f2.getlength("Ambiental")))
        mark = Image.new("RGBA", (w, height), (0, 0, 0, 0))
        d = ImageDraw.Draw(mark)
        d.text((0, 6), "HRBio", font=f1, fill=GREEN_DEEP)
        d.text((0, 54), "Ambiental", font=f2, fill=OLIVE)
    chip = Image.new("RGBA", (mark.width + pad * 2, mark.height + pad), (0, 0, 0, 0))
    ImageDraw.Draw(chip).rounded_rectangle((0, 0, chip.width - 1, chip.height - 1), radius=26, fill=CREAM + (238,))
    chip.alpha_composite(mark, (pad, pad // 2))
    return chip


# ------------------------------------------------------------------ composicao
def _rule(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, color=LIME) -> None:
    draw.rectangle((x, y, x + w, y + 5), fill=color)


def _draw_title(draw: ImageDraw.ImageDraw, block: Block, x: int, y: int) -> int:
    """Titulo em creme com a ultima linha em verde-limao (como nos stories da marca)."""
    for i, line in enumerate(block.lines):
        last = i == len(block.lines) - 1 and len(block.lines) > 1
        draw.text((x, y), line, font=block.fnt, fill=LIME if last else CREAM)
        y += block.line_h
    return y


def render_post_slide(
    title: str,
    body: str,
    kicker: str,
    index: int,
    total: int,
    background: Image.Image,
    logo: Image.Image | None,
) -> Image.Image:
    W, H = POST_SIZE
    img = background.copy().convert("RGBA")
    d = ImageDraw.Draw(img)
    text_w = W - MARGIN_X - 90
    cover = index == 0

    d.rectangle((58, 96, 62, H - 96), fill=LINE + (200,))  # traco vertical da marca
    img.alpha_composite(logo_chip(logo), (MARGIN_X, 76))

    top = 330
    k_text, k_font, tracking = fit_kicker(kicker, text_w)
    draw_spaced(d, (MARGIN_X, top), k_text, k_font, LIME, tracking)
    _rule(d, MARGIN_X, top + 56, 120)

    title_block = fit_block(title.upper() if cover else title, "ExtraBold", text_w, 380 if cover else 300,
                            start=104 if cover else 80, minimum=52, spacing=1.12)
    y = _draw_title(d, title_block, MARGIN_X, top + 96)
    if body.strip():
        body_block = fit_block(body, "Medium", text_w, H - 190 - (y + 40), start=42, minimum=28, spacing=1.38)
        y += 40
        for line in body_block.lines:
            d.text((MARGIN_X, y), line, font=body_block.fnt, fill=CREAM)
            y += body_block.line_h

    d.text((MARGIN_X, H - 120), f"{index + 1:02d} / {total:02d}", font=font("SemiBold", 28), fill=CREAM)
    swipe = "arraste  →" if index < total - 1 else "HRBio Ambiental"
    sw = font("Medium", 28)
    d.text((W - 90 - sw.getlength(swipe), H - 120), swipe, font=sw, fill=LIME)
    return img.convert("RGB")


def render_story(
    headline: str, text: str, kicker: str, footer: str, background: Image.Image, logo: Image.Image | None
) -> Image.Image:
    W, H = STORY_SIZE
    img = background.copy().convert("RGBA")
    d = ImageDraw.Draw(img)
    text_w = W - MARGIN_X - 90

    d.rectangle((58, 120, 62, H - 480), fill=LINE + (200,))
    img.alpha_composite(logo_chip(logo, 110), (MARGIN_X, 110))

    top = 520
    k_text, k_font, tracking = fit_kicker(kicker, text_w, size=32)
    draw_spaced(d, (MARGIN_X, top), k_text, k_font, LIME, tracking)
    _rule(d, MARGIN_X, top + 60, 130)
    block = fit_block(headline.upper(), "ExtraBold", text_w, 520, start=112, minimum=56, spacing=1.12)
    y = _draw_title(d, block, MARGIN_X, top + 104)
    body = fit_block(text, "Medium", text_w, H - 520 - (y + 40), start=48, minimum=30, spacing=1.38)
    y += 44
    for line in body.lines:
        d.text((MARGIN_X, y), line, font=body.fnt, fill=CREAM)
        y += body.line_h

    # painel verde-escuro arredondado no canto, como nos stories da marca
    panel = Image.new("RGBA", (W, 330), (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle((-80, 0, 760, 420), radius=90, fill=GREEN_DEEP + (235,))
    img.alpha_composite(panel, (0, H - 330))
    d = ImageDraw.Draw(img)
    cta = fit_block(footer, "SemiBold", 560, 150, start=40, minimum=28, spacing=1.3)
    cy = H - 250
    for i, line in enumerate(cta.lines):
        d.text((MARGIN_X - 40, cy), line, font=cta.fnt, fill=CREAM if i == 0 else LIME)
        cy += cta.line_h
    return img.convert("RGB")


def render_set(
    slides: list[tuple[str, str]],
    headline: str,
    status_text: str,
    norm_label: str,
    photo: Image.Image | None,
    logo: Image.Image | None,
    seed: int = 0,
) -> dict[str, Image.Image]:
    """Todas as artes de um rascunho: slide_01.. (carrossel) e story."""
    out: dict[str, Image.Image] = {}
    post_bg = make_background(POST_SIZE, photo, seed)
    for i, (title, body) in enumerate(slides):
        kicker = "Novidade na legislação ambiental" if i == 0 else norm_label
        out[f"slide_{i + 1:02d}"] = render_post_slide(title, body, kicker, i, len(slides), post_bg, logo)
    story_bg = make_background(STORY_SIZE, photo, seed + 1)
    out["story"] = render_story(headline, status_text, "Novidade na legislação ambiental",
                                f"Fale com a HRBio\n{norm_label}", story_bg, logo)
    return out

"""Artes no estilo da HRBio: carrossel (1080x1350) e story/Status do WhatsApp (1080x1920).

Identidade medida nos stories da marca: foto de natureza escurecida, titulo grande em Montserrat
extranegrito com a ultima linha em verde-limao, traco fino vertical, paineis verde-escuros arredondados.
"""
from __future__ import annotations

import math
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


def _horizontal_gradient(size: tuple[int, int], left: int, right: int) -> Image.Image:
    grad = Image.linear_gradient("L").rotate(90, expand=True).transpose(Image.FLIP_LEFT_RIGHT).resize(size)
    return grad.point(lambda v: int(left + (right - left) * v / 255))


# Paisagens geradas (sem fotos): cada paleta e uma hora do dia / clima, todas no verde da marca.
PALETTES = [
    dict(top=(22, 50, 34), horizon=(208, 222, 168), sun=(255, 244, 190),
         ridges=[(150, 176, 128), (112, 148, 98), (78, 114, 72), (46, 82, 48), (26, 54, 32)]),      # manha com neblina
    dict(top=(28, 38, 28), horizon=(230, 198, 138), sun=(255, 212, 150),
         ridges=[(168, 158, 108), (124, 130, 84), (86, 102, 58), (54, 74, 40), (30, 48, 26)]),      # fim de tarde
    dict(top=(14, 40, 44), horizon=(168, 206, 190), sun=(232, 248, 220),
         ridges=[(120, 164, 150), (86, 132, 114), (56, 100, 82), (36, 72, 56), (20, 46, 36)]),      # vale azulado
    dict(top=(10, 30, 20), horizon=(150, 184, 120), sun=(220, 240, 170),
         ridges=[(98, 138, 86), (68, 108, 62), (46, 82, 44), (30, 58, 32), (18, 38, 22)]),          # floresta fechada
]


def _blend(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))  # type: ignore[return-value]


def landscape(size: tuple[int, int], seed: int = 0) -> Image.Image:
    """Paisagem verde em camadas (colinas, neblina, sol e, as vezes, um rio). Determinista por `seed`."""
    W, H = size
    rnd = random.Random(seed)
    pal = PALETTES[seed % len(PALETTES)]
    horizon_y = H * 0.60

    # ceu: escuro em cima, claro perto do horizonte
    # (o gradiente cobre a imagem toda: abaixo do horizonte fica na cor do horizonte, sem faixa vazia entre colinas)
    ramp = Image.linear_gradient("L").resize((W, int(horizon_y)))
    mask = Image.new("L", size, 255)
    mask.paste(ramp.point(lambda v: int(255 * (v / 255) ** 1.7)), (0, 0))
    img = Image.composite(Image.new("RGB", size, pal["horizon"]), Image.new("RGB", size, pal["top"]), mask)

    # brilho do sol (calculado em baixa resolucao: o desfoque grande fica barato)
    small = (W // 4, H // 4)
    glow = Image.new("RGBA", small, (0, 0, 0, 0))
    sx = int(small[0] * rnd.uniform(0.3, 0.8))
    r = small[0] // 3
    ImageDraw.Draw(glow).ellipse((sx - r, int(horizon_y / 4) - r, sx + r, int(horizon_y / 4) + r), fill=pal["sun"] + (150,))
    glow = glow.filter(ImageFilter.GaussianBlur(small[0] // 7)).resize(size, Image.BICUBIC)
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")

    layers = len(pal["ridges"])
    for i, color in enumerate(pal["ridges"]):
        base = horizon_y + H * 0.065 * i
        amp = H * (0.018 + 0.012 * i)
        waves = [(rnd.uniform(0.6, 1.2) * (k + 1), rnd.uniform(0, 6.28), amp / (k + 1) ** 0.8) for k in range(4)]
        pts = []
        for x in range(0, W + 8, 8):
            y = base + sum(a * math.sin(6.2832 * f * x / W + ph) for f, ph, a in waves)
            pts.append((x, y))
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.polygon(pts + [(W, H), (0, H)], fill=color + (255,))
        if i >= 2:  # copas de arvores no contorno das colinas proximas (pequenas e suaves, para nao virarem "bolhas")
            lighter, darker = _blend(color, (255, 255, 255), 0.07), _blend(color, (0, 0, 0), 0.16)
            trees = Image.new("RGBA", size, (0, 0, 0, 0))
            td = ImageDraw.Draw(trees)
            for _ in range(700 + 250 * i):
                px = rnd.randint(0, W)
                py = base + sum(a * math.sin(6.2832 * f * px / W + ph) for f, ph, a in waves) + rnd.randint(-3, 60)
                rr = rnd.randint(3 + i, 6 + 2 * i)
                td.ellipse((px - rr, py - rr, px + rr, py + rr), fill=rnd.choice([lighter, darker, color]) + (255,))
            layer = Image.alpha_composite(layer, trees.filter(ImageFilter.GaussianBlur(1.6)))
        if i < 2:
            layer = layer.filter(ImageFilter.GaussianBlur(2.5 - i))  # montanhas distantes mais suaves
        img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
        if i < layers - 1:  # neblina entre as camadas
            band = int(H * 0.08)
            fog = Image.new("RGB", (W, band), pal["horizon"])
            mask = _vertical_gradient((W, band), 0, 0).point(lambda v: 0)
            ramp = Image.linear_gradient("L").resize((W, band))
            mask = ramp.point(lambda v: int(70 * (1 - abs(v - 128) / 128) ** 1.2 * (1 - i / layers)))
            img.paste(fog, (0, int(base + H * 0.01)), mask)

    if seed % 2 == 0:  # rio sinuoso em primeiro plano
        river = Image.new("RGBA", size, (0, 0, 0, 0))
        rd = ImageDraw.Draw(river)
        phase, steps = rnd.uniform(0, 6.28), 60
        prev = None
        for k in range(steps + 1):
            t = k / steps
            y = horizon_y + H * 0.10 + (H - horizon_y - H * 0.10) * t ** 1.35
            x = W * (0.55 + 0.20 * t * math.sin(3.4 * t + phase))
            w = 8 + 170 * t ** 1.7
            if prev:
                rd.polygon([(prev[0] - prev[2], prev[1]), (prev[0] + prev[2], prev[1]), (x + w, y), (x - w, y)],
                           fill=_blend(pal["horizon"], pal["top"], 0.55) + (92,))
            prev = (x, y, w)
        img = Image.alpha_composite(img.convert("RGBA"), river.filter(ImageFilter.GaussianBlur(5))).convert("RGB")

    # granulado fino, para parecer foto e nao vetor
    # (ruido com a mesma semente da paisagem: gerar de novo da exatamente a mesma imagem)
    noise = Image.frombytes("L", size, random.Random(seed).randbytes(W * H)).convert("RGB")
    return Image.blend(img, noise, 0.016)


def make_background(size: tuple[int, int], photo: Image.Image | None, seed: int = 0) -> Image.Image:
    if photo is not None:
        bg = ImageOps.fit(photo.convert("RGB"), size, Image.LANCZOS, centering=(0.5, 0.45))
        bg = Image.blend(bg, Image.new("RGB", size, GREEN_DEEP), 0.28)
        top, bottom = 110, 225
    else:
        bg = landscape(size, seed)
        top, bottom = 50, 175  # a paisagem ja e escura: so reforca onde ficam o texto e o rodape
    shade = Image.new("RGB", size, INK)
    bg = Image.composite(shade, bg, _vertical_gradient(size, top, bottom))
    # o texto fica a esquerda: um escurecimento suave desse lado garante a leitura sobre qualquer fundo
    return Image.composite(shade, bg, _horizontal_gradient(size, 95, 0))


# ----------------------------------------------------------------------- logo
def place_logo(img: Image.Image, xy: tuple[int, int], logo_dark_bg: Image.Image | None,
               logo_light_bg: Image.Image | None, height: int = 96) -> None:
    """Logo de texto branco direto sobre a arte escura; senao, o de texto escuro sobre uma plaquinha clara."""
    if logo_dark_bg is not None:
        w = int(logo_dark_bg.width * (height / logo_dark_bg.height))
        img.alpha_composite(logo_dark_bg.convert("RGBA").resize((w, height), Image.LANCZOS), xy)
    else:
        img.alpha_composite(logo_chip(logo_light_bg, height), xy)


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
    logos: tuple[Image.Image | None, Image.Image | None],
) -> Image.Image:
    W, H = POST_SIZE
    img = background.copy().convert("RGBA")
    d = ImageDraw.Draw(img)
    text_w = W - MARGIN_X - 90
    cover = index == 0

    d.rectangle((58, 96, 62, H - 96), fill=LINE + (200,))  # traco vertical da marca
    place_logo(img, (MARGIN_X, 70), logos[0], logos[1])

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
    headline: str, text: str, kicker: str, footer: str, background: Image.Image,
    logos: tuple[Image.Image | None, Image.Image | None],
) -> Image.Image:
    W, H = STORY_SIZE
    img = background.copy().convert("RGBA")
    d = ImageDraw.Draw(img)
    text_w = W - MARGIN_X - 90

    d.rectangle((58, 120, 62, H - 480), fill=LINE + (200,))
    place_logo(img, (MARGIN_X, 104), logos[0], logos[1], height=116)

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
    logo_dark_bg: Image.Image | None = None,
    logo_light_bg: Image.Image | None = None,
    seed: int = 0,
    invite_story: bool = False,
) -> dict[str, Image.Image]:
    """Todas as artes de um rascunho: slide_01.. (carrossel) e story.

    Com `invite_story`, sai tambem `story_convite`: um segundo story que manda o seguidor para o post do feed."""
    out: dict[str, Image.Image] = {}
    logos = (logo_dark_bg, logo_light_bg)
    post_bg = make_background(POST_SIZE, photo, seed)
    for i, (title, body) in enumerate(slides):
        kicker = "Novidade na legislação ambiental" if i == 0 else norm_label
        out[f"slide_{i + 1:02d}"] = render_post_slide(title, body, kicker, i, len(slides), post_bg, logos)
    story_bg = make_background(STORY_SIZE, photo, seed + 1)
    out["story"] = render_story(headline, status_text, "Novidade na legislação ambiental",
                                f"Fale com a HRBio\n{norm_label}", story_bg, logos)
    if invite_story:
        invite_bg = make_background(STORY_SIZE, photo, seed + 2)
        out["story_convite"] = render_story(headline, "Saiu post novo no nosso feed, com o resumo completo e a fonte oficial. Confira!",
                                            "Post novo no feed", f"Fale com a HRBio\n{norm_label}", invite_bg, logos)
    return out

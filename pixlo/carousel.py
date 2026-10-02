"""Carousel slide renderer: 1080x1350 JPEGs in one of several visual styles.

Styles rotate between posts so the feed never looks like one template on repeat:
  classic: full-bleed photo, soft gradient behind white text
  panel:   full-bleed photo, text on a dark translucent card
  bold:    full-bleed photo, title in accent highlight boxes, body on a light card
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from .config import ROOT

STYLES = ["classic", "panel", "bold"]
FALLBACK_FONTS = ["C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "DejaVuSans-Bold.ttf"]
MARGIN = 72


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _font(path: str | None, size: int) -> ImageFont.FreeTypeFont:
    for candidate in ([str(ROOT / path)] if path else []) + FALLBACK_FONTS:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _balanced(draw, text: str, font, max_width: int) -> list[str]:
    """Wrap into as few lines as possible, then even out line lengths (no one-word orphan lines)."""
    lines = _wrap(draw, text, font, max_width)
    if len(lines) < 2:
        return lines
    lo, hi, best = max_width // 2, max_width, lines
    while hi - lo > 6:
        mid = (lo + hi) // 2
        trial = _wrap(draw, text, font, mid)
        if len(trial) <= len(lines) and all(draw.textlength(l, font=font) <= max_width for l in trial):
            best, hi = trial, mid
        else:
            lo = mid
    return best


def _fit(draw, text: str, font_path: str, max_width: int, max_lines: int, size: int, min_size: int):
    """Largest font size (stepping down) at which text wraps into max_lines."""
    while True:
        font = _font(font_path, size)
        lines = _balanced(draw, text, font, max_width)
        if len(lines) <= max_lines or size <= min_size:
            return font, lines
        size -= 4


def _line_h(font) -> int:
    asc, desc = font.getmetrics()
    return int((asc + desc) * 1.08)


def _gradient(img: Image.Image, position: str, coverage: float, strength: int) -> Image.Image:
    W, H = img.size
    mask = Image.new("L", (1, H))
    span = int(H * coverage)
    for y in range(H):
        d = (H - 1 - y) if position == "bottom" else y  # distance from the text edge
        mask.putpixel((0, y), int(strength * max(0.0, 1 - d / span) ** 1.4) if d < span else 0)
    return Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, mask.resize((W, H)))


def _photo(path: Path, size: tuple[int, int]) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return ImageOps.fit(img, size, Image.LANCZOS, centering=(0.5, 0.45))


def _pill(draw, xy, text, font, fill, text_fill, anchor_right=False, pad=(22, 10)):
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    w, h = r - l + 2 * pad[0], b - t + 2 * pad[1]
    x, y = xy
    if anchor_right:
        x -= w
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=fill)
    draw.text((x + pad[0] - l, y + pad[1] - t), text, font=font, fill=text_fill)
    return w, h


def _ensure_contrast(img: Image.Image, draw, box: tuple[int, int, int, int]) -> None:
    """If the photo behind a text block is bright or busy, put a translucent dark card behind it."""
    W, H = img.size
    x0, y0, x1, y1 = max(0, box[0]), max(0, box[1]), min(W, box[2]), min(H, box[3])
    stat = ImageStat.Stat(img.crop((x0, y0, x1, y1)).convert("L"))
    if stat.mean[0] > 70 or stat.stddev[0] > 45:
        draw.rounded_rectangle((x0, y0, x1, y1), radius=32, fill=(0, 0, 0, 150))


def _text_block(draw, x, y, lines, font, fill, shadow=True):
    lh = _line_h(font)
    for i, line in enumerate(lines):
        if shadow:
            draw.text((x + 2, y + i * lh + 3), line, font=font, fill=(0, 0, 0, 160))
        draw.text((x, y + i * lh), line, font=font, fill=fill)
    return lh * len(lines)


def render_slide(cfg: dict, slide: dict, photo_path: Path, index: int, total: int, style: str,
                 out_path: Path) -> Path:
    c, brand, colors = cfg["carousel"], cfg["brand"], cfg["brand"]["colors"]
    W, H = c["width"], c["height"]
    accent, dark, white = _rgb(colors["accent"]), _rgb(colors["dark"]), _rgb(colors["text"])
    kind, pos = slide["kind"], slide["text_position"]
    max_w = W - 2 * MARGIN

    img = _photo(photo_path, (W, H))
    if kind == "cta":
        img = img.filter(ImageFilter.GaussianBlur(14))
        img = Image.blend(img, Image.new("RGB", (W, H), _rgb(colors["primary"])), 0.55)
    elif style == "classic" or kind == "cover":
        img = _gradient(img, pos, 0.62 if kind == "cover" else 0.58, 230)
    else:
        img = _gradient(img, pos, 0.35, 120)

    draw = ImageDraw.Draw(img, "RGBA")
    f_small = _font(brand["font_semibold"], 30)

    # Slide counter (not on cover) and handle
    if kind != "cover":
        _pill(draw, (W - MARGIN, MARGIN - 20), f"{index}/{total}", f_small, (0, 0, 0, 110), white,
              anchor_right=True)
    handle_y = H - MARGIN - 6 if pos == "top" or kind == "cta" else MARGIN - 20
    draw.text((MARGIN, handle_y), brand["handle"], font=f_small, fill=(255, 255, 255, 220))

    # ---- text --------------------------------------------------------------------------------
    if kind == "cover":
        f_title, t_lines = _fit(draw, slide["title"].upper() if style == "bold" else slide["title"],
                                brand["font_black"], max_w, 4, 112, 64)
        f_body, b_lines = _fit(draw, slide["body"], brand["font_regular"], max_w, 2, 44, 32) \
            if slide["body"] else (None, [])
        block_h = _line_h(f_title) * len(t_lines) + (24 + _line_h(f_body) * len(b_lines) if b_lines else 0) + 90
        y = (H - MARGIN - 40 - block_h) if pos == "bottom" else (MARGIN + 60)
        if style != "bold":
            _ensure_contrast(img, draw, (MARGIN - 32, y - 28, W - MARGIN + 32, y + block_h - 40))
        draw.rectangle((MARGIN, y, MARGIN + 110, y + 10), fill=accent)
        y += 34
        if style == "bold":
            for line in t_lines:
                l, t, r, b = draw.textbbox((0, 0), line, font=f_title)
                draw.rectangle((MARGIN - 14, y - 6, MARGIN + r - l + 14, y + _line_h(f_title) - 10), fill=accent)
                draw.text((MARGIN - l, y - t + 4), line, font=f_title, fill=dark)
                y += _line_h(f_title) + 6
        else:
            y += _text_block(draw, MARGIN, y, t_lines, f_title, white)
        if b_lines:
            y += 18
            if style == "bold":
                _ensure_contrast(img, draw, (MARGIN - 24, y - 12, W - MARGIN + 24,
                                             y + _line_h(f_body) * len(b_lines) + 12))
            y += _text_block(draw, MARGIN, y, b_lines, f_body, accent if style != "bold" else white)
        # "Swipe" pill with a drawn arrow (Poppins has no arrow glyph)
        pw, ph = _pill(draw, (W - MARGIN, y + 26), "Swipe        ", f_small, accent, dark, anchor_right=True)
        ax, ay = W - MARGIN - 60, y + 26 + ph // 2
        draw.line((ax, ay, ax + 32, ay), fill=dark, width=5)
        draw.polygon([(ax + 38, ay), (ax + 24, ay - 11), (ax + 24, ay + 11)], fill=dark)

    elif kind == "info":
        f_title, t_lines = _fit(draw, slide["title"], brand["font_bold"], max_w - 60, 3, 76, 48)
        f_body, b_lines = _fit(draw, slide["body"], brand["font_regular"], max_w - 60, 7, 42, 30)
        t_h, b_h = _line_h(f_title) * len(t_lines), _line_h(f_body) * len(b_lines)
        card_h = t_h + b_h + 26 + 2 * 46
        y0 = (H - MARGIN - 70 - card_h) if pos == "bottom" else (MARGIN + 70)
        x0, pad = MARGIN - 10, 40
        if style == "panel":
            draw.rounded_rectangle((x0, y0, W - x0, y0 + card_h), radius=36, fill=(*dark, 205))
            draw.rectangle((x0 + pad, y0 + 30, x0 + pad + 70, y0 + 38), fill=accent)
            y = y0 + 52
            y += _text_block(draw, x0 + pad, y, t_lines, f_title, white, shadow=False) + 22
            _text_block(draw, x0 + pad, y, b_lines, f_body, (230, 236, 240), shadow=False)
        elif style == "bold":
            y = y0
            for line in t_lines:
                l, t, r, b = draw.textbbox((0, 0), line, font=f_title)
                draw.rectangle((x0, y, x0 + r - l + 36, y + _line_h(f_title) + 4), fill=accent)
                draw.text((x0 + 18 - l, y - t + 8), line, font=f_title, fill=dark)
                y += _line_h(f_title) + 8
            y += 14
            draw.rounded_rectangle((x0, y, W - x0, y + b_h + 60), radius=26, fill=(255, 255, 255, 235))
            _text_block(draw, x0 + 30, y + 28, b_lines, f_body, dark, shadow=False)
        else:  # classic
            _ensure_contrast(img, draw, (MARGIN - 32, y0 + 6, W - MARGIN + 32, y0 + card_h + 14))
            y = y0 + 30
            draw.rectangle((MARGIN, y, MARGIN + 90, y + 9), fill=accent)
            y += 30
            y += _text_block(draw, MARGIN, y, t_lines, f_title, white) + 18
            _text_block(draw, MARGIN, y, b_lines, f_body, (238, 242, 245))

    else:  # cta
        f_title, t_lines = _fit(draw, slide["title"], brand["font_black"], max_w, 4, 96, 60)
        f_body, b_lines = _fit(draw, slide["body"], brand["font_regular"], max_w, 4, 44, 32)
        f_handle = _font(brand["font_bold"], 52)
        block_h = _line_h(f_title) * len(t_lines) + _line_h(f_body) * len(b_lines) + 260
        y = (H - block_h) // 2
        # Bookmark icon: a hint to the save button
        bx = W // 2 - 32
        draw.polygon([(bx, y), (bx + 64, y), (bx + 64, y + 84), (bx + 32, y + 60), (bx, y + 84)], fill=accent)
        y += 120
        for line in t_lines:
            w = draw.textlength(line, font=f_title)
            draw.text(((W - w) / 2, y), line, font=f_title, fill=white)
            y += _line_h(f_title)
        y += 20
        for line in b_lines:
            w = draw.textlength(line, font=f_body)
            draw.text(((W - w) / 2, y), line, font=f_body, fill=(235, 240, 242))
            y += _line_h(f_body)
        y += 40
        w = draw.textlength(brand["handle"], font=f_handle) + 60
        _pill(draw, ((W - w) / 2, y), brand["handle"], f_handle, accent, dark, pad=(30, 16))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=92, optimize=True)  # Graph API requires JPEG
    return out_path


def render(cfg: dict, slides: list[dict], photos: list[dict], style: str, out_dir: Path) -> list[Path]:
    total = len(slides)
    return [render_slide(cfg, s, photos[s["photo"]]["path"], i, total, style, out_dir / f"{i:02d}.jpg")
            for i, s in enumerate(slides, 1)]

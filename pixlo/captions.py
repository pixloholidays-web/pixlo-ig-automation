"""Kinetic on-screen text for Reels: big bold words that pop in one by one, numbers in the accent colour,
and a label pill ("DAY 2", "3") for itinerary and list shots. No dark box: thick outlines, a soft shadow and a
gentle darkening band behind the words keep them readable on any photo.
"""
import re

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .carousel import _balanced, _font, _rgb

W, H = 1080, 1920
LABEL = re.compile(r"^\s*((?:day|night|step|stop)\s+\d+|\d+(?=[.)]\s))\s*[:.)\-–]?\s*", re.IGNORECASE)
UNITS = {"am", "pm", "km", "m", "hrs", "hours", "hour", "h", "min", "mins", "minutes", "minute", "days", "day",
         "nights", "night", "%", "°c", "kg", "l"}
KEYWORDS = {"free", "closed", "shuts", "hidden", "secret", "never", "skip", "cheapest", "best", "only"}
POP_FRAMES = 6      # how long one word takes to pop in
WORD_GAP = 2        # frames between one word and the next


def split_label(text: str) -> tuple[str | None, str]:
    """("DAY 2", "Triund gate shuts 11:30 AM") from "Day 2: Triund gate shuts 11:30 AM"."""
    m = LABEL.match(text)
    if not m or not text[m.end():].strip():
        return None, text.strip()
    return m.group(1).upper(), text[m.end():].strip()


def _highlighted(words: list[str]) -> list[bool]:
    """Numbers, prices, times (and the unit right after them) and a few payoff words get the accent colour."""
    out, prev_num = [], False
    for w in words:
        bare = w.strip(".,!?:;()\"'").lower()
        num = bool(re.search(r"\d|₹", w))
        out.append(num or bare in KEYWORDS or (prev_num and bare in UNITS))
        prev_num = num
    return out


def _ease_out_back(t: float) -> float:
    t = max(0.0, min(1.0, t))
    c = 1.9
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


class Caption:
    """Precomputed sprites for one shot's text; draw(frame, i) composites frame i of the animation."""

    def __init__(self, cfg: dict, text: str, kind: str, position: str = "bottom", top: int | None = None,
                 safe=(300, 1420), margin: int = 70):
        brand, colors = cfg["brand"], cfg["brand"]["colors"]
        self.kind = kind
        accent, white, dark = _rgb(colors["accent"]), _rgb(colors["text"]), _rgb(colors["dark"])
        label, body = split_label(text) if kind == "info" else (None, text.strip())
        size, min_size = (122, 72) if kind == "cover" else (104, 64)
        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        font = _font(brand["font_black"], size)
        lines = _balanced(draw, body, font, W - 2 * margin)
        while (len(lines) > 3 or any(draw.textlength(l, font=font) > W - 2 * margin for l in lines)) \
                and size > min_size:
            size -= 4
            font = _font(brand["font_black"], size)
            lines = _balanced(draw, body, font, W - 2 * margin)
        stroke = max(5, size // 14)
        lh = int(size * 1.18)
        space = draw.textlength(" ", font=font)

        # Label pill above the words
        self.label = None
        label_h = 0
        if label:
            f_l = _font(brand["font_black"], 44)
            tw = draw.textlength(label, font=f_l)
            pill = Image.new("RGBA", (int(tw) + 60, 76), (0, 0, 0, 0))
            dp = ImageDraw.Draw(pill)
            dp.rounded_rectangle((0, 0, pill.width - 1, pill.height - 1), radius=38, fill=(*accent, 255))
            dp.text((30, 10), label, font=f_l, fill=(*dark, 255))
            self.label = np.asarray(pill).astype(np.float32)
            label_h = pill.height + 26

        block_h = label_h + lh * len(lines)
        if top is None:
            top = safe[0] + 80 if position == "top" else safe[1] - 40 - block_h
            if kind == "cover":
                top = max(safe[0] + 60, 760 - block_h // 2)
        self.top, self.bottom = top, top + block_h
        if self.label is not None:
            self.label_xy = ((W - self.label.shape[1]) // 2, top)

        # One sprite per word, with outline and shadow, at its final position
        self.words = []
        words_all = body.split()
        marks = _highlighted(words_all)
        k = 0
        for li, line in enumerate(lines):
            ws = line.split()
            widths = [draw.textlength(w, font=font) for w in ws]
            x = (W - (sum(widths) + space * (len(ws) - 1))) / 2
            y = top + label_h + li * lh
            for w, wd in zip(ws, widths):
                pad = stroke + 18
                img = Image.new("RGBA", (int(wd) + 2 * pad, lh + 2 * pad), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                colour = accent if marks[k] else white
                d.text((pad, pad), w, font=font, fill=(*colour, 255), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
                shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
                ImageDraw.Draw(shadow).text((pad + 4, pad + 7), w, font=font, fill=(0, 0, 0, 150),
                                            stroke_width=stroke, stroke_fill=(0, 0, 0, 150))
                sprite = Image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(8)), img)
                self.words.append((np.asarray(sprite).astype(np.float32), x - pad + 0.0, y - pad + 0.0))
                x += wd + space
                k += 1

        # Soft darkening band behind the text block, so white words read on bright sky or snow
        band = np.zeros(H, np.float32)
        lo, hi = max(0, top - 140), min(H, self.bottom + 140)
        ys = np.arange(lo, hi)
        dist = np.maximum(0, np.maximum(top - ys, ys - self.bottom)) / 140
        band[lo:hi] = 0.34 * (1 - np.clip(dist, 0, 1)) ** 1.5
        self.band_rows = (lo, hi)
        self.band = band[lo:hi, None, None]

    def _paste(self, frame: np.ndarray, sprite: np.ndarray, cx: float, cy: float, scale: float, alpha: float):
        if alpha <= 0.01:
            return
        if abs(scale - 1) > 0.01:
            sprite = cv2.resize(sprite, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
        h, w = sprite.shape[:2]
        x0, y0 = int(round(cx - w / 2)), int(round(cy - h / 2))
        fx0, fy0, fx1, fy1 = max(0, x0), max(0, y0), min(W, x0 + w), min(H, y0 + h)
        if fx1 <= fx0 or fy1 <= fy0:
            return
        s = sprite[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0]
        a = s[..., 3:4] / 255 * alpha
        region = frame[fy0:fy1, fx0:fx1].astype(np.float32)
        frame[fy0:fy1, fx0:fx1] = (region * (1 - a) + s[..., :3] * a).astype(np.uint8)

    def draw(self, frame: np.ndarray, i: int) -> None:
        lo, hi = self.band_rows
        frame[lo:hi] = (frame[lo:hi].astype(np.float32) * (1 - self.band)).astype(np.uint8)
        if self.label is not None:
            p = 1.0 if self.kind == "cover" else _ease_out_back(i / POP_FRAMES)
            lx, ly = self.label_xy
            h, w = self.label.shape[:2]
            self._paste(frame, self.label, lx + w / 2, ly + h / 2, max(0.3, p), min(1.0, i / 3 + 0.01))
        start = 2 if self.label is not None else 0
        for k, (sprite, x, y) in enumerate(self.words):
            h, w = sprite.shape[:2]
            cx, cy = x + w / 2, y + h / 2
            if self.kind == "cover":
                # The hook is readable from the very first frame; the whole block settles into place
                scale, alpha = 1 + 0.07 * (1 - min(1.0, i / 8)) ** 2, 1.0
                mid = (self.top + self.bottom) / 2
                cx, cy = W / 2 + (cx - W / 2) * scale, mid + (cy - mid) * scale
            else:
                t = (i - start - k * WORD_GAP) / POP_FRAMES
                if t <= 0:
                    continue
                scale, alpha = 0.55 + 0.45 * _ease_out_back(t), min(1.0, t * 2.5)
            self._paste(frame, sprite, cx, cy, scale, alpha)

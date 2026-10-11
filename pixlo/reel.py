"""Reels: 1080x1920 videos with 2.5D parallax camera moves, animated text, a route map and original music.

Each photo gets a depth map (Depth Anything V2 Small, ONNX on CPU). A virtual camera then pushes in,
pans or rises, and near pixels move more than far ones, which gives a drone/dolly feel from a still.
On top of that: a colour grade shared by every shot, the place name in huge letters behind the mountains or the
subject on the cover (cutout.py), an animated route map (routemap.py) and a mix of whip, zoom and flash transitions.
Everything runs on CPU, so it works in GitHub Actions. Encoding uses the ffmpeg bundled with imageio-ffmpeg.
"""
import hashlib
import subprocess
from pathlib import Path

import cv2
import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import posts
from .carousel import _fit, _font, _line_h, _rgb
from .config import ROOT

W, H, FPS = 1080, 1920, 30
MARGIN_X = 90
SAFE_TOP, SAFE_BOTTOM = 300, 1420      # Reels UI covers the top bar, the caption and the side buttons
SOURCE_MARGIN = 1.15                     # photo is rendered larger than the frame so pans never show edges
TRANS = 6                                # frames a transition lasts (0.2 s): reads as a cut with energy
TRANSITIONS = ["whip_left", "zoom", "whip_up", "flash"]
DEPTH_REPO = "onnx-community/depth-anything-v2-small"
MEAN, STD = np.array([0.485, 0.456, 0.406], np.float32), np.array([0.229, 0.224, 0.225], np.float32)

# zoom start/end, pan x/y start/end (output px), depth strength
MOVES = {
    "push_in":   {"z": (1.00, 1.10), "x": (0, 0),     "y": (0, 0),     "a": 1.0},
    "pan_right": {"z": (1.06, 1.08), "x": (-45, 45),  "y": (0, 0),     "a": 0.3},
    "rise":      {"z": (1.06, 1.09), "x": (0, 0),     "y": (40, -40),  "a": 0.3},
    "pull_out":  {"z": (1.10, 1.00), "x": (0, 0),     "y": (0, 0),     "a": 1.0},
    "pan_left":  {"z": (1.06, 1.08), "x": (45, -45),  "y": (0, 0),     "a": 0.3},
    "orbit":     {"z": (1.02, 1.09), "x": (-30, 30),  "y": (10, -10),  "a": 0.7},
}
MOVE_ORDER = list(MOVES)
PILLAR_MOOD = {"hidden-gems": "adventure", "budget-breakdown": "upbeat", "exact-itinerary": "upbeat",
               "where-to-go-this-month": "epic", "mistakes-to-avoid": "upbeat", "this-vs-that": "upbeat"}

_session = None


# ------------------------------------------------------------------ depth
def _depth_session():
    global _session
    if _session is None:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        _session = ort.InferenceSession(hf_hub_download(DEPTH_REPO, "onnx/model.onnx"),
                                        providers=["CPUExecutionProvider"])
    return _session


def depth_maps(img: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """Relative depth in [0, 1] (1 = nearest), same size as img: (softened for the camera warp, raw)."""
    w, h = img.size
    scale = 518 / min(w, h)
    iw, ih = max(14, round(w * scale / 14) * 14), max(14, round(h * scale / 14) * 14)
    x = (np.asarray(img.resize((iw, ih), Image.BICUBIC), np.float32) / 255 - MEAN) / STD
    s = _depth_session()
    d = s.run(None, {s.get_inputs()[0].name: x.transpose(2, 0, 1)[None].astype(np.float32)})[0][0]
    d = cv2.resize(d, (w, h), interpolation=cv2.INTER_CUBIC)
    lo, hi = np.percentile(d, 2), np.percentile(d, 98)
    raw = np.clip((d - lo) / (hi - lo + 1e-6), 0, 1).astype(np.float32)
    return cv2.GaussianBlur(raw, (0, 0), sigmaX=w / 220), raw  # soften edges to avoid tearing


def depth_map(img: Image.Image) -> np.ndarray:
    return depth_maps(img)[0]


# ------------------------------------------------------------------ colour grade
def grade(rgb: np.ndarray) -> np.ndarray:
    """One look for every shot, so photos from different photographers feel like one film:
    a little more contrast and colour, warm highlights, cool shadows and a soft vignette."""
    x = rgb.astype(np.float32) / 255
    x = (x - 0.5) * 1.08 + 0.5
    lum = (x @ np.array([0.299, 0.587, 0.114], np.float32))[..., None]
    x = lum + (x - lum) * 1.14
    lum = np.clip(lum, 0, 1)
    x += (1 - lum) ** 2 * np.array([-0.012, 0.004, 0.020], np.float32)   # shadows towards teal
    x += lum ** 2 * np.array([0.020, 0.008, -0.015], np.float32)         # highlights towards warm
    h, w = rgb.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / np.sqrt(2)
    x *= (1 - 0.20 * r ** 2.5)[..., None]
    return (np.clip(x, 0, 1) * 255).astype(np.uint8)


# ------------------------------------------------------------------ text cards
def _box(lines_spec, width: int, pad=(44, 34), bar=True, accent=(255, 183, 3)) -> Image.Image:
    """Rounded dark card holding lines: [(lines, font, colour, gap_after)]."""
    height = pad[1] * 2 + (28 if bar else 0) + sum(_line_h(f) * len(ls) + gap for ls, f, _, gap in lines_spec)
    card = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((0, 0, width - 1, height - 1), radius=36, fill=(8, 14, 20, 175))
    y = pad[1]
    if bar:
        d.rectangle((pad[0], y, pad[0] + 90, y + 9), fill=accent)
        y += 28
    for ls, f, colour, gap in lines_spec:
        for line in ls:
            d.text((pad[0] + 2, y + 3), line, font=f, fill=(0, 0, 0, 140))
            d.text((pad[0], y), line, font=f, fill=colour)
            y += _line_h(f)
        y += gap
    return card


def _text_card(cfg: dict, slide: dict, compact: bool = False) -> tuple[Image.Image, int, int]:
    brand, colors = cfg["brand"], cfg["brand"]["colors"]
    accent, white = _rgb(colors["accent"]), _rgb(colors["text"])
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    max_w = W - 2 * MARGIN_X - 88
    text = slide.get("reel_text") or slide["title"]
    if slide["kind"] == "cover":
        # compact: the hook sits in the lower third, leaving the top for the big place name
        f_t, t_lines = _fit(draw, text, brand["font_black"], max_w, 4, 80 if compact else 100, 56)
        spec = [(t_lines, f_t, white, 0)]
        card = _box(spec, W - 2 * MARGIN_X, accent=accent)
        y = SAFE_BOTTOM - 40 - card.height if compact else max(SAFE_TOP + 40, 760 - card.height // 2)
        return card, MARGIN_X, y
    f_t, t_lines = _fit(draw, text, brand["font_bold"], max_w, 3, 78, 52)
    card = _box([(t_lines, f_t, white, 0)], W - 2 * MARGIN_X, accent=accent)
    y = SAFE_TOP + 60 if slide.get("text_position") == "top" else SAFE_BOTTOM - card.height
    return card, MARGIN_X, y


def headline(destination: str | None) -> str | None:
    """The place name for the big title behind the subject: short enough to read at a glance."""
    name = (destination or "").split(" and ")[0].strip().upper()
    return name if 3 <= len(name) <= 18 else None


def _title_layer(cfg: dict, text: str) -> tuple[np.ndarray, np.ndarray, list[tuple[int, int]]]:
    """Huge bare place name, full frame width: (RGBA layer with its shadow, text-only alpha, row range per line)."""
    brand = cfg["brand"]
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font, lines = _fit(draw, text, brand["font_black"], W - 2 * 50, 2, 210, 110)
    if len(lines) == 2 and font.size > 150:  # two lines: keep the block compact
        font = _font(brand["font_black"], 150)
    lh = int(_line_h(font) * 0.86)
    pad = 40
    height = lh * len(lines) + 2 * pad
    ink = Image.new("L", (W, height), 0)
    d = ImageDraw.Draw(ink)
    rows = []
    for i, line in enumerate(lines):
        d.text(((W - d.textlength(line, font=font)) / 2, pad + i * lh), line, font=font, fill=255)
        rows.append((pad + i * lh, pad + (i + 1) * lh))
    alpha = np.asarray(ink, np.float32) / 255
    # Two soft shadows keep white text readable on bright sky or snow
    shadow = np.maximum(np.asarray(ink.filter(ImageFilter.GaussianBlur(16)), np.float32) / 255 * 0.75,
                        np.asarray(ink.filter(ImageFilter.GaussianBlur(5)), np.float32) / 255 * 0.55)
    layer = np.zeros((height, W, 4), np.float32)
    layer[..., :3] = np.array(_rgb(brand["colors"]["text"]), np.float32) * alpha[..., None]
    layer[..., 3] = alpha + shadow * (1 - alpha)
    layer[..., :3] /= np.maximum(layer[..., 3:4], 1e-6)  # colour un-premultiplied; shadow stays black
    return layer, alpha, rows


def _place_title(alpha: np.ndarray, rows: list[tuple[int, int]], fg: np.ndarray, bottom: int) -> int | None:
    """Best y for the title: partly hidden by the foreground (so it clearly sits behind it) but still easy
    to read. fg is the foreground mask in frame coordinates; the title must end above `bottom`.
    None if no position works."""
    chunk = max(40, (rows[0][1] - rows[0][0]) // 2)  # about one letter wide
    best, best_score = None, 1.0
    for y in range(SAFE_TOP - 20, bottom - alpha.shape[0] + 30, 20):
        m = fg[y:y + alpha.shape[0]]
        hidden = float((alpha * m).sum() / alpha.sum())
        if not 0.04 <= hidden <= 0.28:
            continue
        readable = True
        for a, b in rows:  # no letter may disappear: check the line letter by letter
            ink, cut = alpha[a:b], alpha[a:b] * m[a:b]
            letters = [(ink[:, x:x + chunk].sum(), cut[:, x:x + chunk].sum()) for x in range(0, W, chunk)]
            lost = [c / i for i, c in letters if i > 50]
            if cut.sum() / max(ink.sum(), 1.0) > 0.40 or max(lost, default=0) > 0.80 or sum(v > 0.6 for v in lost) > 1:
                readable = False
        if readable and abs(hidden - 0.14) < best_score:
            best, best_score = y, abs(hidden - 0.14)
    return best


def _cta_overlay(cfg: dict, slide: dict) -> Image.Image:
    brand, colors = cfg["brand"], cfg["brand"]["colors"]
    accent, dark, white = _rgb(colors["accent"]), _rgb(colors["dark"]), _rgb(colors["text"])
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    f_t, t_lines = _fit(d, slide.get("reel_text") or slide["title"], brand["font_black"], W - 2 * MARGIN_X, 3, 92, 60)
    f_b = _font(brand["font_regular"], 42)
    f_h = _font(brand["font_bold"], 54)
    y = 640
    bx = W // 2 - 34
    d.polygon([(bx, y), (bx + 68, y), (bx + 68, y + 90), (bx + 34, y + 64), (bx, y + 90)], fill=accent)
    y += 130
    for line in t_lines:
        w = d.textlength(line, font=f_t)
        d.text(((W - w) / 2 + 2, y + 3), line, font=f_t, fill=(0, 0, 0, 140))
        d.text(((W - w) / 2, y), line, font=f_t, fill=white)
        y += _line_h(f_t)
    y += 26
    sub = "Full details in the caption"
    w = d.textlength(sub, font=f_b)
    d.text(((W - w) / 2, y), sub, font=f_b, fill=(235, 240, 242))
    y += 90
    hw = d.textlength(brand["handle"], font=f_h)
    d.rounded_rectangle(((W - hw) / 2 - 34, y, (W + hw) / 2 + 34, y + 92), radius=46, fill=accent)
    d.text(((W - hw) / 2, y + 14), brand["handle"], font=f_h, fill=dark)
    return ov


# ------------------------------------------------------------------ shots
def _ease(t: float) -> float:
    return t * t * (3 - 2 * t)


def _ease_out(t: float) -> float:
    """Fast start, soft landing: the camera arrives with energy after each cut."""
    return 1 - (1 - t) ** 2.2


def _blend(frame: np.ndarray, card: np.ndarray, x: int, y: int, alpha: float) -> None:
    h, w = card.shape[:2]
    y0, y1 = max(0, y), min(H, y + h)
    if y1 <= y0 or alpha <= 0:
        return
    region = frame[y0:y1, x:x + w].astype(np.float32)
    c = card[y0 - y:y1 - y]
    a = c[..., 3:4].astype(np.float32) / 255 * alpha
    frame[y0:y1, x:x + w] = (region * (1 - a) + c[..., :3] * a).astype(np.uint8)


class Shot:
    def __init__(self, cfg: dict, slide: dict, photo: Path, move: str, seconds: float, place: str | None = None):
        rc = cfg["reels"]
        self.slide, self.move, self.n = slide, MOVES[move], max(TRANS + 2, round(seconds * FPS))
        self.title = None  # (layer, y) when the cover has the place name sitting behind the subject
        img = ImageOps.fit(ImageOps.exif_transpose(Image.open(photo)).convert("RGB"),
                           (round(W * SOURCE_MARGIN), round(H * SOURCE_MARGIN)), Image.LANCZOS)
        if slide["kind"] == "cta":
            img = Image.blend(img.filter(ImageFilter.GaussianBlur(16)),
                              Image.new("RGB", img.size, _rgb(cfg["brand"]["colors"]["primary"])), 0.55)
            self.src = np.asarray(img)
            self.depth = np.zeros(img.size[::-1], np.float32)
            self.card, self.cx, self.cy = np.asarray(_cta_overlay(cfg, slide)), 0, 0
            return
        self.depth, raw = depth_maps(img)
        self.src = grade(np.asarray(img)) if rc.get("grade", True) else np.asarray(img)
        if slide["kind"] == "cover" and place and rc.get("title_behind_subject", True):
            self._try_title_behind(cfg, img, raw, place)
        if self.title is None:
            card, self.cx, self.cy = _text_card(cfg, slide)
            self.card = np.asarray(card)

    def _try_title_behind(self, cfg: dict, img: Image.Image, raw_depth: np.ndarray, place: str) -> None:
        from . import cutout
        found = cutout.foreground(img, raw_depth)
        if not found:
            return
        self.fg = found[0]
        layer, alpha, rows = _title_layer(cfg, place)
        # Foreground as the camera sees it mid-shot, to choose where the title goes
        sh, sw = self.src.shape[:2]
        z = (self.move["z"][0] + self.move["z"][1]) / 2
        u, v = np.meshgrid(np.arange(W, dtype=np.float32) - W / 2, np.arange(H, dtype=np.float32) - H / 2)
        seen = cv2.remap(self.fg, sw / 2 + u / z, sh / 2 + v / z, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        card, cx, cy = _text_card(cfg, self.slide, compact=True)  # the hook, in the lower third
        y = _place_title(alpha, rows, seen, cy)
        if y is not None:
            self.title = (layer, y)
            self.card, self.cx, self.cy = np.asarray(card), cx, cy

    def frames(self, u: np.ndarray, v: np.ndarray):
        sh, sw = self.src.shape[:2]
        cx, cy = sw / 2, sh / 2
        m = self.move
        for i in range(self.n):
            t = _ease_out(i / (self.n - 1))
            z = m["z"][0] + (m["z"][1] - m["z"][0]) * t
            px = m["x"][0] + (m["x"][1] - m["x"][0]) * t
            py = m["y"][0] + (m["y"][1] - m["y"][0]) * t
            xs, ys = cx + u / z, cy + v / z
            d = cv2.remap(self.depth, xs, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            for _ in range(2):  # fixed-point: depth at the source position we actually sample
                zd = z ** (1 + m["a"] * d)
                k = 0.3 + 1.4 * d
                xs, ys = cx + (u - px * k) / zd, cy + (v - py * k) / zd
                d = cv2.remap(self.depth, xs, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            frame = cv2.remap(self.src, xs, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            if self.title:
                # The title is drawn over the photo, then the foreground is put back on top of it
                layer, y0 = self.title
                y1 = y0 + layer.shape[0]
                fg = cv2.remap(self.fg, xs[y0:y1], ys[y0:y1], cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REFLECT)[..., None]
                region = frame[y0:y1].astype(np.float32)
                a = layer[..., 3:4] * (1 - fg)
                frame[y0:y1] = (region * (1 - a) + layer[..., :3] * a).astype(np.uint8)
            # Text slides up and fades in over the first 0.35 s; the hook is fully visible from frame 0
            a = 1.0 if self.slide["kind"] == "cover" else _ease(min(1.0, i / (0.35 * FPS)))
            _blend(frame, self.card, self.cx, self.cy + round(36 * (1 - a)), a)
            yield frame


class MapShot:
    """The animated route map (routemap.py), as a shot."""

    def __init__(self, cfg: dict, route: dict, seconds: float):
        self.cfg, self.route, self.n = cfg, route, max(TRANS + 2, round(seconds * FPS))

    def frames(self, u: np.ndarray, v: np.ndarray):
        from . import routemap
        yield from routemap.frames(self.cfg, self.route, self.n)


def transition(prev: np.ndarray, new: np.ndarray, w: float, kind: str) -> np.ndarray:
    """One frame of the change from the outgoing shot to the incoming one, w going 0 -> 1."""
    e = _ease(w)
    if kind in ("whip_left", "whip_up"):
        # The old shot slides out as the new one slides in, smeared along the direction of travel
        horizontal = kind == "whip_left"
        size = W if horizontal else H
        off = min(size - 1, max(1, round(size * e)))
        if horizontal:
            out = np.concatenate([prev[:, off:], new[:, :off]], axis=1)
        else:
            out = np.concatenate([prev[off:], new[:off]], axis=0)
        k = max(1, round(size * 0.11 * (1 - abs(2 * w - 1)))) | 1
        return cv2.blur(out, (k, 1) if horizontal else (1, k))
    if kind == "zoom":
        # Both shots keep pushing forward through the cut
        def scaled(img: np.ndarray, z: float) -> np.ndarray:
            return cv2.warpAffine(img, cv2.getRotationMatrix2D((W / 2, H / 2), 0, z), (W, H),
                                  flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        return cv2.addWeighted(scaled(prev, 1 + 0.30 * e), 1 - e, scaled(new, 1.30 - 0.30 * e), e, 0)
    out = cv2.addWeighted(prev, 1 - e, new, e, 0)
    if kind == "flash":  # a quick lift to white on the cut
        out = cv2.addWeighted(out, 1, np.full_like(out, 255), 0.5 * (1 - abs(2 * w - 1)), 0)
    return out


# ------------------------------------------------------------------ music
def music_library(cfg: dict) -> list[dict]:
    lib = ROOT / cfg["reels"]["music_dir"] / "library.yaml"
    if not lib.exists():
        return []
    with open(lib, encoding="utf-8") as f:
        tracks = (yaml.safe_load(f) or {}).get("tracks") or []
    return [t for t in tracks if (ROOT / t["file"]).exists()]


def pick_track(cfg: dict, mood: str) -> dict | None:
    """A track from your own library matching the mood (least recently used), if you added any."""
    tracks = [t for t in music_library(cfg) if mood in (t.get("moods") or [])]
    if not tracks:
        return None
    last_used = {}
    for i, (_, p) in enumerate(sorted(posts.load_all(), key=lambda x: str(x[1].get("scheduled_at")))):
        m = (p.get("reel") or {}).get("music") or {}
        if m.get("file"):
            last_used[m["file"]] = i
    return min(tracks, key=lambda t: last_used.get(t["file"], -1))


# ------------------------------------------------------------------ build
def _shot_seconds(kind: str, bpm: float | None) -> float:
    base = {"cover": 2.6, "map": 2.6, "info": 2.3, "cta": 2.6}[kind]
    if not bpm:
        return base
    beat = 60 / bpm
    return max(2, round(base / beat)) * beat  # cut on the beat


def build(cfg: dict, post: dict, mode: str = "auto") -> dict:
    """Render media/posts/<id>/reel.mp4 (+ reel_cover.jpg). mode 'kit' = silent, for finishing in Edits."""
    import imageio_ffmpeg

    from . import routemap

    rc = cfg["reels"]
    slides = post["slides"]
    info = [s for s in slides if s["kind"] == "info"][: rc["max_shots"] - 2]
    chosen = [slides[0]] + info + [slides[-1]]
    mood = post.get("music_mood") or PILLAR_MOOD.get(post.get("pillar"), "calm")
    track = pick_track(cfg, mood) if mode == "auto" else None
    analysis, gen_plan, bpm = None, None, None
    if track:
        from . import music
        analysis = music.analyze(ROOT / track["file"])
        bpm = track.get("bpm") or analysis["bpm"]
    elif mode == "auto" and rc.get("music_fallback") == "generate":
        from . import synth
        gen_plan = synth.plan(mood, post["id"])  # original music, composed for this post
        bpm = gen_plan["bpm"]

    out_dir = ROOT / "media" / "posts" / post["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    video, cover = out_dir / "reel.mp4", out_dir / "reel_cover.jpg"

    shots = [Shot(cfg, s, posts.photo_file(post["photos"][s["photo"]]), MOVE_ORDER[i % len(MOVE_ORDER)],
                  _shot_seconds(s["kind"], bpm), headline(post.get("destination"))) for i, s in enumerate(chosen)]
    route = post.get("route")
    has_map = bool(rc.get("route_map", True) and not rc.get("offline") and routemap.valid(route))
    if has_map:
        try:  # right after the hook: where this place is and how you get there
            routemap._layer("land")
            shots.insert(1, MapShot(cfg, route, _shot_seconds("map", bpm)))
        except Exception as exc:  # map data unavailable: the Reel goes out without the map
            print(f"    ! route map skipped: {exc}")
            has_map = False
    total_frames = sum(s.n for s in shots) - TRANS * (len(shots) - 1)
    duration = total_frames / FPS
    first = int(hashlib.sha256(post["id"].encode()).hexdigest()[:6], 16)  # which transition starts the cycle

    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if gen_plan:
        from . import synth
        wav = ROOT / "media" / "cache" / "music" / f"original_{post['id']}.wav"
        played = synth.render(gen_plan, duration, wav, rc.get("instruments", "sampled"))
        track = {"id": f"original:{gen_plan['seed']}", "file": wav.relative_to(ROOT).as_posix(),
                 "title": f"Pixlo Original - {post.get('destination', 'travel')}", "artist": "Pixlo",
                 "source": "original", "license": "own", "start": 0.0, "mood": gen_plan["mood"],
                 "instruments": played}
    if track:
        from . import music
        start = track["start"] if track.get("start") is not None else music.best_start(analysis, duration)
        track = dict(track, bpm=round(bpm, 1), start=start)
        cmd += ["-stream_loop", "-1", "-ss", f"{start:.2f}", "-i", str(ROOT / track["file"]),
                "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
                # Instagram plays at about -14 LUFS; normalise so quiet and loud tracks sound alike
                "-af", f"loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=in:d=0.3,"
                       f"afade=t=out:st={max(0, duration - 1.6):.2f}:d=1.5",
                "-t", f"{duration:.3f}"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-movflags", "+faststart", str(video)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    u, v = np.meshgrid(np.arange(W, dtype=np.float32) - W / 2, np.arange(H, dtype=np.float32) - H / 2)
    prev_tail: list[np.ndarray] = []
    try:
        for si, shot in enumerate(shots):
            last = si == len(shots) - 1
            kind = TRANSITIONS[(first + si - 1) % len(TRANSITIONS)]  # how this shot comes in
            tail = []
            for i, frame in enumerate(shot.frames(u, v)):
                if si == 0 and i == round(0.9 * FPS):
                    Image.fromarray(frame).save(cover, "JPEG", quality=90)  # hook text fully visible
                if prev_tail and i < TRANS:
                    frame = transition(prev_tail[i], frame, (i + 1) / (TRANS + 1), kind)
                if not last and i >= shot.n - TRANS:
                    tail.append(frame)
                    continue
                proc.stdin.write(frame.tobytes())
            prev_tail = tail
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed: {proc.stderr.read().decode(errors='replace')[:500]}")
    finally:
        if proc.poll() is None:
            proc.kill()

    reel = {
        "video": video.relative_to(ROOT).as_posix(),
        "cover": cover.relative_to(ROOT).as_posix(),
        "mode": mode,
        "duration": round(duration, 1),
        "title_behind_subject": shots[0].title is not None,
        "route_map": has_map,
    }
    if track:
        reel["music"] = {k: track.get(k) for k in ("id", "file", "title", "artist", "source", "license", "link",
                                                   "bpm", "start", "mood", "instruments") if track.get(k) is not None}
    return reel

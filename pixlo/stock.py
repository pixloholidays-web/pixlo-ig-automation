"""Photo sourcing: your own photos first, then Unsplash, then Pexels. A photo is never reused.

For each search, the top results are scored for how eye-catching they are (colour, contrast, exposure and,
on Unsplash, how many people liked the photo) and the best one is used. Grey, dull or dark photos are skipped.
"""
import math
import re
from io import BytesIO
from pathlib import Path

import numpy as np
import requests

from .config import ROOT, env

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _own_photos(cfg: dict, destination: str, used: set[str]) -> list[dict]:
    folder = ROOT / cfg["stock"]["own_photos_dir"] / slug(destination)
    if not folder.is_dir():
        return []
    return [{"id": f"own:{p.relative_to(ROOT).as_posix()}", "path": p, "credit": None, "description": p.stem}
            for p in sorted(folder.iterdir())
            if p.suffix.lower() in PHOTO_EXT and f"own:{p.relative_to(ROOT).as_posix()}" not in used]


def _unsplash(query: str, min_width: int) -> list[dict]:
    key = env("UNSPLASH_ACCESS_KEY")
    if not key:
        return []
    resp = requests.get("https://api.unsplash.com/search/photos", timeout=30,
                        headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1"},
                        params={"query": query, "orientation": "portrait", "per_page": 15,
                                "content_filter": "high"})
    resp.raise_for_status()
    return [{
        "id": f"unsplash:{p['id']}",
        "url": p["urls"]["raw"] + "&w=2160&q=85&fm=jpg&fit=max",
        "download_location": p["links"]["download_location"],
        "thumb": p["urls"]["small"],
        "likes": p.get("likes") or 0,
        "description": p.get("alt_description") or p.get("description") or query,
        "credit": {"name": p["user"]["name"], "source": "Unsplash",
                   "link": p["user"]["links"]["html"] + "?utm_source=pixlo&utm_medium=referral"},
    } for p in resp.json()["results"] if p["width"] >= min_width]


def _pexels(query: str, min_width: int) -> list[dict]:
    key = env("PEXELS_API_KEY")
    if not key:
        return []
    resp = requests.get("https://api.pexels.com/v1/search", timeout=30, headers={"Authorization": key},
                        params={"query": query, "orientation": "portrait", "per_page": 15})
    resp.raise_for_status()
    return [{
        "id": f"pexels:{p['id']}",
        "url": p["src"]["original"] + "?auto=compress&w=2160",
        "thumb": p["src"]["medium"],
        "description": p.get("alt") or query,
        "credit": {"name": p["photographer"], "source": "Pexels", "link": p["photographer_url"]},
    } for p in resp.json()["photos"] if p["width"] >= min_width]


PROVIDERS = {"unsplash": _unsplash, "pexels": _pexels}
CANDIDATES = 6  # results per search that get scored


def eye_catch(img) -> float:
    """0-100: how much a photo pops in a feed. Colourfulness (Hasler & Suesstrunk) and contrast count most;
    grey, dark, washed-out or blown-out photos score low."""
    from PIL import Image
    if not isinstance(img, Image.Image):
        img = Image.open(img)
    x = np.asarray(img.convert("RGB").resize((256, 256)), np.float32)
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    rg, yb = r - g, 0.5 * (r + g) - b
    colourful = math.hypot(rg.std(), yb.std()) + 0.3 * math.hypot(rg.mean(), yb.mean())
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    contrast = float(lum.std())
    exposure = 1 - min(1.0, abs(float(lum.mean()) - 125) / 125)       # mid-tones are best
    clipped = float(((lum < 8) | (lum > 248)).mean())
    score = 0.65 * min(colourful, 80) + 0.4 * min(contrast, 80) + 15 * exposure - 60 * clipped
    return round(max(0.0, min(100.0, score)), 1)


def _dull(score: float) -> bool:
    return score < 25


def _best(hits: list[dict]) -> dict | None:
    """The most eye-catching of the first few hits (Unsplash likes give a small bonus)."""
    from PIL import Image
    scored = []
    for h in hits[:CANDIDATES]:
        try:
            resp = requests.get(h["thumb"], timeout=30)
            resp.raise_for_status()
            h["score"] = eye_catch(Image.open(BytesIO(resp.content)))
        except Exception:
            continue
        if not _dull(h["score"]):
            scored.append((h["score"] + 6 * math.log10(1 + h.get("likes", 0)), h))
    return max(scored, key=lambda s: s[0])[1] if scored else None


def _download(photo: dict, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / (photo["id"].replace(":", "_") + ".jpg")
    if not path.exists():
        resp = requests.get(photo["url"], timeout=60)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    if photo.get("download_location"):
        # Unsplash API guideline: register the download whenever a photo is used
        requests.get(photo["download_location"], timeout=30,
                     headers={"Authorization": f"Client-ID {env('UNSPLASH_ACCESS_KEY')}"})
    return path


def find_photos(cfg: dict, destination: str, queries: list[str], count: int, used: set[str],
                cache_dir: Path) -> list[dict]:
    """Return up to `count` photos as {id, path, credit, description}, one per query where possible."""
    chosen = _own_photos(cfg, destination, used)[:count]
    for p in chosen:
        p["score"] = eye_catch(p["path"])
    taken = used | {p["id"] for p in chosen}
    min_width = cfg["stock"]["min_width"]

    for query in queries:
        if len(chosen) >= count:
            break
        for provider in cfg["stock"]["providers"]:
            hits = [h for h in PROVIDERS[provider](query, min_width) if h["id"] not in taken]
            photo = _best(hits)
            if photo:
                photo["path"] = _download(photo, cache_dir)
                photo["query"] = query
                chosen.append(photo)
                taken.add(photo["id"])
                break

    # Second pass with broader queries if specific scenes found nothing
    for query in (destination, f"{destination} travel", f"{destination} landscape"):
        if len(chosen) >= count:
            break
        for provider in cfg["stock"]["providers"]:
            hits = [h for h in PROVIDERS[provider](query, min_width) if h["id"] not in taken]
            while hits and len(chosen) < count:
                photo = _best(hits)
                if not photo:
                    break
                hits = [h for h in hits if h["id"] != photo["id"]]
                photo["path"] = _download(photo, cache_dir)
                photo["query"] = query
                chosen.append(photo)
                taken.add(photo["id"])
    return chosen


def placeholder_photos(cfg: dict, destination: str, count: int, used: set[str], cache_dir: Path) -> list[dict]:
    """Offline mode: your own photos if present, else generated gradient placeholders."""
    from PIL import Image, ImageDraw
    chosen = _own_photos(cfg, destination, used)[:count]
    palette = [(14, 124, 134), (255, 183, 3), (34, 87, 122), (230, 111, 81), (42, 157, 143), (90, 60, 130)]
    cache_dir.mkdir(parents=True, exist_ok=True)
    for i in range(len(chosen), count):
        path = cache_dir / f"placeholder_{i}.jpg"
        top, bottom = palette[i % len(palette)], (11, 31, 42)
        img = Image.new("RGB", (1080, 1350))
        draw = ImageDraw.Draw(img)
        for y in range(1350):
            t = y / 1349
            draw.line([(0, y), (1080, y)], fill=tuple(int(top[c] * (1 - t) + bottom[c] * t) for c in range(3)))
        img.save(path, "JPEG", quality=90)
        chosen.append({"id": f"placeholder:{i}", "path": path, "credit": None,
                       "description": f"placeholder {i}"})
    return chosen

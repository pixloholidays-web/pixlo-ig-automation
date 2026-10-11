"""Post files: one YAML file per post in content/posts/.

Lifecycle:  scheduled -> published   (or -> failed after max_attempts)
Posts are generated straight onto main with status 'scheduled'; the quality gates in
quality.py stand in for human approval. Set status to 'skip' to cancel a post.
"""
from datetime import datetime
from pathlib import Path

import yaml

from .config import ROOT


def _str_presenter(dumper, data):
    # Multi-line captions as readable "|" blocks, so they're easy to edit
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


yaml.SafeDumper.add_representer(str, _str_presenter)

POSTS_DIR = ROOT / "content" / "posts"
IG_CAPTION_LIMIT = 2200
IG_HASHTAG_LIMIT = 5        # Instagram's cap since Dec 2025; extras are ignored
IG_CAROUSEL_MAX = 10
DONE = ("published", "skip")  # nothing left to publish


def load_all() -> list[tuple[Path, dict]]:
    posts = []
    for path in sorted(POSTS_DIR.glob("*.yaml")):
        with open(path, encoding="utf-8") as f:
            posts.append((path, yaml.safe_load(f)))
    return posts


def save(path: Path, post: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(post, f, sort_keys=False, allow_unicode=True, width=1000)


def scheduled_at(post: dict) -> datetime:
    return datetime.fromisoformat(str(post["scheduled_at"]))


def images(post: dict) -> list[str]:
    return post.get("images") or ([post["image"]] if post.get("image") else [])


def photo_file(photo: dict) -> Path:
    """Local file of a saved photo (older posts didn't store 'file')."""
    if photo.get("file"):
        return ROOT / photo["file"]
    source, _, ident = photo["id"].partition(":")
    if source == "own":
        return ROOT / ident
    return ROOT / "media" / "cache" / f"{source}_{ident}.jpg"


def credits_line(post: dict) -> str:
    by_source: dict[str, list[str]] = {}
    for ph in post.get("photos", []):
        c = ph.get("credit")
        if c and c["name"] not in by_source.setdefault(c["source"], []):
            by_source[c["source"]].append(c["name"])
    parts = [f"{src}: {', '.join(names)}" for src, names in by_source.items() if names]
    return ("📷 " + " · ".join(parts)) if parts else ""


def full_caption(post: dict) -> str:
    tags = " ".join("#" + h.lstrip("#").replace(" ", "") for h in post.get("hashtags", []))
    parts = [post["caption"].strip(), credits_line(post), tags]
    return "\n\n".join(p for p in parts if p)


def validate(post: dict) -> list[str]:
    errors = []
    for key in ("id", "scheduled_at", "status", "caption"):
        if not post.get(key):
            errors.append(f"missing '{key}'")
    try:
        if scheduled_at(post).tzinfo is None:
            errors.append("scheduled_at must include a timezone offset")
    except (KeyError, ValueError):
        errors.append("scheduled_at is not a valid ISO datetime")
    if post.get("caption") and len(full_caption(post)) > IG_CAPTION_LIMIT:
        errors.append(f"caption + credits + hashtags exceed {IG_CAPTION_LIMIT} characters")
    if len(post.get("hashtags", [])) > IG_HASHTAG_LIMIT:
        errors.append(f"more than {IG_HASHTAG_LIMIT} hashtags")
    imgs = images(post)
    if not imgs:
        errors.append("no images")
    if len(imgs) > IG_CAROUSEL_MAX:
        errors.append(f"more than {IG_CAROUSEL_MAX} carousel images")
    if post.get("status") not in DONE:  # media of published posts is deleted (see mediastore.py)
        for img in imgs:
            if not (ROOT / img).exists():
                errors.append(f"image not found: {img}")
    return errors


REEL_MAX_SECONDS = 90  # Graph API limit for Reels


def reel_caption(post: dict) -> str:
    """Reel caption: the post's caption (+ a pointer to the carousel when one is published), music credit,
    photo credits, hashtags."""
    lines = post["caption"].strip().split("\n")
    pointer = "Pura breakdown (costs ke saath) humare carousel mein, profile pe 📌"
    if post.get("status") == "skip":  # Reels only: there is no carousel to point to
        body = "\n".join(lines)
    elif lines and "DM" in lines[-1]:  # keep the DM line last
        body = "\n".join(lines[:-1]).rstrip() + f"\n{pointer}\n\n{lines[-1]}"
    else:
        body = "\n".join(lines) + f"\n\n{pointer}"
    music = (post.get("reel") or {}).get("music")
    if music and music.get("source") == "original":
        music = None  # our own composition: nothing to credit
    # Credit for tracks you added yourself: title, author, source, licence
    music_line = (f"🎵 \"{music.get('title', '')}\" by {music.get('artist', 'unknown')} · "
                  + " · ".join(x for x in (music.get("source"), music.get("license")) if x)
                  if music else "")
    tags = " ".join("#" + h.lstrip("#").replace(" ", "") for h in post.get("hashtags", []))
    return "\n\n".join(x for x in (body, music_line, credits_line(post), tags) if x)


def validate_reel(post: dict) -> list[str]:
    r = post.get("reel") or {}
    errors = []
    for key in ("video", "cover", "scheduled_at", "status"):
        if not r.get(key):
            errors.append(f"reel missing '{key}'")
    if r.get("status") not in DONE:
        for key in ("video", "cover"):
            if r.get(key) and not (ROOT / r[key]).exists():
                errors.append(f"reel {key} not found: {r[key]}")
    if r.get("duration", 0) > REEL_MAX_SECONDS:
        errors.append(f"reel longer than {REEL_MAX_SECONDS}s")
    if post.get("caption") and len(reel_caption(post)) > IG_CAPTION_LIMIT:
        errors.append(f"reel caption exceeds {IG_CAPTION_LIMIT} characters")
    return errors

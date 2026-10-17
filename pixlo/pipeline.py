"""One carousel post, end to end: plan, research, photos, copy, quality gates, render, save."""
import random
from datetime import datetime, timedelta
from pathlib import Path

from . import carousel, copywriter, planner, posts, quality, research, stock
from .config import ROOT


class QualityError(RuntimeError):
    pass


def make_carousel(cfg: dict, when: datetime, offline: bool = False, pillar_name: str | None = None,
                  destination: str | None = None) -> str:
    existing = [p for _, p in posts.load_all()]
    pillar = (next(p for p in cfg["pillars"] if p["name"] == pillar_name) if pillar_name
              else planner.pick_pillar(cfg, existing))
    shortlist = [destination] if destination else planner.destination_shortlist(cfg, existing)
    month = when.strftime("%B %Y")
    print(f"  · {when:%a %d %b %H:%M}  pillar={pillar['name']}  shortlist={', '.join(shortlist)}")

    # 1. Research (live web search, facts with sources)
    researcher = research.offline_research if offline else research.research
    facts = researcher(cfg, pillar, shortlist, month, planner.recent_topics(cfg, existing))
    print(f"    topic: {facts['topic']}")

    # 2. Photos (own > Unsplash > Pexels, never reused)
    want = cfg["carousel"]["max_slides"]
    used = planner.used_photo_ids(existing)
    cache = ROOT / "media" / "cache"
    if offline:
        photos = stock.placeholder_photos(cfg, facts["destination"], want, used, cache)
    else:
        photos = stock.find_photos(cfg, facts["destination"], facts["photo_queries"], want, used, cache)
    if len(photos) < cfg["carousel"]["min_slides"] - 1:
        raise QualityError(f"only {len(photos)} usable photos found for {facts['destination']}")

    # 3. Copy + quality gates, with rewrites
    writer = copywriter.offline_carousel if offline else copywriter.write_carousel
    feedback, report, fallback = None, {}, None
    floor = cfg["quality"].get("fallback_hook_score", cfg["quality"]["min_hook_score"])
    for attempt in range(cfg["quality"]["max_rewrites"] + 1):
        copy = writer(cfg, facts, photos, feedback)
        passed, feedback, report = quality.review(cfg, facts, copy, len(photos), offline)
        print(f"    draft {attempt + 1}: hook {report.get('hook_score', '-')}/10, "
              f"{'PASS' if passed else 'rewrite'}")
        if passed:
            break
        # Clean apart from a slightly weaker hook: keep the best such draft in case none reaches the target
        if (not report.get("rule_problems") and report.get("hard_problems") == []
                and report.get("hook_score", 0) >= floor
                and (fallback is None or report["hook_score"] > fallback[1]["hook_score"])):
            fallback = (copy, report)
    else:
        if not fallback:
            raise QualityError(f"failed quality gates after {attempt + 1} drafts:\n{feedback}")
        copy, report = fallback
        print(f"    using the best clean draft (hook {report['hook_score']}/10)")

    # Polish: one more rewrite for the editor's remaining notes, kept only if it still passes
    if not offline and cfg["quality"].get("polish") and report.get("issues"):
        polished = writer(cfg, facts, photos, feedback)
        ok, _, polished_report = quality.review(cfg, facts, polished, len(photos), offline)
        keep = ok and polished_report.get("hook_score", 0) >= report.get("hook_score", 0)
        print(f"    polish: hook {polished_report.get('hook_score', '-')}/10, {'kept' if keep else 'discarded'}")
        if keep:
            copy, report = polished, polished_report

    # 4. Render slides
    post_id = f"{when:%Y-%m-%d-%H%M}-{stock.slug(facts['destination'])}"
    last_style = existing[-1].get("style") if existing else None
    style = random.choice([s for s in carousel.STYLES if s != last_style])
    out_dir = ROOT / "media" / "posts" / post_id
    images = carousel.render(cfg, copy["slides"], photos, style, out_dir)

    # Save only the photos actually used; slide["photo"] then indexes post["photos"]
    used = sorted({s["photo"] for s in copy["slides"]})
    remap = {old: new for new, old in enumerate(used)}
    slides = [dict(s, photo=remap[s["photo"]]) for s in copy["slides"]]
    post = {
        "id": post_id,
        "type": "carousel",
        "pillar": pillar["name"],
        "destination": facts["destination"],
        "topic": facts["topic"],
        "scheduled_at": when.isoformat(),
        # Reels-only mode: the slides only script the Reel, so the image post itself is never published
        "status": "scheduled" if carousels_published(cfg) else "skip",
        "style": style,
        "images": [p.relative_to(ROOT).as_posix() for p in images],
        "slides": slides,
        "caption": copy["caption"],
        "hashtags": copy["hashtags"],
        "alt_text": copy["alt_text"],
        "music_mood": copy.get("music_mood"),
        "route": research.route_of(facts),
        "photos": [{"id": photos[i]["id"], "file": photos[i]["path"].relative_to(ROOT).as_posix(),
                    "credit": photos[i].get("credit")} for i in used],
        "sources": sorted({f["source_url"] for f in facts["facts"]}),
        "review": {k: v for k, v in report.items() if k in ("hook_score", "issues")},
        "generated_by": "offline-template" if offline else cfg["copy"]["model"],
    }
    if cfg.get("reels", {}).get("enabled"):
        try:
            # Offline test runs: silent kit, so they finish fast
            reel_cfg = {**cfg, "reels": {**cfg["reels"], "offline": True, "music_fallback": None}} if offline else cfg
            add_reel(reel_cfg, post)
            r = post["reel"]
            print(f"    + reel {r['duration']}s, {r['mode']}, {posts.scheduled_at(r):%a %d %b %H:%M}"
                  f"{', music: ' + r['music']['title'] if r.get('music') else ', no music'}")
        except Exception as exc:  # a failed reel must not lose the carousel
            if not carousels_published(cfg):
                raise QualityError(f"reel failed: {exc}")  # Reels only: nothing to post, leave the slot free
            print(f"    ! reel failed: {exc}")
    path = posts.POSTS_DIR / f"{post_id}.yaml"
    posts.save(path, post)
    print(f"    + {path.relative_to(ROOT)}  ({len(images)} slides, style {style})")
    return post_id


WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def carousels_published(cfg: dict) -> bool:
    return cfg["carousel"].get("publish", True)


def add_reel(cfg: dict, post: dict, mode: str | None = None, when: datetime | None = None) -> dict:
    """Render the post's Reel and schedule it: at the post's own slot in Reels-only mode, otherwise
    offset_days after the carousel at reels.time. Reels on kit_weekdays are silent kits to finish in Edits."""
    from . import reel
    rc = cfg["reels"]
    if when is None and not carousels_published(cfg):
        when = posts.scheduled_at(post)  # Reels only: the Reel takes the schedule slot itself
    if when is None:
        hh, mm = map(int, rc["time"].split(":"))
        when = (posts.scheduled_at(post) + timedelta(days=rc["offset_days"])).replace(
            hour=hh, minute=mm, second=0, microsecond=0)
        carousel_days = {s["weekday"] for s in cfg["schedule"]["slots"]}
        while WEEKDAYS[when.weekday()] in carousel_days and len(carousel_days) < 7:
            when += timedelta(days=1)  # keep Reels off carousel days so they don't compete
    if mode is None:
        mode = "kit" if WEEKDAYS[when.weekday()] in rc["kit_weekdays"] else "auto"
    info = reel.build(cfg, post, mode)
    if mode == "auto" and not info.get("music"):
        # Never auto-publish a silent Reel: hand it over as a kit instead
        print("    ! no tracks in assets/music/library.yaml; saved as a silent kit instead of auto-publishing")
        info["mode"] = mode = "kit"
    info.update(scheduled_at=when.isoformat(), status="scheduled" if mode == "auto" else "kit_ready")
    post["reel"] = info
    return info


def rerender(cfg: dict, post: dict) -> list[Path]:
    """Re-draw a post's slides from its YAML (after editing slide text, or after renderer changes)."""
    photos = [{"path": posts.photo_file(ph)} for ph in post["photos"]]
    missing = [p["path"].relative_to(ROOT).as_posix() for p in photos if not p["path"].exists()]
    if missing:
        raise FileNotFoundError(f"photo files missing (media/cache is local-only): {', '.join(missing)}")
    out_dir = ROOT / "media" / "posts" / post["id"]
    for old in out_dir.glob("*.jpg"):
        old.unlink()
    images = carousel.render(cfg, post["slides"], photos, post.get("style", "classic"), out_dir)
    post["images"] = [p.relative_to(ROOT).as_posix() for p in images]
    return images

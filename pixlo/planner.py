"""What to post next: rotate pillars and destinations so the feed stays varied."""
from collections import Counter


def recent(posts: list[dict], n: int) -> list[dict]:
    return sorted(posts, key=lambda p: str(p.get("scheduled_at")))[-n:]


def pick_pillar(cfg: dict, posts: list[dict]) -> dict:
    window = recent(posts, cfg["quality"]["topic_memory"])
    use = Counter(p.get("pillar") for p in window)
    last = window[-1].get("pillar") if window else None
    # Least-used pillar, never the same pillar twice in a row
    candidates = [p for p in cfg["pillars"] if p["name"] != last] or cfg["pillars"]
    return min(candidates, key=lambda p: use[p["name"]])


def destination_shortlist(cfg: dict, posts: list[dict], size: int = 5) -> list[str]:
    last_used = {}
    for i, p in enumerate(sorted(posts, key=lambda p: str(p.get("scheduled_at")))):
        last_used[p.get("destination")] = i
    # Never-used first, then the longest-unused
    return sorted(cfg["destinations"], key=lambda d: last_used.get(d, -1))[:size]


def recent_topics(cfg: dict, posts: list[dict]) -> list[str]:
    return [p["topic"] for p in recent(posts, cfg["quality"]["topic_memory"]) if p.get("topic")]


def used_photo_ids(posts: list[dict]) -> set[str]:
    return {ph["id"] for p in posts for ph in p.get("photos", [])}

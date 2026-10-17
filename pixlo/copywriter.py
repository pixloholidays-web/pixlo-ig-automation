"""Slides (the Reel's script), caption and hashtags, written by Claude while looking at the actual photos."""
from . import llm
from .config import ROOT
from .research import facts_text

SYSTEM = """You write Instagram carousels for {brand} ({handle}). Niche: {niche}.
Tone: {tone}.
Language: {language}. Write plain, natural, conversational English the way a well-travelled Indian friend
would text it: short sentences, everyday words, no Hindi or Hinglish words, no travel-brochure language.
Keep place names, numbers and ₹ amounts exact.

What makes this page grow: people SEND posts to friends ("we should go here") and SAVE them for
later. Everything you write should earn a send or a save through useful, specific information, never through asking for it.

CAROUSEL RULES
- {min_slides}-{max_slides} slides. Slide 1 kind "cover", the last slide kind "cta", all others "info".
- Cover: title is the hook, at most 10 words. Create a curiosity gap or promise a specific payoff
  (number, price, secret, mistake). Don't give the answer away. body is an optional subtitle of at most 10 words, or "".
- Info slides: ONE idea per slide. title at most 6 words; body at most 25 words and at most 3 numbers,
  with concrete details (names, ₹ ranges, timings, distances). No fluff adjectives. Extra numbers go in the caption.
- Every claim on slides and in the caption must come from the research facts or time-sensitive notes.
  Do not add facts of your own, and don't promise what the facts can't back (e.g. "exact timings" from old sources).
- ORDER: when the post is a sequence (a day-by-day or hour-by-hour itinerary, steps, a route, a numbered
  list), the slides MUST follow that sequence from start to finish: Night 0, Day 1, Day 2, Day 3. Never
  pull a later day forward, not even to pay off the cover; the cover's promise is then paid off when its
  day arrives, and the cover body can say which day that is. Practical extras that belong to no single day
  (where to stay, total cost) go after the last day, just before the CTA.
  Only when the post has no natural sequence: the slide right after the cover pays off the cover's promise.
- CTA slide: title like "Save this for your trip"; body: one line on why to follow {handle}.
- No emojis or hashtags on slides; they can't be rendered.
- photo: index of the photo for that slide. Use the most striking photo on the cover. Match each info
  slide to a photo that actually shows what the slide talks about where possible; don't reuse a photo
  unless there are fewer photos than slides.
- Never put a named landmark ("Chehni Kothi", "Serolsar Lake") over a photo of a different place that
  merely looks similar; followers notice. If no photo shows that landmark, use a general scene of the
  valley and keep the slide about the information.
- photo_shows: a few honest words on what that photo actually shows ("temple roof, carved wood").
- text_position: "top" or "bottom", whichever covers the less important part of that photo (sky, water, ground),
  never faces or the main subject.

REEL RULES (the same slides also become a ~20 s Reel, about 2.3 s per shot, mostly watched muted)
- reel_text: the on-screen line for that shot, at most 7 words, readable in 2 seconds, standalone.
  In an itinerary or numbered list, start it with the same label as the slide ("Day 2: ...", "3. ...") so a
  viewer always knows where they are in the sequence.
  Cover: the hook itself (can equal the title if it's short). Info: the single most striking fact
  ("₹30 bus, 9 km uphill"). CTA: "Save this for your trip" or similar.
- music_mood: the feel of the background track: calm, upbeat, romantic, adventure or epic.

CAPTION RULES
- Line 1: a search-friendly hook containing the destination and topic keywords (Instagram search indexes captions).
- Then 3-6 short lines adding value beyond the slides (one extra tip, best time, budget note).
- Include any time-sensitive caveat from the research.
- End with this line on its own: Planning this trip? DM us "TRIP"
- Up to 4 emojis. No hashtags in the caption. No engagement bait: never ask people to send, share, tag,
  like or comment ("send this to your crew", "tag a friend", "share if", "comment yes"), on slides or in the caption.
- hashtags: exactly {max_hashtags}, without "#": destination, specific topic, broad Indian travel, niche. No generic tags like "travel" or "instagood".
- alt_text: one plain sentence describing the cover photo for screen readers."""

SCHEMA = {
    "type": "object",
    "properties": {
        "slides": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["cover", "info", "cta"]},
                "photo": {"type": "integer"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "text_position": {"type": "string", "enum": ["top", "bottom"]},
                "photo_shows": {"type": "string"},
                "reel_text": {"type": "string"},
            },
            "required": ["kind", "photo", "title", "body", "text_position", "photo_shows", "reel_text"],
            "additionalProperties": False,
        }},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "alt_text": {"type": "string"},
        "music_mood": {"type": "string", "enum": ["calm", "upbeat", "romantic", "adventure", "epic"]},
    },
    "required": ["slides", "caption", "hashtags", "alt_text", "music_mood"],
    "additionalProperties": False,
}


def _system(cfg: dict) -> str:
    c, b = cfg["copy"], cfg["brand"]
    return SYSTEM.format(brand=b["name"], handle=b["handle"], niche=c["niche"], tone=c["tone"],
                         language=c["language"], max_hashtags=c["max_hashtags"],
                         min_slides=cfg["carousel"]["min_slides"], max_slides=cfg["carousel"]["max_slides"])


def write_carousel(cfg: dict, research: dict, photos: list[dict], feedback: str | None = None) -> dict:
    photo_list = "\n".join(f"Photo {i}: {p['path'].relative_to(ROOT).as_posix()} ({p['description']})"
                           for i, p in enumerate(photos))
    prompt = (
        "First, open every photo below with the Read tool and look at it. The slide text must match what is\n"
        f"actually in each photo, and text_position must avoid its subject.\n{photo_list}\n\n"
        f"Pillar: {research['pillar']}\nDestination: {research['destination']}\n"
        f"Topic: {research['topic']}\nHook idea: {research['hook_idea']}\n"
        f"Time-sensitive: {research.get('time_sensitive') or 'none'}\n"
        f"Research facts (the only facts you may use):\n{facts_text(research)}\n\n"
        "Write the carousel."
        + (f"\n\nAn editor rejected the previous draft. Fix these issues:\n{feedback}" if feedback else "")
    )
    data = llm.run(cfg, prompt, _system(cfg), SCHEMA, tools=["Read"], max_turns=20)
    data["hashtags"] = [h.lstrip("#").replace(" ", "") for h in data["hashtags"]][: cfg["copy"]["max_hashtags"]]
    return data


def offline_carousel(cfg: dict, research: dict, photos: list[dict], feedback: str | None = None) -> dict:
    """Template copy for testing the pipeline without an API key."""
    dest = research["destination"]
    n = min(cfg["carousel"]["min_slides"], len(photos))
    slides = [{"kind": "cover", "photo": 0, "title": f"4 {dest} secrets nobody tells you",
               "body": "Save this for your trip", "text_position": "bottom", "photo_shows": "placeholder"}]
    for i in range(1, n - 1):
        slides.append({"kind": "info", "photo": i % len(photos), "title": f"{i}. Secret spot number {i}",
                       "body": f"Offline placeholder text: {research['facts'][i - 1]['claim']}. "
                               "A real run puts the exact details here.",
                       "text_position": "bottom" if i % 2 else "top", "photo_shows": "placeholder"})
    slides.append({"kind": "cta", "photo": (n - 1) % len(photos), "title": "Save this for your trip",
                   "body": f"Follow {cfg['brand']['handle']} for real budgets and hidden places",
                   "text_position": "bottom", "photo_shows": "placeholder"})
    for s in slides:
        s["reel_text"] = " ".join(s["title"].split()[:7])
    return {
        "slides": slides,
        "caption": f"Planning a {dest} trip? Read this first.\n\n[offline placeholder caption]\n\n"
                   "Planning this trip? DM us \"TRIP\"",
        "hashtags": [dest.replace(" ", "").lower(), "indiatravel", "budgettravel", "hiddengems", "travelindia"],
        "alt_text": f"Placeholder cover image for {dest}",
        "music_mood": "adventure",
    }

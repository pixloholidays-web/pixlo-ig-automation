"""Quality gates that replace human approval. A post that fails is not scheduled."""
import re

from . import llm
from .research import facts_text

IG_CAPTION_LIMIT = 2200
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")

REVIEW_SYSTEM = """You are a strict editor for an Instagram travel page that grows on sends and saves.
Review the carousel draft against the research facts. Be harsh: a weak post costs the account reach.

Score and check:
- hook_score (1-10): would a 25-year-old Indian traveller stop scrolling at the cover and swipe?
  10 = specific, surprising, high-stakes payoff. 5 = generic ("Best places in X"). 1 = boring.
- unsupported_claims: any place, number, price, timing or rule on the slides or caption that is NOT
  backed by the research facts. Copy the exact phrase. Exaggerating a sourced fact ("the best" when the
  source says "one of the best") and invented specifics (a day of the week, "exact" for old data) count too.
- photo_mismatch: slides whose named landmark is shown on a photo of a different place (from the photo notes).
- engagement_bait: true if it asks for tags, shares, likes or comment-for-reward.
- issues: anything else that would hurt watch time, saves or sends: slides that repeat each other,
  vague filler, awkward or unnatural English, any Hindi or Hinglish words, text too long for a slide, a cover that gives the answer away.
  List at most 5 issues, the ones that matter most for reach, most important first. Each is one concrete
  fix instruction. Ignore nitpicks: a draft with a strong hook, no unsupported claims and no bait should pass."""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "hook_score": {"type": "integer"},
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "engagement_bait": {"type": "boolean"},
        "photo_mismatch": {"type": "array", "items": {"type": "string"}},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hook_score", "unsupported_claims", "engagement_bait", "photo_mismatch", "issues"],
    "additionalProperties": False,
}


def strip_emoji(text: str) -> str:
    return EMOJI.sub("", text).strip()


def rule_check(cfg: dict, copy: dict, photo_count: int) -> list[str]:
    """Deterministic checks. Returns a list of problems (empty = OK)."""
    errors = []
    slides = copy["slides"]
    c = cfg["carousel"]
    if not c["min_slides"] <= len(slides) <= c["max_slides"]:
        errors.append(f"{len(slides)} slides; need {c['min_slides']}-{c['max_slides']}")
    if slides and slides[0]["kind"] != "cover":
        errors.append("first slide must be the cover")
    if slides and slides[-1]["kind"] != "cta":
        errors.append("last slide must be the CTA")
    for i, s in enumerate(slides, 1):
        if not 0 <= s["photo"] < photo_count:
            errors.append(f"slide {i}: photo index {s['photo']} out of range")
        if len(s["title"].split()) > (10 if s["kind"] == "cover" else 9):
            errors.append(f"slide {i}: title too long ({s['title']!r})")
        if len(s.get("reel_text", "").split()) > 9:
            errors.append(f"slide {i}: reel_text too long ({s['reel_text']!r}, max 7 words)")
        if len(s["body"].split()) > 35:
            errors.append(f"slide {i}: body too long ({len(s['body'].split())} words, max 25)")
    text = " ".join([copy["caption"]] + [s["title"] + " " + s["body"] for s in slides]).lower()
    for phrase in cfg["quality"]["banned_phrases"]:
        if phrase in text:
            errors.append(f"engagement bait: '{phrase}'")
    if len(copy["hashtags"]) > cfg["copy"]["max_hashtags"]:
        errors.append(f"more than {cfg['copy']['max_hashtags']} hashtags")
    if len(copy["caption"]) > IG_CAPTION_LIMIT - 400:  # room for hashtags and photo credits
        errors.append("caption too long")
    return errors


def ai_review(cfg: dict, research: dict, copy: dict) -> dict:
    slides = "\n".join(f"{i}. [{s['kind']}] {s['title']} | {s['body']}  (photo: {s.get('photo_shows', '?')})"
                       for i, s in enumerate(copy["slides"], 1))
    prompt = (f"Research facts:\n{facts_text(research)}\n\nDRAFT\nSlides:\n{slides}\n\n"
              f"Caption:\n{copy['caption']}\n\nHashtags: {' '.join(copy['hashtags'])}")
    return llm.run(cfg, prompt, REVIEW_SYSTEM, REVIEW_SCHEMA, effort="medium", max_turns=5)


def review(cfg: dict, research: dict, copy: dict, photo_count: int, offline: bool = False) -> tuple[bool, str, dict]:
    """Returns (passed, feedback_for_rewrite, report)."""
    for s in copy["slides"]:
        s["title"], s["body"] = strip_emoji(s["title"]), strip_emoji(s["body"])
    problems = rule_check(cfg, copy, photo_count)
    report = {"rule_problems": problems}
    if not offline and not problems:  # fix cheap rule problems first, then spend an AI review
        ai = ai_review(cfg, research, copy)
        report.update(ai)
        if ai["hook_score"] < cfg["quality"]["min_hook_score"]:
            problems.append(f"hook scored {ai['hook_score']}/10; write a more specific, surprising cover hook")
        problems += [f"unsupported claim, remove or fix: {c}" for c in ai["unsupported_claims"]]
        if ai["engagement_bait"]:
            problems.append("remove engagement bait")
        problems += [f"wrong photo for a named place, pick another or reword: {m}" for m in ai["photo_mismatch"]]
        # Minor issues are passed along on rewrites but don't fail a post by themselves
        report["issues"] = ai["issues"][:5]
    feedback = "\n".join(f"- {p}" for p in problems + report.get("issues", []))
    return not problems, feedback, report

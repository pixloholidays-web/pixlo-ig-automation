"""Topic research with live web search. Every fact that goes on a slide must come from here."""
import json

from . import llm

SYSTEM = """You are the research desk for an Instagram travel page. Niche: {niche}.
Audience: Indian travellers aged 20-40, mostly budget to mid-range, travelling from Indian metros.

Your job: pick ONE specific, scroll-stopping topic and gather verified facts for it using web search.
- Prefer recent sources (last 2 years): official tourism sites, government notices, reputable travel publications, recent blogs with dates.
- Prices must be realistic INR ranges for the current year. If sources disagree, give a range and say which.
- Never include a place, price, timing or rule you could not find a source for.
- Note anything time-sensitive (permits, closures, seasonal roads, festival dates).
- The topic must be specific enough to be useful ("Spiti 7 days for ₹22-28k from Delhi"),
  not generic ("Beautiful Spiti").
- It must not repeat or closely resemble any recent topic listed by the user.

Return your result in the required JSON format:
- destination: one of the shortlisted destinations
- topic: the specific post topic, in English
- hook_idea: the curiosity or benefit angle that makes people stop and send it
- facts: 8-15 facts, each a claim with the source_url it came from
- photo_queries: 8-10 stock photo searches, each a concrete visual scene at this destination
  (e.g. "Key Monastery Spiti snow", "Chandratal lake blue", not "travel")
- time_sensitive: caveats to mention, or ""
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "destination": {"type": "string"},
        "topic": {"type": "string"},
        "hook_idea": {"type": "string"},
        "facts": {"type": "array", "items": {
            "type": "object",
            "properties": {"claim": {"type": "string"}, "source_url": {"type": "string"}},
            "required": ["claim", "source_url"],
            "additionalProperties": False,
        }},
        "photo_queries": {"type": "array", "items": {"type": "string"}},
        "time_sensitive": {"type": "string"},
    },
    "required": ["destination", "topic", "hook_idea", "facts", "photo_queries", "time_sensitive"],
    "additionalProperties": False,
}


def research(cfg: dict, pillar: dict, shortlist: list[str], month: str, recent_topics: list[str]) -> dict:
    copy_cfg = cfg["copy"]
    prompt = (
        f"Pillar: {pillar['name']}: {pillar['brief']}\n"
        f"Month of posting: {month}\n"
        f"Destination shortlist (choose the one that fits this pillar and month best): {', '.join(shortlist)}\n"
        f"Recent topics to avoid:\n" + ("\n".join(f"- {t}" for t in recent_topics) or "- none yet")
    )
    data = llm.run(cfg, prompt, SYSTEM.format(niche=copy_cfg["niche"]), SCHEMA,
                   tools=["WebSearch", "WebFetch"], max_turns=40)
    if len(data.get("facts", [])) < 5:
        raise RuntimeError(f"Research found too few sourced facts for '{data.get('topic')}'")
    data["pillar"] = pillar["name"]
    return data


def offline_research(cfg: dict, pillar: dict, shortlist: list[str], month: str, recent_topics: list[str]) -> dict:
    """Placeholder research for testing the pipeline without API calls."""
    dest = shortlist[0]
    return {
        "destination": dest,
        "pillar": pillar["name"],
        "topic": f"{dest}: {pillar['name'].replace('-', ' ')} ({month})",
        "hook_idea": f"What most people get wrong about {dest}",
        "facts": [{"claim": f"[offline placeholder fact {i} about {dest}]", "source_url": "https://example.com"}
                  for i in range(1, 9)],
        "photo_queries": [f"{dest} landscape", f"{dest} street", f"{dest} sunset", f"{dest} food",
                          f"{dest} mountains", f"{dest} local market", f"{dest} aerial", f"{dest} night"],
        "time_sensitive": "",
    }


def facts_text(research_data: dict) -> str:
    text = json.dumps(research_data["facts"], ensure_ascii=False, indent=1)
    if research_data.get("time_sensitive"):
        text += f"\nTime-sensitive notes (also sourced): {research_data['time_sensitive']}"
    return text

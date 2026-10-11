# Pixlo: travel page automation (Instagram + Facebook)

General travel content in Hinglish, built to earn **sends and saves** (the signals Instagram uses to reach non-followers). Carousels are researched, written, checked and published automatically. Every post also becomes a Reel with 2.5D camera motion and original music, published automatically.

```
Daily 06:00 IST  daily.yml ─► planner (pillar × destination × month)
                 ─► research: Claude Code (headless) + web search, every fact saved with its source
                 ─► photos: media/own ▸ Unsplash ▸ Pexels (never reused, photographers credited)
                 ─► Claude looks at the photos and writes slides + caption + 5 hashtags
                 ─► quality gates (rules + AI editor; up to 2 rewrites, else the slot is skipped)
                 ─► render 1080×1350 slides (rotating styles) ─► commit to main
Every 30 min     publish.yml ─► due posts ─► public image URLs ─► IG carousel ─► publish
```

## Reels
Every carousel also becomes a ~20 s Reel (1080×1920), posted on a non-carousel day:
- **2.5D parallax**: Depth Anything V2 (ONNX, CPU) splits each photo into depth layers and a virtual camera
  pushes in, pans or rises, so stills feel like drone/dolly shots. Free, no GPU, about 2 minutes per Reel.
- Short animated text per shot (`reel_text`), the hook fully visible on frame 1, a save/follow end card.
- **Music: original, no copyright risk.** Every Reel gets its own track composed by `pixlo/synth.py`: plucked
  strings, soft pads, bass, a sparse melody and light drums in the post's mood (calm, romantic, adventure,
  upbeat, epic), at a tempo the video cuts follow exactly. No samples or third-party audio are used, so there's
  nothing for a copyright system to match. Audio is normalised to Instagram's ~-14 LUFS. (Optional: tracks you
  own or have licensed can go in `assets/music/`; they're used first when one matches the mood.)
- Videos are about 10-15 MB each. Before going live, switch `hosting.provider` to `s3` (Cloudflare R2) so the
  repo doesn't grow by about 2 GB a year.

## Layout
| Path | What it is |
|---|---|
| `config.yaml` | Brand, niche, pillars, destinations, slots, quality thresholds, kill switch (`paused`) |
| `pixlo/planner.py` | Picks the pillar and destination shortlist, avoiding recent repeats |
| `pixlo/research.py` | Claude with web search: topic, sourced facts, photo search queries |
| `pixlo/stock.py` | Your photos first, then Unsplash, then Pexels |
| `pixlo/copywriter.py` | Slides, caption, hashtags and alt text, written while looking at the photos |
| `pixlo/quality.py` | Rule checks + AI editor: hook score, unsupported claims, engagement bait |
| `pixlo/carousel.py` | Slide renderer (`classic`, `panel`, `bold` styles) |
| `content/posts/*.yaml` | One file per post: `scheduled` → `published` / `failed`. Set `status: skip` to cancel one |
| `media/own/<destination-slug>/` | **Your own photos** (e.g. `media/own/spiti-valley/`). Always used before stock photos |
| `media/posts/<id>/` | Rendered slides and Reel. Not on `main`: kept on the `media` branch until the post is published, then deleted. `python cli.py media-pull` downloads them, `media-sync` uploads new ones |

## Accounts and keys
| Secret | Where to get it |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | `claude setup-token` (see below). Uses your Claude Pro/Max subscription; no API key or API bill |
| `UNSPLASH_ACCESS_KEY` | unsplash.com/developers → New Application (demo tier: 50 requests/hour, plenty) |
| `PEXELS_API_KEY` | pexels.com/api (fallback photos) |
| `IG_USER_ID`, `IG_ACCESS_TOKEN` | Meta setup below |

### Claude Code token (one time)
1. Install the Claude Code CLI. In **PowerShell**: `irm https://claude.ai/install.ps1 | iex`, then open a new terminal and check `claude --version`.
2. Run `claude setup-token`. A browser window opens; approve access with your Claude account.
3. The terminal prints a long token (valid for 1 year). Copy it and add it in GitHub as the secret `CLAUDE_CODE_OAUTH_TOKEN`. It isn't saved anywhere else, so store it safely.

Locally you don't need the token: if `claude` works in your terminal, the pipeline uses that login. Each post uses roughly 5 minutes of Claude Code time from your subscription's usage limits.

### Meta setup (one time)
1. Switch the Instagram account to **Professional → Business** and link it to the Facebook Page.
2. At developers.facebook.com, create an app of type **Business** and add the Instagram product.
3. In **Business Settings → Users → System users**, create an admin system user. Assign it the app, the Page and the Instagram account, then **Generate token** with
   `instagram_basic, instagram_content_publish, pages_show_list, pages_read_engagement, business_management`
   and expiry **Never**.
4. Find the IG user id: `python cli.py whoami`

### GitHub
- Add the secrets above under Settings → Secrets and variables → Actions.
- Image hosting: `github_raw` (default) needs a **public** repo. For a private repo, use `hosting.provider: s3` with Cloudflare R2.

## Local use (Windows)
```powershell
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # fill in Unsplash/Pexels/Instagram keys

python cli.py generate --at "2026-10-06 19:30" --offline     # test the pipeline, no API calls
python cli.py generate --at "2026-10-06 19:30"               # real research + photos + copy
python cli.py generate --at "2026-10-06 19:30" --pillar budget-breakdown --destination "Spiti Valley"
python cli.py plan --days 7
python cli.py preview                                        # Instagram-style preview in your browser
python cli.py reel 2026-10-06-1930-tirthan-valley            # (re)build a post's Reel; --mode kit for a silent one
python cli.py rerender 2026-10-06-1930-tirthan-valley        # redraw slides after editing the post's YAML
python cli.py validate
python cli.py publish --dry-run
```

## Rules this system follows (lessons from accounts that got buried or banned)
- Official Graph API only. No automated likes, follows, comments or scraping.
- No reposting other accounts' content. Every post adds its own information layer.
- Every claim needs a source from the research step; the AI editor rejects anything unsupported.
- At most 5 hashtags (Instagram's limit); search keywords go in the caption's first line.
- No engagement bait ("tag a friend", "share if"); Meta demotes it.
- Ramp up slowly: 3 posts/week for 2 weeks, then 5.
- Emergency stop: set `paused: true` in `config.yaml`.

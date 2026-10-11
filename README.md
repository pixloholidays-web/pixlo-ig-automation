# Pixlo: Instagram travel page automation

General travel content in Hinglish, built to earn **sends and saves** (the signals Instagram uses to reach non-followers). Each post is researched, written, checked, turned into a Reel with 2.5D camera motion and original music, and published automatically.

**Currently Reels only:** no image posts go out (`carousel.publish: false` in `config.yaml`). Set it to `true` to also publish each post as a carousel, with its Reel a day later.

Everything runs on GitHub Actions. Nothing needs to stay switched on at home.

> Facebook Page posting is not built yet. Only Instagram is published.

## Contents
- [How it works](#how-it-works)
- [First-time setup](#first-time-setup)
- [Day-to-day use](#day-to-day-use)
- [Running it on your PC](#running-it-on-your-pc)
- [Settings](#settings)
- [Troubleshooting](#troubleshooting)
- [File layout](#file-layout)
- [Rules this system follows](#rules-this-system-follows)

## How it works
```
Daily 06:00 IST   "Generate posts"
                  ─► planner picks a pillar and a destination shortlist
                  ─► research: Claude Code + web search, every fact saved with its source
                  ─► photos: media/own ▸ Unsplash ▸ Pexels (never reused, photographers credited)
                  ─► Claude looks at the photos and writes slides + caption + 5 hashtags
                  ─► quality check (rules + AI editor; up to 3 rewrites, else the slot is skipped)
                  ─► build a ~20 s Reel from the slides' text and photos
                  ─► post text ─► main branch      Reel ─► media branch

18:30–21:00 IST   "Publish due posts" (every 30 min)
                  ─► posts whose time has come ─► Instagram fetches the files from the media branch
                  ─► publish ─► record the Instagram link ─► delete that post's media once fully published
```

### The four workflows
| Workflow (Actions tab) | When | What it does |
|---|---|---|
| **Generate posts** | Daily, 06:00 IST | Creates a Reel for each slot in the next 2 days |
| **Publish due posts** | Every 30 min, 18:30–21:00 IST | Publishes anything that is due, then deletes media that is no longer needed |
| **Refresh Instagram token** | Mondays, 08:30 IST | Extends the Instagram token by 60 days and saves it as the secret |
| **Check Instagram connection** | Manual only | Confirms the token works. Give it a post id to rehearse that post's upload without publishing |

GitHub often starts scheduled runs late, so a 19:30 post may go out some time after 19:30.

### Reels
Every post becomes a ~20 s Reel (1080×1920), published at its schedule slot. The slides Claude writes are the Reel's script: one shot per slide.
- **2.5D parallax:** Depth Anything V2 (ONNX, CPU) splits each photo into depth layers and a virtual camera pushes in, pans or rises, so stills feel like drone or dolly shots. Free, no GPU, about 2 minutes per Reel.
- **Text:** short animated text per shot (`reel_text`), the hook fully visible on frame 1, a save/follow end card.
- **Music is original, with no copyright risk.** Every Reel gets its own track composed by `pixlo/synth.py` in the post's mood (calm, romantic, adventure, upbeat, epic), at a tempo the video cuts follow. No samples or third-party audio are used. Audio is normalised to about −14 LUFS. Tracks you own can go in `assets/music/` (see its README); they're used first when one matches the mood.

### Where the files live
- **`main` branch:** code, settings and one YAML file per post (`content/posts/`).
- **`media` branch:** the Reel videos and covers (and slides, when carousels are on) that are still waiting to be published. It is always a single commit with no history. Each file is deleted from the branch once it has been published. Instagram keeps its own copy.
- Failed posts keep their media, so they can be retried.

## First-time setup

### 1. Secrets
Add these under the repo's **Settings → Secrets and variables → Actions → New repository secret**, or with `gh secret set NAME -R <owner>/<repo>` (it asks for the value and keeps it hidden).

| Secret | Needed | Where to get it |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | Yes | `claude setup-token` (step 2). Uses your Claude subscription; no API key or API bill |
| `UNSPLASH_ACCESS_KEY` | Yes | unsplash.com/developers → New Application → **Access Key** (the Secret Key is not used) |
| `IG_ACCESS_TOKEN` | Yes | Meta setup (step 3) |
| `GH_SECRETS_TOKEN` | Yes, with an Instagram Login token | GitHub token that lets the refresh workflow save the renewed Instagram token (step 4) |
| `PEXELS_API_KEY` | Optional | pexels.com/api. Backup photo source |
| `IG_USER_ID` | Only for a Facebook-route token | `python cli.py whoami` |

Never put a key in a file that gets committed, in an issue or in a chat. `.env` is for local runs only and is ignored by git.

### 2. Claude Code token
1. Install the Claude Code CLI. In PowerShell: `irm https://claude.ai/install.ps1 | iex`, then open a new terminal and check `claude --version`.
2. Run `claude setup-token`. A browser window opens; approve access with your Claude account.
3. The terminal prints a long token (valid for 1 year). Add it as the secret `CLAUDE_CODE_OAUTH_TOKEN`.

Each post uses roughly 5 minutes of Claude Code time from your subscription's usage limits.

### 3. Instagram token
The Instagram account must be a **Professional** account (Creator or Business).

**Route A: Instagram Login (simplest, no Facebook Page needed)**
1. At developers.facebook.com: **My Apps → Create app**, use case "Manage messaging & content on Instagram".
2. In the app: **Instagram → API setup with Instagram login → Add an Instagram account**, and log in.
3. Click **Generate token** next to the account. The token starts with `IGAA`.
4. Add it as the secret `IG_ACCESS_TOKEN`.

This token lasts 60 days. The **Refresh Instagram token** workflow renews it every week, which needs step 4.

**Route B: Facebook system user (token never expires)**
1. Link the Instagram account to a Facebook Page, and add both to a business portfolio at business.facebook.com.
2. Create an app of type **Business** at developers.facebook.com, add the Instagram product, and add the app to the portfolio.
3. In **Settings → Users → System users**, add an admin system user and assign it the app, the Page and the Instagram account.
4. **Generate token** with `instagram_basic, instagram_content_publish, pages_show_list, pages_read_engagement, business_management` and expiry **Never**. The token starts with `EAA`.
5. Add it as `IG_ACCESS_TOKEN`, then run `python cli.py whoami` and add the printed id as `IG_USER_ID`.

With Route B you can skip step 4 and disable the refresh workflow.

The code picks the right API from the token's first letters, so nothing else needs changing.

### 4. GitHub token for the weekly refresh (Route A only)
Instagram hands out a new token on every refresh, and a workflow can't save a secret without this.
1. On GitHub: profile picture → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. **Repository access:** Only select repositories → this repo.
3. **Permissions → Repository → Secrets:** Read and write.
4. Pick the longest expiry allowed, generate, and add it as the secret `GH_SECRETS_TOKEN`.

When this token expires, the Monday run fails and GitHub emails you. Create a new one and replace the secret.

### 5. Repo settings
- **The repo must be public.** Instagram downloads the slides and Reels from it. For a private repo, set `hosting.provider: s3` in `config.yaml` and use Cloudflare R2 or S3 (see the S3 secrets in `.env.example`).
- **Settings → Advanced Security:** turn on **Secret Protection** and **Push protection**, so a push containing a key is blocked.
- The workflows only run on a schedule or when you start them, so outsiders can't trigger them or reach the secrets.

### 6. Test before the first post
1. **Actions → Check Instagram connection → Run workflow.** It should print your follower count and publishing quota.
2. Run it again with a post id (for example `2026-10-05-1930-rishikesh`, the name of a file in `content/posts/` without `.yaml`). Instagram fetches and processes the slides and the Reel, but nothing is published.
3. **Actions → Generate posts → Run workflow** creates posts on demand.

## Day-to-day use
Each post is one file in `content/posts/`. You can edit these files directly on github.com (pencil icon) and commit.

| I want to… | Do this |
|---|---|
| See what's queued | Open `content/posts/`. Look under `reel:` in each file: `status: scheduled` is waiting; `scheduled_at` is the time |
| See what was posted | Published Reels have `status: published` and a `permalink` to Instagram under `reel:` |
| Cancel a post | Set `status: skip` under `reel:`. (The top-level `status` belongs to the image post, which is already `skip` in Reels-only mode) |
| Change a caption or hashtags | Edit `caption` or `hashtags` in the YAML before it publishes |
| Move a post | Change `scheduled_at` under `reel:`. Keep the `+05:30` at the end |
| Retry a failed post | Under `reel:`, set `status: scheduled` again and delete the `attempts` and `last_error` lines |
| Stop everything | Set `paused: true` in `config.yaml`. This stops both generating and publishing |
| Post more often | Add slots under `schedule.slots` in `config.yaml` |
| Change the posting time | Change the slot times in `config.yaml` **and** the hours in `.github/workflows/publish.yml`. Its cron line is in UTC (IST minus 5:30) |
| Use my own photos | Put them in `media/own/<destination-slug>/` (for example `media/own/spiti-valley/`) and commit. They are used before any stock photo |

A post is retried up to 3 times, then marked `failed` with the reason in `last_error`. GitHub emails you whenever a workflow run fails.

Changing the text on a slide needs the slide redrawn (`python cli.py rerender <id>`), which only works on the PC where that post was generated, because the original photos are cached there. For posts made on GitHub it's easier to skip the post and let a new one be generated.

## Running it on your PC
You don't need this for normal operation. It's for trying things out and previewing.

```powershell
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # fill in your keys; this file is never committed
```

Claude: if `claude` works in your terminal, the pipeline uses that login and no token is needed. If the command isn't on your PATH, set `CLAUDE_BIN` in `.env` to the full path of `claude.exe`.

| Command | What it does |
|---|---|
| `python cli.py generate --at "2026-10-16 19:30"` | Create one post (Reel). Add `--pillar` and `--destination` to choose the topic |
| `python cli.py generate --at "..." --offline` | Test the pipeline with placeholder content and no API calls |
| `python cli.py plan --days 7` | Fill the free slots in the next 7 days |
| `python cli.py preview [id]` | Instagram-style preview in your browser |
| `python cli.py media-pull` | Download the slides and Reels of unpublished posts from the `media` branch |
| `python cli.py media-sync` | Upload local slides and Reels to the `media` branch and delete published ones |
| `python cli.py rerender <id>` | Redraw a post's slides after editing its text |
| `python cli.py reel <id>` | Rebuild a post's Reel. `--at` sets a different time |
| `python cli.py validate` | Check every post file |
| `python cli.py publish --dry-run` | Show what would be published now |
| `python cli.py test-upload <id>` | Rehearse a post's upload to Instagram without publishing |
| `python cli.py whoami` | Check the Instagram token |

After generating a post locally:
1. `git add content/posts`, then commit and push.
2. `python cli.py media-sync` to upload its slides and Reel.

Before previewing posts that were generated on GitHub, run `git pull` and `python cli.py media-pull`. Published posts show without images, because their media has been deleted.

`publish` and `test-upload` need `GITHUB_REPOSITORY=<owner>/<repo>` in `.env` when run locally.

## Settings
All in `config.yaml`:

| Section | What you can change |
|---|---|
| `brand` | Name, handle, colours, fonts |
| `paused` | Kill switch |
| `schedule.slots` | Posting days and times |
| `copy` | Claude model, language, niche, tone |
| `pillars` | Content themes the planner rotates through |
| `destinations` | Places the planner picks from |
| `carousel` | `publish` (false = Reels only), slide size and number of slides |
| `reels` | On/off, how many days after the carousel, time, number of shots, music |
| `stock` | Photo sources and minimum photo width |
| `quality` | Minimum hook score, number of rewrites, banned phrases |
| `hosting` | `github_raw` (public repo) or `s3`; name of the media branch |
| `instagram` | Graph API version, retry count |

## Troubleshooting
Open the failed run under **Actions** and read the step marked with a red cross.

| What you see | Cause and fix |
|---|---|
| Generate: `skipped: failed quality gates after N drafts` | No draft passed the quality check, so the slot was left empty. Nothing is posted. Run **Generate posts** again, or lower `quality.min_hook_score` |
| Generate: Claude error or usage limit | The Claude token expired (1 year) or your subscription's limit was hit. Renew with `claude setup-token`, or wait for the limit to reset |
| Publish: `Missing required environment variable` | A secret is missing. See the secrets table |
| Publish: `File not publicly reachable` | The repo is private, or the `media` branch is missing that post's files. Make the repo public, or run `python cli.py media-sync` from the PC that has the files |
| Publish: `OAuthException 190` | The Instagram token expired or was revoked. Generate a new one and replace `IG_ACCESS_TOKEN` |
| Refresh: `GH_SECRETS_TOKEN secret is missing` | Add or renew that token (setup step 4) |
| A post shows `status: failed` | Read its `last_error`, fix the cause, then retry it (see Day-to-day use) |
| Nothing was posted at 19:30 | GitHub ran the schedule late, or nothing was due. Check the latest **Publish due posts** run |

## File layout
| Path | What it is |
|---|---|
| `config.yaml` | All settings |
| `cli.py` | Command-line entry point |
| `content/posts/*.yaml` | One file per post: `scheduled` → `published` / `failed` / `skip` |
| `media/own/<destination-slug>/` | Your own photos |
| `media/posts/<id>/` | Rendered slides and Reel. Not on `main`; kept on the `media` branch until published |
| `media/cache/` | Downloaded stock photos and generated music. Local only |
| `assets/fonts/`, `assets/music/` | Poppins fonts; optional music you own |
| `pixlo/planner.py` | Picks the pillar and destination shortlist, avoiding recent repeats |
| `pixlo/research.py` | Claude with web search: topic, sourced facts, photo search queries |
| `pixlo/stock.py` | Your photos first, then Unsplash, then Pexels |
| `pixlo/copywriter.py` | Slides, caption, hashtags and alt text, written while looking at the photos |
| `pixlo/quality.py` | Rule checks + AI editor: hook score, unsupported claims, engagement bait |
| `pixlo/carousel.py` | Slide renderer (`classic`, `panel`, `bold` styles) |
| `pixlo/reel.py`, `pixlo/synth.py`, `pixlo/music.py` | Reel video, original music, beat analysis |
| `pixlo/instagram.py` | Instagram API client (Instagram Login and Facebook Login tokens) |
| `pixlo/hosting.py`, `pixlo/mediastore.py` | Public file URLs; the `media` branch |
| `pixlo/pipeline.py`, `pixlo/posts.py`, `pixlo/preview.py`, `pixlo/llm.py` | Pipeline steps, post files, preview page, Claude Code calls |
| `.github/workflows/` | The four workflows |

## Rules this system follows
Lessons from accounts that got buried or banned:
- Official Graph API only. No automated likes, follows, comments or scraping.
- No reposting other accounts' content. Every post adds its own information layer.
- Every claim needs a source from the research step; the AI editor rejects anything unsupported.
- At most 5 hashtags (Instagram's limit); search keywords go in the caption's first line.
- No engagement bait ("tag a friend", "share if"); Meta demotes it.
- No third-party or copyrighted audio.
- Ramp up slowly: 3 posts a week for 2 weeks, then 5.
- Emergency stop: set `paused: true` in `config.yaml`.

"""Local HTML preview: each post as an Instagram feed card, plus the profile grid.

python cli.py preview  ->  preview/index.html (opened in your browser). Nothing is uploaded.
"""
import html
import re
from datetime import datetime

from . import posts
from .config import ROOT

OUT = ROOT / "preview" / "index.html"
CAPTION_FOLD = 125  # Instagram shows about this many characters before "... more"

ICONS = {
    "heart": '<path d="M12 21s-7.5-4.6-9.6-9.2C1 8.6 3 5 6.6 5c2 0 3.4 1.1 4.4 2.5C12 6.1 13.4 5 15.4 5 19 5 21 8.6 19.6 11.8 17.5 16.4 12 21 12 21z"/>',
    "comment": '<path d="M20.5 11.5a8.5 8.5 0 1 1-3.6-6.9A8.4 8.4 0 0 1 20.5 11.5z"/><path d="M20.5 20.5l-2.6-2.6"/>',
    "send": '<path d="M21 3L3 10.5l7.5 3L21 3zM10.5 13.5L13.5 21 21 3"/>',
    "save": '<path d="M19 21l-7-5-7 5V4a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1z"/>',
}

CSS = """
:root{--bg:#fafafa;--card:#fff;--text:#111;--muted:#737373;--line:#dbdbdb;--accent:#0E7C86;--chip:#f1f1f1;
--ok:#1a7f37;--warn:#9a6700;--bad:#cf222e}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0b0b0b;--card:#121212;--text:#f2f2f2;
--muted:#a8a8a8;--line:#2a2a2a;--chip:#1e1e1e;--ok:#3fb950;--warn:#d29922;--bad:#f85149}}
:root[data-theme="dark"]{--bg:#0b0b0b;--card:#121212;--text:#f2f2f2;--muted:#a8a8a8;--line:#2a2a2a;--chip:#1e1e1e;
--ok:#3fb950;--warn:#d29922;--bad:#f85149}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);
padding:12px 16px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header h1{font-size:18px;margin:0}header .meta{color:var(--muted)}
header nav{margin-left:auto;display:flex;gap:8px}
header a,header button{color:var(--text);text-decoration:none;border:1px solid var(--line);background:var(--card);
padding:6px 12px;border-radius:8px;font:inherit;cursor:pointer}
main{max-width:1100px;margin:0 auto;padding:16px}
h2{font-size:16px;margin:24px 0 12px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:3px;max-width:480px}
.grid a{display:block;aspect-ratio:3/4;overflow:hidden;background:var(--chip)}
.grid img{width:100%;height:100%;object-fit:cover}
.note{color:var(--muted);font-size:13px;margin:6px 0 0}
.post{display:grid;grid-template-columns:minmax(0,470px) minmax(0,1fr);gap:24px;margin:28px 0;align-items:start}
.ig{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.ig-head{display:flex;align-items:center;gap:10px;padding:10px 12px}
.avatar{width:34px;height:34px;border-radius:50%;background:linear-gradient(45deg,#feda75,#d62976,#4f5bd5);
padding:2px}.avatar span{display:grid;place-items:center;width:100%;height:100%;border-radius:50%;
background:var(--card);font-weight:700;font-size:12px}
.ig-head b{display:block}.ig-head small{color:var(--muted)}
.car{position:relative}
.slides{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;scrollbar-width:none;aspect-ratio:4/5;background:#000}
.slides::-webkit-scrollbar{display:none}.slides img{flex:0 0 100%;width:100%;height:100%;object-fit:cover;scroll-snap-align:center}
.nav{position:absolute;top:50%;transform:translateY(-50%);width:30px;height:30px;border-radius:50%;border:0;
background:rgba(255,255,255,.85);color:#111;font-size:18px;cursor:pointer}.prev{left:8px}.next{right:8px}
.count{position:absolute;top:10px;right:10px;background:rgba(0,0,0,.6);color:#fff;font-size:12px;padding:2px 9px;border-radius:12px}
.dots{display:flex;justify-content:center;gap:4px;padding:8px 0 0}.dots i{width:6px;height:6px;border-radius:50%;background:var(--line)}
.dots i.on{background:#0095f6}
.actions{display:flex;gap:14px;padding:6px 12px}.actions svg{width:24px;height:24px;fill:none;stroke:var(--text);stroke-width:1.8;stroke-linejoin:round}
.actions .save{margin-left:auto}
.cap{padding:0 12px 14px;white-space:pre-wrap;word-wrap:break-word}
.cap .more{color:var(--muted);cursor:pointer;background:none;border:0;padding:0;font:inherit}
.tags{color:#00376b}@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .tags{color:#e0f1ff}}
:root[data-theme="dark"] .tags{color:#e0f1ff}
.reel{margin-bottom:16px}.reel video{display:block;width:100%;aspect-ratio:9/16;background:#000;max-height:80vh}
.reel-meta{padding:10px 12px;margin:0;color:var(--muted)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.panel dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 14px;margin:0 0 12px}
.panel dt{color:var(--muted)}.panel dd{margin:0}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600;background:var(--chip)}
.s-scheduled{color:var(--warn)}.s-published{color:var(--ok)}.s-failed,.s-skip{color:var(--bad)}
.panel h3{font-size:13px;margin:14px 0 6px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.panel ol,.panel ul{margin:0;padding-left:18px}.panel li{margin:3px 0}
.panel a{color:var(--accent);word-break:break-all}
.count-ok{color:var(--ok)}.count-bad{color:var(--bad)}
@media (max-width:820px){.post{grid-template-columns:1fr}.grid{max-width:none}}
"""

JS = """
document.querySelectorAll('.car').forEach(car=>{
  const track=car.querySelector('.slides'),dots=car.parentElement.querySelectorAll('.dots i'),
        count=car.querySelector('.count'),n=dots.length;
  const go=d=>track.scrollBy({left:d*track.clientWidth,behavior:'smooth'});
  car.querySelector('.prev').onclick=()=>go(-1);car.querySelector('.next').onclick=()=>go(1);
  track.addEventListener('scroll',()=>{const i=Math.round(track.scrollLeft/track.clientWidth);
    dots.forEach((d,j)=>d.classList.toggle('on',j===i));count.textContent=(i+1)+'/'+n;});
});
document.querySelectorAll('.cap .more').forEach(b=>b.onclick=()=>{
  const cap=b.closest('.cap');cap.querySelector('.short').hidden=true;cap.querySelector('.full').hidden=false;});
const t=document.getElementById('theme');t.onclick=()=>{const r=document.documentElement;
  const dark=r.dataset.theme?r.dataset.theme==='dark':matchMedia('(prefers-color-scheme:dark)').matches;
  r.dataset.theme=dark?'light':'dark';};
"""


def _esc(text) -> str:
    return html.escape(str(text or ""))


def _rel(path: str) -> str:
    return "../" + path  # preview/index.html -> project root


def _caption_html(post: dict, handle: str) -> str:
    full = posts.full_caption(post)
    body = html.escape(full, quote=False)  # no &#x27; entities, so the hashtag regex can't misfire
    body = re.sub(r"(#\w+)", r'<span class="tags">\1</span>', body)
    # Instagram folds at the first line break or ~125 characters, whichever comes first
    first = full.split("\n", 1)[0][:CAPTION_FOLD].rstrip()
    short = html.escape(first, quote=False)
    fold = len(first) < len(full.strip())
    return (f'<div class="cap"><b>{_esc(handle.lstrip("@"))}</b> '
            f'<span class="short">{short}{"… " if fold else ""}'
            f'{"<button class=more>more</button>" if fold else ""}</span>'
            f'<span class="full"{" hidden" if fold else ""}>{body}</span></div>')


def _card(post: dict, cfg: dict) -> str:
    handle = cfg["brand"]["handle"]
    imgs = posts.images(post)
    when = posts.scheduled_at(post)
    initials = "".join(w[0] for w in cfg["brand"]["name"].split()[:2]).upper()
    slides = "".join(f'<img src="{_rel(i)}" alt="slide {n}" loading="lazy">' for n, i in enumerate(imgs, 1))
    dots = "".join(f'<i{" class=on" if n == 0 else ""}></i>' for n in range(len(imgs)))
    icons = "".join(f'<svg viewBox="0 0 24 24" class="{k}">{v}</svg>' for k, v in ICONS.items())

    status = post.get("status", "?")
    review = post.get("review", {})
    hook = review.get("hook_score")
    cap_len = len(posts.full_caption(post))
    errors = posts.validate(post)
    slide_list = "".join(f"<li><b>{_esc(s['title'])}</b><br>{_esc(s['body'])}"
                         f"{'<br><small>photo: ' + _esc(s['photo_shows']) + '</small>' if s.get('photo_shows') else ''}</li>"
                         for s in post.get("slides", []))
    notes = "".join(f"<li>{_esc(n)}</li>" for n in review.get("issues", [])) or "<li>none</li>"
    sources = "".join(f'<li><a href="{_esc(u)}" target="_blank" rel="noopener">{_esc(u)}</a></li>'
                      for u in post.get("sources", []))
    extra = ""
    if post.get("permalink"):
        extra += f'<dt>Live</dt><dd><a href="{_esc(post["permalink"])}" target="_blank">{_esc(post["permalink"])}</a></dd>'
    if post.get("last_error"):
        extra += f'<dt>Error</dt><dd class="count-bad">{_esc(post["last_error"])}</dd>'

    reel = post.get("reel")
    reel_html = ""
    if reel:
        music = reel.get("music")
        rs = reel.get("status", "?")
        reel_html = f"""
    <article class="ig reel">
      <div class="ig-head"><div class="avatar"><span>{_esc(initials)}</span></div>
        <div><b>{_esc(handle.lstrip('@'))}</b><small>Reel · {_esc(reel.get('duration'))}s ·
        {posts.scheduled_at(reel):%a %d %b, %H:%M}</small></div></div>
      <video src="{_rel(reel['video'])}" poster="{_rel(reel['cover'])}" controls loop playsinline preload="none"></video>
      <p class="reel-meta"><span class="badge s-{_esc(rs)}">{_esc(rs)}</span>
        {'Silent kit: open it in Edits, add a trending song, post it, then set reel status to published.'
         if reel.get('mode') == 'kit' else
         ('🎵 ' + _esc(music.get('title')) + ' · ' + _esc(music.get('artist', ''))) if music else
         'No music yet: add tracks to assets/music (see its README).'}</p>
    </article>"""

    return f"""
<section class="post" id="{_esc(post['id'])}">
  <div>
    {reel_html}
    <article class="ig">
      <div class="ig-head"><div class="avatar"><span>{_esc(initials)}</span></div>
        <div><b>{_esc(handle.lstrip('@'))}</b><small>{_esc(post.get('destination', ''))}</small></div></div>
      <div class="car"><div class="slides">{slides}</div>
        <button class="nav prev" aria-label="previous">‹</button><button class="nav next" aria-label="next">›</button>
        <span class="count">1/{len(imgs)}</span></div>
      <div class="dots">{dots}</div>
      <div class="actions">{icons}</div>
      {_caption_html(post, handle)}
    </article>
  </div>
  <aside class="panel">
    <dl>
      <dt>Status</dt><dd><span class="badge s-{_esc(status)}">{_esc(status)}</span></dd>
      <dt>Scheduled</dt><dd>{when:%a %d %b %Y, %H:%M}</dd>
      <dt>Pillar</dt><dd>{_esc(post.get('pillar'))} · style {_esc(post.get('style'))}</dd>
      <dt>Topic</dt><dd>{_esc(post.get('topic'))}</dd>
      <dt>Hook score</dt><dd>{f'{hook}/10' if hook is not None else '-'}</dd>
      <dt>Caption</dt><dd class="{'count-bad' if cap_len > posts.IG_CAPTION_LIMIT else 'count-ok'}">{cap_len} / {posts.IG_CAPTION_LIMIT} chars ·
        {len(post.get('hashtags', []))} / {posts.IG_HASHTAG_LIMIT} hashtags</dd>
      <dt>Checks</dt><dd class="{'count-bad' if errors else 'count-ok'}">{_esc('; '.join(errors)) or 'all valid'}</dd>
      {extra}
    </dl>
    <h3>Editor notes</h3><ul>{notes}</ul>
    <h3>Slide text</h3><ol>{slide_list}</ol>
    <h3>Sources</h3><ul>{sources}</ul>
    <p class="note">Edit {_esc('content/posts/' + post['id'] + '.yaml')}, then run
      <code>python cli.py rerender {_esc(post['id'])}</code> to redraw the slides.</p>
  </aside>
</section>"""


def build(cfg: dict, only: str | None = None) -> tuple:
    all_posts = sorted((p for _, p in posts.load_all()), key=lambda p: str(p.get("scheduled_at")))
    if only:
        all_posts = [p for p in all_posts if p["id"] == only]
    grid = "".join(f'<a href="#{_esc(p["id"])}" title="{_esc(p.get("topic"))}">'
                   f'<img src="{_rel(posts.images(p)[0])}" alt="cover"></a>'
                   for p in reversed(all_posts) if posts.images(p))
    cards = "".join(_card(p, cfg) for p in reversed(all_posts))
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Post Preview</title>
<style>{CSS}</style></head><body>
<header><h1>{_esc(cfg['brand']['name'])} · preview</h1>
<span class="meta">{len(all_posts)} post(s) · built {datetime.now():%d %b %H:%M}</span>
<nav><a href="#grid">Grid</a><button id="theme" type="button">Theme</button></nav></header>
<main>
<h2 id="grid">Profile grid</h2>
<div class="grid">{grid}</div>
<p class="note">Instagram's profile grid crops posts to 3:4, so check that no cover text is cut off.</p>
<h2>Posts (newest first)</h2>
{cards or '<p class="note">No posts yet. Run: python cli.py generate --at "YYYY-MM-DD HH:MM"</p>'}
</main><script>{JS}</script></body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    return OUT, len(all_posts)

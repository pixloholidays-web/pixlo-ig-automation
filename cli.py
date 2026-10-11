#!/usr/bin/env python3
"""Pixlo travel page: Instagram automation.

  python cli.py generate --at "2026-10-06 19:30" [--pillar hidden-gems] [--destination "Spiti Valley"]
  python cli.py plan --days 7           # fill upcoming slots with carousels
  python cli.py validate                # check every post file
  python cli.py preview [id]            # Instagram-style HTML preview in your browser
  python cli.py rerender <id>           # redraw slides after editing a post's YAML
  python cli.py publish [--dry-run]     # publish posts that are due
  python cli.py whoami                  # find your IG user id / check the token
  python cli.py test-upload <id>        # rehearse the upload to Instagram, without publishing
  python cli.py refresh-token --out f   # extend an Instagram Login token (used by the refresh workflow)
Add --offline to generate/plan to test without any API calls.
"""
import argparse
import sys
from datetime import datetime, timedelta

from pixlo import posts
from pixlo.config import ROOT, env, load_config, tz
from pixlo.pipeline import QualityError, make_carousel

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _paused(cfg) -> bool:
    if cfg.get("paused"):
        print("Paused (config.yaml: paused: true). Nothing done.")
        return True
    return False


# ---------------------------------------------------------------- generate
def cmd_generate(args, cfg):
    when = datetime.strptime(args.at, "%Y-%m-%d %H:%M").replace(tzinfo=tz(cfg))
    try:
        make_carousel(cfg, when, args.offline, args.pillar, args.destination)
    except (QualityError, RuntimeError) as exc:
        print(f"✗ {exc}")
        return 1


def upcoming_slots(cfg, days: int) -> list[datetime]:
    zone = tz(cfg)
    today = datetime.now(zone).date()
    slots = []
    for offset in range(1, days + 1):
        day = today + timedelta(days=offset)
        for slot in cfg["schedule"]["slots"]:
            if WEEKDAYS.index(slot["weekday"]) == day.weekday():
                hh, mm = map(int, slot["time"].split(":"))
                slots.append(datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone))
    return sorted(slots)


def cmd_plan(args, cfg):
    if _paused(cfg):
        return 0
    taken = {posts.scheduled_at(p) for _, p in posts.load_all() if p.get("status") != "failed"}
    created, failed = 0, 0
    for when in upcoming_slots(cfg, args.days):
        if when in taken:
            continue
        try:
            make_carousel(cfg, when, args.offline)
            created += 1
        # Skip the slot rather than post something weak (or if Claude/usage limits fail)
        except (QualityError, RuntimeError) as exc:
            print(f"✗ {when:%a %d %b %H:%M} skipped: {exc}")
            failed += 1
    print(f"Planned {created} new post(s), {failed} slot(s) skipped.")
    return 1 if failed else 0


# ----------------------------------------------------------- preview / rerender
def cmd_preview(args, cfg):
    import webbrowser
    from pixlo import preview
    out, count = preview.build(cfg, args.post)
    print(f"Preview of {count} post(s): {out}")
    if not args.no_open:
        webbrowser.open(out.as_uri())


def cmd_rerender(args, cfg):
    from pixlo.pipeline import rerender
    for path, post in posts.load_all():
        if post["id"] == args.post:
            images = rerender(cfg, post)
            posts.save(path, post)
            print(f"Re-rendered {len(images)} slides for {post['id']}")
            return 0
    print(f"No post with id {args.post}")
    return 1


def cmd_reel(args, cfg):
    from pixlo.pipeline import add_reel
    for path, post in posts.load_all():
        if post["id"] == args.post:
            when = (datetime.strptime(args.at, "%Y-%m-%d %H:%M").replace(tzinfo=tz(cfg)) if args.at else None)
            r = add_reel(cfg, post, mode=args.mode, when=when)
            posts.save(path, post)
            print(f"Reel {r['duration']}s ({r['mode']}) -> {r['video']}, scheduled {r['scheduled_at']}"
                  f"{', music: ' + r['music']['title'] if r.get('music') else ''}")
            return 0
    print(f"No post with id {args.post}")
    return 1


# ---------------------------------------------------------------- validate
def cmd_validate(args, cfg):
    bad = 0
    for path, post in posts.load_all():
        errors = posts.validate(post) + (posts.validate_reel(post) if post.get("reel") else [])
        if errors:
            bad += 1
            print(f"✗ {path.name}: " + "; ".join(errors))
    print("All posts valid." if not bad else f"{bad} invalid post(s).")
    return 1 if bad else 0


# ----------------------------------------------------------------- publish
def ig_client(cfg):
    from pixlo.instagram import InstagramClient
    token = env("IG_ACCESS_TOKEN", required=True)
    # Instagram Login tokens ("IG...") identify the account themselves; Facebook ones need IG_USER_ID
    user_id = "" if token.startswith("IG") else env("IG_USER_ID", required=True)
    return InstagramClient(user_id, token, cfg["instagram"]["graph_version"])


def _create_container(client, cfg, post, kind) -> str:
    from pixlo import hosting
    if kind == "reel":
        r = post["reel"]
        return client.create_reel_container(hosting.public_url(cfg, ROOT / r["video"]), posts.reel_caption(post),
                                            cover_url=hosting.public_url(cfg, ROOT / r["cover"]),
                                            audio_name=(r.get("music") or {}).get("title"))
    urls = [hosting.public_url(cfg, ROOT / img) for img in posts.images(post)]
    caption = posts.full_caption(post)
    if len(urls) == 1:
        return client.create_image_container(urls[0], caption)
    children = [client.create_carousel_item(u) for u in urls]
    for child in children:
        client.wait_until_ready(child)
    return client.create_carousel_container(children, caption)


def _due(now) -> list[tuple]:
    """(path, post, kind) for every carousel and reel whose time has come."""
    items = []
    for path, p in posts.load_all():
        if p.get("status") == "scheduled" and posts.scheduled_at(p) <= now:
            items.append((path, p, "carousel"))
        r = p.get("reel")
        if r and r.get("status") == "scheduled" and posts.scheduled_at(r) <= now:
            items.append((path, p, "reel"))
    return items


def cmd_publish(args, cfg):
    from pixlo.instagram import GraphError

    if _paused(cfg):
        return 0
    now = datetime.now(tz(cfg))
    due = _due(now)
    if not due:
        print("Nothing due.")
        return 0
    if args.dry_run:
        for path, p, kind in due:
            caption = posts.reel_caption(p) if kind == "reel" else posts.full_caption(p)
            print(f"[dry-run] would publish {kind} {p['id']}\n{caption}\n")
        return 0

    client = ig_client(cfg)
    failures = 0
    for path, post, kind in due:
        state = post["reel"] if kind == "reel" else post  # where status/container ids live
        label = f"{kind} {post['id']}"
        errors = posts.validate_reel(post) if kind == "reel" else posts.validate(post)
        if errors:
            state.update(status="failed", last_error="; ".join(errors))
            posts.save(path, post)
            failures += 1
            continue
        try:
            # Idempotency: a container from an earlier interrupted run may already be published
            container = state.get("container_id")
            if container and client.container_status(container) == "PUBLISHED":
                state.update(status="published", published_at=now.isoformat())
                posts.save(path, post)
                print(f"✓ {label} was already published")
                continue

            container = _create_container(client, cfg, post, kind)
            state["container_id"] = container
            posts.save(path, post)  # persist before publishing

            client.wait_until_ready(container, timeout=600 if kind == "reel" else 180)
            media_id = client.publish(container)
            state.update(status="published", media_id=media_id,
                         published_at=datetime.now(tz(cfg)).isoformat(),
                         permalink=client.permalink(media_id))
            state.pop("last_error", None)
            print(f"✓ {label} -> {state['permalink']}")
        except (GraphError, RuntimeError, OSError) as exc:
            state["attempts"] = state.get("attempts", 0) + 1
            state["last_error"] = str(exc)
            if "ERROR" in str(exc) or "EXPIRED" in str(exc):
                state.pop("container_id", None)  # broken container: start fresh next run
            if state["attempts"] >= cfg["instagram"]["max_attempts"]:
                state["status"] = "failed"
            print(f"✗ {label}: {exc}")
            failures += 1
        posts.save(path, post)
    return 1 if failures else 0


def cmd_test_upload(args, cfg):
    """Rehearsal: make Instagram fetch and process a post's images and Reel, but never publish them.
    Unpublished containers are discarded by Instagram after 24 hours; nothing is saved to the post file."""
    from pixlo.instagram import GraphError
    post = next((p for _, p in posts.load_all() if p["id"] == args.post), None)
    if post is None:
        raise SystemExit(f"No post with id {args.post}")
    client = ig_client(cfg)
    failures = 0
    for kind in ["carousel"] + (["reel"] if post.get("reel") else []):
        try:
            container = _create_container(client, cfg, post, kind)
            client.wait_until_ready(container, timeout=600 if kind == "reel" else 180)
            print(f"✓ {kind}: Instagram accepted the files (container {container}, not published)")
        except (GraphError, RuntimeError, OSError) as exc:
            print(f"✗ {kind}: {exc}")
            failures += 1
    return 1 if failures else 0


def cmd_whoami(args, cfg):
    from pixlo.instagram import InstagramClient
    token = env("IG_ACCESS_TOKEN", required=True)
    client = InstagramClient(env("IG_USER_ID") or "", token, cfg["instagram"]["graph_version"])
    if not client.instagram_login:
        for page in client.linked_accounts():
            ig = page.get("instagram_business_account")
            print(f"Page: {page['name']:<30} IG: {ig['username'] + ' (IG_USER_ID=' + ig['id'] + ')' if ig else 'not linked'}")
    if client.ig_user_id:
        print("Login type:", "Instagram Login" if client.instagram_login else "Facebook Login")
        print("Account:", client.account())
        print("Publishing quota:", client.publishing_limit())


def cmd_refresh_token(args, cfg):
    """Extend the Instagram Login token by 60 days. Instagram normally keeps the same token and only moves
    its expiry, so nothing has to be stored. If it ever issues a different one, that is written to --out
    (never printed) for the workflow to save as the new secret."""
    client = ig_client(cfg)
    token, expires_in = client.refresh_token()
    print(f"Token refreshed, valid for {expires_in // 86400} more days.")
    if token != client.token:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(token)
        print("Instagram issued a new token: the IG_ACCESS_TOKEN secret must be updated.")


# ------------------------------------------------------------------- main
def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # ✓, ₹ and Hinglish on Windows consoles
    parser = argparse.ArgumentParser(description="Pixlo Instagram automation")
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="create one carousel")
    g.add_argument("--at", required=True, help='local time, "YYYY-MM-DD HH:MM"')
    g.add_argument("--pillar", help="force a pillar (see config.yaml)")
    g.add_argument("--destination", help="force a destination")
    g.add_argument("--offline", action="store_true", help="placeholder research/copy, no API calls")

    p = sub.add_parser("plan", help="fill upcoming schedule slots")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--offline", action="store_true")

    sub.add_parser("validate", help="check all post files")

    pv = sub.add_parser("preview", help="build an Instagram-style HTML preview and open it")
    pv.add_argument("post", nargs="?", help="only this post id")
    pv.add_argument("--no-open", action="store_true", help="don't open the browser")

    rr = sub.add_parser("rerender", help="redraw a post's slides from its YAML (after editing text)")
    rr.add_argument("post", help="post id, e.g. 2026-10-06-1930-tirthan-valley")

    rl = sub.add_parser("reel", help="(re)build a post's Reel video and schedule it")
    rl.add_argument("post", help="post id")
    rl.add_argument("--mode", choices=["auto", "kit"], help="auto = music + auto-publish, kit = silent for Edits")
    rl.add_argument("--at", help='Reel time "YYYY-MM-DD HH:MM" (default: day after the carousel)')

    pub = sub.add_parser("publish", help="publish due posts")
    pub.add_argument("--dry-run", action="store_true")

    sub.add_parser("whoami", help="list linked IG accounts and check the token")

    tu = sub.add_parser("test-upload", help="rehearse a post's upload to Instagram without publishing")
    tu.add_argument("post", help="post id")

    rt = sub.add_parser("refresh-token", help="extend an Instagram Login token by 60 days")
    rt.add_argument("--out", required=True, help="file to write the new token to")

    args = parser.parse_args()
    cfg = load_config()
    handler = {"generate": cmd_generate, "plan": cmd_plan, "validate": cmd_validate,
               "preview": cmd_preview, "rerender": cmd_rerender, "reel": cmd_reel,
               "publish": cmd_publish, "whoami": cmd_whoami,
               "refresh-token": cmd_refresh_token, "test-upload": cmd_test_upload}[args.cmd]
    sys.exit(handler(args, cfg) or 0)


if __name__ == "__main__":
    main()

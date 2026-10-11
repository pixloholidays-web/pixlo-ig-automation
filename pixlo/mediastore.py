"""Rendered slides and Reels live on a separate `media` branch, not on main.

Instagram fetches the files from that branch. The branch is always ONE commit with no history, holding only
the posts that still have something to publish, so published media really disappears and the repo stays small.

  pull()  copy the branch's files into media/posts/ (ignored by git on main)
  sync()  rebuild the branch from media/posts/, minus the posts that are fully published
"""
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from . import posts
from .config import ROOT

MEDIA_DIR = ROOT / "media" / "posts"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's hash of a tree with no files


def branch(cfg: dict) -> str:
    return cfg["hosting"].get("media_branch", "media")


def _git(*args: str, env: dict | None = None, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, env={**os.environ, **(env or {})},
                            capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


@lru_cache(maxsize=None)
def remote_sha(name: str) -> str | None:
    """Commit the media branch points at on GitHub, or None if the branch doesn't exist yet."""
    out = _git("ls-remote", "origin", f"refs/heads/{name}")
    return out.split()[0] if out else None


def needed_ids() -> set[str]:
    """Posts whose carousel or Reel is still waiting to be published (or failed and may be retried)."""
    keep = set()
    for _, p in posts.load_all():
        reel = p.get("reel") or {}
        if p.get("status") not in posts.DONE or (reel and reel.get("status") not in posts.DONE):
            keep.add(p["id"])
    return keep


def _checkout(sha: str, dest: Path) -> None:
    """Extract every file of a commit into dest, without touching the repo's own index or branch."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index"), "GIT_WORK_TREE": str(dest)}
        _git("read-tree", sha, env=env)
        _git("checkout-index", "--all", "--force", env=env)


def pull(cfg: dict) -> int:
    """Download the media branch into media/posts/. Returns the number of post folders now present."""
    name = branch(cfg)
    if remote_sha(name):
        _git("fetch", "--quiet", "origin", name)
        _checkout(_git("rev-parse", "FETCH_HEAD"), ROOT)
    return sum(1 for d in MEDIA_DIR.iterdir() if d.is_dir()) if MEDIA_DIR.exists() else 0


def sync(cfg: dict) -> str:
    """Rebuild the media branch as a single fresh commit: what's on it now, plus local media/posts/,
    minus posts that are fully published. Returns a short description of what happened."""
    name = branch(cfg)
    keep = needed_ids()
    old = remote_sha(name)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "tree"
        stage.mkdir()
        if old:
            _git("fetch", "--quiet", "origin", name)
            _checkout(old, stage)
        staged = stage / "media" / "posts"
        staged.mkdir(parents=True, exist_ok=True)
        for folder in (MEDIA_DIR.iterdir() if MEDIA_DIR.exists() else []):
            if folder.is_dir() and folder.name in keep:
                shutil.copytree(folder, staged / folder.name, dirs_exist_ok=True)
        removed = [d.name for d in staged.iterdir() if d.is_dir() and d.name not in keep]
        for gone in removed:
            shutil.rmtree(staged / gone)

        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index"), "GIT_WORK_TREE": str(stage),
               "GIT_AUTHOR_NAME": "pixlo-bot", "GIT_AUTHOR_EMAIL": "pixlo-bot@users.noreply.github.com",
               "GIT_COMMITTER_NAME": "pixlo-bot", "GIT_COMMITTER_EMAIL": "pixlo-bot@users.noreply.github.com"}
        _git("add", "--all", "--force", ".", env=env)
        tree = _git("write-tree", env=env)
        kept = sorted(d.name for d in staged.iterdir() if d.is_dir())
        if tree == (_git("rev-parse", f"{old}^{{tree}}") if old else EMPTY_TREE):
            return f"media branch already up to date ({len(kept)} post(s))"
        # No parent commit: the branch never accumulates history, so deleted files are really gone
        commit = _git("commit-tree", tree, "-m", f"media for {len(kept)} unpublished post(s)", env=env)
        _git("push", "--quiet", "--force", "origin", f"{commit}:refs/heads/{name}")
    remote_sha.cache_clear()
    return (f"media branch updated: {len(kept)} post(s) kept"
            + (f", removed {', '.join(removed)}" if removed else ""))


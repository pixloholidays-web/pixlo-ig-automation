"""Rendered slides and Reels live on a separate `media` branch, not on main.

Instagram fetches the files from that branch. The branch is always ONE commit with no history, holding only
the files still waiting to be published, so published media really disappears and the repo stays small.

  pull()  copy the branch's files into media/posts/ (ignored by git on main)
  sync()  rebuild the branch from media/posts/, minus everything that is published or skipped
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


def needed_files() -> set[str]:
    """Repo-relative paths of every file still waiting to be published (or failed and may be retried)."""
    keep = set()
    for _, p in posts.load_all():
        if p.get("status") not in posts.DONE:
            keep.update(posts.images(p))
        reel = p.get("reel") or {}
        if reel and reel.get("status") not in posts.DONE:
            keep.update(x for x in (reel.get("video"), reel.get("cover")) if x)
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
    """Rebuild the media branch as a single fresh commit holding exactly the files still to be published,
    taken from media/posts/ or from the branch as it is now. Returns a short description of what happened."""
    name = branch(cfg)
    keep = needed_files()
    old = remote_sha(name)
    with tempfile.TemporaryDirectory() as tmp:
        current, stage = Path(tmp) / "current", Path(tmp) / "tree"
        current.mkdir()
        stage.mkdir()
        if old:
            _git("fetch", "--quiet", "origin", name)
            _checkout(old, current)
        before = {f.relative_to(current).as_posix() for f in current.rglob("*") if f.is_file()}
        for rel in sorted(keep):
            source = ROOT / rel if (ROOT / rel).exists() else current / rel
            if source.exists():
                (stage / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, stage / rel)
        after = {f.relative_to(stage).as_posix() for f in stage.rglob("*") if f.is_file()}

        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index"), "GIT_WORK_TREE": str(stage),
               "GIT_AUTHOR_NAME": "pixlo-bot", "GIT_AUTHOR_EMAIL": "pixlo-bot@users.noreply.github.com",
               "GIT_COMMITTER_NAME": "pixlo-bot", "GIT_COMMITTER_EMAIL": "pixlo-bot@users.noreply.github.com"}
        _git("add", "--all", "--force", ".", env=env)
        tree = _git("write-tree", env=env)
        if tree == (_git("rev-parse", f"{old}^{{tree}}") if old else EMPTY_TREE):
            return f"media branch already up to date ({len(after)} file(s))"
        # No parent commit: the branch never accumulates history, so deleted files are really gone
        commit = _git("commit-tree", tree, "-m", f"{len(after)} file(s) waiting to be published", env=env)
        _git("push", "--quiet", "--force", "origin", f"{commit}:refs/heads/{name}")
    remote_sha.cache_clear()
    return (f"media branch updated: {len(after)} file(s) kept, {len(after - before)} added, "
            f"{len(before - after)} deleted")

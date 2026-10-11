"""Make a local image or video reachable at a public URL (the Graph API fetches it from there)."""
import mimetypes
from pathlib import Path

import requests

from .config import ROOT, env


def public_url(cfg: dict, local_path: Path) -> str:
    hosting = cfg["hosting"]
    rel = local_path.resolve().relative_to(ROOT).as_posix()

    if hosting["provider"] == "github_raw":
        from . import mediastore
        repo = env("GITHUB_REPOSITORY", required=True)  # set automatically in Actions
        # Slides and Reels live on the media branch; the commit id (not the branch name) avoids stale caches
        ref = mediastore.remote_sha(mediastore.branch(cfg))
        if not ref:
            raise RuntimeError("the media branch doesn't exist yet: run `python cli.py media-sync`")
        url = f"https://raw.githubusercontent.com/{repo}/{ref}/{rel}"

    elif hosting["provider"] == "s3":
        import boto3
        s3cfg = hosting["s3"]
        key = s3cfg["prefix"] + rel  # full relative path: every post has its own 01.jpg
        client = boto3.client("s3", endpoint_url=env("S3_ENDPOINT_URL") or None)
        content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        client.upload_file(str(local_path), s3cfg["bucket"], key, ExtraArgs={"ContentType": content_type})
        url = f"{s3cfg['public_base_url'].rstrip('/')}/{rel}"

    else:
        raise ValueError(f"Unknown hosting provider: {hosting['provider']}")

    resp = requests.head(url, timeout=60, allow_redirects=True)
    if resp.status_code != 200:
        raise RuntimeError(f"File not publicly reachable ({resp.status_code}): {url}")
    return url

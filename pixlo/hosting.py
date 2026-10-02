"""Make a local image reachable at a public URL (the Graph API fetches it from there)."""
import mimetypes
from pathlib import Path

import requests

from .config import ROOT, env


def public_url(cfg: dict, local_path: Path) -> str:
    hosting = cfg["hosting"]
    rel = local_path.resolve().relative_to(ROOT).as_posix()

    if hosting["provider"] == "github_raw":
        repo = env("GITHUB_REPOSITORY", required=True)  # set automatically in Actions
        url = f"https://raw.githubusercontent.com/{repo}/{hosting.get('branch', 'main')}/{rel}"

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

    resp = requests.head(url, timeout=20, allow_redirects=True)
    if resp.status_code != 200:
        raise RuntimeError(f"Image not publicly reachable ({resp.status_code}): {url}")
    return url

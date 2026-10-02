"""Minimal Instagram Graph API client for content publishing."""
import time

import requests


class GraphError(RuntimeError):
    pass


class InstagramClient:
    def __init__(self, ig_user_id: str, access_token: str, version: str = "v22.0"):
        self.ig_user_id = ig_user_id
        self.token = access_token
        self.base = f"https://graph.facebook.com/{version}"

    def _call(self, method: str, path: str, **params) -> dict:
        params["access_token"] = self.token
        kwargs = {"params": params} if method == "GET" else {"data": params}
        resp = requests.request(method, f"{self.base}/{path}", timeout=60, **kwargs)
        body = resp.json()
        if resp.status_code >= 400 or "error" in body:
            err = body.get("error", {})
            raise GraphError(f"{err.get('type')} {err.get('code')}: {err.get('message')}")
        return body

    # --- setup helpers -------------------------------------------------
    def linked_accounts(self) -> list[dict]:
        """Facebook Pages visible to the token, with their linked IG business account."""
        data = self._call("GET", "me/accounts",
                          fields="name,instagram_business_account{id,username}")
        return data.get("data", [])

    def account(self) -> dict:
        return self._call("GET", self.ig_user_id, fields="username,followers_count,media_count")

    def publishing_limit(self) -> dict:
        return self._call("GET", f"{self.ig_user_id}/content_publishing_limit",
                          fields="quota_usage,config")

    # --- publishing ----------------------------------------------------
    def create_image_container(self, image_url: str, caption: str) -> str:
        return self._call("POST", f"{self.ig_user_id}/media",
                          image_url=image_url, caption=caption)["id"]

    def create_carousel_item(self, image_url: str) -> str:
        return self._call("POST", f"{self.ig_user_id}/media",
                          image_url=image_url, is_carousel_item="true")["id"]

    def create_carousel_container(self, children: list[str], caption: str) -> str:
        return self._call("POST", f"{self.ig_user_id}/media", media_type="CAROUSEL",
                          children=",".join(children), caption=caption)["id"]

    def create_reel_container(self, video_url: str, caption: str, cover_url: str | None = None,
                              audio_name: str | None = None) -> str:
        params = {"media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": "true"}
        if cover_url:
            params["cover_url"] = cover_url
        if audio_name:
            params["audio_name"] = audio_name
        return self._call("POST", f"{self.ig_user_id}/media", **params)["id"]

    def container_status(self, container_id: str) -> str:
        return self._call("GET", container_id, fields="status_code")["status_code"]

    def wait_until_ready(self, container_id: str, timeout: int = 180) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.container_status(container_id)
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise GraphError(f"Container {container_id} status {status}")
            time.sleep(5)
        raise GraphError(f"Container {container_id} not ready after {timeout}s")

    def publish(self, container_id: str) -> str:
        return self._call("POST", f"{self.ig_user_id}/media_publish",
                          creation_id=container_id)["id"]

    def permalink(self, media_id: str) -> str | None:
        return self._call("GET", media_id, fields="permalink").get("permalink")

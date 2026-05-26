from __future__ import annotations

import io
import ipaddress
import logging
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from modernreformation_sync.config import ReadeckConfig
from modernreformation_sync.models import Article

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/svg+xml",
    "image/webp",
}


@dataclass(frozen=True)
class Bookmark:
    id: str
    url: str
    title: str
    created: str
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImageResource:
    url: str
    content: bytes
    content_type: str


class ImageSrcParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        attrs_map = {key.lower(): value for key, value in attrs}
        src = attrs_map.get("src")
        if src:
            self.urls.append(src)


class ReadeckClient:
    def __init__(self, config: ReadeckConfig, client: httpx.Client | None = None) -> None:
        if config.enabled and (not config.base_url or not config.token):
            raise ValueError("readeck.enabled is true but base_url or token is empty")
        self.config = config
        self.client = client or httpx.Client(timeout=config.request_timeout_seconds)

    def ensure_collection(self) -> None:
        if not self.config.collection_name:
            return
        existing = self._find_collection(self.config.collection_name)
        payload = {
            "name": self.config.collection_name,
            "labels": self.config.label,
            "type": ["article"],
            "is_archived": False,
        }
        if existing:
            self._request("PATCH", f"/api/bookmarks/collections/{existing['id']}", json=payload)
            logger.info("Updated Readeck collection %s", self.config.collection_name)
        else:
            self._request("POST", "/api/bookmarks/collections", json=payload)
            logger.info("Created Readeck collection %s", self.config.collection_name)

    def push_article(self, article: Article, html: str) -> str | None:
        existing = self.find_bookmark_by_url(article.url)
        labels = [self.config.label, self.config.translated_label, article.resource_type.slug]
        payload = {
            "url": article.url,
            "title": article.translated_title or article.title,
            "labels": [label for label in labels if label],
            "created": article.publish_date.isoformat(),
            "html": html,
        }
        if existing:
            if self.config.existing_policy == "skip":
                logger.info("Skipping existing Readeck bookmark: %s", article.url)
                return existing.id
            if self.config.existing_policy == "patch_metadata":
                self._request(
                    "PATCH",
                    f"/api/bookmarks/{existing.id}",
                    json={
                        "title": payload["title"],
                        "labels": payload["labels"],
                        "published": article.publish_date.isoformat(),
                    },
                )
                logger.info("Updated Readeck bookmark metadata: %s", article.url)
                return existing.id
            self._request("DELETE", f"/api/bookmarks/{existing.id}")
            logger.info("Replacing Readeck bookmark: %s", article.url)
        if self.config.image_mode == "multipart":
            response = self._post_bookmark_multipart(payload, html)
        else:
            response = self._request("POST", "/api/bookmarks", json=payload)
        bookmark_id = response.headers.get("bookmark-id")
        logger.info("Submitted Readeck bookmark %s", article.url)
        return bookmark_id

    def get_bookmark(self, bookmark_id: str) -> dict[str, object]:
        response = self._request("GET", f"/api/bookmarks/{bookmark_id}")
        return response.json()

    def delete_bookmark(self, bookmark_id: str) -> None:
        self._request("DELETE", f"/api/bookmarks/{bookmark_id}")

    def download_bookmark_file(self, bookmark_id: str, name: str) -> bytes:
        response = self._request("GET", f"/api/bookmarks/{bookmark_id}/{name}")
        return response.content

    def find_bookmark_by_url(self, url: str) -> Bookmark | None:
        for bookmark in self.list_synced_bookmarks(per_page=100):
            if bookmark.url == url and self._is_synced_bookmark(bookmark):
                return bookmark
        return None

    def list_synced_bookmarks(self, *, per_page: int = 100) -> list[Bookmark]:
        bookmarks: list[Bookmark] = []
        page = 1
        while True:
            response = self._request(
                "GET",
                "/api/bookmarks",
                params={
                    "labels": self.config.label,
                    "sort": "-created",
                    "per_page": per_page,
                    "page": page,
                },
            )
            payload = response.json()
            bookmarks.extend(
                Bookmark(
                    id=item["id"],
                    url=item.get("url") or "",
                    title=item.get("title") or "",
                    created=item.get("created") or "",
                    labels=tuple(str(label) for label in item.get("labels") or []),
                )
                for item in payload
            )
            if len(payload) < per_page:
                return bookmarks
            page += 1

    def prune(self, keep: int | None = None) -> list[str]:
        keep = self.config.keep if keep is None else keep
        if keep <= 0:
            return []
        bookmarks = self.list_synced_bookmarks()
        owned = [bookmark for bookmark in bookmarks if self._is_synced_bookmark(bookmark)]
        stale = owned[keep:]
        removed = []
        for bookmark in stale:
            if self.config.archive_before_delete:
                self._request("PATCH", f"/api/bookmarks/{bookmark.id}", json={"is_archived": True})
            else:
                self._request("DELETE", f"/api/bookmarks/{bookmark.id}")
            removed.append(bookmark.id)
        if removed:
            logger.info("Pruned %d Readeck bookmarks", len(removed))
        return removed

    def _find_collection(self, name: str) -> dict[str, object] | None:
        response = self._request("GET", "/api/bookmarks/collections")
        for item in response.json():
            if item.get("name") == name:
                return item
        return None

    def _is_synced_bookmark(self, bookmark: Bookmark) -> bool:
        labels = set(bookmark.labels)
        return (
            bookmark.url.startswith("https://www.modernreformation.org/resources/")
            and self.config.label in labels
            and self.config.translated_label in labels
        )

    def _post_bookmark_multipart(
        self,
        payload: dict[str, object],
        html: str,
    ) -> httpx.Response:
        data: dict[str, str | list[str]] = {
            "url": str(payload["url"]),
            "title": str(payload["title"]),
            "created": str(payload["created"]),
        }
        labels = payload.get("labels")
        if isinstance(labels, list):
            data["labels"] = [str(label) for label in labels]
        files = [
            (
                "resource",
                (
                    "_",
                    io.BytesIO(html.encode("utf-8")),
                    "text/html; charset=utf-8",
                    {"Location": str(payload["url"])},
                ),
            )
        ]
        for resource in self._download_image_resources(html):
            files.append(
                (
                    "resource",
                    (
                        "_",
                        io.BytesIO(resource.content),
                        resource.content_type,
                        {"Location": resource.url},
                    ),
                )
            )
        return self._request("POST", "/api/bookmarks", data=data, files=files)

    def _download_image_resources(self, html: str) -> list[ImageResource]:
        resources: list[ImageResource] = []
        total_bytes = 0
        for url in extract_image_urls(html)[: self.config.max_image_count]:
            if not is_allowed_image_url(url, self.config.allowed_image_hosts):
                logger.warning("Skipping image from untrusted URL %s", url)
                continue
            try:
                response = self.client.get(url, follow_redirects=True)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("Could not download image %s: %s", url, exc)
                continue
            if not is_allowed_image_url(str(response.url), self.config.allowed_image_hosts):
                logger.warning("Skipping image redirected to untrusted URL %s", response.url)
                continue
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
            if content_type not in ALLOWED_IMAGE_TYPES:
                logger.warning("Skipping unsupported image type %s for %s", content_type, url)
                continue
            content_length = response.headers.get("content-length")
            if content_length and content_length.isdigit():
                if int(content_length) > self.config.max_image_bytes:
                    logger.warning("Skipping oversized image %s by Content-Length", url)
                    continue
            content = response.content
            if len(content) > self.config.max_image_bytes:
                logger.warning("Skipping oversized image %s (%d bytes)", url, len(content))
                continue
            if total_bytes + len(content) > self.config.max_total_image_bytes:
                logger.warning("Skipping image %s because total image budget is exhausted", url)
                continue
            total_bytes += len(content)
            resources.append(ImageResource(url=url, content=content, content_type=content_type))
        return resources

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        response = self.client.request(
            method,
            f"{self.config.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self.config.token}",
                "Accept": "application/json",
            },
            **kwargs,
        )
        if response.is_error:
            logger.error("Readeck %s %s failed: %s", method, path, response.text[:1000])
        response.raise_for_status()
        return response


def extract_image_urls(html: str) -> list[str]:
    parser = ImageSrcParser()
    parser.feed(html)
    seen = set()
    urls = []
    for url in parser.urls:
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def is_http_url(url: str) -> bool:
    return urlparse(url).scheme in {"http", "https"}


def is_allowed_image_url(url: str, allowed_hosts: list[str]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.rstrip(".").lower()
    if is_private_host(host):
        return False
    normalized_allowed = {item.rstrip(".").lower() for item in allowed_hosts}
    return host in normalized_allowed


def is_private_host(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host in {"localhost", "localhost.localdomain"}
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def maybe_push_to_readeck(
    articles: list[Article],
    rendered_html: dict[str, str],
    config: ReadeckConfig,
) -> None:
    if not config.enabled:
        return
    client = ReadeckClient(config)
    client.ensure_collection()
    for article in articles:
        client.push_article(article, rendered_html[article.slug])
    client.prune()

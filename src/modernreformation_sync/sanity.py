from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx
from dateutil.parser import isoparse

from modernreformation_sync.config import SourceConfig
from modernreformation_sync.models import Article, Author, ResourceType, Topic

RESOURCE_FIELDS = """
title,
"slug": slug.current,
publish_date,
excerpt,
resource_content,
"resource_type": resource_type->{name, "slug": slug.current},
"author": author[]->{
  title,
  "slug": slug.current,
  author_bio,
  author_image{asset->{url}, alt},
  image{asset->{url}, alt},
  headshot{asset->{url}, alt}
},
"primary_topic": primary_topic->{title, name, "slug": slug.current},
"secondary_topics": secondary_topics[]->{title, name, "slug": slug.current},
"image": promo_image.asset->url,
promo_image_alt,
promo_image_credit
"""


class SanityClient:
    def __init__(self, config: SourceConfig, client: httpx.Client | None = None) -> None:
        self.config = config
        self.client = client or httpx.Client(timeout=30)

    @property
    def endpoint(self) -> str:
        return (
            f"https://{self.config.sanity_project_id}.api.sanity.io/"
            f"v{self.config.sanity_api_version}/data/query/{self.config.sanity_dataset}"
        )

    def fetch_latest(self) -> list[Article]:
        type_filter = ""
        params: dict[str, Any] = {"limit": self.config.limit - 1}
        if self.config.resource_types:
            type_filter = " && resource_type->slug.current in $types"
            params["types"] = self.config.resource_types

        query = (
            f'*[_type == "resource" && title != null && publish_date < now(){type_filter}] '
            f"| order(publish_date desc) [0..$limit] {{{RESOURCE_FIELDS}}}"
        )
        result = self._query(query, params)
        return [parse_article(item) for item in result]

    def fetch_by_slug(self, slug: str) -> Article | None:
        query = (
            '*[_type == "resource" && slug.current == $slug && publish_date < now()][0]'
            f"{{{RESOURCE_FIELDS}}}"
        )
        result = self._query(query, {"slug": slug})
        return parse_article(result) if result else None

    def _query(self, query: str, params: dict[str, Any] | None = None) -> Any:
        request_params = {"query": query}
        for key, value in (params or {}).items():
            request_params[f"${key}"] = json.dumps(value)
        response = self.client.get(self.endpoint, params=request_params)
        response.raise_for_status()
        payload = response.json()
        return payload["result"]


def parse_article(data: dict[str, Any]) -> Article:
    resource_type = data.get("resource_type") or {}
    authors = [
        Author(
            title=author.get("title") or "",
            slug=author.get("slug") or "",
            bio=author.get("author_bio") or [],
            image_url=author_image_url(author),
            image_alt=author_image_alt(author),
        )
        for author in data.get("author") or []
    ]
    publish_date = parse_datetime(data["publish_date"])
    slug = data["slug"].strip("/")
    return Article(
        title=data["title"],
        slug=slug,
        url=f"https://www.modernreformation.org/resources/{slug}",
        publish_date=publish_date,
        resource_type=ResourceType(
            name=resource_type.get("name") or "",
            slug=resource_type.get("slug") or "",
        ),
        authors=authors,
        excerpt=data.get("excerpt") or [],
        content=data.get("resource_content") or [],
        image_url=data.get("image") or "",
        image_alt=data.get("promo_image_alt") or "",
        image_credit=data.get("promo_image_credit") or [],
        topics=parse_topics(data),
    )


def parse_datetime(value: str) -> datetime:
    parsed = isoparse(value)
    return parsed if parsed.tzinfo else parsed.astimezone()


def parse_topics(data: dict[str, Any]) -> list[Topic]:
    raw_topics = [data.get("primary_topic"), *(data.get("secondary_topics") or [])]
    topics: list[Topic] = []
    seen: set[str] = set()
    for raw_topic in raw_topics:
        if not isinstance(raw_topic, dict):
            continue
        title = raw_topic.get("title") or raw_topic.get("name") or ""
        slug = raw_topic.get("slug") or ""
        key = slug or title
        if not title or key in seen:
            continue
        topics.append(Topic(title=title, slug=slug))
        seen.add(key)
    return topics


def author_image_url(author: dict[str, Any]) -> str:
    for key in ("author_image", "image", "headshot"):
        image = author.get(key)
        if not isinstance(image, dict):
            continue
        asset = image.get("asset")
        if isinstance(asset, dict) and asset.get("url"):
            return str(asset["url"])
    return ""


def author_image_alt(author: dict[str, Any]) -> str:
    for key in ("author_image", "image", "headshot"):
        image = author.get(key)
        if isinstance(image, dict) and image.get("alt"):
            return str(image["alt"])
    return author.get("title") or ""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

PortableBlock = dict[str, object]


@dataclass(frozen=True)
class Author:
    title: str
    slug: str = ""
    bio: list[PortableBlock] | str = field(default_factory=list)
    image_url: str = ""
    image_alt: str = ""


@dataclass(frozen=True)
class ResourceType:
    name: str
    slug: str


@dataclass(frozen=True)
class Topic:
    title: str
    slug: str = ""


@dataclass
class Article:
    title: str
    slug: str
    url: str
    publish_date: datetime
    resource_type: ResourceType
    authors: list[Author]
    excerpt: list[PortableBlock]
    content: list[PortableBlock]
    image_url: str = ""
    image_alt: str = ""
    image_credit: list[PortableBlock] | str = field(default_factory=list)
    topics: list[Topic] = field(default_factory=list)
    translated_title: str = ""
    translated_html: str = ""
    original_html: str = ""

    @property
    def id(self) -> str:
        return f"modernreformation:{self.slug}"

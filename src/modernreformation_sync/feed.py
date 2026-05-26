from __future__ import annotations

from email.utils import format_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from modernreformation_sync.config import SiteConfig
from modernreformation_sync.models import Article
from modernreformation_sync.render import escape, text_from_blocks

ET.register_namespace("content", "http://purl.org/rss/1.0/modules/content/")


def write_feeds(articles: list[Article], config: SiteConfig) -> None:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    _write_feed(articles, config, config.output_dir / "feed.xml", bilingual=False)
    _write_feed(articles, config, config.output_dir / "feed.zh.xml", bilingual=True)


def _write_feed(
    articles: list[Article], config: SiteConfig, path: Path, *, bilingual: bool
) -> None:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = config.title + (" 中文双语" if bilingual else "")
    ET.SubElement(channel, "link").text = config.base_url or "https://www.modernreformation.org"
    ET.SubElement(channel, "description").text = config.description
    if articles:
        ET.SubElement(channel, "lastBuildDate").text = format_datetime(articles[0].publish_date)

    for article in articles:
        item = ET.SubElement(channel, "item")
        title = (
            article.translated_title if bilingual and article.translated_title else article.title
        )
        link = article_public_url(article, config) if bilingual else article.url
        ET.SubElement(item, "title").text = title
        ET.SubElement(item, "link").text = link
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = article.id
        ET.SubElement(item, "pubDate").text = format_datetime(article.publish_date)
        ET.SubElement(item, "description").text = text_from_blocks(article.excerpt)
        content = (
            article.translated_html
            if bilingual and article.translated_html
            else article.original_html
        )
        ET.SubElement(item, "{http://purl.org/rss/1.0/modules/content/}encoded").text = content

    tree = ET.ElementTree(rss)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def article_public_url(article: Article, config: SiteConfig) -> str:
    suffix = f"articles/{escape_slug(article.slug)}.html"
    return f"{config.base_url}/{suffix}" if config.base_url else suffix


def escape_slug(slug: str) -> str:
    return escape(slug.strip("/").replace("/", "__"))

from __future__ import annotations

from pathlib import Path

from modernreformation_sync.config import AppConfig
from modernreformation_sync.feed import escape_slug, write_feeds
from modernreformation_sync.models import Article
from modernreformation_sync.render import (
    escape,
    render_article_html,
    render_portable_text,
    render_resource_metadata,
)


def prepare_articles(articles: list[Article]) -> None:
    for article in articles:
        article.original_html = (
            f"{render_portable_text(article.content)}\n{render_resource_metadata(article)}"
        )
        if not article.translated_title:
            article.translated_title = article.title
        if not article.translated_html:
            article.translated_html = article.original_html


def write_static_site(articles: list[Article], config: AppConfig) -> dict[str, str]:
    prepare_articles(articles)
    articles_dir = config.site.output_dir / "articles"
    articles_dir.mkdir(parents=True, exist_ok=True)
    rendered: dict[str, str] = {}
    for article in articles:
        html = render_article_html(article, bilingual=True)
        rendered[article.slug] = html
        (articles_dir / f"{escape_slug(article.slug)}.html").write_text(html, encoding="utf-8")
    write_index(articles, config.site.output_dir / "index.html")
    write_feeds(articles, config.site)
    return rendered


def write_index(articles: list[Article], path: Path) -> None:
    items = "\n".join(
        f'<li><a href="articles/{escape_slug(article.slug)}.html">'
        f"{escape(article.translated_title or article.title)}</a></li>"
        for article in articles
    )
    path.write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Modern Reformation Bilingual</title>
</head>
<body>
  <h1>Modern Reformation Bilingual</h1>
  <p><a href="feed.zh.xml">Bilingual RSS</a> · <a href="feed.xml">Original RSS</a></p>
  <ul>{items}</ul>
</body>
</html>
""",
        encoding="utf-8",
    )

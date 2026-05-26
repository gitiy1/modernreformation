from datetime import UTC, datetime

from modernreformation_sync.builder import write_static_site
from modernreformation_sync.config import AppConfig, SiteConfig
from modernreformation_sync.models import Article, ResourceType


def test_write_static_site_removes_stale_article_pages(tmp_path) -> None:
    articles_dir = tmp_path / "articles"
    articles_dir.mkdir()
    stale = articles_dir / "essays__old.html"
    stale.write_text("old", encoding="utf-8")
    article = Article(
        title="New",
        slug="essays/new",
        url="https://www.modernreformation.org/resources/essays/new",
        publish_date=datetime(2026, 5, 27, tzinfo=UTC),
        resource_type=ResourceType(name="Essays", slug="essays"),
        authors=[],
        excerpt=[],
        content=[],
    )

    write_static_site([article], AppConfig(site=SiteConfig(output_dir=tmp_path)))

    assert not stale.exists()
    assert (articles_dir / "essays__new.html").exists()

from __future__ import annotations

from datetime import UTC, datetime

from modernreformation_sync import cli
from modernreformation_sync.config import AppConfig, ReadeckConfig, SiteConfig, SourceConfig
from modernreformation_sync.models import Article, ResourceType


def test_live_test_cleanup_deletes_only_created_bookmarks(monkeypatch) -> None:
    deleted: list[str] = []

    class FakeReadeckClient:
        def __init__(self, config: ReadeckConfig) -> None:
            self.config = config

        def push_article(self, article: Article, html: str) -> str:
            return f"created-{article.slug}"

        def get_bookmark(self, bookmark_id: str) -> dict[str, object]:
            return {"loaded": True}

        def download_bookmark_file(self, bookmark_id: str, name: str) -> bytes:
            if name == "article.md":
                return b"Original markdown"
            return make_epub()

        def delete_bookmark(self, bookmark_id: str) -> None:
            deleted.append(bookmark_id)

    article = make_article()
    monkeypatch.setattr(cli, "fetch_articles", lambda config: [article])
    monkeypatch.setattr(
        cli,
        "maybe_translate_articles",
        lambda articles, config, cache_dir, bible_config=None: None,
    )
    monkeypatch.setattr(
        cli,
        "write_static_site",
        lambda articles, config: {article.slug: "<p>x</p>"},
    )
    monkeypatch.setattr(cli, "ReadeckClient", FakeReadeckClient)

    cli.run_live_test(
        AppConfig(readeck=ReadeckConfig(enabled=True, base_url="https://r", token="t")),
        limit=1,
        debug_label="mr-debug",
        cleanup=True,
        wait_seconds=1,
    )

    assert deleted == ["created-essays/title"]


def test_fetch_articles_merges_latest_incremental_and_state_articles(
    monkeypatch,
    tmp_path,
) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text(
        """
{
  "latest": [
    {
      "slug": "essays/old",
      "url": "https://www.modernreformation.org/resources/essays/old",
      "title": "Old",
      "translated_title": "Old",
      "published": "2026-05-20T00:00:00+00:00"
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )
    latest = make_article("essays/latest", datetime(2026, 5, 27, tzinfo=UTC))
    incremental = make_article("essays/new", datetime(2026, 5, 26, tzinfo=UTC))
    old = make_article("essays/old", datetime(2026, 5, 20, tzinfo=UTC))

    class FakeSanityClient:
        def __init__(self, source: SourceConfig) -> None:
            self.source = source

        def fetch_latest(self) -> list[Article]:
            return [latest]

        def fetch_since(self, since: datetime) -> list[Article]:
            assert since == datetime(2026, 5, 20, tzinfo=UTC)
            return [incremental]

        def fetch_by_slug(self, slug: str) -> Article | None:
            assert slug == "essays/old"
            return old

    monkeypatch.setattr(cli, "SanityClient", FakeSanityClient)

    articles = cli.fetch_articles(AppConfig(site=SiteConfig(state_file=state_file)))

    assert [article.slug for article in articles] == ["essays/latest", "essays/new", "essays/old"]


def make_article(
    slug: str = "essays/title",
    publish_date: datetime | None = None,
) -> Article:
    return Article(
        title="Title",
        slug=slug,
        url=f"https://www.modernreformation.org/resources/{slug}",
        publish_date=publish_date or datetime(2026, 5, 26, tzinfo=UTC),
        resource_type=ResourceType(name="Essays", slug="essays"),
        authors=[],
        excerpt=[],
        content=[],
        translated_title="标题",
    )


def make_epub() -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("OEBPS/content.xhtml", "<html>Original markdown</html>" + ("x" * 1200))
        archive.writestr("OEBPS/image.png", b"png")
    return buffer.getvalue()

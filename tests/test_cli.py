from __future__ import annotations

from datetime import UTC, datetime

from modernreformation_sync import cli
from modernreformation_sync.config import AppConfig, ReadeckConfig
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


def make_article() -> Article:
    return Article(
        title="Title",
        slug="essays/title",
        url="https://www.modernreformation.org/resources/essays/title",
        publish_date=datetime(2026, 5, 26, tzinfo=UTC),
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

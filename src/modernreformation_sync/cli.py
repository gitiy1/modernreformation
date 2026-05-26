from __future__ import annotations

import argparse
import json
import logging
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryFile

from dateutil.parser import isoparse

from modernreformation_sync.builder import prepare_articles, write_static_site
from modernreformation_sync.config import AppConfig, load_config, load_env_file
from modernreformation_sync.readeck import ReadeckClient, maybe_push_to_readeck
from modernreformation_sync.sanity import SanityClient
from modernreformation_sync.state import JsonStore
from modernreformation_sync.translator import maybe_translate_articles

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mr-sync")
    parser.add_argument("--config", default="config.yml", help="Path to YAML config")
    parser.add_argument("--env-file", default=".env", help="Optional dotenv file for local secrets")
    parser.add_argument("--log-level", default="INFO")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("fetch", help="Fetch latest Modern Reformation metadata")
    subparsers.add_parser("build", help="Build static HTML and RSS feeds")
    subparsers.add_parser("push-readeck", help="Push built articles to Readeck")
    subparsers.add_parser("run", help="Fetch, translate, build, and optionally push")
    live_test = subparsers.add_parser("live-test", help="Run a safe live LLM/Readeck test")
    live_test.add_argument("--limit", type=int, default=3)
    live_test.add_argument("--debug-label", default="mr-debug")
    live_test.add_argument("--cleanup", action="store_true")
    live_test.add_argument("--wait-seconds", type=int, default=90)
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    load_env_file(Path(args.env_file))
    config = load_config(Path(args.config))

    match args.command:
        case "fetch":
            articles = fetch_articles(config)
            write_fetch_snapshot(articles, config)
        case "build":
            articles = fetch_articles(config)
            build_articles(articles, config)
        case "push-readeck":
            articles = fetch_articles(config)
            rendered = build_articles(articles, config)
            maybe_push_to_readeck(articles, rendered, config.readeck)
        case "run":
            articles = fetch_articles(config)
            rendered = build_articles(articles, config)
            maybe_push_to_readeck(articles, rendered, config.readeck)
        case "live-test":
            run_live_test(
                config,
                limit=args.limit,
                debug_label=args.debug_label,
                cleanup=args.cleanup,
                wait_seconds=args.wait_seconds,
            )
    return 0


def fetch_articles(config: AppConfig):
    client = SanityClient(config.source)
    latest = client.fetch_latest()
    if not config.source.include_state_articles:
        return latest

    store = JsonStore(config.site.state_file)
    previous_entries = store.get("latest", [])
    previous_slugs = [
        str(entry.get("slug") or "")
        for entry in previous_entries
        if isinstance(entry, dict) and entry.get("slug")
    ]
    previous_dates = [
        isoparse(str(entry["published"]))
        for entry in previous_entries
        if isinstance(entry, dict) and entry.get("published")
    ]

    articles_by_slug = {article.slug: article for article in latest}
    if previous_dates:
        try:
            for article in client.fetch_since(max(previous_dates)):
                articles_by_slug[article.slug] = article
        except Exception:
            logger.exception("Could not fetch incremental articles; continuing with latest window")

    for slug in previous_slugs:
        if slug in articles_by_slug:
            continue
        try:
            article = client.fetch_by_slug(slug)
        except Exception:
            logger.exception("Could not refresh state article %s; omitting it", slug)
            continue
        if article:
            articles_by_slug[article.slug] = article

    return sorted(articles_by_slug.values(), key=lambda article: article.publish_date, reverse=True)


def build_articles(articles, config: AppConfig) -> dict[str, str]:
    prepare_articles(articles)
    maybe_translate_articles(
        articles,
        config.translation,
        config.site.cache_dir / "translations",
        config.bible,
    )
    rendered = write_static_site(articles, config)
    store = JsonStore(config.site.state_file)
    store.set(
        "latest",
        [
            {
                "slug": article.slug,
                "url": article.url,
                "title": article.title,
                "translated_title": article.translated_title,
                "published": article.publish_date.isoformat(),
            }
            for article in articles
        ],
    )
    store.save()
    return rendered


def write_fetch_snapshot(articles, config: AppConfig) -> None:
    config.site.cache_dir.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "slug": article.slug,
            "url": article.url,
            "title": article.title,
            "published": article.publish_date.isoformat(),
        }
        for article in articles
    ]
    (config.site.cache_dir / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


@dataclass
class LiveTestResult:
    bookmark_id: str
    title: str
    url: str
    loaded: bool
    markdown_bytes: int
    epub_bytes: int
    epub_images: int
    cleaned_up: bool = False


def run_live_test(
    config: AppConfig,
    *,
    limit: int,
    debug_label: str,
    cleanup: bool,
    wait_seconds: int,
) -> None:
    source = config.source.model_copy(update={"limit": limit})
    readeck_config = config.readeck.model_copy(
        update={
            "enabled": True,
            "label": debug_label,
            "translated_label": f"{debug_label}-translated",
            "collection_name": "",
            "keep": max(limit, 1),
        }
    )
    test_config = config.model_copy(update={"source": source, "readeck": readeck_config})
    articles = fetch_articles(test_config)
    prepare_articles(articles)
    maybe_translate_articles(
        articles,
        test_config.translation,
        test_config.site.cache_dir / "translations",
        test_config.bible,
    )
    rendered = write_static_site(articles, test_config)
    client = ReadeckClient(readeck_config)
    results: list[LiveTestResult] = []
    created_ids: list[str] = []
    existing_ids = (
        {bookmark.id for bookmark in client.list_synced_bookmarks()}
        if hasattr(client, "list_synced_bookmarks")
        else set()
    )
    try:
        for article in articles:
            bookmark_id = client.push_article(article, rendered[article.slug])
            if not bookmark_id:
                raise RuntimeError(f"Readeck did not return a bookmark id for {article.url}")
            if bookmark_id not in existing_ids:
                created_ids.append(bookmark_id)
            loaded = wait_for_bookmark_loaded(client, bookmark_id, wait_seconds=wait_seconds)
            markdown = client.download_bookmark_file(bookmark_id, "article.md")
            epub = client.download_bookmark_file(bookmark_id, "article.epub")
            write_live_test_export(
                test_config.site.cache_dir / "live-test-exports",
                article.slug,
                markdown,
                epub,
            )
            epub_images = count_epub_images(epub)
            validate_export(article.translated_title or article.title, markdown, epub)
            results.append(
                LiveTestResult(
                    bookmark_id=bookmark_id,
                    title=article.translated_title or article.title,
                    url=article.url,
                    loaded=loaded,
                    markdown_bytes=len(markdown),
                    epub_bytes=len(epub),
                    epub_images=epub_images,
                )
            )
    finally:
        if cleanup:
            for bookmark_id in created_ids:
                client.delete_bookmark(bookmark_id)
            for result in results:
                result.cleaned_up = True
    write_live_test_report(test_config.site.cache_dir / "live-test-report.json", results)


def wait_for_bookmark_loaded(
    client: ReadeckClient,
    bookmark_id: str,
    *,
    wait_seconds: int,
) -> bool:
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        bookmark = client.get_bookmark(bookmark_id)
        if bookmark.get("loaded") is True:
            return True
        if bookmark.get("state") == 1:
            raise RuntimeError(f"Readeck bookmark {bookmark_id} entered error state")
        time.sleep(3)
    return False


def count_epub_images(epub: bytes) -> int:
    with TemporaryFile() as temp:
        temp.write(epub)
        temp.seek(0)
        with zipfile.ZipFile(temp) as archive:
            return sum(
                1
                for name in archive.namelist()
                if name.lower().endswith((".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"))
            )


def validate_export(title: str, markdown: bytes, epub: bytes) -> None:
    markdown_text = markdown.decode("utf-8", errors="ignore")
    if title[:20] not in markdown_text and "Original" not in markdown_text:
        raise RuntimeError("Downloaded Markdown does not look like the translated article")
    if len(epub) < 1000:
        raise RuntimeError("Downloaded EPUB is unexpectedly small")


def write_live_test_report(path: Path, results: list[LiveTestResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_live_test_export(path: Path, slug: str, markdown: bytes, epub: bytes) -> None:
    path.mkdir(parents=True, exist_ok=True)
    name = slug.replace("/", "__")
    (path / f"{name}.md").write_bytes(markdown)
    (path / f"{name}.epub").write_bytes(epub)


if __name__ == "__main__":
    raise SystemExit(main())

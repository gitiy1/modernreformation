from datetime import UTC, datetime

import httpx

from modernreformation_sync.config import ReadeckConfig
from modernreformation_sync.models import Article, ResourceType
from modernreformation_sync.readeck import ReadeckClient


def test_ensure_collection_creates_label_collection() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        assert request.method == "POST"
        assert request.url.path == "/api/bookmarks/collections"
        assert request.headers["Authorization"] == "Bearer token"
        assert b'"labels":"modern-reformation"' in request.content
        return httpx.Response(201, json={"status": 201, "message": "Collection created"})

    client = ReadeckClient(
        ReadeckConfig(enabled=True, base_url="https://readeck.test", token="token"),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.ensure_collection()

    assert [request.method for request in requests] == ["GET", "POST"]


def test_prune_deletes_bookmarks_after_keep_count() -> None:
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert request.url.params["page"] == "1"
            return httpx.Response(
                200,
                json=[
                    {"id": "a", "url": "https://a", "title": "A", "created": "1"},
                    {
                        "id": "b",
                        "url": "https://www.modernreformation.org/resources/essays/b",
                        "title": "B",
                        "created": "2",
                        "labels": ["modern-reformation", "translated"],
                    },
                    {
                        "id": "c",
                        "url": "https://example.test/c",
                        "title": "C",
                        "created": "3",
                        "labels": ["modern-reformation", "translated"],
                    },
                ],
            )
        deleted.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(204)

    client = ReadeckClient(
        ReadeckConfig(enabled=True, base_url="https://readeck.test", token="token", keep=1),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.prune() == []
    assert deleted == []


def test_prune_deletes_only_owned_bookmarks_after_keep_count() -> None:
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "a",
                        "url": "https://www.modernreformation.org/resources/essays/a",
                        "title": "A",
                        "created": "1",
                        "labels": ["modern-reformation", "translated"],
                    },
                    {
                        "id": "b",
                        "url": "https://www.modernreformation.org/resources/essays/b",
                        "title": "B",
                        "created": "2",
                        "labels": ["modern-reformation", "translated"],
                    },
                    {
                        "id": "c",
                        "url": "https://www.modernreformation.org/resources/essays/c",
                        "title": "C",
                        "created": "3",
                        "labels": ["modern-reformation"],
                    },
                ],
            )
        deleted.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(204)

    client = ReadeckClient(
        ReadeckConfig(enabled=True, base_url="https://readeck.test", token="token", keep=1),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.prune() == ["b"]
    assert deleted == ["b"]


def test_push_article_remote_mode_uses_json_html() -> None:
    posted: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[])
        posted["content_type"] = request.headers["content-type"]
        posted["body"] = request.content
        return httpx.Response(202, headers={"bookmark-id": "abc"}, json={})

    client = ReadeckClient(
        ReadeckConfig(
            enabled=True,
            base_url="https://readeck.test",
            token="token",
            image_mode="remote",
        ),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    bookmark_id = client.push_article(make_article(), "<p>Hello</p>")

    assert bookmark_id == "abc"
    assert posted["content_type"] == "application/json"
    assert b'"html":"<p>Hello</p>"' in posted["body"]


def test_push_article_multipart_includes_html_and_image_resources() -> None:
    post_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_body
        if request.method == "GET" and request.url.host == "readeck.test":
            return httpx.Response(200, json=[])
        if request.method == "GET" and request.url.host == "cdn.test":
            return httpx.Response(200, headers={"content-type": "image/png"}, content=b"png")
        post_body = request.content
        return httpx.Response(202, headers={"bookmark-id": "abc"}, json={})

    client = ReadeckClient(
        ReadeckConfig(enabled=True, base_url="https://readeck.test", token="token"),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.push_article(make_article(), '<p>Hello</p><img src="https://cdn.test/a.png">')

    assert post_body.count(b'name="resource"; filename="_"') == 1
    assert b"Location: https://www.modernreformation.org/resources/essays/title" in post_body
    assert b"Location: https://cdn.test/a.png" not in post_body
    assert b"Content-Type: text/html; charset=utf-8" in post_body
    assert post_body.count(b'name="labels"') == 3


def test_push_article_multipart_includes_trusted_image_resources() -> None:
    post_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_body
        if request.method == "GET" and request.url.host == "readeck.test":
            return httpx.Response(200, json=[])
        if request.method == "GET" and request.url.host == "cdn.test":
            return httpx.Response(200, headers={"content-type": "image/png"}, content=b"png")
        post_body = request.content
        return httpx.Response(202, headers={"bookmark-id": "abc"}, json={})

    client = ReadeckClient(
        ReadeckConfig(
            enabled=True,
            base_url="https://readeck.test",
            token="token",
            allowed_image_hosts=["cdn.test"],
        ),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.push_article(make_article(), '<p>Hello</p><img src="https://cdn.test/a.png">')

    assert post_body.count(b'name="resource"; filename="_"') == 2
    assert b"Location: https://cdn.test/a.png" in post_body
    assert b"Content-Type: image/png" in post_body


def test_push_article_multipart_skips_failed_image_download() -> None:
    post_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_body
        if request.method == "GET" and request.url.host == "readeck.test":
            return httpx.Response(200, json=[])
        if request.method == "GET" and request.url.host == "cdn.test":
            return httpx.Response(404)
        post_body = request.content
        return httpx.Response(202, headers={"bookmark-id": "abc"}, json={})

    client = ReadeckClient(
        ReadeckConfig(
            enabled=True,
            base_url="https://readeck.test",
            token="token",
            allowed_image_hosts=["cdn.test"],
        ),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.push_article(make_article(), '<p>Hello</p><img src="https://cdn.test/missing.png">')

    assert post_body.count(b'name="resource"; filename="_"') == 1
    assert b"Content-Type: text/html; charset=utf-8" in post_body
    assert b"Location: https://cdn.test/missing.png" not in post_body


def test_push_article_replaces_existing_owned_bookmark() -> None:
    methods: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "old",
                        "url": "https://www.modernreformation.org/resources/essays/title",
                        "title": "Old",
                        "created": "1",
                        "labels": ["modern-reformation", "translated"],
                    }
                ],
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(202, headers={"bookmark-id": "new"}, json={})

    client = ReadeckClient(
        ReadeckConfig(enabled=True, base_url="https://readeck.test", token="token"),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.push_article(make_article(), "<p>Hello</p>") == "new"
    assert ("DELETE", "/api/bookmarks/old") in methods
    assert ("POST", "/api/bookmarks") in methods


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

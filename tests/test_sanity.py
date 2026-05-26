import json
from datetime import UTC, datetime

import httpx

from modernreformation_sync.config import SourceConfig
from modernreformation_sync.sanity import SanityClient


def test_fetch_latest_uses_sanity_query_with_type_filter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert "_type" in params["query"]
        assert json.loads(params["$types"]) == ["essays"]
        assert json.loads(params["$limit"]) == 0
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "title": "Title",
                        "slug": "essays/title",
                        "publish_date": "2026-05-26T15:00:00.000Z",
                        "resource_type": {"name": "Essays", "slug": "essays"},
                        "author": [
                            {
                                "title": "Ronnie Brown",
                                "slug": "ronnie-brown",
                                "author_bio": "Bio",
                                "author_image": {
                                    "asset": {"url": "https://example.test/ronnie.png"}
                                },
                            }
                        ],
                        "primary_topic": {"title": "Baptism", "slug": "baptism"},
                        "secondary_topics": [{"title": "The Covenants", "slug": "the-covenants"}],
                        "excerpt": [],
                        "resource_content": [],
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sanity = SanityClient(SourceConfig(limit=1, resource_types=["essays"]), client=client)

    articles = sanity.fetch_latest()

    assert articles[0].url == "https://www.modernreformation.org/resources/essays/title"
    assert articles[0].authors[0].bio == "Bio"
    assert articles[0].authors[0].image_url == "https://example.test/ronnie.png"
    assert [topic.title for topic in articles[0].topics] == ["Baptism", "The Covenants"]


def test_parse_article_allows_primary_topic_without_secondary_topics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "title": "Title",
                        "slug": "essays/title",
                        "publish_date": "2026-05-26T15:00:00.000Z",
                        "resource_type": {"name": "Essays", "slug": "essays"},
                        "author": [],
                        "primary_topic": {"title": "Grace", "slug": "grace"},
                        "secondary_topics": None,
                        "excerpt": [],
                        "resource_content": [],
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sanity = SanityClient(SourceConfig(limit=1), client=client)

    articles = sanity.fetch_latest()

    assert [topic.title for topic in articles[0].topics] == ["Grace"]


def test_fetch_since_uses_publish_date_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert "publish_date > $since" in params["query"]
        assert json.loads(params["$since"]) == "2026-05-20T00:00:00+00:00"
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "title": "New",
                        "slug": "essays/new",
                        "publish_date": "2026-05-21T00:00:00.000Z",
                        "resource_type": {"name": "Essays", "slug": "essays"},
                        "author": [],
                        "excerpt": [],
                        "resource_content": [],
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sanity = SanityClient(SourceConfig(limit=1), client=client)

    articles = sanity.fetch_since(datetime(2026, 5, 20, tzinfo=UTC))

    assert [article.slug for article in articles] == ["essays/new"]

from datetime import UTC, datetime

from modernreformation_sync.models import Article, Author, ResourceType, Topic
from modernreformation_sync.render import render_portable_text, render_resource_metadata


def test_render_portable_text_preserves_basic_structure() -> None:
    blocks = [
        {
            "_type": "block",
            "style": "h2",
            "children": [{"text": "Heading", "marks": []}],
            "markDefs": [],
        },
        {
            "_type": "block",
            "style": "normal",
            "children": [
                {"text": "Hello ", "marks": []},
                {"text": "world", "marks": ["strong"]},
            ],
            "markDefs": [],
        },
        {
            "_type": "block",
            "style": "normal",
            "listItem": "number",
            "level": 1,
            "children": [{"text": "One", "marks": []}],
            "markDefs": [],
        },
    ]

    html = render_portable_text(blocks)

    assert "<h2>Heading</h2>" in html
    assert "<p>Hello <strong>world</strong></p>" in html
    assert "<ol>" in html
    assert "<li>One</li>" in html


def test_render_portable_text_wraps_lists_without_explicit_level() -> None:
    html = render_portable_text(
        [
            {
                "_type": "block",
                "style": "normal",
                "listItem": "bullet",
                "children": [{"text": "One", "marks": []}],
                "markDefs": [],
            },
            {
                "_type": "block",
                "style": "normal",
                "children": [{"text": "After", "marks": []}],
                "markDefs": [],
            },
        ]
    )

    assert html == "<ul>\n<li>One</li>\n</ul>\n<p>After</p>"


def test_render_portable_text_preserves_embedded_html_table() -> None:
    html = render_portable_text(
        [
            {
                "_type": "block",
                "style": "normal",
                "children": [{"text": "The parallel is striking:", "marks": []}],
                "markDefs": [],
            },
            {
                "_type": "embedded_HTML",
                "embed": (
                    "<table><tr><td><strong>Deuteronomy 30:1-4</strong></td>"
                    "<td><strong>Acts 2:39</strong></td></tr>"
                    '<tr><td>"Your children"</td><td>"Your children"</td></tr></table>'
                ),
                "isScript": False,
            },
        ]
    )

    assert "<p>The parallel is striking:</p>" in html
    assert '<div class="embedded-html"><table>' in html
    assert "<td><strong>Deuteronomy 30:1-4</strong></td>" in html
    assert '<td>"Your children"</td>' in html


def test_render_portable_text_skips_script_embedded_html() -> None:
    html = render_portable_text(
        [
            {
                "_type": "embedded_HTML",
                "embed": '<script>alert("x")</script><table><tr><td>Safe</td></tr></table>',
                "isScript": True,
            },
        ]
    )

    assert html == ""


def test_render_portable_text_renders_footnotes_with_backlinks() -> None:
    html = render_portable_text(
        [
            {
                "_type": "block",
                "style": "normal",
                "children": [
                    {"text": "A sentence", "marks": []},
                    {"text": ".", "marks": ["note-1"]},
                ],
                "markDefs": [
                    {
                        "_key": "note-1",
                        "_type": "footnote",
                        "note": [
                            {
                                "_type": "block",
                                "style": "normal",
                                "children": [{"text": "Footnote text", "marks": []}],
                                "markDefs": [],
                            }
                        ],
                    }
                ],
            }
        ]
    )

    assert '<sup id="fnref-1"><a href="#fn-1" class="footnote-ref">1</a></sup>' in html
    assert '<section class="footnotes">' in html
    assert '<li id="fn-1">Footnote text ' in html
    assert '<a href="#fnref-1" class="footnote-backref">&larr;</a>' in html


def test_render_portable_text_can_prefix_footnote_ids() -> None:
    html = render_portable_text(
        [
            {
                "_type": "block",
                "style": "normal",
                "children": [{"text": "Text", "marks": ["note-1"]}],
                "markDefs": [
                    {
                        "_key": "note-1",
                        "_type": "footnote",
                        "note": [{"_type": "block", "children": [{"text": "Note", "marks": []}]}],
                    }
                ],
            }
        ],
        id_prefix="chunk 1",
    )

    assert 'id="chunk-1-fnref-1"' in html
    assert 'href="#chunk-1-fn-1"' in html
    assert 'id="chunk-1-fn-1"' in html
    assert 'href="#chunk-1-fnref-1"' in html


def test_render_portable_text_preserves_horizontal_rule_pull_quote_and_block_image() -> None:
    html = render_portable_text(
        [
            {"_type": "horizontal_rule"},
            {
                "_type": "pull_quote",
                "text": [
                    {
                        "_type": "block",
                        "style": "normal",
                        "children": [{"text": "Quoted text", "marks": ["strong"]}],
                        "markDefs": [],
                    }
                ],
            },
            {
                "_type": "block_image",
                "image_alt": "A church",
                "label": "Church caption",
                "image_file": {
                    "asset": {
                        "_ref": "image-0ba7ae41cb850f9b394058d2ca9850892071f0c6-1445x1087-png"
                    }
                },
            },
        ]
    )

    assert "<hr>" in html
    assert '<blockquote class="pull-quote"><p><strong>Quoted text</strong></p></blockquote>' in html
    assert (
        'src="https://cdn.sanity.io/images/sxo7ym47/production/'
        '0ba7ae41cb850f9b394058d2ca9850892071f0c6-1445x1087.png"'
    ) in html
    assert 'alt="A church"' in html
    assert "<figcaption>Church caption</figcaption>" in html


def test_render_resource_metadata_includes_author_topics_and_date() -> None:
    article = Article(
        title="Title",
        slug="essays/title",
        url="https://example.test",
        publish_date=datetime(2026, 5, 26, tzinfo=UTC),
        resource_type=ResourceType(name="Essays", slug="essays"),
        authors=[
            Author(
                title="Ronnie Brown",
                bio="Ronnie Brown has served as a ruling elder.",
                image_url="https://example.test/ronnie.png",
            )
        ],
        topics=[Topic(title="Baptism"), Topic(title="The Covenants")],
        excerpt=[],
        content=[],
    )

    html = render_resource_metadata(article)

    assert '<section class="resource-meta">' in html
    assert 'src="https://example.test/ronnie.png"' in html
    assert "Ronnie Brown has served as a ruling elder." in html
    assert '<span class="topic">Baptism</span>' in html
    assert '<span class="topic">The Covenants</span>' in html
    assert "Tuesday, May 26, 2026" in html


def test_article_model_smoke() -> None:
    article = Article(
        title="Title",
        slug="essays/title",
        url="https://example.test",
        publish_date=datetime.now(UTC),
        resource_type=ResourceType(name="Essays", slug="essays"),
        authors=[],
        excerpt=[],
        content=[],
    )

    assert article.id == "modernreformation:essays/title"

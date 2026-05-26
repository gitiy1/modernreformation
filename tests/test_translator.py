from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import modernreformation_sync.translator as translator_module
from modernreformation_sync.bible import BibleIndex
from modernreformation_sync.config import TranslationConfig
from modernreformation_sync.models import Article, ResourceType
from modernreformation_sync.translator import (
    OpenAITranslator,
    TranslationBudgetExceeded,
    TranslationCache,
    batch_split,
    build_bilingual_html,
    clean_model_output,
    contains_bible_reference,
    maybe_translate_articles,
    sanitize_translated_html,
)


def test_batch_split_only_uses_standalone_separator() -> None:
    text = "one\nnot %% here\n%%\ntwo\n  %%  \nthree"

    assert batch_split(text, "%%") == ["one\nnot %% here", "two", "three"]


def test_clean_model_output_removes_visible_reasoning_and_fences() -> None:
    text = "<thought>private analysis</thought>```html\n<p>译文</p>\n```"

    assert clean_model_output(text) == "<p>译文</p>"


def test_translation_cache_cleans_legacy_reasoning(tmp_path: Path) -> None:
    cache = TranslationCache(tmp_path)
    (tmp_path / "abc.json").write_text(
        '{"text": "<thought>analysis</thought>译文"}',
        encoding="utf-8",
    )

    assert cache.get("abc") == "译文"


def test_sanitize_translated_html_removes_active_content() -> None:
    html = sanitize_translated_html(
        '<p onclick="x()">译文</p><a href="javascript:alert(1)">bad</a><script>x()</script>'
    )

    assert html == "<p>译文</p><a>bad</a>"


def test_translator_round_robins_multiple_api_keys(monkeypatch, tmp_path: Path) -> None:
    used_keys: list[str] = []

    class FakeCompletions:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def create(self, **kwargs: object) -> object:
            used_keys.append(self.api_key)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="<p>译文</p>"))]
            )

    class FakeOpenAI:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions(api_key))

    monkeypatch.setattr(translator_module, "OpenAI", FakeOpenAI)
    translator = OpenAITranslator(
        TranslationConfig(api_keys=["key-a", "key-b"]),
        TranslationCache(tmp_path),
    )

    assert translator._request("a", text_type="html", context="", is_batch=False) == "<p>译文</p>"
    assert translator._request("b", text_type="html", context="", is_batch=False) == "<p>译文</p>"
    assert translator._request("c", text_type="html", context="", is_batch=False) == "<p>译文</p>"
    assert used_keys == ["key-a", "key-b", "key-a"]


def test_build_bilingual_html_places_full_original_after_translation_without_images() -> None:
    html = build_bilingual_html(
        translated_html='<p>中文一</p>\n<figure><img src="cover.png"></figure>\n<p>中文二</p>',
        original_html=(
            '<p>English one</p>\n<figure><img src="cover.png"></figure>\n<p>English two</p>'
        ),
    )

    assert html == (
        '<p>中文一</p>\n<figure><img src="cover.png"></figure>\n<p>中文二</p>'
        '\n<div class="original">\n<p>English one</p>\n\n<p>English two</p>\n</div>'
    )


def test_build_bilingual_html_keeps_author_image_in_original_metadata() -> None:
    html = build_bilingual_html(
        translated_html=(
            '<p>中文</p><section class="resource-meta"><img src="author.png"></section>'
        ),
        original_html=(
            '<p>English</p><figure><img src="cover.png"></figure>'
            '<section class="resource-meta"><img src="author.png"></section>'
        ),
    )

    assert '<figure><img src="cover.png"></figure>' not in html
    assert html.count('<img src="author.png">') == 2


def test_translator_runs_bible_lookup_tool(tmp_path: Path) -> None:
    translator = OpenAITranslator(
        TranslationConfig(api_key="key"),
        TranslationCache(tmp_path),
        BibleIndex({("ACT", 2, 39): "因为这应许是给你们和你们的儿女"}),
    )
    tool_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="lookup_bible", arguments='{"reference": "Acts 2:39"}'),
    )

    result = translator._run_tool_call(tool_call)

    assert result == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": (
            '{"reference": "Acts 2:39", "verses": [{"book": "ACT", "chapter": 2, '
            '"verse": 39, "reference": "ACT.2.39", '
            '"text": "因为这应许是给你们和你们的儿女"}], '
            '"text": "39 因为这应许是给你们和你们的儿女"}'
        ),
    }


def test_translator_runs_structured_bible_lookup_tool(tmp_path: Path) -> None:
    translator = OpenAITranslator(
        TranslationConfig(api_key="key"),
        TranslationCache(tmp_path),
        BibleIndex({("JHN", 6, 45): "他们都要蒙神的教训。"}),
    )
    tool_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(
            name="lookup_bible",
            arguments=(
                '{"start_book": "John", "start_chapter": 6, "start_verse": 45, '
                '"reference_text": "John 6:45"}'
            ),
        ),
    )

    result = translator._run_tool_call(tool_call)

    assert '"reference": "JHN 6:45"' in result["content"]
    assert '"text": "45 他们都要蒙神的教训。"' in result["content"]


def test_translator_uses_reference_text_when_model_omits_range_end(tmp_path: Path) -> None:
    translator = OpenAITranslator(
        TranslationConfig(api_key="key"),
        TranslationCache(tmp_path),
        BibleIndex(
            {
                ("DEU", 30, 1): "等到这一切事临到你",
                ("DEU", 30, 2): "你和你的儿女若尽心尽性归向耶和华",
            }
        ),
    )
    tool_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(
            name="lookup_bible",
            arguments=(
                '{"start_book": "Deut", "start_chapter": 30, "start_verse": 1, '
                '"reference_text": "Deut. 30:1-2"}'
            ),
        ),
    )

    result = translator._run_tool_call(tool_call)

    assert '"reference": "Deut. 30:1-2"' in result["content"]
    assert (
        '"text": "1 等到这一切事临到你\\n2 你和你的儿女若尽心尽性归向耶和华"' in result["content"]
    )


def test_contains_bible_reference_detects_common_article_references() -> None:
    assert contains_bible_reference("The promise is for you (Acts 2:39).")
    assert contains_bible_reference("heart circumcision appears in Deut. 30:6")
    assert contains_bible_reference("children are holy in 1 Cor. 7:14")
    assert contains_bible_reference("They shall all be taught by God (John 6:45).")
    assert contains_bible_reference("他们都要蒙神的教训(约翰福音 6\uff1a45).")
    assert not contains_bible_reference("This paragraph has no citation.")
    assert not contains_bible_reference("The meeting starts at 7:14 tomorrow.")


def test_batch_count_mismatch_falls_back_to_individual(tmp_path: Path) -> None:
    translator = FakeTranslator(
        TranslationConfig(
            api_key="key",
            max_retries=1,
            base_retry_delay_seconds=0,
            batch_separator="%%",
        ),
        tmp_path,
    )

    result = translator._translate_batch(["alpha", "beta"], text_type="html", context="ctx")

    assert result == ["translated:alpha", "translated:beta"]
    assert translator.requests == [
        ("alpha\n\n%%\n\nbeta", True),
        ("alpha\n\n%%\n\nbeta", True),
        ("alpha", False),
        ("beta", False),
    ]


def test_budget_keep_original_sets_all_remaining_articles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    articles = [make_article("first"), make_article("second")]
    monkeypatch.setattr(translator_module, "OpenAITranslator", FakeBudgetTranslator)

    maybe_translate_articles(
        articles,
        TranslationConfig(
            enabled=True,
            api_key="key",
            budget_exceeded="keep_original",
        ),
        tmp_path,
    )

    assert [article.translated_title for article in articles] == ["first", "second"]
    assert [article.translated_html for article in articles] == ["<p>first</p>", "<p>second</p>"]


def test_budget_fail_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(translator_module, "OpenAITranslator", FakeBudgetTranslator)

    with pytest.raises(TranslationBudgetExceeded):
        maybe_translate_articles(
            [make_article("first")],
            TranslationConfig(
                enabled=True,
                api_key="key",
                budget_exceeded="fail",
            ),
            tmp_path,
        )


class FakeTranslator(OpenAITranslator):
    def __init__(self, config: TranslationConfig, cache_dir: Path) -> None:
        super().__init__(config, cache=DummyCache(cache_dir))
        self.requests: list[tuple[str, bool]] = []

    def _request(self, text: str, *, text_type: str, context: str, is_batch: bool) -> str:
        self.requests.append((text, is_batch))
        if is_batch:
            return "only one result"
        return f"translated:{text}"


class DummyCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir

    def get(self, key: str) -> str | None:
        return None

    def set(self, key: str, text: str) -> None:
        pass


class FakeBudgetTranslator:
    def __init__(self, *args: object) -> None:
        pass

    def translate_article(self, article: Article) -> None:
        raise TranslationBudgetExceeded("test budget")


def make_article(title: str) -> Article:
    return Article(
        title=title,
        slug=title,
        url=f"https://example.test/{title}",
        publish_date=datetime(2026, 5, 26, tzinfo=UTC),
        resource_type=ResourceType(name="Essays", slug="essays"),
        authors=[],
        excerpt=[],
        content=[
            {
                "_type": "block",
                "style": "normal",
                "children": [{"_type": "span", "text": title, "marks": []}],
            }
        ],
        original_html=f"<p>{title}</p>",
    )

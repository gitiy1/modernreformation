from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Iterable
from pathlib import Path

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from modernreformation_sync.bible import BibleIndex, bible_reference_pattern
from modernreformation_sync.config import BibleConfig, TranslationConfig
from modernreformation_sync.models import Article, PortableBlock
from modernreformation_sync.render import (
    render_portable_text,
    render_resource_metadata,
    strip_unsafe_embedded_html,
    text_from_blocks,
)

logger = logging.getLogger(__name__)

THOUGHT_RE = re.compile(r"<thought\b[^>]*>.*?</thought>", re.IGNORECASE | re.DOTALL)
FENCED_HTML_RE = re.compile(r"^```(?:html)?\s*(.*?)\s*```$", re.IGNORECASE | re.DOTALL)
TRANSLATION_PROMPT_VERSION = "2026-05-27-cuvnp-structured-bible-lookup-metadata"
BIBLE_REFERENCE_RE = bible_reference_pattern()
BIBLE_LOOKUP_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_bible",
        "description": (
            "Look up Chinese Union Version with New Punctuation, Shen Edition text "
            "for a structured Bible reference such as Acts 2:39 or Deut. 30:1-4. "
            "Use this for direct Scripture quotations and recognizable biblical phrases. "
            "For a range like Deut. 30:1-4, set end_verse to 4."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_book": {
                    "type": "string",
                    "description": (
                        "Start book name or abbreviation, for example Acts, Deut, "
                        "John, 1 Cor, or 申命记."
                    ),
                },
                "start_chapter": {"type": "integer", "minimum": 1},
                "start_verse": {"type": "integer", "minimum": 1},
                "end_book": {
                    "type": "string",
                    "description": (
                        "End book if the range crosses a book; omit for same-book references."
                    ),
                },
                "end_chapter": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "End chapter; omit for single-verse or same-chapter ranges.",
                },
                "end_verse": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "End verse; omit for single-verse references.",
                },
                "reference_text": {
                    "type": "string",
                    "description": (
                        "The original reference string from the article, for audit/debug only."
                    ),
                },
            },
            "required": ["start_book", "start_chapter", "start_verse"],
            "additionalProperties": False,
        },
    },
}


class TranslationBudgetExceeded(RuntimeError):
    pass


class RateLimiter:
    def __init__(self, *, rpm: int = 0, interval_seconds: float = 0) -> None:
        self.rpm = rpm
        self.interval_seconds = interval_seconds
        self.calls: list[float] = []
        self.last_call = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        if self.interval_seconds and self.last_call:
            delay = self.interval_seconds - (now - self.last_call)
            if delay > 0:
                time.sleep(delay)
        if self.rpm > 0:
            now = time.monotonic()
            window_start = now - 60
            self.calls = [item for item in self.calls if item > window_start]
            if len(self.calls) >= self.rpm:
                wait_for = 60 - (now - self.calls[0]) + 0.1
                logger.info("RPM limit reached; sleeping %.2f seconds", wait_for)
                time.sleep(max(wait_for, 0))
        self.last_call = time.monotonic()
        self.calls.append(self.last_call)


class TranslationCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> str | None:
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        return clean_model_output(json.loads(path.read_text(encoding="utf-8"))["text"])

    def set(self, key: str, text: str) -> None:
        path = self.cache_dir / f"{key}.json"
        text = clean_model_output(text)
        path.write_text(
            json.dumps({"text": text}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


class OpenAITranslator:
    def __init__(
        self,
        config: TranslationConfig,
        cache: TranslationCache,
        bible_index: BibleIndex | None = None,
    ) -> None:
        self.config = config
        self.cache = cache
        self.bible_index = bible_index
        self.clients = [
            OpenAI(api_key=api_key, base_url=config.base_url)
            for api_key in (config.api_keys or [config.api_key])
        ]
        self.client_index = 0
        self.limiter = RateLimiter(
            rpm=config.rpm,
            interval_seconds=config.request_interval_seconds,
        )
        self.request_count = 0

    def next_client(self) -> OpenAI:
        client = self.clients[self.client_index % len(self.clients)]
        self.client_index += 1
        return client

    def translate_article(self, article: Article) -> None:
        article.translated_title = self.translate_text(
            article.title,
            text_type="title",
            cache_namespace=f"title:{article.slug}",
            context=article_context(article),
        )
        chunks = chunk_blocks(article.content, self.config.chunk_chars)
        metadata_for_translation = render_resource_metadata(article)
        metadata_for_display = render_resource_metadata(article)
        originals_for_translation = [
            *[
                render_portable_text(chunk, id_prefix=f"tr-{index}")
                for index, chunk in enumerate(chunks, start=1)
            ],
            metadata_for_translation,
        ]
        originals_for_display = [
            *[
                render_portable_text(chunk, id_prefix=f"orig-{index}")
                for index, chunk in enumerate(chunks, start=1)
            ],
            metadata_for_display,
        ]
        translated = self.translate_many(
            originals_for_translation,
            text_type="html",
            cache_namespace=f"body:{article.slug}",
            context=article_context(article),
        )
        article.translated_html = build_bilingual_html(
            translated_html="\n".join(translated),
            original_html="\n".join(originals_for_display),
        )

    def translate_many(
        self,
        texts: list[str],
        *,
        text_type: str,
        cache_namespace: str,
        context: str,
    ) -> list[str]:
        results: list[str | None] = []
        missing: list[tuple[int, str, str]] = []
        for index, text in enumerate(texts):
            digest = translation_cache_key(
                namespace=f"{cache_namespace}:{index}",
                model=self.config.model,
                target=self.config.target_language,
                text=text,
                context=context,
            )
            cached = self.cache.get(digest)
            if cached is not None and text_type == "html":
                cached = sanitize_translated_html(cached)
            results.append(cached)
            if cached is None:
                missing.append((index, text, digest))
        if not missing:
            return [result or "" for result in results]

        if self.config.batch_enabled:
            for batch in build_batches(
                missing,
                max_items=self.config.max_batch_items,
                max_chars=self.config.max_batch_chars,
            ):
                batch_results = self._translate_batch(
                    [text for _, text, _ in batch],
                    text_type=text_type,
                    context=context,
                )
                for (index, _text, digest), translated in zip(batch, batch_results, strict=True):
                    if text_type == "html":
                        translated = sanitize_translated_html(translated)
                    self.cache.set(digest, translated)
                    results[index] = translated
        else:
            for index, text, digest in missing:
                translated = self._translate_with_budget(text, text_type=text_type, context=context)
                if text_type == "html":
                    translated = sanitize_translated_html(translated)
                self.cache.set(digest, translated)
                results[index] = translated

        return [result or "" for result in results]

    def translate_text(
        self,
        text: str,
        *,
        text_type: str,
        cache_namespace: str,
        context: str,
    ) -> str:
        digest = translation_cache_key(
            namespace=cache_namespace,
            model=self.config.model,
            target=self.config.target_language,
            text=text,
            context=context,
        )
        cached = self.cache.get(digest)
        if cached is not None:
            return cached

        translated = self._translate_with_budget(text, text_type=text_type, context=context)
        self.cache.set(digest, translated)
        return translated

    def _translate_with_budget(
        self,
        text: str,
        *,
        text_type: str,
        context: str,
        is_batch: bool = False,
    ) -> str:
        if (
            self.config.max_requests_per_run
            and self.request_count >= self.config.max_requests_per_run
        ):
            raise TranslationBudgetExceeded(
                f"max_requests_per_run={self.config.max_requests_per_run} reached"
            )

        self.limiter.wait()
        self.request_count += 1
        logger.info("Translating %s with %s", text_type, self.config.model)
        return self._request_with_retries(
            text,
            text_type=text_type,
            context=context,
            is_batch=is_batch,
        )

    def _translate_batch(self, texts: list[str], *, text_type: str, context: str) -> list[str]:
        joined = batch_join(texts, self.config.batch_separator)
        for attempt in range(self.config.max_retries + 1):
            try:
                translated = self._translate_with_budget(
                    joined,
                    text_type=text_type,
                    context=context,
                    is_batch=True,
                )
                results = batch_split(translated, self.config.batch_separator)
                if len(results) != len(texts):
                    raise ValueError(
                        f"batch result count mismatch: expected {len(texts)}, got {len(results)}"
                    )
                return results
            except ValueError as exc:
                logger.warning("Batch translation mismatch: %s", exc)
                if attempt >= self.config.max_retries:
                    logger.warning("Falling back to individual translation for this batch")
                    return [
                        self._translate_with_budget(text, text_type=text_type, context=context)
                        for text in texts
                    ]
                time.sleep(backoff_seconds(self.config.base_retry_delay_seconds, attempt))
        raise RuntimeError("unreachable batch translation state")

    def _request_with_retries(
        self,
        text: str,
        *,
        text_type: str,
        context: str,
        is_batch: bool,
    ) -> str:
        for attempt in range(self.config.max_retries + 1):
            try:
                return self._request(text, text_type=text_type, context=context, is_batch=is_batch)
            except (APIConnectionError, APITimeoutError, APIStatusError) as exc:
                if not should_retry(exc) or attempt >= self.config.max_retries:
                    raise
                delay = retry_delay_seconds(exc, self.config.base_retry_delay_seconds, attempt)
                logger.warning("Translation request failed; retrying in %.1fs: %s", delay, exc)
                time.sleep(delay)
        raise RuntimeError("unreachable retry state")

    def _request(self, text: str, *, text_type: str, context: str, is_batch: bool) -> str:
        if text_type == "title":
            system_prompt = (
                f"Translate the title into {self.config.target_language}. "
                "Return only the translated title, with no explanation. "
                "Prefer established Chinese theological terminology. "
                "Do not include reasoning, analysis, <thought> tags, markdown fences, or notes."
            )
        else:
            system_prompt = (
                "You are translating Modern Reformation articles into "
                f"{self.config.target_language} "
                "for e-reader reading. Use clear, faithful, literary Chinese suitable for Reformed "
                "theology, church history, biblical studies, and book reviews. Preserve HTML tags, "
                "block boundaries, URLs, code, scripture references, names, dates, and citation "
                "markers. Translate visible prose only. Keep doctrinal claims precise. If the "
                "`lookup_bible` tool is available and the input contains Bible references, call it "
                "before translating direct Bible quotations and recognizable quoted biblical "
                "phrases, then use the returned wording in the translation. Use "
                "the Chinese Union Version with New Punctuation, Shen Edition "
                "(神版新标点和合本) wording for direct Bible quotations and recognizable quoted "
                "biblical phrases; do not independently paraphrase Scripture quotations. Never "
                "leave ordinary English words inside Chinese Bible quotes. Avoid adding "
                "commentary. If the HTML contains resource metadata, translate visible author "
                "bios and UI labels too: render `Topics` as `主题` and `Date` as `日期`, and "
                "translate topic names when a natural Chinese theological equivalent is clear. "
                "Preserve author names, image URLs, links, classes, ids, and dates. Do not "
                "include reasoning, analysis, <thought> tags, markdown "
                "fences, or notes. "
                "When calling `lookup_bible`, pass structured fields: start_book, "
                "start_chapter, start_verse, and optional end_chapter/end_verse/end_book; "
                "do not put the whole reference only in a free-form argument. "
                "Return only an HTML fragment."
            )
        if context:
            system_prompt += f"\n\n## Article Context\n{context}"
        if is_batch:
            system_prompt += batch_prompt_rules(self.config.batch_separator)

        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": text}]
        if self.config.merge_system_prompt:
            messages = [{"role": "user", "content": f"{system_prompt}\n\n{text}"}]

        kwargs: dict[str, object] = {
            "model": self.config.model,
            "messages": messages,
        }
        if self.config.temperature is not None:
            kwargs["temperature"] = self.config.temperature
        if self.config.reasoning_effort:
            kwargs["reasoning_effort"] = self.config.reasoning_effort
        if self.config.max_completion_tokens:
            kwargs["max_completion_tokens"] = self.config.max_completion_tokens
        if text_type == "html" and self.bible_index is not None:
            kwargs["tools"] = [BIBLE_LOOKUP_TOOL]
            kwargs["tool_choice"] = "required" if contains_bible_reference(text) else "auto"

        client = self.next_client()
        response = client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        if getattr(message, "tool_calls", None):
            messages.append(message.model_dump(exclude_none=True))
            for tool_call in message.tool_calls or []:
                messages.append(self._run_tool_call(tool_call))
            kwargs["messages"] = messages
            kwargs.pop("tool_choice", None)
            kwargs.pop("tools", None)
            response = client.chat.completions.create(**kwargs)
            message = response.choices[0].message
        content = message.content
        return clean_model_output(content or "")

    def _run_tool_call(self, tool_call: object) -> dict[str, object]:
        name = tool_call.function.name
        if name != "lookup_bible" or self.bible_index is None:
            result = {"error": f"unsupported tool: {name}"}
        else:
            try:
                arguments = json.loads(tool_call.function.arguments or "{}")
                result = self._lookup_bible(arguments)
            except (KeyError, TypeError, ValueError) as exc:
                result = {"error": str(exc)}
        return {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": json.dumps(result, ensure_ascii=False),
        }

    def _lookup_bible(self, arguments: dict[str, object]) -> dict[str, object]:
        if not self.bible_index:
            return {"error": "Bible index is not loaded"}
        if reference := str(arguments.get("reference") or ""):
            logger.info("Looking up Bible reference %s", reference)
            verses = self.bible_index.lookup(reference)
            return format_bible_tool_result(reference, verses)

        reference_text = str(arguments.get("reference_text") or "")
        if (
            reference_text
            and arguments.get("end_book") is None
            and arguments.get("end_chapter") is None
            and arguments.get("end_verse") is None
            and "-" in reference_text.translate(str.maketrans({"\u2013": "-", "\u2014": "-"}))
        ):
            logger.info("Looking up Bible reference %s", reference_text)
            verses = self.bible_index.lookup(reference_text)
            if verses:
                return format_bible_tool_result(reference_text, verses)

        start_book = str(arguments.get("start_book") or "")
        start_chapter = int(arguments["start_chapter"])
        start_verse = int(arguments["start_verse"])
        end_book_value = arguments.get("end_book")
        end_chapter_value = arguments.get("end_chapter")
        end_verse_value = arguments.get("end_verse")
        end_book = str(end_book_value) if end_book_value else None
        end_chapter = int(end_chapter_value) if end_chapter_value is not None else None
        end_verse = int(end_verse_value) if end_verse_value is not None else None
        bible_range = self.bible_index.normalize_range(
            start_book=start_book,
            start_chapter=start_chapter,
            start_verse=start_verse,
            end_book=end_book,
            end_chapter=end_chapter,
            end_verse=end_verse,
        )
        normalized_reference = bible_range.reference if bible_range else reference_text
        logger.info("Looking up Bible reference %s", normalized_reference)
        verses = self.bible_index.lookup_range(
            start_book=start_book,
            start_chapter=start_chapter,
            start_verse=start_verse,
            end_book=end_book,
            end_chapter=end_chapter,
            end_verse=end_verse,
        )
        return format_bible_tool_result(normalized_reference, verses)


def maybe_translate_articles(
    articles: list[Article],
    config: TranslationConfig,
    cache_dir: Path,
    bible_config: BibleConfig | None = None,
) -> None:
    if not config.enabled:
        for article in articles:
            article.translated_title = article.title
            article.translated_html = article.original_html
        return
    if not config.api_keys:
        raise ValueError("translation.enabled is true but api_key/api_keys is empty")

    translator = OpenAITranslator(
        config,
        TranslationCache(cache_dir),
        load_bible_index(bible_config),
    )
    for index, article in enumerate(articles):
        try:
            translator.translate_article(article)
        except TranslationBudgetExceeded:
            if config.budget_exceeded == "keep_original":
                logger.warning(
                    "Translation budget reached; leaving remaining articles untranslated"
                )
                for remaining in articles[index:]:
                    remaining.translated_title = remaining.title
                    remaining.translated_html = remaining.original_html
                break
            raise


def chunk_blocks(blocks: list[PortableBlock], max_chars: int) -> list[list[PortableBlock]]:
    chunks: list[list[PortableBlock]] = []
    current: list[PortableBlock] = []
    current_size = 0
    for block in blocks:
        size = len(json.dumps(block, ensure_ascii=False))
        if current and current_size + size > max_chars:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(block)
        current_size += size
    if current:
        chunks.append(current)
    return chunks


def build_bilingual_html(translated_html: str, original_html: str) -> str:
    return f'{translated_html}\n<div class="original">\n{strip_images(original_html)}\n</div>'


def clean_model_output(text: str) -> str:
    cleaned = THOUGHT_RE.sub("", text).strip()
    fenced = FENCED_HTML_RE.match(cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()
    return cleaned


def sanitize_translated_html(text: str) -> str:
    return strip_unsafe_embedded_html(text).strip()


def load_bible_index(config: BibleConfig | None) -> BibleIndex | None:
    if not config or not config.enabled or not config.usfx_zip_path.exists():
        return None
    return BibleIndex.from_usfx_zip(config.usfx_zip_path)


def strip_images(html: str) -> str:
    protected_sections: list[str] = []

    def protect(match: re.Match[str]) -> str:
        protected_sections.append(match.group(0))
        return f"@@MR_RESOURCE_META_{len(protected_sections) - 1}@@"

    html = re.sub(
        r"<section\b[^>]*class=\"[^\"]*\bresource-meta\b[^\"]*\"[^>]*>.*?</section>",
        protect,
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    without_figures = re.sub(
        r"<figure\b[^>]*>.*?</figure>", "", html, flags=re.IGNORECASE | re.DOTALL
    )
    without_images = re.sub(r"<img\b[^>]*>", "", without_figures, flags=re.IGNORECASE)
    for index, section in enumerate(protected_sections):
        without_images = without_images.replace(f"@@MR_RESOURCE_META_{index}@@", section)
    return without_images.strip()


def hash_article_content(blocks: Iterable[PortableBlock]) -> str:
    return hashlib.sha256(json.dumps(list(blocks), sort_keys=True).encode("utf-8")).hexdigest()


def translation_cache_key(
    *,
    namespace: str,
    model: str,
    target: str,
    text: str,
    context: str,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "namespace": namespace,
                "prompt_version": TRANSLATION_PROMPT_VERSION,
                "model": model,
                "target": target,
                "text": text,
                "context": context,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def article_context(article: Article) -> str:
    authors = ", ".join(author.title for author in article.authors if author.title) or "Unknown"
    author_bios = "\n".join(
        f"{author.title}: {author_bio_text(author.bio)}" for author in article.authors if author.bio
    )
    topics = ", ".join(topic.title for topic in article.topics) or "None"
    excerpt = text_from_blocks(article.excerpt)
    return (
        f"Title: {article.title}\n"
        f"Type: {article.resource_type.name}\n"
        f"Authors: {authors}\n"
        f"Author bios: {author_bios or 'None'}\n"
        f"Topics: {topics}\n"
        f"Published: {article.publish_date.date().isoformat()}\n"
        f"Excerpt: {excerpt[:1200] or 'No excerpt available'}"
    )


def author_bio_text(bio: list[PortableBlock] | str) -> str:
    return text_from_blocks(bio) if isinstance(bio, list) else bio


def build_batches(
    missing: list[tuple[int, str, str]],
    *,
    max_items: int,
    max_chars: int,
) -> list[list[tuple[int, str, str]]]:
    batches: list[list[tuple[int, str, str]]] = []
    current: list[tuple[int, str, str]] = []
    current_chars = 0
    for item in missing:
        text_len = len(item[1])
        if current and (len(current) >= max_items or current_chars + text_len > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += text_len
    if current:
        batches.append(current)
    return batches


def batch_join(texts: list[str], separator: str) -> str:
    return f"\n\n{separator}\n\n".join(texts)


def batch_split(text: str, separator: str) -> list[str]:
    lines = text.strip().splitlines()
    results: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.strip() == separator:
            results.append("\n".join(current).strip())
            current = []
        else:
            current.append(line)
    results.append("\n".join(current).strip())
    return results


def batch_prompt_rules(separator: str) -> str:
    return (
        "\n\n## Batch Translation Rules\n"
        f"The input may contain a standalone separator line containing only `{separator}`. "
        f"If it does, output exactly one standalone `{separator}` line between translated "
        "segments, preserving the same segment count. Treat the separator as a delimiter only "
        "when it appears alone on a line; never translate or duplicate it inside normal prose."
    )


def contains_bible_reference(text: str) -> bool:
    return bool(BIBLE_REFERENCE_RE.search(text))


def format_bible_tool_result(reference: str, verses: object) -> dict[str, object]:
    verse_list = list(verses)
    return {
        "reference": reference,
        "verses": [
            {
                "book": verse.book,
                "chapter": verse.chapter,
                "verse": verse.verse,
                "reference": verse.reference,
                "text": verse.text,
            }
            for verse in verse_list
        ],
        "text": "\n".join(f"{verse.verse} {verse.text}" for verse in verse_list),
    }


def should_retry(exc: Exception) -> bool:
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in {408, 409, 429} or exc.status_code >= 500
    return False


def retry_delay_seconds(exc: Exception, base: float, attempt: int) -> float:
    if isinstance(exc, APIStatusError):
        retry_after = exc.response.headers.get("retry-after")
        if retry_after:
            try:
                return max(float(retry_after), 0)
            except ValueError:
                pass
    return backoff_seconds(base, attempt)


def backoff_seconds(base: float, attempt: int) -> float:
    return min(base * (2**attempt), 60)

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, ClassVar

from modernreformation_sync.models import Article, PortableBlock

SANITY_IMAGE_BASE_URL = "https://cdn.sanity.io/images/sxo7ym47/production"


@dataclass
class RenderState:
    id_prefix: str = ""
    footnotes: list[tuple[int, str]] = field(default_factory=list)
    footnote_keys: dict[str, int] = field(default_factory=dict)

    def add_footnote(self, key: str, html_text: str) -> int:
        if key in self.footnote_keys:
            return self.footnote_keys[key]
        number = len(self.footnotes) + 1
        self.footnote_keys[key] = number
        self.footnotes.append((number, html_text))
        return number


def text_from_blocks(blocks: Iterable[PortableBlock]) -> str:
    parts: list[str] = []
    for block in blocks:
        if block.get("_type") == "block":
            text = "".join(str(child.get("text", "")) for child in block.get("children", []))
            if text:
                parts.append(text)
        elif is_embedded_html_block(block):
            text = html_to_text(str(block.get("embed") or block.get("html") or ""))
            if text:
                parts.append(text)
    return "\n\n".join(parts)


def render_article_html(article: Article, *, bilingual: bool = True) -> str:
    title = article.translated_title if bilingual and article.translated_title else article.title
    body = (
        article.translated_html if bilingual and article.translated_html else article.original_html
    )
    original_title = (
        f'<p class="original-title">{escape(article.title)}</p>'
        if bilingual and article.translated_title and article.translated_title != article.title
        else ""
    )
    authors = ", ".join(author.title for author in article.authors if author.title)
    image = ""
    if article.image_url:
        image_alt = escape_attr(article.image_alt or article.title)
        image = (
            "<figure>"
            f'<img src="{escape_attr(article.image_url)}" alt="{image_alt}">'
            f"<figcaption>{render_blocks_inline(article.image_credit)}</figcaption>"
            "</figure>"
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    body {{ font-family: serif; line-height: 1.55; max-width: 42rem; margin: 0 auto;
      padding: 1.2rem; }}
    img {{ max-width: 100%; height: auto; }}
    blockquote {{ border-left: 0.2rem solid #999; margin-left: 0; padding-left: 1rem; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    td, th {{ border: 1px solid #ccc; padding: 0.35rem 0.5rem; vertical-align: top; }}
    .resource-meta {{ border-top: 1px solid #ddd; border-bottom: 1px solid #ddd;
      margin: 1.7rem 0 1.3rem; padding: 1rem 0; font-size: 0.94em; clear: both; }}
    .resource-author {{ display: table; width: 100%; margin-bottom: 0.85rem; }}
    .resource-author-portrait {{ display: table-cell; width: 4.6rem; vertical-align: top; }}
    .resource-author-portrait img {{ width: 3.8rem; height: 3.8rem; object-fit: cover;
      border-radius: 50%; }}
    .resource-author-copy {{ display: table-cell; vertical-align: top; }}
    .resource-author-name {{ font-weight: bold; margin: 0 0 0.25rem; }}
    .resource-author-bio {{ margin: 0; }}
    .resource-topics, .resource-date {{ margin: 0.45rem 0; }}
    .resource-label {{ color: #666; font-size: 0.76em; letter-spacing: 0.04em;
      text-transform: uppercase; margin-right: 0.4rem; }}
    .topic {{ white-space: nowrap; border-bottom: 1px solid #ddd; margin-right: 0.55rem; }}
    .footnotes {{ font-size: 0.9em; }}
    .footnotes li p {{ display: inline; }}
    .footnote-backref {{ white-space: nowrap; }}
    .metadata, .original-title {{ color: #555; }}
    .original {{ color: #555; font-size: 0.92em; }}
    .original::before {{ content: "Original"; display: block; font-size: 0.72em;
      text-transform: uppercase; letter-spacing: 0.04em; color: #777; }}
    hr {{ border: 0; border-top: 1px solid #ccc; margin: 1.5rem 0; }}
  </style>
</head>
<body>
  <article>
    <header>
      <p class="metadata">{escape(article.resource_type.name)}</p>
      <h1>{escape(title)}</h1>
      {original_title}
      <p class="metadata">{escape(authors)} · {article.publish_date.date().isoformat()}</p>
      {image}
    </header>
    {body}
    <hr>
    <p><a href="{escape_attr(article.url)}">Original article</a></p>
  </article>
</body>
</html>
"""


def render_portable_text(blocks: Iterable[PortableBlock], *, id_prefix: str = "") -> str:
    output: list[str] = []
    list_stack: list[str] = []
    state = RenderState(id_prefix=safe_id_prefix(id_prefix))

    def close_lists(to_level: int = 0) -> None:
        while len(list_stack) > to_level:
            output.append(f"</{list_stack.pop()}>")

    for block in blocks:
        block_type = str(block.get("_type", ""))
        if block_type == "block":
            list_item = block.get("listItem")
            level = int(block.get("level") or 0)
            if list_item:
                tag = "ol" if list_item == "number" else "ul"
                level = max(level, 1)
                while len(list_stack) < level:
                    list_stack.append(tag)
                    output.append(f"<{tag}>")
                while len(list_stack) > level:
                    output.append(f"</{list_stack.pop()}>")
                if list_stack and list_stack[-1] != tag:
                    output.append(f"</{list_stack.pop()}>")
                    list_stack.append(tag)
                    output.append(f"<{tag}>")
                output.append(f"<li>{render_spans(block, state)}</li>")
                continue

            close_lists()
            style = str(block.get("style") or "normal")
            text = render_spans(block, state)
            if not text:
                continue
            match style:
                case "h1" | "h2" | "h3" | "h4" | "h5" | "h6":
                    output.append(f"<{style}>{text}</{style}>")
                case "blockquote":
                    output.append(f"<blockquote><p>{text}</p></blockquote>")
                case "article_intro":
                    output.append(f'<p class="intro">{text}</p>')
                case _:
                    output.append(f"<p>{text}</p>")
        elif block_type == "horizontal_rule":
            close_lists()
            output.append("<hr>")
        elif block_type == "pull_quote":
            close_lists()
            text_blocks = block.get("text") if isinstance(block.get("text"), list) else []
            quote_html = render_portable_text(text_blocks)
            if quote_html:
                output.append(f'<blockquote class="pull-quote">{quote_html}</blockquote>')
        elif block_type in {"block_image", "image"}:
            close_lists()
            image_url = image_url_from_block(block)
            alt = str(block.get("alt") or block.get("image_alt") or block.get("label") or "")
            if image_url:
                caption = str(block.get("label") or "")
                figcaption = f"<figcaption>{escape(caption)}</figcaption>" if caption else ""
                output.append(
                    f'<figure><img src="{escape_attr(image_url)}" '
                    f'alt="{escape_attr(alt)}">{figcaption}</figure>'
                )
        elif is_embedded_html_block(block):
            close_lists()
            embedded_html = render_embedded_html(block)
            if embedded_html:
                output.append(embedded_html)

    close_lists()
    if state.footnotes:
        output.append(render_footnotes(state.footnotes, state.id_prefix))
    return "\n".join(output)


def render_resource_metadata(article: Article) -> str:
    parts: list[str] = []
    author_html = render_author_metadata(article)
    if author_html:
        parts.append(author_html)
    if article.topics:
        topics = ", ".join(
            f'<span class="topic">{escape(topic.title)}</span>' for topic in article.topics
        )
        parts.append(
            f'<p class="resource-topics"><span class="resource-label">Topics</span> {topics}</p>'
        )
    parts.append(
        '<p class="resource-date">'
        '<span class="resource-label">Date</span> '
        f"{escape(format_display_date(article))}</p>"
    )
    return '<section class="resource-meta">\n' + "\n".join(parts) + "\n</section>"


def render_author_metadata(article: Article) -> str:
    author_sections = []
    for author in article.authors:
        image = ""
        if author.image_url:
            image = (
                '<div class="resource-author-portrait">'
                f'<img src="{escape_attr(author.image_url)}" '
                f'alt="{escape_attr(author.image_alt or author.title)}">'
                "</div>"
            )
        bio = render_blocks_inline(author.bio) if author.bio else ""
        author_sections.append(
            '<div class="resource-author">'
            f"{image}"
            '<div class="resource-author-copy">'
            f'<p class="resource-author-name">{escape(author.title)}</p>'
            f'<p class="resource-author-bio">{bio}</p>'
            "</div>"
            "</div>"
        )
    return "\n".join(author_sections)


def render_spans(block: PortableBlock, state: RenderState | None = None) -> str:
    mark_defs = {str(item.get("_key")): item for item in block.get("markDefs", [])}
    spans = []
    for child in block.get("children", []):
        text = escape(str(child.get("text", "")))
        for mark in child.get("marks", []):
            text = apply_mark(text, str(mark), mark_defs, state)
        spans.append(text)
    return "".join(spans)


def apply_mark(
    text: str,
    mark: str,
    mark_defs: dict[str, dict[str, Any]],
    state: RenderState | None = None,
) -> str:
    if mark == "strong":
        return f"<strong>{text}</strong>"
    if mark == "em":
        return f"<em>{text}</em>"
    if mark in {"code", "underline", "strike-through"}:
        tag = {"code": "code", "underline": "u", "strike-through": "s"}[mark]
        return f"<{tag}>{text}</{tag}>"
    mark_def = mark_defs.get(mark)
    if mark_def and mark_def.get("_type") == "link":
        href = str(mark_def.get("href") or "")
        return f'<a href="{escape_attr(href)}">{text}</a>'
    if mark_def and mark_def.get("_type") == "footnote" and state is not None:
        note_html = render_footnote_note(mark_def.get("note"))
        number = state.add_footnote(mark, note_html)
        footnote_id = f"{state.id_prefix}fn-{number}"
        ref_id = f"{state.id_prefix}fnref-{number}"
        return (
            f'{text}<sup id="{ref_id}">'
            f'<a href="#{footnote_id}" class="footnote-ref">{number}</a></sup>'
        )
    return text


def render_blocks_inline(blocks: Iterable[PortableBlock] | str | None) -> str:
    if not blocks:
        return ""
    if isinstance(blocks, str):
        return escape(blocks)
    return " ".join(render_spans(block) for block in blocks if block.get("_type") == "block")


def render_footnote_note(note: object) -> str:
    if isinstance(note, list):
        return render_portable_text(note)
    if isinstance(note, str):
        return f"<p>{escape(note)}</p>"
    return ""


def render_footnotes(footnotes: list[tuple[int, str]], id_prefix: str = "") -> str:
    items = []
    for number, note_html in footnotes:
        footnote_id = f"{id_prefix}fn-{number}"
        ref_id = f"{id_prefix}fnref-{number}"
        backref = f' <a href="#{ref_id}" class="footnote-backref">&larr;</a>'
        items.append(f'<li id="{footnote_id}">{append_footnote_backref(note_html, backref)}</li>')
    return '<section class="footnotes">\n<hr>\n<ol>\n' + "\n".join(items) + "\n</ol>\n</section>"


def append_footnote_backref(note_html: str, backref: str) -> str:
    stripped = note_html.strip()
    if stripped.startswith("<p>") and stripped.endswith("</p>") and stripped.count("<p>") == 1:
        return f"{stripped[3:-4]}{backref}"
    if stripped.endswith("</p>"):
        return f"{stripped[:-4]}{backref}</p>"
    return f"{stripped}{backref}"


def format_display_date(article: Article) -> str:
    date = article.publish_date
    return f"{date.strftime('%A, %B')} {date.day}, {date.year}"


def safe_id_prefix(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip())
    return f"{cleaned}-" if cleaned else ""


def image_url_from_block(block: PortableBlock) -> str:
    direct_url = str(block.get("image_url") or block.get("url") or "")
    if direct_url:
        return direct_url
    image_file = block.get("image_file")
    if not isinstance(image_file, dict):
        return ""
    asset = image_file.get("asset")
    if not isinstance(asset, dict):
        return ""
    ref = str(asset.get("_ref") or "")
    return sanity_image_url_from_ref(ref)


def sanity_image_url_from_ref(ref: str) -> str:
    match = re.match(r"^image-([a-f0-9]+)-(\d+x\d+)-([a-z0-9]+)$", ref)
    if not match:
        return ""
    image_id, dimensions, extension = match.groups()
    return f"{SANITY_IMAGE_BASE_URL}/{image_id}-{dimensions}.{extension}"


def is_embedded_html_block(block: PortableBlock) -> bool:
    return str(block.get("_type") or "").lower().replace("-", "_") == "embedded_html"


def render_embedded_html(block: PortableBlock) -> str:
    if bool(block.get("isScript")):
        return ""
    raw_html = str(block.get("embed") or block.get("html") or "")
    cleaned = strip_unsafe_embedded_html(raw_html).strip()
    return f'<div class="embedded-html">{cleaned}</div>' if cleaned else ""


def strip_unsafe_embedded_html(value: str) -> str:
    parser = UnsafeHtmlStripper()
    parser.feed(value)
    parser.close()
    return parser.output


def html_to_text(value: str) -> str:
    parser = HtmlTextExtractor()
    parser.feed(value)
    parser.close()
    return " ".join(parser.parts).strip()


class UnsafeHtmlStripper(HTMLParser):
    blocked_tags: ClassVar[set[str]] = {"script", "style", "iframe", "object", "embed"}
    safe_url_attrs: ClassVar[set[str]] = {"href", "src"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.output = ""
        self.blocked_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self.blocked_tags:
            self.blocked_depth += 1
            return
        if self.blocked_depth:
            return
        self.output += self.get_starttag_text_with_safe_attrs(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.blocked_tags:
            self.blocked_depth = max(self.blocked_depth - 1, 0)
            return
        if not self.blocked_depth:
            self.output += f"</{tag}>"

    def handle_data(self, data: str) -> None:
        if not self.blocked_depth:
            self.output += escape(data)

    def handle_entityref(self, name: str) -> None:
        if not self.blocked_depth:
            self.output += f"&{name};"

    def handle_charref(self, name: str) -> None:
        if not self.blocked_depth:
            self.output += f"&#{name};"

    def get_starttag_text_with_safe_attrs(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> str:
        rendered_attrs = []
        for name, value in attrs:
            attr_name = name.lower()
            if attr_name.startswith("on"):
                continue
            if value is None:
                rendered_attrs.append(attr_name)
                continue
            if attr_name in self.safe_url_attrs and value.strip().lower().startswith(
                ("javascript:", "data:")
            ):
                continue
            rendered_attrs.append(f'{attr_name}="{escape_attr(value)}"')
        suffix = f" {' '.join(rendered_attrs)}" if rendered_attrs else ""
        return f"<{tag}{suffix}>"


class HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


def escape(value: str) -> str:
    return html.escape(value, quote=False)


def escape_attr(value: str) -> str:
    return html.escape(value, quote=True)

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

SKIP_TEXT_TAGS = {"f", "x"}
REFERENCE_RE = re.compile(r"^(.+?)\s*(\d+)\s*:\s*(\d+)(?:\s*-\s*(?:(\d+)\s*:\s*)?(\d+))?$")

BOOK_DATA: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("GEN", "Genesis", ("Gen", "Ge", "Gn", "创", "创世记")),
    ("EXO", "Exodus", ("Exod", "Exo", "Ex", "出", "出埃及记")),
    ("LEV", "Leviticus", ("Lev", "Le", "Lv", "利", "利未记")),
    ("NUM", "Numbers", ("Num", "Nu", "Nm", "民", "民数记")),
    ("DEU", "Deuteronomy", ("Deut", "Deu", "Dt", "申", "申命记")),
    ("JOS", "Joshua", ("Josh", "Jos", "Jsh", "书", "约书亚记")),
    ("JDG", "Judges", ("Judg", "Jdg", "Jg", "士", "士师记")),
    ("RUT", "Ruth", ("Rth", "Ru", "得", "路得记")),
    ("1SA", "1 Samuel", ("1 Sam", "1Sam", "1 Sa", "First Samuel", "撒上", "撒母耳记上")),
    ("2SA", "2 Samuel", ("2 Sam", "2Sam", "2 Sa", "Second Samuel", "撒下", "撒母耳记下")),
    ("1KI", "1 Kings", ("1 Kgs", "1Kgs", "1 Ki", "First Kings", "王上", "列王纪上")),
    ("2KI", "2 Kings", ("2 Kgs", "2Kgs", "2 Ki", "Second Kings", "王下", "列王纪下")),
    (
        "1CH",
        "1 Chronicles",
        ("1 Chr", "1Chr", "1 Ch", "First Chronicles", "代上", "历代志上"),
    ),
    (
        "2CH",
        "2 Chronicles",
        ("2 Chr", "2Chr", "2 Ch", "Second Chronicles", "代下", "历代志下"),
    ),
    ("EZR", "Ezra", ("Ezr", "拉", "以斯拉记")),
    ("NEH", "Nehemiah", ("Neh", "尼", "尼希米记")),
    ("EST", "Esther", ("Esth", "Est", "斯", "以斯帖记")),
    ("JOB", "Job", ("伯", "约伯记")),
    ("PSA", "Psalms", ("Psalm", "Ps", "Psa", "Pss", "诗", "诗篇")),
    ("PRO", "Proverbs", ("Prov", "Pro", "Pr", "箴", "箴言")),
    ("ECC", "Ecclesiastes", ("Eccl", "Ecc", "Qoh", "传", "传道书")),
    ("SNG", "Song of Songs", ("Song", "Song of Solomon", "Sos", "Sng", "歌", "雅歌")),
    ("ISA", "Isaiah", ("Isa", "赛", "以赛亚书")),
    ("JER", "Jeremiah", ("Jer", "耶", "耶利米书")),
    ("LAM", "Lamentations", ("Lam", "哀", "耶利米哀歌")),
    ("EZK", "Ezekiel", ("Ezek", "Eze", "结", "以西结书")),
    ("DAN", "Daniel", ("Dan", "但", "但以理书")),
    ("HOS", "Hosea", ("Hos", "何", "何西阿书")),
    ("JOL", "Joel", ("Joe", "珥", "约珥书")),
    ("AMO", "Amos", ("Amo", "摩", "阿摩司书")),
    ("OBA", "Obadiah", ("Obad", "Oba", "俄", "俄巴底亚书")),
    ("JON", "Jonah", ("Jon", "拿", "约拿书")),
    ("MIC", "Micah", ("Mic", "弥", "弥迦书")),
    ("NAM", "Nahum", ("Nah", "鸿", "那鸿书")),
    ("HAB", "Habakkuk", ("Hab", "哈", "哈巴谷书")),
    ("ZEP", "Zephaniah", ("Zeph", "Zep", "番", "西番雅书")),
    ("HAG", "Haggai", ("Hag", "该", "哈该书")),
    ("ZEC", "Zechariah", ("Zech", "Zec", "亚", "撒迦利亚书")),
    ("MAL", "Malachi", ("Mal", "玛", "玛拉基书")),
    ("MAT", "Matthew", ("Matt", "Mat", "Mt", "太", "马太福音")),
    ("MRK", "Mark", ("Mrk", "Mk", "可", "马可福音")),
    ("LUK", "Luke", ("Luk", "Lk", "路", "路加福音")),
    ("JHN", "John", ("Jn", "Jhn", "约", "约翰福音")),
    ("ACT", "Acts", ("Act", "使徒行传", "徒")),
    ("ROM", "Romans", ("Rom", "Ro", "罗", "罗马书")),
    ("1CO", "1 Corinthians", ("1 Cor", "1Cor", "First Corinthians", "林前", "哥林多前书")),
    ("2CO", "2 Corinthians", ("2 Cor", "2Cor", "Second Corinthians", "林后", "哥林多后书")),
    ("GAL", "Galatians", ("Gal", "加", "加拉太书")),
    ("EPH", "Ephesians", ("Eph", "弗", "以弗所书")),
    ("PHP", "Philippians", ("Phil", "Php", "腓", "腓立比书")),
    ("COL", "Colossians", ("Col", "西", "歌罗西书")),
    (
        "1TH",
        "1 Thessalonians",
        ("1 Thess", "1Thess", "1 Th", "First Thessalonians", "帖前", "帖撒罗尼迦前书"),
    ),
    (
        "2TH",
        "2 Thessalonians",
        ("2 Thess", "2Thess", "2 Th", "Second Thessalonians", "帖后", "帖撒罗尼迦后书"),
    ),
    ("1TI", "1 Timothy", ("1 Tim", "1Tim", "First Timothy", "提前", "提摩太前书")),
    ("2TI", "2 Timothy", ("2 Tim", "2Tim", "Second Timothy", "提后", "提摩太后书")),
    ("TIT", "Titus", ("Tit", "多", "提多书")),
    ("PHM", "Philemon", ("Phlm", "Phm", "门", "腓利门书")),
    ("HEB", "Hebrews", ("Heb", "来", "希伯来书")),
    ("JAS", "James", ("Jas", "Jm", "雅", "雅各书")),
    ("1PE", "1 Peter", ("1 Pet", "1Pet", "First Peter", "彼前", "彼得前书")),
    ("2PE", "2 Peter", ("2 Pet", "2Pet", "Second Peter", "彼后", "彼得后书")),
    ("1JN", "1 John", ("1 Jn", "1Jn", "First John", "约一", "约翰一书")),
    ("2JN", "2 John", ("2 Jn", "2Jn", "Second John", "约二", "约翰二书")),
    ("3JN", "3 John", ("3 Jn", "3Jn", "Third John", "约三", "约翰三书")),
    ("JUD", "Jude", ("Jud", "犹", "犹大书")),
    ("REV", "Revelation", ("Rev", "Re", "启", "启示录")),
)


def normalize_book_key(book: str) -> str:
    key = re.sub(r"\s+", " ", book.strip().lower())
    return key.replace(".", "")


def build_default_book_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for code, english_name, variants in BOOK_DATA:
        for alias in (code, english_name, *variants):
            aliases[normalize_book_key(alias)] = code
    return aliases


BOOK_ALIASES = build_default_book_aliases()
BOOK_ORDER = {code: index for index, (code, _name, _aliases) in enumerate(BOOK_DATA)}


@dataclass(frozen=True)
class BibleVerse:
    book: str
    chapter: int
    verse: int
    text: str

    @property
    def reference(self) -> str:
        return f"{self.book}.{self.chapter}.{self.verse}"


@dataclass(frozen=True)
class BibleEndpoint:
    book: str
    chapter: int
    verse: int


@dataclass(frozen=True)
class BibleReferenceRange:
    start: BibleEndpoint
    end: BibleEndpoint

    @property
    def reference(self) -> str:
        if self.start == self.end:
            return f"{self.start.book} {self.start.chapter}:{self.start.verse}"
        if self.start.book == self.end.book and self.start.chapter == self.end.chapter:
            return f"{self.start.book} {self.start.chapter}:{self.start.verse}-{self.end.verse}"
        return (
            f"{self.start.book} {self.start.chapter}:{self.start.verse}-"
            f"{self.end.book} {self.end.chapter}:{self.end.verse}"
        )


class BibleIndex:
    def __init__(
        self,
        verses: dict[tuple[str, int, int], str],
        *,
        book_aliases: dict[str, str] | None = None,
        book_order: dict[str, int] | None = None,
    ) -> None:
        self.verses = verses
        self.book_aliases = {**BOOK_ALIASES, **(book_aliases or {})}
        self.book_order = {**BOOK_ORDER, **(book_order or {})}

    @classmethod
    def from_usfx_zip(cls, path: Path) -> BibleIndex:
        with zipfile.ZipFile(path) as archive:
            usfx_name = next(name for name in archive.namelist() if name.endswith("_usfx.xml"))
            aliases = load_book_name_aliases(archive)
            with archive.open(usfx_name) as source:
                return cls.from_usfx_bytes(source.read(), book_aliases=aliases)

    @classmethod
    def from_usfx_bytes(
        cls,
        payload: bytes,
        *,
        book_aliases: dict[str, str] | None = None,
    ) -> BibleIndex:
        root = ElementTree.fromstring(payload)
        verses: dict[tuple[str, int, int], str] = {}
        book_order: dict[str, int] = {}
        book_index = 0
        for book in root.findall("book"):
            book_id = book.attrib.get("id", "")
            if book_id:
                book_order[book_id] = book_index
                book_index += 1
            chapter = 0
            current_key: tuple[str, int, int] | None = None
            current_parts: list[str] = []
            for elem in book.iter():
                if elem.tag == "c":
                    chapter = int(elem.attrib["id"])
                elif elem.tag == "v":
                    if current_key:
                        verses[current_key] = normalize_verse_text("".join(current_parts))
                    verse_id = parse_verse_id(elem.attrib["id"])
                    current_key = (book_id, chapter, verse_id) if verse_id is not None else None
                    current_parts = [elem.tail or ""]
                elif current_key and elem.tag != "ve":
                    if elem.text and elem.tag not in SKIP_TEXT_TAGS:
                        current_parts.append(elem.text)
                    if elem.tail:
                        current_parts.append(elem.tail)
            if current_key:
                verses[current_key] = normalize_verse_text("".join(current_parts))
        return cls(verses, book_aliases=book_aliases, book_order=book_order)

    def lookup(self, reference: str) -> list[BibleVerse]:
        parsed = parse_reference(reference, aliases=self.book_aliases)
        if not parsed:
            return []
        start_book, start_chapter, start_verse, end_book, end_chapter, end_verse = parsed
        return self.lookup_range(
            start_book=start_book,
            start_chapter=start_chapter,
            start_verse=start_verse,
            end_book=end_book,
            end_chapter=end_chapter,
            end_verse=end_verse,
        )

    def lookup_range(
        self,
        *,
        start_book: str,
        start_chapter: int,
        start_verse: int,
        end_book: str | None = None,
        end_chapter: int | None = None,
        end_verse: int | None = None,
    ) -> list[BibleVerse]:
        bible_range = self.normalize_range(
            start_book=start_book,
            start_chapter=start_chapter,
            start_verse=start_verse,
            end_book=end_book,
            end_chapter=end_chapter,
            end_verse=end_verse,
        )
        if bible_range is None:
            return []
        start_key = endpoint_sort_key(bible_range.start, self.book_order)
        end_key = endpoint_sort_key(bible_range.end, self.book_order)
        if start_key > end_key:
            return []
        return [
            BibleVerse(book=book, chapter=chapter, verse=verse, text=text)
            for (book, chapter, verse), text in sorted(
                self.verses.items(),
                key=lambda item: (
                    self.book_order.get(item[0][0], 999),
                    item[0][1],
                    item[0][2],
                ),
            )
            if start_key <= (self.book_order.get(book, 999), chapter, verse) <= end_key
        ]

    def lookup_text(self, reference: str) -> str:
        verses = self.lookup(reference)
        return "\n".join(f"{verse.verse} {verse.text}" for verse in verses)

    def normalize_range(
        self,
        *,
        start_book: str,
        start_chapter: int,
        start_verse: int,
        end_book: str | None = None,
        end_chapter: int | None = None,
        end_verse: int | None = None,
    ) -> BibleReferenceRange | None:
        normalized_start_book = normalize_book(start_book, aliases=self.book_aliases)
        if not normalized_start_book:
            return None
        normalized_end_book = (
            normalize_book(end_book, aliases=self.book_aliases)
            if end_book
            else normalized_start_book
        )
        if not normalized_end_book:
            return None
        normalized_end_chapter = end_chapter if end_chapter is not None else start_chapter
        normalized_end_verse = end_verse if end_verse is not None else start_verse
        return BibleReferenceRange(
            start=BibleEndpoint(normalized_start_book, start_chapter, start_verse),
            end=BibleEndpoint(
                normalized_end_book,
                normalized_end_chapter,
                normalized_end_verse,
            ),
        )


def parse_reference(
    reference: str,
    *,
    aliases: dict[str, str] | None = None,
) -> tuple[str, int, int, str, int, int] | None:
    cleaned = reference.strip()
    for source, target in {":": ":", "\uff1a": ":", "\u2013": "-", "\u2014": "-"}.items():
        cleaned = cleaned.replace(source, target)
    cleaned = cleaned.replace(".", " ")
    match = REFERENCE_RE.match(cleaned)
    if not match:
        return None
    raw_book, raw_chapter, raw_start, raw_end_chapter, raw_end = match.groups()
    book = normalize_book(raw_book, aliases=aliases)
    if not book:
        return None
    start = int(raw_start)
    chapter = int(raw_chapter)
    return book, chapter, start, book, int(raw_end_chapter or chapter), int(raw_end or start)


def normalize_book(book: str | None, *, aliases: dict[str, str] | None = None) -> str | None:
    if not book:
        return None
    return (aliases or BOOK_ALIASES).get(normalize_book_key(book))


def parse_verse_id(value: str) -> int | None:
    match = re.match(r"^\d+", value)
    return int(match.group(0)) if match else None


def normalize_verse_text(text: str) -> str:
    return re.sub(r"\s+", "", text).strip()


def load_book_name_aliases(archive: zipfile.ZipFile) -> dict[str, str]:
    try:
        payload = archive.read("BookNames.xml")
    except KeyError:
        return {}
    root = ElementTree.fromstring(payload)
    aliases: dict[str, str] = {}
    for book in root.findall("book"):
        code = book.attrib.get("code", "")
        if not code:
            continue
        for field in ("abbr", "short", "long", "alt"):
            for alias in book.attrib.get(field, "").split(","):
                alias = alias.strip()
                if alias:
                    aliases[normalize_book_key(alias)] = code
    return aliases


def endpoint_sort_key(
    endpoint: BibleEndpoint,
    book_order: dict[str, int],
) -> tuple[int, int, int]:
    return book_order.get(endpoint.book, 999), endpoint.chapter, endpoint.verse


def bible_reference_pattern() -> re.Pattern[str]:
    alias_patterns = sorted(
        {alias_to_regex(alias) for alias in BOOK_ALIASES if alias},
        key=len,
        reverse=True,
    )
    return re.compile(
        rf"(?<![\w])(?:{'|'.join(alias_patterns)})\.?\s*\d+\s*[:\uff1a]\s*\d+",
        flags=re.IGNORECASE,
    )


def alias_to_regex(alias: str) -> str:
    return re.escape(alias).replace(r"\ ", r"\s*")

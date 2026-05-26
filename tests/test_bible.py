from modernreformation_sync.bible import BibleIndex, parse_reference


def test_parse_reference_supports_english_and_chinese_books() -> None:
    assert parse_reference("Deut. 30:1-2") == ("DEU", 30, 1, "DEU", 30, 2)
    assert parse_reference("申 30:6") == ("DEU", 30, 6, "DEU", 30, 6)
    assert parse_reference("1 Cor. 7:14") == ("1CO", 7, 14, "1CO", 7, 14)
    assert parse_reference("John 6:45") == ("JHN", 6, 45, "JHN", 6, 45)
    assert parse_reference("约翰福音 6\uff1a45") == ("JHN", 6, 45, "JHN", 6, 45)
    assert parse_reference("Rom 8:1-2") == ("ROM", 8, 1, "ROM", 8, 2)


def test_bible_index_reads_usfx_verses() -> None:
    payload = """<?xml version="1.0" encoding="utf-8"?>
<usfx>
  <book id="DEU">
    <c id="30" />
    <p><v id="1" bcv="DEU.30.1" />等到这一切事临到你<ve />
    <v id="2" bcv="DEU.30.2" />你和你的儿女若尽心尽性归向耶和华<ve /></p>
    <p><v id="6" bcv="DEU.30.6" />耶和华你神必将你心里和你后裔心里的污秽除掉<ve /></p>
  </book>
</usfx>
""".encode()

    index = BibleIndex.from_usfx_bytes(payload)

    assert index.lookup_text("Deut. 30:1-2") == (
        "1 等到这一切事临到你\n2 你和你的儿女若尽心尽性归向耶和华"
    )
    assert index.lookup_text("申 30:6") == "6 耶和华你神必将你心里和你后裔心里的污秽除掉"


def test_bible_index_supports_structured_ranges() -> None:
    index = BibleIndex(
        {
            ("JHN", 6, 45): "在先知书上写着说:他们都要蒙神的教训。",
            ("JHN", 6, 46): "这不是说有人看见过父。",
        }
    )

    verses = index.lookup_range(
        start_book="John",
        start_chapter=6,
        start_verse=45,
        end_verse=46,
    )

    assert [verse.reference for verse in verses] == ["JHN.6.45", "JHN.6.46"]


def test_bible_index_omits_footnotes() -> None:
    payload = """<?xml version="1.0" encoding="utf-8"?>
<usfx>
  <book id="1CO">
    <c id="7" />
    <p><v id="14" bcv="1CO.7.14" />因为不信的妻子就因着丈夫
    <f caller="-">原文是弟兄</f>成了圣洁。<ve /></p>
  </book>
</usfx>
""".encode()

    index = BibleIndex.from_usfx_bytes(payload)

    assert index.lookup_text("1 Cor. 7:14") == "14 因为不信的妻子就因着丈夫成了圣洁。"

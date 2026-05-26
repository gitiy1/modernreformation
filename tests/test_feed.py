from modernreformation_sync.feed import escape_slug


def test_escape_slug_percent_encodes_path_suffix() -> None:
    assert escape_slug("essays/a title?x=1#frag") == "essays__a%20title%3Fx%3D1%23frag"

from pathlib import Path

PANEL = Path(__file__).parents[1] / "custom_components" / "vacuum_schedule" / "frontend" / "panel.js"


def test_compact_rule_follows_generic_icon_only_rule_and_overrides_all_dimensions():
    text = PANEL.read_text(encoding="utf-8")
    generic = "button.icon-only {"
    compact = "button.icon-only.compact-icon-action {"
    assert generic in text and compact in text
    assert text.index(compact) > text.index(generic)
    block = text[text.index(compact): text.index("}", text.index(compact)) + 1]
    for prop in (
        "width:var(--vs-compact-action-size)!important",
        "min-width:var(--vs-compact-action-size)!important",
        "max-width:var(--vs-compact-action-size)!important",
        "height:var(--vs-compact-action-size)!important",
        "min-height:var(--vs-compact-action-size)!important",
        "max-height:var(--vs-compact-action-size)!important",
        "inline-size:var(--vs-compact-action-size)!important",
        "block-size:var(--vs-compact-action-size)!important",
    ):
        assert prop in block


def test_compact_icon_is_strictly_centered_and_sized():
    text = PANEL.read_text(encoding="utf-8")
    selector = "button.icon-only.compact-icon-action>.button-icon {"
    assert selector in text
    block = text[text.index(selector): text.index("}", text.index(selector)) + 1]
    assert "left:50%!important" in block
    assert "top:50%!important" in block
    assert "transform:translate(-50%,-50%)!important" in block
    assert "width:var(--vs-compact-action-icon-size)!important" in block
    assert "height:var(--vs-compact-action-icon-size)!important" in block

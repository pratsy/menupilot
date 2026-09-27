from app.agent.orchestrator import (
    _drop_empty_restaurant_sections,
    _strip_empty_placeholder_rows,
    _strip_noncompliant_dish_rows,
)

TABLE = """### Cafeteria Afrika
**Address:** somewhere

| Dish | Price | Ingredients |
|---|---|---|
| Vegetable Pizza | 13.25 € | corn, olives, mushrooms |
| Goat Cheese Pizza | 14.90 € | goat cheese, caramelized onion (contains dairy) |
| Afrika Fries & Cheese | 7.50 € | fries, cheese (contains dairy) |

**Testimonials**: No testimonials found."""


def test_strips_rows_containing_excluded_ingredient():
    result = _strip_noncompliant_dish_rows(TABLE, {"cheese"})
    assert "Goat Cheese Pizza" not in result
    assert "Afrika Fries & Cheese" not in result
    assert "Vegetable Pizza" in result  # compliant row untouched


def test_leaves_table_structure_intact_when_nothing_excluded():
    result = _strip_noncompliant_dish_rows(TABLE, set())
    assert result == TABLE


def test_never_strips_header_or_separator_rows():
    result = _strip_noncompliant_dish_rows(TABLE, {"dish", "price", "ingredients"})
    assert "| Dish | Price | Ingredients |" in result
    assert "|---|---|---|" in result


def test_appends_a_note_when_rows_were_removed():
    result = _strip_noncompliant_dish_rows(TABLE, {"cheese"})
    assert "removed" in result.lower()


def test_does_not_append_a_note_when_nothing_removed():
    result = _strip_noncompliant_dish_rows(TABLE, {"truffle"})
    assert "removed" not in result.lower()


def test_real_world_regression_agent_caveated_instead_of_dropping():
    """The exact failure mode seen live: the model wrote a disqualified dish into the
    table with a self-authored '(not compliant)' caveat instead of omitting it. The
    post-filter must catch this regardless of the caveat wording."""
    markdown = (
        "### Cafeteria Afrika\n\n"
        "| Dish | Price | Ingredients |\n"
        "|---|---|---|\n"
        "| Kebab | 14.5 € | kebab meat, white sauce (contains dairy - not compliant) |\n"
        "| Entrecote with Potatoes | 20.8 € | entrecote, potatoes |\n"
    )
    result = _strip_noncompliant_dish_rows(markdown, {"dairy", "milk", "cheese", "butter", "cream", "yogurt", "ghee"})
    assert "Kebab" not in result
    assert "Entrecote with Potatoes" in result


def test_strips_fully_empty_placeholder_row():
    markdown = (
        "| Dish | Price | Ingredients |\n"
        "|---|---|---|\n"
        "| — | — | — |\n"
        "| Tomato Soup | 5.90 € | tomato, basil |\n"
    )
    result = _strip_empty_placeholder_rows(markdown)
    assert "| — | — | — |" not in result
    assert "Tomato Soup" in result


def test_strips_empty_row_regardless_of_dash_style():
    markdown = "| Dish | Price | Ingredients |\n|---|---|---|\n| - | – | |\n"
    result = _strip_empty_placeholder_rows(markdown)
    assert "| - | – | |" not in result


def test_drops_restaurant_section_with_only_placeholder_row():
    """Regression test for a real bug seen live: a restaurant with zero qualifying
    dishes still got a full header/address/table shown, just with a "— | — | —" row
    instead of being omitted entirely."""
    markdown = (
        "### Empty Place\n"
        "**Address:** 1 Nowhere St\n\n"
        "| Dish | Price | Ingredients |\n"
        "|---|---|---|\n"
        "| — | — | — |\n\n"
        "### Good Place\n"
        "**Address:** 2 Somewhere St\n\n"
        "| Dish | Price | Ingredients |\n"
        "|---|---|---|\n"
        "| Tomato Soup | 5.90 € | tomato, basil |\n"
    )
    result = _drop_empty_restaurant_sections(_strip_empty_placeholder_rows(markdown))
    assert "Empty Place" not in result
    assert "Good Place" in result
    assert "Tomato Soup" in result


def test_drops_restaurant_section_with_no_table_at_all():
    markdown = "### Empty Place\n**Address:** somewhere\n\nNothing found here.\n\n### Good Place\n**Address:** elsewhere\n\n| Dish | Price | Ingredients |\n|---|---|---|\n| Soup | 5 € | tomato |\n"
    # a section with no table isn't dropped by this function (it has nothing to judge
    # "qualifying" against) - that's the system prompt's job to not write it in the
    # first place; this backstop only catches the "table with nothing real in it" case
    result = _drop_empty_restaurant_sections(markdown)
    assert "Good Place" in result


def test_keeps_trailing_footnote_even_if_last_restaurant_is_dropped():
    """The footnote must survive even when it directly follows the restaurant section
    that gets dropped, since it's textually the last thing in the message."""
    markdown = (
        "### Good Place\n**Address:** somewhere\n\n"
        "| Dish | Price | Ingredients |\n|---|---|---|\n| Soup | 5 € | tomato |\n\n"
        "### Empty Place\n**Address:** elsewhere\n\n"
        "| Dish | Price | Ingredients |\n|---|---|---|\n| — | — | — |\n\n"
        "*✓ = ingredient list confirmed on the restaurant's own menu; everything else is inferred.*"
    )
    result = _drop_empty_restaurant_sections(_strip_empty_placeholder_rows(markdown))
    assert "Empty Place" not in result
    assert "✓ = ingredient list confirmed" in result

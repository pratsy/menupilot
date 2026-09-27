from app.agent.orchestrator import _strip_noncompliant_dish_rows

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

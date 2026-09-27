import pytest
from pydantic import ValidationError

from app.schemas import MenuExtractionResult, MenuItemLLM, TestimonialExtractionResult, TestimonialItem


def test_menu_item_llm_requires_translated_name():
    with pytest.raises(ValidationError):
        MenuItemLLM()  # translated_name is required, everything else has defaults


def test_menu_item_llm_defaults():
    item = MenuItemLLM(translated_name="Tomato Salad")
    assert item.ingredients == []
    assert item.ingredients_source == "menu_stated"
    assert item.price is None


def test_menu_extraction_result_parses_nested_items():
    data = {"items": [{"translated_name": "Hummus", "price": 8.5, "currency": "EUR"}]}
    result = MenuExtractionResult.model_validate(data)
    assert len(result.items) == 1
    assert result.items[0].translated_name == "Hummus"
    assert result.items[0].price == 8.5


def test_menu_extraction_result_defaults_to_empty_list():
    assert MenuExtractionResult().items == []


def test_testimonial_item_requires_text_fields():
    with pytest.raises(ValidationError):
        TestimonialItem()


def test_testimonial_extraction_result_parses():
    data = {"items": [{"original_text": "Muy bueno", "translated_text": "Very good", "sentiment": "positive"}]}
    result = TestimonialExtractionResult.model_validate(data)
    assert result.items[0].sentiment == "positive"

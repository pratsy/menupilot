from app.db import vectorstore


class _FakeCollection:
    def __init__(self):
        self.calls = []

    def upsert(self, ids, documents, metadatas):
        self.calls.append({"ids": ids, "documents": documents, "metadatas": metadatas})


def test_upsert_menu_items_makes_a_single_batched_call(monkeypatch):
    fake = _FakeCollection()
    monkeypatch.setattr(vectorstore, "_menu_items_collection", fake)

    items = [
        {"menu_item_id": 1, "restaurant_id": "r1", "text": "Tomato Salad", "price": 8.0,
         "currency": "EUR", "ingredients": "tomato, basil", "ingredients_source": "menu_stated"},
        {"menu_item_id": 2, "restaurant_id": "r1", "text": "Tofu Curry", "price": 12.0,
         "currency": "EUR", "ingredients": "tofu, curry", "ingredients_source": "menu_stated"},
        {"menu_item_id": 3, "restaurant_id": "r1", "text": "  ", "price": None,
         "currency": None, "ingredients": "", "ingredients_source": "llm_inferred"},  # blank text, dropped
    ]
    vectorstore.upsert_menu_items(items)

    assert len(fake.calls) == 1  # one embedding-API-backed call, not one per item
    call = fake.calls[0]
    assert call["ids"] == ["menu_item-1", "menu_item-2"]  # blank-text item excluded
    assert call["documents"] == ["Tomato Salad", "Tofu Curry"]


def test_upsert_menu_items_skips_call_entirely_when_all_blank(monkeypatch):
    fake = _FakeCollection()
    monkeypatch.setattr(vectorstore, "_menu_items_collection", fake)

    vectorstore.upsert_menu_items([
        {"menu_item_id": 1, "restaurant_id": "r1", "text": "", "price": None,
         "currency": None, "ingredients": "", "ingredients_source": "llm_inferred"},
    ])

    assert fake.calls == []


def test_upsert_reviews_makes_a_single_batched_call(monkeypatch):
    fake = _FakeCollection()
    monkeypatch.setattr(vectorstore, "_reviews_collection", fake)

    reviews = [
        {"review_id": 1, "restaurant_id": "r1", "text": "Great food", "rating": None, "sentiment": "positive"},
        {"review_id": 2, "restaurant_id": "r1", "text": "Slow service", "rating": None, "sentiment": "negative"},
    ]
    vectorstore.upsert_reviews(reviews)

    assert len(fake.calls) == 1
    assert fake.calls[0]["ids"] == ["review-1", "review-2"]

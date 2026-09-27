import json
from types import SimpleNamespace

from app.services import rerank


def _fake_openai_response(content: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


class _FakeClient:
    def __init__(self, response_content: str | None = None, raise_exc: Exception | None = None):
        self._response_content = response_content
        self._raise_exc = raise_exc
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        if self._raise_exc:
            raise self._raise_exc
        return _fake_openai_response(self._response_content)


def test_rerank_empty_candidates_returns_empty(monkeypatch):
    assert rerank.rerank("query", []) == []


def test_rerank_reorders_by_llm_score(monkeypatch):
    candidates = [
        {"text": "Spicy noodle soup with pork"},
        {"text": "Spicy vegetarian noodle soup"},
        {"text": "Plain rice"},
    ]
    # LLM says index 1 (the vegetarian one) is the best match for the query
    scores_json = json.dumps({"scores": [{"index": 0, "score": 2}, {"index": 1, "score": 9}, {"index": 2, "score": 0}]})
    monkeypatch.setattr(rerank, "_client", _FakeClient(response_content=scores_json))

    result = rerank.rerank("vegetarian spicy soup", candidates, text_key="text")

    assert result[0]["text"] == "Spicy vegetarian noodle soup"
    assert result[1]["text"] == "Spicy noodle soup with pork"


def test_rerank_respects_top_k(monkeypatch):
    candidates = [{"text": f"item {i}"} for i in range(5)]
    scores_json = json.dumps({"scores": [{"index": i, "score": i} for i in range(5)]})
    monkeypatch.setattr(rerank, "_client", _FakeClient(response_content=scores_json))

    result = rerank.rerank("query", candidates, top_k=2)

    assert len(result) == 2
    assert result[0]["text"] == "item 4"  # highest score first


def test_rerank_falls_back_to_original_order_on_failure(monkeypatch):
    candidates = [{"text": "a"}, {"text": "b"}]
    monkeypatch.setattr(rerank, "_client", _FakeClient(raise_exc=RuntimeError("API down")))

    result = rerank.rerank("query", candidates)

    assert result == candidates  # unchanged, not dropped


def test_rerank_falls_back_on_malformed_json(monkeypatch):
    candidates = [{"text": "a"}, {"text": "b"}]
    monkeypatch.setattr(rerank, "_client", _FakeClient(response_content="not json"))

    result = rerank.rerank("query", candidates)

    assert result == candidates

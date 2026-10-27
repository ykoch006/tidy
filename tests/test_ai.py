"""AI classifier tests with a fake Anthropic client — no network, no cost."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tidy.ai.anthropic_classifier import (
    MAX_AI_CONFIDENCE,
    AIUnavailable,
    AnthropicClassifier,
    describe_transmission,
)
from tidy.core.categories import CategoryTree
from tidy.core.rules import classify_by_rules
from tidy.models import Classification, FileItem, Source
from tidy.settings import AppSettings


class FakeMessages:
    def __init__(self, owner: FakeClient) -> None:
        self.owner = owner

    def create(self, **kwargs):
        self.owner.calls.append(kwargs)
        if self.owner.error is not None:
            raise self.owner.error
        entries = json.loads(kwargs["messages"][0]["content"])
        results = [self.owner.answer(e) for e in entries]
        text = json.dumps({"results": [r for r in results if r]})
        return SimpleNamespace(
            stop_reason=self.owner.stop_reason, content=[SimpleNamespace(type="text", text=text)]
        )


class FakeClient:
    def __init__(self, answer=None, error: Exception | None = None, stop_reason="end_turn"):
        self.calls: list[dict] = []
        self.error = error
        self.stop_reason = stop_reason
        self.answer = answer or (
            lambda e: {
                "id": e["id"],
                "category": "documents.finance",
                "confidence": 0.85,
                "reason": "looks like a bill",
            }
        )
        self.messages = FakeMessages(self)
        self.beta = SimpleNamespace(messages=FakeMessages(self))


def settings(**overrides) -> AppSettings:
    s = AppSettings(ai_enabled=True, ai_consent_filenames=True)
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def item(downloads: Path, name: str, content: bytes = b"x") -> FileItem:
    path = downloads / name
    path.write_bytes(content)
    return FileItem(path=path, root=downloads, size=len(content), mtime_ns=0)


def run(classifier, items):
    cls = [classify_by_rules(i) for i in items]
    report = classifier.refine(items, cls, threshold=0.55)
    return cls, report


def test_disabled_by_default_and_requires_consent(db) -> None:
    assert AppSettings().ai_enabled is False
    with pytest.raises(AIUnavailable):
        AnthropicClassifier(AppSettings(), CategoryTree(), db)
    with pytest.raises(AIUnavailable):
        AnthropicClassifier(AppSettings(ai_enabled=True), CategoryTree(), db)


def test_only_low_confidence_items_sent_without_paths(downloads: Path, db) -> None:
    fake = FakeClient()
    classifier = AnthropicClassifier(settings(), CategoryTree(), db, client=fake)
    items = [item(downloads, "mystery.qqq"), item(downloads, "song.mp3")]
    cls, report = run(classifier, items)
    assert report.sent == 1 and report.applied == 1
    payload = fake.calls[0]["messages"][0]["content"]
    assert "mystery.qqq" in payload
    assert "song.mp3" not in payload, "confident items are never sent"
    assert str(downloads) not in payload and "sandbox" not in payload, "no paths"
    assert cls[0].category == "documents.finance"
    assert cls[0].source is Source.AI
    assert cls[0].confidence <= MAX_AI_CONFIDENCE
    assert "Claude" in cls[0].explain()


def test_content_excerpt_sent_with_consent(downloads: Path) -> None:
    fake = FakeClient()
    c = AnthropicClassifier(settings(ai_consent_content=True), CategoryTree(), None, client=fake)
    it = item(downloads, "notes.qqq")
    entry = c.payload_for(0, it, Classification("other", 0.3, Source.FALLBACK, []))
    assert "text_excerpt" not in entry  # unknown type: nothing extracted
    txt = item(downloads, "notes.txt", b"hello lecture notes")
    entry = c.payload_for(1, txt, Classification("documents.general", 0.3, Source.RULE, []))
    assert entry["text_excerpt"] == "hello lecture notes"
    no = AnthropicClassifier(settings(), CategoryTree(), None, client=fake)
    assert "text_excerpt" not in no.payload_for(1, txt, Classification("x", 0.3, Source.RULE, []))


def test_results_are_cached(downloads: Path, db) -> None:
    fake = FakeClient()
    classifier = AnthropicClassifier(settings(), CategoryTree(), db, client=fake)
    run(classifier, [item(downloads, "mystery.qqq")])
    _, report = run(
        AnthropicClassifier(settings(), CategoryTree(), db, client=fake),
        [item(downloads, "mystery.qqq")],
    )
    assert len(fake.calls) == 1
    assert report.cached == 1 and report.applied == 1


def test_changing_instructions_invalidates_cache(downloads: Path, db) -> None:
    fake = FakeClient()
    run(
        AnthropicClassifier(settings(), CategoryTree(), db, client=fake),
        [item(downloads, "mystery.qqq")],
    )
    run(
        AnthropicClassifier(
            settings(ai_instructions="Bills go to Personal"), CategoryTree(), db, client=fake
        ),
        [item(downloads, "mystery.qqq")],
    )
    assert len(fake.calls) == 2
    assert "Bills go to Personal" in fake.calls[1]["system"]


def test_invalid_answers_discarded(downloads: Path) -> None:
    answers = iter(
        [
            {"id": 0, "category": "../../Windows", "confidence": 0.99, "reason": "x"},
            {"id": 99, "category": "archives", "confidence": 0.99, "reason": "wrong id"},
        ]
    )
    fake = FakeClient(answer=lambda e: next(answers))
    c = AnthropicClassifier(settings(), CategoryTree(), None, client=fake)
    cls, report = run(c, [item(downloads, "a.qqq"), item(downloads, "b.qqq")])
    assert report.applied == 0
    assert all(x.source is not Source.AI for x in cls)


def test_confidence_clamped_and_review_ignored(downloads: Path) -> None:
    fake = FakeClient(
        answer=lambda e: {"id": e["id"], "category": "archives", "confidence": 7, "reason": "x"}
    )
    cls, _ = run(
        AnthropicClassifier(settings(), CategoryTree(), None, client=fake),
        [item(downloads, "a.qqq")],
    )
    assert cls[0].confidence == MAX_AI_CONFIDENCE
    fake = FakeClient(
        answer=lambda e: {
            "id": e["id"],
            "category": "review",
            "confidence": 0.9,
            "reason": "unsure",
        }
    )
    cls, report = run(
        AnthropicClassifier(settings(), CategoryTree(), None, client=fake),
        [item(downloads, "b.qqq")],
    )
    assert report.applied == 0


def test_schema_enum_matches_enabled_categories(downloads: Path) -> None:
    tree = CategoryTree(overrides={"archives": {"enabled": False}})
    fake = FakeClient()
    run(AnthropicClassifier(settings(), tree, None, client=fake), [item(downloads, "a.qqq")])
    call = fake.calls[0]
    schema = call["output_config"]["format"]["schema"]
    enum = schema["properties"]["results"]["items"]["properties"]["category"]["enum"]
    assert "archives" not in enum and "documents.finance" in enum
    assert call["output_config"]["effort"] == "low"
    assert call["model"] == "claude-opus-5-5"
    assert call["fallbacks"] == "default"


def test_haiku_uses_plain_endpoint(downloads: Path) -> None:
    fake = FakeClient()
    run(
        AnthropicClassifier(
            settings(ai_model="claude-haiku-5-5"), CategoryTree(), None, client=fake
        ),
        [item(downloads, "a.qqq")],
    )
    assert "fallbacks" not in fake.calls[0]


def test_per_scan_limit(downloads: Path) -> None:
    fake = FakeClient()
    items = [item(downloads, f"{i}.qqq") for i in range(20)]
    _, report = run(
        AnthropicClassifier(settings(ai_max_files_per_scan=5), CategoryTree(), None, client=fake),
        items,
    )
    assert report.sent == 5 and report.skipped_limit == 15


def test_api_errors_are_reported_not_raised(downloads: Path) -> None:
    import anthropic
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.AuthenticationError(
        "bad key", response=httpx2.Response(401, request=request), body=None
    )
    fake = FakeClient(error=error)
    cls, report = run(
        AnthropicClassifier(settings(), CategoryTree(), None, client=fake),
        [item(downloads, f"{i}.qqq") for i in range(30)],
    )
    assert len(fake.calls) == 1, "fatal errors stop further batches"
    assert "API key" in report.errors[0]
    assert all(c.source is not Source.AI for c in cls)


def test_refusal_and_malformed_json(downloads: Path) -> None:
    fake = FakeClient(stop_reason="refusal")
    _, report = run(
        AnthropicClassifier(settings(), CategoryTree(), None, client=fake),
        [item(downloads, "a.qqq")],
    )
    assert report.applied == 0 and "declined" in report.errors[0]
    c = AnthropicClassifier(settings(), CategoryTree(), None, client=FakeClient())
    with pytest.raises(RuntimeError):
        c.parse("not json", {0})


def test_disclosure_text_reflects_consent() -> None:
    off = describe_transmission(AppSettings())
    assert "file name" not in off and "text from" not in off
    on = describe_transmission(settings(ai_consent_content=True))
    assert "file name" in on and "text from" in on and "no paths" in on


def test_learned_and_user_choices_never_sent(downloads: Path) -> None:
    fake = FakeClient()
    c = AnthropicClassifier(settings(), CategoryTree(), None, client=fake)
    it = item(downloads, "x.qqq")
    cls = [Classification("archives", 0.3, Source.USER, [])]
    c.refine([it], cls, threshold=0.55)
    assert fake.calls == []

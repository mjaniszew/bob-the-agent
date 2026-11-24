"""Fake PydanticAI model factory for the router-brain tests.

The mock seam (plan R5, REVIEW.md R-3): a fake model OBJECT injected into
``RouterBrain(cfg, model=...)`` — never an httpx/transport patch, never a
real provider model. :func:`make_fake_model` builds a
:class:`pydantic_ai.models.function.FunctionModel` whose function feeds the
given ``outputs`` (strings verbatim, dicts JSON-encoded) in call order; when
the list is exhausted the LAST item repeats forever, so an
invalid-then-valid list yields invalid on call 1 and valid forever after.

Each fake model records the message-lists it has been called with on
``.calls`` so tests assert the retry behavior (with ``retries=1`` the second
call's message list ends with a ``RetryPromptPart`` carrying the validator
error).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

__all__ = ["load_outputs", "make_fake_model", "preset", "sample"]

_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "router-outputs.json"


def load_outputs() -> dict[str, Any]:
    """The raw ``router-outputs.json`` fixture mapping."""
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def sample(name: str) -> str | dict:
    """One named fixture sample (string for invalid outputs, dict for valid)."""
    try:
        return load_outputs()[name]
    except KeyError:
        raise ValueError(f"unknown router fixture sample {name!r}") from None


def preset(name: str) -> FunctionModel:
    """Convenience single-shot fake model built from one fixture sample."""
    return make_fake_model([sample(name)])


def make_fake_model(
    outputs: list[str | dict], *, model_name: str = "fake-router"
) -> FunctionModel:
    """Build the fake model returning ``outputs`` items in order (last repeats).

    Not a ``pydantic_ai.Model`` subclass from the test's point of view —
    the factory hands back a real :class:`FunctionModel`, which
    ``Agent(model=..., output_type=..., retries=1)`` accepts unchanged.
    ``.calls`` on the returned object records the message-lists seen.
    """
    if outputs:
        items = [json.dumps(item) if isinstance(item, dict) else str(item) for item in outputs]
    else:
        items = [json.dumps({"agent": "hermes", "task_title": "empty"})]
    calls: list[list[Any]] = []

    def _function(messages: list[Any], info: Any) -> ModelResponse:
        calls.append(list(messages))
        index = len(calls) - 1
        content = items[index] if index < len(items) else items[-1]
        return ModelResponse(parts=[TextPart(content=content)])

    model = FunctionModel(_function, model_name=model_name)
    model.calls = calls  # type: ignore[attr-defined] — verified assignable on FunctionModel
    return model

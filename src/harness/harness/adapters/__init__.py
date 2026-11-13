"""Agent adapter registry (ARCHITECTURE.md section 2.2, phase R4).

Single lookup point binding agent names to adapter factories. Factories take
ONE argument — whatever the caller wants to hand the adapter — and return an
object satisfying the :class:`~harness.adapters.base.AgentRun` protocol:

- ``"hermes"`` → :class:`harness.adapters.hermes.HermesAdapter(config)`
  (factory arg: the full :class:`~harness.config.HarnessConfig`)
- ``"opencode"`` → :class:`harness.adapters.opencode.OpencodeAdapter(config)`
  (factory arg: the full ``HarnessConfig``)
- ``"fake"`` → :class:`harness.adapters.fake.FakeAdapter(scenario)` (factory
  arg: a scenario mapping or YAML/JSON file path)

:func:`register` overwrites a previous factory under the same name (extension
point for R5+ agents); :func:`get` invokes the registered factory with the
caller's ``factory_or_cfg`` argument and raises ``KeyError`` naming the known
agents for unknown names; :func:`known_agents` returns the sorted names.
Re-exports ``AgentRun`` / ``RunSpec`` / ``StreamEvent`` so consumers only need
``from harness.adapters import ...``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from harness.adapters.base import AgentRun, RunSpec, StreamEvent
from harness.adapters.fake import FakeAdapter
from harness.adapters.hermes import HermesAdapter
from harness.adapters.opencode import OpencodeAdapter

__all__ = [
    "AgentRun",
    "RunSpec",
    "StreamEvent",
    "get",
    "known_agents",
    "register",
]

_REGISTRY: dict[str, Callable[[Any], AgentRun]] = {}


def register(agent_name: str, factory: Callable[[Any], AgentRun]) -> None:
    """Bind ``agent_name`` to ``factory`` (overwrites any previous binding)."""
    _REGISTRY[agent_name] = factory


def get(agent_name: str, factory_or_cfg: Any) -> AgentRun:
    """Instantiate the adapter registered under ``agent_name``.

    The ``factory_or_cfg`` argument is passed verbatim to the factory (config
    object, scenario, ...). Unknown names raise ``KeyError`` listing the
    :func:`known_agents` set.
    """
    factory = _REGISTRY.get(agent_name)
    if factory is None:
        raise KeyError(
            f"unknown agent {agent_name!r}; known agents: {', '.join(known_agents())}"
        )
    return factory(factory_or_cfg)


def known_agents() -> tuple[str, ...]:
    """Sorted tuple of registered agent names."""
    return tuple(sorted(_REGISTRY))


register("hermes", lambda config: HermesAdapter(config))
register("opencode", lambda config: OpencodeAdapter(config))
register("fake", lambda scenario: FakeAdapter(scenario))

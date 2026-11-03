"""Static allow / confirm / deny table. Unknown tools are deny."""

from __future__ import annotations

from enum import Enum


class Decision(Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


DEFAULT_PERM: dict[str, Decision] = {
    "read_local": Decision.ALLOW,
    "search": Decision.ALLOW,
    "recall": Decision.ALLOW,
    "write_report": Decision.CONFIRM,
    "delete_data": Decision.DENY,
}


class PermTable:
    def __init__(self, overrides: dict[str, dict[str, Decision]] | None = None) -> None:
        self.overrides = overrides or {}

    def check(self, agent_id: str, tool: str) -> Decision:
        agent = self.overrides.get(agent_id, {})
        if tool in agent:
            return agent[tool]
        if tool in DEFAULT_PERM:
            return DEFAULT_PERM[tool]
        return Decision.DENY

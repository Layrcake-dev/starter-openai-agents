"""The Layrcake tools for this agent, as Agents SDK function tools.

The tools, their prices and every rule from https://layrcake.dev/agents.md live
in the official SDK's `layrcake.agent_tools`, the one implementation all
Layrcake agent integrations share. This file only hands five of them to the
Agents SDK. The run state is an `AsyncLayrcakeSession`, passed to the agent as
its context: each paid call carries the budget that is left, --dry-run reaches
every call, only verified emails go into a campaign, and nothing runs past the
approval link.
"""

from __future__ import annotations

import json
from typing import Any

from agents import FunctionTool
from agents.tool_context import ToolContext
from layrcake.agent_tools import SPECS, AsyncLayrcakeSession

NAMES = ("find_leads", "enrich", "verify_email", "create_campaign", "launch_campaign")
"""The tools this agent uses, out of layrcake.agent_tools.TOOL_SPECS."""


def layrcake_tool(name: str) -> FunctionTool:
    """One Layrcake tool: the SDK's name, description and argument schema, run on the session in the context."""
    spec = SPECS[name]

    async def invoke(ctx: ToolContext[Any], arguments: str) -> str:
        session: AsyncLayrcakeSession = ctx.context
        try:
            parsed = json.loads(arguments or "{}")
        except ValueError:
            return "Refused: invalid arguments: not valid JSON"
        if not isinstance(parsed, dict):
            return "Refused: invalid arguments: expected a JSON object"
        return await session.call(spec.name, parsed)  # never raises: refusals come back as text

    return FunctionTool(
        name=spec.name,
        description=spec.description,
        params_json_schema=spec.args.model_json_schema(),  # made strict by FunctionTool
        on_invoke_tool=invoke,
    )


TOOLS = [layrcake_tool(name) for name in NAMES]

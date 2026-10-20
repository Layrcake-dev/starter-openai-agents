"""Find people, verify their work emails and draft a campaign that waits for a person to approve."""

from __future__ import annotations

import argparse
import asyncio
import os
from decimal import Decimal
from typing import Any, Optional

from agents import Agent, ModelSettings, RunContextWrapper, RunHooks, Runner, Tool
from agents.agent import StopAtTools
from layrcake import AsyncLayrcake
from layrcake.agent_tools import AsyncLayrcakeSession

from instructions import DEFAULT_BRIEF, instructions
from layrcake_tools import TOOLS


def build_agent(budget: Decimal, dry_run: bool, model: Optional[str] = None) -> Agent[AsyncLayrcakeSession]:
    return Agent[AsyncLayrcakeSession](
        name="Layrcake prospector",
        instructions=instructions(budget, dry_run),
        model=model,  # None uses the Agents SDK default
        tools=list(TOOLS),
        tool_use_behavior=StopAtTools(stop_at_tool_names=["launch_campaign"]),  # stop at the approval link
        model_settings=ModelSettings(parallel_tool_calls=False),  # one Layrcake call at a time
    )


class PrintToolCalls(RunHooks[AsyncLayrcakeSession]):
    async def on_tool_start(
        self, context: RunContextWrapper[AsyncLayrcakeSession], agent: Agent[Any], tool: Tool
    ) -> None:
        print(f"-> {tool.name}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("brief", nargs="*", help="one sentence: who to find and what to say")
    parser.add_argument(
        "--dry-run", action="store_true", help="every Layrcake call returns its plan and charges nothing"
    )
    args = parser.parse_args()
    brief = " ".join(args.brief) or DEFAULT_BRIEF
    budget = Decimal(os.environ.get("BUDGET", "100"))
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY. Get one at https://platform.openai.com/api-keys")
    if budget <= 0:
        raise SystemExit("BUDGET must be a positive number of credits.")

    print(
        f"{'Dry run, nothing is charged' if args.dry_run else 'Live run'}. Budget: {budget} credits.\nBrief: {brief}\n"
    )
    async with AsyncLayrcake() as client:  # reads LAYRCAKE_API_KEY
        run = AsyncLayrcakeSession(client, budget_credits=budget, dry_run=args.dry_run)
        agent = build_agent(budget, args.dry_run, os.environ.get("MODEL"))
        result = await Runner.run(agent, brief, context=run, max_turns=40, hooks=PrintToolCalls())

    if not run.approval_url and not run.launch_error:
        print(f"\n{result.final_output}")
    print(f"\nFound {run.found} people. Kept {len(run.verified)} verified emails ({len(run.catch_all)} catch-all).")
    print(f"Layrcake credits spent: {run.spent} of {budget}.")
    if run.approval_url:
        print(f"Nothing has been sent. A person approves or declines here: {run.approval_url}")
    if run.launch_error:
        print(f"No approval link. Layrcake said: {run.launch_error}")


if __name__ == "__main__":
    asyncio.run(main())

"""Free setup check. Runs the agent's tools in dry-run mode against the live Layrcake API, with no model.

Every call returns a plan and charges nothing.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from decimal import Decimal
from typing import Any

from agents.tool import FunctionTool
from agents.tool_context import ToolContext
from layrcake import AsyncLayrcake

from layrcake_tools import Run, create_campaign, enrich, find_leads, verify_email

SAMPLE = {"email": "sample.person@example.com", "first_name": "Sample"}
CALLS: list[tuple[FunctionTool, dict[str, Any]]] = [
    (
        find_leads,
        {
            "audience": "Heads of sales at London software companies with 20 to 200 staff",
            "titles": ["Head of Sales"],
            "locations": ["London, United Kingdom"],
            "industries": ["Software Development"],
            "company_headcount": {"min": 20, "max": 200},
            "limit": 10,
        },
    ),
    (enrich, {"full_name": "Sample Person", "company_domain": "example.com"}),
    (verify_email, {"email": SAMPLE["email"]}),
    (
        create_campaign,
        {"name": "Smoke test", "leads": [SAMPLE], "steps": [{"subject": "Hello", "body": "Hi {{first_name}}"}]},
    ),
]


async def call(tool: FunctionTool, run: Run, args: dict[str, Any]) -> Any:
    """Invoke a tool exactly as the agent loop does, from JSON arguments."""
    arguments = json.dumps(args)
    ctx = ToolContext(context=run, tool_name=tool.name, tool_call_id="smoke", tool_arguments=arguments)
    output = await tool.on_invoke_tool(ctx, arguments)
    try:
        return json.loads(output)
    except (TypeError, ValueError):
        raise SystemExit(f"{tool.name} failed: {output}") from None


async def main() -> None:
    async with AsyncLayrcake() as client:  # reads LAYRCAKE_API_KEY
        me = await client.get_me()
        print(f'Key works for workspace "{me.result["workspace"]["name"]}".')
        run = Run(client=client, budget=Decimal(os.environ.get("BUDGET", "100")), dry_run=True, run_id=uuid.uuid4().hex)
        for tool, args in CALLS:
            plan = await call(tool, run, args)
            if not plan.get("dry_run"):
                raise SystemExit(f"{tool.name}: expected a plan, got {plan}")
            trips = f", would trip: {', '.join(plan['would_exceed'])}" if plan["would_exceed"] else ""
            print(f"{tool.name}: would cost up to {plan['would_cost_up_to']} credits{trips}")
    print(f"\nCharged: {run.spent} credits. Your key and the Layrcake API work.")


if __name__ == "__main__":
    asyncio.run(main())

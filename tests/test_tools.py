"""The agent's tools are the SDK's tools. The run rules themselves are tested once, in the layrcake SDK."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from agents.strict_schema import ensure_strict_json_schema
from agents.tool_context import ToolContext
from layrcake.agent_tools import SPECS, AsyncLayrcakeSession

from layrcake_tools import NAMES, TOOLS
from tests.conftest import APPROVAL_URL, CAMPAIGN_ID, MockLayrcake, meta

TOOL = {tool.name: tool for tool in TOOLS}


async def call(name: str, run: AsyncLayrcakeSession, /, **args: Any) -> Any:
    """Invoke the tool the way the agent loop does: JSON arguments in, JSON text out."""
    arguments = json.dumps(args)
    ctx = ToolContext(context=run, tool_name=name, tool_call_id="t1", tool_arguments=arguments)
    output = await TOOL[name].on_invoke_tool(ctx, arguments)
    try:
        return json.loads(output)
    except ValueError:
        return output  # a refusal, as the model reads it


def test_the_tools_are_the_sdk_tools() -> None:
    assert [tool.name for tool in TOOLS] == list(NAMES)
    for tool in TOOLS:
        spec = SPECS[tool.name]
        assert tool.description == spec.description
        assert tool.params_json_schema == ensure_strict_json_schema(spec.args.model_json_schema())
        assert tool.strict_json_schema
    assert list(TOOL["launch_campaign"].params_json_schema["properties"]) == ["campaign_id"]


async def test_paid_calls_carry_the_budget_left_and_an_idempotency_key(
    run: AsyncLayrcakeSession, api: MockLayrcake
) -> None:
    found = await call("find_leads", run, audience="Sales leaders", titles=["Head of Sales"])
    assert found["leads"] == [
        {
            "full_name": "Ada Lovelace",
            "title": "Head of Sales",
            "company_name": "Acme",
            "company_domain": "acme.com",
            "location": "London",
            "linkedin_url": None,
        }
    ]
    path, body, key = api.requests[0]
    assert (path, body["budget_credits"], body["dry_run"], body["limit"]) == ("/v1/find_leads", 100.0, False, 10)
    assert key is not None
    assert key.startswith("run-test-find_leads-")

    await call("enrich", run, full_name="Ada Lovelace", company_domain="acme.com")
    assert api.requests[1][1]["want"] == ["work_email"]
    assert api.requests[1][1]["budget_credits"] == 97.0
    assert run.spent == Decimal(8)


async def test_strict_mode_nulls_are_accepted(run: AsyncLayrcakeSession, api: MockLayrcake) -> None:
    """In strict mode the model sends every field, with null for the ones it leaves out."""
    found = await call(
        "find_leads",
        run,
        audience="Sales leaders",
        titles=["Head of Sales"],
        industries=None,
        locations=None,
        company_domains=None,
        company_headcount=None,
        limit=10,
    )
    assert found["leads"]
    assert "industries" not in api.requests[0][1]


async def test_find_leads_needs_a_filter(run: AsyncLayrcakeSession, api: MockLayrcake) -> None:
    output = await call("find_leads", run, audience="Anyone")
    assert output.startswith("Refused: Add at least one of titles")
    assert api.requests == []


async def test_campaigns_only_take_verified_emails(run: AsyncLayrcakeSession) -> None:
    await call("verify_email", run, email="ada@acme.com")
    await call("verify_email", run, email="bob@acme.com")
    steps = [{"subject": "Hi", "body": "Hi {{first_name}}"}]
    refused = await call("create_campaign", run, name="Q4", leads=[{"email": "bob@acme.com"}], steps=steps)
    assert refused.startswith("Refused: Verify these first")
    assert "bob@acme.com" in refused
    created = await call("create_campaign", run, name="Q4", leads=[{"email": "ada@acme.com"}], steps=steps)
    assert created["campaign_id"] == CAMPAIGN_ID
    assert (run.verified, run.spent) == ({"ada@acme.com"}, Decimal("2.5"))


async def test_launch_stops_at_the_approval_link(run: AsyncLayrcakeSession) -> None:
    preview = await call("launch_campaign", run, campaign_id=CAMPAIGN_ID)
    assert preview["approval_url"] == APPROVAL_URL
    assert "tok_1" not in json.dumps(preview)
    assert run.approval_url == APPROVAL_URL
    assert "waiting for approval" in await call("verify_email", run, email="x@acme.com")


async def test_paid_calls_stop_when_the_budget_is_spent(run: AsyncLayrcakeSession) -> None:
    run.budget = Decimal(5)
    await call("enrich", run, full_name="Ada Lovelace", company_domain="acme.com")
    assert "budget" in await call("enrich", run, full_name="Bob Smith", company_domain="acme.com")


async def test_dry_run_returns_the_plan(run: AsyncLayrcakeSession, api: MockLayrcake) -> None:
    run.dry_run = True
    plan = {"verb": "verify_email", "credits_estimate": "1.25", "would_exceed": [], "warnings": []}
    api.answers["/v1/verify_email"] = lambda body: {
        "result": {"dry_run": True, "advisory": True, "evaluated_at": "2026-10-20T00:00:00Z", "plan": plan},
        "layrcake": meta("0"),
    }
    assert await call("verify_email", run, email="ada@acme.com") == {
        "dry_run": True,
        "would_cost_up_to": "1.25",
        "would_exceed": [],
        "warnings": [],
    }
    assert api.requests[0][1]["dry_run"] is True


async def test_bad_json_comes_back_as_text(run: AsyncLayrcakeSession) -> None:
    ctx = ToolContext(context=run, tool_name="enrich", tool_call_id="t1", tool_arguments="{")
    assert (await TOOL["enrich"].on_invoke_tool(ctx, "{")).startswith("Refused: invalid arguments")

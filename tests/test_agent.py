"""Runs the real agent loop with a scripted model, so no model provider is called."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

import pytest
from agents import Runner
from agents.testing import ScriptedModel, assistant_message, function_call
from layrcake.agent_tools import AsyncLayrcakeSession

from agent import build_agent
from instructions import instructions
from tests.conftest import APPROVAL_URL, CAMPAIGN_ID, MockLayrcake


async def test_agent_stops_at_the_approval_link(run: AsyncLayrcakeSession, api: MockLayrcake) -> None:
    lead = {"email": "ada@acme.com", "first_name": "Ada", "company_name": "Acme"}
    model = ScriptedModel(
        [
            [
                function_call(
                    "find_leads", {"audience": "Sales leaders in London", "titles": ["Head of Sales"]}, call_id="c1"
                )
            ],
            [function_call("enrich", {"full_name": "Ada Lovelace", "company_domain": "acme.com"}, call_id="c2")],
            [function_call("verify_email", {"email": "ada@acme.com"}, call_id="c3")],
            [
                function_call(
                    "create_campaign",
                    {"name": "Q4", "leads": [lead], "steps": [{"subject": "Hi", "body": "Hi {{first_name}}"}]},
                    call_id="c4",
                )
            ],
            [function_call("launch_campaign", {"campaign_id": CAMPAIGN_ID}, call_id="c5")],
            [assistant_message("This turn must never happen.")],
        ]
    )
    agent = build_agent(Decimal(100), dry_run=False).clone(model=model)

    await Runner.run(agent, "Find heads of sales in London", context=run, max_turns=10)

    assert api.paths() == ["/v1/find_leads", "/v1/enrich", "/v1/verify_email", "/v1/campaigns", "/v1/launch_campaign"]
    assert run.approval_url == APPROVAL_URL
    assert model.remaining_steps == 1  # the loop stopped at launch_campaign
    assert run.spent == Decimal("9.25")
    assert "confirmation_token" not in api.requests[-1][1]


def test_the_agent_signs_with_sender_name() -> None:
    agent = build_agent(Decimal(100), dry_run=False, sender_name="Sam Carter")

    assert isinstance(agent.instructions, str)
    assert "End every email with this sign-off line, exactly as written: Sam Carter\n" in agent.instructions
    assert "never invent a name, title or company for the sender" in agent.instructions
    assert "[Your name]" not in agent.instructions


@pytest.mark.parametrize("sender_name", [None, "", "   "])
def test_without_sender_name_emails_are_signed_your_name(sender_name: Optional[str]) -> None:
    prompt = instructions(Decimal(100), dry_run=False, sender_name=sender_name)

    assert "End every email with this sign-off line, exactly as written: [Your name]\n" in prompt
    assert "never invent a name, title or company for the sender" in prompt
    assert prompt.endswith(
        "Also say that every email is signed [Your name] and the sign-off must be filled in before the campaign\n"
        "is approved."
    )

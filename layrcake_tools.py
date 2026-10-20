"""The Layrcake tools for this agent, and the run state they share.

Every rule from https://layrcake.dev/agents.md is enforced here, in code:
each paid call carries the budget that is left, --dry-run reaches every call,
only verified emails go into a campaign, and nothing runs past the approval link.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING, Any, Optional, cast

from agents import RunContextWrapper, function_tool
from layrcake import AsyncLayrcake, LayrcakeError, approval_required
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from layrcake.types import CreateCampaignResultData

SENDABLE = {"valid", "catch_all_valid"}  # verify_email answers we send to


@dataclass
class Run:
    """Shared by every tool call in one run. Passed to the agent as its context."""

    client: AsyncLayrcake
    budget: Decimal
    dry_run: bool
    run_id: str
    spent: Decimal = Decimal(0)
    found: int = 0
    kept: int = 0
    catch_all: int = 0
    approval_url: Optional[str] = None
    launch_error: Optional[str] = None
    verified: set[str] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # one paid call at a time keeps the budget exact

    def left(self) -> Decimal:
        return self.budget - self.spent

    def key(self, verb: str, args: Any) -> str:
        """Same call, same key: a repeated call returns the first result instead of charging again."""
        digest = hashlib.sha256(json.dumps([args, self.dry_run], sort_keys=True, default=str).encode()).hexdigest()
        return f"{self.run_id}-{verb}-{digest[:24]}"

    async def paid(self, verb: str, args: dict[str, Any], call: Any) -> Any:
        """Refuse past the budget, cap the call at what is left, and count what it cost."""
        async with self.lock:
            if self.approval_url:
                raise RuntimeError("The campaign is waiting for approval. Stop and report the approval link.")
            if self.left() < Decimal("0.01"):
                raise RuntimeError(f"The run budget of {self.budget} credits is spent. Stop and report.")
            ceiling = float(min(self.left(), Decimal(2000)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))
            response = await call(
                **args, budget_credits=ceiling, dry_run=self.dry_run, idempotency_key=self.key(verb, args)
            )
            self.spent += response.credits_charged or Decimal(0)
            return response


def _plan(result: Any) -> Optional[str]:
    """A dry run answers with a plan: the most the call could cost. Nothing is charged."""
    if isinstance(result, dict) and "plan" in result:
        plan = result["plan"]
        return _json(dry_run=True, would_cost_up_to=plan["credits_estimate"], would_exceed=plan["would_exceed"])
    return None


def tell_the_model(ctx: RunContextWrapper[Any], error: Exception) -> str:
    """Show the model why a call failed, so it can fix the call or stop. The default hides the reason."""
    return f"Refused: {error}"


layrcake_tool = function_tool(failure_error_function=tell_the_model)


def _json(**values: Any) -> str:
    return json.dumps(values, default=str)


class Headcount(BaseModel):
    min: Optional[int] = Field(default=None, ge=1)
    max: Optional[int] = Field(default=None, ge=1)


class Lead(BaseModel):
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company_name: Optional[str] = None
    location: Optional[str] = Field(default=None, max_length=200)


class Step(BaseModel):
    subject: Optional[str] = Field(default=None, max_length=300, description="Subject line")
    body: str = Field(description="Plain text. Use {{first_name}}.")
    wait_days: int = Field(default=0, ge=0, le=60)


@layrcake_tool
async def find_leads(
    ctx: RunContextWrapper[Run],
    audience: str,
    titles: Optional[list[str]] = None,
    industries: Optional[list[str]] = None,
    locations: Optional[list[str]] = None,
    company_domains: Optional[list[str]] = None,
    company_headcount: Optional[Headcount] = None,
    limit: int = 10,
) -> str:
    """Find people who match an audience. 0.25 credits per person found, 3 minimum.

    Args:
        audience: Who you want, in plain words.
        titles: Job titles, for example ["Head of Sales", "VP Sales"].
        industries: LinkedIn industry names or everyday words, for example ["Software Development"].
        locations: For example ["London, United Kingdom"].
        company_domains: Only people at these companies.
        company_headcount: Company size range.
        limit: How many people, 1 to 25.
    """
    if not (titles or industries or locations or company_domains):
        raise ValueError("Add at least one of titles, industries, locations or company_domains.")
    args: dict[str, Any] = {"audience": audience, "limit": max(1, min(limit, 25))}
    args.update(
        {
            k: v
            for k, v in {
                "titles": titles,
                "industries": industries,
                "locations": locations,
                "company_domains": company_domains,
            }.items()
            if v
        }
    )
    if company_headcount:
        args["company_headcount"] = company_headcount.model_dump(exclude_none=True)
    run = ctx.context
    response = await run.paid("find_leads", args, run.client.find_leads)
    if plan := _plan(response.result):
        return plan
    leads = response.result["leads"]
    run.found += len(leads)
    keep = ("full_name", "title", "company_name", "company_domain", "location")
    return _json(
        leads=[{k: lead[k] for k in keep} for lead in leads],
        credits_charged=response.credits_charged,
        budget_left=run.left(),
    )


@layrcake_tool
async def enrich(ctx: RunContextWrapper[Run], full_name: str, company_domain: str) -> str:
    """Find one person's work email from their full name and company domain. 5 credits when found, free when not."""
    run = ctx.context
    args = {"person": {"full_name": full_name, "company_domain": company_domain}, "want": ["work_email"]}
    response = await run.paid("enrich", args, run.client.enrich)
    if plan := _plan(response.result):
        return plan
    contact = response.result.get("contact") or {}
    return _json(work_email=contact.get("work_email"), credits_charged=response.credits_charged, budget_left=run.left())


@layrcake_tool
async def verify_email(ctx: RunContextWrapper[Run], email: str) -> str:
    """Check that an address can receive mail. Keep valid and catch_all_valid. Drop everything else. 1.25 credits."""
    run = ctx.context
    response = await run.paid("verify_email", {"email": email}, run.client.verify_email)
    if plan := _plan(response.result):
        return plan
    status = response.result["status"]
    keep = status in SENDABLE
    if keep and email.lower() not in run.verified:
        run.verified.add(email.lower())
        run.kept += 1
        if status == "catch_all_valid":
            run.catch_all += 1
    return _json(
        email=email, status=status, keep=keep, credits_charged=response.credits_charged, budget_left=run.left()
    )


@layrcake_tool
async def create_campaign(ctx: RunContextWrapper[Run], name: str, leads: list[Lead], steps: list[Step]) -> str:
    """Save a draft campaign for verified people, with one to three steps. Drafts never send. Free."""
    run = ctx.context
    unverified = [lead.email for lead in leads if lead.email.lower() not in run.verified]
    if unverified and not run.dry_run:
        raise ValueError(f"Verify these first, or drop them: {', '.join(unverified)}")
    lead_rows: list[Any] = [lead.model_dump(exclude_none=True) for lead in leads]
    step_rows: list[Any] = [step.model_dump(exclude_none=True) for step in steps[:3]]
    key = run.key("create_campaign", [name, lead_rows, step_rows])
    response = await run.client.create_campaign(
        name=name, leads=lead_rows, steps=step_rows, dry_run=run.dry_run, idempotency_key=key
    )
    if plan := _plan(response.result):
        return plan
    draft = cast("CreateCampaignResultData", response.result)
    return _json(campaign_id=draft["campaign_id"], state=draft["state"], removed=draft.get("removed"))


@layrcake_tool
async def launch_campaign(ctx: RunContextWrapper[Run], campaign_id: str) -> str:
    """Ask a person to approve the draft. Sends nothing. Returns a preview and an approval link. Call it once, last."""
    run = ctx.context
    try:
        response = await run.client.launch_campaign(
            campaign_id=campaign_id, dry_run=run.dry_run, idempotency_key=run.key("launch_campaign", campaign_id)
        )
    except LayrcakeError as error:
        run.launch_error = f"{error.code}: {error.message}"  # the run stops here, so keep the reason
        raise
    if plan := _plan(response.result):
        return plan
    approval = approval_required(response)
    run.approval_url = approval.approval_url if approval else None
    return _json(status=response.result.get("status"), approval_url=run.approval_url, preview=response.result)


TOOLS = [find_leads, enrich, verify_email, create_campaign, launch_campaign]

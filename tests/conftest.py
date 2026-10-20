"""A Layrcake client whose HTTP answers come from a table instead of the network."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
from agents import set_tracing_disabled
from layrcake import AsyncLayrcake

from layrcake_tools import Run

set_tracing_disabled(True)  # tests never export traces

Answer = Callable[[dict[str, Any]], Any]
CAMPAIGN_ID = "4f1c2b8e-0b7a-4c1e-9a55-2f6f1d2c3b4a"
APPROVAL_URL = "https://app.layrcake.dev/approvals/a1"


def meta(credits: str) -> dict[str, str]:
    return {"request_id": "req_1", "credits_charged": credits, "credits_available": "250"}


LEAD = {
    "fingerprint": "f1",
    "full_name": "Ada Lovelace",
    "title": "Head of Sales",
    "company_name": "Acme",
    "company_domain": "acme.com",
    "work_email": None,
    "work_email_status": None,
    "mobile_phone": None,
    "linkedin_url": None,
    "location": "London",
    "observed_at": None,
}

ANSWERS: dict[str, Answer] = {
    "/v1/find_leads": lambda body: {"result": {"leads": [LEAD], "total_found": 1}, "layrcake": meta("3")},
    "/v1/enrich": lambda body: {"result": {"contact": {"work_email": "ada@acme.com"}}, "layrcake": meta("5.000000")},
    "/v1/verify_email": lambda body: {
        "result": {"email": body["email"], "status": "valid" if body["email"] == "ada@acme.com" else "invalid"},
        "layrcake": meta("1.250000"),
    },
    "/v1/campaigns": lambda body: {"result": {"campaign_id": CAMPAIGN_ID, "state": "draft", "removed": {"total": 0}}},
    "/v1/launch_campaign": lambda body: {
        "result": {
            "status": "needs_confirmation",
            "campaign_id": body["campaign_id"],
            "confirmation_token": "tok_1",
            "approval_url": APPROVAL_URL,
        },
    },
}


class MockLayrcake:
    def __init__(self, answers: dict[str, Answer]) -> None:
        self.answers = answers
        self.requests: list[tuple[str, dict[str, Any], str | None]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append((request.url.path, body, request.headers.get("Idempotency-Key")))
        answer = self.answers.get(request.url.path)
        if answer is None:
            return httpx.Response(404, json={"error": {"code": "not_found", "message": request.url.path}})
        return httpx.Response(200, json=answer(body))

    def paths(self) -> list[str]:
        return [path for path, _, _ in self.requests]


@pytest.fixture
def api() -> MockLayrcake:
    return MockLayrcake(dict(ANSWERS))


@pytest.fixture
async def client(api: MockLayrcake) -> AsyncIterator[AsyncLayrcake]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(api.handle))
    async with AsyncLayrcake(api_key="lc_test", http_client=http, max_retries=0) as layrcake:
        yield layrcake
    await http.aclose()


@pytest.fixture
def run(client: AsyncLayrcake) -> Run:
    return Run(client=client, budget=Decimal(100), dry_run=False, run_id="run-test")

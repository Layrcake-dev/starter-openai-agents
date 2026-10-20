# Layrcake starter for the OpenAI Agents SDK

An OpenAI agent that turns a one-sentence brief into about 10 people with verified work emails and a draft campaign, then stops at the approval link.

It uses the [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) for Python and the official [`layrcake`](https://github.com/Layrcake-dev/layrcake-python) Python SDK. Nothing is sent. A person approves the campaign in the Layrcake console before any email goes out.

## What it does

1. `find_leads` finds people who match your brief.
2. `enrich` gets each person's work email.
3. `verify_email` checks every address. Only `valid` and `catch_all_valid` addresses are kept.
4. `create_campaign` saves a short draft sequence for the kept people. Drafts never send.
5. `launch_campaign` returns a preview and an approval link. The agent stops there.

## Why the SDK for this kit

The Agents SDK builds a tool from a typed Python function and its docstring with `@function_tool`. Wrapping the `layrcake` SDK in five such functions fits that model:

- Each tool takes only the arguments this agent needs. `launch_campaign` has no `confirmation_token` argument, so the model cannot approve anything.
- The run state travels in the run context (`RunContextWrapper[Run]`): the Layrcake client, the budget, what was spent, and which emails passed verification. The rules live in `layrcake_tools.py`. Every paid call gets `budget_credits` set to what is left of `BUDGET`. `--dry-run` adds `dry_run: true` to every call. `create_campaign` refuses any address that did not pass `verify_email` in this run.
- `StopAtTools(stop_at_tool_names=["launch_campaign"])` ends the run at the approval link. `parallel_tool_calls=False` and a lock run one Layrcake call at a time, so the running total is exact.
- A custom `failure_error_function` shows the model why a call was refused, such as a spent budget. The SDK's default hides the reason.
- The SDK handles retries, typed errors and idempotency keys. The tools pass a key derived from the call, so a repeated call returns the first result instead of charging again. Credits are `Decimal`, never floats.

The hosted MCP server at `https://mcp.layrcake.dev/mcp` also works with the Agents SDK through `MCPServerStreamableHttp`. Function tools keep the schemas small and the guardrails in your code.

Layrcake's approval happens in the Layrcake console and is enforced by the API. The Agents SDK's own `needs_approval` option is not needed for it.

## Prerequisites

- Python 3.10 or later and [uv](https://docs.astral.sh/uv/).
- A Layrcake API key. Create one at [layrcake.dev/connect](https://layrcake.dev/connect). New accounts start with 300 free credits.
- An OpenAI API key from [platform.openai.com](https://platform.openai.com/api-keys).
- Sending mailboxes in your Layrcake workspace, for the launch preview. Without them, `launch_campaign` returns an error that says what is missing, and the run reports it.

## Setup

```sh
uv sync
cp .env.example .env   # then add LAYRCAKE_API_KEY and OPENAI_API_KEY
uv run --env-file .env smoke.py   # free check of your Layrcake key, no model call
```

`smoke.py` runs the agent's own tools in dry-run mode against the live API, the same way the agent loop calls them. It charges nothing and calls no model. It prints something like this:

```text
Key works for workspace "My workspace".
find_leads: would cost up to 3 credits
enrich: would cost up to 5 credits
verify_email: would cost up to 1.25 credits
create_campaign: would cost up to 0 credits

Charged: 0 credits. Your key and the Layrcake API work.
```

If `create_campaign` says it `would trip: sending_capacity`, connect mailboxes in the [Layrcake console](https://app.layrcake.dev) before a live run.

## Run

Start with a dry run. Every Layrcake call returns a plan with its cost instead of data, so it is free. It still uses your OpenAI key.

```sh
uv run --env-file .env agent.py --dry-run
```

Then run it for real with your own brief:

```sh
uv run --env-file .env agent.py "Find 10 heads of sales at software companies in London with 20 to 200 staff and draft a short campaign about cutting time spent on lead research."
```

With no brief, it uses the one above.

## What you will see

Each tool call prints as it starts. The run ends with a short report from the run state:

```text
Live run. Budget: 100 credits.
Brief: Find 10 heads of sales at software companies in London ...

-> find_leads
-> enrich
-> verify_email
...
-> create_campaign
-> launch_campaign

Found 10 people. Kept 7 verified emails (3 catch-all).
Layrcake credits spent: 46.75 of 100.
Nothing has been sent. A person approves or declines here: <approval link>
```

These numbers are from our test run of the default brief on 20 October 2026. Your run will differ. A dry run ends with the model's cost estimate instead of a link.

## How approval works

`launch_campaign` never sends on its own. Its first answer is a preview, a `confirmation_token` and an `approval_url`. The tool returns the link and the run stops.

A signed-in person in your workspace opens the link, reads the preview and approves or declines. Layrcake can also email an approver in your workspace. After approval, Layrcake starts the campaign itself when the approval says `execution: server`. Otherwise you repeat the call with the token. This agent never does either: its `launch_campaign` tool has no token argument.

## Cost

Our test runs of the default brief cost 45.5 to 53 Layrcake credits. A credit is $0.01. Finding people costs 0.25 credits each, an email costs 5 credits when found, a verification costs 1.25, and lookups that find nothing are free. Drafting the campaign is free. See [pricing](https://docs.layrcake.dev/pricing.html).

`BUDGET` in `.env` is the ceiling for the whole run (default 100). Model usage is billed separately by OpenAI. Set `MODEL` to change the model. Without it, the agent uses the Agents SDK default (`gpt-5.6-luna` in openai-agents 0.24).

The Agents SDK sends traces of each run to your OpenAI dashboard, including tool results such as names and emails. Set `OPENAI_AGENTS_DISABLE_TRACING=1` to turn that off.

## Using OpenRouter

The kit runs unchanged through [OpenRouter](https://openrouter.ai). Put these in `.env` instead of a direct model key:

```text
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=<your OpenRouter key>
OPENAI_AGENTS_DISABLE_TRACING=1
MODEL=openai/gpt-5.6-luna
```

We ran this kit this way on 20 October 2026.

## Project layout

```text
agent.py              the agent: model settings, stop rule, report
layrcake_tools.py     the five Layrcake tools and the run state
instructions.py       the system prompt
smoke.py              free setup check, no model call
tests/test_tools.py   tool tests with a mocked Layrcake API
tests/test_agent.py   the full agent loop with a scripted model
```

## Development

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

The tests give the `layrcake` client an `httpx.MockTransport`, and drive the agent with the Agents SDK's `ScriptedModel`. They need no keys and make no network calls.

## Links

- Layrcake docs: [docs.layrcake.dev](https://docs.layrcake.dev)
- Rules for agents: [layrcake.dev/agents.md](https://layrcake.dev/agents.md)
- Python SDK: [github.com/Layrcake-dev/layrcake-python](https://github.com/Layrcake-dev/layrcake-python)
- TypeScript SDK: [github.com/Layrcake-dev/layrcake-js](https://github.com/Layrcake-dev/layrcake-js)
- MCP server: `https://mcp.layrcake.dev/mcp`
- OpenAI Agents SDK: [openai.github.io/openai-agents-python](https://openai.github.io/openai-agents-python/)

## License

MIT. See [LICENSE](LICENSE).

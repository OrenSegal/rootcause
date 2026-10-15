"""Cost accounting, and the request the Anthropic adapter actually sends (no network)."""

import json

import httpx2
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient
from anthropic.types.beta import BetaUsage

from rootcause.adapters.anthropic_provider import FALLBACK_BETA, PRICES, AnthropicProvider
from rootcause.application.budget import Budget, BudgetExceededError
from rootcause.application.investigator import SUBMIT_DIAGNOSIS


def usage(**kw) -> BetaUsage:
    return BetaUsage.model_validate({"input_tokens": 0, "output_tokens": 0, **kw})


def test_cost_counts_every_token_class():
    budget = Budget(limit_usd=10, prices=PRICES)
    cost = budget.record(
        "claude-opus-5-5",
        usage(
            input_tokens=1_000_000,
            output_tokens=100_000,
            cache_read_input_tokens=1_000_000,
            cache_creation_input_tokens=100_000,
        ),
    )
    assert cost == pytest.approx(4.00 + 2.00 + 0.20 + 0.50)


def test_unknown_model_is_priced_at_the_most_expensive_rate():
    budget = Budget(limit_usd=10, prices=PRICES)
    priciest = max(PRICES.values(), key=lambda p: p.output)
    assert budget.price_for("claude-someday-9") == priciest


def test_fallback_targets_are_priced():
    # A declined request can be re-run and billed on these.
    assert {"claude-opus-5", "claude-opus-4-8", "claude-fable-5-1"} <= set(PRICES)


def test_check_raises_once_the_cap_is_reached():
    budget = Budget(limit_usd=0.01, prices=PRICES)
    budget.record("claude-opus-5-5", usage(output_tokens=1000))
    with pytest.raises(BudgetExceededError):
        budget.check()


async def test_request_carries_thinking_effort_cache_and_fallbacks():
    captured: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client = AsyncAnthropic(
        api_key="test",
        http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    provider = AnthropicProvider(effort="xhigh", client=client)
    response = await provider.complete(
        system=[{"type": "text", "text": "s"}],
        messages=[{"role": "user", "content": "hi"}],
        tools=[SUBMIT_DIAGNOSIS.definition],
    )

    body = captured["body"]
    assert response.stop_reason == "end_turn"
    assert body["model"] == "claude-opus-5-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "xhigh"}
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["fallbacks"] == "default"
    assert "tool_choice" not in body  # forced tool choice is rejected by Opus 5.5
    assert body["tools"][0]["strict"] is True
    assert FALLBACK_BETA in captured["headers"]["anthropic-beta"]

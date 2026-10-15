from typing import Any, Literal, cast

from anthropic import AsyncAnthropic
from anthropic.types.beta import (
    BetaMessage,
    BetaMessageParam,
    BetaTextBlockParam,
    BetaToolUnionParam,
)

from rootcause.application.budget import Price

Effort = Literal["low", "medium", "high", "xhigh", "max"]
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# USD per million tokens, Anthropic first-party rates (cached 2026-10-06).
PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20, cache_write=5.00),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.20, cache_write=2.50),
    # Possible server-side fallback targets: a declined request can be billed at these rates.
    "claude-opus-5": Price(input=5.00, output=25.00, cache_read=0.50, cache_write=6.25),
    "claude-opus-4-8": Price(input=5.00, output=25.00, cache_read=0.50, cache_write=6.25),
    "claude-fable-5-1": Price(input=10.00, output=50.00, cache_read=0.25, cache_write=12.50),
}


class AnthropicProvider:
    """Claude via the Messages API.

    - Thinking is always on for Opus 5.5; depth is set with `effort`, which is
      set explicitly because the model's default is medium.
    - `fallbacks="default"` re-runs a request the safety classifier declines on
      a fallback model picked by refusal category, inside the same call.
    - Top-level `cache_control` caches the stable prefix (tools + system).
    """

    def __init__(
        self,
        model: str = "claude-opus-5-5",
        effort: Effort = "high",
        max_tokens: int = 16_000,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self.model = model
        self.effort: Effort = effort
        self.name = f"anthropic:{model}:{effort}"
        self.max_tokens = max_tokens
        self.client = client or AsyncAnthropic()

    async def complete(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> BetaMessage:
        # The loop keeps plain dicts (they are also what gets logged); the SDK's
        # TypedDicts describe the same wire shapes.
        return await self.client.beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=cast(list[BetaTextBlockParam], system),
            messages=cast(list[BetaMessageParam], messages),
            tools=cast(list[BetaToolUnionParam], tools),
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},
            fallbacks="default",
            betas=[FALLBACK_BETA],
        )

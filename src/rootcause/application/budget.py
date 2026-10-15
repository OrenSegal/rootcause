from collections.abc import Mapping
from dataclasses import dataclass, field

from anthropic.types.beta import BetaUsage


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


class BudgetExceededError(Exception):
    pass


@dataclass
class Budget:
    """Hard spending cap, checked before every model call. One budget can be shared
    by an orchestrator and its workers, so the cap covers the whole investigation."""

    limit_usd: float
    prices: Mapping[str, Price]
    spent_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    calls: int = 0
    by_model: dict[str, float] = field(default_factory=dict)

    def check(self) -> None:
        if self.spent_usd >= self.limit_usd:
            raise BudgetExceededError(f"spent ${self.spent_usd:.4f} of ${self.limit_usd:.2f}")

    def price_for(self, model: str) -> Price:
        # An unlisted model is priced at the most expensive listed rate, so the cap errs safe.
        return self.prices.get(model) or max(self.prices.values(), key=lambda p: p.output)

    def record(self, model: str, usage: BetaUsage) -> float:
        price = self.price_for(model)
        cache_read = usage.cache_read_input_tokens or 0
        cache_write = usage.cache_creation_input_tokens or 0
        cost = (
            usage.input_tokens * price.input
            + usage.output_tokens * price.output
            + cache_read * price.cache_read
            + cache_write * price.cache_write
        ) / 1_000_000
        self.spent_usd += cost
        self.input_tokens += usage.input_tokens + cache_read + cache_write
        self.output_tokens += usage.output_tokens
        self.cache_read_tokens += cache_read
        self.calls += 1
        self.by_model[model] = self.by_model.get(model, 0.0) + cost
        return cost

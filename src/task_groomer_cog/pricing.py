"""What a model run costs, from the tokens it used.

Prices are USD per million tokens, as Anthropic lists them. A model missing
from the table cannot be costed, so it cannot be capped, and is refused
rather than run.

Cached input is billed apart from fresh input: a write to the cache costs
more than plain input, and a read from it far less. The groomer caches its
fixed prompt (instructions, tools, registry), so every round after the
first reads most of its input from the cache.

Haiku 5.5 is the one model priced by prompt length: a request whose input,
cache included, is over 100,000 tokens pays the higher rates for all of it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rates:
    """USD per million tokens."""

    input: float
    output: float
    cache_write: float
    cache_read: float


@dataclass(frozen=True)
class Price:
    """A model's rates, with the higher tier for long prompts if it has one."""

    base: Rates
    long_prompt: Rates | None = None
    long_prompt_over: int = 100_000


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(Rates(4.0, 20.0, 5.0, 0.20)),
    "claude-sonnet-5-5": Price(Rates(2.0, 10.0, 2.50, 0.10)),
    "claude-haiku-5-5": Price(
        Rates(0.10, 0.50, 0.125, 0.01),
        long_prompt=Rates(0.50, 2.50, 0.625, 0.05),
    ),
}


class UnpricedModel(ValueError):
    """The model is not in the price table, so a run could not be capped."""


@dataclass(frozen=True)
class Usage:
    """Tokens one request used, as the Messages API reports them."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def prompt_tokens(self) -> int:
        """Every input token, cached or not: what the prompt-length tier reads."""
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
        )


def price_of(model: str) -> Price:
    """The model's price. Raises :class:`UnpricedModel` for one with none here."""
    try:
        return PRICES[model]
    except KeyError:
        raise UnpricedModel(
            f"{model} has no price here, so its runs could not be capped; "
            f"use one of {', '.join(sorted(PRICES))}"
        ) from None


def cost(model: str, usage: Usage) -> float:
    """The USD cost of one request."""
    price = price_of(model)
    rates = price.base
    if price.long_prompt and usage.prompt_tokens > price.long_prompt_over:
        rates = price.long_prompt
    return (
        usage.input_tokens * rates.input
        + usage.output_tokens * rates.output
        + usage.cache_creation_input_tokens * rates.cache_write
        + usage.cache_read_input_tokens * rates.cache_read
    ) / 1_000_000

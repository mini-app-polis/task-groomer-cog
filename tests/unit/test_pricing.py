from __future__ import annotations

import pytest

from task_groomer_cog import pricing
from task_groomer_cog.pricing import Usage, cost


def test_sonnet_prices_each_kind_of_token() -> None:
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_input_tokens=1_000_000,
        cache_read_input_tokens=1_000_000,
    )
    assert cost("claude-sonnet-5-5", usage) == pytest.approx(2 + 10 + 2.5 + 0.1)


def test_haiku_pays_the_long_rate_for_all_of_a_long_prompt() -> None:
    short = Usage(input_tokens=100_000)
    long = Usage(input_tokens=60_000, cache_read_input_tokens=60_000)
    assert cost("claude-haiku-5-5", short) == pytest.approx(0.01)
    assert cost("claude-haiku-5-5", long) == pytest.approx(
        (60_000 * 0.50 + 60_000 * 0.05) / 1_000_000
    )


def test_usage_adds_up() -> None:
    total = Usage(1, 2, 3, 4) + Usage(10, 20, 30, 40)
    assert total == Usage(11, 22, 33, 44)
    assert total.prompt_tokens == 11 + 33 + 44


def test_a_model_without_a_price_is_refused() -> None:
    with pytest.raises(pricing.UnpricedModel):
        cost("claude-unknown", Usage())

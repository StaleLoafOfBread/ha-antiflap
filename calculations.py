"""Shared calculation functions for Antiflap."""

from __future__ import annotations

from math import ceil, log

from .const import (
    DEFAULT_BASE_HOLD_DIVISOR,
    DEFAULT_WINDOW_FLAP_COUNT,
    DEFAULT_WINDOW_FLAP_GAP_DIVISOR,
)


def default_window_seconds(
    free_flaps: int,
    flap_gap_seconds: int,
    base_hold_seconds: int,
    hold_factor: float,
    max_hold_seconds: int,
) -> int:
    """Return the default memory window based on the adaptive hold curve."""
    flap_interval_seconds = max(
        ceil(flap_gap_seconds / DEFAULT_WINDOW_FLAP_GAP_DIVISOR),
        1,
    )
    flap_count_to_max = _flap_count_to_reach_max_hold(
        base_hold_seconds,
        hold_factor,
        max_hold_seconds,
    )
    flap_timestamp_count = free_flaps + flap_count_to_max

    return max(
        (flap_timestamp_count - 1) * flap_interval_seconds,
        flap_interval_seconds,
    )


def _flap_count_to_reach_max_hold(
    base_hold_seconds: int,
    hold_factor: float,
    max_hold_seconds: int,
) -> int:
    """Return counted flaps needed for the hold curve to reach its cap."""
    if base_hold_seconds >= max_hold_seconds:
        return 1

    if hold_factor <= 1.0:
        return DEFAULT_WINDOW_FLAP_COUNT

    # Each counted flap multiplies the base hold by hold_factor:
    #   flap 1: base_hold_seconds
    #   flap 2: base_hold_seconds * hold_factor
    #   flap 3: base_hold_seconds * hold_factor ** 2
    #
    # The multiplier we need to reach the cap is max/base. For example, if the
    # base hold is 30s and the cap is 600s, the hold must grow by 20x.
    required_multiplier = max_hold_seconds / base_hold_seconds

    # This is how many multiplication steps are needed to reach that growth.
    # With hold_factor 1.4 and required_multiplier 20, this is about 8.9 steps.
    multiplication_steps = log(required_multiplier) / log(hold_factor)

    # Flap 1 uses zero multiplication steps, so add 1 to convert steps to the
    # counted flap number. ceil() gives the first flap that reaches the cap.
    return ceil(multiplication_steps) + 1


def default_base_hold_seconds(flap_gap_seconds: int) -> int:
    """Return the default base hold as a fraction of the flap-gap duration."""
    return max(ceil(flap_gap_seconds / DEFAULT_BASE_HOLD_DIVISOR), 1)

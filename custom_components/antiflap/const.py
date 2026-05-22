"""Constants for Antiflap."""

from __future__ import annotations

from homeassistant.const import STATE_ON

DOMAIN = "antiflap"

CONF_INPUT_ENTITY = "input_entity"
CONF_ACTIVE_STATE = "active_state"
CONF_FREE_FLAPS = "free_flaps"
CONF_FLAP_GAP_SECONDS = "flap_gap_seconds"
CONF_BASE_HOLD_SECONDS = "base_hold_seconds"
CONF_HOLD_FACTOR = "hold_factor"
CONF_MAX_HOLD_SECONDS = "max_hold_seconds"
CONF_MIN_ON_SECONDS = "min_on_seconds"
CONF_WINDOW_SECONDS = "window_seconds"

DEFAULT_ACTIVE_STATE = STATE_ON
DEFAULT_BASE_HOLD_DIVISOR = 4
DEFAULT_FREE_FLAPS = 0
DEFAULT_FLAP_GAP_SECONDS = 120
DEFAULT_HOLD_FACTOR = 1.4
DEFAULT_MAX_HOLD_SECONDS = 600
DEFAULT_MIN_ON_SECONDS = 0
DEFAULT_WINDOW_FLAP_COUNT = 10
DEFAULT_WINDOW_FLAP_GAP_DIVISOR = 2

SERVICE_RESET = "reset"

# Attributes exposed on the binary sensor for debugging.
ATTR_BASE_HOLD_SECONDS = "base_hold_seconds"
ATTR_FREE_FLAPS = "free_flaps"
ATTR_HOLD_FACTOR = "hold_factor"
ATTR_HOLD_SECONDS = "hold_seconds"
ATTR_HOLD_UNTIL = "hold_until"
ATTR_MAX_HOLD_SECONDS = "max_hold_seconds"
ATTR_MIN_ON_SECONDS = "min_on_seconds"
ATTR_MIN_ON_UNTIL = "min_on_until"
ATTR_ACTIVE_STARTED_AT = "active_started_at"
ATTR_INACTIVE_STARTED_AT = "inactive_started_at"
ATTR_FLAP_GAP_SECONDS = "flap_gap_seconds"
ATTR_FLAP_TIMESTAMPS = "flap_timestamps"
ATTR_INPUT_ENTITY = "input_entity"
ATTR_ACTIVE_STATE = "active_state"
ATTR_INPUT_STATE = "input_state"
ATTR_TOTAL_FLAPS = "total_flaps"
ATTR_WINDOW_SECONDS = "window_seconds"

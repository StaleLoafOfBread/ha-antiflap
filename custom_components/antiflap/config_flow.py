"""Config flow for Antiflap.

A config flow is what makes the integration configurable from the Home Assistant
UI instead of only from configuration.yaml.

This file intentionally keeps the flow simple:

    - One setup form.
    - One options form for editing later.
    - No automatic discovery yet.

The flow stores user input in a config entry. The binary sensor reads that config
entry in binary_sensor.py.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import (
    ATTR_FRIENDLY_NAME,
    CONF_DEVICE_ID,
    CONF_NAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult, section
from homeassistant.helpers.device import async_entity_id_to_device_id
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector

from .const import (
    CONF_BASE_HOLD_SECONDS,
    CONF_ACTIVE_STATE,
    CONF_FREE_FLAPS,
    CONF_HOLD_FACTOR,
    CONF_INPUT_ENTITY,
    CONF_MAX_HOLD_SECONDS,
    CONF_MIN_BASE_HOLD_SECONDS,
    CONF_MIN_ON_SECONDS,
    CONF_FLAP_GAP_SECONDS,
    CONF_WINDOW_SECONDS,
    DEFAULT_MAX_HOLD_SECONDS,
    DEFAULT_MIN_BASE_HOLD_SECONDS,
    DEFAULT_FLAP_GAP_SECONDS,
    DOMAIN,
)
from .calculations import default_base_hold_seconds_with_floor

SECTION_FLAP_DETECTION = "flap_detection"
SECTION_HOLD_BEHAVIOR = "hold_behavior"

DEFAULTED_CONFIG_KEYS = (
    CONF_FREE_FLAPS,
    CONF_FLAP_GAP_SECONDS,
    CONF_BASE_HOLD_SECONDS,
    CONF_MIN_BASE_HOLD_SECONDS,
    CONF_HOLD_FACTOR,
    CONF_MAX_HOLD_SECONDS,
    CONF_MIN_ON_SECONDS,
    CONF_WINDOW_SECONDS,
)


def _current_data(config_entry: config_entries.ConfigEntry) -> dict[str, Any]:
    """Return the current config-entry options."""
    return dict(config_entry.options)


def _build_schema(current: dict[str, Any] | None = None) -> vol.Schema:
    """Build the setup/options form schema.

    Home Assistant uses voluptuous schemas to decide what fields to show in the
    UI and how to validate submitted values.
    """
    current = current or {}

    input_entity_key = vol.Required(CONF_INPUT_ENTITY)
    if CONF_INPUT_ENTITY in current:
        input_entity_key = vol.Required(
            CONF_INPUT_ENTITY, default=current[CONF_INPUT_ENTITY])

    def optional_key(key: str) -> vol.Optional:
        if key in current:
            return vol.Optional(key, default=current[key])

        return vol.Optional(key)

    def optional_int(*, min_value: int) -> vol.Schema:
        return vol.Any(
            None,
            "",
            vol.All(vol.Coerce(int), vol.Range(min=min_value)),
        )

    def optional_float(*, min_value: float) -> vol.Schema:
        return vol.Any(
            None,
            "",
            vol.All(vol.Coerce(float), vol.Range(min=min_value)),
        )

    base_hold_key = optional_key(CONF_BASE_HOLD_SECONDS)
    window_key = optional_key(CONF_WINDOW_SECONDS)

    return vol.Schema(
        {
            input_entity_key: selector.EntitySelector(),

            vol.Optional(
                CONF_ACTIVE_STATE,
                default=current.get(CONF_ACTIVE_STATE, ""),
            ): selector.TextSelector(),

            vol.Required(SECTION_FLAP_DETECTION): section(
                vol.Schema(
                    {
                        optional_key(CONF_FREE_FLAPS): optional_int(min_value=0),

                        optional_key(CONF_FLAP_GAP_SECONDS): optional_int(min_value=1),

                        window_key: optional_int(min_value=1),
                    }
                ),
                {"collapsed": True},
            ),

            vol.Required(SECTION_HOLD_BEHAVIOR): section(
                vol.Schema(
                    {
                        base_hold_key: optional_int(min_value=1),

                        optional_key(CONF_MIN_BASE_HOLD_SECONDS): optional_int(
                            min_value=1,
                        ),

                        optional_key(CONF_HOLD_FACTOR): optional_float(min_value=1.0),

                        optional_key(CONF_MAX_HOLD_SECONDS): optional_int(min_value=1),

                        optional_key(CONF_MIN_ON_SECONDS): optional_int(min_value=0),
                    }
                ),
                {"collapsed": True},
            ),
        }
    )


def _flatten_sections(user_input: dict[str, Any]) -> dict[str, Any]:
    """Return submitted form values as flat config-entry data."""
    flattened = dict(user_input)

    for section_key in (SECTION_FLAP_DETECTION, SECTION_HOLD_BEHAVIOR):
        section_input = flattened.pop(section_key, {})
        if isinstance(section_input, dict):
            flattened.update(section_input)

    return flattened


def _input_entity_name(hass: HomeAssistant, entity_id: str) -> str:
    """Return a friendly name for an input entity."""
    state = hass.states.get(entity_id)

    if state is not None:
        friendly_name = state.attributes.get(ATTR_FRIENDLY_NAME)
        if isinstance(friendly_name, str) and friendly_name.strip():
            return friendly_name.strip()

    object_id = entity_id.split(".", 1)[-1]
    return object_id.replace("_", " ").strip() or entity_id


def _antiflap_name(name: str) -> str:
    """Return an Antiflap entity name based on an input entity name."""
    if name.lower().endswith(" antiflap"):
        return name

    return f"{name} Antiflap"


def _prepare_input(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    """Validate submitted form values and return config-flow errors.

    The keys in this returned dictionary correspond to field names. The values
    correspond to error IDs defined in translations/en.json.
    """
    errors: dict[str, str] = {}

    entity_id = user_input[CONF_INPUT_ENTITY]
    try:
        cv.entity_id(entity_id)
    except vol.Invalid:
        errors[CONF_INPUT_ENTITY] = "invalid_input_entity"
    else:
        user_input[CONF_DEVICE_ID] = async_entity_id_to_device_id(hass, entity_id)

    active_state = user_input.get(CONF_ACTIVE_STATE)
    if isinstance(active_state, str) and active_state.strip():
        user_input[CONF_ACTIVE_STATE] = active_state.strip()
    else:
        user_input[CONF_ACTIVE_STATE] = ""

    flap_gap_seconds = _override_or_default(
        user_input,
        CONF_FLAP_GAP_SECONDS,
        DEFAULT_FLAP_GAP_SECONDS,
    )
    min_base_hold_seconds = _override_or_default(
        user_input,
        CONF_MIN_BASE_HOLD_SECONDS,
        DEFAULT_MIN_BASE_HOLD_SECONDS,
    )
    base = user_input.get(CONF_BASE_HOLD_SECONDS)
    if base in (None, ""):
        base = default_base_hold_seconds_with_floor(
            flap_gap_seconds,
            min_base_hold_seconds,
        )

    max_hold = _override_or_default(
        user_input,
        CONF_MAX_HOLD_SECONDS,
        DEFAULT_MAX_HOLD_SECONDS,
    )

    if max_hold < base:
        errors[CONF_MAX_HOLD_SECONDS] = "max_less_than_base"

    if not errors:
        user_input[CONF_NAME] = _antiflap_name(
            _input_entity_name(hass, entity_id))

    return errors


def _override_or_default(
    user_input: dict[str, Any],
    key: str,
    default: int | float | None,
) -> int | float | None:
    """Return a submitted override, or the runtime default for blank values."""
    value = user_input.get(key)

    if value in (None, ""):
        return default

    return value


def _remove_blank_derived_values(user_input: dict[str, Any]) -> None:
    """Remove blank override values so the entity uses runtime defaults."""
    for key in DEFAULTED_CONFIG_KEYS:
        if key in user_input and user_input[key] in (None, ""):
            user_input.pop(key)


class AntiflapConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Antiflap."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the initial setup step from the UI."""
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input = _flatten_sections(user_input)
            errors = _prepare_input(self.hass, user_input)

            if not errors:
                _remove_blank_derived_values(user_input)

                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={},
                    options=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(user_input),
            errors=errors,
            last_step=True,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> AntiflapOptionsFlow:
        """Return the options flow handler for editing an existing entry."""
        return AntiflapOptionsFlow(config_entry)


class AntiflapOptionsFlow(config_entries.OptionsFlow):
    """Handle editing an existing Antiflap config entry."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize the options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Show and handle the options form."""
        current = _current_data(self._config_entry)
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input = _flatten_sections(user_input)
            errors = _prepare_input(self.hass, user_input)

            if not errors:
                _remove_blank_derived_values(user_input)

                return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(
                current if user_input is None else user_input),
            errors=errors,
            last_step=True,
        )

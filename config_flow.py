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

from math import ceil
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import ATTR_FRIENDLY_NAME, CONF_NAME, CONF_UNIQUE_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    CONF_BASE_HOLD_SECONDS,
    CONF_ACTIVE_STATE,
    CONF_FREE_FLAPS,
    CONF_HOLD_FACTOR,
    CONF_INPUT_ENTITY,
    CONF_MAX_HOLD_SECONDS,
    CONF_MIN_ON_SECONDS,
    CONF_FLAP_GAP_SECONDS,
    CONF_WINDOW_SECONDS,
    DEFAULT_ACTIVE_STATE,
    DEFAULT_FREE_FLAPS,
    DEFAULT_HOLD_FACTOR,
    DEFAULT_MAX_HOLD_SECONDS,
    DEFAULT_MIN_ON_SECONDS,
    DEFAULT_FLAP_GAP_SECONDS,
    DEFAULT_WINDOW_MULTIPLIER,
    DOMAIN,
)


def _default_window_seconds(max_hold_seconds: int) -> int:
    """Return the default memory window for a given maximum hold.

    We default the memory window to 1.5x the maximum hold. That means if the
    maximum hold time is 15 minutes, Antiflap remembers flaps for 22.5
    minutes unless the user overrides it.
    """
    return ceil(max_hold_seconds * DEFAULT_WINDOW_MULTIPLIER)


def _default_base_hold_seconds(flap_gap_seconds: int) -> int:
    """Return the default base hold as half the flap-gap duration."""
    return max(ceil(flap_gap_seconds / 2), 1)


def _current_data(config_entry: config_entries.ConfigEntry) -> dict[str, Any]:
    """Return config-entry data with any options overlaid.

    Home Assistant stores the original setup values in config_entry.data.
    Later edits from the Options UI are stored in config_entry.options.

    By merging them, the options form can show the current effective settings.
    """
    return {**config_entry.data, **config_entry.options}


def _build_schema(current: dict[str, Any] | None = None) -> vol.Schema:
    """Build the setup/options form schema.

    Home Assistant uses voluptuous schemas to decide what fields to show in the
    UI and how to validate submitted values.
    """
    current = current or {}

    # window_seconds is intentionally optional. If the user leaves it blank, the
    # entity derives it from max_hold_seconds at runtime.
    window_key = vol.Optional(CONF_WINDOW_SECONDS)
    if CONF_WINDOW_SECONDS in current:
        window_key = vol.Optional(
            CONF_WINDOW_SECONDS, default=current[CONF_WINDOW_SECONDS])

    # base_hold_seconds is intentionally optional. If the user leaves it blank,
    # the entity derives it from flap_gap_seconds at runtime.
    base_hold_key = vol.Optional(CONF_BASE_HOLD_SECONDS)
    if CONF_BASE_HOLD_SECONDS in current:
        base_hold_key = vol.Optional(
            CONF_BASE_HOLD_SECONDS, default=current[CONF_BASE_HOLD_SECONDS])

    input_entity_key = vol.Required(CONF_INPUT_ENTITY)
    if CONF_INPUT_ENTITY in current:
        input_entity_key = vol.Required(
            CONF_INPUT_ENTITY, default=current[CONF_INPUT_ENTITY])

    flap_gap_seconds = current.get(
        CONF_FLAP_GAP_SECONDS,
        DEFAULT_FLAP_GAP_SECONDS,
    )

    return vol.Schema(
        {
            input_entity_key: selector.EntitySelector(),

            vol.Required(
                CONF_ACTIVE_STATE,
                default=current.get(CONF_ACTIVE_STATE, DEFAULT_ACTIVE_STATE),
            ): selector.TextSelector(),

            vol.Required(
                CONF_FREE_FLAPS,
                default=current.get(CONF_FREE_FLAPS, DEFAULT_FREE_FLAPS),
            ): vol.All(vol.Coerce(int), vol.Range(min=0)),

            vol.Required(
                CONF_FLAP_GAP_SECONDS,
                default=flap_gap_seconds,
            ): vol.All(vol.Coerce(int), vol.Range(min=1)),

            base_hold_key: vol.All(vol.Coerce(int), vol.Range(min=1)),

            vol.Required(
                CONF_HOLD_FACTOR,
                default=current.get(CONF_HOLD_FACTOR, DEFAULT_HOLD_FACTOR),
            ): vol.All(vol.Coerce(float), vol.Range(min=1.0)),

            vol.Required(
                CONF_MAX_HOLD_SECONDS,
                default=current.get(CONF_MAX_HOLD_SECONDS,
                                    DEFAULT_MAX_HOLD_SECONDS),
            ): vol.All(vol.Coerce(int), vol.Range(min=1)),

            vol.Required(
                CONF_MIN_ON_SECONDS,
                default=current.get(CONF_MIN_ON_SECONDS,
                                    DEFAULT_MIN_ON_SECONDS),
            ): vol.All(vol.Coerce(int), vol.Range(min=0)),

            window_key: vol.All(vol.Coerce(int), vol.Range(min=1)),
        }
    )


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

    active_state = user_input.get(CONF_ACTIVE_STATE, DEFAULT_ACTIVE_STATE)
    if not isinstance(active_state, str) or not active_state.strip():
        errors[CONF_ACTIVE_STATE] = "active_state_required"
    else:
        user_input[CONF_ACTIVE_STATE] = active_state.strip()

    flap_gap_seconds = user_input[CONF_FLAP_GAP_SECONDS]
    base = user_input.get(CONF_BASE_HOLD_SECONDS)
    if base is None:
        base = _default_base_hold_seconds(flap_gap_seconds)

    max_hold = user_input[CONF_MAX_HOLD_SECONDS]

    if max_hold < base:
        errors[CONF_MAX_HOLD_SECONDS] = "max_less_than_base"

    entity_id = user_input[CONF_INPUT_ENTITY]
    try:
        cv.entity_id(entity_id)
    except vol.Invalid:
        errors[CONF_INPUT_ENTITY] = "invalid_input_entity"

    if not errors:
        user_input[CONF_NAME] = _antiflap_name(_input_entity_name(hass, entity_id))

    return errors


def _remove_derived_defaults(user_input: dict[str, Any]) -> None:
    """Remove blank values that the entity can derive at runtime."""
    for key in (CONF_BASE_HOLD_SECONDS, CONF_WINDOW_SECONDS):
        if key in user_input and user_input[key] is None:
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
            errors = _prepare_input(self.hass, user_input)

            if not errors:
                _remove_derived_defaults(user_input)

                # This unique ID prevents accidentally creating the exact same
                # helper twice for one input entity.
                unique_id = slugify(f"{DOMAIN}_{user_input[CONF_INPUT_ENTITY]}")
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                user_input[CONF_UNIQUE_ID] = unique_id

                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(user_input),
            errors=errors,
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
            errors = _prepare_input(self.hass, user_input)

            if not errors:
                _remove_derived_defaults(user_input)

                return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(
                current if user_input is None else user_input),
            errors=errors,
        )

"""Helpers for resolving the active state for an input entity."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import DEFAULT_ACTIVE_STATE

DEFAULT_ACTIVE_STATE_BY_DOMAIN = {
    "alarm_control_panel": "triggered",
    "automation": "on",
    "binary_sensor": "on",
    "button": "pressed",
    "cover": "open",
    "device_tracker": "home",
    "fan": "on",
    "humidifier": "on",
    "input_boolean": "on",
    "light": "on",
    "lock": "unlocked",
    "media_player": "playing",
    "person": "home",
    "remote": "on",
    "script": "on",
    "siren": "on",
    "switch": "on",
    "timer": "active",
    "update": "on",
    "vacuum": "cleaning",
}


def default_active_state(hass: HomeAssistant, entity_id: str) -> str:
    """Return a reasonable active state for the selected entity."""
    state = hass.states.get(entity_id)
    domain = state.domain if state is not None else entity_id.split(".", 1)[0]

    return DEFAULT_ACTIVE_STATE_BY_DOMAIN.get(domain, DEFAULT_ACTIVE_STATE)

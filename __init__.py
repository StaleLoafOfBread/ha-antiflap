"""Antiflap integration.

This file is the integration entry point.

A Home Assistant integration can expose one or more platforms. This integration
only exposes one platform: a binary_sensor. The config flow creates a config
entry, and Home Assistant calls async_setup_entry() when that entry should be
loaded.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device import (
    async_remove_stale_devices_links_keep_current_device,
)

from .const import DOMAIN

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Antiflap from a config entry.

    A config entry is the saved configuration created by the UI flow. We do not
    create the binary sensor directly here. Instead, we forward setup to the
    binary_sensor platform, which lives in binary_sensor.py.
    """
    # Store any future shared integration data here if we need it.
    hass.data.setdefault(DOMAIN, {})

    async_remove_stale_devices_links_keep_current_device(
        hass,
        entry.entry_id,
        entry.options.get(CONF_DEVICE_ID, entry.data.get(CONF_DEVICE_ID)),
    )

    # If the user changes options later, reload this entry so the entity uses
    # the new values.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an Antiflap config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options are changed from the UI."""
    await hass.config_entries.async_reload(entry.entry_id)

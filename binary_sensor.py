"""Binary sensor platform for Antiflap.

This file creates the actual entity exposed to Home Assistant.

The output entity is a binary sensor. It answers one question:

    Should the request condition be treated as active right now?

For an office motion example:

    input_entity: binary_sensor.office_motion

The binary sensor is on when either:

    1. input_entity is currently on, or
    2. input_entity recently turned off and we are inside a calculated hold
       window, or
    3. the minimum-on timer has not expired yet.

The hold window is based only on short inactive/request gaps. There is no
separate tracked entity state; the input entity is the single source of truth for
whether the request is currently active.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
import logging
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    CONF_NAME,
    CONF_UNIQUE_ID,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.device import async_device_info_to_link_from_entity
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import (
    ATTR_BASE_HOLD_SECONDS,
    ATTR_ACTIVE_STATE,
    ATTR_ACTIVE_STARTED_AT,
    ATTR_FREE_FLAPS,
    ATTR_HOLD_FACTOR,
    ATTR_HOLD_SECONDS,
    ATTR_HOLD_UNTIL,
    ATTR_INACTIVE_STARTED_AT,
    ATTR_MAX_HOLD_SECONDS,
    ATTR_MIN_ON_SECONDS,
    ATTR_MIN_ON_UNTIL,
    ATTR_INPUT_ENTITY,
    ATTR_INPUT_STATE,
    ATTR_FLAP_GAP_SECONDS,
    ATTR_FLAP_TIMESTAMPS,
    ATTR_TOTAL_FLAPS,
    ATTR_WINDOW_SECONDS,
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
    DEFAULT_MIN_ON_SECONDS,
    DEFAULT_WINDOW_MULTIPLIER,
    DOMAIN,
    SERVICE_RESET,
)

_LOGGER = logging.getLogger(__name__)
_DEFAULT_REGISTRY_ASSIGNMENT_DELAY_SECONDS = 1
_DEFAULT_REGISTRY_ASSIGNMENT_MAX_ATTEMPTS = 60


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one Antiflap binary sensor from a config entry.

    Home Assistant calls this because __init__.py forwards config-entry setup to
    the binary_sensor platform.
    """
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(SERVICE_RESET, {}, "async_reset")

    async_add_entities([AntiflapBinarySensor(hass, entry)])


class AntiflapBinarySensor(BinarySensorEntity, RestoreEntity):
    """Binary sensor that applies adaptive hold behavior to an entity state.

    BinarySensorEntity:
        Makes this object appear as a binary_sensor in Home Assistant.

    RestoreEntity:
        Lets this object restore its previous attributes after Home Assistant
        restarts. We use that to restore recent flap timestamps and an
        unexpired hold window.
    """

    # This entity updates itself from callbacks. Home Assistant does not need to
    # poll it periodically.
    _attr_should_poll = False

    # This integration is a helper where the user-provided name is already the
    # complete entity name. If Home Assistant composes entity names from the
    # config entry title, IDs become duplicated like "q_antiflap_q".
    _attr_has_entity_name = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the entity object from a config entry.

        The constructor should only store values. It should not register event
        listeners yet; that happens in async_added_to_hass().
        """
        data = _entry_data(entry)

        self._entry = entry
        self._attr_name = _antiflap_name(data[CONF_NAME])

        # Use the config entry unique_id if available. If not, fall back to the
        # entry ID. Entity unique IDs are what let Home Assistant remember user
        # customizations like renamed entity IDs.
        self._attr_unique_id = data.get(CONF_UNIQUE_ID) or entry.unique_id or entry.entry_id

        self._input_entity_id: str = data[CONF_INPUT_ENTITY]
        self._attr_device_info = async_device_info_to_link_from_entity(
            hass,
            self._input_entity_id,
        )

        self._active_state: str = data.get(CONF_ACTIVE_STATE, DEFAULT_ACTIVE_STATE)

        self._free_flaps: int = data[CONF_FREE_FLAPS]
        self._flap_gap_seconds: int = data[CONF_FLAP_GAP_SECONDS]
        base_hold_seconds = data.get(CONF_BASE_HOLD_SECONDS)
        if base_hold_seconds is None:
            base_hold_seconds = _default_base_hold_seconds(self._flap_gap_seconds)
        self._base_hold_seconds: int = base_hold_seconds
        self._hold_factor: float = data[CONF_HOLD_FACTOR]
        self._max_hold_seconds: int = data[CONF_MAX_HOLD_SECONDS]
        self._min_on_seconds: int = data.get(
            CONF_MIN_ON_SECONDS,
            DEFAULT_MIN_ON_SECONDS,
        )
        self._window_seconds: int = data.get(
            CONF_WINDOW_SECONDS,
            _default_window_seconds(self._max_hold_seconds),
        )

        # Runtime state -------------------------------------------------------

        # Cached current boolean value of the input entity.
        self._input_state = False

        # When input_entity became true. If the input is currently false, this
        # is None.
        self._active_started_at: datetime | None = None

        # When input_entity became false. If the input is currently true, this
        # is None.
        self._inactive_started_at: datetime | None = None

        # Each timestamp represents the END of one inactive/request gap that
        # was short enough to count as a flap.
        # Example:
        #   active false at 12:00:00
        #   active true at 12:00:10
        #   flap_gap_seconds = 60
        #   -> store timestamp 12:00:10
        self._flap_timestamps: list[datetime] = []

        # If set, the binary sensor remains on until this UTC timestamp unless
        # input_entity turns on again. It is stored directly so restart
        # restore can keep an in-progress hold window.
        self._hold_until: datetime | None = None

        # The most recently calculated hold length. This is a debug value; the
        # actual hold timer uses _hold_until.
        self._hold_seconds = 0

        # If set, the binary sensor remains on until this UTC timestamp. This
        # is independent from adaptive hold; it starts whenever the output
        # changes from off to on.
        self._min_on_until: datetime | None = None

        # Cancel function for our scheduled update callback.
        # We use this to wake the entity when a hold expires, a minimum-on
        # timer expires, or an old flap timestamp falls out of the
        # rolling window.
        self._cancel_update_timer = None
        self._cancel_default_registry_assignment = None
        self._default_registry_assignment_attempts = 0

    @property
    def suggested_object_id(self) -> str:
        """Return the preferred entity registry object ID.

        Home Assistant can still let the user rename the entity later, but this
        gives new helpers a predictable default like:

            binary_sensor.office_light_antiflap
        """
        return slugify(self._attr_name)

    async def async_added_to_hass(self) -> None:
        """Finish setup after Home Assistant has added the entity.

        This is where it is safe to interact with Home Assistant:

            - set default registry metadata from the input entity
            - restore old attributes
            - read the current input entity state
            - register state listeners
            - schedule timers
        """
        if not self._assign_default_registry_metadata():
            self._schedule_default_registry_assignment()

        await self._restore_previous_runtime_state()

        # Initialize active state without counting a flap. We only count a
        # flap when active changes from false to true after setup.
        self._input_state = self._read_input_state()

        if self._input_state:
            if self._active_started_at is None:
                self._active_started_at = _utcnow()
            self._inactive_started_at = None
            if self._min_on_until is None:
                self._start_min_on_if_needed(self._active_started_at)
        else:
            self._active_started_at = None
            if self._inactive_started_at is None:
                self._inactive_started_at = _utcnow()

        self._clear_expired_hold()
        self._clear_expired_min_on()
        self._purge_old_flap_timestamps()

        state_tracker = async_track_state_change_event(
            self.hass,
            [self._input_entity_id],
            self._handle_input_entity_change,
        )
        self.async_on_remove(state_tracker)

        registry_tracker = self.hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            self._handle_entity_registry_update,
        )
        self.async_on_remove(registry_tracker)

        self._schedule_next_update()

    @property
    def is_on(self) -> bool:
        """Return true when the output binary sensor should be on.

        This is the main output of the integration:

            active is true
                -> on

            active is false, but an unexpired hold window exists
                -> on

            otherwise
                -> off
        """
        return (
            self._input_state
            or self.remaining_hold_seconds > 0
            or self.remaining_min_on_seconds > 0
        )

    @property
    def device_class(self) -> str | None:
        """Return the current device class from the input entity."""
        state = self.hass.states.get(self._input_entity_id)

        if state is None:
            return None

        device_class = state.attributes.get(ATTR_DEVICE_CLASS)

        if not isinstance(device_class, str):
            return None

        return device_class

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return attributes visible in Developer Tools -> States.

        These are intentionally verbose so you can debug why the binary sensor is
        on or off without reading logs.
        """
        recent_flaps = self._recent_flap_timestamps()
        total_flaps = len(recent_flaps)

        return {
            ATTR_BASE_HOLD_SECONDS: self._base_hold_seconds,
            ATTR_FREE_FLAPS: self._free_flaps,
            ATTR_HOLD_FACTOR: self._hold_factor,
            ATTR_HOLD_SECONDS: self._hold_seconds,
            ATTR_HOLD_UNTIL: _datetime_to_iso(self._hold_until),
            ATTR_MAX_HOLD_SECONDS: self._max_hold_seconds,
            ATTR_MIN_ON_SECONDS: self._min_on_seconds,
            ATTR_MIN_ON_UNTIL: _datetime_to_iso(self._min_on_until),
            ATTR_ACTIVE_STARTED_AT: _datetime_to_iso(self._active_started_at),
            ATTR_INACTIVE_STARTED_AT: _datetime_to_iso(self._inactive_started_at),
            ATTR_FLAP_GAP_SECONDS: self._flap_gap_seconds,

            # These are stored as ISO strings so RestoreEntity can recover them
            # after restart. Home Assistant attributes must be JSON-friendly.
            ATTR_FLAP_TIMESTAMPS: [
                timestamp.isoformat() for timestamp in recent_flaps
            ],
            ATTR_ENTITY_ID: self._entity_ids_attribute(),
            ATTR_INPUT_ENTITY: self._input_entity_id,
            ATTR_ACTIVE_STATE: self._active_state,
            ATTR_INPUT_STATE: self._input_state,
            ATTR_TOTAL_FLAPS: total_flaps,
            ATTR_WINDOW_SECONDS: self._window_seconds,
        }

    @property
    def remaining_hold_seconds(self) -> int:
        """Return seconds left in the current hold window.

        This is 0 when:

            - no hold window is active
            - the current hold window already expired
        """
        if self._hold_until is None:
            return 0

        remaining = ceil((self._hold_until - _utcnow()).total_seconds())
        return max(remaining, 0)

    @property
    def remaining_min_on_seconds(self) -> int:
        """Return seconds left in the current minimum-on window."""
        if self._min_on_until is None:
            return 0

        remaining = ceil((self._min_on_until - _utcnow()).total_seconds())
        return max(remaining, 0)

    async def async_reset(self) -> None:
        """Clear runtime flap memory and any active hold."""
        self._flap_timestamps = []
        self._hold_until = None
        self._hold_seconds = 0

        self._input_state = self._read_input_state()
        now = _utcnow()
        self._active_started_at = now if self._input_state else None
        self._inactive_started_at = None if self._input_state else now
        if self._input_state:
            self._start_min_on_if_needed(now)
        else:
            self._clear_min_on()

        self._schedule_next_update()
        self.async_write_ha_state()

    @callback
    def _schedule_default_registry_assignment(self) -> None:
        """Schedule registry metadata assignment after entity registry creation."""
        if self._cancel_default_registry_assignment is not None:
            self._cancel_default_registry_assignment()
            self._cancel_default_registry_assignment = None

        self._cancel_default_registry_assignment = async_call_later(
            self.hass,
            _DEFAULT_REGISTRY_ASSIGNMENT_DELAY_SECONDS,
            self._handle_default_registry_assignment,
        )
        self.async_on_remove(self._cancel_default_registry_assignment)

    @callback
    def _handle_default_registry_assignment(self, now: datetime) -> None:
        """Handle delayed registry metadata assignment."""
        self._cancel_default_registry_assignment = None
        assignment_finished = self._assign_default_registry_metadata()

        if assignment_finished:
            return

        self._default_registry_assignment_attempts += 1

        if (
            self._default_registry_assignment_attempts
            < _DEFAULT_REGISTRY_ASSIGNMENT_MAX_ATTEMPTS
        ):
            self._schedule_default_registry_assignment()

    @callback
    def _handle_entity_registry_update(self, event: Event) -> None:
        """Refresh registry metadata when the input entity registry entry changes."""
        entity_id = event.data.get("entity_id")

        if entity_id not in (self._input_entity_id, self.entity_id):
            return

        self._default_registry_assignment_attempts = 0

        if not self._assign_default_registry_metadata():
            self._schedule_default_registry_assignment()

    @callback
    def _assign_default_registry_metadata(self) -> bool:
        """Assign registry metadata from the input entity."""
        entity_id = self.entity_id

        if entity_id is None:
            return False

        entity_registry = er.async_get(self.hass)
        input_entity_entry = entity_registry.async_get(self._input_entity_id)

        if input_entity_entry is None:
            _LOGGER.debug(
                "Input entity %s has no entity registry entry yet; cannot assign "
                "registry metadata for %s",
                self._input_entity_id,
                entity_id,
            )
            return False

        entity_entry = entity_registry.async_get(entity_id)

        if entity_entry is None:
            _LOGGER.debug(
                "Antiflap entity %s has no entity registry entry yet; cannot assign "
                "registry metadata from %s",
                entity_id,
                self._input_entity_id,
            )
            return False

        input_device_id = input_entity_entry.device_id
        input_entity_category = input_entity_entry.entity_category

        # area_id on the entity registry entry is an explicit entity area
        # assignment. Do not copy area inherited from the input entity's device,
        # because this helper should only attach metadata to its own entity.
        input_area_id = input_entity_entry.area_id

        if (
            input_device_id is None
            and input_area_id is None
            and entity_entry.entity_category == input_entity_category
        ):
            _LOGGER.debug(
                "Input entity %s has no device, explicit area, or category metadata "
                "to copy to %s",
                self._input_entity_id,
                entity_id,
            )
            return False

        updates: dict[str, Any] = {}

        if input_device_id is not None and entity_entry.device_id != input_device_id:
            updates["device_id"] = input_device_id

        if input_area_id is not None and entity_entry.area_id != input_area_id:
            updates["area_id"] = input_area_id

        if entity_entry.entity_category != input_entity_category:
            updates["entity_category"] = input_entity_category

        if updates:
            _LOGGER.debug(
                "Assigning registry metadata to %s from %s: %s",
                entity_id,
                self._input_entity_id,
                updates,
            )
            self.registry_entry = entity_registry.async_update_entity(
                entity_id,
                **updates,
            )

        return True

    async def _restore_previous_runtime_state(self) -> None:
        """Restore runtime memory from previous entity attributes.

        RestoreEntity gives us the last state this entity had before Home
        Assistant restarted. We only restore values that represent actual memory:

            - flap timestamps
            - active_started_at, if input was active before restart
            - inactive_started_at, if input was inactive before restart
            - hold_until, if an old hold window may still be active
            - min_on_until, if an old minimum-on timer may still be active

        Everything else is recalculated from the current input entity and settings.
        """
        last_state = await self.async_get_last_state()

        if last_state is None:
            return

        self._flap_timestamps = _parse_datetime_list(
            last_state.attributes.get(ATTR_FLAP_TIMESTAMPS, [])
        )

        restored_active_started_at = last_state.attributes.get(ATTR_ACTIVE_STARTED_AT)
        if isinstance(restored_active_started_at, str):
            parsed = dt_util.parse_datetime(restored_active_started_at)
            if parsed is not None:
                self._active_started_at = _ensure_utc(parsed)

        restored_inactive_started_at = last_state.attributes.get(ATTR_INACTIVE_STARTED_AT)
        if isinstance(restored_inactive_started_at, str):
            parsed = dt_util.parse_datetime(restored_inactive_started_at)
            if parsed is not None:
                self._inactive_started_at = _ensure_utc(parsed)

        restored_hold_until = last_state.attributes.get(ATTR_HOLD_UNTIL)
        if isinstance(restored_hold_until, str):
            parsed = dt_util.parse_datetime(restored_hold_until)
            if parsed is not None:
                self._hold_until = _ensure_utc(parsed)

        restored_hold_seconds = last_state.attributes.get(ATTR_HOLD_SECONDS)
        if isinstance(restored_hold_seconds, int):
            self._hold_seconds = restored_hold_seconds

        restored_min_on_until = last_state.attributes.get(ATTR_MIN_ON_UNTIL)
        if isinstance(restored_min_on_until, str):
            parsed = dt_util.parse_datetime(restored_min_on_until)
            if parsed is not None:
                self._min_on_until = _ensure_utc(parsed)

    def _read_input_state(self) -> bool:
        """Read input_entity and convert its current state to bool."""
        state = self.hass.states.get(self._input_entity_id)
        if state is None:
            _LOGGER.warning(
                "Input entity %s for %s was not found",
                self._input_entity_id,
                self.name,
            )
            return False

        return state.state == self._active_state

    def _entity_ids_attribute(self) -> list[str]:
        """Return source entity IDs for Home Assistant group-style display."""
        entity_ids = [self._input_entity_id]
        state = self.hass.states.get(self._input_entity_id)

        if state is None:
            return entity_ids

        source_entity_ids = state.attributes.get(ATTR_ENTITY_ID)

        if isinstance(source_entity_ids, str):
            entity_ids.append(source_entity_ids)
        elif isinstance(source_entity_ids, list | tuple | set):
            entity_ids.extend(
                entity_id
                for entity_id in source_entity_ids
                if isinstance(entity_id, str)
            )

        return list(dict.fromkeys(entity_ids))

    @callback
    def _handle_input_entity_change(self, *args: Any) -> None:
        """Handle changes in the input entity state."""
        new_input_state = self._read_input_state()
        self._update_active_state(new_input_state)
        self._recalculate_and_write_state()

    @callback
    def _update_active_state(self, new_input_state: bool) -> None:
        """Update cached active state and count flaps.

        This is the heart of the hold logic.

        We count a flap when active goes false -> true quickly. The
        number of flaps that actually cause hold behavior is then:

            flap_count = max(flap_timestamp_count - free_flaps, 0)
        """
        now = _utcnow()
        was_on = self.is_on

        if new_input_state == self._input_state:
            return

        # Active changed false -> true. The inactive gap ended; decide whether
        # it was within the flap gap and should count toward adaptive hold.
        if new_input_state:
            if self._inactive_started_at is not None:
                duration_seconds = (now - self._inactive_started_at).total_seconds()

                if duration_seconds <= self._flap_gap_seconds:
                    self._flap_timestamps.append(now)

            self._active_started_at = now
            self._inactive_started_at = None
            self._input_state = True
            self._clear_expired_hold(now)
            if not was_on:
                self._start_min_on_if_needed(now)
            return

        # Active changed true -> false. Start a new inactive gap and calculate
        # whether recent flap gaps should keep the output on.
        self._active_started_at = None
        self._inactive_started_at = now
        self._input_state = False
        self._start_hold_if_needed(now)
        if not was_on and self.is_on:
            self._start_min_on_if_needed(now)

    @callback
    def _handle_scheduled_update(self, now: datetime) -> None:
        """Handle a scheduled update.

        We schedule updates for three reasons:

            - the current hold window expires
            - the current minimum-on timer expires
            - an old flap timestamp falls out of the rolling window

        If hold expires while input_entity is still off, this callback is
        what turns the binary sensor off. If input_entity is on, is_on stays
        true because the active request takes priority over the timer.
        """
        self._clear_expired_hold()
        self._clear_expired_min_on()
        self._recalculate_and_write_state()

    @callback
    def _recalculate_and_write_state(self) -> None:
        """Clean old data, reschedule timers, and tell HA state changed."""
        self._clear_expired_hold()
        self._clear_expired_min_on()
        self._purge_old_flap_timestamps()
        self._schedule_next_update()
        self.async_write_ha_state()

    @callback
    def _schedule_next_update(self) -> None:
        """Schedule the next time this entity should update itself.

        Without this, the entity might stay on until some unrelated state change
        happens after the hold expires.
        """
        if self._cancel_update_timer is not None:
            self._cancel_update_timer()
            self._cancel_update_timer = None

        delays: list[int] = []

        remaining_hold = self.remaining_hold_seconds
        if remaining_hold > 0:
            delays.append(remaining_hold)

        remaining_min_on = self.remaining_min_on_seconds
        if remaining_min_on > 0:
            delays.append(remaining_min_on)

        seconds_until_window_expiry = self._seconds_until_next_flap_expires()
        if seconds_until_window_expiry is not None:
            delays.append(seconds_until_window_expiry)

        if not delays:
            return

        delay = max(min(delays), 1)
        self._cancel_update_timer = async_call_later(
            self.hass,
            delay,
            self._handle_scheduled_update,
        )
        self.async_on_remove(self._cancel_update_timer)

    def _start_hold_if_needed(self, now: datetime) -> None:
        """Calculate and store a new hold window after active turns false."""
        self._purge_old_flap_timestamps()

        flap_timestamp_count = len(self._recent_flap_timestamps())
        flap_count = self._flap_count_from_flap_timestamp_count(flap_timestamp_count)
        self._hold_seconds = self._hold_seconds_from_flap_count(flap_count)

        if self._hold_seconds > 0:
            self._hold_until = now + timedelta(seconds=self._hold_seconds)
        else:
            self._hold_until = None

    def _recent_flap_timestamps(self) -> list[datetime]:
        """Return flap timestamps still inside the rolling window."""
        cutoff = _utcnow() - timedelta(seconds=self._window_seconds)
        return [
            timestamp for timestamp in self._flap_timestamps if timestamp >= cutoff
        ]

    def _purge_old_flap_timestamps(self) -> None:
        """Drop flap timestamps outside the rolling window."""
        self._flap_timestamps = self._recent_flap_timestamps()

    def _seconds_until_next_flap_expires(self) -> int | None:
        """Return seconds until the oldest flap timestamp expires."""
        recent = self._recent_flap_timestamps()

        if not recent:
            return None

        oldest = recent[0]
        expiry = oldest + timedelta(seconds=self._window_seconds)
        seconds = ceil((expiry - _utcnow()).total_seconds())

        if seconds <= 0:
            return 1

        return seconds

    def _flap_count_from_flap_timestamp_count(self, flap_timestamp_count: int) -> int:
        """Return counted flaps after subtracting free_flaps.

        Example with free_flaps = 1:

            flap_timestamp_count = 1 -> flap_count = 0
            flap_timestamp_count = 2 -> flap_count = 1
            flap_timestamp_count = 3 -> flap_count = 2
        """
        return max(flap_timestamp_count - self._free_flaps, 0)

    def _hold_seconds_from_flap_count(self, flap_count: int) -> int:
        """Return total hold seconds for the current flap count.

        Example with:
            base_hold_seconds = 120
            hold_factor = 1.4
            max_hold_seconds = 900

        The sequence is approximately:
            0, 120, 168, 236, 330, 461, 646, 900 capped
        """
        if flap_count <= 0:
            return 0

        calculated_seconds = self._base_hold_seconds * (
            self._hold_factor ** (flap_count - 1)
        )

        # ceil() keeps fractional factors like 1.4 from producing fractional
        # seconds and avoids silently rounding down.
        return min(ceil(calculated_seconds), self._max_hold_seconds)

    def _clear_expired_hold(self, now: datetime | None = None) -> None:
        """Remove hold state once its timestamp is no longer in the future."""
        if self._hold_until is None:
            return

        now = now or _utcnow()
        if self._hold_until <= now:
            self._hold_until = None
            self._hold_seconds = 0

    def _start_min_on_if_needed(self, now: datetime) -> None:
        """Start the minimum-on timer if the feature is enabled."""
        if self._min_on_seconds <= 0:
            self._min_on_until = None
            return

        self._min_on_until = now + timedelta(seconds=self._min_on_seconds)

    def _clear_min_on(self) -> None:
        """Clear minimum-on runtime state."""
        self._min_on_until = None

    def _clear_expired_min_on(self, now: datetime | None = None) -> None:
        """Remove minimum-on state once its timestamp is no longer in the future."""
        if self._min_on_until is None:
            return

        now = now or _utcnow()
        if self._min_on_until <= now:
            self._min_on_until = None


def _entry_data(entry: ConfigEntry) -> dict[str, Any]:
    """Return config-entry data with options overlaid."""
    return {**entry.data, **entry.options}


def _antiflap_name(name: str) -> str:
    """Return an Antiflap entity name based on an input entity name."""
    if name.lower().endswith(" antiflap"):
        return name

    return f"{name} Antiflap"


def _default_window_seconds(max_hold_seconds: int) -> int:
    """Return default rolling window based on max hold."""
    return ceil(max_hold_seconds * DEFAULT_WINDOW_MULTIPLIER)


def _default_base_hold_seconds(flap_gap_seconds: int) -> int:
    """Return default base hold based on flap-gap duration."""
    return max(ceil(flap_gap_seconds / 2), 1)


def _parse_datetime_list(raw_values: Any) -> list[datetime]:
    """Parse a restored attribute list of ISO datetime strings."""
    if not isinstance(raw_values, list):
        return []

    parsed_values: list[datetime] = []

    for value in raw_values:
        if not isinstance(value, str):
            continue

        parsed = dt_util.parse_datetime(value)

        if parsed is None:
            continue

        parsed_values.append(_ensure_utc(parsed))

    return parsed_values


def _datetime_to_iso(value: datetime | None) -> str | None:
    """Return an ISO string for attributes, or None when no timestamp exists."""
    if value is None:
        return None

    return value.isoformat()


def _ensure_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def _utcnow() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)

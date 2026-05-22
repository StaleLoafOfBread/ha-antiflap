# Antiflap

Antiflap creates a Home Assistant binary sensor from an input entity and keeps
that sensor on for an adaptive hold period after repeated short inactive gaps.

The defaults are optimized for use with a motion sensor controlling whether or not a room's lights should be on or off.

## Installation

### HACS

1. Open HACS in Home Assistant.
2. Add `https://github.com/StaleLoafOfBread/ha-antiflap` as a custom repository
   with the category `Integration`.
3. Install Antiflap from HACS.
4. Restart Home Assistant.

## Example

```yaml
input_entity: binary_sensor.office_motion
active_state: "on"
sync_area: true
free_flaps: 0
flap_gap_seconds: 120
base_hold_seconds: 30
hold_factor: 1.4
max_hold_seconds: 600
min_on_seconds: 0
window_seconds: 540
```

The Antiflap sensor name is the input entity's friendly name plus `Antiflap`.
With `input_entity: binary_sensor.office_motion`, the suggested entity ID is:

```text
binary_sensor.office_motion_antiflap
```

## Behavior

The binary sensor is on while `input_entity` state matches `active_state`.
If `active_state` is left blank, Antiflap chooses a default based on the input
entity type.

`min_on_seconds` controls the minimum time the Antiflap binary sensor remains on
after it turns on. It defaults to `0`, which disables the minimum-on timer and
keeps the previous behavior.

The Antiflap binary sensor also exposes an `entity_id` attribute. It contains
the source `input_entity` plus any entity IDs from the source entity's own
`entity_id` attribute, which lets Home Assistant show group-style source info.

By default, Antiflap keeps its entity area in sync with the original entity.
Turn off `sync_area` if you want to assign the Antiflap entity to a different
area manually or leave it unset.

When `input_entity` changes from the active state to any other state, the
integration starts measuring an inactive gap. If `input_entity` returns to the
active state within `flap_gap_seconds`, it records a flap timestamp.
When `input_entity` next leaves the active state, Antiflap counts flaps
inside `window_seconds`, subtracts `free_flaps`, and calculates the hold:

```text
flap_count = max(flap_timestamp_count - free_flaps, 0)

if flap_count <= 0:
    hold_seconds = 0
else:
    hold_seconds = min(
        ceil(base_hold_seconds * (hold_factor ** (flap_count - 1))),
        max_hold_seconds,
    )
```

If `hold_seconds` is greater than zero, the binary sensor stays on until
`hold_until`. If `input_entity` becomes active again during that time, the sensor
remains on because the request is active.

`window_seconds` is optional in the UI. If omitted, Antiflap sizes it so the
hold curve can reach `max_hold_seconds` when flaps happen every
`flap_gap_seconds / 2`.

`hold_until` shows when the active hold will end.

## Configuration

### `input_entity`

*Required.*

The entity Antiflap watches.

### `active_state`

*Optional.* Leave blank to choose a default based on the input entity type.

This is the exact input entity state string that means the request is active.
For a normal binary sensor this is usually `on`. For another entity type, it
could be another state such as `heat`, `cool`, or `open`.
The current default mapping is in
[`DEFAULT_ACTIVE_STATE_BY_DOMAIN`](https://github.com/StaleLoafOfBread/ha-antiflap/blob/main/custom_components/antiflap/active_state.py).

### `sync_area`

*Optional.* Defaults to `true`.

When enabled, Antiflap keeps its entity area synchronized with the original
entity. When disabled, Antiflap does not change its entity area.

### `free_flaps`

*Optional.* Defaults to `0`.

This is how many recent flaps are ignored before Antiflap starts applying an
adaptive hold. With `free_flaps: 0`, the first flap inside the memory window can
create a hold. With `free_flaps: 1`, the first flap is ignored and the second
recent flap starts the hold sequence.

### `flap_gap_seconds`

*Optional.* Defaults to `120` seconds.

This is the maximum inactive gap that counts as a flap. Antiflap starts timing
when `input_entity` leaves `active_state`. If the input returns to
`active_state` within this many seconds, that inactive gap is recorded as one
flap.

For example, with `flap_gap_seconds: 60`, an input that turns inactive for 45
seconds and then active again records one flap. An input that stays inactive for
90 seconds does not record a flap.

### `base_hold_seconds`

*Optional.* Defaults to one quarter of `flap_gap_seconds` seconds, rounded up.

This is the first hold duration once the number of recent flaps exceeds
`free_flaps`. For example, if `base_hold_seconds` is `30`, the first counted
flap holds the output on for 30 seconds after the input becomes inactive.

### `hold_factor`

*Optional.* Defaults to `1.4`.

This multiplier grows the hold duration as more flaps happen inside the memory
window. A value of `1.0` keeps every counted hold at `base_hold_seconds`. Values
above `1.0` increase each later hold until `max_hold_seconds` is reached.

### `max_hold_seconds`

*Optional.* Defaults to `600` seconds.

This caps the adaptive hold duration. No calculated hold will be longer than
this value.

### `min_on_seconds`

*Optional.* Defaults to `0` seconds.

This is a separate minimum-on timer for the Antiflap output binary sensor. When
set above `0`, the output remains on for at least this many seconds after it
turns on, even if the input becomes inactive immediately. Set it to `0` to
disable the minimum-on timer.

### `window_seconds`

*Optional.* Defaults to a derived value based on the hold curve and
`flap_gap_seconds`.

This controls how long flap timestamps are remembered. Only flaps inside this
rolling window are counted when Antiflap calculates the next hold.

## Service

Call `antiflap.reset` to clear flap-gap history and any active hold for one or
more Antiflap entities.

```yaml
service: antiflap.reset
target:
  entity_id: binary_sensor.antiflap_office_light
```

## Translation notes

Runtime UI labels and descriptions for this custom integration live in
`custom_components/antiflap/translations/en.json`. `strings.json` is not
required for Home Assistant to load custom integration translations.

After changing translations, restart Home Assistant and hard-refresh the browser.
The frontend caches translations, so stale cached text can make fixed
translations appear broken.

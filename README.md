# Antiflap

Antiflap creates a Home Assistant binary sensor from an input entity and keeps
that sensor on for an adaptive hold period after repeated short inactive gaps.

The defaults are optimized for a motion-style signal that controls whether a
room's lights should be on.

## Installation

### HACS

1. Open HACS in Home Assistant.
2. Add `https://github.com/StaleLoafOfBread/ha-antiflap` as a custom repository
   with the category `Integration`.
3. Install Antiflap from HACS.
4. Restart Home Assistant.

## Minimum Settings

```yaml
input_entity: binary_sensor.office_motion
```

Leave every tuning field blank to use the integration defaults. For a binary
sensor input, the current defaults behave like:

```yaml
active_state: "on"
free_flaps: 0
flap_gap_seconds: 30
base_hold_seconds: 30
min_base_hold_seconds: 30
hold_factor: 1.5
max_hold_seconds: 600
min_on_seconds: 0
window_seconds: 120
```

The Antiflap sensor name is the input entity's friendly name plus `Antiflap`.
With `input_entity: binary_sensor.office_motion`, the default entity ID is:

```text
binary_sensor.office_motion_antiflap
```

## Behavior

The binary sensor is on while `input_entity` state matches `active_state`.
If `active_state` is left blank, Antiflap chooses a default based on the input
entity type.

`min_on_seconds` can force the Antiflap binary sensor to stay on for at least a
set amount of time after it turns on. The default is `0`, so only the input
state and adaptive hold logic control when it turns off.

The Antiflap binary sensor also exposes an `entity_id` attribute. It contains
the source `input_entity` plus any entity IDs from the source entity's own
`entity_id` attribute, which lets Home Assistant show group-style source info.

When `input_entity` changes from the active state to any other state, the
integration starts measuring an inactive gap. If `input_entity` returns to the
active state within `flap_gap_seconds`, it records a flap timestamp.
When `input_entity` next leaves the active state, Antiflap counts flaps
inside `window_seconds`, subtracts `free_flaps`, and calculates the hold:

```text
if base_hold_seconds is blank:
    base_hold_seconds = max(
        ceil(flap_gap_seconds / 4),
        min_base_hold_seconds or flap_gap_seconds,
    )

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

### `free_flaps`

*Optional.* Leave blank to use the integration default, currently `0`.

This is how many recent flaps are ignored before Antiflap starts applying an
adaptive hold. With `free_flaps: 0`, the first flap inside the memory window can
create a hold. With `free_flaps: 1`, the first flap is ignored and the second
recent flap starts the hold sequence.

### `flap_gap_seconds`

*Optional.* Leave blank to use the integration default, currently `30`
seconds.

This is the maximum inactive gap that counts as a flap. Antiflap starts timing
when `input_entity` leaves `active_state`. If the input returns to
`active_state` within this many seconds, that inactive gap is recorded as one
flap.

For example, with `flap_gap_seconds: 30`, an input that turns inactive for 20
seconds and then active again records one flap. An input that stays inactive for
45 seconds does not record a flap.

### `base_hold_seconds`

*Optional.* Leave blank to derive it from `flap_gap_seconds` and
`min_base_hold_seconds`.

This is the first hold duration once the number of recent flaps exceeds
`free_flaps`. For example, if `base_hold_seconds` is `30`, the first counted
flap holds the output on for 30 seconds after the input becomes inactive.

### `min_base_hold_seconds`

*Optional.* Leave blank to use the current `flap_gap_seconds` value.

This is the minimum derived `base_hold_seconds` when `base_hold_seconds` is left
blank. For example, with `flap_gap_seconds: 30` and blank
`min_base_hold_seconds`, the derived base hold is at least 30 seconds. Set this
if you want short flap detection but a longer first hold.

### `hold_factor`

*Optional.* Leave blank to use the integration default, currently `1.5`.

This multiplier grows the hold duration as more flaps happen inside the memory
window. A value of `1.0` keeps every counted hold at `base_hold_seconds`. Values
above `1.0` increase each later hold until `max_hold_seconds` is reached.

### `max_hold_seconds`

*Optional.* Leave blank to use the integration default, currently `600`
seconds.

This caps the adaptive hold duration. No calculated hold will be longer than
this value.

### `min_on_seconds`

*Optional.* Leave blank to use the integration default, currently `0` seconds.

This is a separate minimum-on timer for the Antiflap output binary sensor. When
set above `0`, the output remains on for at least this many seconds after it
turns on, even if the input becomes inactive immediately. Set it to `0` to
disable the minimum-on timer.

### `window_seconds`

*Optional.* Leave blank to use a derived default based on the hold curve and
`flap_gap_seconds`.

This controls how long flap timestamps are remembered. Only flaps inside this
rolling window are counted when Antiflap calculates the next hold.

## Service

Call `antiflap.reset` to clear flap-gap history and any active hold for one or
more Antiflap entities.

```yaml
service: antiflap.reset
target:
  entity_id: binary_sensor.office_motion_antiflap
```

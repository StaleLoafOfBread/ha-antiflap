# Antiflap

Antiflap creates a Home Assistant binary sensor from an input entity and keeps
that sensor on for an adaptive hold period after repeated short inactive gaps.

The input entity is the
single source of truth for whether the request is active.

## Example

```yaml
input_entity: binary_sensor.office_motion
active_state: "on"
free_flaps: 1
short_flap_seconds: 60
base_hold_seconds: 30
hold_factor: 1.4
max_hold_seconds: 900
min_on_seconds: 0
window_seconds: 1350
```

The Antiflap sensor name is the input entity's friendly name plus `Antiflap`.
With `input_entity: binary_sensor.office_motion`, the suggested entity ID is:

```text
binary_sensor.office_motion_antiflap
```

## Behavior

The binary sensor is on while `input_entity` state matches `active_state`.
`active_state` defaults to `on`.

`min_on_seconds` controls the minimum time the Antiflap binary sensor remains on
after it turns on. It defaults to `0`, which disables the minimum-on timer and
keeps the previous behavior.

The Antiflap binary sensor also exposes an `entity_id` attribute. It contains
the source `input_entity` plus any entity IDs from the source entity's own
`entity_id` attribute, which lets Home Assistant show group-style source info.

When `input_entity` changes from the active state to any other state, the
integration starts measuring an inactive gap. If `input_entity` returns to the
active state within `short_flap_seconds`, it records a short flap timestamp.
When `input_entity` next leaves the active state, Antiflap counts short flaps
inside `window_seconds`, subtracts `free_flaps`, and calculates the hold:

```text
flap_count = max(short_flap_count - free_flaps, 0)

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

`window_seconds` is optional in the UI. If omitted, it defaults to
`ceil(max_hold_seconds * 1.5)`.

`hold_until` shows when the active hold will end.

## Service

Call `antiflap.reset` to clear short-flap history and any active hold for one or
more Antiflap entities.

```yaml
service: antiflap.reset
target:
  entity_id: binary_sensor.antiflap_office_light
```

## Translation notes

Runtime UI labels and descriptions for this custom integration live in
`translations/en.json`. `strings.json` is not required for Home Assistant to load
custom integration translations.

After changing translations, restart Home Assistant and hard-refresh the browser.
The frontend caches translations, so stale cached text can make fixed
translations appear broken.

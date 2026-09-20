# Irrigation Scheduler for Home Assistant

UI-configured irrigation scheduling for multiple valves sharing one water supply.

## 0.2.3 architectural release

This release deliberately adds no new scheduling behaviour. It restructures the
integration around current Home Assistant architecture while preserving existing
config entries, zone data and entity unique IDs.

- Uses a typed `ConfigEntry.runtime_data` coordinator.
- Uses `DataUpdateCoordinator` for event-driven source updates and a one-minute
  refresh for time-dependent planning and eligibility.
- All entities inherit from `CoordinatorEntity`; the former direct
  `async_write_ha_state` callback fan-out has been removed.
- Keeps the modern UI `ConfigFlow`/options flow; no YAML or legacy platform setup.
- `manifest.json` explicitly declares `version`, `dependencies` and `requirements`.
- Bundles light/dark irrigation-and-clock branding assets.
- Makes no network requests. If network functionality is introduced later, it must
  use Home Assistant's shared `aiohttp` client session.

## Features

- Add, edit and remove zones from **Settings → Devices & services → Irrigation Scheduler → Configure**.
- Uses any Home Assistant `switch` or `valve` entity as a watering actuator.
- Separate moisture **measurement** and **trigger** entities.
- Numeric moisture trigger with hysteresis (`water below` / `reset above`).
- Binary moisture trigger with configurable active state.
- Configurable behaviour when moisture data is unavailable.
- Both days, odd day-of-year, even day-of-year or minimum-interval frequency.
- One integration-owned Watering duration number per zone, optionally backed by an external failsafe-duration number.
- Per-zone and global runtime safety limits.
- Rainfall lockout using today/yesterday numeric entities.
- Weekday, weekend and holiday cutoffs.
- Fair rotating queue persisted across restarts.
- Shared-supply interlock: all configured valves are closed before one is opened.
- Startup and shutdown valve-off safety checks.
- Run, stop and run-zone actions.
- Overall and per-zone dashboard entities.
- Diagnostics download.

## Installation

1. Copy `custom_components/irrigation_scheduler` into your Home Assistant `/config/custom_components/` directory.
2. Restart Home Assistant.
3. Open **Settings → Devices & services → Add integration**.
4. Search for **Irrigation Scheduler**.
5. Complete the global settings flow.
6. Open the integration, choose **Configure**, then **Add zone** for each valve.

## Moisture configuration

Each zone can use one or more numeric **moisture measurement sensors**.

- With **numeric moisture control**, those measurement sensors are compared directly with the integration-owned **Water below** and **Reset above** number entities. There is no second list of numeric trigger sensors.
- With **binary moisture control**, one or more separate binary trigger sensors may be selected, while numeric measurements remain optional for display and drying-trend history.
- Moisture control can also be disabled.

For multiple numeric measurements, the zone can respond to a selected sensor, any dry sensor, all dry sensors, the average reading, or the lowest reading. Hysteresis is retained across runs and restarts.

## Actions

```yaml
action: irrigation_scheduler.run
data:
  force: false
```

```yaml
action: irrigation_scheduler.stop
```

```yaml
action: irrigation_scheduler.run_zone
data:
  zone_id: "ZONE_ID_FROM_ENTITY_ATTRIBUTES_OR_DIAGNOSTICS"
  duration: 10
  force: true
```

Each zone also creates a **Run now** button, avoiding the need to find its internal ID for ordinary use.

## Migration from chained scripts

Do not leave the old chain enabled while this integration controls the same valves. Initially:

1. Add all zones.
2. Keep **Automatic scheduling** off.
3. Test each zone using its **Run now** button.
4. Test the scheduler manually.
5. Disable the old 00:15 automation and chained scripts.
6. Turn **Automatic scheduling** on.

## Important beta note

This is an initial custom integration release. Test valve entity selection, duration units and cutoff behaviour before unattended use. Keep the LinkTap controller's own maximum-run protection enabled where available.

Duration entities are interpreted as **minutes**. Rain and moisture thresholds use the native numeric values of their selected source entities.

## 0.1.1 fixes

- Fixed the Home Assistant 2025.12+ / 2026.x options-flow crash caused by assigning to the read-only `config_entry` property.
- Added explicit validation for required rainfall, valve and moisture-trigger entities.
- Added validation that a numeric moisture reset threshold is greater than its watering threshold.
- Preserves entered values when a validation error is shown.

## 0.1.2 changes

- Added a configurable zone category: Pots, Greenhouse, Border, Lawn or Other.
- Added an explicit **Both odd and even days** schedule option.
- Changed odd/even scheduling to use the ordinal day of the year (`tm_yday`, 1–366), rather than the day number within the month.
- Existing `daily` zone values are treated as `both` and shown as `both` when edited.
- Added category, frequency and current day-of-year to each zone status sensor's attributes.


## 0.1.3

- Rebuilt from the last working options-flow implementation.
- Keeps `config_entry` read-only as required by current Home Assistant.
- Adds zone categories and day-of-year odd/even scheduling.
- Normalises missing/null numeric values from earlier saved zones.
- Uses a consistent archive layout under `custom_components/irrigation_scheduler`.

### 0.1.4

- Fixed Edit zone and Remove zone frontend forms by supplying labelled selector options in the format expected by current Home Assistant.
- Added the missing `number` platform.
- Added available per-zone number entities for fixed duration, minimum interval, order and maximum runtime.
- Numeric moisture zones also expose Water below and Reset above number entities.


## v0.1.6

- Restores a per-zone **Watering duration** number entity.
- If a duration source entity is configured, the entity mirrors it and writes changes back to `number` or `input_number`.
- If no source is configured, it edits the integration-stored duration.


## Generic smart valves

Zones only require a switch or valve entity. Every zone receives an integration-owned **Watering duration** number entity. If a LinkTap (or other) editable failsafe-duration entity is selected, the integration number mirrors it and writes changes through to it. If no external duration entity exists, as with many Sonoff Zigbee valves, the same integration number stores the duration itself. The controller availability sensor remains optional; when omitted, the scheduler uses the valve switch's own availability.


## v0.1.7

- Restored the original single-duration model.
- Every zone always exposes an integration-owned **Watering duration** number.
- The external failsafe-duration number is optional and transparently backs the integration number when configured.
- Valves without an external duration entity use the same integration number as stored configuration.
- Removed the fixed/entity mode selector introduced in v0.1.6.

## v0.1.8

- Fixed optional entity selectors rejecting blank values with “Entity is neither a valid entity ID nor a valid UUID”.
- Uses suggested values rather than invalid empty-string defaults for entity selectors.
- Clearing an optional linked, failsafe-duration, holiday or moisture entity now removes the stored reference.
- Normalises blank optional entity references saved by earlier releases.



## v0.1.10

- Watering actuator selection now accepts Home Assistant `switch` and `valve` entities.
- Uses `switch.turn_on`/`switch.turn_off` for switches and `valve.open_valve`/`valve.close_valve` for valves.
- Adds a parent **Watering** switch which starts the scheduler when turned on and safely stops it when turned off.
- Adds a per-zone **Stop watering** button, available while that zone is active.
- Run buttons now start background tasks instead of holding the entity service call open for the full watering duration.
- **Needs water** now reports moisture demand only.
- Adds a separate **Eligible** binary sensor for the combined valve, controller, frequency, rain and moisture decision.
- Tracks configured source-entity state changes, so moisture, rainfall, controller and valve status update integration entities immediately.
- Refreshes time-dependent eligibility once per minute.
- Editing integration-owned numeric controls now persists without reloading the integration, so changing a duration or moisture threshold does not stop an active run.

## v0.1.11

Adds scheduler planning and cycle-summary entities:

- **Odd day possible watering** — maximum enabled-zone valve-open time for an odd day-of-year.
- **Even day possible watering** — maximum enabled-zone valve-open time for an even day-of-year.
- **Tonight planned watering** — live total for zones currently expected to be eligible at the next configured start.
- **Tonight available duration** — minutes between the next configured start and the applicable weekday, weekend or holiday cutoff.
- **Tonight plan exceeds window** — problem binary sensor which turns on when one or more eligible zones are projected to be deferred.
- **Last cycle watering** — actual valve-open time in the most recent full scheduler cycle, persisted across restarts.

The odd/even maximum sensors include daily/both zones on both parities. Minimum-interval zones are also included on both because they can potentially fall on either day. Their attributes identify those interval zones.

The tonight plan uses the next configured start date for day-of-year parity and minimum-interval calculations. Moisture, rainfall, valve and controller states are a live snapshot and update whenever their source entities change. The planned sensor attributes list eligible, skipped, fitting and projected-deferred zones.

Individual per-zone **Run now** actions do not replace the **Last cycle watering** value; it records full scheduler cycles only. Interrupted full cycles retain the actual partial valve-open duration and a result attribute such as `cancelled` or `fault`.

### Multiple moisture sources

A zone can have several numeric display measurements and several trigger entities.
For numeric triggers, the response strategy can be changed from the zone controls:

- **selected**: only the chosen source controls watering;
- **any**: any source below its hysteresis threshold requests watering;
- **all**: every available source must request watering;
- **average**: the average reading is compared with the thresholds;
- **minimum**: the lowest reading is compared with the thresholds.

Binary triggers support selected, any and all. The **Moisture source** select is
available when Selected sensor mode is active.

### Adaptive duration

For numeric moisture zones, **Adaptive duration** can be enabled per zone. The
integration stores the daily minimum moisture value for up to 31 days. It examines
completed, consecutive calendar days only. When the configured number of days each
show at least the configured reduction from the previous day, the scheduler adds the
configured minutes per declining transition, subject to the maximum boost and the
zone maximum-runtime safety cap.

The **Drying trend** binary sensor and **Duration adjustment** sensor expose the
calculation and daily minima. When a scheduled zone actually receives a boost, a
persistent Home Assistant frontend notification is created. Historical minima begin
accumulating after this version is installed; Recorder history is not backfilled.

For a LinkTap zone backed by an external failsafe-duration number, the integration
temporarily raises that number to the effective adaptive duration before opening the
valve and restores the configured base value after the valve closes.

## v0.2.3

- Migrated zone device relationships from deprecated `DeviceInfo.via_device` identifier tuples to `DeviceInfo.via_device_id`.
- Registers the parent Garden irrigation scheduler device before forwarding entity platforms, then uses its concrete device-registry ID for all child zone devices.
- Removes the Home Assistant 2026.9 deprecation warning and is compatible with the planned removal of `via_device` in Home Assistant 2027.8.

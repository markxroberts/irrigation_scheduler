"""Scheduler engine for Irrigation Scheduler."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
import logging
from time import monotonic
from collections.abc import Callable
from typing import Any

from homeassistant.components.persistent_notification import async_create, async_dismiss
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import *
from .models import RuntimeState, ZoneDecision

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1


class IrrigationSchedulerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate irrigation zones sharing one water supply."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(minutes=1),
            always_update=True,
        )
        self.entry = entry
        self.state = RuntimeState()
        self._unsub_callbacks: list[Callable[[], None]] = []
        self._store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._cursor_id: str | None = None
        self._last_watered: dict[str, str] = {}
        self._numeric_latches: dict[str, bool] = {}
        self._daily_minima: dict[str, dict[str, float]] = {}
        self._automatic = True
        self._last_cycle_watering_seconds: float = 0.0
        self._last_cycle_started: str | None = None
        self._last_cycle_finished: str | None = None
        self._last_cycle_result: str | None = None
        self._last_cycle_completed_zones: list[str] = []
        self._last_cycle_zones_deferred: int = 0
        self._suppress_reload_once = False
        self._revision = 0

    @property
    def config(self) -> dict:
        """Return merged config-entry data and options."""
        return {**self.entry.data, **self.entry.options}

    @property
    def zones(self) -> list[dict]:
        """Return configured zones in execution order."""
        return sorted(
            self.entry.options.get(CONF_ZONES, []),
            key=lambda z: (self._safe_number(z.get("order"), 999), z.get("name", "")),
        )

    def get_zone(self, zone_id: str) -> dict | None:
        """Return the latest configured zone dictionary by stable ID."""
        return next((zone for zone in self.zones if zone.get("id") == zone_id), None)

    async def _async_setup(self) -> None:
        """Restore persisted state and register event-driven listeners."""
        saved = await self._store.async_load() or {}
        self._cursor_id = saved.get("cursor_id")
        self._last_watered = saved.get("last_watered", {})
        self._numeric_latches = saved.get("numeric_latches", {})
        self._daily_minima = {
            zone_id: {day: float(value) for day, value in values.items()}
            for zone_id, values in saved.get("daily_minima", {}).items()
            if isinstance(values, dict)
        }
        self._automatic = saved.get("automatic", True)
        self._last_cycle_watering_seconds = self._safe_number(
            saved.get("last_cycle_watering_seconds"), 0
        )
        self._last_cycle_started = saved.get("last_cycle_started")
        self._last_cycle_finished = saved.get("last_cycle_finished")
        self._last_cycle_result = saved.get("last_cycle_result")
        self._last_cycle_completed_zones = list(
            saved.get("last_cycle_completed_zones", [])
        )
        self._last_cycle_zones_deferred = int(
            self._safe_number(saved.get("last_cycle_zones_deferred"), 0)
        )
        self._schedule_start_listener()
        if self._record_daily_minima():
            await self._save()
        # Perform the startup safety close before subscribing to actuator state
        # changes. Otherwise our own switch/valve commands can create a burst of
        # source events while Home Assistant is restoring entities. The initial
        # coordinator refresh immediately after setup reads the final live states.
        await self._close_all_valves("Home Assistant startup safety check")
        self._schedule_source_listeners()

    async def _async_update_data(self) -> dict[str, Any]:
        """Refresh calculated state for coordinator polling or push updates."""
        if self._record_daily_minima():
            await self._save()
        return self._coordinator_snapshot()

    async def async_shutdown(self) -> None:
        """Stop watering and remove coordinator-owned listeners."""
        await self.async_stop("Integration unloaded")
        for unsub in self._unsub_callbacks:
            unsub()
        self._unsub_callbacks.clear()
        await super().async_shutdown()

    def _schedule_start_listener(self) -> None:
        start = self._parse_time(self.config.get(CONF_START_TIME, DEFAULT_START_TIME))
        self._unsub_callbacks.append(
            async_track_time_change(
                self.hass,
                self._scheduled_start,
                hour=start.hour,
                minute=start.minute,
                second=start.second,
            )
        )

    def _source_entity_ids(self) -> list[str]:
        """Return external entities which affect scheduler/entity state."""
        entity_ids: set[str] = set()
        for key in (
            CONF_HOLIDAY_ENTITY,
            CONF_RAIN_TODAY_ENTITY,
            CONF_RAIN_YESTERDAY_ENTITY,
        ):
            entity_id = self.config.get(key)
            if entity_id:
                entity_ids.add(entity_id)

        for zone in self.zones:
            for key in (
                "valve_entity_id",
                "linked_entity_id",
                "duration_entity_id",
            ):
                entity_id = zone.get(key)
                if entity_id:
                    entity_ids.add(entity_id)
            entity_ids.update(self._measurement_entity_ids(zone))
            entity_ids.update(self._trigger_entity_ids(zone))
        return sorted(entity_ids)

    def _schedule_source_listeners(self) -> None:
        entity_ids = self._source_entity_ids()
        if entity_ids:
            self._unsub_callbacks.append(
                async_track_state_change_event(
                    self.hass, entity_ids, self._source_state_changed
                )
            )

    @callback
    def _source_state_changed(self, event: Event) -> None:
        """Coalesce source-entity changes into a coordinator refresh.

        Home Assistant restores many entity states in a burst during startup.
        Calling ``async_set_updated_data`` for every one of those events causes
        DataUpdateCoordinator to emit a debug message for every update and can
        trip Home Assistant's log-rate protection. ``async_request_refresh``
        uses the coordinator's built-in debouncer, so a burst is collapsed into
        a small number of refreshes while retaining prompt live updates.
        Daily minima are recorded by ``_async_update_data`` during that refresh.
        """
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _scheduled_start(self, now: datetime) -> None:
        if self._automatic:
            self.start_run()

    @callback
    def start_run(self, force: bool = False) -> bool:
        """Start a scheduler task and return immediately."""
        if self.state.running or (self.state.task and not self.state.task.done()):
            return False
        self.state.task = self.hass.async_create_task(self.async_run(force=force))
        return True

    @callback
    def start_zone(
        self,
        zone_id: str,
        *,
        force: bool = True,
        duration_override: float | None = None,
    ) -> bool:
        """Start a manual zone task and return immediately."""
        if self.state.running or (self.state.task and not self.state.task.done()):
            return False
        self.state.task = self.hass.async_create_task(
            self.async_run_zone(
                zone_id,
                force=force,
                duration_override=duration_override,
            )
        )
        return True

    async def async_run(self, force: bool = False) -> None:
        """Evaluate and execute all zones in cursor order."""
        if self.state.running:
            return
        if not self.zones:
            self.state.last_message = "No irrigation zones configured"
            self.state.task = None
            self.async_notify()
            return
        self.state.running = True
        self.state.last_run = dt_util.now()
        self.state.zones_completed = 0
        self.state.zones_deferred = 0
        self.state.current_cycle_watering_seconds = 0.0
        self.state.current_cycle_completed_zones = []
        self.state.fault = None
        self.state.task = asyncio.current_task()
        cycle_started = self.state.last_run
        cycle_result = "completed"
        self.async_notify()
        try:
            ordered = self._ordered_zones()
            decisions = [(z, self.evaluate_zone(z, force=force)) for z in ordered]
            self.state.zones_due = sum(
                1 for _, decision in decisions if decision.eligible
            )
            deadline = self._deadline()
            for zone, decision in decisions:
                if not self.state.running:
                    break
                self.state.next_zone_id = zone["id"]
                self._cursor_id = zone["id"]
                self.async_notify()
                self._fire_decision(zone, decision)
                if not decision.eligible:
                    self.state.last_message = (
                        f"Skipped {zone['name']}: {decision.reason}"
                    )
                    self._advance_cursor(zone["id"])
                    self.async_notify()
                    continue
                duration = self._effective_duration_minutes(zone)
                if (
                    dt_util.now() + timedelta(minutes=duration, seconds=10)
                    > deadline
                ):
                    self.state.zones_deferred += 1
                    self.state.last_message = (
                        f"Deferred {zone['name']}: insufficient time before "
                        f"{deadline:%H:%M}"
                    )
                    self.async_notify()
                    break
                trend = self.drying_trend_summary(zone)
                self._update_adaptive_notification(zone, trend)
                await self._water_zone(zone, duration)
                self._advance_cursor(zone["id"])
                delay = float(
                    self.config.get(CONF_INTER_ZONE_DELAY, DEFAULT_INTER_ZONE_DELAY)
                )
                if delay:
                    await asyncio.sleep(delay)
            if self.state.running:
                if self.state.zones_deferred:
                    cycle_result = "completed_with_deferred"
                self.state.last_message = "Irrigation run completed"
        except asyncio.CancelledError:
            cycle_result = "cancelled"
            self.state.last_message = "Irrigation run cancelled"
            raise
        except Exception as err:  # noqa: BLE001
            cycle_result = "fault"
            _LOGGER.exception("Irrigation run failed")
            self.state.fault = str(err)
            self.state.last_message = f"Fault: {err}"
        finally:
            await self._close_all_valves("End of irrigation run")
            self.state.running = False
            self.state.current_zone_id = None
            self.state.current_zone_name = None
            self.state.task = None
            self._last_cycle_watering_seconds = (
                self.state.current_cycle_watering_seconds
            )
            self._last_cycle_started = (
                cycle_started.isoformat() if cycle_started else None
            )
            self._last_cycle_finished = dt_util.now().isoformat()
            self._last_cycle_result = cycle_result
            self._last_cycle_completed_zones = list(
                self.state.current_cycle_completed_zones
            )
            self._last_cycle_zones_deferred = self.state.zones_deferred
            await self._save()
            self.async_notify()

    async def async_run_zone(
        self,
        zone_id: str,
        force: bool = True,
        duration_override: float | None = None,
    ) -> None:
        """Run one zone manually."""
        if self.state.running:
            return
        zone = next((z for z in self.zones if z["id"] == zone_id), None)
        if zone is None:
            raise ValueError(f"Unknown irrigation zone: {zone_id}")
        if not zone.get("enabled", True) and not zone.get(
            "allow_manual_when_disabled", True
        ):
            raise ValueError("Manual watering is disabled for this zone")
        decision = self.evaluate_zone(zone, force=force)
        if not decision.eligible:
            self.state.last_message = f"Manual run rejected: {decision.reason}"
            self.async_notify()
            return
        self.state.running = True
        self.state.current_cycle_watering_seconds = 0.0
        self.state.current_cycle_completed_zones = []
        self.state.task = asyncio.current_task()
        self.async_notify()
        try:
            await self._water_zone(
                zone, float(duration_override or self._duration_minutes(zone))
            )
        except asyncio.CancelledError:
            self.state.last_message = "Manual zone run cancelled"
            raise
        finally:
            await self._close_all_valves("Manual zone run ended")
            self.state.running = False
            self.state.current_zone_id = None
            self.state.current_zone_name = None
            self.state.task = None
            await self._save()
            self.async_notify()

    async def async_stop(self, reason: str = "Stopped") -> None:
        """Cancel an active run and close every configured actuator."""
        task = self.state.task
        self.state.running = False
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._close_all_valves(reason)
        self.state.current_zone_id = None
        self.state.current_zone_name = None
        self.state.task = None
        self.state.last_message = reason
        self.async_notify()

    async def async_update_zone_setting(
        self, zone_id: str, key: str, value: Any
    ) -> None:
        """Persist one non-structural zone setting without interrupting a run.

        Use copy-on-write because Home Assistant replaces the options mapping on
        update.  Zone entities resolve their configuration dynamically by ID, so
        they immediately see the newly stored dictionary rather than a stale
        object retained from setup.
        """
        current_zones = self.entry.options.get(CONF_ZONES, [])
        updated_zones: list[dict] = []
        found = False
        for configured_zone in current_zones:
            zone = dict(configured_zone)
            if zone.get("id") == zone_id:
                zone[key] = value
                found = True
            updated_zones.append(zone)
        if not found:
            raise ValueError(f"Unknown irrigation zone: {zone_id}")

        options = dict(self.entry.options)
        options[CONF_ZONES] = updated_zones
        # Simple value changes do not alter platform composition or source
        # listeners, so a full reload would unnecessarily stop an active run.
        self._suppress_reload_once = True
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        self.async_notify()

    async def async_update_zone_value(
        self, zone_id: str, key: str, value: float
    ) -> None:
        """Backward-compatible wrapper for editable numeric entities."""
        await self.async_update_zone_setting(zone_id, key, value)

    @callback
    def consume_suppressed_reload(self) -> bool:
        """Return and clear the one-shot config-entry reload suppression flag."""
        if not self._suppress_reload_once:
            return False
        self._suppress_reload_once = False
        return True

    def evaluate_zone(
        self, zone: dict, force: bool = False, at: datetime | None = None
    ) -> ZoneDecision:
        """Return full scheduler eligibility for a zone."""
        if force:
            return ZoneDecision(True, "Manual override")
        if not zone.get("enabled", True):
            return ZoneDecision(False, "disabled")
        valve_entity = zone.get("valve_entity_id")
        valve_state = self.hass.states.get(valve_entity) if valve_entity else None
        if valve_state is None or valve_state.state in (
            STATE_UNKNOWN,
            STATE_UNAVAILABLE,
        ):
            return ZoneDecision(False, "valve unavailable")
        linked = zone.get("linked_entity_id")
        if linked:
            linked_state = self.hass.states.get(linked)
            if linked_state is None or linked_state.state in (
                STATE_UNKNOWN,
                STATE_UNAVAILABLE,
            ):
                return ZoneDecision(False, "controller status unavailable")
            if linked_state.state != STATE_ON:
                return ZoneDecision(False, "controller unavailable")
        if not self._frequency_due(zone, at=at):
            return ZoneDecision(False, "not due by frequency")
        if zone.get("rain_sensitive", True):
            rain_reason = self._rain_lockout_reason()
            if rain_reason:
                return ZoneDecision(False, rain_reason)
        return self.moisture_decision(zone)

    @staticmethod
    def _as_entity_list(value: Any) -> list[str]:
        """Normalise legacy single entity IDs and new multi-entity lists."""
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, (list, tuple, set)):
            return [item for item in value if isinstance(item, str) and item.strip()]
        return []

    def _measurement_entity_ids(self, zone: dict) -> list[str]:
        """Return numeric moisture sensors, including v0.1.14 legacy selections."""
        values = self._as_entity_list(zone.get("moisture_measurement_entity_ids"))
        if not values:
            values = self._as_entity_list(zone.get("moisture_measurement_entity_id"))
        if zone.get("moisture_trigger_type") == TRIGGER_NUMERIC:
            # v0.1.14 briefly stored numeric sources in a second trigger list.
            # Treat them as measurements until the zone is next reconfigured.
            legacy = self._as_entity_list(zone.get("moisture_trigger_entity_ids"))
            if not legacy:
                legacy = self._as_entity_list(zone.get("moisture_trigger_entity_id"))
            values = list(dict.fromkeys([*values, *legacy]))
        return values

    def _trigger_entity_ids(self, zone: dict) -> list[str]:
        """Return binary trigger entities; numeric control uses measurements."""
        if zone.get("moisture_trigger_type") != TRIGGER_BINARY:
            return []
        values = self._as_entity_list(zone.get("moisture_trigger_entity_ids"))
        if not values:
            values = self._as_entity_list(zone.get("moisture_trigger_entity_id"))
        return values

    def _moisture_source_entity_ids(self, zone: dict) -> list[str]:
        """Return the entities evaluated by the configured moisture rule."""
        if zone.get("moisture_trigger_type") == TRIGGER_NUMERIC:
            return self._measurement_entity_ids(zone)
        if zone.get("moisture_trigger_type") == TRIGGER_BINARY:
            return self._trigger_entity_ids(zone)
        return []

    def _selected_trigger_entity(self, zone: dict) -> str | None:
        """Return the selected numeric measurement or binary trigger source."""
        entity_ids = self._moisture_source_entity_ids(zone)
        selected = zone.get("moisture_selected_entity_id")
        return selected if selected in entity_ids else (entity_ids[0] if entity_ids else None)

    def moisture_measurements(self, zone: dict) -> dict[str, float | None]:
        """Return all configured numeric moisture measurements."""
        return {
            entity_id: self._numeric_state(entity_id)
            for entity_id in self._measurement_entity_ids(zone)
        }

    def moisture_trigger_readings(self, zone: dict) -> dict[str, Any]:
        """Return values actually used by the configured moisture rule."""
        entity_ids = self._moisture_source_entity_ids(zone)
        if zone.get("moisture_trigger_type") == TRIGGER_NUMERIC:
            return {entity_id: self._numeric_state(entity_id) for entity_id in entity_ids}
        readings: dict[str, Any] = {}
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            readings[entity_id] = (
                None
                if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE)
                else state.state
            )
        return readings

    def zone_moisture_value(self, zone: dict) -> float | None:
        """Return the primary/aggregate moisture value shown by the zone sensor."""
        readings = self.moisture_measurements(zone)
        valid = {key: value for key, value in readings.items() if value is not None}
        if not valid:
            return None
        selected = self._selected_trigger_entity(zone)
        if selected in valid:
            return valid[selected]
        mode = zone.get("moisture_response_mode", MOISTURE_MODE_SELECTED)
        values = list(valid.values())
        if mode == MOISTURE_MODE_AVERAGE:
            return sum(values) / len(values)
        # The lowest reading is the most useful display default for a zone with
        # several probes because it identifies the driest sampled position.
        return min(values)

    def _apply_numeric_latch(
        self, zone_id: str, latch_key: str, value: float, low: float, reset: float
    ) -> bool:
        key = f"{zone_id}:{latch_key}"
        previous = self._numeric_latches.get(key, False)
        latched = previous
        if value < low:
            latched = True
        elif value > reset:
            latched = False
        if latched != previous:
            self._numeric_latches[key] = latched
            self.hass.async_create_task(self._save())
        return latched

    def _unavailable_moisture_decision(
        self, zone: dict, unavailable: list[str], measurement: float | None
    ) -> ZoneDecision | None:
        """Apply the configured fallback for unavailable trigger sources."""
        if not unavailable:
            return None
        fallback = zone.get("moisture_unavailable", FALLBACK_SKIP)
        sources = ", ".join(unavailable)
        if fallback == FALLBACK_SKIP:
            return ZoneDecision(
                False, f"moisture sensor unavailable: {sources}", measurement
            )
        if fallback == FALLBACK_WATER:
            return ZoneDecision(
                True, f"moisture unavailable; water fallback: {sources}", measurement
            )
        # Ignore unavailable sources where at least one usable source remains.
        return None

    def moisture_decision(self, zone: dict) -> ZoneDecision:
        """Return moisture demand independently of schedule/rain/availability."""
        trigger_type = zone.get("moisture_trigger_type", TRIGGER_NONE)
        measurement = self.zone_moisture_value(zone)
        if trigger_type == TRIGGER_NONE:
            return ZoneDecision(True, "moisture trigger disabled", measurement)

        entity_ids = self._moisture_source_entity_ids(zone)
        if not entity_ids:
            unavailable = self._unavailable_moisture_decision(
                zone, ["no trigger entity configured"], measurement
            )
            return unavailable or ZoneDecision(
                True, "moisture trigger unavailable; ignored", measurement
            )

        mode = zone.get("moisture_response_mode", MOISTURE_MODE_SELECTED)
        if mode == MOISTURE_MODE_SELECTED:
            selected = self._selected_trigger_entity(zone)
            entity_ids = [selected] if selected else []

        if trigger_type == TRIGGER_BINARY:
            readings = self.moisture_trigger_readings(zone)
            unavailable_ids = [entity_id for entity_id in entity_ids if readings.get(entity_id) is None]
            fallback_decision = self._unavailable_moisture_decision(
                zone, unavailable_ids, measurement
            )
            if fallback_decision is not None:
                return fallback_decision
            usable = [
                readings[entity_id] == zone.get("moisture_trigger_state", "off")
                for entity_id in entity_ids
                if readings.get(entity_id) is not None
            ]
            if not usable:
                return ZoneDecision(
                    True, "all unavailable moisture sources ignored", measurement
                )
            required = all(usable) if mode == MOISTURE_MODE_ALL else any(usable)
            return ZoneDecision(
                required,
                f"{mode} binary moisture source{'s' if len(usable) != 1 else ''} "
                + ("require watering" if required else "report adequate moisture"),
                measurement,
            )

        readings = self.moisture_trigger_readings(zone)
        unavailable_ids = [entity_id for entity_id in entity_ids if readings.get(entity_id) is None]
        fallback_decision = self._unavailable_moisture_decision(
            zone, unavailable_ids, measurement
        )
        if fallback_decision is not None:
            return fallback_decision
        available = {
            entity_id: float(readings[entity_id])
            for entity_id in entity_ids
            if readings.get(entity_id) is not None
        }
        if not available:
            return ZoneDecision(
                True, "all unavailable moisture sources ignored", measurement
            )

        low = float(zone.get("moisture_below", 30))
        reset = float(zone.get("moisture_reset_above", low))
        zone_id = zone["id"]
        if mode in (MOISTURE_MODE_AVERAGE, MOISTURE_MODE_MINIMUM):
            values = list(available.values())
            aggregate = (
                sum(values) / len(values)
                if mode == MOISTURE_MODE_AVERAGE
                else min(values)
            )
            required = self._apply_numeric_latch(
                zone_id, f"aggregate:{mode}", aggregate, low, reset
            )
            return ZoneDecision(
                required,
                f"{mode} moisture {aggregate:g}; trigger below {low:g}, "
                f"reset above {reset:g}",
                aggregate,
            )

        latched = {
            entity_id: self._apply_numeric_latch(
                zone_id, entity_id, value, low, reset
            )
            for entity_id, value in available.items()
        }
        required = all(latched.values()) if mode == MOISTURE_MODE_ALL else any(latched.values())
        chosen = self._selected_trigger_entity(zone)
        aggregate = (
            available.get(chosen)
            if mode == MOISTURE_MODE_SELECTED and chosen
            else min(available.values())
        )
        return ZoneDecision(
            required,
            f"{mode} numeric moisture sources "
            + ("require watering" if required else "report adequate moisture"),
            aggregate,
        )

    def _trend_sample_value(self, zone: dict) -> float | None:
        """Return the value whose daily minimum is tracked for adaptive watering."""
        if zone.get("moisture_trigger_type") != TRIGGER_NUMERIC:
            return None
        source_ids = self._measurement_entity_ids(zone)
        selected = self._selected_trigger_entity(zone)
        if zone.get("moisture_response_mode") == MOISTURE_MODE_SELECTED and selected:
            selected_value = self._numeric_state(selected)
            if selected_value is not None:
                return selected_value
        values = [
            value
            for entity_id in source_ids
            if (value := self._numeric_state(entity_id)) is not None
        ]
        return min(values) if values else None

    def _record_daily_minima(self, now: datetime | None = None) -> bool:
        """Capture lower moisture values and prune old daily history."""
        reference = now or dt_util.now()
        today = reference.date().isoformat()
        cutoff = reference.date() - timedelta(days=DAILY_MINIMUM_HISTORY_DAYS)
        changed = False
        configured_ids = {zone["id"] for zone in self.zones}
        for stale_id in set(self._daily_minima) - configured_ids:
            self._daily_minima.pop(stale_id, None)
            changed = True
        for zone in self.zones:
            value = self._trend_sample_value(zone)
            if value is None:
                continue
            history = self._daily_minima.setdefault(zone["id"], {})
            previous = history.get(today)
            if previous is None or value < previous:
                history[today] = value
                changed = True
            for day in list(history):
                try:
                    if date.fromisoformat(day) < cutoff:
                        history.pop(day, None)
                        changed = True
                except ValueError:
                    history.pop(day, None)
                    changed = True
        return changed

    def drying_trend_summary(
        self, zone: dict, reference: datetime | None = None
    ) -> dict[str, Any]:
        """Return consecutive completed-day drying trend and duration boost."""
        enabled = bool(zone.get("adaptive_duration_enabled", False))
        numeric = zone.get("moisture_trigger_type") == TRIGGER_NUMERIC
        required_days = max(2, int(self._safe_number(zone.get("adaptive_trend_days"), DEFAULT_TREND_DAYS)))
        min_drop = max(0.0, self._safe_number(zone.get("adaptive_min_drop"), DEFAULT_TREND_MIN_DROP))
        boost_per_day = max(0.0, self._safe_number(zone.get("adaptive_boost_per_day"), DEFAULT_TREND_BOOST_PER_DAY))
        max_boost = max(0.0, self._safe_number(zone.get("adaptive_max_boost"), DEFAULT_TREND_MAX_BOOST))
        today = (reference or dt_util.now()).date()
        history = self._daily_minima.get(zone["id"], {})

        consecutive: list[tuple[str, float]] = []
        cursor = today - timedelta(days=1)
        for _ in range(DAILY_MINIMUM_HISTORY_DAYS):
            key = cursor.isoformat()
            if key not in history:
                break
            consecutive.append((key, float(history[key])))
            cursor -= timedelta(days=1)
        consecutive.reverse()

        transitions = 0
        for index in range(len(consecutive) - 1, 0, -1):
            older = consecutive[index - 1][1]
            newer = consecutive[index][1]
            if newer <= older - min_drop:
                transitions += 1
            else:
                break
        declining_days = transitions + 1 if transitions else 0
        active = enabled and numeric and declining_days >= required_days
        boost = min(max_boost, transitions * boost_per_day) if active else 0.0
        return {
            "enabled": enabled,
            "supported": numeric,
            "active": active,
            "required_days": required_days,
            "declining_days": declining_days,
            "declining_transitions": transitions,
            "minimum_daily_drop": min_drop,
            "boost_per_declining_day": boost_per_day,
            "maximum_boost": max_boost,
            "boost_minutes": boost,
            "daily_minima": [
                {"date": day, "minimum": value} for day, value in consecutive
            ],
        }

    def _update_adaptive_notification(self, zone: dict, trend: dict[str, Any]) -> None:
        """Create/dismiss a stable frontend notification for a duration boost."""
        notification_id = f"{DOMAIN}_adaptive_{zone['id']}"
        if not trend["active"] or trend["boost_minutes"] <= 0:
            async_dismiss(self.hass, notification_id)
            return
        if not zone.get("adaptive_notify", True):
            return
        base = self._duration_minutes(zone)
        effective = min(
            base + trend["boost_minutes"],
            self._safe_number(
                zone.get("max_runtime"),
                self.config.get(CONF_MAX_RUNTIME, DEFAULT_MAX_RUNTIME),
            ),
        )
        minima = ", ".join(
            f"{item['date']}: {item['minimum']:g}"
            for item in trend["daily_minima"][-7:]
        )
        async_create(
            self.hass,
            (
                f"{zone['name']} has shown a progressive reduction in its daily "
                f"minimum moisture level. Watering has been increased from "
                f"{base:g} to {effective:g} minutes (+{trend['boost_minutes']:g}).\n\n"
                f"Daily minima: {minima}"
            ),
            title="Irrigation duration increased",
            notification_id=notification_id,
        )

    async def _water_zone(self, zone: dict, duration: float) -> None:
        duration = min(
            duration,
            float(
                zone.get(
                    "max_runtime",
                    self.config.get(CONF_MAX_RUNTIME, DEFAULT_MAX_RUNTIME),
                )
            ),
        )
        self.state.current_zone_id = zone["id"]
        self.state.current_zone_name = zone["name"]
        self.state.last_message = f"Watering {zone['name']} for {duration:g} minutes"
        self.async_notify()
        await self._close_all_valves("Shared supply interlock")
        duration_source = zone.get("duration_entity_id") or None
        original_source_duration = (
            self._numeric_state(duration_source) if duration_source else None
        )
        source_was_adjusted = False
        if (
            duration_source
            and original_source_duration is not None
            and abs(original_source_duration - duration) > 0.01
        ):
            await self._set_duration_source(duration_source, duration)
            source_was_adjusted = True
        await self._set_actuator(zone["valve_entity_id"], opened=True)
        opened_at = monotonic()
        completed = False
        self.hass.bus.async_fire(
            EVENT_ZONE_STARTED,
            {"zone_id": zone["id"], "name": zone["name"], "duration": duration},
        )
        try:
            await asyncio.sleep(duration * 60)
            completed = True
        finally:
            elapsed = max(0.0, monotonic() - opened_at)
            self.state.current_cycle_watering_seconds += elapsed
            await self._set_actuator(zone["valve_entity_id"], opened=False)
            if source_was_adjusted and original_source_duration is not None:
                try:
                    await self._set_duration_source(
                        duration_source, original_source_duration
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Unable to restore duration source %s after watering %s",
                        duration_source,
                        zone["name"],
                    )
            self.async_notify()
        if not completed:
            return
        self._last_watered[zone["id"]] = dt_util.now().isoformat()
        self.state.zones_completed += 1
        self.state.current_cycle_completed_zones.append(zone["name"])
        self.state.last_message = f"Completed {zone['name']}"
        self.hass.bus.async_fire(
            EVENT_ZONE_FINISHED,
            {"zone_id": zone["id"], "name": zone["name"], "duration": duration},
        )
        await self._save()
        self.async_notify()

    async def _set_duration_source(self, entity_id: str, value: float) -> None:
        """Set an editable external duration number in minutes."""
        domain = entity_id.split(".", 1)[0]
        if domain == "number":
            service = "set_value"
        elif domain == "input_number":
            service = "set_value"
        else:
            raise ValueError(
                f"Unsupported duration source {entity_id}; use number or input_number"
            )
        await self.hass.services.async_call(
            domain,
            service,
            {"entity_id": entity_id, "value": value},
            blocking=True,
        )

    async def _set_actuator(self, entity_id: str, *, opened: bool) -> None:
        """Open/close either a switch or a valve entity."""
        domain = entity_id.split(".", 1)[0]
        if domain == "switch":
            service = "turn_on" if opened else "turn_off"
        elif domain == "valve":
            service = "open_valve" if opened else "close_valve"
        else:
            raise ValueError(
                f"Unsupported watering actuator {entity_id}; use a switch or valve"
            )
        await self.hass.services.async_call(
            domain, service, {"entity_id": entity_id}, blocking=True
        )

    async def _close_all_valves(self, reason: str) -> None:
        """Close all configured switch/valve actuators, continuing on errors."""
        actuators = {
            zone.get("valve_entity_id")
            for zone in self.zones
            if zone.get("valve_entity_id")
        }
        for entity_id in sorted(actuators):
            try:
                await self._set_actuator(entity_id, opened=False)
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Unable to close irrigation actuator %s (%s)", entity_id, reason
                )

    def _frequency_due(self, zone: dict, at: datetime | None = None) -> bool:
        frequency = zone.get("frequency", FREQ_DAILY)
        if frequency in (FREQ_DAILY, FREQ_BOTH):
            return True
        # Odd/even schedules are based on the ordinal day of the year (1-366),
        # not the day number within the current month.
        reference = at or dt_util.now()
        day = reference.timetuple().tm_yday
        if frequency == FREQ_ODD:
            return day % 2 == 1
        if frequency == FREQ_EVEN:
            return day % 2 == 0
        if frequency == FREQ_INTERVAL:
            last = self._last_watered.get(zone["id"])
            if not last:
                return True
            return reference - datetime.fromisoformat(last) >= timedelta(
                hours=float(zone.get("minimum_interval_hours", 24))
            )
        return True

    def _rain_lockout_reason(self) -> str | None:
        checks = (
            (
                self.config.get(CONF_RAIN_TODAY_ENTITY),
                float(
                    self.config.get(
                        CONF_RAIN_TODAY_LIMIT, DEFAULT_RAIN_TODAY_LIMIT
                    )
                ),
                "today",
            ),
            (
                self.config.get(CONF_RAIN_YESTERDAY_ENTITY),
                float(
                    self.config.get(
                        CONF_RAIN_YESTERDAY_LIMIT,
                        DEFAULT_RAIN_YESTERDAY_LIMIT,
                    )
                ),
                "yesterday",
            ),
        )
        for entity_id, limit, label in checks:
            if not entity_id:
                continue
            value = self._numeric_state(entity_id)
            if value is not None and value >= limit:
                return f"rain {label} is {value:g}, limit {limit:g}"
        return None

    @staticmethod
    def _safe_number(value: Any, fallback: float) -> float:
        """Coerce legacy/null numeric configuration safely."""
        try:
            if value is None or value == "":
                raise ValueError
            return float(value)
        except (TypeError, ValueError):
            return float(fallback)

    def _duration_minutes(self, zone: dict) -> float:
        # Every zone has one integration-owned Watering duration value. An
        # optional external number (for example LinkTap failsafe duration)
        # backs that value when configured; generic valves use fixed_duration.
        entity = zone.get("duration_entity_id") or None
        value = self._numeric_state(entity) if entity else None
        return max(
            0.1,
            self._safe_number(
                value if value is not None else zone.get("fixed_duration"), 10
            ),
        )


    def _effective_duration_minutes(self, zone: dict) -> float:
        """Return base duration plus any drying-trend boost, safety capped."""
        configured = self._duration_minutes(zone)
        trend = self.drying_trend_summary(zone)
        configured += trend["boost_minutes"]
        maximum = self._safe_number(
            zone.get("max_runtime"),
            self.config.get(CONF_MAX_RUNTIME, DEFAULT_MAX_RUNTIME),
        )
        return min(configured, max(0.1, maximum))

    def next_scheduled_start(self, reference: datetime | None = None) -> datetime:
        """Return the next configured automatic start in local time."""
        now = reference or dt_util.now()
        start_time = self._parse_time(
            self.config.get(CONF_START_TIME, DEFAULT_START_TIME)
        )
        candidate = datetime.combine(now.date(), start_time, tzinfo=now.tzinfo)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def _deadline_for(self, run_start: datetime) -> datetime:
        """Return the cutoff belonging to a particular irrigation start."""
        holiday_entity = self.config.get(CONF_HOLIDAY_ENTITY)
        if holiday_entity and self._is_on(holiday_entity):
            value = self.config.get(CONF_HOLIDAY_CUTOFF, DEFAULT_HOLIDAY_CUTOFF)
            cutoff_type = "holiday"
        elif run_start.weekday() >= 5:
            value = self.config.get(CONF_WEEKEND_CUTOFF, DEFAULT_WEEKEND_CUTOFF)
            cutoff_type = "weekend"
        else:
            value = self.config.get(CONF_WEEKDAY_CUTOFF, DEFAULT_WEEKDAY_CUTOFF)
            cutoff_type = "weekday"
        deadline = datetime.combine(
            run_start.date(), self._parse_time(value), tzinfo=run_start.tzinfo
        )
        if deadline <= run_start:
            deadline += timedelta(days=1)
        # The type is useful to plan consumers without changing the public return.
        self._last_planned_cutoff_type = cutoff_type
        return deadline

    def possible_duration_for_parity(self, parity: str) -> dict[str, Any]:
        """Return maximum configured duration for an odd or even day.

        This deliberately ignores moisture, rain and source availability. Enabled
        daily/both zones are included on both days. Minimum-interval zones are also
        included on both because they can potentially fall on either parity.
        """
        included: list[dict[str, Any]] = []
        interval_zones: list[str] = []
        for zone in self.zones:
            if not zone.get("enabled", True):
                continue
            frequency = zone.get("frequency", FREQ_DAILY)
            include = frequency in (FREQ_DAILY, FREQ_BOTH, FREQ_INTERVAL)
            include = include or (parity == FREQ_ODD and frequency == FREQ_ODD)
            include = include or (parity == FREQ_EVEN and frequency == FREQ_EVEN)
            if not include:
                continue
            duration = self._effective_duration_minutes(zone)
            included.append(
                {
                    "id": zone["id"],
                    "name": zone["name"],
                    "duration_minutes": duration,
                    "frequency": frequency,
                }
            )
            if frequency == FREQ_INTERVAL:
                interval_zones.append(zone["name"])

        watering_minutes = sum(item["duration_minutes"] for item in included)
        delay_minutes = (
            max(0, len(included) - 1)
            * self._safe_number(
                self.config.get(CONF_INTER_ZONE_DELAY), DEFAULT_INTER_ZONE_DELAY
            )
            / 60
        )
        return {
            "parity": parity,
            "watering_minutes": watering_minutes,
            "cycle_minutes": watering_minutes + delay_minutes,
            "inter_zone_delay_minutes": delay_minutes,
            "zones": included,
            "interval_zones": interval_zones,
        }

    def next_run_plan(self, reference: datetime | None = None) -> dict[str, Any]:
        """Build a live plan for the next configured automatic cycle."""
        start = self.next_scheduled_start(reference)
        deadline = self._deadline_for(start)
        available_minutes = max(0.0, (deadline - start).total_seconds() / 60)
        delay_minutes_each = self._safe_number(
            self.config.get(CONF_INTER_ZONE_DELAY), DEFAULT_INTER_ZONE_DELAY
        ) / 60

        eligible: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for zone in self._ordered_zones():
            decision = self.evaluate_zone(zone, at=start)
            if decision.eligible:
                eligible.append(
                    {
                        "id": zone["id"],
                        "name": zone["name"],
                        "duration_minutes": self._effective_duration_minutes(zone),
                        "base_duration_minutes": self._duration_minutes(zone),
                        "adaptive_boost_minutes": self.drying_trend_summary(zone)["boost_minutes"],
                    }
                )
            else:
                skipped.append(
                    {
                        "id": zone["id"],
                        "name": zone["name"],
                        "reason": decision.reason,
                    }
                )

        watering_minutes = sum(item["duration_minutes"] for item in eligible)
        inter_zone_delay_minutes = max(0, len(eligible) - 1) * delay_minutes_each
        cycle_minutes = watering_minutes + inter_zone_delay_minutes

        fitting: list[dict[str, Any]] = []
        deferred: list[dict[str, Any]] = []
        elapsed = 0.0
        for index, item in enumerate(eligible):
            # Match the scheduler's ten-second per-zone cutoff margin. The delay
            # after a completed zone counts before the following zone can begin.
            required_to_finish = elapsed + item["duration_minutes"] + (10 / 60)
            if required_to_finish > available_minutes:
                deferred = eligible[index:]
                break
            fitting.append(item)
            elapsed += item["duration_minutes"]
            if index < len(eligible) - 1:
                elapsed += delay_minutes_each

        return {
            "planned_start": start,
            "deadline": deadline,
            "day_of_year": start.timetuple().tm_yday,
            "day_parity": FREQ_ODD if start.timetuple().tm_yday % 2 else FREQ_EVEN,
            "watering_minutes": watering_minutes,
            "inter_zone_delay_minutes": inter_zone_delay_minutes,
            "cycle_minutes": cycle_minutes,
            "available_minutes": available_minutes,
            "exceeds_window": bool(deferred),
            "eligible_zones": eligible,
            "fitting_zones": fitting,
            "deferred_zones": deferred,
            "skipped_zones": skipped,
            "automatic_scheduling": self.automatic,
            "cutoff_type": getattr(self, "_last_planned_cutoff_type", None),
        }

    @property
    def last_cycle_summary(self) -> dict[str, Any]:
        """Return persisted details of the most recent full scheduler cycle."""
        return {
            "watering_minutes": self._last_cycle_watering_seconds / 60,
            "started": self._last_cycle_started,
            "finished": self._last_cycle_finished,
            "result": self._last_cycle_result,
            "completed_zones": list(self._last_cycle_completed_zones),
            "zones_deferred": self._last_cycle_zones_deferred,
        }

    def _deadline(self) -> datetime:
        """Return the cutoff for a cycle running now."""
        return self._deadline_for(dt_util.now())

    def _ordered_zones(self) -> list[dict]:
        zones = self.zones
        if not zones or not self._cursor_id:
            return zones
        index = next(
            (i for i, z in enumerate(zones) if z["id"] == self._cursor_id), 0
        )
        return zones[index:] + zones[:index]

    def _advance_cursor(self, current_id: str) -> None:
        zones = self.zones
        for idx, zone in enumerate(zones):
            if zone["id"] == current_id:
                self._cursor_id = zones[(idx + 1) % len(zones)]["id"]
                return

    def _fire_decision(self, zone: dict, decision: ZoneDecision) -> None:
        self.hass.bus.async_fire(
            EVENT_DECISION,
            {
                "zone_id": zone["id"],
                "name": zone["name"],
                "eligible": decision.eligible,
                "reason": decision.reason,
                "moisture": decision.moisture_value,
                "moisture_response_mode": zone.get(
                    "moisture_response_mode", MOISTURE_MODE_SELECTED
                ),
                "moisture_sources": self.moisture_trigger_readings(zone),
            },
        )

    def _numeric_state(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _is_on(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        return bool(state and state.state == STATE_ON)

    @staticmethod
    def _parse_time(value) -> time:
        if isinstance(value, time):
            return value
        return time.fromisoformat(str(value))

    async def _save(self) -> None:
        await self._store.async_save(
            {
                "cursor_id": self._cursor_id,
                "last_watered": self._last_watered,
                "numeric_latches": self._numeric_latches,
                "daily_minima": self._daily_minima,
                "automatic": self._automatic,
                "last_cycle_watering_seconds": self._last_cycle_watering_seconds,
                "last_cycle_started": self._last_cycle_started,
                "last_cycle_finished": self._last_cycle_finished,
                "last_cycle_result": self._last_cycle_result,
                "last_cycle_completed_zones": self._last_cycle_completed_zones,
                "last_cycle_zones_deferred": self._last_cycle_zones_deferred,
            }
        )

    def _coordinator_snapshot(self) -> dict[str, Any]:
        """Return a lightweight immutable-style snapshot for entity updates."""
        return {
            "revision": self._revision,
            "running": self.state.running,
            "current_zone_id": self.state.current_zone_id,
            "last_message": self.state.last_message,
            "fault": self.state.fault,
            "automatic": self._automatic,
            "zone_count": len(self.zones),
        }

    @callback
    def async_notify(self) -> None:
        """Publish a recalculated snapshot to all CoordinatorEntity listeners."""
        self._revision += 1
        self.async_set_updated_data(self._coordinator_snapshot())

    @property
    def automatic(self) -> bool:
        return self._automatic

    async def async_set_automatic(self, enabled: bool) -> None:
        self._automatic = enabled
        await self._save()
        self.async_notify()

    def zone_last_watered(self, zone_id: str) -> str | None:
        return self._last_watered.get(zone_id)

"""Sensor entities for Irrigation Scheduler."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.util import dt as dt_util

from .const import FREQ_EVEN, FREQ_ODD, TRIGGER_NUMERIC
from .entity import IrrigationEntity, IrrigationZoneEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up scheduler and zone sensors."""
    scheduler = entry.runtime_data
    entities = [
        SchedulerStatusSensor(scheduler),
        CurrentZoneSensor(scheduler),
        LastMessageSensor(scheduler),
        ZonesDueSensor(scheduler),
        PossibleDayDurationSensor(
            scheduler,
            parity=FREQ_ODD,
            key="odd_day_possible_duration",
            name="Odd day possible watering",
        ),
        PossibleDayDurationSensor(
            scheduler,
            parity=FREQ_EVEN,
            key="even_day_possible_duration",
            name="Even day possible watering",
        ),
        TonightPlannedDurationSensor(scheduler),
        TonightAvailableDurationSensor(scheduler),
        LastCycleWateringDurationSensor(scheduler),
    ]
    for zone in scheduler.zones:
        entities.extend(
            [
                ZoneStatusSensor(scheduler, zone),
                ZoneMoistureSensor(scheduler, zone),
                ZoneLastWateredSensor(scheduler, zone),
            ]
        )
        if zone.get("moisture_trigger_type") == TRIGGER_NUMERIC:
            entities.append(ZoneDurationAdjustmentSensor(scheduler, zone))
    async_add_entities(entities)


class SchedulerStatusSensor(IrrigationEntity, SensorEntity):
    _attr_icon = "mdi:sprinkler-variant"

    def __init__(self, scheduler):
        super().__init__(scheduler, "status", "Status")

    @property
    def native_value(self):
        if self.scheduler.state.fault:
            return "fault"
        if self.scheduler.state.running:
            return "watering"
        if not self.scheduler.automatic:
            return "disabled"
        return "ready"

    @property
    def extra_state_attributes(self):
        s = self.scheduler.state
        return {
            "current_zone": s.current_zone_name,
            "last_message": s.last_message,
            "zones_completed": s.zones_completed,
            "zones_deferred": s.zones_deferred,
            "current_cycle_watering_minutes": round(
                s.current_cycle_watering_seconds / 60, 2
            ),
            "last_run": s.last_run.isoformat() if s.last_run else None,
        }


class CurrentZoneSensor(IrrigationEntity, SensorEntity):
    _attr_icon = "mdi:map-marker"

    def __init__(self, scheduler):
        super().__init__(scheduler, "current_zone", "Current zone")

    @property
    def native_value(self):
        return self.scheduler.state.current_zone_name or "None"


class LastMessageSensor(IrrigationEntity, SensorEntity):
    _attr_icon = "mdi:message-text-clock"

    def __init__(self, scheduler):
        super().__init__(scheduler, "last_message", "Last message")

    @property
    def native_value(self):
        return self.scheduler.state.last_message


class ZonesDueSensor(IrrigationEntity, SensorEntity):
    """Number of zones currently eligible for an automatic run."""

    _attr_icon = "mdi:counter"

    def __init__(self, scheduler):
        super().__init__(scheduler, "zones_due", "Zones due")

    @property
    def native_value(self):
        return len(self._eligible_zones)

    @property
    def _eligible_zones(self):
        return [
            zone
            for zone in self.scheduler.zones
            if self.scheduler.evaluate_zone(zone).eligible
        ]

    @property
    def extra_state_attributes(self):
        return {
            "eligible_zones": [zone.get("name") for zone in self._eligible_zones],
            "automatic_scheduling": self.scheduler.automatic,
            "calculation": "live eligibility",
        }


class DurationSensor(IrrigationEntity, SensorEntity):
    """Base class for duration measurements expressed in minutes."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1


class PossibleDayDurationSensor(DurationSensor):
    """Maximum configured watering duration for an odd/even day."""

    _attr_icon = "mdi:calendar-clock"

    def __init__(self, scheduler, *, parity: str, key: str, name: str):
        super().__init__(scheduler, key, name)
        self._parity = parity

    @property
    def _summary(self):
        return self.scheduler.possible_duration_for_parity(self._parity)

    @property
    def native_value(self):
        return round(self._summary["watering_minutes"], 2)

    @property
    def extra_state_attributes(self):
        summary = self._summary
        return {
            "day_parity": summary["parity"],
            "cycle_minutes_with_delays": round(summary["cycle_minutes"], 2),
            "inter_zone_delay_minutes": round(
                summary["inter_zone_delay_minutes"], 2
            ),
            "zones": summary["zones"],
            "minimum_interval_zones_included": summary["interval_zones"],
            "definition": (
                "Maximum enabled-zone duration; ignores moisture, rain and "
                "current device availability"
            ),
        }


class TonightPlannedDurationSensor(DurationSensor):
    """Live eligible watering duration for the next automatic cycle."""

    _attr_icon = "mdi:weather-night-partly-cloudy"

    def __init__(self, scheduler):
        super().__init__(
            scheduler, "tonight_planned_duration", "Tonight planned watering"
        )

    @property
    def _plan(self):
        return self.scheduler.next_run_plan()

    @property
    def native_value(self):
        return round(self._plan["watering_minutes"], 2)

    @property
    def extra_state_attributes(self):
        plan = self._plan
        return {
            "planned_start": plan["planned_start"].isoformat(),
            "deadline": plan["deadline"].isoformat(),
            "day_of_year": plan["day_of_year"],
            "day_parity": plan["day_parity"],
            "cutoff_type": plan["cutoff_type"],
            "cycle_minutes_with_delays": round(plan["cycle_minutes"], 2),
            "available_minutes": round(plan["available_minutes"], 2),
            "inter_zone_delay_minutes": round(
                plan["inter_zone_delay_minutes"], 2
            ),
            "exceeds_window": plan["exceeds_window"],
            "eligible_zones": plan["eligible_zones"],
            "zones_expected_to_fit": plan["fitting_zones"],
            "zones_expected_to_be_deferred": plan["deferred_zones"],
            "skipped_zones": plan["skipped_zones"],
            "automatic_scheduling": plan["automatic_scheduling"],
            "calculation": "live conditions projected at the next start time",
        }


class TonightAvailableDurationSensor(DurationSensor):
    """Time available between the next start and its configured cutoff."""

    _attr_icon = "mdi:timer-sand"

    def __init__(self, scheduler):
        super().__init__(
            scheduler, "tonight_available_duration", "Tonight available duration"
        )

    @property
    def _plan(self):
        return self.scheduler.next_run_plan()

    @property
    def native_value(self):
        return round(self._plan["available_minutes"], 2)

    @property
    def extra_state_attributes(self):
        plan = self._plan
        return {
            "planned_start": plan["planned_start"].isoformat(),
            "deadline": plan["deadline"].isoformat(),
            "cutoff_type": plan["cutoff_type"],
            "planned_cycle_minutes": round(plan["cycle_minutes"], 2),
            "exceeds_window": plan["exceeds_window"],
        }


class LastCycleWateringDurationSensor(DurationSensor):
    """Actual valve-open time in the most recent full scheduler cycle."""

    _attr_icon = "mdi:history"

    def __init__(self, scheduler):
        super().__init__(
            scheduler, "last_cycle_watering_duration", "Last cycle watering"
        )

    @property
    def available(self):
        return self.scheduler.last_cycle_summary["started"] is not None

    @property
    def native_value(self):
        return round(self.scheduler.last_cycle_summary["watering_minutes"], 2)

    @property
    def extra_state_attributes(self):
        summary = self.scheduler.last_cycle_summary
        return {
            "started": summary["started"],
            "finished": summary["finished"],
            "result": summary["result"],
            "completed_zones": summary["completed_zones"],
            "zones_deferred": summary["zones_deferred"],
            "definition": (
                "Actual valve-open time from the most recent full scheduler cycle; "
                "individual Run now actions do not replace this value"
            ),
        }


class ZoneStatusSensor(IrrigationZoneEntity, SensorEntity):
    _attr_icon = "mdi:water-check"

    def __init__(self, scheduler, zone):
        super().__init__(scheduler, zone, "status", "Status")

    @property
    def native_value(self):
        if self.scheduler.state.current_zone_id == self.zone["id"]:
            return "watering"
        decision = self.scheduler.evaluate_zone(self.zone)
        return "ready" if decision.eligible else "skipped"

    @property
    def extra_state_attributes(self):
        decision = self.scheduler.evaluate_zone(self.zone)
        return {
            "reason": decision.reason,
            "eligible": decision.eligible,
            "category": self.zone.get("category", "other"),
            "frequency": self.zone.get("frequency", "both"),
            "day_of_year": dt_util.now().timetuple().tm_yday,
            "valve_entity_id": self.zone.get("valve_entity_id"),
            "configured_duration_minutes": self.scheduler._duration_minutes(self.zone),
            "effective_duration_minutes": self.scheduler._effective_duration_minutes(
                self.zone
            ),
            "adaptive_duration": self.scheduler.drying_trend_summary(self.zone),
            "moisture_response_mode": self.zone.get(
                "moisture_response_mode", "selected"
            ),
            "moisture_selected_source": self.scheduler._selected_trigger_entity(
                self.zone
            ),
        }


class ZoneMoistureSensor(IrrigationZoneEntity, SensorEntity):
    _attr_icon = "mdi:water-percent"

    def __init__(self, scheduler, zone):
        super().__init__(scheduler, zone, "moisture", "Moisture")

    @property
    def native_value(self):
        return self.scheduler.zone_moisture_value(self.zone)

    @property
    def _primary_source(self):
        measurements = self.scheduler.moisture_measurements(self.zone)
        selected = self.scheduler._selected_trigger_entity(self.zone)
        if selected in measurements and measurements[selected] is not None:
            return selected
        return next(
            (entity_id for entity_id, value in measurements.items() if value is not None),
            None,
        )

    @property
    def native_unit_of_measurement(self):
        source_id = self._primary_source
        source = self.scheduler.hass.states.get(source_id) if source_id else None
        return source.attributes.get("unit_of_measurement") if source else None

    @property
    def available(self):
        return self.zone_exists and self.native_value is not None

    @property
    def extra_state_attributes(self):
        return {
            "measurements": self.scheduler.moisture_measurements(self.zone),
            "trigger_readings": self.scheduler.moisture_trigger_readings(self.zone),
            "response_mode": self.zone.get("moisture_response_mode", "selected"),
            "selected_source": self.scheduler._selected_trigger_entity(self.zone),
            "primary_display_source": self._primary_source,
        }


class ZoneDurationAdjustmentSensor(IrrigationZoneEntity, SensorEntity):
    """Extra watering minutes currently justified by a drying trend."""

    _attr_icon = "mdi:timer-plus-outline"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, scheduler, zone):
        super().__init__(
            scheduler, zone, "duration_adjustment", "Duration adjustment"
        )

    @property
    def native_value(self):
        return round(
            self.scheduler.drying_trend_summary(self.zone)["boost_minutes"], 2
        )

    @property
    def available(self):
        return self.zone_exists and self.scheduler.drying_trend_summary(self.zone)[
            "supported"
        ]

    @property
    def extra_state_attributes(self):
        summary = self.scheduler.drying_trend_summary(self.zone)
        return {
            **summary,
            "base_duration_minutes": self.scheduler._duration_minutes(self.zone),
            "effective_duration_minutes": self.scheduler._effective_duration_minutes(
                self.zone
            ),
        }


class ZoneLastWateredSensor(IrrigationZoneEntity, SensorEntity):
    _attr_icon = "mdi:clock-check"
    _attr_device_class = "timestamp"

    def __init__(self, scheduler, zone):
        super().__init__(scheduler, zone, "last_watered", "Last watered")

    @property
    def native_value(self):
        value = self.scheduler.zone_last_watered(self.zone["id"])
        return dt_util.parse_datetime(value) if value else None

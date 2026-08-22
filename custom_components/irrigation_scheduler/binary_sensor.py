"""Binary sensor entities for Irrigation Scheduler."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity

from .const import TRIGGER_NONE, TRIGGER_NUMERIC
from .entity import IrrigationEntity, IrrigationZoneEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up scheduler and zone binary sensors."""
    scheduler = entry.runtime_data
    entities = [
        RunningBinarySensor(scheduler),
        RainLockoutBinarySensor(scheduler),
        FaultBinarySensor(scheduler),
        TonightPlanExceedsWindowBinarySensor(scheduler),
    ]
    for zone in scheduler.zones:
        entities.extend(
            [
                ZoneNeedsWaterBinarySensor(scheduler, zone),
                ZoneEligibleBinarySensor(scheduler, zone),
            ]
        )
        if zone.get("moisture_trigger_type") == TRIGGER_NUMERIC:
            entities.append(ZoneDryingTrendBinarySensor(scheduler, zone))
    async_add_entities(entities)


class RunningBinarySensor(IrrigationEntity, BinarySensorEntity):
    """Whether an irrigation run is active."""

    _attr_icon = "mdi:play-circle"

    def __init__(self, scheduler):
        super().__init__(scheduler, "running", "Running")

    @property
    def is_on(self):
        return self.scheduler.state.running


class RainLockoutBinarySensor(IrrigationEntity, BinarySensorEntity):
    """Whether the global rain lockout is active."""

    _attr_icon = "mdi:weather-pouring"

    def __init__(self, scheduler):
        super().__init__(scheduler, "rain_lockout", "Rain lockout")

    @property
    def is_on(self):
        return self.scheduler._rain_lockout_reason() is not None

    @property
    def extra_state_attributes(self):
        return {"reason": self.scheduler._rain_lockout_reason()}


class FaultBinarySensor(IrrigationEntity, BinarySensorEntity):
    """Whether the scheduler has recorded a fault."""

    _attr_device_class = "problem"

    def __init__(self, scheduler):
        super().__init__(scheduler, "fault", "Fault")

    @property
    def is_on(self):
        return self.scheduler.state.fault is not None

    @property
    def extra_state_attributes(self):
        return {"fault": self.scheduler.state.fault}


class TonightPlanExceedsWindowBinarySensor(IrrigationEntity, BinarySensorEntity):
    """Whether the next live plan cannot fit before its cutoff."""

    _attr_device_class = "problem"
    _attr_icon = "mdi:timer-alert-outline"

    def __init__(self, scheduler):
        super().__init__(
            scheduler, "tonight_plan_exceeds_window", "Tonight plan exceeds window"
        )

    @property
    def is_on(self):
        return self.scheduler.next_run_plan()["exceeds_window"]

    @property
    def extra_state_attributes(self):
        plan = self.scheduler.next_run_plan()
        return {
            "planned_start": plan["planned_start"].isoformat(),
            "deadline": plan["deadline"].isoformat(),
            "planned_watering_minutes": round(plan["watering_minutes"], 2),
            "planned_cycle_minutes": round(plan["cycle_minutes"], 2),
            "available_minutes": round(plan["available_minutes"], 2),
            "zones_expected_to_be_deferred": plan["deferred_zones"],
        }


class ZoneNeedsWaterBinarySensor(IrrigationZoneEntity, BinarySensorEntity):
    """Moisture demand only, independent of rain and schedule eligibility."""

    _attr_icon = "mdi:water-alert"

    def __init__(self, scheduler, zone):
        super().__init__(scheduler, zone, "needs_water", "Needs water")

    @property
    def is_on(self):
        if self.zone.get("moisture_trigger_type", TRIGGER_NONE) == TRIGGER_NONE:
            return False
        return self.scheduler.moisture_decision(self.zone).eligible

    @property
    def extra_state_attributes(self):
        decision = self.scheduler.moisture_decision(self.zone)
        return {
            "reason": decision.reason,
            "measurement": decision.moisture_value,
            "measurements": self.scheduler.moisture_measurements(self.zone),
            "trigger_readings": self.scheduler.moisture_trigger_readings(self.zone),
            "trigger_type": self.zone.get("moisture_trigger_type", TRIGGER_NONE),
            "response_mode": self.zone.get("moisture_response_mode", "selected"),
            "selected_source": self.scheduler._selected_trigger_entity(self.zone),
            "current_zone": self.scheduler.state.current_zone_id == self.zone["id"],
        }


class ZoneEligibleBinarySensor(IrrigationZoneEntity, BinarySensorEntity):
    """Full automatic-run eligibility after all configured rules."""

    _attr_icon = "mdi:check-decagram-outline"

    def __init__(self, scheduler, zone):
        super().__init__(scheduler, zone, "eligible", "Eligible")

    @property
    def is_on(self):
        return self.scheduler.evaluate_zone(self.zone).eligible

    @property
    def extra_state_attributes(self):
        decision = self.scheduler.evaluate_zone(self.zone)
        return {
            "reason": decision.reason,
            "measurement": decision.moisture_value,
            "response_mode": self.zone.get("moisture_response_mode", "selected"),
            "selected_source": self.scheduler._selected_trigger_entity(self.zone),
        }


class ZoneDryingTrendBinarySensor(IrrigationZoneEntity, BinarySensorEntity):
    """Whether completed daily minima show a qualifying drying trend."""

    _attr_icon = "mdi:chart-line-variant"

    def __init__(self, scheduler, zone):
        super().__init__(scheduler, zone, "drying_trend", "Drying trend")

    @property
    def is_on(self):
        return self.scheduler.drying_trend_summary(self.zone)["active"]

    @property
    def available(self):
        return self.zone_exists and self.scheduler.drying_trend_summary(self.zone)["supported"]

    @property
    def extra_state_attributes(self):
        return self.scheduler.drying_trend_summary(self.zone)

"""Switch entities for Irrigation Scheduler."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity

from .const import TRIGGER_NUMERIC
from .entity import IrrigationEntity, IrrigationZoneEntity


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up scheduler control switches."""
    scheduler = entry.runtime_data
    entities = [
        AutomaticSwitch(scheduler),
        WateringControlSwitch(scheduler),
    ]
    for zone in scheduler.zones:
        entities.append(ZoneEnabledSwitch(scheduler, zone))
        if zone.get("moisture_trigger_type") == TRIGGER_NUMERIC:
            entities.append(ZoneAdaptiveDurationSwitch(scheduler, zone))
    async_add_entities(entities)


class AutomaticSwitch(IrrigationEntity, SwitchEntity):
    """Enable or disable automatic scheduled starts."""

    _attr_icon = "mdi:water-sync"

    def __init__(self, scheduler):
        super().__init__(scheduler, "automatic", "Automatic scheduling")

    @property
    def is_on(self):
        return self.scheduler.automatic

    async def async_turn_on(self, **kwargs):
        await self.scheduler.async_set_automatic(True)

    async def async_turn_off(self, **kwargs):
        await self.scheduler.async_set_automatic(False)


class WateringControlSwitch(IrrigationEntity, SwitchEntity):
    """Start the scheduler or stop an active irrigation run."""

    _attr_icon = "mdi:water-pump"

    def __init__(self, scheduler):
        super().__init__(scheduler, "watering", "Watering")

    @property
    def is_on(self):
        return self.scheduler.state.running

    async def async_turn_on(self, **kwargs):
        self.scheduler.start_run()

    async def async_turn_off(self, **kwargs):
        await self.scheduler.async_stop("Stopped from watering switch")


class ZoneEnabledSwitch(IrrigationZoneEntity, SwitchEntity):
    """Enable or disable one zone for automatic scheduling."""

    _attr_icon = "mdi:water-check"

    def __init__(self, scheduler, zone):
        super().__init__(scheduler, zone, "enabled", "Enabled")

    @property
    def is_on(self):
        return bool(self.zone.get("enabled", True))

    @property
    def available(self):
        return self.zone_exists

    async def async_turn_on(self, **kwargs):
        await self.scheduler.async_update_zone_setting(
            self.zone["id"], "enabled", True
        )

    async def async_turn_off(self, **kwargs):
        await self.scheduler.async_update_zone_setting(
            self.zone["id"], "enabled", False
        )


class ZoneAdaptiveDurationSwitch(IrrigationZoneEntity, SwitchEntity):
    """Enable or disable drying-trend duration adjustment for one zone."""

    _attr_icon = "mdi:chart-line-variant"

    def __init__(self, scheduler, zone):
        super().__init__(scheduler, zone, "adaptive_duration", "Adaptive duration")

    @property
    def is_on(self):
        return bool(self.zone.get("adaptive_duration_enabled", False))

    @property
    def available(self):
        return self.zone_exists and self.zone.get("moisture_trigger_type") == TRIGGER_NUMERIC

    async def async_turn_on(self, **kwargs):
        await self.scheduler.async_update_zone_setting(
            self.zone_id, "adaptive_duration_enabled", True
        )

    async def async_turn_off(self, **kwargs):
        await self.scheduler.async_update_zone_setting(
            self.zone_id, "adaptive_duration_enabled", False
        )

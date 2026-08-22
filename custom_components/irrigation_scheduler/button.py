"""Button entities for Irrigation Scheduler."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .entity import IrrigationEntity, IrrigationZoneEntity


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up scheduler and per-zone buttons."""
    scheduler = entry.runtime_data
    entities = [RunButton(scheduler), StopButton(scheduler)]
    for zone in scheduler.zones:
        entities.extend(
            [
                ZoneRunButton(scheduler, zone),
                ZoneStopButton(scheduler, zone),
            ]
        )
    async_add_entities(entities)


class RunButton(IrrigationEntity, ButtonEntity):
    """Start the automatic scheduler without blocking the service call."""

    _attr_icon = "mdi:play-circle"

    def __init__(self, scheduler):
        super().__init__(scheduler, "run", "Run scheduler")

    @property
    def available(self):
        return not self.scheduler.state.running

    async def async_press(self):
        self.scheduler.start_run()


class StopButton(IrrigationEntity, ButtonEntity):
    """Stop the active run and close every configured actuator."""

    _attr_icon = "mdi:stop-circle"

    def __init__(self, scheduler):
        super().__init__(scheduler, "stop", "Stop all")

    @property
    def available(self):
        return self.scheduler.state.running

    async def async_press(self):
        await self.scheduler.async_stop("Stopped from button")


class ZoneRunButton(IrrigationZoneEntity, ButtonEntity):
    """Start one zone manually."""

    _attr_icon = "mdi:play"

    def __init__(self, scheduler, zone):
        super().__init__(scheduler, zone, "run", "Run now")

    @property
    def available(self):
        return not self.scheduler.state.running

    async def async_press(self):
        self.scheduler.start_zone(self.zone["id"], force=True)


class ZoneStopButton(IrrigationZoneEntity, ButtonEntity):
    """Stop watering from the active zone device."""

    _attr_icon = "mdi:stop"

    def __init__(self, scheduler, zone):
        super().__init__(scheduler, zone, "stop", "Stop watering")

    @property
    def available(self):
        return (
            self.scheduler.state.running
            and self.scheduler.state.current_zone_id == self.zone["id"]
        )

    async def async_press(self):
        await self.scheduler.async_stop(f"Stopped from {self.zone['name']}")

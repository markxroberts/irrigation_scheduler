"""Select entities for Irrigation Scheduler."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity

from .const import (
    MOISTURE_MODE_ALL,
    MOISTURE_MODE_ANY,
    MOISTURE_MODE_AVERAGE,
    MOISTURE_MODE_MINIMUM,
    MOISTURE_MODE_SELECTED,
    TRIGGER_BINARY,
    TRIGGER_NUMERIC,
)
from .entity import IrrigationZoneEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up per-zone moisture response selectors."""
    scheduler = entry.runtime_data
    entities: list[SelectEntity] = []
    for zone in scheduler.zones:
        if len(scheduler._moisture_source_entity_ids(zone)) <= 1:
            continue
        if zone.get("moisture_trigger_type") not in (TRIGGER_NUMERIC, TRIGGER_BINARY):
            continue
        entities.extend(
            [
                ZoneMoistureResponseModeSelect(scheduler, zone),
                ZoneMoistureSourceSelect(scheduler, zone),
            ]
        )
    async_add_entities(entities)


class ZoneMoistureResponseModeSelect(IrrigationZoneEntity, SelectEntity):
    """Choose how several moisture sources are combined."""

    _attr_icon = "mdi:call-merge"

    def __init__(self, scheduler, zone):
        super().__init__(scheduler, zone, "moisture_response_mode", "Moisture response")

    @property
    def options(self) -> list[str]:
        if self.zone.get("moisture_trigger_type") == TRIGGER_NUMERIC:
            return [
                MOISTURE_MODE_SELECTED,
                MOISTURE_MODE_ANY,
                MOISTURE_MODE_ALL,
                MOISTURE_MODE_AVERAGE,
                MOISTURE_MODE_MINIMUM,
            ]
        return [MOISTURE_MODE_SELECTED, MOISTURE_MODE_ANY, MOISTURE_MODE_ALL]

    @property
    def current_option(self) -> str:
        configured = self.zone.get("moisture_response_mode", MOISTURE_MODE_SELECTED)
        return configured if configured in self.options else MOISTURE_MODE_SELECTED

    @property
    def available(self) -> bool:
        return (
            self.zone_exists
            and len(self.scheduler._moisture_source_entity_ids(self.zone)) > 1
        )

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"Unsupported moisture response mode: {option}")
        await self.scheduler.async_update_zone_setting(
            self.zone_id, "moisture_response_mode", option
        )


class ZoneMoistureSourceSelect(IrrigationZoneEntity, SelectEntity):
    """Choose the active moisture source in Selected sensor mode."""

    _attr_icon = "mdi:water-percent"

    def __init__(self, scheduler, zone):
        super().__init__(scheduler, zone, "moisture_source", "Moisture source")

    @property
    def options(self) -> list[str]:
        return self.scheduler._moisture_source_entity_ids(self.zone)

    @property
    def current_option(self) -> str | None:
        return self.scheduler._selected_trigger_entity(self.zone)

    @property
    def available(self) -> bool:
        return (
            self.zone_exists
            and len(self.options) > 1
            and self.zone.get("moisture_response_mode", MOISTURE_MODE_SELECTED)
            == MOISTURE_MODE_SELECTED
        )

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"Unknown moisture source: {option}")
        await self.scheduler.async_update_zone_setting(
            self.zone_id, "moisture_selected_entity_id", option
        )

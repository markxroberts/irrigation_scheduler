"""Number entities for Irrigation Scheduler."""
from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTime
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DEFAULT_MAX_RUNTIME,
    DEFAULT_TREND_BOOST_PER_DAY,
    DEFAULT_TREND_DAYS,
    DEFAULT_TREND_MAX_BOOST,
    DEFAULT_TREND_MIN_DROP,
    TRIGGER_NUMERIC,
)
from .entity import IrrigationZoneEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up zone number entities."""
    scheduler = entry.runtime_data
    entities: list[NumberEntity] = []
    for zone in scheduler.zones:
        entities.extend(
            [
                ZoneDurationNumber(
                    scheduler,
                    zone,
                ),
                ZoneConfigNumber(
                    scheduler,
                    zone,
                    key="minimum_interval",
                    name="Minimum interval",
                    config_key="minimum_interval_hours",
                    minimum=0,
                    maximum=720,
                    step=1,
                    unit=UnitOfTime.HOURS,
                    fallback=24,
                ),
                ZoneConfigNumber(
                    scheduler,
                    zone,
                    key="order",
                    name="Order",
                    config_key="order",
                    minimum=1,
                    maximum=999,
                    step=1,
                    unit=None,
                    fallback=999,
                ),
                ZoneConfigNumber(
                    scheduler,
                    zone,
                    key="maximum_runtime",
                    name="Maximum runtime",
                    config_key="max_runtime",
                    minimum=1,
                    maximum=360,
                    step=1,
                    unit=UnitOfTime.MINUTES,
                    fallback=DEFAULT_MAX_RUNTIME,
                ),
            ]
        )
        if zone.get("moisture_trigger_type") == TRIGGER_NUMERIC:
            entities.extend(
                [
                    ZoneConfigNumber(
                        scheduler,
                        zone,
                        key="moisture_below",
                        name="Water below",
                        config_key="moisture_below",
                        minimum=-1000,
                        maximum=1000,
                        step=0.1,
                        unit=None,
                        fallback=30,
                    ),
                    ZoneConfigNumber(
                        scheduler,
                        zone,
                        key="moisture_reset_above",
                        name="Reset above",
                        config_key="moisture_reset_above",
                        minimum=-1000,
                        maximum=1000,
                        step=0.1,
                        unit=None,
                        fallback=35,
                    ),
                    ZoneConfigNumber(
                        scheduler,
                        zone,
                        key="adaptive_trend_days",
                        name="Drying trend days",
                        config_key="adaptive_trend_days",
                        minimum=2,
                        maximum=7,
                        step=1,
                        unit=None,
                        fallback=DEFAULT_TREND_DAYS,
                    ),
                    ZoneConfigNumber(
                        scheduler,
                        zone,
                        key="adaptive_min_drop",
                        name="Minimum daily moisture drop",
                        config_key="adaptive_min_drop",
                        minimum=0,
                        maximum=1000,
                        step=0.1,
                        unit=None,
                        fallback=DEFAULT_TREND_MIN_DROP,
                    ),
                    ZoneConfigNumber(
                        scheduler,
                        zone,
                        key="adaptive_boost_per_day",
                        name="Extra time per declining day",
                        config_key="adaptive_boost_per_day",
                        minimum=0,
                        maximum=120,
                        step=1,
                        unit=UnitOfTime.MINUTES,
                        fallback=DEFAULT_TREND_BOOST_PER_DAY,
                    ),
                    ZoneConfigNumber(
                        scheduler,
                        zone,
                        key="adaptive_max_boost",
                        name="Maximum extra watering",
                        config_key="adaptive_max_boost",
                        minimum=0,
                        maximum=240,
                        step=1,
                        unit=UnitOfTime.MINUTES,
                        fallback=DEFAULT_TREND_MAX_BOOST,
                    ),
                ]
            )
    async_add_entities(entities)


class ZoneDurationNumber(IrrigationZoneEntity, NumberEntity):
    """Effective watering duration, optionally backed by another HA entity."""

    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = 360
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, scheduler, zone: dict[str, Any]) -> None:
        # Keep the old unique-id key so existing Fixed duration registry entries
        # are renamed rather than duplicated.
        super().__init__(scheduler, zone, "fixed_duration", "Watering duration")

    @property
    def _source_entity_id(self) -> str | None:
        # The external duration number is optional.  If configured, this
        # integration entity transparently mirrors and writes through to it.
        # Otherwise it uses the integration-stored fixed_duration value.
        return self.zone.get("duration_entity_id") or None

    @property
    def native_value(self) -> float:
        source = self._source_entity_id
        value = self.scheduler._numeric_state(source) if source else None
        return self.scheduler._safe_number(
            value if value is not None else self.zone.get("fixed_duration"), 10
        )

    @property
    def available(self) -> bool:
        if not self.zone_exists:
            return False
        source = self._source_entity_id
        if not source:
            return True
        state = self.hass.states.get(source)
        return state is not None and state.state not in ("unknown", "unavailable")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        source = self._source_entity_id
        return {
            "duration_source_entity": source,
            "duration_source": "entity" if source else "integration",
        }

    async def async_set_native_value(self, value: float) -> None:
        source = self._source_entity_id
        if not source:
            await self.scheduler.async_update_zone_value(
                self.zone["id"], "fixed_duration", value
            )
            return

        domain = source.split(".", 1)[0]
        if domain == "number":
            await self.hass.services.async_call(
                "number", "set_value",
                {"entity_id": source, "value": value},
                blocking=True,
            )
            return
        if domain == "input_number":
            await self.hass.services.async_call(
                "input_number", "set_value",
                {"entity_id": source, "value": value},
                blocking=True,
            )
            return
        raise HomeAssistantError(
            f"Configured duration source {source} is read-only; select a number or input_number entity"
        )


class ZoneConfigNumber(IrrigationZoneEntity, NumberEntity):
    """Editable numeric configuration for an irrigation zone."""

    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        scheduler,
        zone: dict[str, Any],
        *,
        key: str,
        name: str,
        config_key: str,
        minimum: float,
        maximum: float,
        step: float,
        unit: str | None,
        fallback: float,
    ) -> None:
        super().__init__(scheduler, zone, key, name)
        self._config_key = config_key
        self._fallback = fallback
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self) -> float:
        """Return the current configured value."""
        return self.scheduler._safe_number(self.zone.get(self._config_key), self._fallback)

    @property
    def available(self) -> bool:
        """Configuration numbers are available whenever their zone exists."""
        return self.zone_exists

    async def async_set_native_value(self, value: float) -> None:
        """Persist a new zone configuration value."""
        await self.scheduler.async_update_zone_value(
            self.zone["id"], self._config_key, value
        )

"""Entity helpers for Irrigation Scheduler."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IrrigationSchedulerCoordinator


class IrrigationEntity(CoordinatorEntity[IrrigationSchedulerCoordinator]):
    """Base scheduler entity backed by the shared coordinator."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, coordinator: IrrigationSchedulerCoordinator, key: str, name: str
    ) -> None:
        super().__init__(coordinator)
        # Keep the established attribute name so platform code and unique IDs
        # remain unchanged while all updates are delivered by CoordinatorEntity.
        self.scheduler = coordinator
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.entry.title,
            manufacturer="Irrigation Scheduler",
            model="Shared supply scheduler",
        )


class IrrigationZoneEntity(IrrigationEntity):
    """Base coordinator entity attached to one configured zone."""

    def __init__(
        self,
        coordinator: IrrigationSchedulerCoordinator,
        zone: dict,
        key: str,
        name: str,
    ) -> None:
        super().__init__(coordinator, f"{zone['id']}_{key}", name)
        self._zone_id = zone["id"]
        self._zone_snapshot = dict(zone)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_{zone['id']}")},
            name=zone["name"],
            manufacturer="Irrigation Scheduler",
            model="Irrigation zone",
            via_device_id=coordinator.parent_device_id,
        )

    @property
    def zone(self) -> dict:
        """Return the latest stored configuration for this zone."""
        return self.scheduler.get_zone(self._zone_id) or self._zone_snapshot

    @property
    def zone_id(self) -> str:
        """Return the stable zone identifier."""
        return self._zone_id

    @property
    def zone_exists(self) -> bool:
        """Return whether the zone is still configured."""
        return self.scheduler.get_zone(self._zone_id) is not None

"""Irrigation Scheduler integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS
from .coordinator import IrrigationSchedulerCoordinator

type IrrigationSchedulerConfigEntry = ConfigEntry[IrrigationSchedulerCoordinator]


def _loaded_coordinator(hass: HomeAssistant) -> IrrigationSchedulerCoordinator:
    """Return the single loaded scheduler coordinator."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED and entry.runtime_data is not None:
            return entry.runtime_data
    raise ServiceValidationError("Irrigation Scheduler is not loaded")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration service actions once for the domain."""

    async def _async_run(call: ServiceCall) -> None:
        _loaded_coordinator(hass).start_run(
            force=bool(call.data.get("force", False))
        )

    async def _async_stop(call: ServiceCall) -> None:
        await _loaded_coordinator(hass).async_stop("Stopped by service")

    async def _async_run_zone(call: ServiceCall) -> None:
        _loaded_coordinator(hass).start_zone(
            call.data["zone_id"],
            force=bool(call.data.get("force", True)),
            duration_override=call.data.get("duration"),
        )

    hass.services.async_register(DOMAIN, "run", _async_run)
    hass.services.async_register(DOMAIN, "stop", _async_stop)
    hass.services.async_register(DOMAIN, "run_zone", _async_run_zone)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: IrrigationSchedulerConfigEntry
) -> bool:
    """Set up Irrigation Scheduler from a config entry."""
    coordinator = IrrigationSchedulerCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: IrrigationSchedulerConfigEntry
) -> bool:
    """Unload an Irrigation Scheduler config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_shutdown()
    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: IrrigationSchedulerConfigEntry
) -> None:
    """Reload after structural UI options changes."""
    coordinator = entry.runtime_data
    if coordinator.consume_suppressed_reload():
        return
    await hass.config_entries.async_reload(entry.entry_id)

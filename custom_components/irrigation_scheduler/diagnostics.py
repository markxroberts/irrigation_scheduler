"""Diagnostics for Irrigation Scheduler."""
from homeassistant.components.diagnostics import async_redact_data

TO_REDACT = {
    "entity_id",
    "valve_entity_id",
    "linked_entity_id",
    "duration_entity_id",
    "moisture_measurement_entity_id",
    "moisture_trigger_entity_id",
    "moisture_measurement_entity_ids",
    "moisture_trigger_entity_ids",
    "moisture_selected_entity_id",
}


async def async_get_config_entry_diagnostics(hass, entry):
    """Return redacted scheduler configuration, runtime and plan diagnostics."""
    scheduler = entry.runtime_data
    plan = scheduler.next_run_plan()
    return async_redact_data(
        {
            "config": {**entry.data, **entry.options},
            "coordinator": {
                "last_update_success": scheduler.last_update_success,
                "data": scheduler.data,
                "update_interval_seconds": (
                    scheduler.update_interval.total_seconds()
                    if scheduler.update_interval
                    else None
                ),
            },
            "runtime": {
                "running": scheduler.state.running,
                "current_zone_id": scheduler.state.current_zone_id,
                "last_message": scheduler.state.last_message,
                "zones_due": scheduler.state.zones_due,
                "zones_completed": scheduler.state.zones_completed,
                "zones_deferred": scheduler.state.zones_deferred,
                "current_cycle_watering_minutes": (
                    scheduler.state.current_cycle_watering_seconds / 60
                ),
                "fault": scheduler.state.fault,
            },
            "next_run_plan": {
                **plan,
                "planned_start": plan["planned_start"].isoformat(),
                "deadline": plan["deadline"].isoformat(),
            },
            "last_cycle": scheduler.last_cycle_summary,
            "odd_day_possible": scheduler.possible_duration_for_parity("odd"),
            "even_day_possible": scheduler.possible_duration_for_parity("even"),
        },
        TO_REDACT,
    )

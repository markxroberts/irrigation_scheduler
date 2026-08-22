"""Config flow for Irrigation Scheduler."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_ENTITY_ID
from homeassistant.helpers import selector

from .const import (
    CONF_HOLIDAY_CUTOFF,
    CONF_HOLIDAY_ENTITY,
    CONF_INTER_ZONE_DELAY,
    CONF_MAX_RUNTIME,
    CONF_NAME,
    CONF_RAIN_TODAY_ENTITY,
    CONF_RAIN_TODAY_LIMIT,
    CONF_RAIN_YESTERDAY_ENTITY,
    CONF_RAIN_YESTERDAY_LIMIT,
    CONF_START_TIME,
    CONF_WEEKDAY_CUTOFF,
    CONF_WEEKEND_CUTOFF,
    CONF_ZONES,
    CONF_CATEGORY,
    DEFAULT_HOLIDAY_CUTOFF,
    DEFAULT_INTER_ZONE_DELAY,
    DEFAULT_MAX_RUNTIME,
    DEFAULT_NAME,
    DEFAULT_RAIN_TODAY_LIMIT,
    DEFAULT_RAIN_YESTERDAY_LIMIT,
    DEFAULT_START_TIME,
    DEFAULT_WEEKDAY_CUTOFF,
    DEFAULT_WEEKEND_CUTOFF,
    DOMAIN,
    DURATION_FIXED,
    DURATION_ENTITY,
    FALLBACK_IGNORE,
    FALLBACK_SKIP,
    FALLBACK_WATER,
    FREQ_DAILY,
    FREQ_BOTH,
    FREQ_EVEN,
    FREQ_INTERVAL,
    FREQ_ODD,
    TRIGGER_BINARY,
    TRIGGER_NONE,
    TRIGGER_NUMERIC,
    CATEGORY_POTS,
    CATEGORY_GREENHOUSE,
    CATEGORY_BORDER,
    CATEGORY_LAWN,
    CATEGORY_OTHER,
    MOISTURE_MODE_SELECTED,
    MOISTURE_MODE_ANY,
    MOISTURE_MODE_ALL,
    MOISTURE_MODE_AVERAGE,
    MOISTURE_MODE_MINIMUM,
    DEFAULT_TREND_DAYS,
    DEFAULT_TREND_MIN_DROP,
    DEFAULT_TREND_BOOST_PER_DAY,
    DEFAULT_TREND_MAX_BOOST,
)


def _optional_entity(domain: str | list[str] | None = None):
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))



def _optional_entities(domain: str | list[str] | None = None):
    """Return a multi-entity selector for optional moisture sources."""
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=domain, multiple=True)
    )


def _as_entity_list(value) -> list[str]:
    """Normalise legacy single entity values and new multi-select values."""
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []

def _entity_field(
    key: str,
    *,
    required: bool = False,
    suggested_value: str | None = None,
):
    """Create an entity-selector schema key without an invalid empty default.

    Entity selectors reject an empty string as neither an entity ID nor a UUID.
    A suggested value pre-fills an existing entity while still allowing an
    optional field to be cleared.
    """
    marker = vol.Required if required else vol.Optional
    if isinstance(suggested_value, str) and suggested_value.strip():
        return marker(key, description={"suggested_value": suggested_value})
    return marker(key)


def _entities_field(
    key: str,
    *,
    required: bool = False,
    suggested_value: list[str] | None = None,
):
    """Create a multi-entity selector key with an optional suggested list."""
    marker = vol.Required if required else vol.Optional
    values = _as_entity_list(suggested_value)
    if values:
        return marker(key, description={"suggested_value": values})
    return marker(key)


def _remove_empty_entity_values(data: dict, keys: tuple[str, ...]) -> dict:
    """Return a copy with blank optional entity references removed."""
    cleaned = dict(data)
    for key in keys:
        if not cleaned.get(key):
            cleaned.pop(key, None)
    return cleaned


def _number_default(value, fallback, *, integer=False):
    """Return a valid numeric selector default for legacy/null option data."""
    try:
        if value is None or value == "":
            raise ValueError
        number = float(value)
    except (TypeError, ValueError):
        number = float(fallback)
    return int(number) if integer else number


def _normalise_zone(zone: dict, default_order: int) -> dict:
    """Normalise zones created by earlier releases."""
    item = deepcopy(zone)
    item.setdefault("id", uuid4().hex)
    item.setdefault("name", "Irrigation zone")
    item.setdefault(CONF_CATEGORY, CATEGORY_OTHER)
    item = _remove_empty_entity_values(
        item,
        (
            "linked_entity_id",
            "duration_entity_id",
            "moisture_measurement_entity_id",
            "moisture_trigger_entity_id",
        ),
    )
    frequency = item.get("frequency")
    item["frequency"] = FREQ_BOTH if frequency in (None, "", FREQ_DAILY) else frequency
    item["fixed_duration"] = _number_default(item.get("fixed_duration"), 10)
    # Every zone owns an integration Watering duration number.  When an
    # external number is selected it backs/proxies that number; otherwise
    # fixed_duration is the integration-stored value.  Retain this legacy
    # marker only for backwards compatibility with 0.1.6 option data.
    item["duration_source"] = DURATION_ENTITY if item.get("duration_entity_id") else DURATION_FIXED
    item["minimum_interval_hours"] = _number_default(item.get("minimum_interval_hours"), 24, integer=True)
    item["order"] = _number_default(item.get("order"), default_order, integer=True)
    item["max_runtime"] = _number_default(item.get("max_runtime"), DEFAULT_MAX_RUNTIME, integer=True)
    item["moisture_below"] = _number_default(item.get("moisture_below"), 30)
    item["moisture_reset_above"] = _number_default(item.get("moisture_reset_above"), 35)

    measurement_ids = _as_entity_list(item.get("moisture_measurement_entity_ids"))
    if not measurement_ids:
        measurement_ids = _as_entity_list(item.get("moisture_measurement_entity_id"))
    trigger_ids = _as_entity_list(item.get("moisture_trigger_entity_ids"))
    if not trigger_ids:
        trigger_ids = _as_entity_list(item.get("moisture_trigger_entity_id"))

    # Numeric moisture control uses the measurement sensors directly.  v0.1.14
    # briefly asked for a second list of "numeric trigger sensors"; merge those
    # legacy selections into the measurement list so no configured probe is lost.
    trigger_type = item.get("moisture_trigger_type", TRIGGER_NONE)
    if trigger_type == TRIGGER_NUMERIC:
        measurement_ids = list(dict.fromkeys([*measurement_ids, *trigger_ids]))
        trigger_ids = []
        item.pop("moisture_trigger_state", None)
    elif trigger_type != TRIGGER_BINARY:
        trigger_ids = []

    item["moisture_measurement_entity_ids"] = measurement_ids
    item["moisture_trigger_entity_ids"] = trigger_ids
    item.pop("moisture_measurement_entity_id", None)
    item.pop("moisture_trigger_entity_id", None)

    source_ids = measurement_ids if trigger_type == TRIGGER_NUMERIC else trigger_ids
    default_mode = MOISTURE_MODE_SELECTED if len(source_ids) <= 1 else MOISTURE_MODE_ANY
    item.setdefault("moisture_response_mode", default_mode)
    selected = item.get("moisture_selected_entity_id")
    if selected not in source_ids:
        item["moisture_selected_entity_id"] = source_ids[0] if source_ids else None

    item.setdefault("adaptive_duration_enabled", False)
    item.setdefault("adaptive_notify", True)
    item["adaptive_trend_days"] = _number_default(
        item.get("adaptive_trend_days"), DEFAULT_TREND_DAYS, integer=True
    )
    item["adaptive_min_drop"] = _number_default(
        item.get("adaptive_min_drop"), DEFAULT_TREND_MIN_DROP
    )
    item["adaptive_boost_per_day"] = _number_default(
        item.get("adaptive_boost_per_day"), DEFAULT_TREND_BOOST_PER_DAY
    )
    item["adaptive_max_boost"] = _number_default(
        item.get("adaptive_max_boost"), DEFAULT_TREND_MAX_BOOST
    )
    return item


class IrrigationSchedulerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial config flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Create the parent scheduler."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors = {}
        if user_input is not None:
            for key in (CONF_RAIN_TODAY_ENTITY, CONF_RAIN_YESTERDAY_ENTITY):
                if not user_input.get(key):
                    errors[key] = "entity_required"
            if errors:
                return self.async_show_form(
                    step_id="user", data_schema=self._user_schema(user_input), errors=errors
                )
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        return self.async_show_form(step_id="user", data_schema=self._user_schema())

    def _user_schema(self, values=None):
        values = values or {}
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default=values.get(CONF_NAME, DEFAULT_NAME)): str,
                vol.Required(CONF_START_TIME, default=values.get(CONF_START_TIME, DEFAULT_START_TIME)): selector.TimeSelector(),
                vol.Required(CONF_WEEKDAY_CUTOFF, default=values.get(CONF_WEEKDAY_CUTOFF, DEFAULT_WEEKDAY_CUTOFF)): selector.TimeSelector(),
                vol.Required(CONF_WEEKEND_CUTOFF, default=values.get(CONF_WEEKEND_CUTOFF, DEFAULT_WEEKEND_CUTOFF)): selector.TimeSelector(),
                vol.Required(CONF_HOLIDAY_CUTOFF, default=values.get(CONF_HOLIDAY_CUTOFF, DEFAULT_HOLIDAY_CUTOFF)): selector.TimeSelector(),
                _entity_field(CONF_HOLIDAY_ENTITY, suggested_value=values.get(CONF_HOLIDAY_ENTITY)): _optional_entity("input_boolean"),
                _entity_field(CONF_RAIN_TODAY_ENTITY, required=True, suggested_value=values.get(CONF_RAIN_TODAY_ENTITY)): _optional_entity("sensor"),
                vol.Required(CONF_RAIN_TODAY_LIMIT, default=values.get(CONF_RAIN_TODAY_LIMIT, DEFAULT_RAIN_TODAY_LIMIT)): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=100, step=0.1, mode=selector.NumberSelectorMode.BOX)),
                _entity_field(CONF_RAIN_YESTERDAY_ENTITY, required=True, suggested_value=values.get(CONF_RAIN_YESTERDAY_ENTITY)): _optional_entity("sensor"),
                vol.Required(CONF_RAIN_YESTERDAY_LIMIT, default=values.get(CONF_RAIN_YESTERDAY_LIMIT, DEFAULT_RAIN_YESTERDAY_LIMIT)): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=100, step=0.1, mode=selector.NumberSelectorMode.BOX)),
                vol.Required(CONF_INTER_ZONE_DELAY, default=values.get(CONF_INTER_ZONE_DELAY, DEFAULT_INTER_ZONE_DELAY)): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=300, step=1, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)),
                vol.Required(CONF_MAX_RUNTIME, default=values.get(CONF_MAX_RUNTIME, DEFAULT_MAX_RUNTIME)): selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=360, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)),
            }
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return IrrigationOptionsFlow()


class IrrigationOptionsFlow(config_entries.OptionsFlow):
    """Manage global settings and a dynamic list of zones."""

    def __init__(self) -> None:
        self._zones = None
        self._working_zone = None
        self._edit_id = None
        self._remove_id = None

    def _ensure_initialized(self) -> None:
        """Initialise mutable flow state after Home Assistant attaches the entry."""
        if self._zones is None:
            saved = self.config_entry.options.get(CONF_ZONES, [])
            self._zones = [_normalise_zone(zone, index + 1) for index, zone in enumerate(saved)]

    async def async_step_init(self, user_input=None):
        self._ensure_initialized()
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "add_zone", "edit_zone", "remove_zone"],
        )

    async def async_step_general(self, user_input=None):
        self._ensure_initialized()
        current = {**self.config_entry.data, **self.config_entry.options}
        errors = {}
        if user_input is not None:
            for key in (CONF_RAIN_TODAY_ENTITY, CONF_RAIN_YESTERDAY_ENTITY):
                if not user_input.get(key):
                    errors[key] = "entity_required"
            if not errors:
                options = dict(self.config_entry.options)
                options.update(user_input)
                if not user_input.get(CONF_HOLIDAY_ENTITY):
                    options.pop(CONF_HOLIDAY_ENTITY, None)
                options[CONF_ZONES] = self._zones
                return self.async_create_entry(title="", data=options)
            current.update(user_input)
        schema = vol.Schema(
            {
                vol.Required(CONF_START_TIME, default=current.get(CONF_START_TIME, DEFAULT_START_TIME)): selector.TimeSelector(),
                vol.Required(CONF_WEEKDAY_CUTOFF, default=current.get(CONF_WEEKDAY_CUTOFF, DEFAULT_WEEKDAY_CUTOFF)): selector.TimeSelector(),
                vol.Required(CONF_WEEKEND_CUTOFF, default=current.get(CONF_WEEKEND_CUTOFF, DEFAULT_WEEKEND_CUTOFF)): selector.TimeSelector(),
                vol.Required(CONF_HOLIDAY_CUTOFF, default=current.get(CONF_HOLIDAY_CUTOFF, DEFAULT_HOLIDAY_CUTOFF)): selector.TimeSelector(),
                _entity_field(CONF_HOLIDAY_ENTITY, suggested_value=current.get(CONF_HOLIDAY_ENTITY)): _optional_entity("input_boolean"),
                _entity_field(CONF_RAIN_TODAY_ENTITY, required=True, suggested_value=current.get(CONF_RAIN_TODAY_ENTITY)): _optional_entity("sensor"),
                vol.Required(CONF_RAIN_TODAY_LIMIT, default=current.get(CONF_RAIN_TODAY_LIMIT, DEFAULT_RAIN_TODAY_LIMIT)): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=100, step=0.1, mode=selector.NumberSelectorMode.BOX)),
                _entity_field(CONF_RAIN_YESTERDAY_ENTITY, required=True, suggested_value=current.get(CONF_RAIN_YESTERDAY_ENTITY)): _optional_entity("sensor"),
                vol.Required(CONF_RAIN_YESTERDAY_LIMIT, default=current.get(CONF_RAIN_YESTERDAY_LIMIT, DEFAULT_RAIN_YESTERDAY_LIMIT)): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=100, step=0.1, mode=selector.NumberSelectorMode.BOX)),
                vol.Required(CONF_INTER_ZONE_DELAY, default=current.get(CONF_INTER_ZONE_DELAY, DEFAULT_INTER_ZONE_DELAY)): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=300, step=1, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)),
                vol.Required(CONF_MAX_RUNTIME, default=current.get(CONF_MAX_RUNTIME, DEFAULT_MAX_RUNTIME)): selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=360, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)),
            }
        )
        return self.async_show_form(step_id="general", data_schema=schema, errors=errors)

    async def async_step_add_zone(self, user_input=None):
        self._ensure_initialized()
        self._edit_id = None
        return await self.async_step_zone_basic(user_input)

    async def async_step_edit_zone(self, user_input=None):
        self._ensure_initialized()
        if not self._zones:
            return self.async_abort(reason="no_zones")
        choices = [{"value": z["id"], "label": z["name"]} for z in self._zones]
        if user_input is not None:
            self._edit_id = user_input["zone_id"]
            self._working_zone = deepcopy(next(z for z in self._zones if z["id"] == self._edit_id))
            return await self.async_step_zone_basic()
        return self.async_show_form(
            step_id="edit_zone",
            data_schema=vol.Schema({vol.Required("zone_id"): selector.SelectSelector(selector.SelectSelectorConfig(options=choices, mode=selector.SelectSelectorMode.DROPDOWN))}),
        )

    async def async_step_remove_zone(self, user_input=None):
        self._ensure_initialized()
        if not self._zones:
            return self.async_abort(reason="no_zones")
        choices = [{"value": z["id"], "label": z["name"]} for z in self._zones]
        if user_input is not None:
            self._zones = [z for z in self._zones if z["id"] != user_input["zone_id"]]
            return self._save_zones()
        return self.async_show_form(
            step_id="remove_zone",
            data_schema=vol.Schema({vol.Required("zone_id"): selector.SelectSelector(selector.SelectSelectorConfig(options=choices, mode=selector.SelectSelectorMode.DROPDOWN))}),
        )

    async def async_step_zone_basic(self, user_input=None):
        defaults = self._working_zone or {}
        errors = {}
        if user_input is not None:
            if not str(user_input.get("name", "")).strip():
                errors["name"] = "name_required"
            if not user_input.get("valve_entity_id"):
                errors["valve_entity_id"] = "entity_required"
            if not errors:
                # duration_entity_id is optional.  When present it backs the
                # integration-owned Watering duration number; when absent the
                # integration stores the value in fixed_duration.
                user_input = _remove_empty_entity_values(
                    user_input, ("linked_entity_id", "duration_entity_id")
                )
                updated = {**defaults, **user_input}
                for key in ("linked_entity_id", "duration_entity_id"):
                    if key not in user_input:
                        updated.pop(key, None)
                updated["duration_source"] = (
                    DURATION_ENTITY if updated.get("duration_entity_id") else DURATION_FIXED
                )
                self._working_zone = updated
                return await self.async_step_zone_moisture()
            defaults = {**defaults, **user_input}
        schema = vol.Schema(
            {
                vol.Required("name", default=defaults.get("name", "")): str,
                vol.Required(CONF_CATEGORY, default=defaults.get(CONF_CATEGORY, CATEGORY_OTHER)): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[CATEGORY_POTS, CATEGORY_GREENHOUSE, CATEGORY_BORDER, CATEGORY_LAWN, CATEGORY_OTHER],
                        translation_key="zone_category",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                _entity_field("valve_entity_id", required=True, suggested_value=defaults.get("valve_entity_id")): _optional_entity(["switch", "valve"]),
                _entity_field("linked_entity_id", suggested_value=defaults.get("linked_entity_id")): _optional_entity("binary_sensor"),
                _entity_field("duration_entity_id", suggested_value=defaults.get("duration_entity_id")): _optional_entity(["number", "input_number"]),
                vol.Required("fixed_duration", default=_number_default(defaults.get("fixed_duration"), 10)): selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=360, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)),
                vol.Required("enabled", default=defaults.get("enabled", True)): bool,
                vol.Required("rain_sensitive", default=defaults.get("rain_sensitive", True)): bool,
                vol.Required(
                    "frequency",
                    default=(
                        FREQ_BOTH
                        if defaults.get("frequency", FREQ_BOTH) == FREQ_DAILY
                        else defaults.get("frequency", FREQ_BOTH)
                    ),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[FREQ_BOTH, FREQ_ODD, FREQ_EVEN, FREQ_INTERVAL],
                        translation_key="watering_frequency",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required("minimum_interval_hours", default=_number_default(defaults.get("minimum_interval_hours"), 24, integer=True)): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=720, step=1, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX)),
                vol.Required("order", default=_number_default(defaults.get("order"), len(self._zones) + 1, integer=True)): selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=999, step=1, mode=selector.NumberSelectorMode.BOX)),
            }
        )
        return self.async_show_form(step_id="zone_basic", data_schema=schema, errors=errors)

    async def async_step_zone_moisture(self, user_input=None):
        """Configure measurement sources and the broad trigger type."""
        defaults = self._working_zone or {}
        errors = {}
        if user_input is not None:
            measurement_ids = _as_entity_list(
                user_input.get("moisture_measurement_entity_ids")
            )
            trigger_type = user_input["moisture_trigger_type"]
            if trigger_type == TRIGGER_NUMERIC and not measurement_ids:
                errors["moisture_measurement_entity_ids"] = "entity_required"
            if not errors:
                self._working_zone["moisture_measurement_entity_ids"] = measurement_ids
                self._working_zone.update(
                    {
                        "moisture_trigger_type": trigger_type,
                        "moisture_unavailable": user_input["moisture_unavailable"],
                    }
                )
                if trigger_type == TRIGGER_NUMERIC:
                    # Numeric thresholds are integration-owned number values and
                    # are applied directly to the selected measurement sensor(s).
                    self._working_zone["moisture_trigger_entity_ids"] = []
                    self._working_zone.pop("moisture_trigger_state", None)
                    return await self.async_step_zone_numeric()
                if trigger_type == TRIGGER_BINARY:
                    return await self.async_step_zone_binary()
                self._working_zone["moisture_trigger_entity_ids"] = []
                self._working_zone.pop("moisture_selected_entity_id", None)
                self._working_zone.pop("moisture_trigger_state", None)
                return await self.async_step_zone_adaptive()
            defaults = {**defaults, **user_input}

        schema = vol.Schema(
            {
                _entities_field(
                    "moisture_measurement_entity_ids",
                    suggested_value=defaults.get("moisture_measurement_entity_ids"),
                ): _optional_entities("sensor"),
                vol.Required(
                    "moisture_trigger_type",
                    default=defaults.get("moisture_trigger_type", TRIGGER_NONE),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[TRIGGER_NONE, TRIGGER_NUMERIC, TRIGGER_BINARY],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    "moisture_unavailable",
                    default=defaults.get("moisture_unavailable", FALLBACK_SKIP),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[FALLBACK_SKIP, FALLBACK_WATER, FALLBACK_IGNORE],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="zone_moisture", data_schema=schema, errors=errors
        )

    async def async_step_zone_numeric(self, user_input=None):
        """Configure thresholds applied to one or more measurement sensors."""
        defaults = self._working_zone or {}
        errors = {}
        measurement_ids = _as_entity_list(
            defaults.get("moisture_measurement_entity_ids")
        )
        if not measurement_ids:
            return await self.async_step_zone_moisture()

        if user_input is not None:
            if user_input.get("moisture_reset_above", 0) <= user_input.get(
                "moisture_below", 0
            ):
                errors["moisture_reset_above"] = "reset_must_exceed_trigger"
            if not errors:
                self._working_zone.update(user_input)
                self._working_zone["moisture_trigger_entity_ids"] = []
                self._working_zone.pop("moisture_trigger_state", None)
                mode = user_input["moisture_response_mode"]
                if mode == MOISTURE_MODE_SELECTED and len(measurement_ids) > 1:
                    return await self.async_step_zone_trigger_selection()
                self._working_zone["moisture_selected_entity_id"] = measurement_ids[0]
                return await self.async_step_zone_adaptive()
            defaults = {**defaults, **user_input}

        schema = vol.Schema(
            {
                vol.Required(
                    "moisture_response_mode",
                    default=defaults.get(
                        "moisture_response_mode", MOISTURE_MODE_SELECTED
                    ),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            MOISTURE_MODE_SELECTED,
                            MOISTURE_MODE_ANY,
                            MOISTURE_MODE_ALL,
                            MOISTURE_MODE_AVERAGE,
                            MOISTURE_MODE_MINIMUM,
                        ],
                        translation_key="moisture_response_mode_numeric",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    "moisture_below",
                    default=_number_default(defaults.get("moisture_below"), 30),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-1000,
                        max=1000,
                        step=0.1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    "moisture_reset_above",
                    default=_number_default(
                        defaults.get("moisture_reset_above"), 35
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-1000,
                        max=1000,
                        step=0.1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="zone_numeric", data_schema=schema, errors=errors
        )

    async def async_step_zone_binary(self, user_input=None):
        """Configure one or more binary moisture trigger entities."""
        defaults = self._working_zone or {}
        errors = {}
        if user_input is not None:
            trigger_ids = _as_entity_list(user_input.get("moisture_trigger_entity_ids"))
            if not trigger_ids:
                errors["moisture_trigger_entity_ids"] = "entity_required"
            if not errors:
                self._working_zone.update(user_input)
                self._working_zone["moisture_trigger_entity_ids"] = trigger_ids
                mode = user_input["moisture_response_mode"]
                if mode == MOISTURE_MODE_SELECTED and len(trigger_ids) > 1:
                    return await self.async_step_zone_trigger_selection()
                self._working_zone["moisture_selected_entity_id"] = trigger_ids[0]
                return await self.async_step_zone_adaptive()
            defaults = {**defaults, **user_input}

        schema = vol.Schema(
            {
                _entities_field(
                    "moisture_trigger_entity_ids",
                    required=True,
                    suggested_value=defaults.get("moisture_trigger_entity_ids"),
                ): _optional_entities("binary_sensor"),
                vol.Required(
                    "moisture_response_mode",
                    default=defaults.get(
                        "moisture_response_mode", MOISTURE_MODE_SELECTED
                    ),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            MOISTURE_MODE_SELECTED,
                            MOISTURE_MODE_ANY,
                            MOISTURE_MODE_ALL,
                        ],
                        translation_key="moisture_response_mode_binary",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    "moisture_trigger_state",
                    default=defaults.get("moisture_trigger_state", "off"),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["on", "off"],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="zone_binary", data_schema=schema, errors=errors
        )

    async def async_step_zone_trigger_selection(self, user_input=None):
        """Choose the source used when the response mode is Selected sensor."""
        working = self._working_zone or {}
        if working.get("moisture_trigger_type") == TRIGGER_NUMERIC:
            source_ids = _as_entity_list(working.get("moisture_measurement_entity_ids"))
        else:
            source_ids = _as_entity_list(working.get("moisture_trigger_entity_ids"))
        if not source_ids:
            return await self.async_step_zone_moisture()
        if user_input is not None:
            selected = user_input["moisture_selected_entity_id"]
            if selected not in source_ids:
                return self.async_show_form(
                    step_id="zone_trigger_selection",
                    data_schema=self._selected_source_schema(source_ids),
                    errors={"moisture_selected_entity_id": "entity_required"},
                )
            self._working_zone["moisture_selected_entity_id"] = selected
            return await self.async_step_zone_adaptive()
        return self.async_show_form(
            step_id="zone_trigger_selection",
            data_schema=self._selected_source_schema(source_ids),
        )

    def _selected_source_schema(self, entity_ids: list[str]) -> vol.Schema:
        options = []
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            label = (
                state.attributes.get("friendly_name", entity_id)
                if state is not None
                else entity_id
            )
            options.append({"value": entity_id, "label": label})
        selected = (self._working_zone or {}).get("moisture_selected_entity_id")
        if selected not in entity_ids:
            selected = entity_ids[0]
        return vol.Schema(
            {
                vol.Required(
                    "moisture_selected_entity_id", default=selected
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options, mode=selector.SelectSelectorMode.DROPDOWN
                    )
                )
            }
        )

    async def async_step_zone_adaptive(self, user_input=None):
        """Configure optional drying-trend duration adjustment."""
        defaults = self._working_zone or {}
        if user_input is not None:
            if defaults.get("moisture_trigger_type") != TRIGGER_NUMERIC:
                user_input["adaptive_duration_enabled"] = False
            self._working_zone.update(user_input)
            return await self.async_step_zone_safety()
        numeric = defaults.get("moisture_trigger_type") == TRIGGER_NUMERIC
        schema = vol.Schema(
            {
                vol.Required(
                    "adaptive_duration_enabled",
                    default=defaults.get("adaptive_duration_enabled", False),
                ): bool,
                vol.Required(
                    "adaptive_trend_days",
                    default=_number_default(
                        defaults.get("adaptive_trend_days"),
                        DEFAULT_TREND_DAYS,
                        integer=True,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=2, max=7, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    "adaptive_min_drop",
                    default=_number_default(
                        defaults.get("adaptive_min_drop"), DEFAULT_TREND_MIN_DROP
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=1000, step=0.1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    "adaptive_boost_per_day",
                    default=_number_default(
                        defaults.get("adaptive_boost_per_day"),
                        DEFAULT_TREND_BOOST_PER_DAY,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=120, step=1, unit_of_measurement="min",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    "adaptive_max_boost",
                    default=_number_default(
                        defaults.get("adaptive_max_boost"), DEFAULT_TREND_MAX_BOOST
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=240, step=1, unit_of_measurement="min",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    "adaptive_notify",
                    default=defaults.get("adaptive_notify", True),
                ): bool,
            }
        )
        return self.async_show_form(
            step_id="zone_adaptive",
            data_schema=schema,
            description_placeholders={
                "availability": (
                    "Available for numeric moisture triggers"
                    if numeric
                    else "Requires a numeric moisture trigger"
                )
            },
        )

    async def async_step_zone_safety(self, user_input=None):
        defaults = self._working_zone or {}
        if user_input is not None:
            self._working_zone.update(user_input)
            zone = _normalise_zone(self._working_zone, len(self._zones) + 1)
            zone.setdefault("id", self._edit_id or uuid4().hex)
            if self._edit_id:
                self._zones = [
                    zone if z["id"] == self._edit_id else z for z in self._zones
                ]
            else:
                self._zones.append(zone)
            return self._save_zones()
        schema = vol.Schema(
            {
                vol.Required(
                    "max_runtime",
                    default=_number_default(
                        defaults.get("max_runtime"),
                        self.config_entry.options.get(
                            CONF_MAX_RUNTIME,
                            self.config_entry.data.get(
                                CONF_MAX_RUNTIME, DEFAULT_MAX_RUNTIME
                            ),
                        ),
                        integer=True,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=360, step=1, unit_of_measurement="min",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    "allow_manual_when_disabled",
                    default=defaults.get("allow_manual_when_disabled", True),
                ): bool,
            }
        )
        return self.async_show_form(step_id="zone_safety", data_schema=schema)

    def _save_zones(self):
        options = dict(self.config_entry.options)
        options[CONF_ZONES] = sorted(self._zones, key=lambda z: (z.get("order", 999), z["name"].lower()))
        return self.async_create_entry(title="", data=options)

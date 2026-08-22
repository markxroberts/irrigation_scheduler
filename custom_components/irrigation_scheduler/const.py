"""Constants for Irrigation Scheduler."""
from __future__ import annotations

DOMAIN = "irrigation_scheduler"
PLATFORMS = ["sensor", "binary_sensor", "switch", "button", "number", "select"]

CONF_NAME = "name"
CONF_START_TIME = "start_time"
CONF_WEEKDAY_CUTOFF = "weekday_cutoff"
CONF_WEEKEND_CUTOFF = "weekend_cutoff"
CONF_HOLIDAY_CUTOFF = "holiday_cutoff"
CONF_HOLIDAY_ENTITY = "holiday_entity"
CONF_RAIN_TODAY_ENTITY = "rain_today_entity"
CONF_RAIN_YESTERDAY_ENTITY = "rain_yesterday_entity"
CONF_RAIN_TODAY_LIMIT = "rain_today_limit"
CONF_RAIN_YESTERDAY_LIMIT = "rain_yesterday_limit"
CONF_INTER_ZONE_DELAY = "inter_zone_delay"
CONF_MAX_RUNTIME = "max_runtime"
CONF_ZONES = "zones"
CONF_CATEGORY = "category"

DEFAULT_NAME = "Garden irrigation"
DEFAULT_START_TIME = "00:15:00"
DEFAULT_WEEKDAY_CUTOFF = "06:30:00"
DEFAULT_WEEKEND_CUTOFF = "08:00:00"
DEFAULT_HOLIDAY_CUTOFF = "10:00:00"
DEFAULT_INTER_ZONE_DELAY = 15
DEFAULT_MAX_RUNTIME = 90
DEFAULT_RAIN_TODAY_LIMIT = 2.0
DEFAULT_RAIN_YESTERDAY_LIMIT = 2.0

TRIGGER_NONE = "none"
TRIGGER_NUMERIC = "numeric"
TRIGGER_BINARY = "binary"
FREQ_DAILY = "daily"  # Legacy value, treated as both
FREQ_BOTH = "both"
FREQ_ODD = "odd"
FREQ_EVEN = "even"
FREQ_INTERVAL = "interval"
FALLBACK_SKIP = "skip"
FALLBACK_WATER = "water"
FALLBACK_IGNORE = "ignore"

CATEGORY_POTS = "pots"
CATEGORY_GREENHOUSE = "greenhouse"
CATEGORY_BORDER = "border"
CATEGORY_LAWN = "lawn"
CATEGORY_OTHER = "other"

EVENT_DECISION = f"{DOMAIN}_decision"
EVENT_ZONE_STARTED = f"{DOMAIN}_zone_started"
EVENT_ZONE_FINISHED = f"{DOMAIN}_zone_finished"

DURATION_FIXED = "fixed"
DURATION_ENTITY = "entity"

# Multi-sensor moisture response modes
MOISTURE_MODE_SELECTED = "selected"
MOISTURE_MODE_ANY = "any"
MOISTURE_MODE_ALL = "all"
MOISTURE_MODE_AVERAGE = "average"
MOISTURE_MODE_MINIMUM = "minimum"

# Optional drying-trend duration adjustment defaults
DEFAULT_TREND_DAYS = 3
DEFAULT_TREND_MIN_DROP = 0.5
DEFAULT_TREND_BOOST_PER_DAY = 5.0
DEFAULT_TREND_MAX_BOOST = 20.0
DAILY_MINIMUM_HISTORY_DAYS = 31

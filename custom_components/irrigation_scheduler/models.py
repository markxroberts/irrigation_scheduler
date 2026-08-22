"""Data models for Irrigation Scheduler."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class ZoneDecision:
    eligible: bool
    reason: str
    moisture_value: float | None = None


@dataclass(slots=True)
class RuntimeState:
    running: bool = False
    current_zone_id: str | None = None
    current_zone_name: str | None = None
    next_zone_id: str | None = None
    last_message: str = "Ready"
    last_run: datetime | None = None
    zones_due: int = 0
    zones_completed: int = 0
    zones_deferred: int = 0
    current_cycle_watering_seconds: float = 0.0
    current_cycle_completed_zones: list[str] = field(default_factory=list)
    fault: str | None = None
    task: Any = None

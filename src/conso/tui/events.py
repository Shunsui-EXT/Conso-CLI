"""Event bus for the Conso TUI dashboard.

Decouples the automation (register/farm/earn) from the rendering layer: the
pipeline emits events, the TUI subscribes and updates its state.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class EventType(str, Enum):
    BATCH_STARTED = "BATCH_STARTED"
    BATCH_STOPPED = "BATCH_STOPPED"
    BATCH_COMPLETED = "BATCH_COMPLETED"

    ACCOUNT_QUEUED = "ACCOUNT_QUEUED"
    ACCOUNT_STAGE = "ACCOUNT_STAGE"       # create/otp/referral/consoname/earn
    ACCOUNT_COMPLETED = "ACCOUNT_COMPLETED"
    ACCOUNT_FAILED = "ACCOUNT_FAILED"

    TURN_CREDITED = "TURN_CREDITED"
    MISSION_CLAIMED = "MISSION_CLAIMED"

    LOG = "LOG"
    METRICS = "METRICS"


@dataclass
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class EventBus:
    """Thread-safe pub/sub bus."""

    def __init__(self) -> None:
        self._subs: dict[EventType, list[Callable[[Event], None]]] = {}
        self._all: list[Callable[[Event], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, event_type: EventType, cb: Callable[[Event], None]) -> None:
        with self._lock:
            self._subs.setdefault(event_type, []).append(cb)

    def subscribe_all(self, cb: Callable[[Event], None]) -> None:
        with self._lock:
            self._all.append(cb)

    def emit(self, event_type: EventType, **data: Any) -> None:
        event = Event(type=event_type, data=data)
        with self._lock:
            specific = list(self._subs.get(event_type, []))
            general = list(self._all)
        for cb in specific + general:
            try:
                cb(event)
            except Exception:
                pass


_bus: EventBus | None = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    global _bus
    with _bus_lock:
        if _bus is None:
            _bus = EventBus()
        return _bus

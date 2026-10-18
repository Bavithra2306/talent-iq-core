"""In-memory request metrics (no extra service needed).

Counts requests and errors and keeps latency totals per route. The numbers live
in the process and reset when the container restarts; read them with
``GET /api/v1/system/metrics`` (admins only).
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class RouteStats:
    """Aggregated numbers for one ``METHOD /route/template``."""

    count: int = 0
    errors: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    statuses: dict[int, int] = field(default_factory=lambda: defaultdict(int))


class MetricsRegistry:
    """Thread-safe store of per-route request statistics."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._routes: dict[str, RouteStats] = defaultdict(RouteStats)
        self._started = time.time()

    def record(self, route: str, status_code: int, duration_ms: float) -> None:
        """Adds one finished request to the stats."""
        with self._lock:
            stats = self._routes[route]
            stats.count += 1
            stats.total_ms += duration_ms
            stats.max_ms = max(stats.max_ms, duration_ms)
            stats.statuses[status_code] += 1
            if status_code >= 500:
                stats.errors += 1

    def snapshot(self) -> dict[str, object]:
        """Returns a JSON-friendly copy of all stats."""
        with self._lock:
            routes = {
                route: {
                    "requests": s.count,
                    "server_errors": s.errors,
                    "avg_ms": round(s.total_ms / s.count, 1) if s.count else 0.0,
                    "max_ms": round(s.max_ms, 1),
                    "status_codes": dict(s.statuses),
                }
                for route, s in sorted(self._routes.items())
            }
            return {
                "uptime_seconds": int(time.time() - self._started),
                "total_requests": sum(s.count for s in self._routes.values()),
                "routes": routes,
            }


metrics = MetricsRegistry()

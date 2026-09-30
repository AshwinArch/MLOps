"""Tiny in-process Prometheus-format counters (swap for prometheus_client / OpenTelemetry in prod)."""
import threading
from collections import defaultdict

_lock = threading.Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
_hist: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = {}  # [bucket counts..., sum, count]


def inc(name: str, amount: float = 1.0, **labels: str) -> None:
    with _lock:
        _counters[(name, tuple(sorted(labels.items())))] += amount


def observe(name: str, value: float, **labels: str) -> None:
    """Histogram observation (e.g. http_request_duration_seconds) so p50/p95/p99 can be computed in Prometheus."""
    key = (name, tuple(sorted(labels.items())))
    with _lock:
        h = _hist.setdefault(key, [0.0] * (len(BUCKETS) + 2))
        for i, b in enumerate(BUCKETS):
            if value <= b:
                h[i] += 1
        h[-2] += value
        h[-1] += 1


def render() -> str:
    lines = []
    with _lock:
        for (name, labels), value in sorted(_counters.items()):
            lbl = ",".join(f'{k}="{v}"' for k, v in labels)
            lines.append(f"{name}{{{lbl}}} {value}" if lbl else f"{name} {value}")
        for (name, labels), h in sorted(_hist.items()):
            base = ",".join(f'{k}="{v}"' for k, v in labels)
            sep = "," if base else ""
            for i, b in enumerate(BUCKETS):
                lines.append(f'{name}_bucket{{{base}{sep}le="{b}"}} {h[i]}')
            lines.append(f'{name}_bucket{{{base}{sep}le="+Inf"}} {h[-1]}')
            lines.append(f"{name}_sum{{{base}}} {h[-2]}")
            lines.append(f"{name}_count{{{base}}} {h[-1]}")
    return "\n".join(lines) + "\n"


def reset() -> None:
    with _lock:
        _counters.clear()
        _hist.clear()

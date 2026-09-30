"""Runtime selection by configuration (RUNTIME_ADAPTER). The simulator is for tests and demos only."""
from app.core.config import Settings
from app.workers.runtime import ModelRuntime, SimulatedRuntime


def build_runtime(settings: Settings) -> ModelRuntime:
    if settings.runtime_adapter == "http":
        from app.workers.http_runtime import HttpRuntime

        if not settings.runtime_http_url:
            raise RuntimeError("RUNTIME_HTTP_URL is required when RUNTIME_ADAPTER=http")
        return HttpRuntime(settings.runtime_http_url, settings.runtime_http_token,
                           timeout=settings.runtime_timeout_seconds)
    if settings.runtime_adapter == "simulated":
        return SimulatedRuntime(settings.runtime_step_seconds)
    raise RuntimeError(f"Unknown RUNTIME_ADAPTER '{settings.runtime_adapter}'")

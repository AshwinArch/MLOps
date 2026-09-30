"""External model-runtime port + simulated adapter.

Real adapters (KServe, SageMaker, Azure ML, Triton) implement the same `ModelRuntime` protocol, which is how
multiple runtimes are supported (docs/architecture.md, Q4).
"""
import threading
import time
import uuid
from typing import Protocol


class RuntimeFailure(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ModelRuntime(Protocol):
    def deploy(self, *, model_id: str, version: str, environment: str, artifact_uri: str,
               idempotency_token: str, attempt: int, simulate_failure: str) -> str: ...

    def status(self, external_ref: str) -> str: ...  # "running" | "unknown"

    def lookup(self, idempotency_token: str) -> str | None: ...  # ref if the runtime already holds this token

    def verify_artifact(self, artifact_uri: str, checksum: str) -> bool: ...  # digest matches the stored bytes

    def undeploy(self, idempotency_token: str) -> None: ...  # remove what this token deployed (compensation)


class SimulatedRuntime:
    def __init__(self, step_seconds: float = 0.0):
        self.step_seconds = step_seconds
        self._deployed: dict[str, str] = {}
        self._artifacts: dict[str, str] = {}
        self._lock = threading.Lock()

    def deploy(self, *, model_id, version, environment, artifact_uri, idempotency_token, attempt,
               simulate_failure) -> str:
        if self.step_seconds:
            time.sleep(self.step_seconds)
        if simulate_failure == "permanent":
            raise RuntimeFailure("runtime_rejected")
        if simulate_failure == "transient" and attempt == 1:
            raise RuntimeFailure("runtime_timeout")
        with self._lock:
            # idempotent on the token so a re-sent call never double-deploys
            ref = self._deployed.setdefault(idempotency_token, f"rt-{uuid.uuid4().hex[:10]}")
        return ref

    def status(self, external_ref: str) -> str:
        with self._lock:
            return "running" if external_ref in self._deployed.values() else "unknown"

    def lookup(self, idempotency_token: str) -> str | None:
        with self._lock:
            return self._deployed.get(idempotency_token)

    def undeploy(self, idempotency_token: str) -> None:
        with self._lock:
            self._deployed.pop(idempotency_token, None)

    def register_artifact(self, uri: str, checksum: str) -> None:  # test helper: what the artifact store really holds
        with self._lock:
            self._artifacts[uri] = checksum

    def verify_artifact(self, artifact_uri: str, checksum: str) -> bool:
        with self._lock:
            actual = self._artifacts.get(artifact_uri)
        return actual is None or actual == checksum  # unknown artifacts are not contradicted by the simulator

    def register_external(self, token: str, ref: str) -> None:  # test helper: runtime succeeded, DB didn't know
        with self._lock:
            self._deployed[token] = ref

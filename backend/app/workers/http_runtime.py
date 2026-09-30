"""Generic REST runtime adapter. Contract (implement it in front of KServe/SageMaker/Triton, or as a thin shim):

  POST   {url}/deployments                 header Idempotency-Key=<token>; body {model_id, version, environment,
                                           artifact_uri} -> 200/201 {"ref": "..."}; 4xx = rejected, 5xx = unavailable
  GET    {url}/deployments/{token}         200 {"ref": "..."} if this token was deployed, else 404
  GET    {url}/refs/{ref}                  200 {"state": "running"} or 404
  DELETE {url}/deployments/{token}         compensation; 200/204/404 are all success
  POST   {url}/artifacts/verify            {artifact_uri, checksum} -> {"match": true|false}

The adapter maps transport problems to the platform's failure vocabulary: timeouts -> runtime_timeout and
connection/5xx errors -> runtime_unavailable (both TRANSIENT), 4xx -> runtime_rejected (PERMANENT).
It has been exercised against a mock transport only, not against a real runtime (docs/known-limitations.md).
"""
import httpx

from app.workers.runtime import RuntimeFailure


class HttpRuntime:
    def __init__(self, base_url: str, token: str = "", timeout: float = 30.0,
                 transport: httpx.BaseTransport | None = None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._c = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport)

    def _call(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            return self._c.request(method, path, **kw)
        except httpx.TimeoutException:
            raise RuntimeFailure("runtime_timeout") from None
        except httpx.TransportError:
            raise RuntimeFailure("runtime_unavailable") from None

    def deploy(self, *, model_id, version, environment, artifact_uri, idempotency_token, attempt,
               simulate_failure) -> str:
        r = self._call("POST", "/deployments", headers={"Idempotency-Key": idempotency_token},
                       json={"model_id": model_id, "version": version, "environment": environment,
                             "artifact_uri": artifact_uri})
        if r.status_code >= 500:
            raise RuntimeFailure("runtime_unavailable")
        if r.status_code >= 400:
            raise RuntimeFailure("runtime_rejected")
        return r.json()["ref"]

    def lookup(self, idempotency_token: str) -> str | None:
        r = self._call("GET", f"/deployments/{idempotency_token}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()["ref"]

    def status(self, external_ref: str) -> str:
        r = self._call("GET", f"/refs/{external_ref}")
        return "running" if r.status_code == 200 and r.json().get("state") == "running" else "unknown"

    def undeploy(self, idempotency_token: str) -> None:
        r = self._call("DELETE", f"/deployments/{idempotency_token}")
        if r.status_code >= 500:
            raise RuntimeFailure("runtime_unavailable")

    def verify_artifact(self, artifact_uri: str, checksum: str) -> bool:
        r = self._call("POST", "/artifacts/verify", json={"artifact_uri": artifact_uri, "checksum": checksum})
        r.raise_for_status()
        return bool(r.json().get("match"))

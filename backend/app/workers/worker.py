"""Deployment worker: REQUESTED -> VALIDATING -> DEPLOYING -> SUCCEEDED|FAILED.

Runs as a background thread inside the API process for the assignment. The contract (claim by atomic UPDATE,
idempotent steps, reconciler) lets it move unchanged to a separate Deployment / Celery / Arq / K8s Job worker.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core import metrics
from app.core.config import Settings
from app.core.context import correlation_id_var
from app.core.errors import ConflictError
from app.core.security import SYSTEM
from app.db.base import utcnow
from app.db.models import Deployment, DeploymentEvent, EnvironmentState, ModelVersion
from app.domain import deployment_fsm as fsm
from app.domain import lifecycle
from app.domain.enums import ApprovalStatus, DeploymentStatus, Environment, Stage
from app.services.audit import audit, deployment_event
from app.services.deployments import has_newer, live_state
from app.services.registry import ARTIFACT
from app.workers.runtime import ModelRuntime, RuntimeFailure

log = logging.getLogger(__name__)
S = DeploymentStatus


class DeploymentWorker:
    def __init__(self, session_factory: sessionmaker[Session], runtime: ModelRuntime, settings: Settings):
        self.sf = session_factory
        self.runtime = runtime
        self.settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- lifecycle of the polling thread -------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="deployment-worker", daemon=True)
        self._thread.start()

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_pending()
                self.reconcile()
            except Exception:  # keep the worker alive
                log.exception("worker loop error")
                worked = 0
            if not worked:
                self._stop.wait(self.settings.worker_poll_seconds)

    # ---- work ----------------------------------------------------------------------------
    def run_pending(self) -> int:
        with self.sf() as session:
            ids = session.scalars(select(Deployment.id).where(Deployment.status == S.REQUESTED.value)
                                  .order_by(Deployment.created_at)).all()
        if self.settings.worker_concurrency > 1 and len(ids) > 1:
            with ThreadPoolExecutor(max_workers=self.settings.worker_concurrency) as pool:
                list(pool.map(self.process, ids))  # one slow runtime no longer blocks every other deployment
        else:
            for dep_id in ids:
                self.process(dep_id)
        return len(ids)

    def _claim(self, session: Session, dep_id: str) -> bool:
        """Atomic claim so two workers can never process the same deployment."""
        res = session.execute(
            update(Deployment).where(Deployment.id == dep_id, Deployment.status == S.REQUESTED.value)
            .values(status=S.VALIDATING.value, updated_at=utcnow(), row_version=Deployment.row_version + 1))
        session.commit()
        return res.rowcount == 1

    def process(self, dep_id: str) -> None:
        with self.sf() as s0:
            req_cid = s0.scalar(select(Deployment.request_correlation_id).where(Deployment.id == dep_id))
        token = correlation_id_var.set(f"{req_cid}.w" if req_cid else f"worker-{dep_id}")
        try:
            with self.sf() as session:
                if not self._claim(session, dep_id):
                    return
                dep = session.get(Deployment, dep_id)
                session.refresh(dep)
                deployment_event(session, dep, "validation_started", attempt=dep.attempt)
                session.commit()
                try:
                    self._run(session, dep)
                except Exception:
                    session.rollback()
                    log.exception("unexpected worker error; leaving for reconciler",
                                  extra={"deployment_id": dep_id})
        finally:
            correlation_id_var.reset(token)

    def _run(self, session: Session, dep: Deployment) -> None:
        mv = session.scalar(select(ModelVersion).where(ModelVersion.model_id == dep.model_id,
                                                       ModelVersion.version == dep.version))
        env = Environment(dep.environment)
        # 1) VALIDATING - governance is re-checked here: approval may have been revoked since the request.
        try:
            if dep.rollback_of:
                if mv.approval_status != ApprovalStatus.APPROVED.value:
                    raise ConflictError("rollback target not approved")
            else:
                lifecycle.assert_deployable(env, Stage(mv.stage), ApprovalStatus(mv.approval_status),
                                            model_id=dep.model_id, version=dep.version)
        except ConflictError as exc:
            reason = "approval_validation_failed" if exc.code == "VERSION_NOT_APPROVED" else "stage_validation_failed"
            return self._fail(session, dep, reason)
        if not ARTIFACT.match(mv.artifact_uri):
            return self._fail(session, dep, "artifact_validation_failed")
        # Integrity: production needs a checksum, and the runtime/artifact store must confirm it (content check).
        if mv.artifact_checksum is None and env == Environment.PRODUCTION and self.settings.require_artifact_checksum:
            return self._fail(session, dep, "artifact_checksum_missing")
        if mv.artifact_checksum:
            verify = getattr(self.runtime, "verify_artifact", None)
            if verify and not verify(mv.artifact_uri, mv.artifact_checksum):
                return self._fail(session, dep, "artifact_checksum_mismatch")

        # 2) DEPLOYING - record intent + token BEFORE calling the external system (reconciliation anchor).
        fsm.assert_transition(S.VALIDATING, S.DEPLOYING)
        dep.status = S.DEPLOYING.value
        dep.external_ref = None
        deployment_event(session, dep, "runtime_call_started", runtime_token=dep.id)
        session.commit()
        try:
            # A previous attempt may have timed out locally yet completed remotely: adopt it instead of re-calling.
            ref = self.runtime.lookup(dep.id) if dep.attempt > 1 else None
            ref = ref or self._call_runtime(dep, mv)
        except RuntimeFailure as exc:
            return self._fail(session, dep, exc.reason)
        except FutureTimeout:
            # Outcome unknown: the call may still complete remotely. Fail as transient; the retry first asks the
            # runtime via lookup(token) and adopts an already-completed deployment, else re-sends the same token.
            return self._fail(session, dep, "runtime_timeout")
        self._succeed(session, dep, ref)

    def _call_runtime(self, dep: Deployment, mv: ModelVersion) -> str:
        pool = ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(self.runtime.deploy, model_id=dep.model_id, version=dep.version,
                          environment=dep.environment, artifact_uri=mv.artifact_uri, idempotency_token=dep.id,
                          attempt=dep.attempt, simulate_failure=dep.simulate_failure)
        try:
            return fut.result(timeout=self.settings.runtime_timeout_seconds)
        finally:
            pool.shutdown(wait=False)

    def _fail(self, session: Session, dep: Deployment, reason: str) -> None:
        fsm.assert_transition(S(dep.status), S.FAILED)  # VALIDATING/DEPLOYING -> FAILED
        cls = fsm.classify(reason)
        dep.status = S.FAILED.value
        dep.failure_reason = reason
        dep.failure_class = cls.value
        deployment_event(session, dep, reason, failure_class=cls.value, attempt=dep.attempt)
        audit(session, SYSTEM, "deployment.failed", "deployment", dep.id, reason=reason, failure_class=cls.value)
        session.commit()
        metrics.inc("deployments_failed_total", failure_class=cls.value, reason=reason)
        log.warning("deployment failed", extra={"deployment_id": dep.id, "reason": reason,
                                                "failure_class": cls.value})

    def _succeed(self, session: Session, dep: Deployment, external_ref: str) -> None:
        """One DB transaction: deployment status, version stages, events. If it fails the row stays DEPLOYING
        with external_ref unset -> reconciler asks the runtime (token == deployment id) for the truth."""
        fsm.assert_transition(S(dep.status), S.SUCCEEDED)
        env = Environment(dep.environment)
        live = live_state(session, dep.model_id, dep.environment)
        # Lock the version row and re-validate governance INSIDE the finalising transaction: approval may have been
        # revoked, or the version archived/withdrawn, while the runtime call was in flight (review F3).
        target = session.scalar(select(ModelVersion).where(ModelVersion.model_id == dep.model_id,
                                                           ModelVersion.version == dep.version)
                               .with_for_update().execution_options(populate_existing=True))
        if not self._still_deployable(dep, target, env):
            return self._compensate(session, dep)
        dep.external_ref = external_ref
        if live and live.version != dep.version:
            dep.previous_version = live.version

        if dep.rollback_of:
            original = session.get(Deployment, dep.rollback_of)
            fsm.assert_transition(S.SUCCEEDED, S.ROLLED_BACK)
            original.status = S.ROLLED_BACK.value
            bad = session.scalar(select(ModelVersion).where(ModelVersion.model_id == dep.model_id,
                                                            ModelVersion.version == original.version))
            # The faulty version loses its approval: it needs a fresh validation + approval before it can ship again.
            bad.stage = Stage.VALIDATED.value
            bad.approval_status = ApprovalStatus.PENDING.value
            bad.approved_by = None
            bad.approved_at = None
            session.flush()  # free the PRODUCTION slot (single-production index) before re-assigning it
            target.stage = Stage.PRODUCTION.value
            dep.previous_version = original.version
            deployment_event(session, original, "deployment_rolled_back", rolled_back_by=dep.id)
            dep.status = S.SUCCEEDED.value
            deployment_event(session, dep, "rollback_completed", restored_version=target.version)
            audit(session, SYSTEM, "deployment.rolled_back", "deployment", original.id, rollback_id=dep.id,
                  reapproval_required=original.version)
        else:
            dep.status = S.SUCCEEDED.value
            if env == Environment.PRODUCTION and live and live.version != dep.version:
                old = session.scalar(select(ModelVersion).where(ModelVersion.model_id == dep.model_id,
                                                                ModelVersion.version == live.version))
                if old and old.stage == Stage.PRODUCTION.value:
                    old.stage = Stage.ARCHIVED.value  # superseded; remains a valid rollback target
                    session.flush()  # single-production index: release the slot before the new version takes it
            target.stage = lifecycle.stage_after_success(env, Stage(target.stage)).value
            deployment_event(session, dep, "deployment_completed", external_ref=external_ref)
            audit(session, SYSTEM, "deployment.succeeded", "deployment", dep.id, external_ref=external_ref)
        if live:  # update the live pointer atomically with the deployment result
            live.version, live.live_deployment_id = dep.version, dep.id
        else:
            session.add(EnvironmentState(model_id=dep.model_id, environment=dep.environment,
                                         version=dep.version, live_deployment_id=dep.id))
        session.commit()
        metrics.inc("deployments_succeeded_total", environment=dep.environment)
        log.info("deployment succeeded", extra={"deployment_id": dep.id, "external_ref": external_ref})

    def _still_deployable(self, dep: Deployment, target: ModelVersion, env: Environment) -> bool:
        if target.stage in (Stage.ARCHIVED.value, Stage.WITHDRAWN.value) and not dep.rollback_of:
            return False
        if dep.rollback_of:
            return (target.approval_status == ApprovalStatus.APPROVED.value
                    and target.stage != Stage.WITHDRAWN.value)
        try:
            lifecycle.assert_deployable(env, Stage(target.stage), ApprovalStatus(target.approval_status),
                                        model_id=dep.model_id, version=dep.version)
        except ConflictError:
            return False
        return True

    def _compensate(self, session: Session, dep: Deployment) -> None:
        """Governance changed after the external call: undo the remote deployment and fail permanently."""
        session.rollback()
        dep = session.get(Deployment, dep.id)
        undo = getattr(self.runtime, "undeploy", None)
        undone = False
        if undo:
            try:
                undo(dep.id)
                undone = True
            except Exception:
                log.exception("compensating undeploy failed", extra={"deployment_id": dep.id})
        audit(session, SYSTEM, "deployment.compensated", "deployment", dep.id, undeployed=undone,
              reason="approval_revoked_during_deploy")
        metrics.inc("deployments_compensated_total", undeployed=str(undone).lower())
        self._fail(session, dep, "approval_revoked_during_deploy")

    # ---- reconciliation (external success vs internal DB failure) -------------------------
    MAX_REQUEUES = 3
    LATE_WINDOW_HOURS = 24
    DRIFT_CHECK_SECONDS = 60
    _last_drift = 0.0

    def sweep_late_completions(self) -> int:
        """A runtime call that timed out may still complete remotely (review F4). Look at recent timeout failures:
        if the runtime holds their token and the platform has moved on, undeploy the orphan; if nothing newer exists,
        flag it so a retry (which adopts via lookup) or an operator can finish it."""
        found = 0
        since = utcnow() - timedelta(hours=self.LATE_WINDOW_HOURS)
        with self.sf() as session:
            rows = session.scalars(select(Deployment).where(
                Deployment.status == S.FAILED.value, Deployment.failure_reason == "runtime_timeout",
                Deployment.updated_at > since)).all()
            for dep in rows:
                seen = session.scalar(select(func.count()).select_from(DeploymentEvent).where(
                    DeploymentEvent.deployment_id == dep.id, DeploymentEvent.event.like("late_completion%")))
                if seen:
                    continue
                try:
                    ref = self.runtime.lookup(dep.id)
                    if not ref:
                        continue
                    superseded = has_newer(session, dep)
                    if superseded and getattr(self.runtime, "undeploy", None):
                        self.runtime.undeploy(dep.id)
                    event = "late_completion_undeployed" if superseded else "late_completion_detected"
                    deployment_event(session, dep, event, external_ref=ref, superseded=superseded)
                    audit(session, SYSTEM, f"deployment.{event}", "deployment", dep.id, external_ref=ref)
                    session.commit()
                    metrics.inc("deployments_late_completion_total", superseded=str(superseded).lower())
                    found += 1
                except Exception:
                    session.rollback()
                    log.exception("late-completion sweep failed", extra={"deployment_id": dep.id})
        return found

    def check_drift(self) -> int:
        """Compare what the platform believes is live with what the runtime reports (MATCH / MISSING)."""
        import time
        if time.monotonic() - self._last_drift < self.DRIFT_CHECK_SECONDS:
            return 0
        self._last_drift = time.monotonic()
        missing = 0
        with self.sf() as session:
            # only deployments this platform made through a runtime (seeded/legacy live versions have no external_ref)
            live = select(EnvironmentState).join(Deployment, Deployment.id == EnvironmentState.live_deployment_id)
            for st in session.scalars(live.where(Deployment.external_ref.is_not(None))):
                try:
                    if not self.runtime.lookup(st.live_deployment_id):
                        missing += 1
                        metrics.inc("environment_drift_detected_total", kind="missing", environment=st.environment)
                        log.error("runtime does not hold the live deployment",
                                  extra={"model_id": st.model_id, "environment": st.environment})
                except Exception:
                    log.exception("drift check failed", extra={"model_id": st.model_id})
        return missing

    def reconcile(self) -> int:
        cutoff = utcnow() - timedelta(seconds=self.settings.stuck_deployment_seconds)
        fixed = self._requeue_stuck_validating(cutoff)
        fixed += self.sweep_late_completions()
        self.check_drift()
        with self.sf() as session:
            stuck = session.scalars(select(Deployment).where(Deployment.status == S.DEPLOYING.value,
                                                             Deployment.updated_at < cutoff)).all()
            for dep in stuck:
                token = correlation_id_var.set(f"reconcile-{dep.id}")
                try:
                    ref = dep.external_ref or self._runtime_ref(dep.id)
                    if ref and self.runtime.status(ref) == "running":
                        self._succeed(session, dep, ref)
                    else:
                        self._fail(session, dep, "state_reconciliation_required")
                    fixed += 1
                except Exception:
                    session.rollback()
                    log.exception("reconcile failed", extra={"deployment_id": dep.id})
                finally:
                    correlation_id_var.reset(token)
        return fixed

    def _requeue_stuck_validating(self, cutoff) -> int:
        """A worker died after claiming (VALIDATING). Nothing external has happened yet, so re-queue it.
        Capped: a poison row fails as RECONCILIATION instead of looping forever."""
        done = 0
        with self.sf() as session:
            stuck = session.scalars(select(Deployment).where(Deployment.status == S.VALIDATING.value,
                                                             Deployment.updated_at < cutoff)).all()
            for dep in stuck:
                tok = correlation_id_var.set(f"reconcile-{dep.id}")
                try:
                    tries = session.scalar(select(func.count()).select_from(DeploymentEvent).where(
                        DeploymentEvent.deployment_id == dep.id, DeploymentEvent.event == "validation_requeued"))
                    if tries >= self.MAX_REQUEUES:
                        self._fail(session, dep, "validation_requeue_limit")
                    else:
                        dep.status = S.REQUESTED.value
                        deployment_event(session, dep, "validation_requeued", attempt_no=tries + 1)
                        session.commit()
                        metrics.inc("deployments_requeued_total")
                    done += 1
                except Exception:
                    session.rollback()
                    log.exception("requeue failed", extra={"deployment_id": dep.id})
                finally:
                    correlation_id_var.reset(tok)
        return done

    def _runtime_ref(self, token: str) -> str | None:
        """Ask the runtime (port method `lookup`) whether it already holds a deployment for our token."""
        return self.runtime.lookup(token)

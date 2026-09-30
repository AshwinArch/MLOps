import re

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

from app.core.config import Settings
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailed
from app.core.security import Actor
from app.db.base import utcnow
from app.db.models import Deployment, Model, ModelVersion
from app.domain import lifecycle
from app.domain.enums import ApprovalStatus, DeploymentStatus, Role, Stage
from app.services.audit import audit, record_denied

SEMVER = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")
ARTIFACT = re.compile(r"^(s3|gs|abfs|https)://\S+$")
CHECKSUM = re.compile(r"^sha256:[0-9a-f]{64}$")


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def get_model(session: Session, model_id: str) -> Model:
    model = session.get(Model, model_id)
    if not model:
        raise NotFoundError(f"Model '{model_id}' not found", code="MODEL_NOT_FOUND")
    return model


def get_version(session: Session, model_id: str, version: str) -> ModelVersion:
    mv = session.scalar(select(ModelVersion).where(ModelVersion.model_id == model_id,
                                                   ModelVersion.version == version))
    if not mv:
        raise NotFoundError(f"Version '{version}' of model '{model_id}' not found", code="VERSION_NOT_FOUND")
    return mv


def create_model(session: Session, actor: Actor, *, name: str, owner: str, framework: str,
                 description: str | None, tags: list[str], model_id: str | None) -> Model:
    actor.require(Role.ENGINEER, "register models")
    mid = model_id or slugify(name)
    if not mid:
        raise ValidationFailed("Could not derive a model id from the name")
    model = Model(id=mid, name=name, owner=owner, framework=framework, description=description, tags=tags)
    session.add(model)
    audit(session, actor, "model.created", "model", mid, name=name)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ConflictError(f"Model '{mid}' already exists", code="MODEL_EXISTS") from None
    return model


def list_models(session: Session, *, q: str | None, framework: str | None, stage: str | None,
                owner: str | None, limit: int, offset: int) -> tuple[list[Model], int]:
    stmt = select(Model)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Model.name).like(like), func.lower(Model.id).like(like),
                              func.lower(Model.owner).like(like)))
    if framework:
        stmt = stmt.where(Model.framework == framework)
    if owner:
        stmt = stmt.where(func.lower(Model.owner).like(f"%{owner.lower()}%"))
    if stage:
        stmt = stmt.where(Model.id.in_(select(ModelVersion.model_id).where(ModelVersion.stage == stage)))
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = session.scalars(stmt.options(selectinload(Model.versions)).order_by(Model.name)
                            .limit(limit).offset(offset)).all()
    return list(items), total


def create_version(session: Session, actor: Actor, settings: Settings, model_id: str, *, version: str,
                   algorithm: str | None, framework: str | None, artifact_uri: str,
                   training_data_ref: str | None, tags: list[str], metadata: dict,
                   artifact_checksum: str | None = None) -> ModelVersion:
    actor.require(Role.ENGINEER, "register versions")
    model = get_model(session, model_id)
    if not SEMVER.match(version):
        raise ValidationFailed("version must be semantic (e.g. 1.2.0)", details={"field": "version"})
    if not ARTIFACT.match(artifact_uri):
        raise ValidationFailed("artifact_uri must use s3://, gs://, abfs:// or https://",
                               details={"field": "artifact_uri"})
    prefixes = [x.strip() for x in settings.artifact_uri_prefixes.split(",") if x.strip()]
    if prefixes and not any(artifact_uri.startswith(x) for x in prefixes):
        raise ValidationFailed("artifact_uri is outside the approved artifact stores",
                               details={"field": "artifact_uri", "allowed_prefixes": prefixes},
                               code="ARTIFACT_STORE_NOT_ALLOWED")
    if artifact_checksum and not CHECKSUM.match(artifact_checksum):
        raise ValidationFailed("artifact_checksum must be 'sha256:' followed by 64 hex chars",
                               details={"field": "artifact_checksum"})
    mv = ModelVersion(model_id=model.id, version=version, algorithm=algorithm,
                      framework=framework or model.framework, artifact_uri=artifact_uri,
                      artifact_checksum=artifact_checksum, training_data_ref=training_data_ref, tags=tags,
                      extra=metadata,
                      stage=Stage.DRAFT.value, approval_status=ApprovalStatus.PENDING.value,
                      created_by=actor.user)
    session.add(mv)
    audit(session, actor, "version.created", "model_version", f"{model_id}:{version}")
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ConflictError(f"Version {version} of '{model_id}' already exists", code="VERSION_EXISTS") from None
    return mv


def transition_version(session: Session, actor: Actor, settings: Settings, model_id: str, version: str,
                       action: str, comment: str | None = None) -> ModelVersion:
    try:
        return _transition(session, actor, settings, model_id, version, action, comment)
    except (ConflictError, ForbiddenError) as exc:
        record_denied(session, actor, f"version.{action}", "model_version", f"{model_id}:{version}", exc)
        raise


def _transition(session: Session, actor: Actor, settings: Settings, model_id: str, version: str, action: str,
                comment: str | None) -> ModelVersion:
    needed = lifecycle.ACTION_ROLES.get(action)
    if needed:
        actor.require(Role(needed), f"{action} versions")
    mv = get_version(session, model_id, version)
    if action in ("reject", "archive", "withdraw"):
        busy = session.scalar(select(Deployment.id).where(
            Deployment.model_id == model_id, Deployment.version == version,
            Deployment.status.in_([DeploymentStatus.VALIDATING.value, DeploymentStatus.DEPLOYING.value])).limit(1))
        if busy:
            raise ConflictError(f"Version {version} is being deployed ({busy}); wait for it to finish",
                                code="VERSION_IN_USE", details={"deployment_id": busy})
    same_person = (mv.created_by or "").strip().casefold() == actor.user.strip().casefold()
    if action == "approve" and settings.require_four_eyes and same_person:
        raise ForbiddenError("Four-eyes principle: the person who registered a version cannot approve it",
                             code="SELF_APPROVAL", details={"created_by": mv.created_by})
    if action == "validate":
        required = [f.strip() for f in settings.validation_required_fields.split(",") if f.strip()]
        missing = [f for f in required if not getattr(mv, f, None)]
        if missing:
            raise ConflictError("Version cannot be validated until required fields are set: " + ", ".join(missing),
                                code="VALIDATION_INCOMPLETE", details={"missing": missing})
    old_stage = mv.stage
    new_stage, approval = lifecycle.apply_action(action, Stage(mv.stage))
    mv.stage = new_stage.value
    if approval:
        mv.approval_status = approval.value
        mv.approved_by = actor.user if approval == ApprovalStatus.APPROVED else None
        mv.approved_at = utcnow() if approval == ApprovalStatus.APPROVED else None
    audit(session, actor, f"version.{action}", "model_version", f"{model_id}:{version}",
          from_stage=old_stage, to_stage=new_stage.value, comment=comment)
    try:
        session.commit()
    except StaleDataError:
        session.rollback()
        raise ConflictError("Version was modified concurrently; reload and retry", code="CONCURRENT_UPDATE") from None
    return mv

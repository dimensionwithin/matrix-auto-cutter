"""Closed-layout workspace and explicit project lifecycle orchestration."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4

from pydantic import ValidationError

from matrix_auto_cutter.phase2.artifacts import (
    MAX_PROJECT_BYTES,
    ProjectDocument,
    canonical_bytes,
    is_canonical_uuid4,
    parse_project_bytes,
)
from matrix_auto_cutter.phase2.atomic_project import (
    PublishAlreadyExists as AtomicAlreadyExists,
)
from matrix_auto_cutter.phase2.atomic_project import (
    PublishOk,
    publish_initial,
)
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCategory, ErrorCode, ErrorDetail, failure
from matrix_auto_cutter.phase2.pathing import (
    PathRejected,
    PathRole,
    SecureReadFailed,
    ValidatedPath,
    ValidatedWorkspaceRoot,
    ensure_directory_tree,
    secure_read_file,
    validate_path,
)
from matrix_auto_cutter.phase2.win32_port import (
    ERROR_ALREADY_EXISTS,
    ERROR_FILE_NOT_FOUND,
    ERROR_PATH_NOT_FOUND,
    Win32Err,
    Win32Port,
)

WORKSPACE_ROOT_ENV_VAR = "MATRIX_AUTO_CUTTER_WORKSPACE"


def resolve_default_workspace_root() -> str:
    """Resolve the default workspace root: env override, else a per-user default."""
    override = os.environ.get(WORKSPACE_ROOT_ENV_VAR)
    if override:
        return override
    return str(Path.home() / ".matrix-auto-cutter")


DEFAULT_WORKSPACE_ROOT = resolve_default_workspace_root()


@dataclass(frozen=True, slots=True)
class WorkspaceReady:
    """Validated workspace capability."""

    root: ValidatedWorkspaceRoot
    projects_directory: ValidatedPath


@dataclass(frozen=True, slots=True)
class WorkspaceInvalid:
    """Workspace creation or validation failed closed."""

    error: ErrorDetail
    diagnostics: tuple[ErrorDetail, ...] = ()


WorkspaceResult = WorkspaceReady | WorkspaceInvalid


@dataclass(frozen=True, slots=True)
class ProjectCapability:
    """Fully validated immutable project capability."""

    workspace: WorkspaceReady
    project_directory: ValidatedPath
    metadata_path: ValidatedPath
    document: ProjectDocument
    _trust: _ProjectTrust = field(repr=False, compare=False)
    _seal: InitVar[object] = None

    def __post_init__(self, _seal: object) -> None:
        """Reject construction outside the validated project lifecycle."""
        if _seal is not _PROJECT_CAPABILITY_SEAL:
            raise TypeError("project capabilities are issued only after full validation")

    @property
    def trusted(self) -> bool:
        """Return whether post-publication evidence still supports this capability."""
        return self._trust.valid

    def _invalidate_trust(self) -> None:
        self._trust.invalidate()


@dataclass(frozen=True, slots=True)
class ProjectCreated:
    """A fresh project was completely published and validated."""

    project: ProjectCapability


@dataclass(frozen=True, slots=True)
class ProjectOpened:
    """An existing project was completely validated."""

    project: ProjectCapability


@dataclass(frozen=True, slots=True)
class ProjectAlreadyExists:
    """Initial metadata target appeared concurrently."""

    error: ErrorDetail
    cleanup_diagnostics: tuple[ErrorDetail, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectIdCollision:
    """Sixteen genuine UUID directory collisions occurred."""

    error: ErrorDetail


@dataclass(frozen=True, slots=True)
class InvalidProjectId:
    """Requested project ID is not canonical UUIDv4."""

    error: ErrorDetail


@dataclass(frozen=True, slots=True)
class ProjectMetadataMissing:
    """Direct open found a project directory without metadata."""

    error: ErrorDetail
    diagnostics: tuple[ErrorDetail, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectMetadataInvalid:
    """Project metadata is malformed or noncanonical."""

    error: ErrorDetail
    diagnostics: tuple[ErrorDetail, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectBindingMismatch:
    """Project ID, directory, or workspace binding differs."""

    error: ErrorDetail


@dataclass(frozen=True, slots=True)
class OrphanProjectDirectory:
    """Discovery found a UUID directory without valid published metadata."""

    error: ErrorDetail


@dataclass(frozen=True, slots=True)
class UnsupportedProjectVersion:
    """Syntactically readable metadata uses an unsupported version."""

    error: ErrorDetail


@dataclass(frozen=True, slots=True)
class ProjectOpenFailed:
    """Other project ACL, I/O, cancellation, or OS failure."""

    error: ErrorDetail
    cleanup_diagnostics: tuple[ErrorDetail, ...] = ()


ProjectCreateResult = ProjectCreated | ProjectAlreadyExists | ProjectIdCollision | ProjectOpenFailed
ProjectOpenResult = (
    ProjectOpened
    | InvalidProjectId
    | ProjectMetadataMissing
    | ProjectMetadataInvalid
    | ProjectBindingMismatch
    | OrphanProjectDirectory
    | UnsupportedProjectVersion
    | ProjectOpenFailed
)


class _ProjectTrust:
    __slots__ = ("_lock", "_valid")

    def __init__(self) -> None:
        self._lock = Lock()
        self._valid = True

    @property
    def valid(self) -> bool:
        with self._lock:
            return self._valid

    def invalidate(self) -> None:
        with self._lock:
            self._valid = False


_PROJECT_CAPABILITY_SEAL = object()


def _issue_project_capability(
    workspace: WorkspaceReady,
    project_directory: ValidatedPath,
    metadata_path: ValidatedPath,
    document: ProjectDocument,
) -> ProjectCapability:
    return ProjectCapability(
        workspace,
        project_directory,
        metadata_path,
        document,
        _ProjectTrust(),
        _seal=_PROJECT_CAPABILITY_SEAL,
    )


def _project_validator(expected: bytes, model: ProjectDocument) -> Callable[[bytes], bool]:
    def validate(value: bytes) -> bool:
        return value == expected and parse_project_bytes(value) == model

    return validate


def _workspace_path_error(rejected: PathRejected, fallback_phase: str) -> ErrorDetail:
    close_validation = rejected.error.phase.startswith("close_validation_")
    return failure(
        ErrorCode.WORKSPACE_INVALID,
        rejected.error.category if close_validation else ErrorCategory.POLICY,
        rejected.error.phase if close_validation else fallback_phase,
        rejected.error.message,
        win32_code=rejected.error.win32_code,
        cause=rejected.error.cause,
    )


def ensure_workspace(port: Win32Port, root: str = DEFAULT_WORKSPACE_ROOT) -> WorkspaceResult:
    """Safely create and bind only root and ``projects``."""
    ready_root = ensure_directory_tree(port, root)
    if isinstance(ready_root, PathRejected):
        return WorkspaceInvalid(
            _workspace_path_error(ready_root, "workspace"), ready_root.diagnostics
        )
    projects_dos = ready_root.path.canonical_dos_path.rstrip("\\") + "\\projects"
    ensured_projects = ensure_directory_tree(port, projects_dos)
    if isinstance(ensured_projects, PathRejected):
        return WorkspaceInvalid(
            _workspace_path_error(ensured_projects, "workspace_projects"),
            ensured_projects.diagnostics,
        )
    projects = validate_path(
        port,
        projects_dos,
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=ready_root,
        require_existing=True,
    )
    if isinstance(projects, PathRejected):
        return WorkspaceInvalid(
            _workspace_path_error(projects, "workspace_projects"), projects.diagnostics
        )
    return WorkspaceReady(ready_root, projects.path)


def _project_paths(
    port: Win32Port, workspace: WorkspaceReady, project_id: str, *, existing: bool
) -> tuple[ValidatedPath, ValidatedPath] | ProjectOpenFailed:
    directory = validate_path(
        port,
        workspace.projects_directory.canonical_dos_path + "\\" + project_id,
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=workspace.root,
        require_existing=existing,
    )
    if isinstance(directory, PathRejected):
        return ProjectOpenFailed(
            failure(
                ErrorCode.PROJECT_OPEN_FAILED,
                ErrorCategory.IO,
                (
                    directory.error.phase
                    if directory.error.phase.startswith("close_validation_")
                    else "project_directory"
                ),
                directory.error.message,
                win32_code=directory.error.win32_code,
                cause=directory.error.cause,
            ),
            directory.diagnostics,
        )
    metadata = validate_path(
        port,
        directory.path.canonical_dos_path + "\\project.json",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=workspace.root,
    )
    if isinstance(metadata, PathRejected):
        return ProjectOpenFailed(
            failure(
                ErrorCode.PROJECT_OPEN_FAILED,
                ErrorCategory.POLICY,
                "project_metadata_path",
                metadata.error.message,
            )
        )
    return directory.path, metadata.path


def create_project(
    port: Win32Port,
    workspace: WorkspaceReady,
    cancellation: CancellationToken,
    *,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> ProjectCreateResult:
    """Reserve a fresh UUIDv4 directory and publish initial metadata."""
    for _ in range(16):
        if cancellation.is_cancelled:
            return ProjectOpenFailed(
                failure(
                    ErrorCode.CANCELLED,
                    ErrorCategory.CANCELLED,
                    "create_project",
                    "operation cancelled",
                )
            )
        generated = uuid_factory()
        project_id = str(generated)
        if generated.version != 4 or not is_canonical_uuid4(project_id):
            return ProjectOpenFailed(
                failure(
                    ErrorCode.PROJECT_ID_INVALID,
                    ErrorCategory.INPUT,
                    "project_id",
                    "UUID factory returned non-v4 value",
                )
            )
        candidate_dos = workspace.projects_directory.canonical_dos_path + "\\" + project_id
        candidate = validate_path(
            port,
            candidate_dos,
            PathRole.WORKSPACE_INTERNAL,
            workspace_root=workspace.root,
        )
        if isinstance(candidate, PathRejected):
            return ProjectOpenFailed(
                failure(
                    ErrorCode.PROJECT_OPEN_FAILED,
                    ErrorCategory.POLICY,
                    "project_path",
                    candidate.error.message,
                )
            )
        created = port.create_directory(candidate.path.long_path)
        if isinstance(created, Win32Err):
            if created.error.code == ERROR_ALREADY_EXISTS:
                continue
            return ProjectOpenFailed(
                failure(
                    ErrorCode.PROJECT_OPEN_FAILED,
                    ErrorCategory.IO,
                    created.error.operation,
                    created.error.detail,
                    win32_code=created.error.code,
                )
            )
        checked = validate_path(
            port,
            candidate_dos,
            PathRole.WORKSPACE_INTERNAL,
            workspace_root=workspace.root,
            require_existing=True,
        )
        if isinstance(checked, PathRejected):
            close_validation = checked.error.phase.startswith("close_validation_")
            return ProjectOpenFailed(
                failure(
                    ErrorCode.PROJECT_OPEN_FAILED,
                    checked.error.category if close_validation else ErrorCategory.INTEGRITY,
                    (checked.error.phase if close_validation else "project_revalidate"),
                    checked.error.message,
                    win32_code=checked.error.win32_code,
                    cause=checked.error.cause,
                ),
                checked.diagnostics,
            )
        metadata_result = validate_path(
            port,
            candidate_dos + "\\project.json",
            PathRole.WORKSPACE_INTERNAL,
            workspace_root=workspace.root,
        )
        if isinstance(metadata_result, PathRejected):
            return ProjectOpenFailed(
                failure(
                    ErrorCode.PROJECT_OPEN_FAILED,
                    ErrorCategory.POLICY,
                    "project_metadata_path",
                    metadata_result.error.message,
                )
            )
        document = ProjectDocument(
            project_id=project_id,
            workspace_root_binding=workspace.root.binding,
            revision=0,
        )
        data = canonical_bytes(document)
        published = publish_initial(
            port,
            metadata_result.path,
            data,
            _project_validator(data, document),
            cancellation,
            artifact="project",
        )
        if not isinstance(published, PublishOk):
            if isinstance(published, AtomicAlreadyExists):
                return ProjectAlreadyExists(published.error, published.cleanup_diagnostics)
            return ProjectOpenFailed(
                published.error, getattr(published, "cleanup_diagnostics", ())[:8]
            )
        return ProjectCreated(
            _issue_project_capability(workspace, checked.path, metadata_result.path, document)
        )
    return ProjectIdCollision(
        failure(
            ErrorCode.PROJECT_ID_COLLISION,
            ErrorCategory.CONCURRENCY,
            "project_id",
            "16 UUIDv4 directory collisions",
        )
    )


@dataclass(frozen=True, slots=True)
class _MetadataReadFailed:
    error: ErrorDetail
    diagnostics: tuple[ErrorDetail, ...] = ()


def _read_metadata(port: Win32Port, path: ValidatedPath) -> bytes | _MetadataReadFailed:
    read = secure_read_file(port, path, MAX_PROJECT_BYTES)
    if isinstance(read, SecureReadFailed):
        if read.error.code in {
            ErrorCode.PATH_NOT_REGULAR,
            ErrorCode.PATH_EVIDENCE_INSUFFICIENT,
        }:
            return _MetadataReadFailed(
                failure(
                    ErrorCode.PROJECT_METADATA_INVALID,
                    ErrorCategory.INTEGRITY,
                    read.error.phase,
                    read.error.message,
                    win32_code=read.error.win32_code,
                    cause=read.error.cause,
                ),
                read.diagnostics,
            )
        code = (
            ErrorCode.PROJECT_METADATA_MISSING
            if read.error.win32_code in {ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND}
            else ErrorCode.PROJECT_OPEN_FAILED
        )
        category = (
            ErrorCategory.INPUT if code is ErrorCode.PROJECT_METADATA_MISSING else ErrorCategory.IO
        )
        return _MetadataReadFailed(
            failure(
                code,
                category,
                read.error.phase,
                read.error.message,
                win32_code=read.error.win32_code,
                cause=read.error.cause,
            ),
            read.diagnostics,
        )
    return read.data


def open_project(
    port: Win32Port,
    workspace: WorkspaceReady,
    project_id: str,
    *,
    discovery: bool = False,
) -> ProjectOpenResult:
    """Explicitly open or discovery-classify an existing project."""
    if not is_canonical_uuid4(project_id):
        return InvalidProjectId(
            failure(
                ErrorCode.PROJECT_ID_INVALID,
                ErrorCategory.INPUT,
                "open_project",
                "invalid canonical UUIDv4",
            )
        )
    paths = _project_paths(port, workspace, project_id, existing=True)
    if isinstance(paths, ProjectOpenFailed):
        return paths
    directory, metadata = paths
    raw = _read_metadata(port, metadata)
    if isinstance(raw, _MetadataReadFailed):
        if raw.error.code is ErrorCode.PROJECT_METADATA_MISSING:
            if discovery:
                return OrphanProjectDirectory(
                    failure(
                        ErrorCode.PROJECT_ORPHAN,
                        ErrorCategory.INTEGRITY,
                        "discover_project",
                        "project metadata is absent",
                    )
                )
            return ProjectMetadataMissing(raw.error, raw.diagnostics)
        if raw.error.code is ErrorCode.PROJECT_METADATA_INVALID:
            return ProjectMetadataInvalid(raw.error, raw.diagnostics)
        return ProjectOpenFailed(raw.error, raw.diagnostics)
    try:
        parsed_json = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return ProjectMetadataInvalid(
            failure(
                ErrorCode.PROJECT_METADATA_INVALID,
                ErrorCategory.INPUT,
                "project_json",
                str(exc),
                cause=exc,
            )
        )
    if isinstance(parsed_json, dict) and parsed_json.get("schema_version") != "1.0":
        return UnsupportedProjectVersion(
            failure(
                ErrorCode.PROJECT_VERSION_UNSUPPORTED,
                ErrorCategory.INPUT,
                "project_schema",
                "unsupported schema version",
            )
        )
    try:
        document = parse_project_bytes(raw)
    except (ValueError, UnicodeError, ValidationError) as exc:
        return ProjectMetadataInvalid(
            failure(
                ErrorCode.PROJECT_METADATA_INVALID,
                ErrorCategory.INPUT,
                "project_schema",
                str(exc),
                cause=exc,
            )
        )
    if (
        document.project_id != project_id
        or document.workspace_root_binding != workspace.root.binding
        or directory.canonical_dos_path.rpartition("\\")[2] != project_id
    ):
        return ProjectBindingMismatch(
            failure(
                ErrorCode.PROJECT_BINDING_MISMATCH,
                ErrorCategory.INTEGRITY,
                "project_binding",
                "project ID or root binding mismatch",
            )
        )
    return ProjectOpened(_issue_project_capability(workspace, directory, metadata, document))


def classify_project_directory(
    port: Win32Port, workspace: WorkspaceReady, project_id: str
) -> ProjectOpenResult:
    """Discovery classification with a distinct orphan outcome."""
    return open_project(port, workspace, project_id, discovery=True)

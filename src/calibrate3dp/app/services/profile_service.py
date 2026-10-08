"""Read-only Orca discovery, profile import, and baseline selection."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from typing import Callable, Mapping, Sequence

from calibrate3dp.app.models import ProfileSelection
from calibrate3dp.orca_cli import OrcaCli, OrcaCliError
from calibrate3dp.orca_profiles import (
    ImportedOrcaProfiles,
    InvalidProfileBundleError,
    OrcaProfileAdapter,
)
from calibrate3dp.profiles import (
    AmbiguousProfileError,
    DuplicateProfileError,
    ProfileCatalog,
    ProfileDocument,
    ProfileResolutionError,
    ResolvedProfile,
)


class ProfileServiceError(ValueError):
    """Base error for an invalid profile workflow operation."""


class ProfileImportError(ProfileServiceError):
    """Import error with a UI field and a recovery action."""

    def __init__(self, source: str | Path, field: str, message: str, action: str) -> None:
        self.source = str(source)
        self.field = field
        self.message = message
        self.action = action
        super().__init__(f"{field}: {message}")


class MissingProfileSelectionError(ProfileServiceError):
    """Raised when the user has not selected each required profile role."""


class MissingRequiredProfileSettingError(ProfileServiceError):
    """Raised when a selected baseline lacks a setting needed by a module."""


@dataclass(frozen=True)
class OrcaSetupState:
    """Observed Orca setup details; CLI availability is not a support claim."""

    executable: Path | None
    config_roots: tuple[Path, ...]
    version_banner: str | None = None
    last_checked_at_utc: str | None = None
    cli_status: str = "Not checked"
    compatibility_status: str = "Not verified by the integration suite"
    error: str | None = None

    @property
    def actions(self) -> tuple[str, ...]:
        """Actions that remain available when Orca is missing or offline."""
        return ("browse_executable", "browse_config_root", "recheck", "export_diagnostics")


@dataclass(frozen=True)
class ProfileChoice:
    """An unambiguous profile identity suitable for a UI selector."""

    kind: str
    scope: str
    name: str
    source: str

    @property
    def label(self) -> str:
        return f"{self.name}  ·  {self.kind}  ·  {self.scope}"


class ProfileService:
    """Coordinate the existing resolver and Orca adapter without writing presets."""

    required_profile_directories = frozenset({"machine", "printer", "filament", "process"})
    default_import_scope = "local-import"

    def __init__(
        self,
        adapter: OrcaProfileAdapter | None = None,
        *,
        executable: str | Path | None = None,
        config_roots: Sequence[str | Path] | None = None,
        parent_scopes: Mapping[str, Sequence[str]] | None = None,
        cli_factory: Callable[[str | Path], OrcaCli] = OrcaCli,
        executable_detector: Callable[[], Path | None] | None = None,
    ) -> None:
        self.adapter = adapter or OrcaProfileAdapter()
        self._cli_factory = cli_factory
        self._executable_detector = executable_detector
        self._executable_override = Path(executable).expanduser() if executable else None
        self._config_root_overrides = (
            tuple(Path(root).expanduser() for root in config_roots)
            if config_roots is not None
            else None
        )
        self._infer_parent_scopes = parent_scopes is None
        self._parent_scopes = {
            scope: tuple(scopes) for scope, scopes in (parent_scopes or {}).items()
        }
        self._documents: list[ProfileDocument] = []
        self._manual_imports: list[ImportedOrcaProfiles] = []
        self._source_hashes: dict[tuple[str, str, str], str] = {}
        self._catalog = ProfileCatalog()
        self._discovery_issues: list[ProfileImportError] = []
        self._executable = self._executable_override or self._detect_executable()
        roots = self._config_roots()
        self._setup_state = OrcaSetupState(
            executable=self._executable,
            config_roots=roots,
            cli_status="OrcaSlicer not detected" if self._executable is None else "Detected; not checked",
        )

    @property
    def setup_state(self) -> OrcaSetupState:
        return self._setup_state

    @property
    def catalog(self) -> ProfileCatalog:
        return self._catalog

    @property
    def discovery_issues(self) -> tuple[ProfileImportError, ...]:
        return tuple(self._discovery_issues)

    def set_executable(self, executable: str | Path | None) -> None:
        """Set an in-memory executable override selected by the user."""
        self._executable_override = Path(executable).expanduser() if executable else None
        self._executable = self._executable_override or self._detect_executable()
        self._setup_state = OrcaSetupState(
            executable=self._executable,
            config_roots=self._config_roots(),
            cli_status="Detected; not checked" if self._executable else "OrcaSlicer not detected",
        )

    def set_config_roots(self, roots: Sequence[str | Path] | None) -> None:
        """Set profile-root overrides, or return to automatic discovery."""
        self._config_root_overrides = (
            tuple(Path(root).expanduser() for root in roots) if roots is not None else None
        )
        self._setup_state = OrcaSetupState(
            executable=self._executable,
            config_roots=self._config_roots(),
            version_banner=self._setup_state.version_banner,
            last_checked_at_utc=self._setup_state.last_checked_at_utc,
            cli_status=self._setup_state.cli_status,
            compatibility_status=self._setup_state.compatibility_status,
            error=self._setup_state.error,
        )

    def check_setup(self, *, timeout_seconds: float = 8) -> OrcaSetupState:
        """Probe the selected Orca CLI and retain the observed banner and time."""
        self._executable = self._executable_override or self._detect_executable()
        checked_at = _utc_now()
        roots = self._config_roots()
        if self._executable is None:
            self._setup_state = OrcaSetupState(
                executable=None,
                config_roots=roots,
                last_checked_at_utc=checked_at,
                cli_status="OrcaSlicer not detected",
                error="Select the OrcaSlicer executable to check its command-line support.",
            )
            return self._setup_state

        if not self._executable.is_file():
            self._setup_state = OrcaSetupState(
                executable=self._executable,
                config_roots=roots,
                last_checked_at_utc=checked_at,
                cli_status="Executable path is unavailable",
                error=f"No file exists at {self._executable}",
            )
            return self._setup_state

        try:
            capabilities = self._cli_factory(self._executable).probe(timeout_seconds=timeout_seconds)
        except (OrcaCliError, OSError, TimeoutError) as exc:
            self._setup_state = OrcaSetupState(
                executable=self._executable,
                config_roots=roots,
                last_checked_at_utc=checked_at,
                cli_status="CLI check failed",
                error=str(exc),
            )
            return self._setup_state

        self._setup_state = OrcaSetupState(
            executable=self._executable,
            config_roots=roots,
            version_banner=capabilities.version_banner,
            last_checked_at_utc=checked_at,
            cli_status="CLI responds to the required probe",
            compatibility_status=(
                "CLI responds; end-to-end support for the selected printer and profile set "
                "is not verified."
            ),
        )
        return self._setup_state

    def discover_profiles(self) -> tuple[ProfileChoice, ...]:
        """Read profile JSON under Orca's machine, filament, and process folders."""
        self._documents = []
        self._source_hashes = {}
        self._discovery_issues = []
        for root in self._config_roots():
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.json"), key=lambda item: str(item).casefold()):
                profile_directory = self._profile_directory(path)
                if profile_directory is None:
                    continue
                scope = self._scope_for_directory(profile_directory)
                try:
                    imported = self.adapter.load(path, scope=scope)
                    self._record_import(imported)
                except (InvalidProfileBundleError, DuplicateProfileError, ProfileResolutionError) as exc:
                    self._discovery_issues.append(
                        ProfileImportError(
                            path,
                            "profile JSON",
                            str(exc),
                            "Inspect the preset in OrcaSlicer or import a corrected JSON preset.",
                        )
                    )
        for imported in self._manual_imports:
            try:
                self._record_import(imported)
            except DuplicateProfileError as exc:
                self._discovery_issues.append(
                    ProfileImportError(
                        imported.source,
                        "profile catalog",
                        str(exc),
                        "Use a distinct import scope or resolve the duplicate profile identity.",
                    )
                )
        self._rebuild_catalog()
        return tuple(choice for role in ("printer", "filament", "process") for choice in self.choices(role))

    def import_source(
        self,
        path: str | Path,
        *,
        scope: str = default_import_scope,
    ) -> ImportedOrcaProfiles:
        """Import a supported JSON preset or bundle in memory, without extraction."""
        source = Path(path).expanduser()
        try:
            imported = self.adapter.load(source, scope=scope)
        except (InvalidProfileBundleError, ProfileResolutionError, OSError, UnicodeError) as exc:
            suffix = source.suffix.casefold()
            unsupported = suffix not in self.adapter.json_suffixes | self.adapter.bundle_suffixes
            if unsupported:
                message = (
                    f"The {suffix or 'selected'} file type is not supported. Export a preset as "
                    "JSON or choose a supported OrcaSlicer ZIP profile bundle."
                )
                action = "Choose a .json preset or a supported .zip/.orca_printer bundle."
            else:
                message = str(exc)
                action = "Review the profile field and correct it in OrcaSlicer before importing again."
            raise ProfileImportError(source, "profile JSON", message, action) from exc

        duplicate = next(
            (document for document in imported.documents if document.identity in {d.identity for d in self._documents}),
            None,
        )
        if duplicate is not None:
            raise ProfileImportError(
                source,
                "profile catalog",
                f"{duplicate.kind} profile {duplicate.name!r} already exists in scope {duplicate.scope!r}.",
                "Use a distinct scope or remove the duplicate profile before importing.",
            )
        self._record_import(imported)
        self._manual_imports.append(imported)
        self._rebuild_catalog()
        return imported

    def choices(self, role: str) -> tuple[ProfileChoice, ...]:
        """Return stable choices for a UI role; printer accepts machine profiles."""
        allowed_kinds = {
            "printer": {"machine", "printer"},
            "filament": {"filament"},
            "process": {"process"},
        }
        if role not in allowed_kinds:
            raise ProfileServiceError(f"unknown profile role {role!r}")
        return tuple(
            ProfileChoice(document.kind, document.scope, document.name, document.source)
            for document in sorted(
                (item for item in self._documents if item.kind in allowed_kinds[role]),
                key=lambda item: (item.name.casefold(), item.scope.casefold(), item.kind),
            )
        )

    def resolve(self, kind: str, scope: str | None, name: str) -> ResolvedProfile:
        """Resolve an explicit profile identity using the domain catalog."""
        if scope is not None:
            self._check_parent_ambiguity(kind, scope, name)
        return self._catalog.resolve(kind, scope, name)

    def resolve_choice(self, choice: ProfileChoice) -> ResolvedProfile:
        if not isinstance(choice, ProfileChoice):
            raise ProfileServiceError("profile selection must identify a scoped profile choice")
        return self.resolve(choice.kind, choice.scope, choice.name)

    def source_sha256(self, choice: ProfileChoice) -> str | None:
        """Return the hash of the selected source file or bundle, when known."""
        return self._source_hashes.get((choice.kind, choice.scope, choice.name))

    def build_selection(
        self,
        choices: Mapping[str, ProfileChoice | None],
        *,
        required_settings: Sequence[str] = (),
    ) -> ProfileSelection:
        """Create a complete selection only after all profiles and required keys resolve."""
        roles = ("printer", "filament", "process")
        missing_roles = [role for role in roles if choices.get(role) is None]
        if missing_roles:
            raise MissingProfileSelectionError(
                "Select a printer, filament, and process profile before continuing; missing: "
                + ", ".join(missing_roles)
            )

        resolved: dict[str, ResolvedProfile] = {}
        for role in roles:
            choice = choices[role]
            assert choice is not None
            expected = {"printer": {"machine", "printer"}, "filament": {"filament"}, "process": {"process"}}[role]
            if choice.kind not in expected:
                raise ProfileServiceError(
                    f"{role} selection must be one of {', '.join(sorted(expected))} profiles"
                )
            resolved[role] = self.resolve_choice(choice)

        missing_settings = [
            key for key in required_settings
            if key not in resolved["process"].settings
            or resolved["process"].settings[key] is None
            or resolved["process"].settings[key] == ""
        ]
        if missing_settings:
            raise MissingRequiredProfileSettingError(
                "The selected process profile does not define required setting(s): "
                + ", ".join(missing_settings)
                + ". Import a profile that explicitly contains them; Orca defaults are not used."
            )

        source_paths: dict[str, str] = {}
        source_hashes: dict[str, str] = {}
        for role, profile in resolved.items():
            document = profile.profile
            source_paths[role] = document.source
            digest = self._source_hashes.get(document.identity)
            if digest:
                source_hashes[role] = digest

        warnings = (
            "End-to-end compatibility for this Orca version and selected printer profile has not been verified.",
        )
        return ProfileSelection(
            printer=resolved["printer"],
            filament=resolved["filament"],
            process=resolved["process"],
            source_paths=source_paths,
            source_hashes=source_hashes,
            compatibility_warnings=warnings,
        )

    def export_diagnostics(
        self,
        destination: str | Path,
        *,
        include_paths: bool = True,
        include_profile_counts: bool = True,
    ) -> Path:
        """Write an offline setup report containing only the selected metadata."""
        if type(include_paths) is not bool or type(include_profile_counts) is not bool:
            raise TypeError("diagnostics inclusion options must be booleans")
        target = Path(destination).expanduser()
        state = self._setup_state
        counts = Counter(document.kind for document in self._documents)
        orca = {
            "version_banner": state.version_banner,
            "last_checked_at_utc": state.last_checked_at_utc,
            "cli_status": state.cli_status,
            "compatibility_status": state.compatibility_status,
            "error": (
                state.error
                if include_paths or state.error is None
                else "The setup check returned an error; local paths were excluded."
            ),
        }
        if include_paths:
            orca["executable"] = str(state.executable) if state.executable else None
            orca["config_roots"] = [str(root) for root in state.config_roots]
        payload = {
            "schema_version": 1,
            "created_at_utc": _utc_now(),
            "orca": orca,
            "discovery_issue_count": len(self._discovery_issues),
        }
        if include_profile_counts:
            payload["profiles"] = {"count_by_kind": dict(sorted(counts.items()))}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        return target

    def _record_import(self, imported: ImportedOrcaProfiles) -> None:
        existing = {document.identity for document in self._documents}
        incoming = [document.identity for document in imported.documents]
        if len(incoming) != len(set(incoming)) or existing.intersection(incoming):
            duplicate = next(
                identity for identity in incoming
                if incoming.count(identity) > 1 or identity in existing
            )
            raise DuplicateProfileError(
                f"duplicate profile identity {duplicate[0]!r}/{duplicate[1]!r}/{duplicate[2]!r}"
            )
        for document in imported.documents:
            self._documents.append(document)
            self._source_hashes[document.identity] = imported.source_sha256

    def _rebuild_catalog(self) -> None:
        parent_scopes = (
            self._derive_parent_scopes() if self._infer_parent_scopes else self._parent_scopes
        )
        self._catalog = ProfileCatalog(self._documents, parent_scopes=parent_scopes)

    def _derive_parent_scopes(self) -> dict[str, tuple[str, ...]]:
        """Allow only uniquely identifiable cross-scope parent references."""
        inferred: dict[str, list[str]] = {}
        for child in self._documents:
            parent_name = child.raw.get("inherits")
            if not isinstance(parent_name, str):
                continue
            same_scope = [
                document for document in self._documents
                if document.kind == child.kind
                and document.scope == child.scope
                and document.name == parent_name
            ]
            if same_scope:
                continue
            parent_scopes = {
                document.scope for document in self._documents
                if document.kind == child.kind
                and document.scope != child.scope
                and document.name == parent_name
            }
            if len(parent_scopes) == 1:
                inferred.setdefault(child.scope, []).extend(parent_scopes)
        return {
            scope: tuple(dict.fromkeys(scopes))
            for scope, scopes in inferred.items()
        }

    def _check_parent_ambiguity(self, kind: str, scope: str, name: str) -> None:
        """Block a cross-scope parent when multiple profiles share its name."""
        document = next(
            (item for item in self._documents if item.identity == (kind, scope, name)),
            None,
        )
        visited: set[tuple[str, str, str]] = set()
        while document is not None and document.identity not in visited:
            visited.add(document.identity)
            parent_name = document.raw.get("inherits")
            if not isinstance(parent_name, str):
                return
            same_scope = [
                item for item in self._documents
                if item.kind == document.kind
                and item.scope == document.scope
                and item.name == parent_name
            ]
            if same_scope:
                document = same_scope[0]
                continue
            candidates = [
                item for item in self._documents
                if item.kind == document.kind
                and item.name == parent_name
                and item.scope != document.scope
            ]
            if not self._infer_parent_scopes:
                allowed = set(self._parent_scopes.get(document.scope, ()))
                candidates = [item for item in candidates if item.scope in allowed]
            scopes = sorted({item.scope for item in candidates})
            if len(scopes) > 1:
                raise AmbiguousProfileError(
                    f"parent {parent_name!r} for {document.name!r} exists in multiple scopes: "
                    + ", ".join(scopes)
                    + "; import or select the intended parent with an explicit scope"
                )
            if not candidates:
                return
            document = candidates[0]

    def _config_roots(self) -> tuple[Path, ...]:
        if self._config_root_overrides is not None:
            candidates = self._config_root_overrides
        else:
            candidates = self._default_config_roots()
        unique: dict[str, Path] = {}
        for candidate in candidates:
            key = os.path.normcase(str(candidate.resolve(strict=False)))
            unique[key] = candidate
        return tuple(unique.values())

    @staticmethod
    def _default_config_roots() -> tuple[Path, ...]:
        configured = os.environ.get("ORCA_PROFILE_ROOT")
        candidates: list[Path] = [Path(configured).expanduser()] if configured else []
        if os.name == "nt":
            app_data = os.environ.get("APPDATA")
            if app_data:
                candidates.append(Path(app_data) / "OrcaSlicer")
            candidates.append(Path.home() / "AppData" / "Roaming" / "OrcaSlicer")
        elif sys_platform_is_macos():
            candidates.append(Path.home() / "Library" / "Application Support" / "OrcaSlicer")
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME")
            candidates.append((Path(xdg).expanduser() if xdg else Path.home() / ".config") / "OrcaSlicer")
            candidates.append(Path.home() / ".OrcaSlicer")
        return tuple(path for path in candidates if path.is_dir())

    def _detect_executable(self) -> Path | None:
        if self._executable_detector is not None:
            return self._executable_detector()
        configured = os.environ.get("ORCA_SLICER_EXE")
        candidates: list[Path] = [Path(configured).expanduser()] if configured else []
        for command in ("OrcaSlicer", "orca-slicer", "orcaslicer"):
            found = shutil.which(command)
            if found:
                candidates.append(Path(found))
        if os.name == "nt":
            for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
                base = os.environ.get(variable)
                if base:
                    candidates.append(Path(base) / "OrcaSlicer" / "orca-slicer.exe")
        elif sys_platform_is_macos():
            candidates.append(
                Path("/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer")
            )
        else:
            candidates.extend((Path("/usr/bin/orca-slicer"), Path("/usr/local/bin/orca-slicer")))
        return next((path for path in candidates if path.is_file()), None)

    @classmethod
    def _profile_directory(cls, path: Path) -> Path | None:
        for parent in path.parents:
            if parent.name.casefold() in cls.required_profile_directories:
                return parent
        return None

    @staticmethod
    def _scope_for_directory(profile_directory: Path) -> str:
        return f"orca:{profile_directory.parent.resolve(strict=False)}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sys_platform_is_macos() -> bool:
    """Small wrapper to keep platform detection easy to exercise in tests."""
    import sys

    return sys.platform == "darwin"

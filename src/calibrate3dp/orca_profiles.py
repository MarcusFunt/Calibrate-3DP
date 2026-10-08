"""Read Orca preset JSON files and exported profile bundles safely.

This adapter covers the documented JSON and ZIP-based preset export formats.
It does not claim that a specific OrcaSlicer release's CLI inheritance or
3MF override behavior has been validated; those are separate integration gates.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
import zipfile

from calibrate3dp.profiles import (
    DuplicateProfileError,
    InvalidProfileDocumentError,
    InvalidProfilePatchError,
    ProfileCatalog,
    ProfileDocument,
    ProfileResolutionError,
    ResolvedProfile,
    clone_profile_with_patch,
)


class InvalidProfileBundleError(ProfileResolutionError):
    """Raised when a source is not a supported Orca preset file or bundle."""


@dataclass(frozen=True)
class ImportedOrcaProfiles:
    """Profiles loaded from one source, tied to its content hash and adapter."""

    adapter_version: int
    format: str
    scope: str
    source: str
    source_sha256: str
    documents: tuple[ProfileDocument, ...]


class OrcaProfileAdapter:
    """Version 1 adapter for Orca profile JSON and exported ZIP bundles.

    The importer never extracts archive members. `scope` is explicit so the
    caller controls how imported profiles are grouped for inheritance lookup.
    Unknown top-level JSON keys remain in each ProfileDocument's raw mapping.
    """

    adapter_version = 1
    json_suffixes = frozenset({".json"})
    bundle_suffixes = frozenset({
        ".zip",
        ".orca_printer",
        ".orca_filament",
        ".orca_filaments",
        ".orca_bundle",
    })
    profile_kinds = frozenset({"machine", "printer", "filament", "process"})
    profile_directories = frozenset({"machine", "printer", "filament", "process"})
    max_source_bytes = 64 * 1024 * 1024
    max_archive_entries = 4096
    max_member_bytes = 16 * 1024 * 1024
    max_uncompressed_bytes = 128 * 1024 * 1024

    def load(self, path: str | Path, *, scope: str) -> ImportedOrcaProfiles:
        """Load one preset JSON or Orca preset bundle without writing files."""
        source_path = Path(path)
        if not isinstance(scope, str) or not scope.strip():
            raise InvalidProfileBundleError("profile import scope must be a non-empty string")
        try:
            source_size = source_path.stat().st_size
        except OSError as exc:
            raise InvalidProfileBundleError(f"could not read profile source {source_path}: {exc}") from exc
        if source_size > self.max_source_bytes:
            raise InvalidProfileBundleError(
                f"profile source exceeds the {self.max_source_bytes} byte size limit"
            )

        source_hash = self._sha256_file(source_path)
        suffix = source_path.suffix.lower()
        if suffix in self.json_suffixes:
            try:
                document = ProfileDocument.from_json_file(source_path, scope=scope)
            except (InvalidProfileDocumentError, UnicodeDecodeError) as exc:
                raise InvalidProfileBundleError(str(exc)) from exc
            if document.kind not in self.profile_kinds:
                raise InvalidProfileBundleError(
                    f"profile JSON {source_path} has unsupported profile type {document.kind!r}"
                )
            return ImportedOrcaProfiles(
                adapter_version=self.adapter_version,
                format="json-profile",
                scope=scope,
                source=str(source_path),
                source_sha256=source_hash,
                documents=(document,),
            )

        if suffix not in self.bundle_suffixes:
            raise InvalidProfileBundleError(
                f"unsupported Orca profile source extension {suffix or '<none>'!r}"
            )
        documents = self._load_archive(source_path, scope=scope)
        return ImportedOrcaProfiles(
            adapter_version=self.adapter_version,
            format="zip-profile-bundle",
            scope=scope,
            source=str(source_path),
            source_sha256=source_hash,
            documents=documents,
        )

    def _load_archive(self, path: Path, *, scope: str) -> tuple[ProfileDocument, ...]:
        try:
            with zipfile.ZipFile(path, "r") as archive:
                entries = archive.infolist()
                if len(entries) > self.max_archive_entries:
                    raise InvalidProfileBundleError(
                        f"profile bundle contains more than {self.max_archive_entries} entries"
                    )
                expanded_size = sum(entry.file_size for entry in entries)
                if expanded_size > self.max_uncompressed_bytes:
                    raise InvalidProfileBundleError(
                        "profile bundle exceeds the uncompressed size limit"
                    )
                for entry in entries:
                    self._validate_member_name(entry.filename)
                    if not entry.is_dir() and entry.file_size > self.max_member_bytes:
                        raise InvalidProfileBundleError(
                            f"profile bundle entry {entry.filename!r} exceeds the size limit"
                        )
                documents = self._read_profile_entries(archive, path, scope, entries)
        except InvalidProfileBundleError:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise InvalidProfileBundleError(f"could not read Orca profile bundle {path}: {exc}") from exc

        if not documents:
            raise InvalidProfileBundleError(
                f"Orca profile bundle {path} contains no recognized machine, filament, or process presets"
            )
        try:
            ProfileCatalog(documents)
        except DuplicateProfileError:
            raise
        except ProfileResolutionError as exc:
            raise InvalidProfileBundleError(f"invalid profile bundle {path}: {exc}") from exc
        return tuple(documents)

    def _read_profile_entries(
        self,
        archive: zipfile.ZipFile,
        bundle_path: Path,
        scope: str,
        entries: list[zipfile.ZipInfo],
    ) -> list[ProfileDocument]:
        documents: list[ProfileDocument] = []
        for entry in sorted(entries, key=lambda item: (item.filename.casefold(), item.filename)):
            if entry.is_dir() or PurePosixPath(entry.filename.replace("\\", "/")).suffix.lower() != ".json":
                continue
            try:
                with archive.open(entry, "r") as member:
                    raw_bytes = member.read(self.max_member_bytes + 1)
                if len(raw_bytes) > self.max_member_bytes:
                    raise InvalidProfileBundleError(
                        f"profile bundle entry {entry.filename!r} exceeds the size limit"
                    )
                if len(raw_bytes) != entry.file_size:
                    raise InvalidProfileBundleError(
                        f"profile bundle entry {entry.filename!r} has inconsistent size metadata"
                    )
                payload: Any = json.loads(raw_bytes.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile, RuntimeError) as exc:
                raise InvalidProfileBundleError(
                    f"profile bundle entry {entry.filename!r} is not valid UTF-8 JSON"
                ) from exc

            if not isinstance(payload, dict):
                if self._is_profile_path(entry.filename):
                    raise InvalidProfileBundleError(
                        f"profile bundle entry {entry.filename!r} must contain a JSON object"
                    )
                continue
            kind = payload.get("type")
            if not isinstance(kind, str) or kind not in self.profile_kinds:
                if self._is_profile_path(entry.filename):
                    raise InvalidProfileBundleError(
                        f"profile bundle entry {entry.filename!r} has unsupported or missing profile type"
                    )
                continue
            try:
                document = ProfileDocument(
                    name=payload.get("name"),
                    kind=kind,
                    scope=scope,
                    raw=payload,
                    source=f"{bundle_path}!{entry.filename}",
                )
            except (ProfileResolutionError, TypeError) as exc:
                raise InvalidProfileBundleError(
                    f"profile bundle entry {entry.filename!r} is not a valid preset: {exc}"
                ) from exc
            documents.append(document)
        return documents

    def to_cli_profile(
        self,
        resolved: ResolvedProfile,
        *,
        name: str,
        settings_patch: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build an inheritance-flattened candidate JSON profile for the CLI.

        The candidate contains all resolved parent and child settings, then only
        the caller's explicit patch. The source document and its provenance are
        never mutated.
        """
        if not isinstance(resolved, ResolvedProfile):
            raise InvalidProfilePatchError("resolved must be a ResolvedProfile")
        patch: Mapping[str, Any] = {} if settings_patch is None else settings_patch
        # Reuse the profile clone validator for candidate naming and reserved keys.
        clone_profile_with_patch(resolved.profile, new_name=name, patch=patch)
        candidate = deepcopy(dict(resolved.settings))
        candidate.pop("inherits", None)
        candidate["name"] = name
        candidate["type"] = resolved.profile.kind
        candidate.update(deepcopy(dict(patch)))
        try:
            json.dumps(candidate, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise InvalidProfilePatchError(
                "resolved candidate profile must contain valid JSON values"
            ) from exc
        return candidate

    @classmethod
    def _is_profile_path(cls, filename: str) -> bool:
        parts = PurePosixPath(filename.replace("\\", "/")).parts
        return any(part.casefold() in cls.profile_directories for part in parts[:-1])

    @staticmethod
    def _validate_member_name(filename: str) -> None:
        normalized = filename.replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            path.is_absolute()
            or any(part in {"..", "."} for part in path.parts)
            or (path.parts and ":" in path.parts[0])
        ):
            raise InvalidProfileBundleError(
                f"profile bundle contains an unsafe entry path: {filename!r}"
            )

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
        except OSError as exc:
            raise InvalidProfileBundleError(f"could not read profile source {path}: {exc}") from exc
        return digest.hexdigest()

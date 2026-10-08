"""Loss-preserving Orca-style preset inheritance resolution.

The resolver deliberately does not invent defaults. Callers supply profile
scope rules because Orca presets can inherit across known bundle boundaries
(for example, a printer-specific filament inheriting from a shared library).
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


class ProfileResolutionError(ValueError):
    """Base class for invalid or unresolved profile references."""


class InvalidProfileDocumentError(ProfileResolutionError):
    """Raised when a JSON file is not a valid named Orca-style preset."""


class InvalidProfilePatchError(ProfileResolutionError):
    """Raised when a profile clone patch could corrupt its Orca identity."""


class DuplicateProfileError(ProfileResolutionError):
    """Raised when two documents claim the same typed, scoped identity."""


class UnknownProfileError(ProfileResolutionError):
    """Raised when a requested profile identity is not in the catalog."""


class AmbiguousProfileError(ProfileResolutionError):
    """Raised when a name-only profile selection has multiple matches."""


class MissingParentError(ProfileResolutionError):
    """Raised when an inherited preset cannot be found in allowed scopes."""


class InheritanceCycleError(ProfileResolutionError):
    """Raised when profile inheritance loops back to an earlier profile."""


@dataclass(frozen=True)
class ProfileDocument:
    """A raw preset plus the source context needed to resolve its parent."""

    name: str
    kind: str
    scope: str
    raw: Mapping[str, Any]
    source: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("name", self.name),
            ("kind", self.kind),
            ("scope", self.scope),
            ("source", self.source),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ProfileResolutionError(
                    f"profile {field_name} must be a non-empty string"
                )
        if not isinstance(self.raw, Mapping):
            raise ProfileResolutionError("profile raw data must be a JSON object")
        raw = deepcopy(dict(self.raw))
        if raw.get("name") not in (None, self.name):
            raise ProfileResolutionError(
                f"profile identity name {self.name!r} does not match raw name {raw['name']!r}"
            )
        if raw.get("type") not in (None, self.kind):
            raise ProfileResolutionError(
                f"profile identity kind {self.kind!r} does not match raw type {raw['type']!r}"
            )
        inherits = raw.get("inherits")
        if inherits is not None and (not isinstance(inherits, str) or not inherits.strip()):
            raise ProfileResolutionError("profile inherits field must be a non-empty string")
        object.__setattr__(self, "raw", raw)

    @classmethod
    def from_json_file(cls, path: str | Path, *, scope: str) -> "ProfileDocument":
        """Load one Orca-style preset JSON without discarding unrecognized keys."""
        source_path = Path(path)
        try:
            payload = json.loads(source_path.read_text(encoding="utf-8-sig"))
        except OSError as exc:
            raise InvalidProfileDocumentError(
                f"could not read profile JSON {source_path}: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise InvalidProfileDocumentError(
                f"profile JSON {source_path} is malformed at line {exc.lineno}, column {exc.colno}"
            ) from exc

        if not isinstance(payload, dict):
            raise InvalidProfileDocumentError(
                f"profile JSON {source_path} must contain a JSON object"
            )
        name = payload.get("name")
        kind = payload.get("type")
        if not isinstance(name, str) or not name.strip():
            raise InvalidProfileDocumentError(
                f"profile JSON {source_path} must contain a non-empty string 'name'"
            )
        if not isinstance(kind, str) or not kind.strip():
            raise InvalidProfileDocumentError(
                f"profile JSON {source_path} must contain a non-empty string 'type'"
            )
        try:
            return cls(
                name=name,
                kind=kind,
                scope=scope,
                raw=payload,
                source=str(source_path),
            )
        except ProfileResolutionError as exc:
            raise InvalidProfileDocumentError(
                f"profile JSON {source_path} is not a valid preset: {exc}"
            ) from exc

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.kind, self.scope, self.name)


@dataclass(frozen=True)
class ResolvedProfile:
    """Flattened values with per-key provenance and a root-to-child chain."""

    profile: ProfileDocument
    settings: Mapping[str, Any]
    provenance: Mapping[str, ProfileDocument]
    chain: tuple[ProfileDocument, ...]


class ProfileCatalog:
    """Index and resolve profile documents without mutating their source data.

    `parent_scopes` is ordered by preference and keyed by a profile's scope.
    A profile first looks for its parent in its own scope, then in the listed
    scopes. A missing parent never falls back to slicer defaults.
    """

    def __init__(
        self,
        documents: Iterable[ProfileDocument] = (),
        *,
        parent_scopes: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self._documents: dict[tuple[str, str, str], ProfileDocument] = {}
        self._parent_scopes: dict[str, tuple[str, ...]] = {}
        for scope, allowed_scopes in (parent_scopes or {}).items():
            if not isinstance(scope, str) or not scope.strip():
                raise ProfileResolutionError("parent-scope keys must be non-empty strings")
            if isinstance(allowed_scopes, str) or not isinstance(allowed_scopes, Sequence):
                raise ProfileResolutionError(
                    f"parent scopes for {scope!r} must be a sequence of scope names"
                )
            if any(not isinstance(item, str) or not item.strip() for item in allowed_scopes):
                raise ProfileResolutionError(
                    f"parent scopes for {scope!r} must contain non-empty strings"
                )
            self._parent_scopes[scope] = tuple(allowed_scopes)
        for document in documents:
            self.add(document)

    @property
    def documents(self) -> tuple[ProfileDocument, ...]:
        return tuple(self._documents.values())

    def add(self, document: ProfileDocument) -> None:
        if not isinstance(document, ProfileDocument):
            raise ProfileResolutionError("catalog entries must be ProfileDocument instances")
        key = document.identity
        if key in self._documents:
            raise DuplicateProfileError(
                f"duplicate profile identity {document.kind!r}/{document.scope!r}/{document.name!r}"
            )
        self._documents[key] = document

    def resolve(self, kind: str, scope: str | None, name: str) -> ResolvedProfile:
        """Resolve one selected profile; scope=None requires a unique name."""
        selected = self._select(kind, scope, name)
        return self._resolve_document(selected, ())

    def _select(self, kind: str, scope: str | None, name: str) -> ProfileDocument:
        if scope is not None:
            document = self._documents.get((kind, scope, name))
            if document is None:
                raise UnknownProfileError(
                    f"profile {kind!r}/{scope!r}/{name!r} was not found"
                )
            return document

        candidates = [
            document
            for document in self._documents.values()
            if document.kind == kind and document.name == name
        ]
        if not candidates:
            raise UnknownProfileError(f"profile {kind!r}/{name!r} was not found")
        if len(candidates) > 1:
            scopes = ", ".join(sorted(document.scope for document in candidates))
            raise AmbiguousProfileError(
                f"profile {kind!r}/{name!r} is ambiguous across scopes: {scopes}"
            )
        return candidates[0]

    def _find_parent(self, child: ProfileDocument, parent_name: str) -> ProfileDocument:
        scopes = list(dict.fromkeys((child.scope, *self._parent_scopes.get(child.scope, ()))))
        for scope in scopes:
            candidates = [
                document
                for document in self._documents.values()
                if document.kind == child.kind
                and document.scope == scope
                and document.name == parent_name
            ]
            if len(candidates) > 1:
                raise AmbiguousProfileError(
                    f"parent {parent_name!r} for {child.name!r} is ambiguous in scope {scope!r}"
                )
            if candidates:
                return candidates[0]
        searched = ", ".join(scopes)
        raise MissingParentError(
            f"parent {parent_name!r} for {child.kind!r}/{child.scope!r}/{child.name!r} "
            f"was not found in allowed scopes [{searched}]"
        )

    def _resolve_document(
        self,
        document: ProfileDocument,
        stack: tuple[tuple[str, str, str], ...],
    ) -> ResolvedProfile:
        key = document.identity
        if key in stack:
            start = stack.index(key)
            cycle_keys = (*stack[start:], key)
            cycle_names = " -> ".join(item[2] for item in cycle_keys)
            raise InheritanceCycleError(f"profile inheritance cycle: {cycle_names}")

        next_stack = (*stack, key)
        parent_name = document.raw.get("inherits")
        if parent_name is None:
            settings: dict[str, Any] = {}
            provenance: dict[str, ProfileDocument] = {}
            chain: tuple[ProfileDocument, ...] = ()
        else:
            parent = self._find_parent(document, parent_name)
            resolved_parent = self._resolve_document(parent, next_stack)
            settings = deepcopy(dict(resolved_parent.settings))
            provenance = dict(resolved_parent.provenance)
            chain = resolved_parent.chain

        for setting, value in document.raw.items():
            if setting == "inherits":
                continue
            settings[setting] = deepcopy(value)
            provenance[setting] = document

        return ResolvedProfile(
            profile=document,
            settings=settings,
            provenance=provenance,
            chain=(*chain, document),
        )


def clone_profile_with_patch(
    source: ProfileDocument,
    *,
    new_name: str,
    patch: Mapping[str, Any],
) -> dict[str, Any]:
    """Create a new raw profile document with only explicit settings changed.

    The returned document retains the source inheritance and all unknown
    fields. Version-specific bundle metadata and import validation belong to
    the Orca export adapter, which can build on this loss-preserving primitive.
    """
    if not isinstance(source, ProfileDocument):
        raise InvalidProfilePatchError("source must be a ProfileDocument")
    if not isinstance(new_name, str) or not new_name.strip():
        raise InvalidProfilePatchError("new profile name must be a non-empty string")
    if new_name == source.name:
        raise InvalidProfilePatchError("new profile name must differ from the source profile name")
    if not isinstance(patch, Mapping):
        raise InvalidProfilePatchError("profile patch must be a mapping of setting names to JSON values")

    reserved = {"name", "type", "inherits"}
    for setting in patch:
        if not isinstance(setting, str) or not setting.strip():
            raise InvalidProfilePatchError("profile patch setting names must be non-empty strings")
        if setting in reserved:
            raise InvalidProfilePatchError(
                f"profile patch cannot overwrite reserved identity field {setting!r}"
            )
    try:
        json.dumps(dict(patch), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise InvalidProfilePatchError(
            "profile patch values must be valid JSON values"
        ) from exc

    candidate = deepcopy(dict(source.raw))
    candidate["name"] = new_name
    candidate.update(deepcopy(dict(patch)))
    return candidate

"""Conservative commit-scoped license evidence; candidate scope is not T14 verification."""

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from robo_minerador.discovery.github import GitHubResponse
from robo_minerador.domain import CriterionStatus

ALLOWED_SPDX = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"})


def allowed_spdx(identifier: str | None) -> bool:
    return identifier in ALLOWED_SPDX


@dataclass(frozen=True)
class FileLicenseEvidence:
    status: str
    identifiers: tuple[str, ...]
    sha256: str
    header_base64: str
    license_path: str | None


@dataclass(frozen=True)
class LicenseAssessment:
    status: CriterionStatus
    reason_code: str
    spdx: str | None = None
    path: str | None = None
    body_base64: str = ""
    file_evidence: dict[str, FileLicenseEvidence] = field(default_factory=dict)


def _license_file(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return bool(re.fullmatch(r"(?:licen[cs]e|copying)(?:[._-].*)?", name))


def _files(
    contents: dict[str, bytes],
    root_path: str | None,
    root_spdx: str | None,
) -> dict[str, FileLicenseEvidence]:
    result = {}
    scopes = [p for p in contents if _license_file(p) and p != root_path]
    for path, body in sorted(contents.items()):
        header = body[:16_384]
        text = header.decode("utf-8", errors="replace")
        identifiers = tuple(re.findall(r"SPDX-License-Identifier:\s*([^\r\n*]+)", text))
        identifiers = tuple(item.strip() for item in identifiers)
        # Textual legal notices are ambiguity evidence, never SPDX classifiers.
        legal_notice = bool(
            re.search(
                r"GNU (?:GENERAL PUBLIC|LESSER GENERAL PUBLIC|AFFERO GENERAL PUBLIC) LICENSE"
                r"|licensed under|license terms|all rights reserved|@license\b|licen[cs]e\s*:"
                r"|subject\s+to\s+(?:the\s+)?terms\s+of"
                r"|MIT License|Apache License|BSD[^\r\n]*License|ISC License",
                text,
                re.IGNORECASE,
            )
        )
        nested = next(
            (
                p
                for p in scopes
                if path == p or ("/" in p and path.startswith(str(PurePosixPath(p).parent) + "/"))
            ),
            None,
        )
        if root_path is None:
            status = "license_scope_unresolved"
        elif any(identifier != root_spdx for identifier in identifiers):
            status = "conflicting_header"
        elif nested is not None or (path != root_path and legal_notice):
            # A matching SPDX line does not resolve an additional unparsed notice.
            status = "license_scope_unresolved"
        elif identifiers:
            status = "matching_header_candidate"
        else:
            status = "root_license_candidate"
        result[path] = FileLicenseEvidence(
            status,
            identifiers,
            hashlib.sha256(body).hexdigest(),
            base64.b64encode(header).decode("ascii"),
            nested or root_path,
        )
    return result


def assess_license(
    contents: dict[str, bytes],
    commit_sha: str,
    response: GitHubResponse | None,
    *,
    api_missing: bool = False,
) -> LicenseAssessment:
    """Compare API's original base64 body with the exact file in this commit.

    Repository metadata's default-branch SPDX is deliberately not an input.
    Transport failure is unknown, whereas confirmed absence is a rejection.
    """
    unknown_scope = _files(contents, None, None)
    if response is None:
        missing = (
            api_missing
            and not any(_license_file(p) for p in contents)
            and not any(
                b"SPDX-License-Identifier:" in content[:16_384] for content in contents.values()
            )
        )
        return LicenseAssessment(
            "rejected" if missing else "not_evaluated",
            "license_missing" if missing else "license_unverifiable",
            file_evidence=unknown_scope,
        )
    envelope = response.envelope
    if (
        envelope.get("params", {}).get("ref") != commit_sha
        or not envelope.get("endpoint", "").endswith("/license")
        or envelope.get("status") != 200
        or envelope.get("error")
    ):
        return LicenseAssessment(
            "not_evaluated", "license_unverifiable", file_evidence=unknown_scope
        )
    try:
        data = response.data
        path, encoded = data["path"], data["content"]
        if (
            not isinstance(path, str)
            or not isinstance(encoded, str)
            or data["encoding"] != "base64"
            or data.get("type", "file") != "file"
        ):
            raise ValueError
        # API may wrap base64 at 60 columns; only its whitespace is removed.
        body = base64.b64decode(re.sub(r"\s", "", encoded), validate=True)
        spdx = data.get("license", {}).get("spdx_id")
        if not isinstance(spdx, str):
            spdx = None
    except (KeyError, TypeError, AttributeError, ValueError, binascii.Error):
        return LicenseAssessment(
            "not_evaluated", "license_unverifiable", file_evidence=unknown_scope
        )
    if not body or contents.get(path) != body or "/" in path or not _license_file(path):
        return LicenseAssessment(
            "not_evaluated", "license_unverifiable", spdx, path, encoded, unknown_scope
        )
    evidence = _files(contents, path, spdx or "")
    if spdx in (None, "NOASSERTION", "OTHER") or not re.fullmatch(r"[A-Za-z0-9.+-]+", spdx):
        return LicenseAssessment(
            "not_evaluated", "license_ambiguous", spdx, path, encoded, evidence
        )
    if not allowed_spdx(spdx):
        return LicenseAssessment("rejected", "license_not_allowed", spdx, path, encoded, evidence)
    if any(
        item.status in {"conflicting_header", "license_scope_unresolved"}
        for item in evidence.values()
    ):
        return LicenseAssessment(
            "not_evaluated", "license_ambiguous", spdx, path, encoded, evidence
        )
    return LicenseAssessment(
        "approved", "license_verified_at_commit", spdx, path, encoded, evidence
    )

"""Validate, merge, report, and download UGC collection material.

The committed artifact is JSONL containing short, rewritten mechanism facts.
Raw posts, captions, transcripts, dumps, and download state belong in the
ignored cache directory and are deliberately rejected by the record schema.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import stat
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, Iterable, Sequence


TRACKS = {"live", "batch"}
ROLE_TYPES = {"host", "hype"}
ROLE_STATUSES = {"candidate", "verified"}
ATOM_TYPES = {
    "screen_self_ref",
    "bgm_chant",
    "song_chain",
    "drinking_overlay",
    "parlor_game",
    "other",
}
ADULT_LEVELS = {"none", "yellow", "adult"}
ADULT_RANK = {"none": 0, "yellow": 1, "adult": 2}
BATCH_KINDS = {
    "http",
    "huggingface",
    "wikimedia",
    "common_crawl",
    "arctic_shift",
    "academic_torrents",
    "url_list",
}

REQUIRED_FIELDS = {
    "track",
    "platform",
    "source_url",
    "captured_at",
    "language",
    "query",
    "role_terms",
    "atom_type",
    "title",
    "mechanic",
    "safety",
    "license",
    "evidence_note",
    "tags",
}
RAW_CONTENT_FIELDS = {
    "raw",
    "raw_text",
    "raw_html",
    "body_html",
    "caption",
    "full_caption",
    "transcript",
    "full_transcript",
    "source_excerpt",
    "verbatim",
    "verbatim_text",
    "lyrics",
}
TRACKING_QUERY_FIELDS = {
    "fbclid",
    "gclid",
    "dclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref_src",
    "ref_url",
    "share_source",
    "share_red_id",
    "share_medium",
    "share_plat",
    "share_session_id",
    "share_tag",
    "spm_id_from",
    "from_spmid",
    "vd_source",
    "si",
    "source",
    "xsec_source",
}
SENSITIVE_QUERY_FIELDS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "credential",
    "key",
    "oauth_token",
    "password",
    "secret",
    "session",
    "session_id",
    "sessionid",
    "sig",
    "signature",
    "signed",
    "token",
    "xsec_token",
}
RECORD_FIELDS = REQUIRED_FIELDS | {
    "id",
    "source_urls",
    "published_at",
    "region",
    "creator",
    "licenses",
    "evidence_notes",
}
ROLE_TERM_FIELDS = {"term", "role", "status"}
MECHANIC_FIELDS = {"trigger", "action", "resolution"}
SAFETY_FIELDS = {
    "forced_drinking",
    "non_alcohol_alternative",
    "adult_level",
    "refusal_guard",
    "non_alcohol_alternatives",
    "refusal_guards",
}
MANIFEST_FIELDS = {"id", "url", "filename", "license", "kind", "sha256", "enabled"}
LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
RECORD_ID_RE = re.compile(r"^ugc_[0-9a-f]{16}$")
MANIFEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
ILLEGAL_FILENAME_RE = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")
CONTENT_RANGE_RE = re.compile(r"^bytes ([0-9]+)-([0-9]+)/([0-9]+)$", re.ASCII)
INVALID_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")

DEFAULT_CHUNK_SIZE = 1024 * 1024
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024
DEFAULT_USER_AGENT = (
    "aiparty-exp-ugc-collector/1.0 "
    "(+https://github.com/z1035639466-del/aiparty-exp; public-data probe)"
)
MAX_EVIDENCE_CHARS = 500
MAX_RECORD_BYTES = 64 * 1024
MAX_URL_CHARS = 4096
MAX_SOURCE_URLS = 50
MAX_ROLE_TERMS = 50
MAX_TAGS = 100
MAX_AUXILIARY_VALUES = 100
MAX_TAG_CHARS = 120
MAX_SAFETY_CHARS = 1000


class UGCCollectionError(RuntimeError):
    """Base class for user-facing collection errors."""


class RecordFormatError(UGCCollectionError):
    pass


class RecordValidationError(UGCCollectionError):
    def __init__(self, issues: Sequence[str]):
        self.issues = tuple(issues)
        super().__init__("\n".join(self.issues))


class ManifestError(UGCCollectionError):
    pass


class DownloadError(UGCCollectionError):
    pass


@dataclass(frozen=True)
class LocatedRecord:
    value: dict[str, Any]
    path: Path | None = None
    line: int | None = None

    @property
    def label(self) -> str:
        if self.path is None:
            return "record"
        if self.line is None:
            return str(self.path)
        return f"{self.path}:{self.line}"


@dataclass(frozen=True)
class BatchItem:
    item_id: str
    url: str
    filename: str
    license: str
    kind: str = "http"
    sha256: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class DownloadResult:
    item_id: str
    status: str
    path: str
    bytes: int
    error: str = ""


def _nonempty_string(value: Any, *, maximum: int | None = None) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    return maximum is None or len(value) <= maximum


def _is_iso_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _load_strict_json(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_strict_json_object,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number {value}")
        ),
    )


def _forbidden_fields(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if isinstance(key, str) and key.casefold() in RAW_CONTENT_FIELDS:
                found.append(child_path)
            found.extend(_forbidden_fields(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_forbidden_fields(child, f"{path}[{index}]"))
    return found


def _validate_http_url(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("must be a non-empty URL")
    candidate = url.strip()
    if len(candidate) > MAX_URL_CHARS:
        raise ValueError(f"must not exceed {MAX_URL_CHARS} characters")
    if any(ord(character) <= 0x20 or ord(character) == 0x7F for character in candidate):
        raise ValueError("must not contain whitespace or control characters")
    if "\\" in candidate:
        raise ValueError("must not contain backslashes")
    if INVALID_PERCENT_RE.search(candidate):
        raise ValueError("contains an invalid percent escape")
    parts = urllib.parse.urlsplit(candidate)
    if parts.scheme.casefold() not in {"http", "https"}:
        raise ValueError("must use http or https")
    if not parts.hostname:
        raise ValueError("must include a hostname")
    if parts.username is not None or parts.password is not None:
        raise ValueError("must not embed credentials")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("has an invalid port") from exc
    if port == 0:
        raise ValueError("has an invalid port")
    return candidate


def _normalized_host(parts: urllib.parse.SplitResult) -> str:
    raw_hostname = parts.hostname or ""
    if "%" in raw_hostname:
        raise ValueError("IPv6 zone identifiers are not allowed")
    try:
        address = ipaddress.ip_address(raw_hostname)
    except ValueError:
        hostname = raw_hostname.rstrip(".")
        if not hostname:
            raise ValueError("must include a hostname")
        try:
            hostname = hostname.encode("idna").decode("ascii").casefold()
        except UnicodeError as exc:
            raise ValueError("has an invalid hostname") from exc
        labels = hostname.split(".")
        if len(hostname) > 253 or any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not re.fullmatch(r"[a-z0-9-]+", label)
            for label in labels
        ):
            raise ValueError("has an invalid hostname")
        return hostname
    if isinstance(address, ipaddress.IPv6Address):
        return f"[{address.compressed}]"
    return address.compressed


def _normalize_component(value: str, *, safe: str) -> str:
    if INVALID_PERCENT_RE.search(value):
        raise ValueError("contains an invalid percent escape")
    result: list[str] = []
    index = 0
    safe_characters = UNRESERVED | frozenset(safe)
    while index < len(value):
        character = value[index]
        if character == "%":
            octet = int(value[index + 1 : index + 3], 16)
            decoded = chr(octet)
            if decoded in UNRESERVED:
                result.append(decoded)
            else:
                result.append(f"%{octet:02X}")
            index += 3
            continue
        if character in safe_characters:
            result.append(character)
        else:
            try:
                result.append(urllib.parse.quote(character, safe=""))
            except UnicodeError as exc:
                raise ValueError("contains invalid Unicode") from exc
        index += 1
    return "".join(result)


def _query_key_name(value: str) -> str:
    try:
        decoded = urllib.parse.unquote_to_bytes(value).decode("utf-8", "strict")
    except (UnicodeDecodeError, ValueError):
        decoded = urllib.parse.unquote(value)
    return decoded.strip().casefold().replace("-", "_")


def _discard_query_field(raw_key: str) -> bool:
    key = _query_key_name(raw_key)
    if key.startswith("utm_") or key in TRACKING_QUERY_FIELDS or _sensitive_query_field(raw_key):
        return True
    return False


def _sensitive_query_field(raw_key: str) -> bool:
    key = _query_key_name(raw_key)
    compact = re.sub(r"[^a-z0-9]", "", key)
    if key in SENSITIVE_QUERY_FIELDS or compact in {
        "accesstoken",
        "apikey",
        "authorization",
        "awsaccesskeyid",
        "credential",
        "googleaccessid",
        "keypairid",
        "mstoken",
        "oauthtoken",
        "sessionid",
        "signature",
        "xbogus",
        "xsectoken",
    }:
        return True
    if compact.endswith(("signature", "credential", "apikey", "secret", "password")):
        return True
    if compact.endswith("token") and compact not in {"pagetoken", "nextpagetoken"}:
        return True
    if key.startswith(("x_amz_", "x_goog_")):
        return True
    segments = {segment for segment in re.split(r"[^a-z0-9]+", key) if segment}
    return bool(segments & {"signature", "credential", "authorization"}) or (
        "token" in segments and "page" not in segments
    )


def _url_has_sensitive_query(url: str) -> bool:
    return any(
        _sensitive_query_field(field.partition("=")[0])
        for field in urllib.parse.urlsplit(url).query.split("&")
        if field
    )


def _normalize_query(raw_query: str) -> str:
    if not raw_query:
        return ""
    normalized: list[tuple[str, str, bool]] = []
    for field in raw_query.split("&"):
        raw_key, separator, raw_value = field.partition("=")
        if _discard_query_field(raw_key):
            continue
        key = _normalize_component(raw_key, safe="!$'()*+,-./:;?@_~")
        value = _normalize_component(raw_value, safe="!$'()*+,-./:;?@_~")
        normalized.append((key, value, bool(separator)))
    return "&".join(key + (f"={value}" if had_equals else "") for key, value, had_equals in normalized)


def _remove_dot_segments(path: str) -> str:
    """Apply RFC 3986 section 5.2.4 without decoding reserved delimiters."""
    remaining = path
    output = ""
    while remaining:
        if remaining.startswith("../"):
            remaining = remaining[3:]
        elif remaining.startswith("./"):
            remaining = remaining[2:]
        elif remaining.startswith("/./"):
            remaining = "/" + remaining[3:]
        elif remaining == "/.":
            remaining = "/"
        elif remaining.startswith("/../"):
            remaining = "/" + remaining[4:]
            output = output.rsplit("/", 1)[0]
        elif remaining == "/..":
            remaining = "/"
            output = output.rsplit("/", 1)[0]
        elif remaining in {".", ".."}:
            remaining = ""
        else:
            segment_end = remaining.find("/", 1 if remaining.startswith("/") else 0)
            if segment_end < 0:
                output += remaining
                remaining = ""
            else:
                output += remaining[:segment_end]
                remaining = remaining[segment_end:]
    return output


def _platform_canonical_parts(
    scheme: str, hostname: str, path: str, query: str
) -> tuple[str, str, str, str]:
    bare_hostname = hostname.strip("[]")
    if bare_hostname in {"xiaohongshu.com", "www.xiaohongshu.com"}:
        match = re.fullmatch(r"/(?:explore|discovery/item)/([A-Za-z0-9]+)", path.rstrip("/"))
        if match:
            return "https", "www.xiaohongshu.com", f"/explore/{match.group(1)}", ""
    if bare_hostname in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}:
        if re.fullmatch(r"/@[^/]+/video/[0-9]+", path.rstrip("/")):
            return "https", "www.tiktok.com", path.rstrip("/"), ""
    if bare_hostname in {"douyin.com", "www.douyin.com"}:
        if re.fullmatch(r"/video/[0-9]+", path.rstrip("/")):
            return "https", "www.douyin.com", path.rstrip("/"), ""
    return scheme, hostname, path, query


def normalize_url(url: str) -> str:
    """Return an RFC-3986-safe source URL with tracking and secrets removed."""
    candidate = _validate_http_url(url)
    parts = urllib.parse.urlsplit(candidate)
    scheme = parts.scheme.casefold()
    hostname = _normalized_host(parts)
    port = parts.port
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    path = _remove_dot_segments(
        _normalize_component(parts.path or "/", safe="/:@!$&'()*+,;=-._~")
    )
    query = _normalize_query(parts.query)
    scheme, netloc, path, query = _platform_canonical_parts(scheme, netloc, path, query)
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def _address_is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global


def _parse_ip_literal(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        pass
    if re.fullmatch(r"(?:0[xX][0-9A-Fa-f]+|[0-9.]+)", hostname):
        try:
            packed = socket.inet_aton(hostname)
        except OSError:
            return None
        return ipaddress.IPv4Address(packed)
    return None


def assert_public_url(
    url: str,
    *,
    resolve: bool = True,
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> str:
    """Validate that a canonical HTTP URL cannot target a local/private address."""
    canonical = normalize_url(url)
    parts = urllib.parse.urlsplit(canonical)
    hostname = parts.hostname or ""
    lowered = hostname.casefold().rstrip(".")
    if lowered == "localhost" or lowered.endswith((".localhost", ".local", ".internal")):
        raise ValueError("must not target localhost or a local hostname")
    literal = _parse_ip_literal(hostname)
    if literal is not None and not _address_is_public(literal):
        raise ValueError("must not target a private, loopback, link-local, or reserved address")
    if resolve and literal is None:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            resolved = resolver(hostname, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError(f"hostname resolution failed: {exc}") from exc
        if not resolved:
            raise ValueError("hostname did not resolve")
        for result in resolved:
            try:
                address = ipaddress.ip_address(result[4][0].split("%", 1)[0])
            except (ValueError, IndexError, TypeError) as exc:
                raise ValueError("hostname resolved to an invalid address") from exc
            if not _address_is_public(address):
                raise ValueError(
                    "hostname resolves to a private, loopback, link-local, or reserved address"
                )
    return canonical


def _fingerprint_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return " ".join(normalized.split())


def mechanism_fingerprint(record: dict[str, Any]) -> str:
    mechanic = record["mechanic"]
    payload = {
        "atom_type": _fingerprint_text(record["atom_type"]),
        "trigger": _fingerprint_text(mechanic["trigger"]),
        "action": _fingerprint_text(mechanic["action"]),
        "resolution": _fingerprint_text(mechanic["resolution"]),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stable_record_id(record: dict[str, Any]) -> str:
    return f"ugc_{mechanism_fingerprint(record)[:16]}"


def _unexpected_fields(value: dict[str, Any], allowed: set[str], field: str, issues: list[str]) -> None:
    unexpected = sorted(repr(key) for key in value if not isinstance(key, str) or key not in allowed)
    if unexpected:
        issues.append(f"{field} contains unsupported fields: {', '.join(unexpected)}")


def _validate_unique_strings(
    value: Any,
    field: str,
    issues: list[str],
    *,
    allow_empty: bool = False,
    maximum_count: int = MAX_AUXILIARY_VALUES,
    maximum_chars: int = MAX_TAG_CHARS,
) -> None:
    if not isinstance(value, list):
        issues.append(f"{field} must be a list")
        return
    if len(value) > maximum_count:
        issues.append(f"{field} must contain at most {maximum_count} values")
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or (not allow_empty and not item.strip()):
            issues.append(f"{field}[{index}] must be a non-empty string")
            continue
        if len(item) > maximum_chars:
            issues.append(f"{field}[{index}] must not exceed {maximum_chars} characters")
        normalized = item.strip()
        if normalized in seen:
            issues.append(f"{field} contains duplicate value {normalized!r}")
        seen.add(normalized)


def record_issues(record: Any, *, require_id: bool = True) -> list[str]:
    """Return all schema, provenance, copyright, and safety errors."""
    issues: list[str] = []
    if not isinstance(record, dict):
        return ["record must be a JSON object"]

    _unexpected_fields(record, RECORD_FIELDS, "record", issues)
    try:
        serialized_size = len(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
    except (TypeError, ValueError):
        issues.append("record must contain only finite JSON values")
    else:
        if serialized_size > MAX_RECORD_BYTES:
            issues.append(f"record must not exceed {MAX_RECORD_BYTES} UTF-8 bytes")

    for forbidden in _forbidden_fields(record):
        issues.append(f"forbidden raw-content field: {forbidden}")

    missing = sorted(REQUIRED_FIELDS - set(record))
    if missing:
        issues.append(f"missing required fields: {', '.join(missing)}")

    if record.get("track") not in TRACKS:
        issues.append(f"track must be one of {sorted(TRACKS)}")
    if not _nonempty_string(record.get("platform"), maximum=100):
        issues.append("platform must be a non-empty string of at most 100 characters")

    source_url = record.get("source_url")
    try:
        normalized_primary = normalize_url(source_url)
    except (TypeError, ValueError) as exc:
        normalized_primary = ""
        issues.append(f"source_url {exc}")
    else:
        if source_url != normalized_primary:
            issues.append(f"source_url must be canonical; use {normalized_primary}")

    source_urls = record.get("source_urls")
    if source_urls is not None:
        if not isinstance(source_urls, list) or not source_urls:
            issues.append("source_urls must be a non-empty list when present")
        else:
            if len(source_urls) > MAX_SOURCE_URLS:
                issues.append(f"source_urls must contain at most {MAX_SOURCE_URLS} URLs")
            normalized_urls: list[str] = []
            for index, candidate in enumerate(source_urls):
                try:
                    canonical = normalize_url(candidate)
                    normalized_urls.append(canonical)
                    if candidate != canonical:
                        issues.append(f"source_urls[{index}] must be canonical; use {canonical}")
                except (TypeError, ValueError) as exc:
                    issues.append(f"source_urls[{index}] {exc}")
            if len(normalized_urls) != len(set(normalized_urls)):
                issues.append("source_urls must not contain duplicate normalized URLs")
            if normalized_primary and normalized_primary not in normalized_urls:
                issues.append("source_urls must include source_url")

    if not _is_iso_datetime(record.get("captured_at")):
        issues.append("captured_at must be an ISO-8601 datetime with timezone")
    published_at = record.get("published_at")
    if published_at is not None and not _is_iso_datetime(published_at):
        issues.append("published_at must be null or an ISO-8601 datetime with timezone")

    language = record.get("language")
    if not isinstance(language, str) or not LANGUAGE_RE.fullmatch(language):
        issues.append("language must be a BCP-47-like language tag")
    for optional_name, maximum in (("region", 100), ("creator", 200)):
        if optional_name in record and record[optional_name] is not None:
            value = record[optional_name]
            if not isinstance(value, str):
                issues.append(f"{optional_name} must be a string or null")
            elif len(value) > maximum:
                issues.append(f"{optional_name} must not exceed {maximum} characters")
    if not _nonempty_string(record.get("query"), maximum=300):
        issues.append("query must be a non-empty string of at most 300 characters")

    role_terms = record.get("role_terms")
    if not isinstance(role_terms, list):
        issues.append("role_terms must be a list")
    else:
        if len(role_terms) > MAX_ROLE_TERMS:
            issues.append(f"role_terms must contain at most {MAX_ROLE_TERMS} entries")
        seen_roles: set[str] = set()
        for index, role in enumerate(role_terms):
            prefix = f"role_terms[{index}]"
            if not isinstance(role, dict):
                issues.append(f"{prefix} must be an object")
                continue
            _unexpected_fields(role, ROLE_TERM_FIELDS, prefix, issues)
            if not _nonempty_string(role.get("term"), maximum=120):
                issues.append(f"{prefix}.term must be a non-empty string of at most 120 characters")
            if role.get("role") not in ROLE_TYPES:
                issues.append(f"{prefix}.role must be one of {sorted(ROLE_TYPES)}")
            if role.get("status") not in ROLE_STATUSES:
                issues.append(f"{prefix}.status must be one of {sorted(ROLE_STATUSES)}")
            try:
                canonical_role = json.dumps(
                    role, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            except (TypeError, ValueError):
                canonical_role = f"invalid:{index}"
            if canonical_role in seen_roles:
                issues.append(f"role_terms contains duplicate entry at index {index}")
            seen_roles.add(canonical_role)

    if record.get("atom_type") not in ATOM_TYPES:
        issues.append(f"atom_type must be one of {sorted(ATOM_TYPES)}")
    if not _nonempty_string(record.get("title"), maximum=200):
        issues.append("title must be a non-empty string of at most 200 characters")

    mechanic = record.get("mechanic")
    mechanic_valid = isinstance(mechanic, dict)
    if not mechanic_valid:
        issues.append("mechanic must be an object")
    else:
        _unexpected_fields(mechanic, MECHANIC_FIELDS, "mechanic", issues)
        for field in ("trigger", "action", "resolution"):
            if not _nonempty_string(mechanic.get(field), maximum=1000):
                issues.append(f"mechanic.{field} must be a non-empty string of at most 1000 characters")
                mechanic_valid = False

    safety = record.get("safety")
    if not isinstance(safety, dict):
        issues.append("safety must be an object")
    else:
        _unexpected_fields(safety, SAFETY_FIELDS, "safety", issues)
        forced = safety.get("forced_drinking")
        if not isinstance(forced, bool):
            issues.append("safety.forced_drinking must be boolean")
        alternative = safety.get("non_alcohol_alternative")
        if not isinstance(alternative, str):
            issues.append("safety.non_alcohol_alternative must be a string")
        elif len(alternative) > MAX_SAFETY_CHARS:
            issues.append(
                f"safety.non_alcohol_alternative must not exceed {MAX_SAFETY_CHARS} characters"
            )
        elif forced is True and not alternative.strip():
            issues.append("forced drinking requires a non-empty safety.non_alcohol_alternative")

        adult_level = safety.get("adult_level")
        if adult_level not in ADULT_LEVELS:
            issues.append(f"safety.adult_level must be one of {sorted(ADULT_LEVELS)}")
        refusal_guard = safety.get("refusal_guard")
        if not isinstance(refusal_guard, str):
            issues.append("safety.refusal_guard must be a string")
        elif len(refusal_guard) > MAX_SAFETY_CHARS:
            issues.append(f"safety.refusal_guard must not exceed {MAX_SAFETY_CHARS} characters")
        elif adult_level in {"yellow", "adult"} and not refusal_guard.strip():
            issues.append("yellow/adult material requires a non-empty safety.refusal_guard")

        if "non_alcohol_alternatives" in safety:
            _validate_unique_strings(
                safety["non_alcohol_alternatives"],
                "safety.non_alcohol_alternatives",
                issues,
                maximum_chars=MAX_SAFETY_CHARS,
            )
        if "refusal_guards" in safety:
            _validate_unique_strings(
                safety["refusal_guards"],
                "safety.refusal_guards",
                issues,
                maximum_chars=MAX_SAFETY_CHARS,
            )

    if not _nonempty_string(record.get("license"), maximum=200):
        issues.append("license must be a non-empty string of at most 200 characters")
    if "licenses" in record:
        _validate_unique_strings(record["licenses"], "licenses", issues, maximum_chars=200)
    evidence_note = record.get("evidence_note")
    if not _nonempty_string(evidence_note, maximum=MAX_EVIDENCE_CHARS):
        issues.append(f"evidence_note must be a short rewritten fact (1-{MAX_EVIDENCE_CHARS} characters)")
    if "evidence_notes" in record:
        _validate_unique_strings(
            record["evidence_notes"],
            "evidence_notes",
            issues,
            maximum_chars=MAX_EVIDENCE_CHARS,
        )
    _validate_unique_strings(
        record.get("tags"),
        "tags",
        issues,
        maximum_count=MAX_TAGS,
        maximum_chars=MAX_TAG_CHARS,
    )

    record_id = record.get("id")
    if require_id:
        if not isinstance(record_id, str) or not RECORD_ID_RE.fullmatch(record_id):
            issues.append("id must match ugc_<16 lowercase hex characters>")
        elif mechanic_valid and record.get("atom_type") in ATOM_TYPES:
            expected = stable_record_id(record)
            if record_id != expected:
                issues.append(f"id does not match mechanism fingerprint; expected {expected}")
    return issues


def validate_located_records(records: Sequence[LocatedRecord], *, require_id: bool = True) -> None:
    issues: list[str] = []
    id_locations: dict[str, str] = {}
    for located in records:
        issues.extend(f"{located.label}: {issue}" for issue in record_issues(located.value, require_id=require_id))
        record_id = located.value.get("id") if isinstance(located.value, dict) else None
        if require_id and isinstance(record_id, str) and RECORD_ID_RE.fullmatch(record_id):
            first_location = id_locations.get(record_id)
            if first_location is None:
                id_locations[record_id] = located.label
            else:
                issues.append(
                    f"{located.label}: duplicate id {record_id!r}; first seen at {first_location}"
                )
    if issues:
        raise RecordValidationError(issues)


def load_jsonl(path: Path) -> list[LocatedRecord]:
    records: list[LocatedRecord] = []
    try:
        handle = path.open("rb")
    except OSError as exc:
        raise RecordFormatError(f"{path}: cannot read: {exc}") from exc
    with handle:
        line_number = 0
        while True:
            raw_line = handle.readline(MAX_RECORD_BYTES + 3)
            if not raw_line:
                break
            line_number += 1
            content_bytes = raw_line.rstrip(b"\r\n")
            if len(content_bytes) > MAX_RECORD_BYTES:
                raise RecordFormatError(
                    f"{path}:{line_number}: record exceeds {MAX_RECORD_BYTES} UTF-8 bytes"
                )
            if not content_bytes.strip():
                continue
            try:
                raw_text = content_bytes.decode("utf-8-sig" if line_number == 1 else "utf-8")
                value = _load_strict_json(raw_text)
            except UnicodeDecodeError as exc:
                raise RecordFormatError(f"{path}:{line_number}: invalid UTF-8: {exc.reason}") from exc
            except json.JSONDecodeError as exc:
                raise RecordFormatError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
            except ValueError as exc:
                raise RecordFormatError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise RecordFormatError(f"{path}:{line_number}: each JSONL line must be an object")
            records.append(LocatedRecord(value=value, path=path, line=line_number))
    return records


def load_many_jsonl(paths: Iterable[Path]) -> list[LocatedRecord]:
    records: list[LocatedRecord] = []
    for path in paths:
        records.extend(load_jsonl(path))
    return records


def _sorted_unique_strings(values: Iterable[Any]) -> list[str]:
    cleaned = {value.strip() for value in values if isinstance(value, str) and value.strip()}
    return sorted(cleaned, key=lambda value: (_fingerprint_text(value), value))


def prepare_record(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize a merge input, generate its stable ID, and strictly validate it."""
    prepared = copy.deepcopy(record)
    prepared.pop("id", None)
    if isinstance(prepared.get("source_url"), str):
        prepared["source_url"] = normalize_url(prepared["source_url"])

    source_candidates: list[Any] = [prepared.get("source_url")]
    if isinstance(prepared.get("source_urls"), list):
        source_candidates.extend(prepared["source_urls"])
    normalized_urls: set[str] = set()
    for candidate in source_candidates:
        if isinstance(candidate, str) and candidate.strip():
            normalized_urls.add(normalize_url(candidate))
    primary = prepared.get("source_url")
    if isinstance(primary, str) and len(normalized_urls) > 1:
        prepared["source_urls"] = [primary] + sorted(normalized_urls - {primary})
    else:
        prepared.pop("source_urls", None)

    if isinstance(prepared.get("tags"), list):
        prepared["tags"] = _sorted_unique_strings(prepared["tags"])
    if isinstance(prepared.get("role_terms"), list):
        role_map: dict[str, dict[str, Any]] = {}
        for role in prepared["role_terms"]:
            if isinstance(role, dict):
                normalized_role = copy.deepcopy(role)
                if isinstance(normalized_role.get("term"), str):
                    normalized_role["term"] = normalized_role["term"].strip()
                key = json.dumps(normalized_role, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                role_map[key] = normalized_role
            else:
                role_map[f"invalid:{len(role_map)}"] = role
        prepared["role_terms"] = [role_map[key] for key in sorted(role_map)]

    for field in ("evidence_notes", "licenses"):
        if isinstance(prepared.get(field), list):
            prepared[field] = _sorted_unique_strings(prepared[field])
            if len(prepared[field]) <= 1:
                prepared.pop(field, None)
    safety = prepared.get("safety")
    if isinstance(safety, dict):
        for field in ("non_alcohol_alternatives", "refusal_guards"):
            if isinstance(safety.get(field), list):
                safety[field] = _sorted_unique_strings(safety[field])
                if len(safety[field]) <= 1:
                    safety.pop(field, None)

    preliminary = record_issues(prepared, require_id=False)
    if preliminary:
        raise RecordValidationError([f"record: {issue}" for issue in preliminary])
    prepared["id"] = stable_record_id(prepared)
    final_issues = record_issues(prepared, require_id=True)
    if final_issues:
        raise RecordValidationError([f"record: {issue}" for issue in final_issues])
    return prepared


def _values_from_singular_and_plural(record: dict[str, Any], singular: str, plural: str) -> list[Any]:
    values = [record.get(singular)]
    if isinstance(record.get(plural), list):
        values.extend(record[plural])
    return values


def _merge_group(group: Sequence[dict[str, Any]]) -> dict[str, Any]:
    canonical = min(
        group,
        key=lambda record: json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
    merged = copy.deepcopy(canonical)

    all_urls: set[str] = set()
    for record in group:
        all_urls.add(record["source_url"])
        all_urls.update(record.get("source_urls", []))
    primary = canonical["source_url"]
    merged["source_url"] = primary
    if len(all_urls) > 1:
        merged["source_urls"] = [primary] + sorted(all_urls - {primary})
    else:
        merged.pop("source_urls", None)

    merged["tags"] = _sorted_unique_strings(tag for record in group for tag in record.get("tags", []))
    roles: dict[str, dict[str, Any]] = {}
    for record in group:
        for role in record.get("role_terms", []):
            key = json.dumps(role, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            roles[key] = copy.deepcopy(role)
    merged["role_terms"] = [roles[key] for key in sorted(roles)]

    evidence_notes = _sorted_unique_strings(
        value
        for record in group
        for value in _values_from_singular_and_plural(record, "evidence_note", "evidence_notes")
    )
    if len(evidence_notes) > 1:
        merged["evidence_notes"] = evidence_notes
    else:
        merged.pop("evidence_notes", None)

    licenses = _sorted_unique_strings(
        value
        for record in group
        for value in _values_from_singular_and_plural(record, "license", "licenses")
    )
    if len(licenses) > 1:
        merged["licenses"] = licenses
    else:
        merged.pop("licenses", None)

    safety_records = [record["safety"] for record in group]
    merged_safety = copy.deepcopy(canonical["safety"])
    merged_safety["forced_drinking"] = any(safety["forced_drinking"] for safety in safety_records)
    alternatives = _sorted_unique_strings(
        value
        for safety in safety_records
        for value in _values_from_singular_and_plural(
            safety, "non_alcohol_alternative", "non_alcohol_alternatives"
        )
    )
    if alternatives:
        canonical_alternative = canonical["safety"].get("non_alcohol_alternative", "").strip()
        merged_safety["non_alcohol_alternative"] = canonical_alternative or alternatives[0]
    if len(alternatives) > 1:
        merged_safety["non_alcohol_alternatives"] = alternatives
    else:
        merged_safety.pop("non_alcohol_alternatives", None)

    merged_level = max(
        (safety["adult_level"] for safety in safety_records),
        key=lambda level: ADULT_RANK[level],
    )
    merged_safety["adult_level"] = merged_level
    refusal_guards = _sorted_unique_strings(
        value
        for safety in safety_records
        for value in _values_from_singular_and_plural(safety, "refusal_guard", "refusal_guards")
    )
    if refusal_guards:
        canonical_guard = canonical["safety"].get("refusal_guard", "").strip()
        merged_safety["refusal_guard"] = canonical_guard or refusal_guards[0]
    if len(refusal_guards) > 1:
        merged_safety["refusal_guards"] = refusal_guards
    else:
        merged_safety.pop("refusal_guards", None)
    merged["safety"] = merged_safety
    merged["id"] = stable_record_id(merged)

    issues = record_issues(merged, require_id=True)
    if issues:
        raise RecordValidationError([f"merged {merged['id']}: {issue}" for issue in issues])
    return merged


def merge_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = [prepare_record(record) for record in records]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in prepared:
        groups[mechanism_fingerprint(record)].append(record)
    merged = [_merge_group(group) for _, group in sorted(groups.items())]
    return sorted(merged, key=lambda record: record["id"])


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_jsonl(path: Path, records: Sequence[dict[str, Any]]) -> None:
    content = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for record in records
    )
    _atomic_write_text(path, content)


def build_report(records: Sequence[dict[str, Any]]) -> str:
    located = [LocatedRecord(record) for record in records]
    validate_located_records(located, require_id=True)

    source_urls = {
        url
        for record in records
        for url in [record["source_url"], *record.get("source_urls", [])]
    }
    forced = sum(record["safety"]["forced_drinking"] for record in records)
    guarded = sum(record["safety"]["adult_level"] in {"yellow", "adult"} for record in records)
    lines = [
        "# UGC Collection Report",
        "",
        f"- Records: {len(records)}",
        f"- Source URLs: {len(source_urls)}",
        f"- Forced-drinking atoms: {forced}",
        f"- Yellow/adult atoms: {guarded}",
        "",
    ]

    dimensions = (
        ("Track", Counter(record["track"] for record in records)),
        ("Platform", Counter(record["platform"] for record in records)),
        ("Language", Counter(record["language"] for record in records)),
        ("Atom type", Counter(record["atom_type"] for record in records)),
        ("Adult level", Counter(record["safety"]["adult_level"] for record in records)),
    )
    for heading, counts in dimensions:
        lines.extend([f"## {heading}", "", "| Value | Count |", "|---|---:|"])
        if counts:
            for value, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])):
                escaped = value.replace("|", "\\|")
                lines.append(f"| {escaped} | {count} |")
        else:
            lines.append("| — | 0 |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _safe_filename(value: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ManifestError("filename must be a non-empty basename")
    if len(value) > 240:
        raise ManifestError("filename must not exceed 240 characters")
    if value.endswith((".", " ")):
        raise ManifestError(f"unsafe cache filename with trailing dot/space: {value!r}")
    if ILLEGAL_FILENAME_RE.search(value) or Path(value).name != value:
        raise ManifestError(f"unsafe cache filename: {value!r}")
    device_name = value.split(".", 1)[0].rstrip(" .").upper()
    if device_name in WINDOWS_RESERVED_NAMES or re.fullmatch(r"(?:COM|LPT)[¹²³]", device_name):
        raise ManifestError(f"unsafe Windows reserved cache filename: {value!r}")
    return value


def _filename_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _manifest_filename(item_id: str, url: str, explicit: Any) -> str:
    if explicit is not None:
        return _safe_filename(explicit)
    basename = PurePosixPath(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).name
    return _safe_filename(basename or f"{item_id}.bin")


def load_batch_manifest(path: Path) -> list[BatchItem]:
    try:
        payload = _load_strict_json(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ManifestError(f"{path}: cannot read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{path}: invalid JSON: {exc.msg}") from exc
    except ValueError as exc:
        raise ManifestError(f"{path}: invalid JSON: {exc}") from exc
    entries = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ManifestError(f"{path}: manifest must be a list or an object with a sources list")

    items: list[BatchItem] = []
    seen_ids: set[str] = set()
    seen_filenames: set[str] = set()
    occupied_cache_paths: set[str] = set()
    seen_urls: set[str] = set()
    issues: list[str] = []
    for index, entry in enumerate(entries):
        label = f"{path}:sources[{index}]"
        if not isinstance(entry, dict):
            issues.append(f"{label}: must be an object")
            continue
        unexpected = sorted(set(entry) - MANIFEST_FIELDS)
        if unexpected:
            issues.append(f"{label}: unsupported fields: {', '.join(unexpected)}")
            continue
        item_id = entry.get("id")
        if not isinstance(item_id, str) or not MANIFEST_ID_RE.fullmatch(item_id):
            issues.append(f"{label}.id: invalid manifest ID")
            continue
        try:
            raw_url = _validate_http_url(entry.get("url"))
            if _url_has_sensitive_query(raw_url):
                raise ValueError("must not contain authentication, token, or signature parameters")
            url = assert_public_url(raw_url, resolve=False)
        except ValueError as exc:
            issues.append(f"{label}.url: {exc}")
            continue
        license_name = entry.get("license")
        if not _nonempty_string(license_name, maximum=200):
            issues.append(f"{label}.license: required non-empty string")
            continue
        kind = entry.get("kind", "http")
        if kind not in BATCH_KINDS:
            issues.append(f"{label}.kind: must be one of {sorted(BATCH_KINDS)}")
            continue
        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            issues.append(f"{label}.enabled: must be boolean")
            continue
        checksum = entry.get("sha256", "")
        if checksum and (not isinstance(checksum, str) or not SHA256_RE.fullmatch(checksum)):
            issues.append(f"{label}.sha256: must be 64 hexadecimal characters")
            continue
        try:
            filename = _manifest_filename(item_id, url, entry.get("filename"))
        except ManifestError as exc:
            issues.append(f"{label}: {exc}")
            continue
        if item_id in seen_ids:
            issues.append(f"{label}.id: duplicate {item_id!r}")
            continue
        filename_key = _filename_key(filename)
        part_key = _filename_key(f"{filename}.part")
        if filename_key in seen_filenames:
            issues.append(f"{label}.filename: duplicate cache name {filename!r}")
            continue
        if {filename_key, part_key} & occupied_cache_paths:
            issues.append(f"{label}.filename: cache/.part path collision for {filename!r}")
            continue
        if url in seen_urls:
            issues.append(f"{label}.url: duplicate canonical URL {url!r}")
            continue
        seen_ids.add(item_id)
        seen_filenames.add(filename_key)
        occupied_cache_paths.update((filename_key, part_key))
        seen_urls.add(url)
        items.append(
            BatchItem(
                item_id=item_id,
                url=url,
                filename=filename,
                license=license_name.strip(),
                kind=kind,
                sha256=checksum.casefold() if isinstance(checksum, str) else "",
                enabled=enabled,
            )
        )
    if issues:
        raise ManifestError("\n".join(issues))
    return items


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _safe_file_metadata(path: Path, *, allow_missing: bool = True) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return None
        raise DownloadError(f"unsafe cache path disappeared: {path}")
    if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(metadata):
        raise DownloadError(f"refusing symbolic link or reparse point: {path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise DownloadError(f"refusing non-regular cache file: {path}")
    if metadata.st_nlink != 1:
        raise DownloadError(f"refusing hard-linked cache file: {path}")
    return metadata


def _prepare_cache_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    absolute = path.absolute()
    for candidate in (absolute, *absolute.parents):
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(metadata):
            raise DownloadError(f"refusing symlink/reparse cache directory path: {candidate}")
        if not stat.S_ISDIR(metadata.st_mode):
            raise DownloadError(f"cache path component is not a directory: {candidate}")


def _safe_unlink(path: Path) -> None:
    metadata = _safe_file_metadata(path)
    if metadata is not None:
        path.unlink()


def _open_part(path: Path, *, append: bool) -> BinaryIO:
    _safe_file_metadata(path)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or _is_reparse_point(metadata)
        ):
            raise DownloadError(f"refusing linked/non-regular partial file: {path}")
        path_metadata = _safe_file_metadata(path, allow_missing=False)
        assert path_metadata is not None
        if (metadata.st_dev, metadata.st_ino) != (path_metadata.st_dev, path_metadata.st_ino):
            raise DownloadError(f"partial path changed while opening: {path}")
        return os.fdopen(descriptor, "ab" if append else "wb")
    except BaseException:
        os.close(descriptor)
        raise


def _open_bound_reader(path: Path) -> BinaryIO:
    _safe_file_metadata(path, allow_missing=False)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            str(path.absolute()),
            0x80000000,  # GENERIC_READ
            0x00000001 | 0x00000004,  # FILE_SHARE_READ | FILE_SHARE_DELETE
            None,
            3,  # OPEN_EXISTING
            0x00000080 | 0x00200000,  # FILE_ATTRIBUTE_NORMAL | OPEN_REPARSE_POINT
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle == invalid_handle:
            error = ctypes.get_last_error()
            raise OSError(error, ctypes.FormatError(error), str(path))
        try:
            descriptor = msvcrt.open_osfhandle(handle, flags)
        except BaseException:
            kernel32.CloseHandle(handle)
            raise
    else:
        descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or _is_reparse_point(metadata)
        ):
            raise DownloadError(f"refusing linked/non-regular cache file: {path}")
        path_metadata = _safe_file_metadata(path, allow_missing=False)
        assert path_metadata is not None
        if (metadata.st_dev, metadata.st_ino) != (path_metadata.st_dev, path_metadata.st_ino):
            raise DownloadError(f"cache path changed while opening: {path}")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _handle_sha256(handle: BinaryIO) -> str:
    handle.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(DEFAULT_CHUNK_SIZE), b""):
        digest.update(chunk)
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    handle = _open_bound_reader(path)
    with handle:
        return _handle_sha256(handle)


def _verified_download(path: Path, checksum: str) -> bool:
    metadata = _safe_file_metadata(path)
    if metadata is None or not checksum:
        return False
    return file_sha256(path) == checksum


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None and hasattr(response, "getcode"):
        status = response.getcode()
    return int(status or 200)


def _response_header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    value: Any = None
    if headers is not None:
        if hasattr(headers, "get"):
            value = headers.get(name)
        elif hasattr(headers, "getheader"):
            value = headers.getheader(name)
    if value is None and hasattr(response, "getheader"):
        value = response.getheader(name)
    return str(value).strip() if value is not None else None


def _decimal_header(value: str | None, field: str) -> int | None:
    if value is None:
        return None
    if len(value) > 20 or not re.fullmatch(r"[0-9]+", value):
        raise DownloadError(f"invalid {field} header: {value!r}")
    return int(value)


def _set_response_socket_timeout(response: Any, timeout: float) -> None:
    candidates = [
        response,
        getattr(response, "fp", None),
        getattr(getattr(response, "fp", None), "raw", None),
        getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None),
    ]
    for candidate in reversed(candidates):
        setter = getattr(candidate, "settimeout", None)
        if callable(setter):
            setter(max(timeout, 0.001))
            return


def _remaining_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("download deadline expired")
    return remaining


def _resolve_with_deadline(
    resolver: Callable[..., Any], host: str, port: int, deadline: float
) -> list[Any]:
    result: list[Any] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            result.extend(resolver(host, port, type=socket.SOCK_STREAM))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run, name="ugc-dns", daemon=True)
    worker.start()
    worker.join(_remaining_timeout(deadline))
    if worker.is_alive():
        raise TimeoutError(f"hostname resolution timed out for {host}")
    if errors:
        raise errors[0]
    if not result:
        raise OSError(f"hostname did not resolve: {host}")
    return result


def _public_address_results(
    host: str,
    port: int,
    *,
    resolver: Callable[..., Any],
    deadline: float,
) -> list[Any]:
    literal = _parse_ip_literal(host)
    if literal is not None:
        if not _address_is_public(literal):
            raise OSError("refusing private, loopback, link-local, or reserved address")
        if isinstance(literal, ipaddress.IPv6Address):
            return [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (str(literal), port, 0, 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (str(literal), port))]

    results = _resolve_with_deadline(resolver, host, port, deadline)
    for result in results:
        try:
            address = ipaddress.ip_address(result[4][0].split("%", 1)[0])
        except (ValueError, IndexError, TypeError) as exc:
            raise OSError("hostname resolved to an invalid address") from exc
        if not _address_is_public(address):
            raise OSError(
                "hostname resolves to a private, loopback, link-local, or reserved address"
            )
    return results


def _connect_public_socket(
    address: tuple[str, int],
    timeout: Any,
    source_address: Any,
    *,
    resolver: Callable[..., Any],
    deadline: float,
) -> socket.socket:
    host, port = address
    results = _public_address_results(host, port, resolver=resolver, deadline=deadline)
    last_error: OSError | None = None
    for family, socket_type, protocol, _canonical_name, socket_address in results:
        connection = socket.socket(family, socket_type, protocol)
        try:
            remaining = _remaining_timeout(deadline)
            if isinstance(timeout, (int, float)) and math.isfinite(timeout):
                remaining = min(remaining, float(timeout))
            connection.settimeout(max(remaining, 0.001))
            if source_address:
                connection.bind(source_address)
            connection.connect(socket_address)
            return connection
        except OSError as exc:
            last_error = exc
            connection.close()
    if last_error is not None:
        raise last_error
    raise OSError(f"no usable public address for {host}")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args: Any, resolver: Callable[..., Any], deadline: float, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._ugc_resolver = resolver
        self._ugc_deadline = deadline
        self._create_connection = self._create_pinned_connection

    def _create_pinned_connection(
        self, address: tuple[str, int], timeout: Any, source_address: Any
    ) -> socket.socket:
        return _connect_public_socket(
            address,
            timeout,
            source_address,
            resolver=self._ugc_resolver,
            deadline=self._ugc_deadline,
        )

    def send(self, data: Any) -> None:
        if self.sock is not None:
            self.sock.settimeout(max(_remaining_timeout(self._ugc_deadline), 0.001))
        super().send(data)

    def getresponse(self) -> http.client.HTTPResponse:
        if self.sock is not None:
            self.sock.settimeout(max(_remaining_timeout(self._ugc_deadline), 0.001))
        return super().getresponse()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args: Any, resolver: Callable[..., Any], deadline: float, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._ugc_resolver = resolver
        self._ugc_deadline = deadline
        self._create_connection = self._create_pinned_connection

    def _create_pinned_connection(
        self, address: tuple[str, int], timeout: Any, source_address: Any
    ) -> socket.socket:
        return _connect_public_socket(
            address,
            timeout,
            source_address,
            resolver=self._ugc_resolver,
            deadline=self._ugc_deadline,
        )

    def connect(self) -> None:
        http.client.HTTPConnection.connect(self)
        server_hostname = self._tunnel_host or self.host
        assert self.sock is not None
        self.sock.settimeout(max(_remaining_timeout(self._ugc_deadline), 0.001))
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)

    def send(self, data: Any) -> None:
        if self.sock is not None:
            self.sock.settimeout(max(_remaining_timeout(self._ugc_deadline), 0.001))
        super().send(data)

    def getresponse(self) -> http.client.HTTPResponse:
        if self.sock is not None:
            self.sock.settimeout(max(_remaining_timeout(self._ugc_deadline), 0.001))
        return super().getresponse()


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, resolver: Callable[..., Any], deadline: float):
        super().__init__()
        self.resolver = resolver
        self.deadline = deadline

    def http_open(self, request: urllib.request.Request) -> Any:
        def factory(host: str, **kwargs: Any) -> _PinnedHTTPConnection:
            return _PinnedHTTPConnection(
                host, resolver=self.resolver, deadline=self.deadline, **kwargs
            )

        return self.do_open(factory, request)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, resolver: Callable[..., Any], deadline: float):
        super().__init__()
        self.resolver = resolver
        self.deadline = deadline

    def https_open(self, request: urllib.request.Request) -> Any:
        def factory(host: str, **kwargs: Any) -> _PinnedHTTPSConnection:
            return _PinnedHTTPSConnection(
                host, resolver=self.resolver, deadline=self.deadline, **kwargs
            )

        return self.do_open(factory, request, context=self._context)


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        super().__init__()

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        absolute = urllib.parse.urljoin(request.full_url, new_url)
        if _url_has_sensitive_query(absolute):
            raise ValueError("redirect must not contain authentication or signature parameters")
        canonical = assert_public_url(absolute, resolve=False)
        return super().redirect_request(request, file_pointer, code, message, headers, canonical)

    def http_error_302(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
    ) -> Any:
        location = headers.get("location") or headers.get("uri")
        if not location:
            return None
        new_url = urllib.parse.urljoin(request.full_url, str(location))
        try:
            redirected = self.redirect_request(
                request, file_pointer, code, message, headers, new_url
            )
        except BaseException:
            file_pointer.close()
            raise
        if redirected is None:
            file_pointer.close()
            return None

        visited = getattr(request, "redirect_dict", {})
        redirected.redirect_dict = request.redirect_dict = visited
        canonical_url = redirected.full_url
        if (
            visited.get(canonical_url, 0) >= self.max_repeats
            or len(visited) >= self.max_redirections
        ):
            file_pointer.close()
            raise urllib.error.HTTPError(
                request.full_url,
                code,
                self.inf_msg + message,
                headers,
                file_pointer,
            )
        visited[canonical_url] = visited.get(canonical_url, 0) + 1
        # Do not drain an attacker-controlled redirect body. urllib does not
        # reuse this connection, and the next hop has its own pinned socket.
        file_pointer.close()
        return self.parent.open(redirected, timeout=request.timeout)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _open_response(
    request: urllib.request.Request,
    *,
    opener: Callable[..., BinaryIO] | None,
    timeout: float,
    resolver: Callable[..., Any],
    deadline: float,
    allow_test_opener: bool,
) -> BinaryIO:
    if opener is None:
        safe_opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _PinnedHTTPHandler(resolver, deadline),
            _PinnedHTTPSHandler(resolver, deadline),
            _PublicRedirectHandler(),
        )
        return safe_opener.open(request, timeout=timeout)
    if not allow_test_opener:
        raise ValueError(
            "custom opener is disabled; set allow_test_opener only for a trusted single-hop test double"
        )
    # An injected opener is an explicitly trusted, single-hop test transport.
    # Production and CLI calls leave it as None and always use pinned handlers;
    # arbitrary Python callables are code, not an SSRF security boundary.
    return opener(request, timeout=timeout)


def download_item(
    item: BatchItem,
    cache_dir: Path,
    *,
    resume: bool = False,
    opener: Callable[..., BinaryIO] | None = None,
    allow_test_opener: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> DownloadResult:
    """Download one manifest item, publishing only a verified complete partial."""
    if not item.enabled:
        return DownloadResult(item.item_id, "disabled", "", 0)
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("timeout must be positive")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    if item.sha256 and (not isinstance(item.sha256, str) or not SHA256_RE.fullmatch(item.sha256)):
        raise DownloadError(f"{item.item_id}: invalid sha256")
    checksum = item.sha256.casefold() if item.sha256 else ""
    _prepare_cache_directory(cache_dir)
    final_path = cache_dir / _safe_filename(item.filename)
    part_path = cache_dir / f"{item.filename}.part"
    existing_final = _safe_file_metadata(final_path)
    _safe_file_metadata(part_path)

    if existing_final is not None and existing_final.st_size > max_bytes:
        raise DownloadError(
            f"{item.item_id}: cached file exceeds {max_bytes} byte limit"
        )
    if resume and _verified_download(final_path, checksum):
        _safe_unlink(part_path)
        metadata = _safe_file_metadata(final_path, allow_missing=False)
        assert metadata is not None
        return DownloadResult(item.item_id, "skipped", str(final_path), metadata.st_size)
    if not resume:
        _safe_unlink(part_path)

    partial_metadata = _safe_file_metadata(part_path)
    offset = partial_metadata.st_size if resume and partial_metadata is not None else 0
    if offset > max_bytes:
        _safe_unlink(part_path)
        offset = 0
    if offset and not checksum:
        _safe_unlink(part_path)
        offset = 0
    deadline = time.monotonic() + timeout
    try:
        canonical_url = assert_public_url(item.url, resolve=False, resolver=resolver)
    except ValueError as exc:
        raise DownloadError(f"{item.item_id}: unsafe download URL: {exc}") from exc
    if canonical_url != item.url:
        raise DownloadError(f"{item.item_id}: download URL is not canonical")
    request = urllib.request.Request(canonical_url, method="GET")
    request.add_header("User-Agent", DEFAULT_USER_AGENT)
    request.add_header(
        "Accept",
        "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.8",
    )
    request.add_header("Accept-Language", "en-US,en;q=0.8")
    request.add_header("Accept-Encoding", "identity")
    if offset:
        request.add_header("Range", f"bytes={offset}-")

    integrity_failure = False
    try:
        with _open_response(
            request,
            opener=opener,
            timeout=timeout,
            resolver=resolver,
            deadline=deadline,
            allow_test_opener=allow_test_opener,
        ) as response:
            final_url = response.geturl() if hasattr(response, "geturl") else canonical_url
            if final_url:
                joined_final_url = urllib.parse.urljoin(canonical_url, str(final_url))
                try:
                    canonical_final_url = assert_public_url(
                        joined_final_url,
                        resolve=False,
                        resolver=resolver,
                    )
                except ValueError as exc:
                    integrity_failure = True
                    raise DownloadError(f"{item.item_id}: unsafe redirect target: {exc}") from exc
                if canonical_final_url != joined_final_url:
                    integrity_failure = True
                    raise DownloadError(
                        f"{item.item_id}: redirect target is not canonical or contains sensitive parameters"
                    )
            status = _response_status(response)
            content_encoding = _response_header(response, "Content-Encoding")
            if content_encoding and content_encoding.casefold() != "identity":
                integrity_failure = True
                raise DownloadError(f"{item.item_id}: unsupported Content-Encoding {content_encoding!r}")
            try:
                content_length = _decimal_header(
                    _response_header(response, "Content-Length"), "Content-Length"
                )
            except DownloadError:
                integrity_failure = True
                raise
            content_range = _response_header(response, "Content-Range")
            append = False
            expected_body_bytes = content_length
            expected_final_bytes = content_length
            if status == 206:
                if offset <= 0:
                    integrity_failure = True
                    raise DownloadError(f"{item.item_id}: unexpected 206 response without a range request")
                match = CONTENT_RANGE_RE.fullmatch(content_range or "")
                if match is None:
                    integrity_failure = True
                    raise DownloadError(f"{item.item_id}: missing or invalid Content-Range header")
                if any(len(value) > 20 for value in match.groups()):
                    integrity_failure = True
                    raise DownloadError(f"{item.item_id}: invalid Content-Range header")
                start, end, total = (int(value) for value in match.groups())
                range_length = end - start + 1
                if start != offset or end < start or end != total - 1 or total <= offset:
                    integrity_failure = True
                    raise DownloadError(
                        f"{item.item_id}: Content-Range does not complete requested offset {offset}"
                    )
                if content_length is not None and content_length != range_length:
                    integrity_failure = True
                    raise DownloadError(
                        f"{item.item_id}: Content-Length does not match Content-Range"
                    )
                append = True
                expected_body_bytes = range_length
                expected_final_bytes = total
            elif status == 200:
                if content_range is not None:
                    integrity_failure = True
                    raise DownloadError(f"{item.item_id}: unexpected Content-Range on 200 response")
            else:
                integrity_failure = True
                raise DownloadError(f"{item.item_id}: unexpected HTTP status {status}")

            if expected_final_bytes is not None and expected_final_bytes > max_bytes:
                integrity_failure = True
                raise DownloadError(
                    f"{item.item_id}: declared download size exceeds {max_bytes} bytes"
                )

            body_bytes = 0
            with _open_part(part_path, append=append) as handle:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DownloadError(f"{item.item_id}: download exceeded {timeout:g}s timeout")
                    _set_response_socket_timeout(response, remaining)
                    chunk = response.read(chunk_size)
                    if time.monotonic() > deadline:
                        raise DownloadError(f"{item.item_id}: download exceeded {timeout:g}s timeout")
                    if not chunk:
                        break
                    if not isinstance(chunk, (bytes, bytearray)):
                        integrity_failure = True
                        raise DownloadError(f"{item.item_id}: response returned non-byte content")
                    body_bytes += len(chunk)
                    total_bytes = (offset if append else 0) + body_bytes
                    if total_bytes > max_bytes:
                        integrity_failure = True
                        raise DownloadError(
                            f"{item.item_id}: download exceeds {max_bytes} byte limit"
                        )
                    handle.write(chunk)
            if expected_body_bytes is not None and body_bytes != expected_body_bytes:
                integrity_failure = True
                raise DownloadError(
                    f"{item.item_id}: response length mismatch "
                    f"(expected {expected_body_bytes}, got {body_bytes})"
                )

        partial_metadata = _safe_file_metadata(part_path, allow_missing=False)
        assert partial_metadata is not None
        if partial_metadata.st_size == 0:
            integrity_failure = True
            raise DownloadError(f"{item.item_id}: download produced an empty file")
        if expected_final_bytes is not None and partial_metadata.st_size != expected_final_bytes:
            integrity_failure = True
            raise DownloadError(
                f"{item.item_id}: partial file length mismatch "
                f"(expected {expected_final_bytes}, got {partial_metadata.st_size})"
            )
        bound_partial = _open_bound_reader(part_path)
        try:
            bound_metadata = os.fstat(bound_partial.fileno())
            if checksum:
                actual = _handle_sha256(bound_partial)
                if actual != checksum:
                    integrity_failure = True
                    raise DownloadError(
                        f"{item.item_id}: sha256 mismatch (expected {checksum}, got {actual})"
                    )
            path_metadata = _safe_file_metadata(part_path, allow_missing=False)
            assert path_metadata is not None
            if (bound_metadata.st_dev, bound_metadata.st_ino) != (
                path_metadata.st_dev,
                path_metadata.st_ino,
            ):
                integrity_failure = True
                raise DownloadError(f"{item.item_id}: partial path changed before promotion")
            _safe_file_metadata(final_path)
            os.replace(part_path, final_path)
            final_metadata = _safe_file_metadata(final_path, allow_missing=False)
            assert final_metadata is not None
            if (bound_metadata.st_dev, bound_metadata.st_ino) != (
                final_metadata.st_dev,
                final_metadata.st_ino,
            ):
                integrity_failure = True
                _safe_unlink(final_path)
                raise DownloadError(f"{item.item_id}: promoted file identity changed")
        finally:
            bound_partial.close()
        return DownloadResult(item.item_id, "downloaded", str(final_path), final_metadata.st_size)
    except DownloadError:
        if integrity_failure:
            _safe_unlink(part_path)
        else:
            partial_metadata = _safe_file_metadata(part_path)
            if partial_metadata is not None and partial_metadata.st_size == 0:
                _safe_unlink(part_path)
        raise
    except urllib.error.HTTPError as exc:
        if exc.code == 416:
            _safe_unlink(part_path)
        raise DownloadError(f"{item.item_id}: HTTP {exc.code}: {exc.reason}") from exc
    except Exception as exc:
        partial_metadata = _safe_file_metadata(part_path)
        if partial_metadata is not None and partial_metadata.st_size == 0:
            _safe_unlink(part_path)
        raise DownloadError(f"{item.item_id}: download failed: {exc}") from exc


def run_batch(
    items: Sequence[BatchItem],
    cache_dir: Path,
    *,
    jobs: int = 4,
    resume: bool = False,
    opener: Callable[..., BinaryIO] | None = None,
    allow_test_opener: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    resolver: Callable[..., Any] = socket.getaddrinfo,
) -> list[DownloadResult]:
    if jobs <= 0:
        raise ValueError("jobs must be positive")
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("timeout must be positive")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    results: dict[str, DownloadResult] = {}
    enabled = [item for item in items if item.enabled]
    for item in items:
        if not item.enabled:
            results[item.item_id] = DownloadResult(item.item_id, "disabled", "", 0)

    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {
            executor.submit(
                download_item,
                item,
                cache_dir,
                resume=resume,
                opener=opener,
                allow_test_opener=allow_test_opener,
                timeout=timeout,
                max_bytes=max_bytes,
                resolver=resolver,
            ): item
            for item in enabled
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                results[item.item_id] = future.result()
            except Exception as exc:
                results[item.item_id] = DownloadResult(item.item_id, "failed", "", 0, str(exc))
    return [results[item.item_id] for item in items]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="strictly validate committed JSONL")
    validate_parser.add_argument("--input", action="append", required=True, type=Path)

    merge_parser = subparsers.add_parser("merge", help="normalize and deduplicate JSONL inputs")
    merge_parser.add_argument("--input", action="append", required=True, type=Path)
    merge_parser.add_argument("--output", required=True, type=Path)

    report_parser = subparsers.add_parser("report", help="summarize validated JSONL")
    report_parser.add_argument("--input", action="append", required=True, type=Path)
    report_parser.add_argument("--output", type=Path)

    batch_parser = subparsers.add_parser("batch", help="download manifest sources into ignored cache")
    batch_parser.add_argument("--manifest", required=True, type=Path)
    batch_parser.add_argument("--cache-dir", type=Path, default=Path(".ugc-cache"))
    batch_parser.add_argument("--jobs", type=int, default=4)
    batch_parser.add_argument("--resume", action="store_true")
    batch_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    batch_parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_DOWNLOAD_BYTES,
        help="maximum bytes permitted for each source",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            records = load_many_jsonl(args.input)
            validate_located_records(records, require_id=True)
            print(f"Validated {len(records)} record(s) from {len(args.input)} file(s).")
            return 0

        if args.command == "merge":
            located = load_many_jsonl(args.input)
            merged = merge_records(record.value for record in located)
            write_jsonl(args.output, merged)
            print(f"Merged {len(located)} input record(s) into {len(merged)} unique atom(s).")
            return 0

        if args.command == "report":
            located = load_many_jsonl(args.input)
            validate_located_records(located, require_id=True)
            report = build_report([record.value for record in located])
            if args.output:
                _atomic_write_text(args.output, report)
                print(f"Wrote report for {len(located)} record(s) to {args.output}.")
            else:
                print(report, end="")
            return 0

        if args.command == "batch":
            if args.jobs <= 0:
                raise ManifestError("--jobs must be positive")
            if not math.isfinite(args.timeout) or args.timeout <= 0:
                raise ManifestError("--timeout must be positive")
            if args.max_bytes <= 0:
                raise ManifestError("--max-bytes must be positive")
            items = load_batch_manifest(args.manifest)
            results = run_batch(
                items,
                args.cache_dir,
                jobs=args.jobs,
                resume=args.resume,
                timeout=args.timeout,
                max_bytes=args.max_bytes,
            )
            print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))
            return 1 if any(result.status == "failed" for result in results) else 0
    except (UGCCollectionError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

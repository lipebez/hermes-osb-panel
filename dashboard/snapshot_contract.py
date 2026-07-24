"""Portable, privacy-aware snapshot contract for the dashboard UI.

The contract is intentionally independent from any vault layout or provider SDK.
Readers produce dictionaries; :func:`normalize_snapshot` exposes only the stable,
JSON-serializable fields required by the visual companion.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

SNAPSHOT_SCHEMA = "open-second-brain.dashboard.snapshot.v1"
SECRETISH_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|client[_-]?secret|secret|password|passwd|authorization|bearer|cookie)\s*[:=]\s*([^\s,;]+)"
)
SENSITIVE_HEADER_RE = re.compile(
    r"(?im)\b(authorization|proxy-authorization|cookie|set-cookie)\s*[:=]\s*[^\r\n]*"
)
BEARER_VALUE_RE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
URL_CREDENTIAL_RE = re.compile(r"(?i)\b((?:https?|wss?)://)[^/\s:@]+:[^@\s/]+@")
URL_SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|secret|password|authorization|cookie)=)[^&#\s]+"
)
BACKSLASH = chr(92)
ESCAPED_BACKSLASH = re.escape(BACKSLASH)
PATH_SEPARATOR_RE = r"[" + ESCAPED_BACKSLASH + r"/]"
FILE_URI_RE = re.compile(r"(?i)\bfile:(?:" + "/" + r"/)?" + PATH_SEPARATOR_RE + r"+[^\s\"'<>]*")
UNC_PATH_RE = re.compile(
    r"(?<![" + ESCAPED_BACKSLASH + r"\w])" + ESCAPED_BACKSLASH * 2
    + r"[^" + ESCAPED_BACKSLASH + r"\s\"'<>]+(?:" + ESCAPED_BACKSLASH
    + r"[^" + ESCAPED_BACKSLASH + r"\s\"'<>]+)+"
)
ABSOLUTE_UNIX_PATH_RE = re.compile(r"(?<![:/\w])/(?!/)[^\s\"'<>]*")
ABSOLUTE_WINDOWS_PATH_RE = re.compile(r"(?i)(?<!\w)[a-z]:" + PATH_SEPARATOR_RE + r"[^\s\"'<>]*")
POSIX_NETWORK_PATH_RE = re.compile(r"(?<![:/\w])//[^\s\"'<>]+")
PRIVATE_KEY_NORMALIZED = {
    "path",
    "configpath",
    "brainpath",
    "vault",
    "vaultpath",
    "memoryroot",
}
SECRET_VALUE_KEYS = {
    "apikey",
    "accesstoken",
    "token",
    "secret",
    "clientsecret",
    "password",
    "passwd",
    "authorization",
    "proxyauthorization",
    "bearer",
    "cookie",
    "setcookie",
    "sessioncookie",
    "credential",
    "credentials",
    "privatekey",
}
REPORT_CONTENT_KEYS = {"selectedid", "wanted", "label", "title", "body", "assets", "visibletext", "query", "path"}
REPORT_ALLOWED_NORMALIZED_KEYS = {
    re.sub(r"[^a-z0-9]", "", key.lower())
    for key in """
    active activeArea activeListenerSets activeRafs activity activityAtEnd activityBottom
    activityCollapsed activityRestored after allCount alpha app appExists appRect assertions
    autoOrbit autoRotate before bodyCss bodyRect bodyScrollHeight bodyScrollWidth bottom bounds
    boxSizing canvasCss canvasInternal canvasPainted checks clicked client clientHeight cls
    clusterExplain collapse console_errors count countText created cross_area_navigation depth detail
    deterministic disabled display distance endReachable energy expanded explorer_disclosure failures
    filter_reset fitAll fitSelection fits fixture flex focusControl focus_graph focused generated_at
    ghostSupport graph graphHeight graphList graph_3d h1 h1Weight h2 h2Weight hasResult height
    heightCss host hostDisplay host_header_scope htmlRect id inner innerDisplay innerHeight innerWidth
    inspectorActions inspectorCollapsed inspectorModes inspectorRestored internal_scroll_end_reachable
    labels lastActivityBottom layoutChain layout_controls left lifecycle live_search mainClientHeight
    mainRect mainScrollHeight metrics minHeight mobile_modes mode mouseOrbit name note ok oneHopActual
    options orbitButton overflowY paddingBottom paddingTop painted palette passed pitch pixels present
    presets pressed primaryVisible probes product_features reduced reducedAfter reducedBefore
    reducedControls reducedMotion refresh removed rendered_typography results right root rootBottom
    rootRect route running safePadding savedViews saved_views screenshot scroll scrollHeight shortcut
    sidebar sidebarAtEnd sidebarBottom sidebarCollapsed sidebarRestored splitters start statBottom state2d
    status svg switched2d switched3d tag text timelineRange titleFont top touch touchOrbit vault
    viewport viewports visible visualViewport width yaw zoom
    """.split()
}
REPORT_ENUM_VALUES = {
    "mode": {"2d", "3d"},
    "inspectormodes": {"rendered", "source"},
    "labels": {"activity", "graph", "note", "vault"},
    "visible": {"activity", "graph", "note", "vault"},
    "primaryvisible": {"activity", "graph", "note", "vault"},
    "presets": {"all", "brain", "broken", "hubs", "orphans", "projects"},
    "tag": {"BODY", "DIV", "HTML", "MAIN"},
}
REPORT_STYLE_STRING_KEYS = {
    "activitycollapsed",
    "activityrestored",
    "bottom",
    "boxsizing",
    "detail",
    "display",
    "flex",
    "h1",
    "h1weight",
    "h2",
    "h2weight",
    "height",
    "heightcss",
    "hostdisplay",
    "innerdisplay",
    "minheight",
    "note",
    "overflowy",
    "paddingbottom",
    "paddingtop",
    "pressed",
    "sidebar",
    "titlefont",
}
REPORT_STYLE_VALUE_RE = re.compile(
    r"(?i)^(?:-?\d+(?:\.\d+)?(?:px|%|rem)?|none|flex|grid|block|contents|hidden|visible|border-box|content-box|auto|true|false|0 1 auto|1 1 auto|1 1 0%)$"
)

NODE_FIELDS = (
    "label",
    "kind",
    "layer",
    "area",
    "tone",
    "status",
    "topic",
    "preview",
    "created_at",
    "modified_at",
    "size",
    "size_bytes",
    "x",
    "y",
    "degree",
    "incoming",
    "outgoing",
    "hub",
    "ghost",
    "metadata",
)


class SnapshotReader(Protocol):
    """Minimal reader boundary consumed by the dashboard API."""

    mode: str
    forbidden_values: Sequence[str]

    def read(self) -> Mapping[str, Any]: ...


class FixtureSnapshotReader:
    """Read a sanitized contract fixture without touching a configured vault."""

    mode = "fixture"
    forbidden_values: Sequence[str] = ()

    def __init__(self, source: Mapping[str, Any] | Path | str):
        self._source = source

    def read(self) -> Mapping[str, Any]:
        if isinstance(self._source, Mapping):
            return copy.deepcopy(dict(self._source))
        path = Path(self._source)
        return json.loads(path.read_text(encoding="utf-8"))


def _redact_secret(match: re.Match[str]) -> str:
    return f"{match.group(1)}=[redacted]"


def _redact_header(match: re.Match[str]) -> str:
    return f"{match.group(1)}=[redacted]"


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def is_secret_key(value: Any) -> bool:
    """Return whether a mapping key conventionally carries credential material."""

    normalized = _normalized_key(value)
    return normalized in SECRET_VALUE_KEYS or normalized.endswith(
        (
            "token",
            "password",
            "secret",
            "apikey",
            "authorization",
            "cookie",
        )
    )


def is_private_topology_key(value: Any) -> bool:
    """Return whether a mapping key identifies local filesystem or vault topology."""

    normalized = _normalized_key(value)
    return (
        normalized in PRIVATE_KEY_NORMALIZED
        or normalized.endswith("path")
        or normalized.endswith("vault")
        or normalized.endswith("memoryroot")
    )


def sanitize_text(value: Any, forbidden_values: Sequence[str] = ()) -> str:
    """Redact secret-like values, absolute paths and explicit local identifiers."""

    text = str(value or "")
    contains_absolute_path = any(
        pattern.search(text)
        for pattern in (
            FILE_URI_RE,
            UNC_PATH_RE,
            POSIX_NETWORK_PATH_RE,
            ABSOLUTE_UNIX_PATH_RE,
            ABSOLUTE_WINDOWS_PATH_RE,
        )
    )
    text = SENSITIVE_HEADER_RE.sub(_redact_header, text)
    text = BEARER_VALUE_RE.sub("[redacted]", text)
    text = URL_CREDENTIAL_RE.sub(r"\1[redacted]@", text)
    text = URL_SECRET_QUERY_RE.sub(r"\1[redacted]", text)
    text = SECRETISH_RE.sub(_redact_secret, text)
    text = FILE_URI_RE.sub("[redacted-path]", text)
    text = UNC_PATH_RE.sub("[redacted-path]", text)
    text = ABSOLUTE_UNIX_PATH_RE.sub("[redacted-path]", text)
    text = ABSOLUTE_WINDOWS_PATH_RE.sub("[redacted-path]", text)
    if contains_absolute_path:
        text = "[redacted-path]"
    for forbidden in sorted({str(item) for item in forbidden_values if str(item)}, key=len, reverse=True):
        text = re.sub(re.escape(forbidden), "[redacted]", text, flags=re.IGNORECASE)
    return text


def _sanitize_value(value: Any, forbidden_values: Sequence[str]) -> Any:
    if isinstance(value, str):
        return sanitize_text(value, forbidden_values)
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            if is_private_topology_key(key):
                continue
            clean_key = sanitize_text(key, forbidden_values)
            sanitized[clean_key] = "[redacted]" if is_secret_key(key) else _sanitize_value(item, forbidden_values)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item, forbidden_values) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_text(value, forbidden_values)


def _stable_id(value: Any, forbidden_values: Sequence[str]) -> str:
    original = str(value or "").strip()
    if (
        original.startswith("/")
        or FILE_URI_RE.search(original)
        or UNC_PATH_RE.search(original)
        or ABSOLUTE_WINDOWS_PATH_RE.search(original)
        or ABSOLUTE_UNIX_PATH_RE.search(original)
    ):
        return "external-" + hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
    raw = original.replace("\\", "/")
    clean = sanitize_text(raw.lstrip("./"), forbidden_values).strip()
    return clean or "node-unknown"


def sanitize_report_payload(value: Any, key: str = "") -> Any:
    """Remove rendered content and sanitize every retained diagnostic string."""

    normalized_key = _normalized_key(key)
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for item_key, item_value in value.items():
            normalized_item_key = _normalized_key(item_key)
            if normalized_item_key in REPORT_CONTENT_KEYS:
                continue
            clean_key = (
                str(item_key)
                if normalized_item_key in REPORT_ALLOWED_NORMALIZED_KEYS
                else "field-" + hashlib.sha256(str(item_key).encode("utf-8")).hexdigest()[:12]
            )
            sanitized[clean_key] = (
                "[redacted]"
                if is_secret_key(item_key)
                else sanitize_report_payload(item_value, str(item_key))
            )
        return sanitized
    if isinstance(value, (list, tuple)):
        return [sanitize_report_payload(item, key) for item in value]
    if isinstance(value, str):
        if normalized_key == "screenshot":
            portable_name = re.split(r"[\\/]", value)[-1]
            return (
                portable_name
                if re.fullmatch(r"second-brain-\d+x\d+\.png", portable_name)
                else "[redacted]"
            )
        text = sanitize_text(value)
        if normalized_key == "fixture":
            return Path(text).name
        if normalized_key == "route":
            return text if text == "/second-brain" else "[redacted]"
        if normalized_key == "generatedat":
            return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.+-]+Z?", text) else "[redacted]"
        if normalized_key == "name":
            return text if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", text) else "[redacted]"
        if normalized_key in REPORT_ENUM_VALUES:
            return text if text in REPORT_ENUM_VALUES[normalized_key] else "[redacted]"
        if normalized_key in REPORT_STYLE_STRING_KEYS:
            return text if REPORT_STYLE_VALUE_RE.fullmatch(text) else "[redacted]"
        return "[redacted]"
    return value


def _normalize_nodes(
    values: Any, forbidden_values: Sequence[str]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    nodes: list[dict[str, Any]] = []
    id_map: dict[str, str] = {}
    used: set[str] = set()
    for index, raw_value in enumerate(values if isinstance(values, list) else []):
        if not isinstance(raw_value, Mapping):
            continue
        original_id = str(raw_value.get("id") or f"node-{index}")
        node_id = _stable_id(original_id, forbidden_values)
        if node_id in used:
            node_id = f"{node_id}-{hashlib.sha256(original_id.encode()).hexdigest()[:8]}"
        used.add(node_id)
        id_map[original_id] = node_id
        node: dict[str, Any] = {"id": node_id}
        for field in NODE_FIELDS:
            if field in raw_value:
                node[field] = _sanitize_value(raw_value[field], forbidden_values)
        node.setdefault("label", node_id.rsplit("/", 1)[-1].removesuffix(".md"))
        node.setdefault("kind", "note")
        node.setdefault("metadata", {})
        nodes.append(node)
    nodes.sort(key=lambda item: str(item["id"]))
    return nodes, id_map


def _normalize_edges(
    values: Any, id_map: Mapping[str, str], node_ids: set[str], forbidden_values: Sequence[str]
) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw_value in values if isinstance(values, list) else []:
        if not isinstance(raw_value, Mapping):
            continue
        raw_source = str(raw_value.get("source") or "")
        raw_target = str(raw_value.get("target") or "")
        source = id_map.get(raw_source, _stable_id(raw_source, forbidden_values))
        target = id_map.get(raw_target, _stable_id(raw_target, forbidden_values))
        kind = sanitize_text(raw_value.get("kind") or "related", forbidden_values)
        key = (source, target, kind)
        if source in node_ids and target in node_ids and key not in seen:
            seen.add(key)
            edges.append({"source": source, "target": target, "kind": kind})
    edges.sort(key=lambda item: (item["source"], item["target"], item["kind"]))
    return edges


def _sorted_records(
    values: Any, forbidden_values: Sequence[str], *, reverse: bool = False
) -> list[dict[str, Any]]:
    records = [
        _sanitize_value(value, forbidden_values)
        for value in (values if isinstance(values, list) else [])
        if isinstance(value, Mapping)
    ]
    return sorted(
        records,
        key=lambda item: (
            str(item.get("id") or item.get("timestamp") or item.get("time") or ""),
            json.dumps(item, sort_keys=True, ensure_ascii=False),
        ),
        reverse=reverse,
    )


def normalize_snapshot(
    raw_snapshot: Mapping[str, Any] | None,
    *,
    forbidden_values: Sequence[str] = (),
    reader_mode: str | None = None,
) -> dict[str, Any]:
    """Normalize a reader payload into the versioned dashboard contract.

    Unknown provider fields and filesystem topology are dropped. Optional graph,
    metrics and activity surfaces fail soft to empty collections.
    """

    raw = dict(raw_snapshot or {})
    graph_value = raw.get("graph")
    graph: Mapping[str, Any] = graph_value if isinstance(graph_value, Mapping) else {}
    nodes, id_map = _normalize_nodes(graph.get("nodes", []), forbidden_values)
    node_ids = {str(node["id"]) for node in nodes}
    edges = _normalize_edges(graph.get("edges", []), id_map, node_ids, forbidden_values)

    provider_value = raw.get("provider")
    provider_raw: Mapping[str, Any] = provider_value if isinstance(provider_value, Mapping) else {}
    available = bool(provider_raw.get("available"))
    provider: dict[str, Any] = {
        "name": sanitize_text(provider_raw.get("name") or "open-second-brain", forbidden_values),
        "available": available,
        "health": sanitize_text(provider_raw.get("health") or ("ok" if available else "unavailable"), forbidden_values),
        "semantic": sanitize_text(provider_raw.get("semantic") or "unknown", forbidden_values),
        "mode": sanitize_text(reader_mode or provider_raw.get("mode") or "normalized", forbidden_values),
    }
    counts = _sanitize_value(raw.get("counts") if isinstance(raw.get("counts"), Mapping) else {}, forbidden_values)
    metrics = _sanitize_value(raw.get("metrics") if isinstance(raw.get("metrics"), Mapping) else {}, forbidden_values)
    capabilities = _sanitize_value(
        raw.get("capabilities") if isinstance(raw.get("capabilities"), Mapping) else {"graph_3d": True},
        forbidden_values,
    )
    capabilities["graph_3d"] = bool(capabilities.get("graph_3d", True))
    graph_summary_value = raw.get("graph_summary")
    graph_summary_raw: Mapping[str, Any] = (
        graph_summary_value if isinstance(graph_summary_value, Mapping) else {}
    )
    graph_summary = {
        "hub_ids": sorted(id_map.get(str(value), _stable_id(value, forbidden_values)) for value in graph_summary_raw.get("hub_ids", []) if id_map.get(str(value), _stable_id(value, forbidden_values)) in node_ids),
        "orphan_ids": sorted(id_map.get(str(value), _stable_id(value, forbidden_values)) for value in graph_summary_raw.get("orphan_ids", []) if id_map.get(str(value), _stable_id(value, forbidden_values)) in node_ids),
        "broken_count": int(graph_summary_raw.get("broken_count") or 0),
    }
    broken_links = _sorted_records(raw.get("broken_links"), forbidden_values)
    recent_logs = _sorted_records(raw.get("recent_logs"), forbidden_values, reverse=True)
    timeline = _sorted_records(raw.get("timeline_events"), forbidden_values, reverse=True)
    artifacts = _sorted_records(raw.get("artifacts"), forbidden_values)
    vault_notes = _sorted_records(raw.get("vault_notes"), forbidden_values)
    limits = _sanitize_value(raw.get("limits") if isinstance(raw.get("limits"), Mapping) else {}, forbidden_values)
    active_preview = sanitize_text(raw.get("active_preview") or "", forbidden_values)

    generated_at = sanitize_text(
        raw.get("generated_at") or datetime.now(timezone.utc).isoformat(), forbidden_values
    )
    revision = sanitize_text(raw.get("revision") or "", forbidden_values)
    if not revision:
        canonical = json.dumps({"nodes": nodes, "edges": edges}, sort_keys=True, ensure_ascii=False)
        revision = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        "generated_at": generated_at,
        "revision": revision,
        "provider": provider,
        "nodes": nodes,
        "edges": edges,
        "summaries": {"counts": counts, "graph": graph_summary},
        "limits": limits,
        "activity": {"active_preview": active_preview, "recent": recent_logs, "timeline": timeline},
        "metrics": metrics,
        "capabilities": capabilities,
        # Compatibility projection retained while the local v3 UI migrates.
        "counts": counts,
        "graph": {"nodes": nodes, "edges": edges},
        "graph_summary": graph_summary,
        "broken_links": broken_links,
        "active_preview": active_preview,
        "recent_logs": recent_logs,
        "timeline_events": timeline,
        "artifacts": artifacts,
        "vault_notes": vault_notes,
    }
    return snapshot

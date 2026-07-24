"""Open Second Brain dashboard companion backend.

Mounted at ``/api/plugins/hermes-osb-panel/`` by a compatible dashboard host.
The reusable API returns a normalized, privacy-aware snapshot. Direct Markdown
traversal remains available only through an explicitly named prototype reader.
"""
from __future__ import annotations

import hashlib
import importlib.util
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

try:
    from dashboard.snapshot_contract import normalize_snapshot
except ModuleNotFoundError as exc:
    if exc.name not in {"dashboard", "dashboard.snapshot_contract"}:
        raise
    # Hermes imports plugin APIs directly from their manifest path, outside a
    # package namespace. Load only the adjacent contract module without
    # mutating sys.path or coupling the companion to the host installation.
    contract_path = Path(__file__).with_name("snapshot_contract.py")
    contract_name = "_hermes_osb_panel_snapshot_contract"
    contract_spec = importlib.util.spec_from_file_location(contract_name, contract_path)
    if contract_spec is None or contract_spec.loader is None:
        raise ImportError(f"Cannot load snapshot contract from {contract_path}") from exc
    contract_module = importlib.util.module_from_spec(contract_spec)
    sys.modules[contract_name] = contract_module
    contract_spec.loader.exec_module(contract_module)
    normalize_snapshot = contract_module.normalize_snapshot


class SnapshotReader(Protocol):
    """Minimal reader seam consumed by :func:`build_snapshot`."""

    mode: str
    forbidden_values: Sequence[str]

    def read(self) -> Dict[str, Any]: ...

try:
    from fastapi import APIRouter
except Exception:  # pragma: no cover - lets unit tests import without FastAPI.
    class APIRouter:  # type: ignore[no-redef]
        def get(self, *_args: Any, **_kwargs: Any):
            return lambda fn: fn


router = APIRouter()

DEFAULT_CONFIG = Path.home() / ".config" / "open-second-brain" / "config.yaml"
DEFAULT_HERMES_MEMORIES = Path.home() / ".hermes" / "memories"
MAX_PREVIEW_CHARS = 6000
MAX_GRAPH_FILES = 160
MAX_VAULT_FILES = 220
MAX_LOG_EVENTS = 12
MAX_VAULT_NOTES = 80
DIRECT_MARKDOWN_OPT_IN_ENV = "HERMES_OSB_PANEL_ENABLE_DIRECT_MARKDOWN"
_DIRECT_MARKDOWN_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
SECRETISH_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|bearer)\s*[:=]\s*([^\s]+)"
)

KIND_ORDER = {
    "active": 0,
    "preference": 1,
    "signal": 2,
    "log": 3,
    "retired": 4,
    "note": 5,
}

CLUSTERS = {
    "active": (320.0, 220.0),
    "preference": (560.0, 180.0),
    "signal": (180.0, 190.0),
    "log": (220.0, 410.0),
    "retired": (520.0, 450.0),
    "note": (410.0, 305.0),
    "vault": (760.0, 310.0),
}

VAULT_AREA_RULES: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ("inbox", "inbox", ("00 inbox",)),
    ("projects", "projects", ("02 projetos",)),
    ("clients", "clients", ("01 clientes",)),
    ("runbooks", "runbooks", ("04 runbooks",)),
    ("decisions", "decisions", ("07 decisões", "07 decisoes")),
    ("references", "references", ("08 referências", "08 referencias")),
    ("templates", "templates", ("09 templates",)),
)


def _redact(match: re.Match[str]) -> str:
    return f"{match.group(1)}=[redacted]"


def _safe_text(path: Path, limit: int = MAX_PREVIEW_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    text = SECRETISH_RE.sub(_redact, text)
    if len(text) > limit:
        return text[:limit].rstrip() + "\n\n…"
    return text


def _safe_text_head(path: Path, limit: int = 2800) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    text = SECRETISH_RE.sub(_redact, text)
    if len(text) > limit:
        return text[:limit].rstrip() + "\n\n…"
    return text


def _parse_simple_yaml(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not path.exists():
        return data
    for line in _safe_text(path, limit=4000).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and not value.startswith("[") and not value.startswith("{"):
            data[key] = value
    return data


def direct_markdown_opted_in() -> bool:
    """Return whether the process received the explicit local prototype opt-in."""

    value = os.environ.get(DIRECT_MARKDOWN_OPT_IN_ENV)
    return bool(value and value.strip().casefold() in _DIRECT_MARKDOWN_TRUE_VALUES)


def resolve_config_path(config_path: Path | None = None) -> Path:
    if config_path is not None:
        return config_path
    env_path = os.environ.get("OPEN_SECOND_BRAIN_CONFIG") or os.environ.get("O2B_CONFIG")
    return Path(env_path).expanduser() if env_path else DEFAULT_CONFIG


def resolve_vault(vault: Path | str | None = None, config_path: Path | None = None) -> Path | None:
    if vault is not None:
        return Path(vault).expanduser()
    config = _parse_simple_yaml(resolve_config_path(config_path))
    configured = config.get("vault")
    return Path(configured).expanduser() if configured else None


def _brain_dir(vault: Path) -> Path:
    return vault / "Brain"


def _kind_for_rel(rel: str) -> str:
    if rel == "Brain/active.md":
        return "active"
    parts = set(rel.split("/"))
    if "preferences" in parts:
        return "preference"
    if "inbox" in parts:
        return "signal"
    if "retired" in parts:
        return "retired"
    if "log" in parts:
        return "log"
    return "note"


def _label_for(path: Path, rel: str, text: str) -> str:
    match = HEADING_RE.search(text)
    title = match.group(1).strip() if match else path.stem
    for prefix in ("pref-", "ret-", "sig-"):
        if title.startswith(prefix):
            title = title[len(prefix):]
    title = title.replace("-", " ").strip()
    if rel == "Brain/active.md":
        return "Active Memory"
    return title[:60] or path.stem


def _vault_label_for(path: Path, text: str) -> str:
    match = HEADING_RE.search(text)
    title = match.group(1).strip() if match else path.stem
    title = title.replace(" — ", " - ").replace("_", " ").replace("-", " ").strip()
    return title[:64] or path.stem


def _frontmatter_value(text: str, key: str) -> str:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*['\"]?([^'\"\n]+)", text)
    return match.group(1).strip() if match else ""


def _brain_inventory(brain: Path) -> List[Path]:
    if not brain.exists():
        return []
    files = [
        p
        for p in brain.rglob("*.md")
        if p.is_file() and ".snapshots" not in p.parts and ".trash" not in p.parts
    ]

    def key(path: Path) -> Tuple[int, str]:
        try:
            rel = path.relative_to(brain.parent).as_posix()
        except Exception:
            rel = path.as_posix()
        return (KIND_ORDER.get(_kind_for_rel(rel), 9), rel)

    return sorted(files, key=key)


def _markdown_files(brain: Path) -> List[Path]:
    return _brain_inventory(brain)[:MAX_GRAPH_FILES]


def _vault_inventory(vault: Path, brain: Path) -> List[Path]:
    if not vault.exists():
        return []
    files = [
        p
        for p in vault.rglob("*.md")
        if p.is_file()
        and not p.is_relative_to(brain)
        and ".git" not in p.parts
        and ".obsidian" not in p.parts
        and ".trash" not in p.parts
    ]
    return sorted(files, key=lambda p: p.relative_to(vault).as_posix())


def _vault_md_files(vault: Path, brain: Path) -> List[Path]:
    return _vault_inventory(vault, brain)[:MAX_VAULT_FILES]


def _file_metadata(path: Path) -> Dict[str, Any]:
    try:
        stat = path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        return {"modified_at": modified, "size_bytes": stat.st_size}
    except OSError:
        return {"modified_at": "", "size_bytes": 0}


def _aliases_for(rel: str, label: str) -> Iterable[str]:
    path = Path(rel)
    stem = path.stem
    cleaned = stem
    for prefix in ("pref-", "ret-", "sig-"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
    normalized_label = label.replace("—", "-").replace("–", "-").replace("_", " ").strip()
    slugified = cleaned.replace("-", " ").replace("_", " ").strip()
    values = {rel, stem, cleaned, slugified, label, normalized_label}
    if rel == "Brain/active.md":
        values.update({"active", "active.md", "active memory", "active brain preferences"})
    for value in values:
        normalized = str(value).strip().lower()
        if normalized:
            yield normalized


def _vault_area(rel: str) -> str:
    lower = rel.lower()
    for area_id, _, prefixes in VAULT_AREA_RULES:
        for prefix in prefixes:
            if lower.startswith(prefix + "/") or lower.startswith(prefix):
                return area_id
    return "other"


def _position_nodes(grouped: Dict[str, List[Dict[str, Any]]]) -> None:
    for kind, items in grouped.items():
        cx, cy = CLUSTERS.get(kind, CLUSTERS["note"])
        count = len(items)
        for index, node in enumerate(items):
            if count == 1:
                x, y = cx, cy
            else:
                angle = (math.tau * index / count) - math.pi / 2
                radius = 56 + min(128, count * 6)
                x = cx + math.cos(angle) * radius
                y = cy + math.sin(angle) * radius
            node["x"] = round(x, 2)
            node["y"] = round(y, 2)


def _build_graph(vault: Path, brain_files: List[Path], vault_files: List[Path]) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []
    raw: Dict[str, str] = {}
    aliases: Dict[str, str] = {}
    grouped: Dict[str, List[Dict[str, Any]]] = {}

    for path in brain_files:
        try:
            rel = path.relative_to(vault).as_posix()
        except Exception:
            rel = path.as_posix()
        text = _safe_text(path, limit=5000)
        kind = _kind_for_rel(rel)
        label = _label_for(path, rel, text)
        node = {
            "id": rel,
            "label": label,
            "kind": kind,
            "tone": kind,
            "layer": "brain",
            "area": "brain",
            "status": _frontmatter_value(text, "status"),
            "topic": _frontmatter_value(text, "topic"),
            "path": str(path),
            "size": max(8, min(20, 8 + len(text) // 800)),
            "preview": text[:420].strip(),
            **_file_metadata(path),
        }
        nodes.append(node)
        raw[rel] = text
        grouped.setdefault(kind, []).append(node)
        for alias in _aliases_for(rel, label):
            aliases.setdefault(alias, rel)

    for path in vault_files:
        try:
            rel = path.relative_to(vault).as_posix()
        except Exception:
            rel = path.as_posix()
        text = _safe_text_head(path, limit=2600)
        label = _vault_label_for(path, text)
        area = _vault_area(rel)
        node = {
            "id": rel,
            "label": label,
            "kind": "vault",
            "tone": "vault",
            "layer": "vault",
            "area": area,
            "status": "",
            "topic": area,
            "path": str(path),
            "size": max(7, min(17, 7 + len(text) // 1100)),
            "preview": text[:420].strip(),
            **_file_metadata(path),
        }
        nodes.append(node)
        raw[rel] = text
        grouped.setdefault("vault", []).append(node)
        for alias in (rel, path.stem, label, area):
            normalized = str(alias).strip().lower()
            if normalized:
                aliases.setdefault(normalized, rel)

    _position_nodes(grouped)

    edge_keys: set[Tuple[str, str, str]] = set()
    edges: List[Dict[str, Any]] = []
    broken_links: List[Dict[str, str]] = []
    broken_keys: set[Tuple[str, str]] = set()
    for rel, text in raw.items():
        for target_label in WIKILINK_RE.findall(text):
            clean_target = target_label.strip()
            target = aliases.get(clean_target.lower())
            if not target:
                broken_key = (rel, clean_target)
                if clean_target and broken_key not in broken_keys:
                    broken_keys.add(broken_key)
                    broken_links.append({"source": rel, "target": clean_target})
                continue
            if target == rel:
                continue
            edge_key = (rel, target, "wikilink")
            if edge_key not in edge_keys:
                edge_keys.add(edge_key)
                edges.append({"source": rel, "target": target, "kind": "wikilink"})

    active = "Brain/active.md" if any(n["id"] == "Brain/active.md" for n in nodes) else None
    if active:
        for node in nodes:
            if node["id"] == active or node["kind"] not in {"preference", "signal", "log"}:
                continue
            edge_key = (active, node["id"], "surface")
            if edge_key not in edge_keys:
                edge_keys.add(edge_key)
                edges.append({"source": active, "target": node["id"], "kind": "surface"})

    degree: Dict[str, int] = {node["id"]: 0 for node in nodes}
    incoming: Dict[str, int] = {node["id"]: 0 for node in nodes}
    outgoing: Dict[str, int] = {node["id"]: 0 for node in nodes}
    for edge in edges:
        source, target = edge["source"], edge["target"]
        if source in degree:
            degree[source] += 1
            outgoing[source] += 1
        if target in degree:
            degree[target] += 1
            incoming[target] += 1

    ranked_hubs = sorted(
        (node_id for node_id, value in degree.items() if value >= 3),
        key=lambda node_id: (-degree[node_id], node_id),
    )[:12]
    hub_ids = set(ranked_hubs)
    orphan_ids = sorted(node_id for node_id, value in degree.items() if value == 0)
    for node in nodes:
        node_id = node["id"]
        node["degree"] = degree[node_id]
        node["incoming"] = incoming[node_id]
        node["outgoing"] = outgoing[node_id]
        node["hub"] = node_id in hub_ids

    return {
        "nodes": nodes,
        "edges": edges,
        "broken_links": broken_links,
        "summary": {
            "hub_ids": ranked_hubs,
            "orphan_ids": orphan_ids,
            "broken_count": len(broken_links),
        },
    }


def _count_dir(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for p in path.rglob("*.md") if p.is_file())


def _count_markdown_memory_entries(text: str) -> int:
    """Count compact Hermes MEMORY/USER entries without exposing their content."""
    parts = re.split(r"(?m)^\s*§\s*$", text or "")
    count = 0
    for part in parts:
        cleaned = part.strip()
        if not cleaned:
            continue
        # Ignore generated metadata blocks if they ever appear in the same file.
        if cleaned.startswith("---") and "kind: brain-active" in cleaned[:260]:
            continue
        count += 1
    return count


def _hermes_memory_counts(memory_root: Path = DEFAULT_HERMES_MEMORIES) -> Dict[str, Any]:
    user_path = memory_root / "USER.md"
    memory_path = memory_root / "MEMORY.md"

    def read_count(path: Path) -> int:
        try:
            return _count_markdown_memory_entries(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return 0

    user_entries = read_count(user_path)
    memory_entries = read_count(memory_path)
    return {
        "available": bool(user_path.exists() or memory_path.exists()),
        "user": user_entries,
        "memory": memory_entries,
        "total": user_entries + memory_entries,
        "path": str(memory_root),
    }


def _parse_log_events(log_files: List[Path]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    ordered = sorted(log_files, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    for path in ordered:
        text = _safe_text(path, limit=12000)
        current: Optional[Dict[str, Any]] = None
        for line in text.splitlines():
            heading = re.match(r"^##\s+(.+?)\s+—\s+(.+?)\s*$", line)
            if heading:
                if current:
                    events.append(current)
                time_text = heading.group(1).strip()
                date_match = re.search(r"\d{4}-\d{2}-\d{2}", path.stem)
                time_match = re.search(r"\d{2}:\d{2}(?::\d{2})?", time_text)
                timestamp = ""
                if date_match and time_match:
                    clock = time_match.group(0)
                    if len(clock) == 5:
                        clock += ":00"
                    timestamp = f"{date_match.group(0)}T{clock}+00:00"
                current = {
                    "time": time_text,
                    "timestamp": timestamp,
                    "kind": heading.group(2).strip(),
                    "text": "",
                    "agent": "",
                    "path": str(path),
                }
                continue
            if current is None:
                continue
            if line.startswith("- text:"):
                current["text"] = line.split(":", 1)[1].strip()
            elif line.startswith("- agent:"):
                current["agent"] = line.split(":", 1)[1].strip()
        if current:
            events.append(current)
        if len(events) >= MAX_LOG_EVENTS:
            break
    return events[:MAX_LOG_EVENTS]


def _snapshot_revision(vault: Path, files: List[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.as_posix()):
        try:
            stat = path.stat()
            rel = path.relative_to(vault).as_posix()
            digest.update(f"{rel}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
        except OSError:
            continue
    return digest.hexdigest()[:16]


def _timeline_events(graph: Dict[str, Any], log_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = [dict(event) for event in log_events]
    for node in graph.get("nodes", []):
        timestamp = str(node.get("modified_at") or "")
        if not timestamp:
            continue
        events.append(
            {
                "timestamp": timestamp,
                "time": timestamp,
                "kind": "modified",
                "text": f"{node.get('label') or node.get('id')} atualizado",
                "agent": "filesystem",
                "path": node.get("path", ""),
                "node_id": node.get("id", ""),
                "area": node.get("area", ""),
            }
        )
    return sorted(
        events,
        key=lambda event: (str(event.get("timestamp") or ""), str(event.get("path") or "")),
        reverse=True,
    )[:80]


def _status_summary(vault: Path | None, config_path: Path) -> Dict[str, Any]:
    config = _parse_simple_yaml(config_path)
    brain = _brain_dir(vault) if vault is not None else None
    search_db = vault / ".open-second-brain" / "brain.sqlite" if vault is not None else None
    summary: Dict[str, Any] = {
        "name": "open-second-brain",
        "available": bool(vault is not None and vault.exists() and brain is not None and brain.exists()),
        "semantic": "indexed" if search_db is not None and search_db.exists() else "off/unknown",
    }
    safe_vault_id = config.get("obsidian_vault_id") or config.get("obsidian_vault") or ""
    if safe_vault_id and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}", safe_vault_id):
        summary["obsidian_vault_id"] = safe_vault_id
    return summary


def _build_direct_snapshot(vault: Path | str | None = None, config_path: Path | None = None) -> Dict[str, Any]:
    config = resolve_config_path(config_path)
    vault_path = resolve_vault(vault, config)
    provider = _status_summary(vault_path, config)
    hermes_memory = _hermes_memory_counts()
    if not provider["available"]:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "counts": {
                "preferences": 0,
                "inbox": 0,
                "retired": 0,
                "logs": 0,
                "brain_nodes": 0,
                "vault_notes": 0,
                "hermes_memory": hermes_memory["total"],
                "hermes_user": hermes_memory["user"],
                "hermes_notes": hermes_memory["memory"],
            },
            "hermes_memory": hermes_memory,
            "active_preview": "",
            "recent_logs": [],
            "timeline_events": [],
            "artifacts": [],
            "vault_notes": [],
            "graph": {"nodes": [], "edges": []},
            "graph_summary": {"hub_ids": [], "orphan_ids": [], "broken_count": 0},
            "broken_links": [],
            "revision": "",
            "limits": {
                "brain_files": {"shown": 0, "total": 0, "truncated": False},
                "vault_files": {"shown": 0, "total": 0, "truncated": False},
                "artifacts": {"shown": 0, "total": 0, "truncated": False},
                "vault_notes": {"shown": 0, "total": 0, "truncated": False},
            },
        }

    assert vault_path is not None
    brain = _brain_dir(vault_path)

    brain_inventory = _brain_inventory(brain)
    vault_inventory = _vault_inventory(vault_path, brain)
    brain_files = brain_inventory[:MAX_GRAPH_FILES]
    vault_files = vault_inventory[:MAX_VAULT_FILES]
    graph = _build_graph(vault_path, brain_files, vault_files)
    log_dir = brain / "log"
    log_files = [p for p in log_dir.glob("*.md")] if log_dir.exists() else []
    brain_nodes = [n for n in graph["nodes"] if n.get("layer") == "brain"]
    vault_nodes = [n for n in graph["nodes"] if n.get("layer") == "vault"]

    artifacts = [
        {
            "id": node["id"],
            "label": node["label"],
            "kind": node["kind"],
            "status": node.get("status") or "",
            "topic": node.get("topic") or "",
            "path": node["path"],
            "preview": node.get("preview", ""),
            "modified_at": node.get("modified_at", ""),
            "size_bytes": node.get("size_bytes", 0),
            "degree": node.get("degree", 0),
        }
        for node in brain_nodes[:48]
    ]

    vault_notes = [
        {
            "id": node["id"],
            "label": node["label"],
            "area": node.get("area") or "other",
            "path": node["path"],
            "preview": node.get("preview", ""),
            "modified_at": node.get("modified_at", ""),
            "size_bytes": node.get("size_bytes", 0),
            "degree": node.get("degree", 0),
        }
        for node in vault_nodes[:MAX_VAULT_NOTES]
    ]

    log_events = _parse_log_events(log_files)
    timeline_events = _timeline_events(graph, log_events)
    revision = _snapshot_revision(vault_path, brain_inventory + vault_inventory)
    graph_summary = graph.get("summary", {"hub_ids": [], "orphan_ids": [], "broken_count": 0})
    broken_links = graph.get("broken_links", [])
    limits = {
        "brain_files": {"shown": len(brain_files), "total": len(brain_inventory), "truncated": len(brain_files) < len(brain_inventory)},
        "vault_files": {"shown": len(vault_files), "total": len(vault_inventory), "truncated": len(vault_files) < len(vault_inventory)},
        "artifacts": {"shown": len(artifacts), "total": len(brain_nodes), "truncated": len(artifacts) < len(brain_nodes)},
        "vault_notes": {"shown": len(vault_notes), "total": len(vault_nodes), "truncated": len(vault_notes) < len(vault_nodes)},
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "counts": {
            "preferences": _count_dir(brain / "preferences"),
            "inbox": _count_dir(brain / "inbox"),
            "retired": _count_dir(brain / "retired"),
            "logs": len(log_files),
            "brain_nodes": len(brain_nodes),
            "vault_notes": len(vault_nodes),
            "hermes_memory": hermes_memory["total"],
            "hermes_user": hermes_memory["user"],
            "hermes_notes": hermes_memory["memory"],
        },
        "hermes_memory": hermes_memory,
        "active_preview": _safe_text(brain / "active.md", limit=MAX_PREVIEW_CHARS),
        "recent_logs": log_events,
        "timeline_events": timeline_events,
        "artifacts": artifacts,
        "vault_notes": vault_notes,
        "graph": graph,
        "graph_summary": graph_summary,
        "broken_links": broken_links,
        "revision": revision,
        "limits": limits,
    }


class UnavailableSnapshotReader:
    """Fail-closed default reader that never inspects local user data."""

    mode = "disabled"
    forbidden_values: Sequence[str] = ()

    def read(self) -> Dict[str, Any]:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider": {
                "name": "open-second-brain",
                "available": False,
                "semantic": "disabled",
            },
            "counts": {
                "preferences": 0,
                "inbox": 0,
                "retired": 0,
                "logs": 0,
                "brain_nodes": 0,
                "vault_notes": 0,
                "hermes_memory": 0,
                "hermes_user": 0,
                "hermes_notes": 0,
            },
            "hermes_memory": {"available": False, "user": 0, "memory": 0, "total": 0},
            "active_preview": "",
            "recent_logs": [],
            "timeline_events": [],
            "artifacts": [],
            "vault_notes": [],
            "graph": {"nodes": [], "edges": []},
            "graph_summary": {"hub_ids": [], "orphan_ids": [], "broken_count": 0},
            "broken_links": [],
            "revision": "",
            "limits": {
                "brain_files": {"shown": 0, "total": 0, "truncated": False},
                "vault_files": {"shown": 0, "total": 0, "truncated": False},
                "artifacts": {"shown": 0, "total": 0, "truncated": False},
                "vault_notes": {"shown": 0, "total": 0, "truncated": False},
            },
        }


class DirectMarkdownPrototypeReader:
    """Local-prototype-only reader for direct Markdown traversal.

    This class is not an OSB public adapter and must not be presented as an
    upstream integration contract. It is read-only and resolves a vault solely
    from an injected test override or the configured OSB ``vault`` value.
    """

    mode = "direct-markdown-prototype"

    def __init__(
        self,
        vault: Path | str | None = None,
        config_path: Path | None = None,
    ) -> None:
        self.vault = vault
        self.config_path = config_path
        resolved_config = resolve_config_path(config_path)
        resolved = resolve_vault(vault, resolved_config)
        config = _parse_simple_yaml(resolved_config)
        self.forbidden_values = tuple(
            value
            for value in (
                resolved.name if resolved is not None else "",
                config.get("obsidian_vault_id") or config.get("obsidian_vault") or "",
            )
            if value
        )

    def read(self) -> Dict[str, Any]:
        return _build_direct_snapshot(self.vault, self.config_path)


def build_snapshot(
    vault: Path | str | None = None,
    config_path: Path | None = None,
    *,
    reader: SnapshotReader | None = None,
) -> Dict[str, Any]:
    """Build the normalized UI snapshot from an injected or prototype reader."""

    selected_reader = reader
    if selected_reader is None:
        selected_reader = (
            DirectMarkdownPrototypeReader(vault=vault, config_path=config_path)
            if direct_markdown_opted_in()
            else UnavailableSnapshotReader()
        )
    return normalize_snapshot(
        selected_reader.read(),
        forbidden_values=tuple(getattr(selected_reader, "forbidden_values", ())),
        reader_mode=getattr(selected_reader, "mode", "normalized"),
    )


@router.get("/snapshot")
def snapshot() -> Dict[str, Any]:
    return build_snapshot()


@router.get("/health")
def health() -> Dict[str, Any]:
    snap = build_snapshot()
    return {"ok": bool(snap["provider"]["available"]), "provider": snap["provider"], "counts": snap["counts"]}

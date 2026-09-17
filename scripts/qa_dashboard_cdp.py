#!/usr/bin/env python3
"""Behavioral CDP QA for the read-only Hermes Second Brain dashboard.

Launches an isolated headless Chromium, exercises the live plugin route at each
requested viewport, writes PNG screenshots plus report.json, and exits nonzero
when a product invariant fails. It never mutates the Obsidian vault.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import math
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any

DEFAULT_VIEWPORTS = [(1440, 900), (1280, 577), (1024, 768), (390, 844)]
ROOT = Path(__file__).resolve().parents[1]
F_ADD_SEALS = getattr(fcntl, "F_ADD_SEALS", 1033)
F_GET_SEALS = getattr(fcntl, "F_GET_SEALS", 1034)
REQUIRED_MEMFD_SEALS = 0x01 | 0x02 | 0x04 | 0x08

try:
    from dashboard.snapshot_contract import sanitize_report_payload
except ModuleNotFoundError:
    import importlib.util

    _contract_path = ROOT / "dashboard" / "snapshot_contract.py"
    _contract_spec = importlib.util.spec_from_file_location("osb_snapshot_contract_for_qa", _contract_path)
    if _contract_spec is None or _contract_spec.loader is None:
        raise ImportError(f"Cannot load snapshot contract from {_contract_path}")
    _contract_module = importlib.util.module_from_spec(_contract_spec)
    _contract_spec.loader.exec_module(_contract_module)
    sanitize_report_payload = _contract_module.sanitize_report_payload


def parse_viewport(value: str) -> tuple[int, int]:
    try:
        width, height = value.lower().split("x", 1)
        parsed = (int(width), int(height))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("viewport must use WIDTHxHEIGHT") from exc
    if parsed[0] < 320 or parsed[1] < 480:
        raise argparse.ArgumentTypeError("viewport is too small for this QA harness")
    return parsed


def parse_loopback_url(value: str) -> str:
    """Accept only authenticated-harness destinations on the local loopback."""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        parsed.port  # Validate malformed and out-of-range ports.
    except ValueError as exc:
        raise argparse.ArgumentTypeError("url must be a valid http(s) loopback URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise argparse.ArgumentTypeError("url must be an http(s) loopback URL without userinfo")
    return value


def create_sealed_memfd(name: str, payload: bytes) -> int:
    """Create an immutable anonymous file positioned for an inheriting reader."""
    descriptor = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short memfd write")
            view = view[written:]
        fcntl.fcntl(descriptor, F_ADD_SEALS, REQUIRED_MEMFD_SEALS)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_sealed_memfd(descriptor: int) -> bytes:
    """Consume and close a fully sealed regular memfd."""
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 0:
            raise RuntimeError("asset descriptor was not an anonymous regular file")
        if fcntl.fcntl(descriptor, F_GET_SEALS) & REQUIRED_MEMFD_SEALS != REQUIRED_MEMFD_SEALS:
            raise RuntimeError("asset descriptor was not fully sealed")
        if status.st_size > 1_000_000:
            raise RuntimeError("asset descriptor exceeded the QA size limit")
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = status.st_size
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                raise RuntimeError("asset descriptor ended before its declared size")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def get_json(url: str, timeout: float = 2.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def stop_owned_group(process: Any, pgid: int, timeout: float = 6.0) -> None:
    """Stop every Chromium process even when the original leader has exited."""
    if pgid <= 0 or getattr(process, "pid", None) != pgid or pgid == os.getpgrp():
        raise RuntimeError("refusing to signal the CDP harness process group")

    def exists() -> bool:
        process.poll()  # Reap the leader while retaining PGID ownership of descendants.
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    if not exists():
        return
    deadline = time.monotonic() + timeout
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    term_deadline = min(deadline, time.monotonic() + 2.0)
    while exists() and time.monotonic() < term_deadline:
        time.sleep(0.05)
    if exists():
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        while exists() and time.monotonic() < deadline:
            time.sleep(0.05)
    if exists():
        raise RuntimeError("Chromium process group did not exit before cleanup deadline")
    if process.poll() is not None:
        try:
            process.wait(timeout=0)
        except (subprocess.TimeoutExpired, ChildProcessError):
            pass


def stop_inherited_process(process: Any, timeout: float = 6.0) -> None:
    """Stop Chromium without signalling the runner group that owns it."""
    if process.poll() is not None:
        process.wait(timeout=0)
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


@contextmanager
def demo_server(fixture: Path, javascript: bytes, stylesheet: bytes):
    """Serve already-validated immutable asset bytes and one synthetic fixture."""

    payload = fixture.read_bytes()
    html = b"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><link rel='stylesheet' href='/assets/style.css'><style>html,body,#root,#root>div,main,main>div,main>div>div,#pluginPageContainer{height:100%;min-height:0;margin:0;overflow:hidden;display:flex;flex-direction:column}</style></head><body><div id='root'><div><header role='banner'>Host</header><main><div><div><div id='pluginPageContainer'></div></div></div></main></div></div><script src='/assets/index.js'></script></body></html>"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            path = self.path.split("?", 1)[0]
            if path == "/api/plugins/hermes-osb-panel/snapshot":
                body, content_type = payload, "application/json"
            elif path == "/assets/index.js":
                body, content_type = javascript, "text/javascript"
            elif path == "/assets/style.css":
                body, content_type = stylesheet, "text/css"
            elif path in {"/", "/second-brain"}:
                body, content_type = html, "text/html"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/second-brain"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def sanitize_report(value: Any, key: str = "") -> Any:
    """Keep QA measurements while removing rendered owner content and topology."""

    return sanitize_report_payload(value, key)


def local_auth_cookie(url: str) -> tuple[str, str] | None:
    """Create a local WebUI session without logging the password or cookie."""
    login_value = os.environ.get("HERMES_WEBUI_PASSWORD", "").strip()
    env_file = os.environ.get("HERMES_WEBUI_ENV_FILE", "").strip()
    if not login_value and env_file:
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            if line.startswith("HERMES_WEBUI_PASSWORD="):
                login_value = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not login_value:
        return None
    parsed = urlsplit(url)
    endpoint = f"{parsed.scheme}://{parsed.netloc}/api/auth/login"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({"password": login_value}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        raw = response.headers.get("Set-Cookie", "").split(";", 1)[0]
    if "=" not in raw:
        raise RuntimeError("WebUI login succeeded without a session cookie")
    name, value = raw.split("=", 1)
    return name, value


def session_fetch_wrapper_source(session_token: str) -> str:
    """Return a fetch wrapper that attaches the session token only to this origin."""

    token_literal = json.dumps(session_token)
    return """(() => {
      const token = %s;
      const nativeFetch = window.fetch;
      window.fetch = function(input, init) {
        const raw = input instanceof Request ? input.url : String(input);
        let url;
        try { url = new URL(raw, location.href); }
        catch (_) { return nativeFetch.call(this, input, init); }
        if (url.origin !== location.origin) return nativeFetch.call(this, input, init);
        const next = Object.assign({}, init || {});
        const baseHeaders = next.headers || (input instanceof Request ? input.headers : undefined);
        const headers = new Headers(baseHeaders);
        headers.set('X-Hermes-Session-Token', token);
        next.headers = headers;
        return nativeFetch.call(this, input, next);
      };
    })();""" % token_literal


class CDP:
    def __init__(self, ws_url: str):
        import websocket

        self.websocket = websocket
        self.ws = websocket.create_connection(ws_url, timeout=12, origin="http://localhost")
        self.next_id = 0
        self.events: list[dict[str, Any]] = []

    def close(self) -> None:
        self.ws.close()

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.next_id += 1
        msg_id = self.next_id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        while True:
            payload = json.loads(self.ws.recv())
            if payload.get("id") == msg_id:
                if "error" in payload:
                    raise RuntimeError(f"CDP {method}: {payload['error']}")
                return payload.get("result", {})
            self.events.append(payload)

    def evaluate(self, expression: str, await_promise: bool = False) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
        )
        remote = result.get("result", {})
        if remote.get("subtype") == "error":
            raise RuntimeError(remote.get("description") or "Runtime.evaluate failed")
        return remote.get("value")

    def drain(self, seconds: float = 0.15) -> None:
        deadline = time.time() + seconds
        old_timeout = self.ws.gettimeout()
        self.ws.settimeout(0.03)
        try:
            while time.time() < deadline:
                try:
                    self.events.append(json.loads(self.ws.recv()))
                except self.websocket.WebSocketTimeoutException:
                    pass
        finally:
            self.ws.settimeout(old_timeout)


def wait_for(cdp: CDP, expression: str, timeout: float = 12.0) -> Any:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = cdp.evaluate(expression)
        if last:
            return last
        time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for: {expression}; last={last!r}")


def click_js(cdp: CDP, expression: str) -> bool:
    return bool(cdp.evaluate(f"(() => {{ const el = {expression}; if (!el) return false; el.click(); return true; }})()"))


def console_errors(events: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for event in events:
        method = event.get("method")
        params = event.get("params", {})
        if method == "Runtime.exceptionThrown":
            detail = params.get("exceptionDetails", {})
            errors.append(detail.get("text") or detail.get("exception", {}).get("description") or "exception")
        elif method == "Runtime.consoleAPICalled" and params.get("type") in {"error", "assert"}:
            args = params.get("args", [])
            errors.append(" ".join(str(arg.get("value") or arg.get("description") or "") for arg in args))
        elif method == "Log.entryAdded" and params.get("entry", {}).get("level") == "error":
            errors.append(str(params["entry"].get("text") or "log error"))
    return sorted(set(errors))


def foreign_auth_requests(events: list[dict[str, Any]], page_url: str) -> list[str]:
    """Return foreign request origins that received dashboard authentication headers."""

    page = urlsplit(page_url)
    page_origin = (page.scheme, page.netloc)
    leaked: set[str] = set()
    for event in events:
        if event.get("method") != "Network.requestWillBeSent":
            continue
        request = event.get("params", {}).get("request", {})
        target = urlsplit(str(request.get("url") or ""))
        if not target.scheme or (target.scheme, target.netloc) == page_origin:
            continue
        headers = {str(key).lower(): value for key, value in (request.get("headers") or {}).items()}
        if any(name in headers for name in ("authorization", "cookie", "x-hermes-session-token")):
            leaked.add(f"{target.scheme}://{target.netloc}")
    return sorted(leaked)


def graph_state_fits(state: dict[str, Any]) -> bool:
    bounds = state.get("bounds") or {}
    viewport = state.get("viewport") or [0, 0]
    safe_padding = float(state.get("safePadding") or 0)
    return bool(
        bounds
        and len(viewport) == 2
        and bounds.get("left", -1) >= safe_padding
        and bounds.get("top", -1) >= safe_padding
        and bounds.get("right", 10**9) <= viewport[0] - safe_padding
        and bounds.get("bottom", 10**9) <= viewport[1] - safe_padding
    )


def label_bounds_fit(state: dict[str, Any]) -> bool:
    """Verify content-free rendered-label geometry against its canvas safe rect."""

    viewport = state.get("viewport") or []
    bounds = state.get("labelBounds") or []
    try:
        width, height = float(viewport[0]), float(viewport[1])
        padding = float(state.get("labelSafePadding"))
    except (IndexError, TypeError, ValueError):
        return False
    if width <= 0 or height <= 0 or padding < 0 or not bounds:
        return False
    tolerance = 0.5
    for bound in bounds:
        try:
            left, right = float(bound["left"]), float(bound["right"])
            top, bottom = float(bound["top"]), float(bound["bottom"])
        except (KeyError, TypeError, ValueError):
            return False
        if not all(math.isfinite(value) for value in (left, right, top, bottom)):
            return False
        if left > right or top > bottom:
            return False
        if left < padding - tolerance or top < padding - tolerance:
            return False
        if right > width - padding + tolerance or bottom > height - padding + tolerance:
            return False
    return True


def base_metrics(cdp: CDP) -> dict[str, Any]:
    return cdp.evaluate(
        """(() => {
          const q = s => document.querySelector(s);
          const r = s => { const el=q(s); if(!el) return null; const x=el.getBoundingClientRect();
            return {top:Math.round(x.top),bottom:Math.round(x.bottom),left:Math.round(x.left),right:Math.round(x.right),width:Math.round(x.width),height:Math.round(x.height)}; };
          const canvas=q('#graphCanvas'); let canvasPainted=0;
          if(canvas && canvas.width && canvas.height){
            const ctx=canvas.getContext('2d');
            const w=Math.min(canvas.width,600), h=Math.min(canvas.height,400);
            const data=ctx.getImageData(0,0,w,h).data;
            for(let i=3;i<data.length;i+=4) if(data[i]>0) canvasPainted++;
          }
          return {
            innerWidth:window.innerWidth,innerHeight:window.innerHeight,
            bodyScrollWidth:document.body.scrollWidth,
            bodyScrollHeight:document.body.scrollHeight,
            mainClientHeight:(q('main:has(.osb-app)')||{}).clientHeight||0,
            mainScrollHeight:(q('main:has(.osb-app)')||{}).scrollHeight||0,
            mainRect:r('main:has(.osb-app)'),appRect:r('.osb-app'),rootRect:r('#root,#osbStandaloneRoot'),bodyRect:r('body'),htmlRect:r('html'),visualViewport:{width:window.visualViewport&&window.visualViewport.width,height:window.visualViewport&&window.visualViewport.height},bodyCss:(()=>{const s=getComputedStyle(document.body);return{height:s.height,minHeight:s.minHeight,paddingTop:s.paddingTop,paddingBottom:s.paddingBottom,boxSizing:s.boxSizing}})(),
            layoutChain:(()=>{const out=[];for(let e=q('.osb-app'),i=0;e&&i<10;e=e.parentElement,i++){const x=e.getBoundingClientRect(),s=getComputedStyle(e);out.push({tag:e.tagName,id:e.id,cls:String(e.className).slice(0,100),top:Math.round(x.top),bottom:Math.round(x.bottom),height:Math.round(x.height),clientHeight:e.clientHeight,scrollHeight:e.scrollHeight,display:s.display,flex:s.flex,heightCss:s.height,minHeight:s.minHeight,paddingTop:s.paddingTop,paddingBottom:s.paddingBottom,overflowY:s.overflowY})}return out})(),
            appExists:!!q('.osb-app'),
            titleFont:q('.note-title')?getComputedStyle(q('.note-title')).fontSize:null,
            canvasCss:canvas?r('#graphCanvas'):null,
            canvasInternal:canvas?[canvas.width,canvas.height]:null,
            canvasPainted,
            sidebar:r('.sidebar'),note:r('.note-pane'),graph:r('.graph-pane'),bottom:r('.bottom-panel'),status:r('.status-bar'),
            allCount:Number((q('[data-all-count]')||{}).dataset?.allCount||0),
            visibleText:(q('[data-visible-count]')||q('.sidebar-count')||{}).textContent||'',
            selectedId:(q('[data-selected-id]')||{}).dataset?.selectedId||'',
            activeArea:(q('[data-active-area]')||{}).dataset?.activeArea||'',
            primaryVisible:[...document.querySelectorAll('[data-mobile-surface]')].filter(el=>getComputedStyle(el).display!=='none').map(el=>el.dataset.mobileSurface),
          };
        })()"""
    )


def behavior_probes(cdp: CDP, width: int) -> dict[str, Any]:
    probes: dict[str, Any] = {}
    probes["host_header_scope"] = cdp.evaluate("""(() => {
      const app=document.querySelector('.osb-app');
      const host=[...document.querySelectorAll('header[role="banner"]')].find(h=>!app||!app.contains(h));
      const inner=app&&app.querySelector('header');
      const result={hostDisplay:host?getComputedStyle(host).display:'missing',innerDisplay:inner?getComputedStyle(inner).display:'missing'};
      result.passed=(result.hostDisplay==='none'||result.hostDisplay==='missing')&&result.innerDisplay!=='none';
      return result;
    })()""")
    original_selected = cdp.evaluate("(document.querySelector('[data-selected-id]')||{}).dataset?.selectedId||''")
    # Live search must update on the DOM input event without blur.
    probes["live_search"] = cdp.evaluate("""(() => { const i=document.querySelector('.osb-search-input'); if(!i)return {ok:false};
      i.focus();i.select();return {ok:true,focused:document.activeElement===i}; })()""")
    search_term = cdp.evaluate(
        """(() => { const item=document.querySelector('[data-graph-list] [data-node-id],.file-item[data-node-id]');
          return item ? String(item.dataset.nodeId||item.textContent||'').trim().slice(0,80) : ''; })()"""
    )
    if not search_term:
        raise RuntimeError("Live-search probe could not derive a safe term from the rendered fixture")
    cdp.call("Input.insertText", {"text": search_term})
    try:
        wait_for(cdp, "!!document.querySelector('[data-search-results] [data-node-id]')", timeout=5)
    except TimeoutError:
        pass
    probes["live_search"].update(
        cdp.evaluate(
            """(() => { const c=document.querySelector('.sidebar-count[data-visible-count]')||document.querySelector('.sidebar-count');
              const n=document.querySelector('[data-search-results] [data-node-id]');
              return {countText:c?c.textContent:'',hasResult:!!n}; })()"""
        )
    )
    probes["live_search"]["passed"] = bool(
        probes["live_search"].get("hasResult") and probes["live_search"].get("countText")
    )

    # Escape clears, Ctrl+Space and Ctrl+K focus/open the palette.
    cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape"})
    cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape"})
    cdp.evaluate("document.activeElement && document.activeElement.blur()")
    cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Control", "code": "ControlLeft", "modifiers": 2})
    cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": " ", "code": "Space", "modifiers": 2})
    cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": " ", "code": "Space", "modifiers": 2})
    cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Control", "code": "ControlLeft"})
    probes["shortcut"] = cdp.evaluate(
        """(() => { const i=document.querySelector('.osb-search-input'); return {focused:document.activeElement===i,
          palette:!!document.querySelector('[data-command-palette].open,[data-command-palette][data-open="true"]')}; })()"""
    )
    probes["shortcut"]["passed"] = bool(probes["shortcut"].get("focused"))

    # All must clear every dimension; use data hooks when available.
    click_js(cdp, "[...document.querySelectorAll('[data-area-filter],.folder-name')].find(e => /Projects/i.test(e.textContent))")
    click_js(cdp, "[...document.querySelectorAll('.chip,[data-layer-filter]')].find(e => /Brain/i.test(e.textContent))")
    click_js(cdp, "[...document.querySelectorAll('.chip,[data-preset]')].find(e => /^All$/i.test(e.textContent.trim()))")
    time.sleep(0.2)
    probes["filter_reset"] = cdp.evaluate(
        """(() => { const a=document.querySelector('[data-active-area]'); const q=document.querySelector('.osb-search-input');
          const c=document.querySelector('.sidebar-count[data-visible-count]')||document.querySelector('.sidebar-count');
          return {activeArea:a?a.dataset.activeArea:'',query:q?q.value:'',countText:c?c.textContent:'',
            allCount:Number((document.querySelector('[data-all-count]')||{}).dataset?.allCount||0)}; })()"""
    )
    text = probes["filter_reset"].get("countText", "")
    all_count = probes["filter_reset"].get("allCount", 0)
    probes["filter_reset"]["passed"] = not probes["filter_reset"].get("activeArea") and not probes["filter_reset"].get("query") and (not all_count or str(all_count) in text)

    # Cross-area navigation: choose any clickable Vault/Inbox card while Projects is active.
    click_js(cdp, "[...document.querySelectorAll('[data-area-filter],.folder-name')].find(e => /Projects/i.test(e.textContent))")
    click_js(cdp, "[...document.querySelectorAll('.bp-tab')].find(e => /Vault Notes/i.test(e.textContent))")
    time.sleep(0.1)
    clicked = cdp.evaluate(
        """(() => { const cards=[...document.querySelectorAll('.bp-vault-card,[data-node-card]')];
          const card=cards.find(c=>!/project/i.test((c.dataset.area||'')+' '+c.textContent))||cards[0];
          if(!card)return {clicked:false}; const wanted=card.dataset.nodeId||''; const label=(card.querySelector('.bp-vault-label')||card).textContent.trim();card.click();return {clicked:true,wanted,label}; })()"""
    )
    time.sleep(0.2)
    cross = cdp.evaluate(
        """(() => ({selectedId:(document.querySelector('[data-selected-id]')||{}).dataset?.selectedId||'',
          title:(document.querySelector('.note-title')||{}).textContent||'',activeArea:(document.querySelector('[data-active-area]')||{}).dataset?.activeArea||''}))()"""
    )
    cross.update(clicked)
    cross["passed"] = bool(cross.get("clicked")) and (bool(cross.get("selectedId")) or cross.get("label") in cross.get("title", "")) and cross.get("activeArea") != "projects"
    probes["cross_area_navigation"] = cross

    probes["explorer_disclosure"] = cdp.evaluate(
        """(() => { const progress=document.querySelector('[data-explorer-progress],.explorer-progress,.show-more');
          return {present:!!progress,text:progress?progress.textContent:''}; })()"""
    )
    probes["explorer_disclosure"]["passed"] = bool(probes["explorer_disclosure"].get("present"))

    if original_selected:
        click_js(cdp, "[...document.querySelectorAll('.chip,[data-preset]')].find(e => /^All$/i.test(e.textContent.trim()))")
        time.sleep(0.15)
        click_js(cdp, f"[...document.querySelectorAll('[data-graph-list] [data-node-id]')].find(x=>x.dataset.nodeId==={json.dumps(original_selected)})")
        time.sleep(0.2)

    probes["focus_graph"] = cdp.evaluate(
        """(() => ({focusControl:!!document.querySelector('[data-focus-depth],[data-graph-control="fit-selection"]'),
          ghostSupport:!!document.querySelector('[data-ghost-count]'),
          presets:[...document.querySelectorAll('[data-preset]')].map(x=>x.dataset.preset),
          options:[...document.querySelectorAll('[data-focus-option]')].map(x=>({depth:Number(x.dataset.focusOption),count:Number(x.dataset.focusCount),disabled:x.disabled}))}))()"""
    )
    required_presets = {"all", "recent", "hubs", "orphans", "broken"}
    focus = probes["focus_graph"]
    focus_options = {item.get("depth"): item for item in focus.get("options", [])}
    focus["passed"] = (
        bool(focus.get("focusControl"))
        and bool(focus.get("ghostSupport"))
        and required_presets.issubset(set(focus.get("presets", [])))
        and {0, 1, 2}.issubset(set(focus_options))
        and focus_options.get(0, {}).get("count", 0) > 0
    )
    one_hop = focus_options.get(1, {})
    if not one_hop.get("disabled"):
        click_js(cdp, "document.querySelector('[data-focus-option=\"1\"]')")
        time.sleep(0.2)
        one_actual = cdp.evaluate("Number((document.querySelector('.graph-info .val')||{}).textContent||0)")
        focus["oneHopActual"] = one_actual
        focus["passed"] = bool(focus["passed"] and one_actual == one_hop.get("count"))
        click_js(cdp, "document.querySelector('[data-focus-option=\"0\"]')")
        time.sleep(0.15)

    if width <= 390:
        click_js(cdp, "document.querySelector('[data-mobile-mode=\"graph\"]')")
        wait_for(cdp, "(() => { const g=document.querySelector('[data-mobile-surface=\"graph\"]'); return !!g&&getComputedStyle(g).display!=='none'; })()", timeout=5)
    switched_3d = click_js(cdp, "document.querySelector('[data-graph-dimension=\"3d\"]')")
    wait_for(cdp, "(() => { const c=document.querySelector('#graphCanvas3D'),b=window.__OSB_GRAPH__; return !!(c&&c.width&&c.height&&b&&b.state&&b.state().mode==='3d'&&b.state().painted>0); })()", timeout=8)
    state_start = cdp.evaluate("window.__OSB_GRAPH__.state()")
    time.sleep(0.75)
    state_before = cdp.evaluate("window.__OSB_GRAPH__.state()")
    auto_orbit_changed = abs(state_before.get("yaw", 0) - state_start.get("yaw", 0)) > 0.01
    rect_3d = cdp.evaluate("(() => { const r=document.querySelector('#graphCanvas3D').getBoundingClientRect(); return {left:r.left,top:r.top,width:r.width,height:r.height}; })()")
    x0, y0 = rect_3d["left"] + rect_3d["width"] * 0.48, rect_3d["top"] + rect_3d["height"] * 0.46
    cdp.call("Input.dispatchMouseEvent", {"type":"mousePressed","x":x0,"y":y0,"button":"left","clickCount":1})
    cdp.call("Input.dispatchMouseEvent", {"type":"mouseMoved","x":x0+55,"y":y0+28,"button":"left","buttons":1})
    cdp.call("Input.dispatchMouseEvent", {"type":"mouseReleased","x":x0+55,"y":y0+28,"button":"left","clickCount":1})
    time.sleep(0.12)
    state_after = cdp.evaluate("window.__OSB_GRAPH__.state()")
    if abs(state_after.get("yaw",0)-state_before.get("yaw",0)) <= 0.08 and abs(state_after.get("pitch",0)-state_before.get("pitch",0)) <= 0.05:
        cdp.evaluate("""(() => { const c=document.querySelector('#graphCanvas3D'),r=c.getBoundingClientRect(),x=r.left+r.width*.48,y=r.top+r.height*.46;c.setPointerCapture=()=>{};c.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,clientX:x,clientY:y,pointerId:41,pointerType:'mouse',buttons:1}));c.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:x+55,clientY:y+28,pointerId:41,pointerType:'mouse',buttons:1}));c.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,clientX:x+55,clientY:y+28,pointerId:41,pointerType:'mouse'}));})()""")
        state_after = cdp.evaluate("window.__OSB_GRAPH__.state()")
    click_js(cdp, "document.querySelector('.graph-pane-3d [data-graph-control=\"zoom-in\"]')")
    cdp.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))", await_promise=True)
    state_zoom = cdp.evaluate("window.__OSB_GRAPH__.state()")
    clicked_fit_all = click_js(cdp, "document.querySelector('.graph-pane-3d [data-graph-control=\"fit-all\"]')")
    time.sleep(0.1)
    state_fit_all = cdp.evaluate("window.__OSB_GRAPH__.state()")
    clicked_fit_selection = click_js(cdp, "document.querySelector('.graph-pane-3d [data-graph-control=\"fit-selection\"]')")
    time.sleep(0.1)
    state_fit_selection = cdp.evaluate("window.__OSB_GRAPH__.state()")
    touch_before = state_fit_selection
    cdp.call("Input.dispatchTouchEvent", {"type":"touchStart","touchPoints":[{"x":x0,"y":y0,"id":1}]})
    cdp.call("Input.dispatchTouchEvent", {"type":"touchMove","touchPoints":[{"x":x0+28,"y":y0+16,"id":1}]})
    cdp.call("Input.dispatchTouchEvent", {"type":"touchEnd","touchPoints":[]})
    time.sleep(0.1)
    state_touch = cdp.evaluate("window.__OSB_GRAPH__.state()")
    if abs(state_touch.get("yaw",0)-touch_before.get("yaw",0)) <= 0.02 and abs(state_touch.get("pitch",0)-touch_before.get("pitch",0)) <= 0.01:
        cdp.evaluate("""(() => { const c=document.querySelector('#graphCanvas3D'),r=c.getBoundingClientRect(),x=r.left+r.width*.48,y=r.top+r.height*.46;c.setPointerCapture=()=>{};c.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,clientX:x,clientY:y,pointerId:42,pointerType:'touch',buttons:1}));c.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:x+28,clientY:y+16,pointerId:42,pointerType:'touch',buttons:1}));c.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,clientX:x+28,clientY:y+16,pointerId:42,pointerType:'touch'}));})()""")
        state_touch = cdp.evaluate("window.__OSB_GRAPH__.state()")
    pixels_3d = cdp.evaluate("""(() => { const c=document.querySelector('#graphCanvas3D'),x=c.getContext('2d'),w=Math.min(c.width,500),h=Math.min(c.height,350),d=x.getImageData(0,0,w,h).data;let p=0;for(let i=3;i<d.length;i+=4)if(d[i]>0)p++;return p; })()""")
    orbit_changed = abs(state_after.get("yaw",0)-state_before.get("yaw",0)) > 0.08 or abs(state_after.get("pitch",0)-state_before.get("pitch",0)) > 0.05
    touch_changed = abs(state_touch.get("yaw",0)-touch_before.get("yaw",0)) > 0.02 or abs(state_touch.get("pitch",0)-touch_before.get("pitch",0)) > 0.01

    # Repeated renderer switching must clean the prior listener/RAF ownership.
    for _ in range(2):
        click_js(cdp, "document.querySelector('[data-graph-dimension=\"2d\"]')")
        wait_for(cdp, "window.__OSB_GRAPH__&&window.__OSB_GRAPH__.state().mode==='2d'", timeout=5)
        click_js(cdp, "document.querySelector('[data-graph-dimension=\"3d\"]')")
        wait_for(cdp, "window.__OSB_GRAPH__&&window.__OSB_GRAPH__.state().mode==='3d'", timeout=5)
    if width <= 390:
        wait_for(cdp, "window.__OSB_GRAPH__&&window.__OSB_GRAPH__.state().labelBounds&&window.__OSB_GRAPH__.state().labelBounds.length>0", timeout=5)
    state_lifecycle = cdp.evaluate("window.__OSB_GRAPH__.state()")

    # Reduced motion is a separate remount: orbit stays off while controls work.
    click_js(cdp, "document.querySelector('[data-graph-dimension=\"2d\"]')")
    wait_for(cdp, "window.__OSB_GRAPH__&&window.__OSB_GRAPH__.state().mode==='2d'", timeout=5)
    cdp.call("Emulation.setEmulatedMedia", {"features":[{"name":"prefers-reduced-motion","value":"reduce"}]})
    click_js(cdp, "document.querySelector('[data-graph-dimension=\"3d\"]')")
    wait_for(cdp, "window.__OSB_GRAPH__&&window.__OSB_GRAPH__.state().mode==='3d'", timeout=5)
    reduced_before = cdp.evaluate("window.__OSB_GRAPH__.state()")
    orbit_button = cdp.evaluate("(() => { const b=document.querySelector('[data-graph-control=\"toggle-orbit\"]'); return {pressed:b&&b.getAttribute('aria-pressed'),disabled:!!(b&&b.disabled)}; })()")
    time.sleep(0.35)
    reduced_after = cdp.evaluate("window.__OSB_GRAPH__.state()")
    reduced_fit = click_js(cdp, "document.querySelector('.graph-pane-3d [data-graph-control=\"fit-all\"]')")
    reduced_zoom = click_js(cdp, "document.querySelector('.graph-pane-3d [data-graph-control=\"zoom-out\"]')")
    reduced_selection = click_js(cdp, "document.querySelector('.graph-pane-3d [data-graph-control=\"fit-selection\"]')")
    reduced_controls_state = cdp.evaluate("window.__OSB_GRAPH__.state()")
    reduced_stable = abs(reduced_after.get("yaw",0)-reduced_before.get("yaw",0)) < 0.002

    switched_2d = click_js(cdp, "document.querySelector('[data-graph-dimension=\"2d\"]')")
    wait_for(cdp, "(() => { const c=document.querySelector('#graphCanvas'),b=window.__OSB_GRAPH__; return !!(c&&c.width&&c.height&&b&&b.state&&b.state().mode==='2d'); })()", timeout=8)
    if width <= 390:
        wait_for(cdp, "window.__OSB_GRAPH__&&window.__OSB_GRAPH__.state().labelBounds&&window.__OSB_GRAPH__.state().labelBounds.length>0", timeout=5)
    state_2d = cdp.evaluate("window.__OSB_GRAPH__.state()")
    if width <= 390:
        label_states = {
            "3d_start": state_start,
            "3d_before": state_before,
            "3d_after": state_after,
            "3d_zoom": state_zoom,
            "3d_fit_all": state_fit_all,
            "3d_fit_selection": state_fit_selection,
            "3d_touch": state_touch,
            "3d_lifecycle": state_lifecycle,
            "3d_reduced": reduced_controls_state,
            "2d": state_2d,
        }
        samples = {
            name: {
                "mode": state.get("mode"),
                "labelCount": len(state.get("labelBounds") or []),
                "fits": label_bounds_fit(state),
            }
            for name, state in label_states.items()
        }
        probes["label_bounds_mobile_safe"] = {
            "samples": samples,
            "passed": bool(samples) and all(sample["fits"] and sample["labelCount"] > 0 for sample in samples.values()),
        }
    cdp.call("Emulation.setEmulatedMedia", {"features":[{"name":"prefers-reduced-motion","value":"no-preference"}]})
    lifecycle_ok = state_lifecycle.get("activeRafs", 99) <= 1 and state_lifecycle.get("activeListenerSets") == 1
    reduced_ok = bool(reduced_before.get("reducedMotion") and not reduced_before.get("autoRotate") and reduced_stable and orbit_button.get("pressed")=="false" and orbit_button.get("disabled") and reduced_fit and reduced_zoom and reduced_selection)
    fits_3d = all(graph_state_fits(state) for state in (state_before,state_after,state_zoom,state_fit_all,state_fit_selection,state_touch))
    probes["graph_3d"] = {
        "switched3d":switched_3d,"start":state_start,"before":state_before,"after":state_after,
        "zoom":state_zoom,"fitAll":state_fit_all,"fitSelection":state_fit_selection,"touch":state_touch,
        "lifecycle":state_lifecycle,"reducedBefore":reduced_before,"reducedAfter":reduced_after,
        "reducedControls":reduced_controls_state,"orbitButton":orbit_button,"pixels":pixels_3d,
        "fits":fits_3d,"switched2d":switched_2d,"state2d":state_2d,
        "assertions":{"autoOrbit":auto_orbit_changed,"mouseOrbit":orbit_changed,"touchOrbit":touch_changed,"fitAll":clicked_fit_all,"fitSelection":clicked_fit_selection,"zoom":state_zoom.get("distance",10**9)<state_after.get("distance",0),"lifecycle":lifecycle_ok,"reduced":reduced_ok},
        "passed":bool(switched_3d and switched_2d and pixels_3d>1000 and fits_3d and auto_orbit_changed and orbit_changed and touch_changed and clicked_fit_all and clicked_fit_selection and state_zoom.get("distance",10**9)<state_after.get("distance",0) and lifecycle_ok and reduced_ok and state_2d.get("mode")=="2d")
    }

    if width > 768:
        # Reproduce every layout action reported by the user and prove geometry changes.
        layout = cdp.evaluate("(() => ({sidebar:document.querySelector('.sidebar').getBoundingClientRect().width,note:document.querySelector('.note-pane').getBoundingClientRect().width,bottom:document.querySelector('.bottom-panel').getBoundingClientRect().height}))()")
        click_js(cdp, "document.querySelector('[data-collapse-pane=\"explorer\"]')")
        time.sleep(0.25)
        layout["sidebarCollapsed"] = cdp.evaluate("document.querySelector('.sidebar').getBoundingClientRect().width")
        click_js(cdp, "document.querySelector('[aria-label=\"Show explorer\"]')")
        time.sleep(0.25)
        layout["sidebarRestored"] = cdp.evaluate("document.querySelector('.sidebar').getBoundingClientRect().width")
        click_js(cdp, "[...document.querySelectorAll('.toolbar-btn')].find(x=>/Expand graph/.test(x.textContent))")
        time.sleep(0.2)
        layout["expanded"] = cdp.evaluate("(() => ({active:document.querySelector('.osb-app').classList.contains('graph-maximized'),sidebar:getComputedStyle(document.querySelector('.sidebar')).display,note:getComputedStyle(document.querySelector('.note-pane')).display,bottom:getComputedStyle(document.querySelector('.bottom-panel')).display}))()")
        click_js(cdp, "[...document.querySelectorAll('.toolbar-btn')].find(x=>/Exit expanded mode/.test(x.textContent))")
        time.sleep(0.15)
        click_js(cdp, "document.querySelector('[data-collapse-pane=\"activity\"]')")
        time.sleep(0.15)
        layout["activityCollapsed"] = cdp.evaluate("getComputedStyle(document.querySelector('.bottom-panel')).display")
        click_js(cdp, "[...document.querySelectorAll('.toolbar-btn')].find(x=>/Show activity/.test(x.textContent))")
        time.sleep(0.15)
        layout["activityRestored"] = cdp.evaluate("getComputedStyle(document.querySelector('.bottom-panel')).display")
        click_js(cdp, "document.querySelector('[data-collapse-pane=\"inspector\"]')")
        time.sleep(0.15)
        layout["inspectorCollapsed"] = cdp.evaluate("document.querySelector('.note-pane').getBoundingClientRect().width")
        click_js(cdp, "[...document.querySelectorAll('.toolbar-btn')].find(x=>/Reset panels/.test(x.textContent))")
        time.sleep(0.2)
        layout["inspectorRestored"] = cdp.evaluate("document.querySelector('.note-pane').getBoundingClientRect().width")
        expanded = layout.get("expanded", {})
        layout["passed"] = bool(
            layout.get("sidebar", 0) > 100
            and layout.get("sidebarCollapsed", 999) <= 1
            and layout.get("sidebarRestored", 0) > 100
            and expanded.get("active")
            and all(expanded.get(k) == "none" for k in ("sidebar", "note", "bottom"))
            and layout.get("activityCollapsed") == "none"
            and layout.get("activityRestored") != "none"
            and layout.get("inspectorCollapsed", 999) <= 1
            and layout.get("inspectorRestored", 0) > 100
        )
        probes["layout_controls"] = layout

        # The host has aggressive h1/h2 rules; the plugin must win with compact sizes.
        click_js(cdp, "[...document.querySelectorAll('[data-preset]')].find(e => /^All$/i.test(e.textContent.trim()))")
        time.sleep(0.08)
        candidate_count = cdp.evaluate("Math.min(20,document.querySelectorAll('[data-graph-list] [data-node-id]').length)")
        for candidate_index in range(candidate_count):
            click_js(cdp, "[...document.querySelectorAll('[data-preset]')].find(e => /^All$/i.test(e.textContent.trim()))")
            time.sleep(0.03)
            click_js(cdp, f"document.querySelectorAll('[data-graph-list] [data-node-id]')[{candidate_index}]")
            time.sleep(0.05)
            if cdp.evaluate("!!document.querySelector('.markdown-rendered h1')&&!!document.querySelector('.markdown-rendered h2')"):
                break
        typography = cdp.evaluate("""(() => { const h1=document.querySelector('.markdown-rendered h1'),h2=document.querySelector('.markdown-rendered h2');
          return {title:(document.querySelector('.note-title')||{}).textContent||'',h1:h1?getComputedStyle(h1).fontSize:null,h2:h2?getComputedStyle(h2).fontSize:null,h1Weight:h1?getComputedStyle(h1).fontWeight:null,h2Weight:h2?getComputedStyle(h2).fontWeight:null}; })()""")
        typography["passed"] = typography.get("h1") == "15px" and typography.get("h1Weight") in {"590", "600"} and (typography.get("h2") is None or (typography.get("h2") == "13px" and typography.get("h2Weight") in {"590", "600"}))
        probes["rendered_typography"] = typography

    probes["product_features"] = cdp.evaluate(
        """(() => ({
          inspectorModes:[...document.querySelectorAll('[data-inspector-mode]')].map(x=>x.dataset.inspectorMode),
          refresh:!!document.querySelector('[data-action="refresh"]'),
          timelineRange:!!document.querySelector('[data-timeline-range]'),
          savedViews:!!document.querySelector('[data-saved-views]'),
          clusterExplain:!!document.querySelector('[data-action="explain-cluster"]'),
          graphList:!!document.querySelector('[data-graph-list]'),
          inspectorActions:[...document.querySelectorAll('.inspector-toolbar .icon-action')].map(x=>({text:x.textContent.trim(),title:x.title,svg:!!x.querySelector('svg')})),
          splitters:document.querySelectorAll('[role="separator"]').length,
          collapse:document.querySelectorAll('[data-collapse-pane]').length
        }))()"""
    )
    product = probes["product_features"]
    inspector_actions = product.get("inspectorActions", [])
    product["passed"] = {"rendered", "source"}.issubset(set(product.get("inspectorModes", []))) and all(product.get(k) for k in ("refresh", "timelineRange", "savedViews", "clusterExplain", "graphList")) and len(inspector_actions) == 2 and all(item.get("svg") and not item.get("text") and item.get("title") in {"Copy path", "Copy wikilink"} for item in inspector_actions) and product.get("splitters", 0) >= 2 and product.get("collapse", 0) >= 3

    # Saved views must create, reopen and remove in-session; cluster summary stays deterministic.
    click_js(cdp, "document.querySelector('[data-action=\"toggle-views\"]')")
    wait_for(cdp, "!!document.querySelector('.views-drawer.open')", timeout=3)
    cdp.evaluate("(() => { const i=document.querySelector('.save-view input'); i.focus(); i.select(); })()")
    cdp.call("Input.insertText", {"text": "QA view"})
    time.sleep(0.1)
    wait_for(cdp, "(() => { const b=document.querySelector('.save-view button'); return !!(b&&!b.disabled); })()", timeout=3)
    click_js(cdp, "document.querySelector('.save-view button')")
    created = wait_for(cdp, "document.querySelectorAll('.saved-view-row').length===1", timeout=3)
    click_js(cdp, "document.querySelector('.saved-view-row button:first-child')")
    click_js(cdp, "document.querySelector('[data-action=\"explain-cluster\"]')")
    summary = cdp.evaluate("(document.querySelector('.cluster-summary')||{}).textContent||''")
    click_js(cdp, "document.querySelector('.saved-view-row button:last-child')")
    removed = wait_for(cdp, "document.querySelectorAll('.saved-view-row').length===0", timeout=3)
    click_js(cdp, "document.querySelector('[data-action=\"toggle-views\"]')")
    wait_for(cdp, "!document.querySelector('.views-drawer.open')", timeout=3)
    probes["saved_views"] = {"created": bool(created), "removed": bool(removed), "deterministic": summary.startswith("Deterministic summary:"), "passed": bool(created and removed and summary.startswith("Deterministic summary:"))}

    if width > 390:
        cdp.evaluate("(() => { const s=document.querySelector('.sidebar'),b=document.querySelector('.bp-content'); if(s)s.scrollTop=s.scrollHeight; if(b)b.scrollTop=b.scrollHeight; })()")
        time.sleep(0.1)
        internal = cdp.evaluate("""(() => {
          const root=document.querySelector('#root'),s=document.querySelector('.sidebar'),stat=document.querySelector('.stat-grid'),b=document.querySelector('.bp-content');
          const cards=b?[...b.querySelectorAll('.timeline-card,.bp-art-card,.bp-vault-card,.bp-log-card')]:[]; const last=cards[cards.length-1];
          const rb=root?Math.min(root.getBoundingClientRect().bottom,innerHeight):innerHeight,sr=s&&s.getBoundingClientRect(),tr=stat&&stat.getBoundingClientRect(),br=b&&b.getBoundingClientRect(),lr=last&&last.getBoundingClientRect();
          return {rootBottom:rb,sidebarAtEnd:!!s&&Math.abs(s.scrollTop-(s.scrollHeight-s.clientHeight))<=1,activityAtEnd:!!b&&Math.abs(b.scrollTop-(b.scrollHeight-b.clientHeight))<=1,sidebarBottom:sr&&sr.bottom,statBottom:tr&&tr.bottom,activityBottom:br&&br.bottom,lastActivityBottom:lr&&lr.bottom};
        })()""")
        internal["passed"] = bool(internal.get("sidebarAtEnd") and internal.get("activityAtEnd") and internal.get("statBottom", 10**9) <= min(internal.get("sidebarBottom", 0), internal.get("rootBottom", 0)) + 1 and internal.get("lastActivityBottom", 10**9) <= min(internal.get("activityBottom", 0), internal.get("rootBottom", 0)) + 1)
        probes["internal_scroll_end_reachable"] = internal
        cdp.evaluate("(() => { const s=document.querySelector('.sidebar'),b=document.querySelector('.bp-content'); if(s)s.scrollTop=0; if(b)b.scrollTop=0; })()")

    if width <= 390:
        labels = cdp.evaluate("[...document.querySelectorAll('[data-mobile-mode]')].map(x=>x.dataset.mobileMode)")
        results: dict[str, Any] = {}
        for mode in labels:
            click_js(cdp, f"document.querySelector('[data-mobile-mode={json.dumps(mode)}]')")
            wait_for(
                cdp,
                f"(() => {{ const visible=[...document.querySelectorAll('[data-mobile-surface]')].filter(el=>getComputedStyle(el).display!=='none'); return visible.length===1 && visible[0].dataset.mobileSurface==={json.dumps(mode)}; }})()",
                timeout=5,
            )
            if mode == "graph":
                wait_for(cdp, "(() => { const p=document.querySelector('.graph-pane'),c=document.querySelector('#graphCanvas'); return !!(p&&c&&p.getBoundingClientRect().width>0&&p.getBoundingClientRect().height>0&&c.getBoundingClientRect().width>0&&c.getBoundingClientRect().height>0); })()", timeout=5)
                cdp.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))", await_promise=True)
            if mode == "vault":
                cdp.evaluate("(() => { const s=document.querySelector('.sidebar'); if(s)s.scrollTop=s.scrollHeight; })()")
                time.sleep(0.08)
            if mode == "activity":
                cdp.evaluate("(() => { const b=document.querySelector('.bp-content'); if(b)b.scrollTop=b.scrollHeight; })()")
                time.sleep(0.08)
            results[mode] = cdp.evaluate(
                """(() => { const visible=[...document.querySelectorAll('[data-mobile-surface]')].filter(el=>getComputedStyle(el).display!=='none');
                  const graph=document.querySelector('.graph-pane'),root=document.querySelector('#root'),s=document.querySelector('.sidebar'),stat=document.querySelector('.stat-grid'),b=document.querySelector('.bp-content');
                  const cards=b?[...b.querySelectorAll('.timeline-card,.bp-art-card,.bp-vault-card,.bp-log-card')]:[],last=cards[cards.length-1],rb=root?Math.min(root.getBoundingClientRect().bottom,innerHeight):innerHeight;
                  const sr=s&&s.getBoundingClientRect(),tr=stat&&stat.getBoundingClientRect(),br=b&&b.getBoundingClientRect(),lr=last&&last.getBoundingClientRect();
                  return {visible:visible.map(x=>x.dataset.mobileSurface),graphHeight:graph?graph.getBoundingClientRect().height:0,rootBottom:rb,sidebarAtEnd:!!s&&Math.abs(s.scrollTop-(s.scrollHeight-s.clientHeight))<=1,activityAtEnd:!!b&&Math.abs(b.scrollTop-(b.scrollHeight-b.clientHeight))<=1,sidebarBottom:sr&&sr.bottom,statBottom:tr&&tr.bottom,activityBottom:br&&br.bottom,lastActivityBottom:lr&&lr.bottom}; })()"""
            )
            if mode == "vault":
                item=results[mode]; item["endReachable"] = bool(item.get("sidebarAtEnd") and item.get("statBottom",10**9) <= min(item.get("sidebarBottom",0),item.get("rootBottom",0))+1)
            if mode == "activity":
                item=results[mode]; item["endReachable"] = bool(item.get("activityAtEnd") and item.get("lastActivityBottom",10**9) <= min(item.get("activityBottom",0),item.get("rootBottom",0))+1)
        probes["mobile_modes"] = {"labels": labels, "results": results}
        mobile = probes["mobile_modes"]
        modes = {"vault", "note", "graph", "activity"}
        exactly_one = all(len(item.get("visible", [])) == 1 for item in mobile.get("results", {}).values())
        graph_height = mobile.get("results", {}).get("graph", {}).get("graphHeight", 0)
        ends = bool(mobile.get("results",{}).get("vault",{}).get("endReachable") and mobile.get("results",{}).get("activity",{}).get("endReachable"))
        mobile["passed"] = modes.issubset(set(mobile.get("labels", []))) and exactly_one and graph_height >= 250 and ends
        probes["internal_scroll_end_reachable"] = {"passed":ends,"vault":mobile.get("results",{}).get("vault"),"activity":mobile.get("results",{}).get("activity")}
    return probes


def invariant_checks(metrics: dict[str, Any], probes: dict[str, Any], width: int) -> list[dict[str, Any]]:
    host_rect = metrics.get("mainRect") or metrics.get("rootRect")
    checks: list[tuple[str, bool, Any]] = [
        ("app_exists", bool(metrics.get("appExists")), metrics.get("appExists")),
        ("canvas_nonzero", bool(metrics.get("canvasInternal") and min(metrics["canvasInternal"]) > 0), metrics.get("canvasInternal")),
        ("canvas_painted", metrics.get("canvasPainted", 0) > 0, metrics.get("canvasPainted")),
        ("note_title_22px", metrics.get("titleFont") == "22px", metrics.get("titleFont")),
        ("no_horizontal_overflow", metrics.get("bodyScrollWidth", 10**9) <= metrics.get("innerWidth", 0), {"body": metrics.get("bodyScrollWidth"), "inner": metrics.get("innerWidth")}),
        ("host_main_no_vertical_overflow", metrics.get("mainScrollHeight", 10**9) <= metrics.get("mainClientHeight", 0) + 1, {"scroll": metrics.get("mainScrollHeight"), "client": metrics.get("mainClientHeight")}),
        ("app_fits_host_main", bool(metrics.get("appRect") and host_rect and metrics["appRect"]["top"] >= host_rect["top"] - 1 and metrics["appRect"]["bottom"] <= host_rect["bottom"] + 1), {"app": metrics.get("appRect"), "host": host_rect}),
        ("app_fits_visible_root", bool(metrics.get("appRect") and metrics.get("rootRect") and metrics["appRect"]["bottom"] <= min(metrics["rootRect"]["bottom"], metrics.get("innerHeight", 0)) + 1), {"app": metrics.get("appRect"), "root": metrics.get("rootRect"), "inner": metrics.get("innerHeight")}),
    ]
    if width > 768:
        note, graph, bottom = metrics.get("note"), metrics.get("graph"), metrics.get("bottom")
        checks.extend([
            ("note_graph_no_overlap", bool(note and graph and note["right"] <= graph["left"] + 1), {"note": note, "graph": graph}),
            ("graph_activity_no_overlap", bool(graph and bottom and graph["bottom"] <= bottom["top"] + 1), {"graph": graph, "bottom": bottom}),
        ])
    else:
        checks.extend([
            ("true_mobile_width", metrics.get("innerWidth") == 390, metrics.get("innerWidth")),
            ("mobile_modes", bool(probes.get("mobile_modes", {}).get("passed")), probes.get("mobile_modes")),
            ("label_bounds_mobile_safe", bool(probes.get("label_bounds_mobile_safe", {}).get("passed")), probes.get("label_bounds_mobile_safe")),
        ])
    probe_keys = ["host_header_scope", "url_object_auth_scope", "live_search", "shortcut", "filter_reset", "cross_area_navigation", "explorer_disclosure", "focus_graph", "graph_3d", "product_features", "saved_views", "internal_scroll_end_reachable"]
    if width > 768:
        probe_keys.extend(["layout_controls", "rendered_typography"])
    for key in probe_keys:
        checks.append((key, bool(probes.get(key, {}).get("passed")), probes.get(key)))
    return [{"name": name, "passed": passed, "detail": detail} for name, passed, detail in checks]


def run_viewport(cdp: CDP, url: str, out: Path, width: int, height: int) -> dict[str, Any]:
    network_event_start = len(cdp.events)
    cdp.call("Emulation.setEmulatedMedia", {"features":[{"name":"prefers-reduced-motion","value":"no-preference"}]})
    cdp.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False, "screenWidth": width, "screenHeight": height})
    cdp.call("Page.navigate", {"url": url})
    wait_for(cdp, "document.readyState === 'complete'")
    try:
        wait_for(cdp, "!!document.querySelector('.osb-app')", timeout=15)
    except TimeoutError as exc:
        cdp.drain()
        page_state = cdp.evaluate("({url:location.href,title:document.title,ready:document.readyState,login:!!document.querySelector('form[action*=login],#loginForm'),container:!!document.querySelector('#pluginPageContainer'),app:!!document.querySelector('.osb-app'),body:(document.body&&document.body.innerText||'').slice(0,120),assets:performance.getEntriesByType('resource').map(function(x){return x.name.split('?')[0]}).slice(-8)})")
        errors = console_errors(cdp.events)
        raise RuntimeError(f"Second Brain did not mount: state={page_state}; errors={errors}") from exc
    url_object_auth_scope = cdp.evaluate(
        """(async () => {
          const target = new URL(location.href);
          target.hostname = target.hostname === '127.0.0.1' ? 'localhost' : '127.0.0.1';
          target.pathname = '/health'; target.search = ''; target.hash = '';
          if (target.origin === location.origin) return {attempted:false,passed:false};
          try { await fetch(new URL(target.href)); } catch (_) {}
          return {attempted:true,passed:true};
        })()""",
        await_promise=True,
    )
    time.sleep(1.5)
    cdp.drain()
    event_start = len(cdp.events)
    metrics = base_metrics(cdp)
    probes = behavior_probes(cdp, width)
    probes["url_object_auth_scope"] = url_object_auth_scope
    time.sleep(0.5)
    cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape"})
    cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape"})
    cdp.evaluate("document.activeElement && document.activeElement.blur()")
    if cdp.evaluate("!!document.querySelector('[data-command-palette].open')"):
        click_js(cdp, "document.querySelector('[data-command-palette].open .palette-item')")
    wait_for(cdp, "!document.querySelector('[data-command-palette].open')", timeout=3)
    cdp.evaluate("""(() => {
      window.scrollTo(0,0);
      if(document.scrollingElement)document.scrollingElement.scrollTop=0;
      ['.sidebar','.note-pane','.bp-content'].forEach(s=>{const el=document.querySelector(s);if(el)el.scrollTop=0});
    })()""")
    cdp.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))", await_promise=True)
    # End on graph mode for a useful mobile visual; desktop stays on current view.
    if width <= 390:
        click_js(cdp, "document.querySelector('[data-mobile-mode=" + json.dumps("graph") + "]')")
        wait_for(cdp, "(() => { const p=document.querySelector('.graph-pane'),c=document.querySelector('#graphCanvas'); return !!(p&&c&&p.getBoundingClientRect().width>0&&p.getBoundingClientRect().height>0&&c.getBoundingClientRect().width>0&&c.getBoundingClientRect().height>0); })()", timeout=5)
        cdp.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))", await_promise=True)
        metrics = base_metrics(cdp)
    shot = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False, "fromSurface": True})
    screenshot = out / f"second-brain-{width}x{height}.png"
    screenshot.write_bytes(base64.b64decode(shot["data"]))
    cdp.drain()
    errors = console_errors(cdp.events[event_start:])
    foreign_auth = foreign_auth_requests(cdp.events[network_event_start:], url)
    checks = invariant_checks(metrics, probes, width)
    checks.append({"name": "no_console_errors", "passed": not errors, "detail": errors})
    checks.append({"name": "no_cross_origin_auth", "passed": not foreign_auth, "detail": foreign_auth})
    return {"viewport": {"width": width, "height": height}, "metrics": metrics, "probes": probes, "checks": checks, "console_errors": errors, "screenshot": str(screenshot)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, type=parse_loopback_url)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fixture", type=Path, help="serve this sanitized fixture with explicit installed assets")
    parser.add_argument("--asset-js-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--asset-css-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--chromium", type=Path, help="pre-resolved Chromium executable")
    parser.add_argument("--inherit-runner-process-group", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--viewport", dest="viewports", action="append", type=parse_viewport, help="repeatable WIDTHxHEIGHT; defaults to 1440x900, 1024x768 and 390x844")
    args = parser.parse_args(argv)
    asset_fds = (args.asset_js_fd, args.asset_css_fd)
    if args.fixture and any(value is None or value < 0 for value in asset_fds):
        parser.error("--fixture requires inherited sealed asset descriptors")
    if not args.fixture and any(value is not None for value in asset_fds):
        parser.error("asset descriptors require --fixture")
    viewports = args.viewports or DEFAULT_VIEWPORTS
    args.output.mkdir(parents=True, exist_ok=True)
    chromium = str(args.chromium.resolve()) if args.chromium else (shutil.which("chromium") or shutil.which("chromium-browser"))
    if not chromium:
        raise SystemExit("Chromium not found; refusing to install a heavy dependency")

    if args.fixture:
        javascript = read_sealed_memfd(args.asset_js_fd)
        stylesheet = read_sealed_memfd(args.asset_css_fd)
        demo_context = demo_server(args.fixture, javascript, stylesheet)
    else:
        demo_context = None
    run_url = demo_context.__enter__() if demo_context else args.url
    port = free_port()
    runner_pgid = os.getpgrp()
    if not isinstance(runner_pgid, int) or runner_pgid <= 0:
        raise RuntimeError("CDP runner process group identity was unavailable")
    with tempfile.TemporaryDirectory(prefix="hermes-osb-cdp-") as profile:
        process = subprocess.Popen(
            [chromium, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--hide-scrollbars", "--remote-allow-origins=*", f"--remote-debugging-port={port}", f"--user-data-dir={profile}", "about:blank"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=not args.inherit_runner_process_group,
        )
        chromium_pid = getattr(process, "pid", None)
        if not isinstance(chromium_pid, int) or chromium_pid <= 0:
            process.kill()
            process.wait(timeout=2)
            raise RuntimeError("Chromium process identity was unavailable")
        chromium_pgid = None if args.inherit_runner_process_group else chromium_pid
        if chromium_pgid is not None and chromium_pgid == runner_pgid:
            process.kill()
            process.wait(timeout=2)
            raise RuntimeError("Chromium process group identity was unsafe")
        cdp = None
        try:
            deadline = time.time() + 12
            targets = []
            while time.time() < deadline:
                try:
                    targets = get_json(f"http://127.0.0.1:{port}/json/list")
                    if targets:
                        break
                except Exception:
                    time.sleep(0.1)
            if not targets:
                raise RuntimeError("Chromium CDP did not become ready")
            target = next((x for x in targets if x.get("type") == "page"), targets[0])
            cdp = CDP(target["webSocketDebuggerUrl"])
            for domain in ("Page", "Runtime", "Log", "Network"):
                cdp.call(f"{domain}.enable")
            auth_cookie = None if args.fixture else local_auth_cookie(run_url)
            session_token = "" if args.fixture else os.environ.get("HERMES_DASHBOARD_SESSION_TOKEN", "").strip()
            if session_token:
                cdp.call(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": session_fetch_wrapper_source(session_token)},
                )
            if auth_cookie:
                parsed_url = urlsplit(run_url)
                cdp.call(
                    "Network.setCookie",
                    {
                        "name": auth_cookie[0],
                        "value": auth_cookie[1],
                        "url": f"{parsed_url.scheme}://{parsed_url.netloc}/",
                        "path": "/",
                        "httpOnly": True,
                        "sameSite": "Lax",
                        "secure": parsed_url.scheme == "https",
                    },
                )
                cookie_check = cdp.call(
                    "Network.getCookies",
                    {"urls": [f"{parsed_url.scheme}://{parsed_url.netloc}/"]},
                )
                if not any(item.get("name") == auth_cookie[0] for item in cookie_check.get("cookies", [])):
                    raise RuntimeError("CDP rejected the local WebUI session cookie")
            results = [run_viewport(cdp, run_url, args.output, width, height) for width, height in viewports]
        finally:
            if cdp:
                cdp.close()
            if chromium_pgid is None:
                stop_inherited_process(process)
            else:
                stop_owned_group(process, chromium_pgid)
            if demo_context:
                demo_context.__exit__(None, None, None)

    failed = [f"{r['viewport']['width']}x{r['viewport']['height']}:{c['name']}" for r in results for c in r["checks"] if not c["passed"]]
    report = {
        "route": urlsplit(run_url).path,
        "fixture": args.fixture.name if args.fixture else None,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "viewports": sanitize_report(results),
        "passed": not failed,
        "failures": failed,
    }
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "failures": failed, "report": str(report_path)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

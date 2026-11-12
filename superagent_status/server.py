"""Tiny dependency-free HTTP server for the superagent status dashboard."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from . import __version__
from .store import load_status, resolve_state_dir

STATIC_DIR = Path(__file__).resolve().parent / "static"
CSP = (
    "default-src 'self'; "
    "base-uri 'none'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "form-action 'none'; "
    "frame-ancestors 'none'; "
    "img-src 'self' data:; "
    "object-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'"
)


class StatusServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], state_dir: Path, *, verbose: bool = False):
        self.state_dir = state_dir
        self.verbose = verbose
        super().__init__(address, StatusRequestHandler)


class StatusRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"SuperagentStatus/{__version__}"
    server: StatusServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # The page polls every few seconds; access logs would only create noise.
        if self.server.verbose:
            super().log_message(format, *args)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                self._serve_file(STATIC_DIR / "index.html")
            elif path == "/api/status":
                self._serve_status()
            elif path == "/healthz":
                self._serve_health()
            elif path == "/favicon.svg":
                self._serve_file(STATIC_DIR / "favicon.svg")
            elif path.startswith("/static/"):
                self._serve_static(path[len("/static/") :])
            else:
                self._send_error_page(HTTPStatus.NOT_FOUND, "页面不存在")
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            traceback.print_exc()
            self._send_error_page(HTTPStatus.INTERNAL_SERVER_ERROR, "服务器内部错误")

    def do_HEAD(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            candidate = STATIC_DIR / "index.html"
        elif path == "/favicon.svg":
            candidate = STATIC_DIR / "favicon.svg"
        elif path.startswith("/static/"):
            candidate = self._resolve_static(path[len("/static/") :])
        else:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if candidate is None or not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._send_headers(
            HTTPStatus.OK,
            _content_type(candidate),
            candidate.stat().st_size,
            cache="no-cache",
        )

    # -- responses ---------------------------------------------------------

    def _serve_status(self) -> None:
        payload = load_status(self.server.state_dir)
        self._send_json(payload, HTTPStatus.OK)

    def _serve_health(self) -> None:
        payload = load_status(self.server.state_dir)
        if payload.get("ok"):
            body = {"ok": True, "generatedAt": payload["generatedAt"]}
            self._send_json(body, HTTPStatus.OK)
        else:
            body = {
                "ok": False,
                "generatedAt": payload["generatedAt"],
                "error": payload.get("error", "状态不可用"),
            }
            self._send_json(body, HTTPStatus.SERVICE_UNAVAILABLE)

    def _serve_static(self, relative: str) -> None:
        candidate = self._resolve_static(relative)
        if candidate is None:
            self._send_error_page(HTTPStatus.NOT_FOUND, "资源不存在")
            return
        self._serve_file(candidate, cache="no-cache")

    def _resolve_static(self, relative: str) -> Path | None:
        relative = unquote(relative)
        if not relative or "\x00" in relative:
            return None
        candidate = (STATIC_DIR / relative).resolve()
        try:
            candidate.relative_to(STATIC_DIR)
        except ValueError:
            return None
        return candidate

    def _serve_file(self, path: Path, *, cache: str = "no-store") -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self._send_error_page(HTTPStatus.NOT_FOUND, "资源不存在")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", _content_type(path))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(data)

    def _send_error_page(self, status: HTTPStatus, message: str) -> None:
        data = (
            "<!doctype html><meta charset=\"utf-8\">"
            f"<title>{status.value} {status.phrase}</title>"
            "<link rel=\"stylesheet\" href=\"/static/error.css\">"
            "<body><main><p class=\"code\">"
            f"{status.value}</p><h1>{message}</h1>"
            "<p><a href=\"/\">返回状态台</a></p></main></body>"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(data)

    def _send_headers(
        self,
        status: HTTPStatus,
        content_type: str,
        length: int,
        *,
        cache: str,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()


def _content_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if path.suffix == ".js":
        return "text/javascript; charset=utf-8"
    if path.suffix in (".css",):
        return "text/css; charset=utf-8"
    if path.suffix == ".html":
        return "text/html; charset=utf-8"
    if path.suffix == ".svg":
        return "image/svg+xml"
    return (guessed or "application/octet-stream") + (
        "; charset=utf-8" if (guessed or "").startswith("text/") else ""
    )


def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "::1", "localhost")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读的 superagent 状态监控页面。",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("SUPERAGENT_STATUS_HOST", "127.0.0.1"),
        help="监听地址（默认 127.0.0.1，只允许本机访问）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("SUPERAGENT_STATUS_PORT", "8765")),
        help="监听端口（默认 8765；传 0 自动选择）",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="superagent 状态目录，默认读 SUPERAGENT_STATE_DIR 或 ~/.dsh/state/superagent",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="启动后自动在默认浏览器打开页面",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="打印访问日志（默认关闭，避免轮询刷屏）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    state_dir = resolve_state_dir(args.state_dir)
    server = StatusServer((args.host, args.port), state_dir, verbose=args.verbose)
    host, port = server.server_address[:2]
    url_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    if ":" in url_host and not url_host.startswith("["):
        url_host = f"[{url_host}]"
    url = f"http://{url_host}:{port}/"

    if not _is_loopback(host):
        print(
            "警告：页面包含本机 superagent 的工单与报告摘要，建议只监听 127.0.0.1。",
            file=sys.stderr,
        )
    payload = load_status(state_dir)
    if payload.get("ok"):
        print(f"状态库：{state_dir / 'orchestrator.sqlite'}")
    else:
        print(f"状态库暂不可用：{payload.get('error')}", file=sys.stderr)
    print(f"Superagent 状态台已启动：{url}")
    print("按 Ctrl+C 停止。")

    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0

"""HTTP servers for Fabric OS data and isolated emulator control."""

from __future__ import annotations

import base64
import hmac
import json
import secrets
import ssl
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .model import ENDPOINT_KEYS, EmulatorModel


def _error(tag: str, message: str) -> dict[str, Any]:
    return {"Errors": {"Error": [{"error-tag": tag, "error-message": message}]}}


class ApiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], model: EmulatorModel, username: str, password: str):
        super().__init__(address, ApiHandler)
        self.model = model
        self.username = username
        self.password = password
        self.sessions: set[str] = set()


class ApiHandler(BaseHTTPRequestHandler):
    server: ApiServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format_string: str, *args: Any) -> None:
        # Headers and credentials are deliberately excluded.
        print(f"api {self.address_string()} {self.command} {self.path} {format_string % args}", flush=True)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/rest/login":
            self._login()
            return
        if self.path == "/rest/logout":
            if not self._authorized(session_only=True):
                self._send_json(HTTPStatus.UNAUTHORIZED, _error("access-denied", "A valid session is required"))
                return
            self.server.sessions.discard(self.headers.get("Authorization", ""))
            self._send_empty(HTTPStatus.NO_CONTENT)
            return
        self._send_json(HTTPStatus.NOT_FOUND, _error("invalid-value", "Unsupported emulator route"))

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path not in ENDPOINT_KEYS:
            self._send_json(HTTPStatus.NOT_FOUND, _error("invalid-value", "Unsupported emulator route"))
            return
        if not self._authorized():
            self._send_json(HTTPStatus.UNAUTHORIZED, _error("access-denied", "Authentication is required"))
            return
        behavior = self.server.model.behavior()
        if behavior["timeout_seconds"]:
            time.sleep(behavior["timeout_seconds"])
        if behavior["http_status"] != 200:
            self._send_json(behavior["http_status"], _error("operation-failed", "Injected emulator failure"))
            return
        if behavior["malformed"]:
            self._send_raw(HTTPStatus.OK, b'{"Response":', "application/yang-data+json")
            return
        self._send_json(HTTPStatus.OK, self.server.model.payload(self.path))

    def _login(self) -> None:
        if self.server.model.behavior()["auth_failure"]:
            self._send_json(HTTPStatus.UNAUTHORIZED, _error("access-denied", "Injected authentication failure"))
            return
        credentials = self._basic_credentials()
        if credentials is None:
            self._send_json(HTTPStatus.UNAUTHORIZED, _error("access-denied", "HTTP Basic credentials are required"))
            return
        username, password = credentials
        if not (
            hmac.compare_digest(username, self.server.username)
            and hmac.compare_digest(password, self.server.password)
        ):
            self._send_json(HTTPStatus.UNAUTHORIZED, _error("access-denied", "Invalid credentials"))
            return
        token = f"Custom_Basic {secrets.token_urlsafe(30)}"
        self.server.sessions.add(token)
        response = {
            "Response": {
                "user-name": username,
                "role": "user",
                "session-timeout": 7200,
                "switch-parameters": [
                    {
                        "switch-name": self.server.model.profile["identity"]["switch_name"],
                        "switch-wwn": self.server.model.profile["identity"]["switch_wwn"],
                    }
                ],
            }
        }
        self._send_json(HTTPStatus.OK, response, {"Authorization": token})

    def _authorized(self, session_only: bool = False) -> bool:
        authorization = self.headers.get("Authorization", "")
        if authorization in self.server.sessions:
            return True
        if session_only:
            return False
        credentials = self._basic_credentials()
        return credentials == (self.server.username, self.server.password)

    def _basic_credentials(self) -> tuple[str, str] | None:
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Basic "):
            return None
        try:
            raw = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
            username, password = raw.split(":", 1)
            return username, password
        except (ValueError, UnicodeDecodeError):
            return None

    def _send_json(self, status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self._send_raw(status, encoded, "application/yang-data+json", headers)

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_raw(
        self,
        status: int,
        body: bytes,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Expected when a timeout scenario exceeds the client's timeout.
            pass


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], model: EmulatorModel, token: str | None):
        super().__init__(address, ControlHandler)
        self.model = model
        self.token = token


class ControlHandler(BaseHTTPRequestHandler):
    server: ControlServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"control {self.address_string()} {self.command} {self.path} {format_string % args}", flush=True)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/state":
            self._send(HTTPStatus.NOT_FOUND, {"error": "not-found"})
            return
        if not self._authorized():
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "access-denied"})
            return
        self._send(HTTPStatus.OK, self.server.model.status())

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/scenario":
            self._send(HTTPStatus.NOT_FOUND, {"error": "not-found"})
            return
        if not self._authorized():
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "access-denied"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > 4096:
                raise ValueError("Request size is invalid")
            request = json.loads(self.rfile.read(length))
            response = self.server.model.set_scenario(str(request["name"]))
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send(HTTPStatus.OK, response)

    def _authorized(self) -> bool:
        if not self.server.token:
            return self.client_address[0] in {"127.0.0.1", "::1"}
        supplied = self.headers.get("X-Emulator-Control-Token", "")
        return hmac.compare_digest(supplied, self.server.token)

    def _send(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)


def enable_tls(server: ApiServer, certfile: str, keyfile: str) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=certfile, keyfile=keyfile)
    server.socket = context.wrap_socket(server.socket, server_side=True)

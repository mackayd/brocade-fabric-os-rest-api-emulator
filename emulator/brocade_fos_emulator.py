#!/usr/bin/env python3
"""Run one Brocade Fabric OS REST emulator personality."""

from __future__ import annotations

import argparse
import ipaddress
import os
import signal
import sys
import threading
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from emulator.model import EmulatorModel  # noqa: E402
from emulator.server import ApiServer, ControlServer, enable_tls  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="Path to a profile.json file")
    parser.add_argument("--host", default="127.0.0.1", help="Data API bind address")
    parser.add_argument("--port", type=int, default=8620, help="Data API TCP port")
    parser.add_argument("--control-host", default="127.0.0.1", help="Control API bind address")
    parser.add_argument("--control-port", type=int, default=18620, help="Control API TCP port")
    parser.add_argument("--username", default=os.environ.get("BROCADE_EMULATOR_USERNAME", "monitor"))
    parser.add_argument("--password-env", default="BROCADE_EMULATOR_PASSWORD")
    parser.add_argument("--control-token-env", default="BROCADE_EMULATOR_CONTROL_TOKEN")
    parser.add_argument("--tls-cert", help="PEM certificate for the data API")
    parser.add_argument("--tls-key", help="PEM private key for the data API")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if bool(args.tls_cert) != bool(args.tls_key):
        raise SystemExit("--tls-cert and --tls-key must be supplied together")
    password = os.environ.get(args.password_env, "emulator-only")
    control_token = os.environ.get(args.control_token_env)
    control_ip = ipaddress.ip_address(args.control_host)
    if not control_ip.is_loopback and not control_token:
        raise SystemExit(f"{args.control_token_env} is required when control-host is not loopback")

    model = EmulatorModel.from_file(args.profile)
    api = ApiServer((args.host, args.port), model, args.username, password)
    control = ControlServer((args.control_host, args.control_port), model, control_token)
    if args.tls_cert and args.tls_key:
        enable_tls(api, args.tls_cert, args.tls_key)
    scheme = "https" if args.tls_cert else "http"

    stopped = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        if stopped.is_set():
            return
        stopped.set()
        threading.Thread(target=api.shutdown, daemon=True).start()
        threading.Thread(target=control.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)

    control_thread = threading.Thread(target=control.serve_forever, name="control-api", daemon=True)
    control_thread.start()
    print(
        f"profile={model.profile_id} data={scheme}://{args.host}:{args.port} "
        f"control=http://{args.control_host}:{args.control_port}",
        flush=True,
    )
    try:
        api.serve_forever()
    finally:
        api.server_close()
        control.shutdown()
        control.server_close()
        control_thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Deterministic state and payload generation for the Fabric OS REST emulator."""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import Any


class ProfileError(ValueError):
    """Raised when an emulator profile is incomplete or inconsistent."""


ENDPOINT_KEYS = {
    "/rest/running/brocade-fibrechannel-switch/fibrechannel-switch": "fibrechannel-switch",
    "/rest/running/brocade-fabric/fabric-switch": "fabric-switch",
    "/rest/running/brocade-interface/fibrechannel": "fibrechannel",
    "/rest/running/brocade-interface/fibrechannel-statistics": "fibrechannel-statistics",
    "/rest/running/brocade-name-server/fibrechannel-name-server": "fibrechannel-name-server",
    "/rest/running/brocade-fru/power-supply": "power-supply",
    "/rest/running/brocade-fru/fan": "fan",
    "/rest/running/brocade-fru/sensor": "sensor",
    "/rest/running/brocade-media/media-rdp": "media-rdp",
    "/rest/running/brocade-maps/switch-status-policy-report": "switch-status-policy-report",
    "/rest/running/brocade-maps/system-resources": "system-resources",
}

SCENARIOS = {
    "healthy",
    "recovery",
    "port-admin-down",
    "port-unexpected-down",
    "isl-down",
    "crc-errors",
    "link-errors",
    "loss-of-signal",
    "loss-of-sync",
    "invalid-words",
    "high-utilisation",
    "high-utilization",
    "congestion",
    "slow-drain",
    "switch-degraded",
    "switch-critical",
    "switch-unavailable",
    "psu-failed",
    "fan-failed",
    "high-temperature",
    "optic-warning",
    "optic-failed",
    "auth-failure",
    "timeout",
    "http-500",
    "partial-payload",
    "malformed-payload",
    "reboot",
}


def _parse_ranges(ranges: list[str]) -> set[int]:
    values: set[int] = set()
    for value in ranges:
        parts = value.split("-", 1)
        start = int(parts[0])
        end = int(parts[-1])
        if start < 0 or end < start:
            raise ProfileError(f"Invalid port range: {value}")
        values.update(range(start, end + 1))
    return values


class EmulatorModel:
    """A thread-safe model that expands a compact hardware profile into FOS payloads."""

    def __init__(self, profile: dict[str, Any]):
        self.profile = copy.deepcopy(profile)
        self._validate_profile()
        self._lock = threading.RLock()
        self._scenario = "healthy"
        self._generation = 0
        self._collection_cycles = 0
        self._started = time.time()
        self._counter_epoch = 0

    @classmethod
    def from_file(cls, path: str | Path) -> "EmulatorModel":
        with Path(path).open("r", encoding="utf-8") as stream:
            return cls(json.load(stream))

    def _validate_profile(self) -> None:
        required = {
            "profile_id",
            "evidence_classification",
            "identity",
            "fabric",
            "port_layout",
            "components",
            "endpoints",
        }
        missing = sorted(required - self.profile.keys())
        if missing:
            raise ProfileError(f"Missing profile keys: {', '.join(missing)}")
        identity = self.profile["identity"]
        for key in ("switch_wwn", "chassis_wwn", "serial_number", "switch_type", "firmware_version"):
            if not identity.get(key):
                raise ProfileError(f"Missing identity.{key}")
        layout = self.profile["port_layout"]
        port_count = int(layout.get("logical_port_count", 0))
        if port_count < 1 or port_count > 256:
            raise ProfileError("logical_port_count must be between 1 and 256")
        licensed = _parse_ranges(layout.get("licensed_ranges", []))
        if licensed and max(licensed) >= port_count:
            raise ProfileError("A licensed port is outside logical_port_count")
        for port_key in ("host_port", "storage_port", "isl_port", "fault_port", "optic_port"):
            port = int(layout[port_key])
            if port < 0 or port >= port_count:
                raise ProfileError(f"{port_key} is outside logical_port_count")

    @property
    def profile_id(self) -> str:
        return str(self.profile["profile_id"])

    @property
    def scenario(self) -> str:
        with self._lock:
            return self._scenario

    def set_scenario(self, name: str) -> dict[str, Any]:
        normalized = name.strip().lower()
        if normalized not in SCENARIOS:
            raise ValueError(f"Unknown scenario: {name}")
        if normalized == "recovery":
            normalized = "healthy"
        if normalized == "high-utilization":
            normalized = "high-utilisation"
        with self._lock:
            previous = self._scenario
            self._scenario = normalized
            self._generation += 1
            if normalized == "reboot":
                self._counter_epoch += 1
            return {
                "profile_id": self.profile_id,
                "previous": previous,
                "scenario": normalized,
                "generation": self._generation,
            }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "profile_id": self.profile_id,
                "scenario": self._scenario,
                "generation": self._generation,
                "collection_cycles": self._collection_cycles,
                "uptime_seconds": int(time.time() - self._started),
                "evidence_classification": self.profile["evidence_classification"],
            }

    def behavior(self) -> dict[str, Any]:
        """Return transport behavior for the active scenario."""
        scenario = self.scenario
        return {
            "auth_failure": scenario == "auth-failure",
            "timeout_seconds": 8 if scenario == "timeout" else 0,
            "http_status": 503 if scenario == "switch-unavailable" else (500 if scenario == "http-500" else 200),
            "malformed": scenario == "malformed-payload",
        }

    def payload(self, path: str) -> dict[str, Any]:
        response_key = ENDPOINT_KEYS.get(path)
        if response_key is None:
            raise KeyError(path)
        with self._lock:
            if response_key == "fibrechannel-switch":
                data = [self._switch()]
            elif response_key == "fabric-switch":
                data = self._fabric_switches()
            elif response_key == "fibrechannel":
                data = self._ports()
            elif response_key == "fibrechannel-statistics":
                self._collection_cycles += 1
                data = self._statistics()
            elif response_key == "fibrechannel-name-server":
                data = self._name_server()
            elif response_key == "power-supply":
                data = self._power_supplies()
            elif response_key == "fan":
                data = self._fans()
            elif response_key == "sensor":
                data = self._sensors()
            elif response_key == "media-rdp":
                data = self._media()
            elif response_key == "switch-status-policy-report":
                data = self._health()
            elif response_key == "system-resources":
                data = self._system_resources()
            else:  # pragma: no cover - ENDPOINT_KEYS and branches stay paired
                raise KeyError(path)
            if self._scenario == "partial-payload" and data:
                if isinstance(data, list):
                    data = copy.deepcopy(data[:-1] if len(data) > 1 else data)
                    if len(data) == 1:
                        data[0].pop(next(reversed(data[0])), None)
                else:
                    data = copy.deepcopy(data)
                    data.pop(next(reversed(data)), None)
            return {"Response": {response_key: data}}

    def _switch(self) -> dict[str, Any]:
        i = self.profile["identity"]
        state = "enabled"
        enabled = True
        if self._scenario == "reboot":
            state, enabled = "testing", False
        elif self._scenario == "switch-critical":
            state, enabled = "disabled", False
        return {
            "name": i["switch_wwn"],
            "switch-user-friendly-name": i["switch_name"],
            "fabric-user-friendly-name": self.profile["fabric"]["name"],
            "domain-id": i["domain_id"],
            "fcid-hex": i["fcid"],
            "vf-id": i.get("vf_id", -1),
            "is-principal": i["is_principal"],
            "is-enabled-state": enabled,
            "operational-status-string": state,
            "model": i["switch_type"],
            "firmware-version": i["firmware_version"],
        }

    def _fabric_switches(self) -> list[dict[str, Any]]:
        i = self.profile["identity"]
        local = {
            "name": i["switch_wwn"],
            "switch-user-friendly-name": i["switch_name"],
            "chassis-wwn": i["chassis_wwn"],
            "chassis-user-friendly-name": i["chassis_name"],
            "domain-id": i["domain_id"],
            "is-principal": i["is_principal"],
            "principal": i["is_principal"],
            "fcid-hex": i["fcid"],
            "firmware-version": i["firmware_version"],
            "switch-model": i["switch_type"],
            "serial-number": i["serial_number"],
        }
        return [local, copy.deepcopy(self.profile["fabric"]["peer_switch"])]

    def _ports(self) -> list[dict[str, Any]]:
        i = self.profile["identity"]
        layout = self.profile["port_layout"]
        count = int(layout["logical_port_count"])
        licensed = _parse_ranges(layout["licensed_ranges"])
        active = {int(layout[x]) for x in ("host_port", "storage_port", "isl_port")}
        ports: list[dict[str, Any]] = []
        for index in range(count):
            media = self._media_class(index)
            is_active = index in active
            enabled = index in licensed
            speed = int(layout["default_speed_gbps"]) * 1_000_000_000 if is_active else 0
            protocol_speed = f"{layout['default_speed_gbps']}-gfc" if is_active else "unknown"
            port = {
                "name": f"0/{index}",
                "index": index,
                "default-index": index,
                "wwn": self._port_wwn(index),
                "user-friendly-name": self._port_label(index),
                "port-type-string": "E-Port" if index == int(layout["isl_port"]) else ("F-Port" if index in active else "U-Port"),
                "speed": speed,
                "protocol-speed": protocol_speed,
                "is-enabled-state": enabled,
                "operational-status-string": "online" if is_active else "offline",
                "physical-state": "online" if is_active else "no_light",
                "port-health": "healthy" if is_active else "unknown",
                "pod-license-state": "licensed" if index in licensed else "not-licensed",
                "fcid-hex": f"0x{i['domain_id']:02x}{index:04x}",
                "auto-negotiate-v2": True,
                "trunk-port-enabled": index == int(layout["isl_port"]),
                "fec-enabled": is_active,
                "congestion-signal-enabled": index == int(layout["isl_port"]),
            }
            if index == int(layout["isl_port"]):
                peer = self.profile["fabric"]["peer_switch"]
                port.update(
                    {
                        "neighbor-slot-port": self.profile["fabric"]["peer_isl_port"],
                        "neighbor-node-wwn": peer["name"],
                        "neighbor-switch-user-friendly-name": peer["switch-user-friendly-name"],
                        "neighbor": {"wwn": [peer["name"]]},
                    }
                )
            self._apply_port_scenario(port, index)
            ports.append(port)
        return ports

    def _apply_port_scenario(self, port: dict[str, Any], index: int) -> None:
        layout = self.profile["port_layout"]
        scenario = self._scenario
        fault_port = int(layout["fault_port"])
        isl_port = int(layout["isl_port"])
        if scenario == "port-admin-down" and index == fault_port:
            port.update(
                {
                    "is-enabled-state": False,
                    "operational-status-string": "offline",
                    "physical-state": "offline",
                    "port-health": "unknown",
                }
            )
        elif scenario == "port-unexpected-down" and index == fault_port:
            port.update(
                {
                    "is-enabled-state": True,
                    "operational-status-string": "offline",
                    "physical-state": "faulty",
                    "port-health": "faulty",
                }
            )
        elif scenario == "isl-down" and index == isl_port:
            port.update(
                {
                    "operational-status-string": "offline",
                    "physical-state": "no_light",
                    "port-health": "faulty",
                }
            )
            for key in ("neighbor-slot-port", "neighbor-node-wwn", "neighbor-switch-user-friendly-name", "neighbor"):
                port.pop(key, None)
        elif scenario in {"congestion", "slow-drain"} and index == fault_port:
            port["port-health"] = "marginal"

    def _statistics(self) -> list[dict[str, Any]]:
        layout = self.profile["port_layout"]
        count = int(layout["logical_port_count"])
        cycle = self._collection_cycles
        epoch = self._counter_epoch
        fault_port = int(layout["fault_port"])
        active = {int(layout[x]) for x in ("host_port", "storage_port", "isl_port")}
        values: list[dict[str, Any]] = []
        for index in range(count):
            traffic = cycle * (1_000_000 + index * 1000) if index in active else 0
            base = epoch * 10
            row = {
                "name": f"0/{index}",
                "time-generated": int(time.time()),
                "in-octets": base + traffic,
                "out-octets": base + traffic * 2,
                "in-frames": base + traffic // 2048,
                "out-frames": base + traffic // 1024,
                "in-rate": 8_000_000 if index in active else 0,
                "out-rate": 16_000_000 if index in active else 0,
                "in-frame-rate": 500 if index in active else 0,
                "out-frame-rate": 1000 if index in active else 0,
                "in-crc-errors": 0,
                "invalid-transmission-words": 0,
                "invalid-ordered-sets": 0,
                "link-failures": 0,
                "loss-of-signal": 0,
                "loss-of-sync": 0,
                "class-3-discards": 0,
                "pcs-block-errors": 0,
                "fec-uncorrected": 0,
                "bb-credit-zero": 0,
                "input-buffer-full": 0,
                "state-transition-count": 0,
                "congestion-primitive-in": 0,
            }
            if index == fault_port:
                increment = max(1, cycle)
                if self._scenario == "crc-errors":
                    row["in-crc-errors"] = increment * 100
                elif self._scenario == "link-errors":
                    row["link-failures"] = increment * 10
                    row["state-transition-count"] = increment * 10
                elif self._scenario == "loss-of-signal":
                    row["loss-of-signal"] = increment * 10
                elif self._scenario == "loss-of-sync":
                    row["loss-of-sync"] = increment * 10
                elif self._scenario == "invalid-words":
                    row["invalid-transmission-words"] = increment * 1000
                elif self._scenario == "high-utilisation":
                    speed = int(layout["default_speed_gbps"]) * 1_000_000_000
                    row["in-rate"] = int(speed * 0.92)
                    row["out-rate"] = int(speed * 0.88)
                elif self._scenario == "congestion":
                    row["congestion-primitive-in"] = increment * 10_000
                    row["bb-credit-zero"] = increment * 3_000
                elif self._scenario == "slow-drain":
                    row["bb-credit-zero"] = increment * 20_000
                    row["class-3-discards"] = increment * 100
                    row["input-buffer-full"] = increment * 5_000
            if self._scenario == "reboot":
                for key, value in tuple(row.items()):
                    if key not in {"name", "time-generated"} and isinstance(value, int):
                        row[key] = 0
            values.append(row)
        return values

    def _name_server(self) -> list[dict[str, Any]]:
        layout = self.profile["port_layout"]
        endpoints = self.profile["endpoints"]
        result = []
        for kind, port_key in (("host", "host_port"), ("storage", "storage_port")):
            endpoint = endpoints[kind]
            index = int(layout[port_key])
            result.append(
                {
                    "port-id": f"0x{self.profile['identity']['domain_id']:02x}{index:04x}",
                    "port-name": endpoint["port_wwn"],
                    "node-name": endpoint["node_wwn"],
                    "fabric-port-name": self._port_wwn(index),
                    "class-of-service": "class-3",
                    "node-symbolic-name": endpoint["name"],
                    "port-symbolic-name": f"{endpoint['name']}-p0",
                    "port-type": "n-port",
                }
            )
        return result

    def _power_supplies(self) -> list[dict[str, Any]]:
        result = copy.deepcopy(self.profile["components"]["power_supplies"])
        if self._scenario == "psu-failed" and result:
            result[-1]["operational-state"] = "faulty"
            result[-1]["power-production"] = 0
        return result

    def _fans(self) -> list[dict[str, Any]]:
        result = copy.deepcopy(self.profile["components"]["fans"])
        if self._scenario == "fan-failed" and result:
            result[-1]["operational-state"] = "faulty"
            result[-1]["speed"] = 0
        return result

    def _sensors(self) -> list[dict[str, Any]]:
        result = copy.deepcopy(self.profile["components"]["sensors"])
        if self._scenario == "high-temperature" and result:
            result[0]["state"] = "faulty"
            result[0]["temperature"] = 82
            result[0]["temperature-fahrenheit"] = 180
        return result

    def _media(self) -> list[dict[str, Any]]:
        result = []
        layout = self.profile["port_layout"]
        for index in sorted({int(layout[k]) for k in ("host_port", "storage_port", "isl_port")}):
            media_class = self._media_class(index)
            row = {
                "name": f"0/{index}",
                "identifier": media_class,
                "connector": "lc" if media_class in {"sfp+", "sfp-dd"} else "mpo",
                "vendor-name": "FICTIONAL-LAB",
                "vendor-part-number": f"EMU-{media_class.upper()}-{layout['default_speed_gbps']}G",
                "vendor-serial-number": f"EMU{self.profile_id[-4:].upper()}{index:04d}",
                "nominal-bit-rate": int(layout["default_speed_gbps"]) * 1000,
                "temperature": 31.0 + index / 100,
                "voltage": 3.30,
                "tx-power": -2.4,
                "rx-power": -3.1,
            }
            if self._scenario == "optic-warning" and index == int(layout["optic_port"]):
                row["temperature"] = 73.0
                row["rx-power"] = -9.5
            elif self._scenario == "optic-failed" and index == int(layout["optic_port"]):
                row["temperature"] = 85.0
                row["rx-power"] = -40.0
            result.append(row)
        return result

    def _health(self) -> dict[str, Any]:
        health = {
            "switch-health": "healthy",
            "power-supply-health": "healthy",
            "fan-health": "healthy",
            "wwn-health": "healthy",
            "temperature-sensor-health": "healthy",
            "ha-health": "healthy",
            "control-processor-health": "healthy",
            "core-blade-health": "healthy",
            "flash-health": "healthy",
            "marginal-port-health": "healthy",
            "faulty-port-health": "healthy",
            "missing-sfp-health": "healthy",
            "error-port-health": "healthy",
            "expired-certificate-health": "healthy",
            "airflow-mismatch-health": "healthy",
            "marginal-sfp-health": "healthy",
            "trusted-fos-certificate-health": "healthy",
        }
        scenario = self._scenario
        if scenario in {"switch-degraded", "crc-errors", "congestion", "slow-drain", "optic-warning"}:
            health["switch-health"] = "marginal"
        elif scenario in {"switch-critical", "port-unexpected-down", "isl-down", "psu-failed", "fan-failed", "high-temperature", "optic-failed"}:
            health["switch-health"] = "down" if scenario == "switch-critical" else "marginal"
        if scenario in {"port-unexpected-down", "isl-down"}:
            health["faulty-port-health"] = "down"
        if scenario in {"crc-errors", "congestion", "slow-drain"}:
            health["error-port-health"] = "marginal"
        if scenario == "psu-failed":
            health["power-supply-health"] = "down"
        if scenario == "fan-failed":
            health["fan-health"] = "down"
        if scenario == "high-temperature":
            health["temperature-sensor-health"] = "down"
        if scenario == "optic-warning":
            health["marginal-sfp-health"] = "marginal"
        if scenario == "optic-failed":
            health["marginal-sfp-health"] = "down"
        return health

    def _system_resources(self) -> dict[str, Any]:
        resources = copy.deepcopy(self.profile["components"]["system_resources"])
        if self._scenario == "switch-degraded":
            resources["cpu-usage"] = 91
            resources["memory-usage"] = 88
        elif self._scenario == "switch-critical":
            resources["cpu-usage"] = 99
            resources["memory-usage"] = 97
            resources["free-kernel-memory"] = 8
        return resources

    def _media_class(self, index: int) -> str:
        for media in self.profile["port_layout"]["media_ranges"]:
            if index in _parse_ranges(media["ranges"]):
                return str(media["type"])
        raise ProfileError(f"No media classification for port {index}")

    def _port_label(self, index: int) -> str:
        layout = self.profile["port_layout"]
        if index == int(layout["host_port"]):
            return "fictional-host-a"
        if index == int(layout["storage_port"]):
            return "fictional-array-a"
        if index == int(layout["isl_port"]):
            return "fictional-mixed-fabric-isl"
        return f"unused-{index}"

    def _port_wwn(self, index: int) -> str:
        prefix = self.profile["identity"]["port_wwn_prefix"].split(":")
        suffix = f"{index:04x}"
        return ":".join(prefix + [suffix[:2], suffix[2:]])

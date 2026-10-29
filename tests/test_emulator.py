from __future__ import annotations

import base64
import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from emulator.model import ENDPOINT_KEYS, EmulatorModel, ProfileError
from emulator.server import ApiServer


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATHS = {
    "g620": ROOT / "emulator" / "profiles" / "ds6620b-g620" / "profile.json",
    "g720": ROOT / "emulator" / "profiles" / "ds7720b-g720" / "profile.json",
}


class RunningApi:
    def __init__(self, model: EmulatorModel):
        self.server = ApiServer(("127.0.0.1", 0), model, "monitor", "test-only")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    @staticmethod
    def basic(username="monitor", password="test-only"):
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    def request(self, path, *, method="GET", headers=None, timeout=3):
        request = urllib.request.Request(self.base_url + path, method=method, headers=headers or {})
        return urllib.request.urlopen(request, timeout=timeout)


class ProfileTests(unittest.TestCase):
    def test_profiles_are_distinct_and_complete(self):
        g620 = EmulatorModel.from_file(PROFILE_PATHS["g620"])
        g720 = EmulatorModel.from_file(PROFILE_PATHS["g720"])
        self.assertEqual(g620.profile["identity"]["switch_type"], "162.0")
        self.assertEqual(g720.profile["identity"]["switch_type"], "181.5")
        self.assertEqual(g620.profile["port_layout"]["default_speed_gbps"], 32)
        self.assertEqual(g720.profile["port_layout"]["default_speed_gbps"], 64)
        self.assertEqual(g620._media_class(48), "qsfp")
        self.assertEqual(g720._media_class(48), "sfp-dd")
        self.assertEqual(len(g620.payload(next(path for path, key in ENDPOINT_KEYS.items() if key == "fibrechannel"))["Response"]["fibrechannel"]), 64)
        self.assertEqual(len(g720.profile["components"]["fans"]), 3)

    def test_invalid_profile_is_rejected(self):
        with self.assertRaises(ProfileError):
            EmulatorModel({"profile_id": "broken"})

    def test_mixed_fabric_is_symmetric(self):
        g620 = EmulatorModel.from_file(PROFILE_PATHS["g620"])
        g720 = EmulatorModel.from_file(PROFILE_PATHS["g720"])
        path = "/rest/running/brocade-fabric/fabric-switch"
        names620 = {row["name"] for row in g620.payload(path)["Response"]["fabric-switch"]}
        names720 = {row["name"] for row in g720.payload(path)["Response"]["fabric-switch"]}
        self.assertEqual(names620, names720)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.model = EmulatorModel.from_file(PROFILE_PATHS["g620"])

    def test_session_login_collection_and_logout(self):
        with RunningApi(self.model) as api:
            with api.request("/rest/login", method="POST", headers=api.basic()) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get_content_type(), "application/yang-data+json")
                token = response.headers["Authorization"]
                self.assertTrue(token.startswith("Custom_Basic "))
            with api.request(
                "/rest/running/brocade-fibrechannel-switch/fibrechannel-switch",
                headers={"Authorization": token, "Accept": "application/yang-data+json"},
            ) as response:
                body = json.load(response)
                self.assertEqual(body["Response"]["fibrechannel-switch"][0]["model"], "162.0")
            with api.request("/rest/logout", method="POST", headers={"Authorization": token}) as response:
                self.assertEqual(response.status, 204)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                api.request(
                    "/rest/running/brocade-fibrechannel-switch/fibrechannel-switch",
                    headers={"Authorization": token},
                )
            self.assertEqual(caught.exception.code, 401)

    def test_every_selected_get_endpoint_has_documented_wrapper(self):
        with RunningApi(self.model) as api:
            for path, key in ENDPOINT_KEYS.items():
                with self.subTest(path=path), api.request(path, headers=api.basic()) as response:
                    body = json.load(response)
                    self.assertIn(key, body["Response"])
                    expected_type = dict if key in {"switch-status-policy-report", "system-resources"} else list
                    self.assertIsInstance(body["Response"][key], expected_type)

    def test_wrong_credentials_are_rejected(self):
        with RunningApi(self.model) as api:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                api.request("/rest/login", method="POST", headers=api.basic(password="wrong"))
            self.assertEqual(caught.exception.code, 401)

    def test_unknown_route_is_not_exposed(self):
        with RunningApi(self.model) as api:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                api.request("/rest/running/invented/route", headers=api.basic())
            self.assertEqual(caught.exception.code, 404)

    def test_sessionless_basic_get(self):
        with RunningApi(self.model) as api:
            with api.request("/rest/running/brocade-interface/fibrechannel", headers=api.basic()) as response:
                self.assertEqual(response.status, 200)


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.model = EmulatorModel.from_file(PROFILE_PATHS["g720"])
        self.port_path = "/rest/running/brocade-interface/fibrechannel"
        self.stats_path = "/rest/running/brocade-interface/fibrechannel-statistics"

    def _fault_port(self, path):
        key = ENDPOINT_KEYS[path]
        rows = self.model.payload(path)["Response"][key]
        return next(row for row in rows if row["name"] == "0/1")

    def test_fault_and_recovery(self):
        self.model.set_scenario("port-unexpected-down")
        port = self._fault_port(self.port_path)
        self.assertTrue(port["is-enabled-state"])
        self.assertEqual(port["operational-status-string"], "offline")
        self.assertEqual(port["port-health"], "faulty")
        self.model.set_scenario("recovery")
        recovered = self._fault_port(self.port_path)
        self.assertEqual(recovered["operational-status-string"], "online")
        self.assertEqual(recovered["port-health"], "healthy")

    def test_counter_error_scenarios(self):
        expectations = {
            "crc-errors": "in-crc-errors",
            "link-errors": "link-failures",
            "loss-of-signal": "loss-of-signal",
            "loss-of-sync": "loss-of-sync",
            "invalid-words": "invalid-transmission-words",
            "congestion": "congestion-primitive-in",
            "slow-drain": "bb-credit-zero",
        }
        for scenario, field in expectations.items():
            with self.subTest(scenario=scenario):
                self.model.set_scenario(scenario)
                self.assertGreater(self._fault_port(self.stats_path)[field], 0)

    def test_environmental_and_optic_scenarios(self):
        cases = {
            "psu-failed": ("/rest/running/brocade-fru/power-supply", "operational-state", "faulty"),
            "fan-failed": ("/rest/running/brocade-fru/fan", "operational-state", "faulty"),
            "high-temperature": ("/rest/running/brocade-fru/sensor", "state", "faulty"),
            "optic-failed": ("/rest/running/brocade-media/media-rdp", "rx-power", -40.0),
        }
        for scenario, (path, field, expected) in cases.items():
            with self.subTest(scenario=scenario):
                self.model.set_scenario(scenario)
                rows = self.model.payload(path)["Response"][ENDPOINT_KEYS[path]]
                self.assertTrue(any(row.get(field) == expected for row in rows))

    def test_transport_failures_are_reported(self):
        for scenario, expected in (("auth-failure", "auth_failure"), ("http-500", "http_status"), ("timeout", "timeout_seconds"), ("malformed-payload", "malformed")):
            with self.subTest(scenario=scenario):
                self.model.set_scenario(scenario)
                self.assertTrue(self.model.behavior()[expected])

    def test_partial_payload_remains_parseable(self):
        self.model.set_scenario("partial-payload")
        body = self.model.payload(self.port_path)
        self.assertIn("fibrechannel", body["Response"])
        self.assertEqual(len(body["Response"]["fibrechannel"]), 63)

    def test_maps_singletons_use_documented_shape(self):
        health = self.model.payload("/rest/running/brocade-maps/switch-status-policy-report")
        resources = self.model.payload("/rest/running/brocade-maps/system-resources")
        self.assertIsInstance(health["Response"]["switch-status-policy-report"], dict)
        self.assertIsInstance(resources["Response"]["system-resources"], dict)
        self.assertNotIn("faulty-ports", health["Response"]["switch-status-policy-report"])


if __name__ == "__main__":
    unittest.main()

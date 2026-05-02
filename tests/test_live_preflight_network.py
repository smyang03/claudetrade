from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.live_preflight import _kis_network_checks


class LivePreflightNetworkTests(unittest.TestCase):
    def test_kis_network_check_reports_python_socket_failure(self) -> None:
        config = {"effective": {"KIS_BASE_URL": "https://openapi.koreainvestment.com:9443"}}

        with patch("tools.live_preflight.socket.create_connection", side_effect=PermissionError("blocked")):
            checks = _kis_network_checks(config, "live")

        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].name, "network.kis_rest_python_socket.kr")
        self.assertEqual(checks[0].status, "FAIL")
        self.assertIn("python_executable", checks[0].data)
        self.assertIn("firewall_allow_rule", checks[0].data)
        self.assertIn("PermissionError", checks[0].data["error"])

    def test_kis_network_check_deduplicates_shared_kr_us_endpoint(self) -> None:
        config = {
            "effective": {
                "KIS_BASE_URL": "https://openapi.koreainvestment.com:9443",
                "KIS_BASE_URL_US": "https://openapi.koreainvestment.com:9443",
            }
        }

        with patch("tools.live_preflight.socket.create_connection") as connect_mock:
            checks = _kis_network_checks(config, "live")

        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].status, "PASS")
        connect_mock.assert_called_once()

    def test_kis_network_check_keeps_separate_us_endpoint(self) -> None:
        config = {
            "effective": {
                "KIS_BASE_URL": "https://kr.example:9443",
                "KIS_BASE_URL_US": "https://us.example:9443",
            }
        }

        with patch("tools.live_preflight.socket.create_connection"):
            checks = _kis_network_checks(config, "live")

        self.assertEqual([check.name for check in checks], ["network.kis_rest_python_socket.kr", "network.kis_rest_python_socket.us"])


if __name__ == "__main__":
    unittest.main()

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import unifi  # noqa: E402


def _resp(status=200, body=None):
    r = mock.Mock()
    r.status_code = status
    r.ok = status < 400
    r.reason = "x"
    r.json.return_value = body if body is not None else {}
    return r


def _event(**kw):
    e = {
        "category": "CLIENT_DEVICES", "event": "CLIENT_CONNECTED", "key": "CLIENT_CONNECTED",
        "type": "UPDATE", "timestamp": 1760000000000, "title_raw": "Client Connected",
        "message_raw": "{CLIENT} connected to {WLAN} on {DEVICE}",
        "parameters": {
            "CLIENT": {"id": "aa:bb:cc:dd:ee:01", "name": "iPhone"},
            "WLAN": {"id": "w1"},
            "DEVICE": {"id": "11:22:33:44:55:66", "name": "Living AP"},
        },
    }
    e.update(kw)
    return e


def _args(**kw):
    d = dict(json=False, local=True, filter=None, mac=None, days=7, limit=100)
    d.update(kw)
    return SimpleNamespace(**d)


def _run(fn, args):
    out = io.StringIO()
    with redirect_stdout(out):
        fn(args)
    return out.getvalue()


class LocalSession(unittest.TestCase):
    def setUp(self):
        self.session = mock.Mock()
        for p in (
            mock.patch.object(unifi, "_get_local_session", return_value=(self.session, "https://gw")),
            mock.patch.object(unifi, "_resolve_local_gateway", return_value=("gw", "localkey")),
        ):
            p.start()
            self.addCleanup(p.stop)


class ListEventsV2(LocalSession):
    def test_parses_v2_and_posts_expected_request(self):
        self.session.post.return_value = _resp(body={
            "data": [_event()], "page_number": 0, "total_element_count": 1, "total_page_count": 1})
        out = _run(unifi.cmd_list_events, _args())
        url = self.session.post.call_args[0][0]
        kw = self.session.post.call_args[1]
        self.assertEqual(url, "https://gw/proxy/network/v2/api/site/default/system-log/all")
        self.assertEqual(kw["headers"]["X-API-KEY"], "localkey")
        body = kw["json"]
        self.assertEqual(body["pageNumber"], 0)
        self.assertAlmostEqual(body["timestampTo"] - body["timestampFrom"], 7 * 86400 * 1000, delta=5)
        self.assertIn("Events (1)", out)
        self.assertIn("CLIENT_CONNECTED", out)

    def test_message_raw_rendering(self):
        self.assertEqual(unifi._render_event_message(_event()), "iPhone connected to w1 on Living AP")
        e = _event(message_raw="{CLIENT} left {UNKNOWN}")
        self.assertEqual(unifi._render_event_message(e), "iPhone left {UNKNOWN}")

    def test_mac_filter_matches_parameter_ids(self):
        other = _event(parameters={"CLIENT": {"id": "aa:bb:cc:dd:ee:02", "name": "iPad"}},
                       message_raw="{CLIENT} connected")
        self.session.post.return_value = _resp(body={
            "data": [_event(), other], "total_page_count": 1})
        out = _run(unifi.cmd_list_events, _args(mac="AA:BB:CC:DD:EE:01"))
        self.assertIn("Events (1)", out)
        self.assertIn("iPhone", out)
        self.assertNotIn("iPad", out)
        # device MAC matches too
        out = _run(unifi.cmd_list_events, _args(mac="11:22:33:44:55:66"))
        self.assertIn("Events (1)", out)

    def test_filter_matches_key_event_category(self):
        roam = _event(key="CLIENT_ROAMED", event="CLIENT_ROAMED", category="ROAMING")
        self.session.post.return_value = _resp(body={"data": [_event(), roam], "total_page_count": 1})
        self.assertIn("Events (1)", _run(unifi.cmd_list_events, _args(filter="roam")))
        self.assertIn("Events (1)", _run(unifi.cmd_list_events, _args(filter="client_devices")))

    def test_pagination_stops_at_limit(self):
        def page(n):
            return _resp(body={"data": [_event(timestamp=1760000000000 + n * 10 + i) for i in range(2)],
                               "page_number": n, "total_page_count": 5})
        self.session.post.side_effect = [page(0), page(1), page(2)]
        out = _run(unifi.cmd_list_events, _args(limit=5))
        self.assertIn("Events (5)", out)
        self.assertEqual(self.session.post.call_count, 3)
        self.assertEqual([c[1]["json"]["pageNumber"] for c in self.session.post.call_args_list], [0, 1, 2])

    def test_404_falls_back_to_classic(self):
        self.session.post.return_value = _resp(404, {"meta": {"rc": "error"}})
        classic = {"data": [{"key": "EVT_WU_Roam", "time": 1760000000000, "user": "aa:bb:cc:dd:ee:01"}]}
        self.session.get.return_value = _resp(body=classic)
        out = _run(unifi.cmd_list_events, _args())
        self.assertIn("EVT_WU_Roam", out)
        self.assertIn("stat/event", self.session.get.call_args_list[0][0][0])

    def test_non_404_error_exits(self):
        self.session.post.return_value = _resp(500, {"message": "boom"})
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            _run(unifi.cmd_list_events, _args())


class ListEventsLimits(LocalSession):
    def test_filtered_scan_is_capped(self):
        self.session.post.return_value = _resp(body={
            "data": [_event()], "page_number": 0, "total_page_count": 500})
        err = io.StringIO()
        with redirect_stderr(err):
            out = _run(unifi.cmd_list_events, _args(mac="ff:ff:ff:ff:ff:ff"))
        self.assertEqual(self.session.post.call_count, unifi._MAX_FILTERED_EVENT_PAGES)
        self.assertIn("No events found", out)
        self.assertIn("scanned", err.getvalue())

    def test_times_are_local(self):
        from datetime import datetime
        self.session.post.return_value = _resp(body={"data": [_event()], "total_page_count": 1})
        out = _run(unifi.cmd_list_events, _args())
        expected = datetime.fromtimestamp(1760000000).strftime("%Y-%m-%d %H:%M:%S")
        self.assertIn(expected, out)


class ListAps(unittest.TestCase):
    def test_u7_pro_xg_detected_via_features(self):
        dev = {"name": "AP1", "model": "U7 Pro XG", "features": ["accessPoint"]}
        sw = {"name": "SW1", "model": "USW Flex 2.5G", "features": ["switching"]}
        self.assertTrue(unifi._is_access_point(dev))
        self.assertFalse(unifi._is_access_point(sw))
        self.assertTrue(unifi._is_access_point({"model": "U7 Pro XG", "features": {"accessPoint": None}}))

    def test_fallbacks(self):
        self.assertTrue(unifi._is_access_point({"model": "U7 Pro XG"}))  # keyword fallback
        self.assertTrue(unifi._is_access_point({"model": "Whatever", "type": "uap"}))
        self.assertFalse(unifi._is_access_point({"model": "USW Flex 2.5G"}))

    def test_cmd_list_aps(self):
        data = {"data": [{"devices": [
            {"name": "AP1", "model": "U7 Pro XG", "features": ["accessPoint"]},
            {"name": "SW1", "model": "USW Flex 2.5G", "features": ["switching"]},
        ]}]}
        with mock.patch.object(unifi, "api_request", return_value=data):
            out = _run(unifi.cmd_list_aps, SimpleNamespace(json=False))
        self.assertIn("AP1", out)
        self.assertNotIn("SW1", out)


class Config(unittest.TestCase):
    def _load(self, env, cfg=None):
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(Path, "exists", return_value=cfg is not None), \
             mock.patch.object(Path, "read_text", return_value=json.dumps(cfg or {})):
            return unifi._load_config()

    def test_local_only_config_without_api_key(self):
        cfg = self._load({}, {"gateway_ip": "1.2.3.4", "local_api_key": "k", "gateway_fingerprint": "AA"})
        self.assertNotIn("api_key", cfg)
        cfg = self._load({"UNIFI_GATEWAY_IP": "1.2.3.4"})  # no config file at all
        self.assertNotIn("api_key", cfg)

    def test_cloud_key_from_env(self):
        self.assertEqual(self._load({"UNIFI_API_KEY": "abc"})["api_key"], "abc")

    def test_cloud_call_without_key_gives_clear_error(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.dict(unifi.CONFIG, {}, clear=True), \
             redirect_stderr(err), self.assertRaises(SystemExit):
            unifi.api_request("/v1/hosts")
        self.assertIn("cloud API key", err.getvalue())

    def test_local_request_needs_no_cloud_key(self):
        session = mock.Mock()
        session.get.return_value = _resp(body={"data": []})
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.dict(unifi.CONFIG, {}, clear=True), \
             mock.patch.object(unifi, "_get_local_session", return_value=(session, "https://gw")):
            self.assertEqual(unifi._local_api_request("/v1/sites", "gw", "localkey"), {"data": []})


if __name__ == "__main__":
    unittest.main()

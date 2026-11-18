import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from provider_adapters import MiniMaxAdapter, configured_adapters
from provider_jobs import Generation, JobError


class AdapterTest(unittest.TestCase):
    def adapter(self, response):
        transport = mock.Mock()
        transport.json.return_value = response
        adapter = MiniMaxAdapter(transport=transport)
        return adapter, transport

    def test_real_provider_disabled_by_default_and_capability_differences(self):
        adapters = configured_adapters({})
        self.assertTrue(adapters["simulation"].capabilities.cancellation)
        self.assertFalse(adapters["minimax"].enabled)
        self.assertFalse(adapters["minimax"].capabilities.cancellation)
        self.assertIn("image", adapters["minimax"].capabilities.inputs)
        self.assertNotIn("reference", adapters["minimax"].capabilities.modes)
        self.assertFalse(adapters["minimax"].capabilities.live_verified)

    @mock.patch.dict(os.environ, {"MINIMAX_API_KEY": "test-credential"})
    def test_submit_protocol_and_prompt_optimizer(self):
        adapter, transport = self.adapter({"base_resp": {"status_code": 0}, "task_id": "1234"})
        self.assertEqual(adapter.submit(Generation("A real shot", 6, "MiniMax-Hailuo-2.3", "1080P")), "1234")
        transport.json.assert_called_once_with("POST", "https://api.minimax.io/v1/video_generation", body={"model": "MiniMax-Hailuo-2.3", "prompt": "A real shot", "duration": 6, "resolution": "1080P", "prompt_optimizer": False}, token="test-credential")

    def test_unsupported_duration_rejected_before_network(self):
        adapter, transport = self.adapter({})
        with self.assertRaises(JobError):
            adapter.submit(Generation("x", 10, "MiniMax-Hailuo-2.3", "1080P"))
        transport.json.assert_not_called()

    @mock.patch.dict(os.environ, {"MINIMAX_API_KEY": "test-credential"})
    def test_poll_each_documented_state(self):
        for remote, state in (("Preparing", "queued"), ("Queueing", "queued"), ("Processing", "running"), ("Success", "succeeded"), ("Fail", "failed")):
            adapter, transport = self.adapter({"base_resp": {"status_code": 0}, "task_id": "123", "status": remote, "file_id": "456"})
            self.assertEqual(adapter.poll("123").state, state)
            self.assertTrue(transport.json.call_args.args[1].endswith("?task_id=123"))

    @mock.patch.dict(os.environ, {"MINIMAX_API_KEY": "test-credential"})
    def test_unknown_status_mismatched_id_and_provider_error_are_rejected(self):
        for response in ({"base_resp": {"status_code": 0}, "task_id": "other", "status": "Success", "file_id": "456"}, {"base_resp": {"status_code": 0}, "task_id": "123", "status": "Maybe"}, {"base_resp": {"status_code": 1000, "status_msg": "secret"}}):
            adapter, _ = self.adapter(response)
            with self.assertRaises(JobError) as caught:
                adapter.poll("123")
            self.assertNotIn("secret", str(caught.exception))

    @mock.patch.dict(os.environ, {"MINIMAX_API_KEY": "test-credential"})
    def test_download_retrieves_fresh_url_and_never_gives_cdn_a_key(self):
        adapter, transport = self.adapter({"base_resp": {"status_code": 0}, "file": {"file_id": "456", "purpose": "video_generation", "download_url": "https://cdn.example.com/signed"}})
        adapter.files = mock.Mock()
        adapter.download("456", Path("stage.part"))
        adapter.files.download.assert_called_once_with("https://cdn.example.com/signed", Path("stage.part"))

    def test_missing_credential_and_unsupported_cancel_do_not_call_transport(self):
        adapter, transport = self.adapter({})
        with mock.patch.dict(os.environ, {"MINIMAX_API_KEY": ""}), self.assertRaises(JobError):
            adapter.submit(Generation("x", 6, "MiniMax-Hailuo-02", "768P"))
        with self.assertRaises(JobError):
            adapter.cancel("123")
        transport.json.assert_not_called()

    def test_relay_must_select_implemented_protocol_and_approved_hosts(self):
        adapter = configured_adapters({"providers": [{"id": "relay", "protocol": "minimax-video-v1", "host": "relay.example.com", "credential_env": "H3_RELAY_KEY", "download_hosts": ["cdn.example.com"], "enabled": False}]})["relay"]
        self.assertEqual(adapter.host, "relay.example.com")
        self.assertFalse(adapter.enabled)
        for row in ({"id": "relay", "protocol": "openai-like"}, {"id": "relay", "protocol": "minimax-video-v1", "host": "https://user:pass@host"}, {"id": "relay", "protocol": "minimax-video-v1", "key": "secret"}, {"id": "simulation", "protocol": "minimax-video-v1"}):
            with self.assertRaises(ValueError):
                configured_adapters({"providers": [row]})

    def test_quotes_are_specific_to_model_duration_and_resolution(self):
        adapter = MiniMaxAdapter(quotes_cents={"MiniMax-Hailuo-2.3:768P:6": 10, "MiniMax-Hailuo-2.3:1080P:6": 20})
        self.assertEqual(adapter.quote(Generation("x", 6, "MiniMax-Hailuo-2.3", "768P")), 10)
        self.assertEqual(adapter.quote(Generation("x", 6, "MiniMax-Hailuo-2.3", "1080P")), 20)
        self.assertIsNone(adapter.quote(Generation("x", 10, "MiniMax-Hailuo-2.3", "768P")))
        for quotes in ({"MiniMax-Hailuo-2.3:1080P:10": 20}, {"MiniMax-Hailuo-2.3:768P:6": True}):
            with self.assertRaises(JobError):
                configured_adapters({"providers": [{"id": "minimax", "protocol": "minimax-video-v1", "quotes_cents": quotes}]})

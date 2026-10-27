import json
from unittest.mock import patch
import unittest

import httpx

from finlens.local import local_config, pull_model
from finlens.providers import ModelConfig, Ollama, ProviderError


class LocalModeTests(unittest.TestCase):
    def test_default_profile_is_local_and_multilingual(self):
        with patch.dict("os.environ", {}, clear=True):
            config = local_config()
        self.assertEqual(config.base_url, "http://127.0.0.1:11434")
        self.assertEqual(config.chat_model, "qwen3:4b")
        self.assertEqual(config.embed_model, "bge-m3:latest")

    def test_local_mode_rejects_remote_server_or_cloud_model(self):
        for environment in [{"OLLAMA_BASE_URL": "https://ollama.com"},
                            {"FINLENS_CHAT_MODEL": "gemma4:cloud"},
                            {"OLLAMA_BASE_URL": "http://127.0.0.1.attacker.example:11434"}]:
            with patch.dict("os.environ", environment, clear=True):
                with self.assertRaises(ProviderError):
                    local_config()

    def test_chat_disables_thinking_and_bounds_context(self):
        def handler(request):
            payload = json.loads(request.content)
            self.assertFalse(payload["think"])
            self.assertFalse(payload["stream"])
            self.assertEqual(payload["options"]["num_ctx"], 8192)
            self.assertEqual(payload["format"]["type"], "object")
            self.assertEqual(payload["format"]["properties"]["statements"]["items"]["properties"]["sources"]["items"]["enum"], ["S1"])
            content = json.dumps({"abstain": False, "statements": [{"text": "Chi phí tăng.", "sources": ["S1"]}]})
            return httpx.Response(200, json={"message": {"content": content}})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            provider = Ollama(ModelConfig(chat_model="qwen3:4b"), client)
            self.assertIn("[S1]", provider.generate("Vì sao?", [{"id": "S1", "excerpt": "Chi phí tăng."}]))

    def test_structured_claim_cannot_invent_or_omit_sources(self):
        for sources in [["S99"], [], [1]]:
            content = json.dumps({"abstain": False, "statements": [{"text": "Chi phí tăng.", "sources": sources}]})
            transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"message": {"content": content}}))
            with httpx.Client(transport=transport) as client:
                with self.assertRaises(ProviderError):
                    Ollama(ModelConfig(chat_model="test"), client).generate("Vì sao?", [{"id": "S1", "excerpt": "Chi phí tăng."}])

    def test_structured_abstention_is_explicit(self):
        content = json.dumps({"abstain": True, "statements": []})
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"message": {"content": content}}))
        with httpx.Client(transport=transport) as client:
            answer = Ollama(ModelConfig(chat_model="test"), client).generate("Lương CEO?", [{"id": "S1", "excerpt": "Chi phí tăng."}])
            self.assertEqual(answer, "KHONG_DU_DU_LIEU")

    def test_pull_requires_server_success_event(self):
        actual_client = httpx.Client
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text='{"status":"pulling manifest"}\n'))
        with patch("finlens.local.httpx.Client", side_effect=lambda **kwargs: actual_client(transport=transport)):
            with self.assertRaises(ProviderError):
                pull_model(ModelConfig(), "test:small")

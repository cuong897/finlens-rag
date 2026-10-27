import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

from fastapi.testclient import TestClient
import httpx

from finlens.api import create_app
from finlens.environment import load_env_file
from finlens.providers import Gemini, ModelConfig, ProviderError, make_provider

EVIDENCE = [{"id": "S1", "excerpt": "Biên lợi nhuận cải thiện nhờ giảm chi phí logistics."}]


def candidate(statement=None, finish="STOP"):
    output = {"abstain": False, "statements": [statement or {"text": "Chi phí logistics giảm.", "sources": ["S1"]}]}
    return {"candidates": [{"finishReason": finish, "content": {"parts": [{"text": json.dumps(output)}]}}]}


class GeminiTests(unittest.TestCase):
    def setUp(self):
        self.config = ModelConfig(chat_model="gemini-3.8-flash", embed_model="bge-m3:latest", chat_provider="gemini")

    def test_cloud_payload_key_is_header_and_only_evidence_is_sent(self):
        def handler(request):
            self.assertEqual(request.url.host, "generativelanguage.googleapis.com")
            self.assertEqual(request.url.query, b"")
            self.assertEqual(request.headers["x-goog-api-key"], "unit-test-key")
            payload = json.loads(request.content)
            self.assertNotIn("unit-test-key", request.content.decode())
            self.assertNotIn("tools", payload)
            self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 2048)
            self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
            return httpx.Response(200, json=candidate())
        with patch.dict("os.environ", {"GEMINI_API_KEY": "unit-test-key"}):
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                answer = Gemini(self.config, client).generate("Vì sao?", EVIDENCE)
                self.assertEqual(answer, "Chi phí logistics giảm. [S1]")

    def test_no_key_means_no_http_call(self):
        def handler(request):
            self.fail("Must not contact Google without a key")
        with patch.dict("os.environ", {}, clear=True):
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaisesRegex(ProviderError, "GEMINI_API_KEY"):
                    Gemini(self.config, client).generate("Vì sao?", EVIDENCE)

    def test_quota_error_does_not_retry_or_leak_provider_details(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(429, json={"error": {"message": "unit-test-key should never leak"}})
        with patch.dict("os.environ", {"GEMINI_API_KEY": "unit-test-key"}):
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(ProviderError) as caught:
                    Gemini(self.config, client).generate("Vì sao?", EVIDENCE)
        self.assertEqual(len(calls), 1)
        self.assertIn("hạn mức", str(caught.exception))
        self.assertNotIn("unit-test-key", str(caught.exception))

    def test_invalid_source_and_incomplete_output_are_rejected(self):
        responses = [candidate({"text": "Chi phí giảm.", "sources": ["S99"]}), candidate(finish="MAX_TOKENS"), {"candidates": []}]
        with patch.dict("os.environ", {"GEMINI_API_KEY": "unit-test-key"}):
            for body in responses:
                with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))) as client:
                    with self.assertRaises(ProviderError):
                        Gemini(self.config, client).generate("Vì sao?", EVIDENCE)

    def test_overloaded_service_has_clear_fallback_without_retry(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(503, json={"error": {"message": "High demand"}})
        with patch.dict("os.environ", {"GEMINI_API_KEY": "unit-test-key"}):
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaisesRegex(ProviderError, "quá tải"):
                    Gemini(self.config, client).generate("Vì sao?", EVIDENCE)
        self.assertEqual(len(calls), 1)

    def test_embedding_channel_stays_local(self):
        class LocalEmbedding:
            def embed(self, texts):
                return [[1.0, 0.0] for _ in texts]
        backend = Gemini(self.config, embedder=LocalEmbedding())
        self.assertEqual(backend.embed(["financial statement"]), [[1.0, 0.0]])
        self.assertIsInstance(make_provider(self.config), Gemini)

    def test_paid_model_not_automatically_enabled(self):
        with self.assertRaises(ValueError):
            Gemini(ModelConfig(chat_model="gemini-4-argon", chat_provider="gemini"))

    def test_explicit_env_file_and_health_do_not_expose_key(self):
        root = Path(__file__).resolve().parents[1] / "data" / "test-runs"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            path = Path(directory) / ".env"
            path.write_text('FINLENS_PROVIDER=gemini\nGEMINI_API_KEY="unit-test-key"\n', encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True):
                load_env_file(path)
                config = ModelConfig.from_env()
                self.assertEqual(config.chat_model, "gemini-3.8-flash")
                with TestClient(create_app(Path(directory) / "db", config)) as client:
                    response = client.get("/api/health")
                self.assertTrue(response.json()["chat_ready"])
                self.assertNotIn("unit-test-key", response.text)
            assert Path(directory).resolve().is_relative_to(root.resolve())

    def test_env_loader_does_not_modify_system_settings_or_evaluate_values(self):
        root = Path(__file__).resolve().parents[1] / "data" / "test-runs"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            path = Path(directory) / ".env"
            path.write_text('GEMINI_API_KEY=$(never-executed)\n', encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True):
                load_env_file(path)
                import os
                self.assertEqual(os.environ["GEMINI_API_KEY"], "$(never-executed)")
            path.write_text('HOME=should-not-change\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_env_file(path)
            assert Path(directory).resolve().is_relative_to(root.resolve())

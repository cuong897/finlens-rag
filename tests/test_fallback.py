import json
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

import httpx

from finlens.providers import GeminiFallback, ModelConfig, ProviderError

EVIDENCE = [{"id": "S1", "excerpt": "Chi phí logistics giảm."}]


def valid_body(abstain=False, source="S1"):
    output = {"abstain": abstain, "statements": [] if abstain else [{"text": "Chi phí logistics giảm.", "sources": [source]}]}
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(output)}]}}]}


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.config = ModelConfig(chat_model="gemini-3.8-flash", chat_provider="gemini")
        self.now = 100
        self.env = patch.dict("os.environ", {"GEMINI_API_KEY": "unit-secret"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def backend(self, handler, config=None):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return GeminiFallback(config or self.config, client, clock=lambda: self.now)

    def test_primary_outage_uses_next_model_with_validated_sources(self):
        calls = []
        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(503 if len(calls) == 1 else 200, json=valid_body())
        result = self.backend(handler).generate_with_report("Vì sao?", EVIDENCE)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.model, "gemini-3.6-flash")
        self.assertEqual(result.text, "Chi phí logistics giảm. [S1]")
        self.assertEqual([a["status"] for a in result.attempts], ["failed", "succeeded"])

    def test_cooldown_skips_outage_then_recovers(self):
        calls = []
        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(503 if len(calls) == 1 else 200, json=valid_body())
        backend = self.backend(handler)
        backend.generate("Vì sao?", EVIDENCE)
        second = backend.generate_with_report("Vì sao?", EVIDENCE)
        self.assertEqual(second.attempts[0]["code"], "cooldown")
        self.assertEqual(len(calls), 3)
        self.now += 61
        self.assertEqual(backend.generate_with_report("Vì sao?", EVIDENCE).model, self.config.chat_model)

    def test_rate_limit_respects_retry_after_and_moves_to_other_model(self):
        def handler(request):
            if "3.8" in request.url.path:
                return httpx.Response(429, headers={"Retry-After": "180"})
            return httpx.Response(200, json=valid_body())
        backend = self.backend(handler)
        backend.generate("Vì sao?", EVIDENCE)
        self.now += 61
        result = backend.generate_with_report("Vì sao?", EVIDENCE)
        self.assertEqual(result.attempts[0]["code"], "cooldown")
        self.assertGreaterEqual(backend.states[self.config.chat_model]["until"], 280)

    def test_auth_bad_request_and_safety_do_not_trigger_model_shopping(self):
        for status in [401, 403, 400, 402, 200]:
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(status, json={"candidates": [{"finishReason": "SAFETY"}]})
            backend = self.backend(handler)
            with self.assertRaises(ProviderError):
                backend.generate("Vì sao?", EVIDENCE)
            self.assertEqual(len(calls), 1)
            if status != 200:
                with self.assertRaises(ProviderError):
                    backend.generate("Vì sao?", EVIDENCE)
                self.assertEqual(len(calls), 1)

    def test_grounded_abstention_is_final(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=valid_body(abstain=True))
        self.assertEqual(self.backend(handler).generate("Vì sao?", EVIDENCE), "KHONG_DU_DU_LIEU")
        self.assertEqual(len(calls), 1)

    def test_prompt_feedback_block_is_final_without_cooling_healthy_model(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
        backend = self.backend(handler)
        for _ in range(2):
            with self.assertRaises(ProviderError) as caught:
                backend.generate("Vì sao?", EVIDENCE)
            self.assertEqual(caught.exception.code, "blocked")
        self.assertEqual(len(calls), 2)
        self.assertEqual(backend.states[self.config.chat_model]["until"], 0)

    def test_environment_can_disable_or_choose_fallback_order(self):
        with patch.dict("os.environ", {"FINLENS_PROVIDER": "gemini", "FINLENS_FALLBACK_MODELS": ""}):
            self.assertEqual(ModelConfig.from_env().fallback_models, ())
        with patch.dict("os.environ", {"FINLENS_FALLBACK_MODELS": "gemini-3.5-flash-lite, gemini-3.6-flash,gemini-3.5-flash-lite"}):
            self.assertEqual(ModelConfig.from_env().fallback_models, ("gemini-3.5-flash-lite", "gemini-3.6-flash"))

    def test_invalid_sources_never_count_as_fallback_success(self):
        backend = self.backend(lambda request: httpx.Response(200, json=valid_body(source="S99")))
        with self.assertRaises(ProviderError) as caught:
            backend.generate("Vì sao?", EVIDENCE)
        self.assertEqual(len(caught.exception.attempts), 3)
        self.assertTrue(all(a["status"] == "failed" for a in caught.exception.attempts))
        self.assertNotIn("unit-secret", str(caught.exception))

    def test_all_down_is_bounded_and_next_request_makes_no_network_calls(self):
        calls = []
        def handler(request):
            calls.append(request)
            self.assertLessEqual(request.extensions["timeout"]["read"], 10)
            raise httpx.ReadTimeout("unit-secret", request=request)
        backend = self.backend(handler)
        for _ in range(2):
            with self.assertRaises(ProviderError):
                backend.generate("Vì sao?", EVIDENCE)
        self.assertEqual(len(calls), 3)

    def test_budget_prevents_starting_another_attempt(self):
        calls = []
        def handler(request):
            calls.append(request)
            self.now += 31
            return httpx.Response(503)
        with self.assertRaises(ProviderError):
            self.backend(handler).generate("Vì sao?", EVIDENCE)
        self.assertEqual(len(calls), 1)

    def test_paid_or_excessive_fallbacks_rejected_and_can_disable_chain(self):
        for models in [("gemini-4-argon",), ("gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite")]:
            with self.assertRaises(ValueError):
                self.backend(lambda request: None, replace(self.config, fallback_models=models))
        config = replace(self.config, fallback_models=())
        self.assertEqual(self.backend(lambda request: None, config).models, (config.chat_model,))

    def test_concurrent_questions_keep_attempts_separate_and_skip_busy_model(self):
        entered, release = threading.Event(), threading.Event()
        outputs, errors = [], []
        def handler(request):
            if "3.8" in request.url.path:
                entered.set()
                if not release.wait(3):
                    raise AssertionError("test lease never released")
            return httpx.Response(200, json=valid_body())
        backend = self.backend(handler)
        def worker():
            try:
                outputs.append(backend.generate_with_report("Vì sao?", EVIDENCE))
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            second = backend.generate_with_report("Câu khác?", EVIDENCE)
            self.assertEqual(second.model, "gemini-3.6-flash")
            self.assertEqual(second.attempts[0]["code"], "busy")
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertEqual(len(outputs[0].attempts), 1)

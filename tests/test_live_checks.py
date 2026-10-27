import importlib.util
from pathlib import Path
import unittest

import httpx

spec = importlib.util.spec_from_file_location("gemini_check", Path(__file__).resolve().parents[1] / "scripts" / "check_gemini.py")
checks_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks_module)


class LiveCheckTests(unittest.TestCase):
    def test_outage_fallback_is_failure_and_stops_further_calls(self):
        calls = []
        def handler(request):
            if request.url.path == "/api/health":
                return httpx.Response(200, json={"chat_provider": "gemini", "chat_ready": True})
            calls.append(request)
            return httpx.Response(200, json={"status": "evidence_found", "mode": "extractive",
                "retrieval": "hybrid_rrf", "generation_attempted": True,
                "generation_error": {"code": "service_unavailable", "retryable": True},
                "citations": [], "comparisons": []})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            report = checks_module.run_checks(client, "http://testserver", interval=0)
        self.assertEqual(report["passed"], 0)
        self.assertEqual(report["attempted"], 1)
        self.assertEqual(report["skipped"], 3)
        self.assertEqual(len(calls), 1)

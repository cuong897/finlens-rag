from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from finlens.api import create_app
from finlens.engine import Engine
from finlens.providers import Gemini, ModelConfig, Ollama, ProviderError
from finlens.store import Store


class RetrievalPreparationTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / 'data/test-runs'
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.store.add_document({'id': 'a', 'company': 'A', 'report_year': 2024,
            'scope': 'consolidated', 'filename': 'a.pdf', 'sha256': 'a', 'is_demo': False,
            'pdf_path': None}, ['Doanh thu thuần 100 tỷ VND năm 2024'])
        self.cid = self.store.chunks()[0]['id']
        self.config = ModelConfig(embed_model='m')

    def test_preparation_reuses_application_snapshot_and_never_generates(self):
        self.store.save_embeddings('m', [(self.cid, [1, 0])])
        provider = Mock(); provider.embed.return_value = [[1, 0]]
        engine = Engine(self.store, self.config, provider)
        report = engine.prepare_retrieval()
        self.assertEqual(report['status'], 'ready')
        provider.embed.assert_called_once_with(['FinLens: tìm bằng chứng trong báo cáo tài chính.'], timeout=8)
        provider.generate.assert_not_called(); provider.generate_with_report.assert_not_called()
        self.assertIs(self.store._vector_cache[2], self.store.retrieval_inputs(model='m')[1])
        self.assertEqual(self.store.facts(), [])

    def test_missing_disabled_and_empty_indexes_never_contact_a_model(self):
        provider = Mock()
        for config, dataset, expected in [(self.config, self.store, 'degraded'),
                (ModelConfig(), self.store, 'disabled'), (self.config, Store(Path(self.tmp.name) / 'empty'), 'empty')]:
            with self.subTest(expected=expected):
                report = Engine(dataset, config, provider).prepare_retrieval()
                self.assertEqual(report['status'], expected)
                self.assertIn('latency_ms', report)
        provider.embed.assert_not_called()

    def test_outage_and_changed_dimensions_are_degraded_not_fatal(self):
        self.store.save_embeddings('m', [(self.cid, [1, 0])])
        for answer in [ProviderError('Local model unavailable'), [[1, 0, 1]]]:
            provider = Mock()
            if isinstance(answer, Exception): provider.embed.side_effect = answer
            else: provider.embed.return_value = answer
            report = Engine(self.store, self.config, provider).prepare_retrieval()
            self.assertEqual(report['status'], 'degraded')
            self.assertTrue(report['warnings'])

    def test_app_warms_its_own_engine_once_and_exposes_historical_status(self):
        app = create_app(self.tmp.name, self.config, warm_retrieval=True)
        with patch.object(app.state.engine, 'prepare_retrieval', return_value={'status': 'degraded', 'warnings': ['local offline']}) as prepare:
            with TestClient(app) as client:
                for _ in range(2):
                    self.assertEqual(client.get('/api/health').json()['retrieval_preparation']['status'], 'degraded')
            prepare.assert_called_once_with()
        app = create_app(self.tmp.name, self.config)
        with patch.object(app.state.engine, 'prepare_retrieval') as prepare:
            with TestClient(app) as client:
                self.assertEqual(client.get('/api/health').json()['retrieval_preparation']['status'], 'not_requested')
            prepare.assert_not_called()

    def test_warmup_timeout_is_forwarded_only_to_local_embedding_transport(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={'embeddings': [[1, 0]]})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            provider = Gemini(ModelConfig(chat_model='gemini-3.8-flash', embed_model='m'),
                              embedder=Ollama(self.config, client))
            self.assertEqual(provider.embed(['local preparation'], timeout=8), [[1, 0]])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].url.path, '/api/embed')
        self.assertEqual(seen[0].extensions['timeout']['read'], 8)

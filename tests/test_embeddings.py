import contextlib
import io
from unittest.mock import patch
import unittest

import httpx

from finlens.cli import main
from finlens.local import prepare_embeddings, pull_model, validate_embedding_server
from finlens.providers import ModelConfig, Ollama, make_provider, ProviderError


class EmbeddingOnlyTests(unittest.TestCase):
    def test_default_generation_is_gemini_and_extractive_mode_ignores_chat_model(self):
        with patch.dict("os.environ", {}, clear=True):
            config = ModelConfig.from_env()
        self.assertEqual((config.chat_provider, config.chat_model), ("gemini", "gemini-3.8-flash"))
        with patch.dict("os.environ", {"FINLENS_PROVIDER": "extractive", "FINLENS_CHAT_MODEL": "unused-chat"}, clear=True):
            config = ModelConfig.from_env()
        self.assertEqual(config.chat_model, "")
        self.assertIsInstance(make_provider(config), Ollama)

    def test_removed_provider_cannot_be_selected_via_environment_or_direct_config(self):
        with patch.dict("os.environ", {"FINLENS_PROVIDER": "ollama"}, clear=True):
            with self.assertRaisesRegex(ValueError, "chat local"):
                ModelConfig.from_env()
        with self.assertRaisesRegex(ValueError, "chat local"):
            make_provider(ModelConfig(chat_provider="ollama"))
        with self.assertRaises(ValueError):
            make_provider(ModelConfig(chat_provider="extractive", chat_model="unused-chat"))

    def test_ollama_transport_has_no_chat_generation_method(self):
        provider = Ollama(ModelConfig(embed_model="bge-m3:latest"))
        self.assertFalse(hasattr(provider, "generate"))

    def test_preparation_only_downloads_embedding_model_and_preserves_gemini(self):
        actual_client = httpx.Client
        config = ModelConfig(chat_model="gemini-3.8-flash")
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"models": []}))
        with patch("finlens.local.ensure_server"), patch("finlens.local.pull_model") as pull:
            with patch("finlens.local.httpx.Client", side_effect=lambda **kwargs: actual_client(transport=transport)):
                result = prepare_embeddings("unused", config)
        self.assertEqual(result.chat_model, "gemini-3.8-flash")
        self.assertEqual(result.embed_model, "bge-m3:latest")
        pull.assert_called_once_with(result, "bge-m3:latest")

    def test_installed_embeddings_are_reused_without_downloading_any_model(self):
        actual_client = httpx.Client
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"models": [{"name": "bge-m3:latest"}]}))
        with patch("finlens.local.ensure_server"), patch("finlens.local.pull_model") as pull:
            with patch("finlens.local.httpx.Client", side_effect=lambda **kwargs: actual_client(transport=transport)):
                prepare_embeddings("unused", ModelConfig())
        pull.assert_not_called()

    def test_embedding_runtime_rejects_remote_or_cloud_configuration(self):
        for config in [ModelConfig(base_url="https://ollama.com"),
                       ModelConfig(embed_model="embedding:cloud"),
                       ModelConfig(base_url="http://127.0.0.1.attacker.example:11434")]:
            with self.assertRaises(ProviderError):
                validate_embedding_server(config)
        validate_embedding_server(ModelConfig(chat_model="gemini-3.8-flash", embed_model="bge-m3:latest"))

    def test_pull_requires_server_success_event(self):
        actual_client = httpx.Client
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text='{"status":"pulling manifest"}\n'))
        with patch("finlens.local.httpx.Client", side_effect=lambda **kwargs: actual_client(transport=transport)):
            with self.assertRaises(ProviderError):
                pull_model(ModelConfig(), "test-embedding:small")

    def test_removed_local_commands_are_rejected_before_starting_or_downloading(self):
        for command in ["local", "prepare-local"]:
            with patch("sys.argv", ["finlens", command]), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main()
            self.assertEqual(error.exception.code, 2)

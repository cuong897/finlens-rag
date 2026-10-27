"""Explicit, local-only Ollama startup and model preparation for FinLens."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlparse

import httpx

from finlens.providers import ModelConfig, ProviderError

OLLAMA_VERSION = "0.40.11"
CHAT_MODEL = "qwen3:4b"
EMBED_MODEL = "bge-m3:latest"


def local_config():
    config = ModelConfig.from_env()
    config = ModelConfig(config.base_url, config.chat_model or CHAT_MODEL, config.embed_model or EMBED_MODEL)
    parsed = urlparse(config.base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
        raise ProviderError("Chế độ local chỉ kết nối Ollama trên localhost qua HTTP.")
    if any(":cloud" in name.lower() for name in (config.chat_model, config.embed_model)):
        raise ProviderError("Chế độ local không sử dụng mô hình cloud.")
    return config


def ensure_server(config: ModelConfig, data_dir: str | Path):
    base = Path(data_dir).resolve() / "ollama"
    base.mkdir(parents=True, exist_ok=True)
    runtime = base / f"runtime-{OLLAMA_VERSION}" / "ollama.exe"
    with httpx.Client(timeout=3, trust_env=False) as client:
        try:
            response = client.get(config.base_url + "/api/version")
            response.raise_for_status()
            version = response.json()["version"]
            print(f"Reusing local Ollama {version} at {config.base_url}", flush=True)
            return {"version": version, "started": False}
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    if sys.platform != "win32" or not runtime.is_file():
        raise ProviderError("Chưa có Ollama. Trên Windows, chạy python scripts/setup_ollama.py trước; hệ điều hành khác dùng bản chính thức.")
    marker = runtime.parent / "finlens-runtime.json"
    if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")).get("version") != OLLAMA_VERSION:
        raise ProviderError("Runtime chưa có thông tin xác minh. Hãy chạy lại bước setup chính thức.")
    parsed = urlparse(config.base_url)
    environment = os.environ.copy()
    environment.update({
        "OLLAMA_HOST": parsed.netloc, "OLLAMA_NO_CLOUD": "1",
        "OLLAMA_MODELS": str(base / "models"), "OLLAMA_CONTEXT_LENGTH": "8192",
        "OLLAMA_NUM_PARALLEL": "1",
    })
    log_path = base / "server.log"
    with log_path.open("ab") as log:
        process = subprocess.Popen([str(runtime), "serve"], cwd=runtime.parent, env=environment,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    ownership = {"pid": process.pid, "executable": str(runtime), "base_url": config.base_url}
    (base / "server-process.json").write_text(json.dumps(ownership, indent=2), encoding="utf-8")
    with httpx.Client(timeout=2, trust_env=False) as client:
        for _ in range(40):
            if process.poll() is not None:
                raise ProviderError(f"Ollama dừng khi khởi động. Xem {log_path}.")
            try:
                response = client.get(config.base_url + "/api/version")
                response.raise_for_status()
                return {"version": response.json()["version"], "started": True, "pid": process.pid}
            except (httpx.HTTPError, ValueError, KeyError):
                time.sleep(0.5)
    raise ProviderError(f"Ollama chưa sẵn sàng. Xem {log_path}.")


def pull_model(config: ModelConfig, name: str):
    print(f"Preparing local model: {name}", flush=True)
    last_status, last_bytes, success = "", {}, False
    try:
        with httpx.Client(timeout=httpx.Timeout(30, read=180), trust_env=False) as client:
            with client.stream("POST", config.base_url + "/api/pull", json={"model": name, "stream": True}) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    event = json.loads(line)
                    if event.get("error"):
                        raise ProviderError(f"Không tải được {name}: {event['error']}")
                    status = event.get("status", "")
                    digest = event.get("digest", "")
                    completed = event.get("completed", 0)
                    if status != last_status or completed - last_bytes.get(digest, 0) >= 128 * 1024**2:
                        progress = f" {completed / 1024**2:.0f}/{event.get('total', 0) / 1024**2:.0f} MiB" if "total" in event else ""
                        print(f"{name}: {status}{progress}", flush=True)
                        last_bytes[digest] = completed
                        last_status = status
                    success = status == "success"
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError(f"Tải mô hình {name} chưa hoàn tất; chạy lại để tiếp tục.") from exc
    if not success:
        raise ProviderError(f"Ollama chưa xác nhận tải {name} thành công.")


def prepare_local(data_dir):
    config = local_config()
    server = ensure_server(config, data_dir)
    print(f"Local server ready: {server}", flush=True)
    with httpx.Client(timeout=10, trust_env=False) as client:
        response = client.get(config.base_url + "/api/tags")
        response.raise_for_status()
        installed = {item["name"] for item in response.json().get("models", [])}
    for name in (config.embed_model, config.chat_model):
        canonical = name if ":" in name else name + ":latest"
        if canonical in installed:
            print(f"Model already available locally: {name}", flush=True)
        else:
            pull_model(config, name)
    return config

"""Load an explicitly requested local env file without evaluating its contents."""

import os
from pathlib import Path
import re

ALLOWED = {"FINLENS_PROVIDER", "FINLENS_CHAT_MODEL", "FINLENS_EMBED_MODEL", "FINLENS_DATA_DIR",
           "OLLAMA_BASE_URL", "GEMINI_API_KEY"}


def load_env_file(path: str | Path):
    source = Path(path)
    if not source.is_file():
        raise ValueError("Không tìm thấy tệp cấu hình. Sao chép config/gemini.env.example thành .env trước.")
    parsed = {}
    for number, line in enumerate(source.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not separator or name not in ALLOWED or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or name in parsed:
            raise ValueError(f"Tệp cấu hình có trường không hợp lệ hoặc lặp ở dòng {number}.")
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"Dấu nháy không hợp lệ ở dòng {number}.")
            value = value[1:-1]
        parsed[name] = value
    # Caller-supplied process environment takes precedence. Never print values.
    for name, value in parsed.items():
        os.environ.setdefault(name, value)
    return list(parsed)

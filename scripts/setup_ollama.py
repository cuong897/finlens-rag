"""Download a checksum-verified official Ollama portable runtime on Windows.

No PATH, registry, startup, user environment or cloud account changes.
The model files are downloaded later by the explicitly started local service.
"""

import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

import httpx

VERSION = "0.40.11"
URL = f"https://github.com/ollama/ollama/releases/download/v{VERSION}/ollama-windows-amd64.zip"
SHA256 = "bda4a493ba8a7c24d57263cd843cd68cee84f44aed4299eeed532acad7eda12a"
ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "ollama"


def setup():
    if sys.platform != "win32":
        raise SystemExit("Use the official Ollama installer for your operating system.")
    BASE.mkdir(parents=True, exist_ok=True)
    archive = BASE / f"ollama-{VERSION}-windows-amd64.zip"
    partial = archive.with_suffix(".zip.part")
    runtime = BASE / f"runtime-{VERSION}"
    marker = runtime / "finlens-runtime.json"
    if marker.is_file() and (runtime / "ollama.exe").is_file():
        print(f"Runtime ready: {runtime}", flush=True)
        return
    if shutil.disk_usage(BASE).free < 10 * 1024**3:
        raise SystemExit("Need at least 10 GiB free for runtime, archive and model files.")
    if not archive.exists():
        offset = partial.stat().st_size if partial.exists() else 0
        with httpx.Client(timeout=httpx.Timeout(60, read=120), follow_redirects=True, trust_env=False) as client:
            with client.stream("GET", URL, headers={"Range": f"bytes={offset}-"} if offset else {}) as response:
                response.raise_for_status()
                resume = bool(offset and response.status_code == 206)
                if not resume:
                    offset = 0
                total = int(response.headers.get("content-length", 0)) + offset
                last_report = offset
                with partial.open("ab" if resume else "wb") as destination:
                    for block in response.iter_bytes(1024 * 1024):
                        destination.write(block)
                        offset += len(block)
                        if offset - last_report >= 64 * 1024 * 1024:
                            print(f"Runtime download: {offset / 1024**2:.0f} / {total / 1024**2:.0f} MiB", flush=True)
                            last_report = offset
        partial.replace(archive)
    digest = hashlib.sha256()
    with archive.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != SHA256:
        raise SystemExit(f"Checksum mismatch; do not run this archive. Remove {archive} and retry.")
    print("Official release SHA-256 verified. Extracting portable runtime...", flush=True)
    runtime.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            target = (runtime / member.filename).resolve()
            if not target.is_relative_to(runtime.resolve()):
                raise SystemExit("Unsafe path in release archive.")
        package.extractall(runtime)
    marker.write_text(json.dumps({"version": VERSION, "source": URL, "sha256": SHA256}, indent=2), encoding="utf-8")
    print(f"Runtime ready: {runtime}", flush=True)


if __name__ == "__main__":
    setup()

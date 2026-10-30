"""Bounded public HTTPS PDF downloads with pinned, validated destinations."""

import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from finlens.ingest import IngestError, MAX_PDF_BYTES


def public_target(url):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise IngestError("Đường dẫn PDF không hợp lệ.")
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() != "https" or not parts.hostname or parts.username is not None or parts.password is not None or parts.port not in {None, 443}:
            raise ValueError
        host = parts.hostname.encode("idna").decode("ascii")
        if "%" in host or "\\" in host:
            raise ValueError
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(item[4][0] for item in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast
                          or ipaddress.ip_address(ip).is_reserved for ip in ips):
            raise ValueError
    except (ValueError, UnicodeError, OSError) as exc:
        raise IngestError("Chỉ nhận đường dẫn HTTPS công khai, không có tài khoản đăng nhập hoặc cổng riêng.") from exc
    authority = f"[{host}]" if ":" in host else host
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
    return urlunsplit(("https", authority, path, query, "")), host, ips[0], path + ("?" + query if query else "")


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        self.address = address
        self.tls_context = ssl.create_default_context()
        self.tls_context.set_alpn_protocols(["http/1.1"])
        super().__init__(host, timeout=timeout, context=self.tls_context)

    def connect(self):
        # Connect to the numeric address already checked, while TLS and Host
        # still identify the original hostname. No second hostname lookup.
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self.tls_context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def download_pdf(url):
    deadline = time.monotonic() + 40
    try:
        for redirect in range(4):
            final_url, host, address, target = public_target(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise IngestError("Tải PDF quá thời gian chờ. Hãy tải tệp về máy rồi nhập lại.")
            connection = PinnedHTTPSConnection(host, address, min(15, remaining))
            try:
                connection.request("GET", target, headers={"Accept": "application/pdf", "Accept-Encoding": "identity", "User-Agent": "FinLens/0.1 PDF-import"})
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location or redirect == 3:
                        raise IngestError("Đường dẫn chuyển hướng quá nhiều hoặc không hợp lệ.")
                    url = urljoin(final_url, location)
                    continue
                if response.status != 200:
                    raise IngestError(f"Không tải được báo cáo (HTTP {response.status}). Hãy dùng liên kết PDF trực tiếp.")
                if response.getheader("Content-Encoding", "identity").lower() not in {"", "identity"}:
                    raise IngestError("Máy chủ trả tệp nén không được hỗ trợ. Hãy tải PDF về máy rồi nhập.")
                length = response.getheader("Content-Length")
                if length and (not length.isdigit() or int(length) > MAX_PDF_BYTES):
                    raise IngestError("PDF vượt giới hạn 20 MB hoặc kích thước tải không hợp lệ.")
                content = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise IngestError("Tải PDF quá thời gian chờ. Hãy thử lại.")
                    if connection.sock:
                        connection.sock.settimeout(min(15, remaining))
                    block = response.read1(min(65536, MAX_PDF_BYTES + 1 - len(content)))
                    if not block:
                        break
                    content.extend(block)
                    if len(content) > MAX_PDF_BYTES:
                        raise IngestError("PDF vượt giới hạn 20 MB.")
                    if len(content) >= 5 and not content.startswith(b"%PDF-"):
                        raise IngestError("Liên kết trả về trang web hoặc tệp khác. Hãy dùng đường dẫn PDF trực tiếp.")
                if length and len(content) != int(length):
                    raise IngestError("Tệp tải chưa đầy đủ. Hãy thử lại hoặc tải PDF về máy.")
                if not content.startswith(b"%PDF-"):
                    raise IngestError("Liên kết không trả về PDF hợp lệ.")
                filename = unquote(urlsplit(final_url).path).replace("\\", "/").split("/")[-1]
                if not filename.lower().endswith(".pdf"):
                    filename = "bao-cao.pdf"
                return bytes(content), filename[:180], final_url, host
            finally:
                connection.close()
    except (OSError, http.client.HTTPException) as exc:
        raise IngestError("Không tải được PDF qua kết nối an toàn. Hãy thử lại hoặc tải tệp về máy.") from exc

"""Minimal Hikvision ISAPI client: digest/basic auth, device info and the alarm stream.

Only what VisionGuard needs:
    GET /ISAPI/System/deviceInfo                     -> reachability + credentials check
    GET /ISAPI/Event/notification/alertStream        -> long lived multipart alarm stream

`requests` is used for the streaming read (it ships with ultralytics). When it is missing the
client reports that clearly instead of crashing, exactly like the optional camera SDKs.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Iterator, Optional, Tuple

log = logging.getLogger("EVENT")

try:  # optional at import time, required for AI Camera mode
    import requests
    from requests.auth import HTTPBasicAuth, HTTPDigestAuth

    _REQUESTS_OK = True
    _REQUESTS_STATUS = "OK"
except Exception as _exc:  # pragma: no cover - only when requests is absent
    requests = None  # type: ignore
    _REQUESTS_OK = False
    _REQUESTS_STATUS = f"'requests' not installed ({type(_exc).__name__}) - pip install requests"

ALERT_STREAM_PATH = "/ISAPI/Event/notification/alertStream"
DEVICE_INFO_PATH = "/ISAPI/System/deviceInfo"
CAPABILITIES_PATH = "/ISAPI/System/capabilities"


def strip_namespace(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def short_error(exc: Exception, host: str = "") -> str:
    """One readable line instead of a urllib3 stack of nested messages."""
    name = type(exc).__name__
    text = str(exc)
    where = f" {host}" if host else ""
    if "ConnectTimeout" in name or "ConnectionError" in name or "MaxRetryError" in name:
        if "refused" in text.lower():
            return f"cannot reach{where} - connection refused (wrong port, or HTTP disabled)"
        if "timed out" in text.lower() or "timeout" in text.lower():
            return f"cannot reach{where} - no answer (check IP, cable, firewall)"
        return f"cannot reach{where} - network error"
    if "ReadTimeout" in name or "Timeout" in name:
        return f"{where.strip() or 'camera'} did not answer in time"
    if "SSLError" in name:
        return f"TLS error on{where} - try turning HTTPS off"
    # keep it to one short sentence, never the whole urllib3 chain
    text = text.split("(Caused by")[0].strip().rstrip(":")
    return f"{name}: {text[:120]}"


def mask_url(url: str) -> str:
    """Never let credentials reach the log or the UI."""
    if "@" in url and "//" in url:
        head, tail = url.split("//", 1)
        creds, rest = tail.split("@", 1)
        user = creds.split(":", 1)[0]
        return f"{head}//{user}:***@{rest}"
    return url


class IsapiError(Exception):
    pass


class IsapiClient:
    """One camera, one requests.Session, digest first then basic."""

    def __init__(self, base_url: str, username: str, password: str, timeout: float = 5.0,
                 verify_tls: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = float(timeout)
        self.verify_tls = verify_tls
        self._session = None
        self._auth_mode = "digest"
        self._stream = None

    # ------------------------------------------------------------------ availability
    @staticmethod
    def available() -> bool:
        return _REQUESTS_OK

    @staticmethod
    def status() -> str:
        return _REQUESTS_STATUS

    def url(self, path: str) -> str:
        return self.base_url + (path if path.startswith("/") else "/" + path)

    def _host(self) -> str:
        """host:port without scheme or credentials, for messages."""
        return self.base_url.split("//", 1)[-1]

    # ------------------------------------------------------------------ session
    def _ensure_session(self):
        if not _REQUESTS_OK:
            raise IsapiError(_REQUESTS_STATUS)
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": "VisionGuard/1.0", "Accept": "*/*"})
        return self._session

    def _auth(self):
        if self._auth_mode == "basic":
            return HTTPBasicAuth(self.username, self.password)
        return HTTPDigestAuth(self.username, self.password)

    def close(self) -> None:
        self.close_stream()
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    # ------------------------------------------------------------------ simple GET
    def get(self, path: str, timeout: Optional[float] = None) -> str:
        """GET an ISAPI resource and return the body as text. Raises IsapiError."""
        session = self._ensure_session()
        url = self.url(path)
        to = self.timeout if timeout is None else timeout
        for mode in ("digest", "basic"):
            if self._auth_mode != mode and mode == "basic":
                # only fall back to basic after a digest 401
                pass
            try:
                resp = session.get(url, auth=self._auth(), timeout=to, verify=self.verify_tls)
            except Exception as exc:
                raise IsapiError(short_error(exc, self._host())) from exc
            if resp.status_code == 401 and self._auth_mode == "digest":
                log.debug("ISAPI digest rejected, retrying with basic auth")
                self._auth_mode = "basic"
                continue
            if resp.status_code == 401:
                raise IsapiError("Authentication failed (wrong user name or password)")
            if resp.status_code == 403:
                raise IsapiError("Access denied (403) - the account may lack ISAPI/remote rights")
            if resp.status_code >= 400:
                raise IsapiError(f"HTTP {resp.status_code} on {path}")
            return resp.text
        raise IsapiError("Authentication failed")

    # ------------------------------------------------------------------ device info
    def device_info(self) -> Tuple[str, str, str]:
        """(model, serial, firmware) - also used as the reachability / health probe."""
        text = self.get(DEVICE_INFO_PATH)
        model = serial = firmware = ""
        try:
            root = ET.fromstring(text)
            for child in root:
                tag = strip_namespace(child.tag)
                if tag == "model":
                    model = (child.text or "").strip()
                elif tag == "serialNumber":
                    serial = (child.text or "").strip()
                elif tag == "firmwareVersion":
                    firmware = (child.text or "").strip()
        except ET.ParseError as exc:
            raise IsapiError(f"Unexpected deviceInfo payload: {exc}") from exc
        return model, serial, firmware

    def smart_capabilities(self) -> str:
        """Best effort: which smart events the camera supports (for the Test Event button)."""
        try:
            return self.get("/ISAPI/Smart/capabilities", timeout=self.timeout)
        except IsapiError:
            return ""

    # ------------------------------------------------------------------ alarm stream
    def open_stream(self, path: str = ALERT_STREAM_PATH, read_timeout: float = 2.0):
        """Open the long lived alert stream. Returns a byte iterator."""
        session = self._ensure_session()
        url = self.url(path)
        try:
            resp = session.get(url, auth=self._auth(), stream=True, verify=self.verify_tls,
                               timeout=(self.timeout, read_timeout))
        except Exception as exc:
            raise IsapiError(f"event stream: {short_error(exc, self._host())}") from exc
        if resp.status_code == 401 and self._auth_mode == "digest":
            resp.close()
            self._auth_mode = "basic"
            try:
                resp = session.get(url, auth=self._auth(), stream=True, verify=self.verify_tls,
                                   timeout=(self.timeout, read_timeout))
            except Exception as exc:
                raise IsapiError(f"event stream: {short_error(exc, self._host())}") from exc
        if resp.status_code >= 400:
            code = resp.status_code
            resp.close()
            if code == 401:
                raise IsapiError("Event stream authentication failed (user name / password)")
            if code == 403:
                raise IsapiError("Event stream denied (403) - enable ISAPI / give the account remote rights")
            if code == 404:
                raise IsapiError(f"Event stream path not found (404): {path}")
            raise IsapiError(f"Event stream HTTP {code}")
        self._stream = resp
        log.info("ISAPI event stream open: %s (%s auth)", mask_url(url), self._auth_mode)
        return resp

    def iter_chunks(self, chunk_size: int = 4096) -> Iterator[bytes]:
        """Yield raw bytes as they arrive; a read timeout yields b'' instead of raising."""
        if self._stream is None:
            return
        yield from self._stream.iter_content(chunk_size=chunk_size)

    def close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

    @property
    def stream_open(self) -> bool:
        return self._stream is not None

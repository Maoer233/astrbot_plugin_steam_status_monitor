import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.infrastructure.fonts.pack_service import (
    FontPackError,
    FontPackService,
    load_manifest,
)
from src.shared.fonts import configure_font_resolver, invalidate, load_truetype, resolve_font_path
from src.shared.paths import REQUIRED_FONT_FILES


REQUIRED = list(REQUIRED_FONT_FILES)


def _file_spec(name: str, payload: bytes) -> dict:
    return {"name": name, "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}


def _payloads() -> dict[str, bytes]:
    return {name: f"font-{name}".encode("utf-8") for name in REQUIRED}


def _write_manifest(path: Path, zip_bytes: bytes, files: list[dict], url: str = "https://example.com/fonts.zip") -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pack_version": "test-1",
                "zip": {
                    "url": url,
                    "sha256": hashlib.sha256(zip_bytes).hexdigest(),
                    "max_bytes": 1024 * 1024,
                },
                "files": files,
                "fallback_files": [],
            }
        ),
        encoding="utf-8",
    )


def _make_zip(payloads: dict[str, bytes], *, prefix: str = "", slip: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        if slip:
            zf.writestr(slip, b"evil")
        for name, data in payloads.items():
            zf.writestr(f"{prefix}{name}", data)
        zf.writestr("readme.txt", b"ignore me")
    return buffer.getvalue()


def _download_writer(zip_bytes: bytes):
    async def _write(self, url, dest, max_bytes):
        if len(zip_bytes) > max_bytes:
            raise FontPackError("字体包超过体积上限", retryable=False, kind="size")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as handle:
            handle.write(zip_bytes)
        self.downloaded = len(zip_bytes)
        self.total = len(zip_bytes)
    return _write


class ManifestTests(unittest.TestCase):
    def test_rejects_missing_and_invalid_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            with self.assertRaises(FontPackError):
                load_manifest(missing)
            bad = Path(tmp) / "bad.json"
            bad.write_text("{", encoding="utf-8")
            with self.assertRaises(FontPackError):
                load_manifest(bad)

    def test_rejects_non_https_and_bad_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            files = [_file_spec(name, b"x") for name in REQUIRED]
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "pack_version": "x",
                        "zip": {"url": "http://example.com/fonts.zip", "sha256": "ab", "max_bytes": 10},
                        "files": files,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(FontPackError):
                load_manifest(path)


class FontResolverTests(unittest.TestCase):
    def tearDown(self):
        invalidate()

    def test_prefers_bundled_then_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            data = Path(tmp) / "data"
            bundled.mkdir()
            (data / "fonts").mkdir(parents=True)
            target = REQUIRED[0]
            bundled_file = bundled / target
            bundled_file.write_bytes(b"bundled")
            (data / "fonts" / target).write_bytes(b"cached")
            configure_font_resolver(str(data), str(bundled))
            self.assertEqual(str(bundled_file), resolve_font_path(target))

    def test_load_truetype_does_not_raise_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            configure_font_resolver(str(Path(tmp) / "data"), str(Path(tmp) / "bundled"))
            font = load_truetype("definitely-missing.otf", 12)
            self.assertIsNotNone(font)


class FontPackServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        invalidate()

    def _service(self, tmp: str, manifest_path: str, bundled: str | None = None, enabled: bool = True) -> FontPackService:
        data_dir = os.path.join(tmp, "data")
        os.makedirs(data_dir, exist_ok=True)
        bundled_dir = bundled or os.path.join(tmp, "bundled")
        os.makedirs(bundled_dir, exist_ok=True)
        return FontPackService(
            data_dir,
            enabled=enabled,
            timeout_sec=5,
            bundled_dir=bundled_dir,
            manifest_path=manifest_path,
        )

    async def test_bundled_fonts_skip_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            bundled.mkdir()
            for name in REQUIRED:
                (bundled / name).write_bytes(b"x")
            service = self._service(tmp, os.path.join(tmp, "missing.json"), bundled=str(bundled))
            with patch.object(FontPackService, "_stream_download", new_callable=AsyncMock) as download:
                await service._install_once()
                download.assert_not_called()
            self.assertEqual("READY", service.status)

    async def test_local_complete_skips_http(self):
        payloads = _payloads()
        zip_bytes = _make_zip(payloads)
        files = [_file_spec(name, payloads[name]) for name in REQUIRED]
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            _write_manifest(manifest_path, zip_bytes, files)
            service = self._service(tmp, str(manifest_path))
            fonts_dir = Path(service.fonts_dir)
            fonts_dir.mkdir(parents=True, exist_ok=True)
            recorded = {}
            for item in files:
                (fonts_dir / item["name"]).write_bytes(payloads[item["name"]])
                recorded[item["name"]] = {"sha256": item["sha256"], "size": item["size"]}
            (fonts_dir / "manifest.local.json").write_text(
                json.dumps({"pack_version": "test-1", "files": recorded}),
                encoding="utf-8",
            )
            with patch.object(FontPackService, "_stream_download", new_callable=AsyncMock) as download:
                await service._install_once()
                download.assert_not_called()
            self.assertEqual("READY", service.status)

    async def test_downloads_and_installs_zip(self):
        payloads = _payloads()
        zip_bytes = _make_zip(payloads, prefix="fonts/")
        files = [_file_spec(name, payloads[name]) for name in REQUIRED]
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            _write_manifest(manifest_path, zip_bytes, files)
            service = self._service(tmp, str(manifest_path))
            with patch.object(FontPackService, "_stream_download", new=_download_writer(zip_bytes)):
                await service._install_once()
            self.assertEqual("READY", service.status)
            for name, data in payloads.items():
                installed = Path(service.fonts_dir) / name
                self.assertEqual(data, installed.read_bytes())
            self.assertFalse(os.path.exists(service.download_dir))
            resolved = resolve_font_path(REQUIRED[0])
            self.assertIsNotNone(resolved)
            self.assertEqual(REQUIRED[0], Path(resolved).name)

    async def test_bad_zip_hash_does_not_install(self):
        payloads = _payloads()
        zip_bytes = _make_zip(payloads)
        files = [_file_spec(name, payloads[name]) for name in REQUIRED]
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            _write_manifest(manifest_path, b"not-the-zip", files)
            service = self._service(tmp, str(manifest_path))
            with patch.object(FontPackService, "_stream_download", new=_download_writer(zip_bytes)):
                await service._install_once()
            self.assertIn(service.status, ("FAILED", "BACKOFF"))
            self.assertFalse(any((Path(service.fonts_dir) / name).exists() for name in REQUIRED))

    async def test_zip_slip_is_rejected(self):
        payloads = _payloads()
        zip_bytes = _make_zip(payloads, slip="../evil.otf")
        files = [_file_spec(name, payloads[name]) for name in REQUIRED]
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            _write_manifest(manifest_path, zip_bytes, files)
            service = self._service(tmp, str(manifest_path))
            with patch.object(FontPackService, "_stream_download", new=_download_writer(zip_bytes)):
                await service._install_once()
            self.assertIn(service.status, ("FAILED", "BACKOFF"))
            self.assertFalse((Path(tmp) / "evil.otf").exists())

    async def test_oversize_download_is_aborted(self):
        payloads = _payloads()
        zip_bytes = _make_zip(payloads)
        files = [_file_spec(name, payloads[name]) for name in REQUIRED]
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            _write_manifest(manifest_path, zip_bytes, files)
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            data["zip"]["max_bytes"] = 8
            manifest_path.write_text(json.dumps(data), encoding="utf-8")
            service = self._service(tmp, str(manifest_path))
            with patch.object(FontPackService, "_stream_download", new=_download_writer(zip_bytes)):
                await service._install_once()
            self.assertIn(service.status, ("FAILED", "BACKOFF"))
            self.assertFalse(os.path.exists(service.download_dir))
            self.assertFalse(any((Path(service.fonts_dir) / name).exists() for name in REQUIRED))

    async def test_cancel_cleans_download_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self._service(tmp, os.path.join(tmp, "missing.json"))
            os.makedirs(service.download_dir, exist_ok=True)
            (Path(service.download_dir) / "fonts.zip.part").write_bytes(b"partial")
            await service.aclose()
            self.assertFalse(os.path.exists(service.download_dir))

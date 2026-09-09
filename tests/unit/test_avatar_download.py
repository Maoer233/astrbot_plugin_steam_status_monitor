import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.presentation.renderers.game_start import get_avatar_path, get_horizontal_cover_path


class _FakeResponse:
    def __init__(self, status_code=200, content=b"img", payload=None):
        self.status_code = status_code
        self.content = content
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeClient:
    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.urls = []
        _FakeClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        self.urls.append(url)
        if "appdetails" in url:
            return _FakeResponse(payload={"10": {"data": {"header_image": "https://cdn.example/header.jpg"}}})
        return _FakeResponse(content=b"ok")


class AvatarDownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _FakeClient.instances = []

    async def test_get_avatar_path_downloads_with_async_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("src.presentation.renderers.game_start.httpx.AsyncClient", _FakeClient):
                path = await get_avatar_path(tmp, "s1", "https://cdn.example/avatar.jpg")
            self.assertTrue(Path(path).exists())
            self.assertEqual(Path(path).read_bytes(), b"ok")
            self.assertEqual(_FakeClient.instances[0].urls, ["https://cdn.example/avatar.jpg"])

    async def test_cached_avatar_skips_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            cached = Path(tmp) / "avatars"
            cached.mkdir()
            avatar = cached / "s1.jpg"
            avatar.write_bytes(b"cached")
            with patch("src.presentation.renderers.game_start.httpx.AsyncClient", _FakeClient):
                path = await get_avatar_path(tmp, "s1", "https://cdn.example/avatar.jpg")
            self.assertEqual(path, str(avatar))
            self.assertEqual(_FakeClient.instances, [])

    async def test_horizontal_cover_downloads_with_async_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("src.presentation.renderers.game_start.httpx.AsyncClient", _FakeClient):
                path = await get_horizontal_cover_path(tmp, "10", appid="10")
            self.assertTrue(Path(path).exists())
            self.assertEqual(_FakeClient.instances[0].urls[0].endswith("appids=10&l=schinese"), True)
            self.assertIn("https://cdn.example/header.jpg", _FakeClient.instances[0].urls)

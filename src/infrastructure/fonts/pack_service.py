"""后台下载、校验并安装 CJK 字体包。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

from ...shared.fonts import bundled_fonts_complete, configure_font_resolver, invalidate
from ...shared.logging import logger, redact_sensitive
from ...shared.network import httpx_client_kwargs
from ...shared.paths import FONT_MANIFEST_PATH, FONTS_DIR, REQUIRED_FONT_FILES

STATUS_MISSING = "MISSING"
STATUS_CHECKING = "CHECKING"
STATUS_READY = "READY"
STATUS_DOWNLOADING = "DOWNLOADING"
STATUS_BACKOFF = "BACKOFF"
STATUS_FAILED = "FAILED"
STATUS_DISABLED = "DISABLED"

STATUS_LABELS = {
    STATUS_MISSING: "未下载",
    STATUS_CHECKING: "检查中",
    STATUS_READY: "已就绪",
    STATUS_DOWNLOADING: "下载中",
    STATUS_BACKOFF: "等待重试",
    STATUS_FAILED: "失败",
    STATUS_DISABLED: "已关闭下载",
}

_BACKOFF_SECONDS = (60, 300, 900, 1800, 3600)
_MAX_NOT_FOUND_ATTEMPTS = 5
_MAX_HASH_FAILURES = 3
_PROGRESS_CHUNK = 2 * 1024 * 1024
_PROGRESS_INTERVAL = 2.0
_DEFAULT_TIMEOUT_SEC = 600
_CONNECT_TIMEOUT_SEC = 15.0
_GITHUB_MIRROR_PREFIXES = (
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
    "https://github.akams.cn/",
)

ProgressCallback = Callable[[dict], Awaitable[None] | None]


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _is_https(url: str) -> bool:
    return urlsplit(url).scheme.lower() == "https"


def _is_github_release_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        return False
    host = parts.netloc.lower()
    if host != "github.com" and not host.endswith(".github.com"):
        return False
    return "/releases/download/" in parts.path


def candidate_download_urls(url: str, *, use_mirrors: bool = True) -> list[str]:
    """GitHub Release 先走国内镜像，官方地址兜底。自定义非 GitHub 源不包装。"""
    url = (url or "").strip()
    if not url:
        return []
    if not use_mirrors or not _is_github_release_url(url):
        return [url]
    seen: set[str] = set()
    candidates: list[str] = []
    for prefix in _GITHUB_MIRROR_PREFIXES:
        mirrored = prefix.rstrip("/") + "/" + url
        if mirrored not in seen:
            seen.add(mirrored)
            candidates.append(mirrored)
    if url not in seen:
        candidates.append(url)
    return candidates


def _safe_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "计算中"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


def format_progress_bar(downloaded: int, total: int | None) -> str:
    if not total:
        mb = downloaded / (1024 * 1024)
        return f"已下载 {mb:.1f} MB"
    ratio = min(max(downloaded / total, 0.0), 1.0)
    filled = int(round(ratio * 20))
    bar = "▓" * filled + "░" * (20 - filled)
    percent = int(round(ratio * 100))
    return (
        f"{bar} {percent}% "
        f"({downloaded / (1024 * 1024):.1f} MB / {total / (1024 * 1024):.1f} MB)"
    )


@dataclass
class FontManifest:
    schema_version: int
    pack_version: str
    zip_url: str
    zip_sha256: str
    max_bytes: int
    files: list[dict]
    fallback_files: list[dict] = field(default_factory=list)

    @property
    def file_map(self) -> dict[str, dict]:
        return {item["name"]: item for item in self.files}

    @property
    def required_names(self) -> list[str]:
        return [item["name"] for item in self.files]


class FontPackError(Exception):
    def __init__(self, message: str, *, retryable: bool = True, kind: str = "error"):
        super().__init__(message)
        self.retryable = retryable
        self.kind = kind


def load_manifest(path: str | os.PathLike | None = None) -> FontManifest:
    manifest_path = Path(path or FONT_MANIFEST_PATH)
    if not manifest_path.is_file():
        raise FontPackError(f"字体清单不存在: {manifest_path}", retryable=False, kind="manifest")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise FontPackError(f"字体清单无法解析: {exc}", retryable=False, kind="manifest") from exc
    zip_info = raw.get("zip") or {}
    files = raw.get("files") or []
    if not isinstance(files, list) or not files:
        raise FontPackError("字体清单缺少 files", retryable=False, kind="manifest")
    url = str(zip_info.get("url") or "")
    sha256 = str(zip_info.get("sha256") or "").lower()
    if not _is_https(url):
        raise FontPackError("字体包 URL 必须是 https", retryable=False, kind="manifest")
    if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256):
        raise FontPackError("字体包 SHA256 无效", retryable=False, kind="manifest")
    names = []
    for item in files:
        name = str(item.get("name") or "")
        digest = str(item.get("sha256") or "").lower()
        size = int(item.get("size") or 0)
        if not name or size <= 0 or len(digest) != 64:
            raise FontPackError(f"字体清单文件项无效: {name}", retryable=False, kind="manifest")
        names.append(name)
    missing = [name for name in REQUIRED_FONT_FILES if name not in names]
    if missing:
        raise FontPackError(f"字体清单缺少必需文件: {', '.join(missing)}", retryable=False, kind="manifest")
    return FontManifest(
        schema_version=int(raw.get("schema_version") or 1),
        pack_version=str(raw.get("pack_version") or ""),
        zip_url=url,
        zip_sha256=sha256,
        max_bytes=int(zip_info.get("max_bytes") or 80 * 1024 * 1024),
        files=[{"name": item["name"], "sha256": str(item["sha256"]).lower(), "size": int(item["size"])} for item in files],
        fallback_files=list(raw.get("fallback_files") or []),
    )


class FontPackService:
    def __init__(
        self,
        data_dir: str,
        *,
        proxy: str | None = None,
        enabled: bool = True,
        pack_url: str = "",
        timeout_sec: int = _DEFAULT_TIMEOUT_SEC,
        bundled_dir: str | None = None,
        manifest_path: str | None = None,
    ):
        self.data_dir = data_dir
        self.fonts_dir = os.path.join(data_dir, "fonts")
        self.download_dir = os.path.join(self.fonts_dir, ".download")
        self.local_manifest_path = os.path.join(self.fonts_dir, "manifest.local.json")
        self.proxy = proxy
        self.enabled = enabled
        self.pack_url_override = (pack_url or "").strip()
        self.timeout_sec = max(int(timeout_sec or _DEFAULT_TIMEOUT_SEC), 10)
        self.bundled_dir = bundled_dir or str(FONTS_DIR)
        self.manifest_path = manifest_path or str(FONT_MANIFEST_PATH)
        self.status = STATUS_MISSING
        self.error = ""
        self.pack_version = ""
        self.next_retry_at = 0.0
        self.downloaded = 0
        self.total = 0
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._retry_index = 0
        self._not_found_count = 0
        self._hash_fail_count = 0
        self._stop_until_manual = False
        self._progress_cb: ProgressCallback | None = None
        self._speed_samples: list[tuple[float, int]] = []
        os.makedirs(self.fonts_dir, exist_ok=True)
        configure_font_resolver(data_dir, self.bundled_dir)

    def snapshot(self) -> dict:
        ready = self._ready_file_count()
        return {
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "pack_version": self.pack_version,
            "ready_files": ready,
            "total_files": len(REQUIRED_FONT_FILES),
            "error": self.error,
            "next_retry_at": self.next_retry_at,
            "downloaded": self.downloaded,
            "total": self.total,
            "enabled": self.enabled,
        }

    def format_status_text(self) -> str:
        snap = self.snapshot()
        lines = [
            f"字体状态：{snap['status_label']}",
            f"资源版本：{snap['pack_version'] or '未知'}",
            f"已就绪文件：{snap['ready_files']}/{snap['total_files']}",
        ]
        if snap["status"] == STATUS_DOWNLOADING and snap["total"]:
            lines.append(format_progress_bar(snap["downloaded"], snap["total"]))
        if snap["error"]:
            lines.append(f"原因：{snap['error']}")
        if snap["status"] == STATUS_BACKOFF and snap["next_retry_at"]:
            remain = max(int(snap["next_retry_at"] - time.time()), 0)
            lines.append(f"下次重试：{_format_eta(remain)}")
        return "\n".join(lines)

    def ensure_ready(self) -> asyncio.Task:
        if self._task and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self._run_loop(), name="font-pack-ensure")
        return self._task

    async def download_now(self, progress_cb: ProgressCallback | None = None) -> str:
        if not self.enabled:
            self.status = STATUS_DISABLED
            return "字体下载已关闭，仅使用本地或系统字体。"
        if self.status == STATUS_READY and self._local_complete():
            return "字体已就绪，无需下载。"
        self._stop_until_manual = False
        self._retry_index = 0
        self.next_retry_at = 0
        self._progress_cb = progress_cb
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        await self._install_once(force=True)
        self._progress_cb = None
        if self.status == STATUS_READY:
            return self.format_status_text()
        self.ensure_ready()
        return self.format_status_text()

    def clean(self) -> str:
        removed = []
        for name in REQUIRED_FONT_FILES:
            path = os.path.join(self.fonts_dir, name)
            if os.path.isfile(path):
                os.remove(path)
                removed.append(name)
        if os.path.isfile(self.local_manifest_path):
            os.remove(self.local_manifest_path)
            removed.append("manifest.local.json")
        if os.path.isdir(self.download_dir):
            shutil.rmtree(self.download_dir, ignore_errors=True)
            removed.append(".download/")
        invalidate()
        self.status = STATUS_MISSING
        self.error = ""
        self.pack_version = ""
        return "已清理字体缓存：" + ("、".join(removed) if removed else "无需清理")

    async def aclose(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._cleanup_download_dir()

    async def _run_loop(self) -> None:
        try:
            while True:
                if self._stop_until_manual:
                    self.status = STATUS_FAILED
                    return
                await self._install_once()
                if self.status == STATUS_READY:
                    return
                if self.status == STATUS_DISABLED:
                    return
                delay = _BACKOFF_SECONDS[min(self._retry_index, len(_BACKOFF_SECONDS) - 1)]
                self._retry_index += 1
                self.status = STATUS_BACKOFF
                self.next_retry_at = time.time() + delay
                logger.warning("[Font] 字体下载失败，%s 后重试：%s", _format_eta(delay), self.error)
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self._cleanup_download_dir()
            raise

    async def _install_once(self, force: bool = False) -> None:
        async with self._lock:
            await self._install_once_locked(force=force)

    async def _install_once_locked(self, force: bool = False) -> None:
        self.status = STATUS_CHECKING
        self.error = ""
        try:
            if bundled_fonts_complete(self.bundled_dir):
                self.status = STATUS_READY
                self.pack_version = "bundled"
                invalidate()
                logger.info("[Font] 使用仓库内置字体，跳过下载")
                return
            if not force and self._local_complete():
                self.status = STATUS_READY
                invalidate()
                logger.info("[Font] 数据目录字体已就绪，跳过下载")
                return
            if not self.enabled:
                self.status = STATUS_DISABLED
                self.error = "已关闭字体下载"
                return
            manifest = load_manifest(self.manifest_path)
            self.pack_version = manifest.pack_version
            url = self.pack_url_override or manifest.zip_url
            if self.pack_url_override and not _is_https(self.pack_url_override):
                raise FontPackError("自定义字体包 URL 必须是 https", retryable=False, kind="config")
            self._check_disk_space(manifest)
            await self._download_and_install(
                manifest,
                url,
                use_mirrors=not bool(self.pack_url_override) and not bool(self.proxy),
            )
            self.status = STATUS_READY
            self.error = ""
            self._retry_index = 0
            self._not_found_count = 0
            self._hash_fail_count = 0
            invalidate()
            logger.info("[Font] 字体包安装完成 version=%s", manifest.pack_version)
        except asyncio.CancelledError:
            self._cleanup_download_dir()
            raise
        except FontPackError as exc:
            self.error = str(exc)
            self._handle_failure(exc)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.status = STATUS_FAILED
            logger.warning("[Font] 字体安装异常: %s", self.error)

    def _handle_failure(self, exc: FontPackError) -> None:
        if exc.kind == "http_not_found":
            self._not_found_count += 1
            if self._not_found_count >= _MAX_NOT_FOUND_ATTEMPTS:
                self._stop_until_manual = True
        if exc.kind == "hash":
            self._hash_fail_count += 1
            if self._hash_fail_count >= _MAX_HASH_FAILURES:
                self._stop_until_manual = True
        if not exc.retryable:
            self._stop_until_manual = True
        self.status = STATUS_FAILED if self._stop_until_manual else STATUS_BACKOFF
        logger.warning("[Font] %s", redact_sensitive(str(exc)))

    def _local_complete(self) -> bool:
        try:
            manifest = load_manifest(self.manifest_path)
        except FontPackError:
            return self._files_exist()
        self.pack_version = manifest.pack_version
        local = self._read_local_manifest()
        if not local or local.get("pack_version") != manifest.pack_version:
            return False
        files = local.get("files") or {}
        for item in manifest.files:
            name = item["name"]
            path = os.path.join(self.fonts_dir, name)
            if not os.path.isfile(path):
                return False
            if os.path.getsize(path) != item["size"]:
                return False
            recorded = files.get(name) or {}
            if int(recorded.get("size") or 0) != item["size"]:
                return False
            if str(recorded.get("sha256") or "").lower() != item["sha256"]:
                return False
        return True

    def _files_exist(self) -> bool:
        return all(os.path.isfile(os.path.join(self.fonts_dir, name)) for name in REQUIRED_FONT_FILES)

    def _ready_file_count(self) -> int:
        count = 0
        for name in REQUIRED_FONT_FILES:
            bundled = os.path.join(self.bundled_dir, name)
            cached = os.path.join(self.fonts_dir, name)
            if os.path.isfile(bundled) or os.path.isfile(cached):
                count += 1
        return count

    def _read_local_manifest(self) -> dict:
        if not os.path.isfile(self.local_manifest_path):
            return {}
        try:
            with open(self.local_manifest_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return {}

    def _write_local_manifest(self, manifest: FontManifest) -> None:
        payload = {
            "pack_version": manifest.pack_version,
            "installed_at": int(time.time()),
            "files": {
                item["name"]: {"sha256": item["sha256"], "size": item["size"]}
                for item in manifest.files
            },
        }
        tmp = self.local_manifest_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, self.local_manifest_path)

    def _check_disk_space(self, manifest: FontManifest) -> None:
        files_size = sum(item["size"] for item in manifest.files)
        needed = manifest.max_bytes + files_size + 16 * 1024 * 1024
        try:
            usage = shutil.disk_usage(self.fonts_dir)
        except OSError as exc:
            raise FontPackError(f"无法检查磁盘空间: {exc}", retryable=False, kind="disk") from exc
        if usage.free < needed:
            raise FontPackError(
                f"磁盘空间不足，需要约 {needed / (1024 * 1024):.0f} MB",
                retryable=False,
                kind="disk",
            )

    async def _download_and_install(
        self,
        manifest: FontManifest,
        url: str,
        *,
        use_mirrors: bool = True,
    ) -> None:
        self.status = STATUS_DOWNLOADING
        os.makedirs(self.download_dir, exist_ok=True)
        part_path = os.path.join(self.download_dir, "fonts.zip.part")
        extract_dir = os.path.join(self.download_dir, "extract")
        candidates = candidate_download_urls(url, use_mirrors=use_mirrors)
        if not candidates:
            raise FontPackError("没有可用的字体包下载地址", retryable=False, kind="config")
        last_error: Exception | None = None
        try:
            for index, candidate in enumerate(candidates):
                self.downloaded = 0
                self.total = 0
                self._speed_samples = [(time.monotonic(), 0)]
                if os.path.isdir(extract_dir):
                    shutil.rmtree(extract_dir, ignore_errors=True)
                os.makedirs(extract_dir, exist_ok=True)
                if os.path.isfile(part_path):
                    os.remove(part_path)
                try:
                    logger.info(
                        "[Font] 开始下载字体包 (%s/%s) %s",
                        index + 1,
                        len(candidates),
                        _safe_url(candidate),
                    )
                    await self._stream_download(candidate, part_path, manifest.max_bytes)
                    digest = _sha256_file(part_path)
                    if digest != manifest.zip_sha256:
                        raise FontPackError("字体包 SHA256 校验失败", retryable=True, kind="hash")
                    self._extract_zip(part_path, extract_dir)
                    installed = self._collect_extracted_files(extract_dir, manifest)
                    self._verify_extracted(installed, manifest)
                    self._commit_files(installed, manifest)
                    self._write_local_manifest(manifest)
                    return
                except asyncio.CancelledError:
                    raise
                except FontPackError as exc:
                    last_error = exc
                    logger.warning(
                        "[Font] 字体包源失败 %s：%s",
                        _safe_url(candidate),
                        redact_sensitive(str(exc)),
                    )
                    if exc.kind in ("disk", "config", "manifest"):
                        raise
                    continue
            if last_error:
                raise last_error
            raise FontPackError("没有可用的字体包下载地址", retryable=False, kind="config")
        finally:
            self._cleanup_download_dir()

    async def _stream_download(self, url: str, dest: str, max_bytes: int) -> None:
        import httpx

        timeout = httpx.Timeout(
            connect=_CONNECT_TIMEOUT_SEC,
            read=self.timeout_sec,
            write=self.timeout_sec,
            pool=_CONNECT_TIMEOUT_SEC,
        )
        last_progress = 0.0
        last_bytes = 0
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                **httpx_client_kwargs(self.proxy),
            ) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code in (404, 410):
                        raise FontPackError(
                            f"字体包不存在 (HTTP {response.status_code})",
                            retryable=True,
                            kind="http_not_found",
                        )
                    if response.status_code >= 400:
                        retryable = response.status_code in (408, 425, 429) or response.status_code >= 500
                        raise FontPackError(
                            f"下载字体包失败 (HTTP {response.status_code})",
                            retryable=retryable,
                            kind="http",
                        )
                    total = response.headers.get("Content-Length")
                    self.total = int(total) if total and total.isdigit() else 0
                    if self.total and self.total > max_bytes:
                        raise FontPackError("字体包超过体积上限", retryable=False, kind="size")
                    with open(dest, "wb") as handle:
                        async for chunk in response.aiter_bytes(64 * 1024):
                            self.downloaded += len(chunk)
                            if self.downloaded > max_bytes:
                                raise FontPackError("字体包超过体积上限", retryable=False, kind="size")
                            handle.write(chunk)
                            now = time.monotonic()
                            if (
                                self.downloaded - last_bytes >= _PROGRESS_CHUNK
                                or now - last_progress >= _PROGRESS_INTERVAL
                            ):
                                last_bytes = self.downloaded
                                last_progress = now
                                await self._emit_progress()
            await self._emit_progress(force=True)
        except FontPackError:
            if os.path.isfile(dest):
                os.remove(dest)
            raise
        except httpx.HTTPError as exc:
            if os.path.isfile(dest):
                os.remove(dest)
            raise FontPackError(f"下载字体包网络错误: {type(exc).__name__}", retryable=True, kind="http") from exc

    async def _emit_progress(self, force: bool = False) -> None:
        now = time.monotonic()
        self._speed_samples.append((now, self.downloaded))
        cutoff = now - 10
        self._speed_samples = [item for item in self._speed_samples if item[0] >= cutoff] or self._speed_samples[-2:]
        eta = None
        if self.total and len(self._speed_samples) >= 2:
            t0, b0 = self._speed_samples[0]
            elapsed = now - t0
            gained = self.downloaded - b0
            if elapsed > 0 and gained > 0:
                speed = gained / elapsed
                remain = max(self.total - self.downloaded, 0)
                eta = remain / speed if speed else None
        payload = {
            "downloaded": self.downloaded,
            "total": self.total,
            "eta": eta,
            "bar": format_progress_bar(self.downloaded, self.total),
            "eta_text": _format_eta(eta),
            "force": force,
        }
        if not self._progress_cb:
            return
        try:
            result = self._progress_cb(payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.warning("[Font] 更新下载进度失败: %s", exc)

    def _extract_zip(self, zip_path: str, extract_dir: str) -> None:
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for info in zf.infolist():
                    name = info.filename.replace("\\", "/")
                    if name.endswith("/") or info.is_dir():
                        continue
                    parts = Path(name).parts
                    if (
                        name.startswith("/")
                        or name.startswith("\\")
                        or ".." in parts
                        or ".." in name.split("/")
                    ):
                        raise FontPackError("字体包包含非法路径", retryable=False, kind="zip")
                    target = Path(extract_dir).resolve() / Path(name).name
                    extract_root = str(Path(extract_dir).resolve())
                    if not str(target).startswith(extract_root + os.sep) and str(target) != extract_root:
                        raise FontPackError("字体包包含非法路径", retryable=False, kind="zip")
                    with zf.open(info) as src, open(target, "wb") as dest:
                        shutil.copyfileobj(src, dest)
        except FontPackError:
            raise
        except zipfile.BadZipFile as exc:
            raise FontPackError("字体包不是有效 zip", retryable=True, kind="zip") from exc

    def _collect_extracted_files(self, extract_dir: str, manifest: FontManifest) -> dict[str, str]:
        found: dict[str, str] = {}
        wanted = {name.lower(): name for name in manifest.required_names}
        for root, _dirs, files in os.walk(extract_dir):
            for filename in files:
                canonical = wanted.get(filename.lower())
                if not canonical:
                    continue
                found[canonical] = os.path.join(root, filename)
        missing = [name for name in manifest.required_names if name not in found]
        if missing:
            raise FontPackError(f"字体包缺少文件: {', '.join(missing)}", retryable=True, kind="zip")
        return found

    def _verify_extracted(self, installed: dict[str, str], manifest: FontManifest) -> None:
        for item in manifest.files:
            path = installed[item["name"]]
            size = os.path.getsize(path)
            if size != item["size"]:
                raise FontPackError(f"{item['name']} 大小不匹配", retryable=True, kind="hash")
            digest = _sha256_file(path)
            if digest != item["sha256"]:
                raise FontPackError(f"{item['name']} SHA256 校验失败", retryable=True, kind="hash")

    def _commit_files(self, installed: dict[str, str], manifest: FontManifest) -> None:
        os.makedirs(self.fonts_dir, exist_ok=True)
        for item in manifest.files:
            src = installed[item["name"]]
            dest = os.path.join(self.fonts_dir, item["name"])
            tmp = dest + ".tmp"
            shutil.copyfile(src, tmp)
            os.replace(tmp, dest)

    def _cleanup_download_dir(self) -> None:
        if os.path.isdir(self.download_dir):
            shutil.rmtree(self.download_dir, ignore_errors=True)

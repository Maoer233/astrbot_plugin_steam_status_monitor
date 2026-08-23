"""QQ 官方机器人指令面板 API 客户端。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from ...shared.network import httpx_client_kwargs


class QQOfficialPanelError(RuntimeError):
    """QQ 指令面板接口错误。"""

    def __init__(self, message: str, *, code: int | None = None, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code

    @property
    def panel_not_found(self) -> bool:
        return self.code == 40030006 or self.status_code == 404


class QQOfficialPanelClient:
    """管理一个 QQ 官方机器人应用下的指令面板。"""

    TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
    API_BASE_URL = "https://api.sgroup.qq.com"

    def __init__(
        self,
        appid: str,
        secret: str,
        *,
        proxy: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.appid = str(appid).strip()
        self.secret = str(secret).strip()
        self.proxy = proxy
        self.timeout = timeout
        self._access_token = ""
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def _get_access_token(self, *, force_refresh: bool = False) -> str:
        now = time.monotonic()
        if not force_refresh and self._access_token and now < self._token_expires_at:
            return self._access_token
        async with self._token_lock:
            now = time.monotonic()
            if not force_refresh and self._access_token and now < self._token_expires_at:
                return self._access_token
            async with httpx.AsyncClient(
                timeout=self.timeout,
                **httpx_client_kwargs(self.proxy),
            ) as client:
                response = await client.post(
                    self.TOKEN_URL,
                    json={"appId": self.appid, "clientSecret": self.secret},
                )
            data = self._decode_response(response)
            token = str(data.get("access_token") or "").strip()
            if not token:
                raise QQOfficialPanelError(
                    "QQ AccessToken 响应缺少 access_token",
                    status_code=response.status_code,
                )
            try:
                expires_in = max(60, int(data.get("expires_in", 7200)))
            except (TypeError, ValueError):
                expires_in = 7200
            self._access_token = token
            self._token_expires_at = time.monotonic() + max(30, expires_in - 60)
            return token

    @staticmethod
    def _decode_response(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json() if response.content else {}
        except ValueError as exc:
            raise QQOfficialPanelError(
                f"QQ API 返回了无法解析的响应（HTTP {response.status_code}）",
                status_code=response.status_code,
            ) from exc
        if response.is_success:
            return data if isinstance(data, dict) else {}
        code = data.get("code") if isinstance(data, dict) else None
        message = ""
        if isinstance(data, dict):
            message = str(data.get("message") or data.get("msg") or "").strip()
        safe_message = message or "请求失败"
        raise QQOfficialPanelError(
            f"QQ API {safe_message}（HTTP {response.status_code}，code={code}）",
            code=code if isinstance(code, int) else None,
            status_code=response.status_code,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for attempt in range(2):
            token = await self._get_access_token(force_refresh=attempt > 0)
            headers = {
                "Authorization": f"QQBot {token}",
                "X-Union-Appid": self.appid,
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(
                base_url=self.API_BASE_URL,
                timeout=self.timeout,
                **httpx_client_kwargs(self.proxy),
            ) as client:
                response = await client.request(method, path, headers=headers, json=json_body)
            if response.status_code != 401 or attempt > 0:
                return self._decode_response(response)
            self._access_token = ""
            self._token_expires_at = 0.0
        raise QQOfficialPanelError("QQ API 鉴权失败")

    async def create_panel(
        self,
        *,
        scope: str,
        panel: dict[str, Any],
        group_openids: list[str] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "scope": scope,
            "target_type": "specific" if scope == "group" else "all",
            "panel": panel,
        }
        if scope == "group":
            payload["group_openids"] = group_openids or []
        data = await self._request("POST", "/v2/panels", json_body=payload)
        panel_id = str(data.get("panel_id") or "").strip()
        if not panel_id:
            raise QQOfficialPanelError("创建成功响应缺少 panel_id")
        return panel_id

    async def update_panel(self, panel_id: str, panel: dict[str, Any]) -> int | None:
        current = await self.get_panel(panel_id)
        current_panel = current.get("panel") if isinstance(current.get("panel"), dict) else {}
        version = current_panel.get("version")
        panel_with_version = dict(panel)
        if isinstance(version, int):
            panel_with_version["version"] = version
        data = await self._request(
            "PUT",
            f"/v2/panels/{panel_id}",
            json_body={"panel": panel_with_version},
        )
        new_version = data.get("version")
        return new_version if isinstance(new_version, int) else None

    async def update_targets(self, panel_id: str, group_openids: list[str]) -> None:
        await self._request(
            "PUT",
            f"/v2/panels/{panel_id}/target",
            json_body={
                "op": "add",
                "group_openids": group_openids,
            },
        )

    async def get_panel(self, panel_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v2/panels/{panel_id}")

    async def delete_panel(self, panel_id: str) -> None:
        await self._request("DELETE", f"/v2/panels/{panel_id}")

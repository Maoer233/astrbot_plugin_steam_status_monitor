from __future__ import annotations

import httpx
import pytest

from src.infrastructure.clients.qqofficial_panel import (
    QQOfficialPanelClient,
    QQOfficialPanelError,
)


@pytest.mark.asyncio
async def test_create_group_panel_uses_official_payload(monkeypatch):
    client = QQOfficialPanelClient("app-id", "secret")
    captured = {}

    async def fake_request(method, path, *, json_body=None):
        captured.update(method=method, path=path, json_body=json_body)
        return {"panel_id": "panel-1"}

    monkeypatch.setattr(client, "_request", fake_request)
    panel = {"items": [{"name": "/steam help", "type": "command"}], "remark": "Steam"}

    panel_id = await client.create_panel(
        scope="group",
        panel=panel,
        group_openids=["group-openid"],
    )

    assert panel_id == "panel-1"
    assert captured == {
        "method": "POST",
        "path": "/v2/panels",
        "json_body": {
            "scope": "group",
            "target_type": "specific",
            "group_openids": ["group-openid"],
            "panel": panel,
        },
    }


@pytest.mark.asyncio
async def test_update_panel_carries_current_version(monkeypatch):
    client = QQOfficialPanelClient("app-id", "secret")
    captured = {}

    async def fake_get_panel(panel_id):
        assert panel_id == "panel-1"
        return {"panel": {"version": 4}}

    async def fake_request(method, path, *, json_body=None):
        captured.update(method=method, path=path, json_body=json_body)
        return {"version": 5}

    monkeypatch.setattr(client, "get_panel", fake_get_panel)
    monkeypatch.setattr(client, "_request", fake_request)

    version = await client.update_panel("panel-1", {"items": [], "remark": "Steam"})

    assert version == 5
    assert captured["json_body"]["panel"]["version"] == 4


@pytest.mark.asyncio
async def test_update_targets_uses_add_operation(monkeypatch):
    client = QQOfficialPanelClient("app-id", "secret")
    captured = {}

    async def fake_request(method, path, *, json_body=None):
        captured.update(method=method, path=path, json_body=json_body)
        return {}

    monkeypatch.setattr(client, "_request", fake_request)

    await client.update_targets("panel-1", ["group-openid"])

    assert captured == {
        "method": "PUT",
        "path": "/v2/panels/panel-1/target",
        "json_body": {
            "op": "add",
            "group_openids": ["group-openid"],
        },
    }


def test_panel_not_found_error_recognizes_official_code():
    assert QQOfficialPanelError("missing", code=40030006).panel_not_found
    assert QQOfficialPanelError("missing", status_code=404).panel_not_found
    assert not QQOfficialPanelError("other", code=40030005).panel_not_found


def test_decode_response_does_not_expose_credentials():
    response = httpx.Response(
        401,
        json={"code": 11241, "message": "invalid token"},
        request=httpx.Request("GET", "https://api.sgroup.qq.com/v2/panels/x"),
    )

    with pytest.raises(QQOfficialPanelError) as exc_info:
        QQOfficialPanelClient._decode_response(response)

    assert exc_info.value.code == 11241
    assert "invalid token" in str(exc_info.value)

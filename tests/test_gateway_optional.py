from __future__ import annotations

from pathlib import Path

import pytest

import coral.gateway.server as gateway_server


def test_gateway_start_explains_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        gateway_server,
        "_LITELLM_IMPORT_ERROR",
        ModuleNotFoundError("No module named 'litellm'", name="litellm"),
    )
    monkeypatch.setattr(gateway_server, "litellm_app", None)
    monkeypatch.setattr(gateway_server, "initialize", None)

    manager = gateway_server.GatewayManager(
        port=40199,
        config_path=str(tmp_path / "litellm_config.yaml"),
    )

    with pytest.raises(RuntimeError, match=r"coral\[gateway\]"):
        manager.start()

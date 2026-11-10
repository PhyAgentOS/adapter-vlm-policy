import json

import httpx
import pytest

from adapter_vlm_policy.backend import CompatibleBackend
from adapter_vlm_policy.config import ModelRoute, Settings
from adapter_vlm_policy.errors import ServiceError


@pytest.mark.parametrize(
    "model_env,effort_env,explicit,expected",
    [
        (None, None, "", ("configured-model", "high")),
        ("env-model", "low", "", ("env-model", "low")),
        (
            "env-model",
            "low",
            "    model: route-model\n    reasoning_effort: none\n",
            ("route-model", "none"),
        ),
        (
            None,
            None,
            "    model: ${VLM_MODEL}\n    reasoning_effort: ${VLM_REASONING_EFFORT}\n",
            ("configured-model", "high"),
        ),
    ],
)
def test_provider_model_effort_and_override_precedence(
    tmp_path, monkeypatch, model_env, effort_env, explicit, expected
):
    for key, value in [("VLM_MODEL", model_env), ("VLM_REASONING_EFFORT", effort_env)]:
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    monkeypatch.delenv("VLM_API_KEY", raising=False)
    monkeypatch.delenv("VLM_API_BASE", raising=False)
    provider = tmp_path / "provider"
    provider.write_text(
        "base_url=https://provider.example/v1\n"
        "api_key=test-credential\n"
        "  export model_name='configured-model' # preserved selection\n"
        "model_effort=high\n"
    )
    config = tmp_path / "service.yaml"
    config.write_text(
        "model_routes:\n  default:\n    provider_file: provider\n"
        "    base_url: ${VLM_API_BASE}\n" + explicit
    )
    route = Settings.load(config).routes["default"]
    assert (route.model, route.reasoning_effort) == expected
    assert route.api_key == "test-credential"
    assert route.base_url == "https://provider.example/v1"
    assert "test-credential" not in repr(route)


def test_provider_standard_field_aliases(tmp_path, monkeypatch):
    monkeypatch.delenv("VLM_MODEL", raising=False)
    monkeypatch.delenv("VLM_REASONING_EFFORT", raising=False)
    (tmp_path / "provider").write_text(
        "base_url=https://provider.example/v1\nmodel=other-model\nreasoning_effort=medium\n"
    )
    (tmp_path / "service.yaml").write_text(
        "model_routes:\n  default:\n    provider_file: provider\n"
    )
    route = Settings.load(tmp_path / "service.yaml").routes["default"]
    assert (route.model, route.reasoning_effort) == ("other-model", "medium")


@pytest.mark.parametrize("effort", ["high", "none", None])
async def test_backend_transmits_configured_effort(effort):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "resolved-model",
                "choices": [{"message": {"content": "42"}, "finish_reason": "stop"}],
            },
        )

    backend = CompatibleBackend()
    await backend.client.aclose()
    backend.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await backend.complete(
            ModelRoute("configured-model", "https://provider.example/v1", reasoning_effort=effort),
            [{"role": "user", "content": "17+25"}],
            100,
        )
    finally:
        await backend.close()
    assert sent[0]["model"] == "configured-model"
    if effort is None:
        assert "reasoning_effort" not in sent[0]
    else:
        assert sent[0]["reasoning_effort"] == effort
    assert result.text == "42"


async def test_reasoning_only_truncated_response_is_not_a_malformed_reply():
    def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": None}, "finish_reason": "length"}]},
        )

    backend = CompatibleBackend()
    await backend.client.aclose()
    backend.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ServiceError, match="truncated"):
            await backend.complete(
                ModelRoute("configured-model", "https://provider.example/v1"),
                [{"role": "user", "content": "plan a task"}],
                100,
            )
    finally:
        await backend.close()

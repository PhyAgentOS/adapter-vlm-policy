import asyncio
import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from adapter_vlm_policy.api import create_app
from adapter_vlm_policy.backend import Completion
from adapter_vlm_policy.config import ModelRoute, Settings
from adapter_vlm_policy.errors import ServiceError
from adapter_vlm_policy.models import InferenceRequest
from adapter_vlm_policy.service import InferenceService
from adapter_vlm_policy.store import Store


class Backend:
    def __init__(self, answers=None, gate=None):
        self.answers = list(answers or ["hello"])
        self.messages = []
        self.gate = gate

    async def complete(self, route, messages, max_tokens, schema=None):
        self.messages.append(messages)
        if self.gate:
            await self.gate.wait()
        text = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return Completion(text, "test-model", {"input_tokens": 10, "output_tokens": 3})


@pytest.fixture
async def setup(tmp_path):
    services = []

    def make(backend=None, **kwargs):
        cfg = Settings(
            data_dir=tmp_path / "data",
            allowed_file_roots=[tmp_path],
            routes={"default": ModelRoute("test-model", "http://unused")},
            **kwargs,
        )
        svc = InferenceService(cfg, backend or Backend())
        services.append(svc)
        return svc

    yield make
    for svc in services:
        await svc.close()


def request(id="r1", context=None, text="hello", **extra):
    raw = {"request_id": id, "messages": [{"role": "user", "content": text}], **extra}
    if context:
        raw["context"] = context
    return InferenceRequest.model_validate(raw)


def task(task_id="t1", version=0, context_id=None, **extra):
    return {
        "mode": "task",
        "task_id": task_id,
        "expected_version": version,
        **({"context_id": context_id} if context_id else {}),
        **extra,
    }


async def invoke(svc, req, namespace="a"):
    await svc.submit(namespace, req)
    return await svc.wait(namespace, req.request_id)


def image_block(path=None, media_id=None, color="red"):
    if path:
        source = {"type": "file", "path": str(path)}
    elif media_id:
        source = {"type": "context_media", "media_id": media_id}
    else:
        b = io.BytesIO()
        Image.new("RGB", (20, 20), color).save(b, format="PNG")
        source = {
            "type": "inline_base64",
            "mime_type": "image/png",
            "data": base64.b64encode(b.getvalue()).decode(),
        }
    return {"type": "image", "image_id": color, "source": source}


async def test_stateless_conversation_and_idempotency(setup):
    backend = Backend()
    svc = setup(backend)
    result = await invoke(svc, request())
    assert result["output"]["text"] == "hello"
    repeat = await svc.submit("a", request())
    assert repeat["idempotent"] and len(backend.messages) == 1
    assert (await svc.wait("a", "r1")) == result
    with pytest.raises(ServiceError, match="different payload"):
        await svc.submit("a", request(text="changed"))


async def test_recognition_then_review_uses_pinned_before_and_new_after(setup, tmp_path):
    backend = Backend(['{"candidate":"cup-1"}', '{"verdict":"unknown"}'])
    svc = setup(backend)
    path = tmp_path / "frame.png"
    Image.new("RGB", (20, 20), "red").save(path)
    schema = {
        "type": "object",
        "properties": {"candidate": {"type": "string"}},
        "required": ["candidate"],
    }
    req = request(
        context=task(updates={"goal": "place cup", "constraints": ["do not disturb neighbor"]}),
        messages=[
            {"role": "system", "content": "RECOGNITION_ONLY"},
            {"role": "user", "content": [image_block(path=path)]},
        ],
        response_format={"type": "json_schema", "schema": schema},
    )
    first = await invoke(svc, req)
    ctx = first["context"]["context_id"]
    old = first["media"][0]
    Image.new("RGB", (20, 20), "blue").save(path)
    second = request(
        "r2",
        context=task(
            version=1,
            context_id=ctx,
            selection={"record_ids": [first["output"]["record_id"]]},
            updates={"execution_report": {"source": "tool", "status": "succeeded"}},
        ),
        messages=[
            {"role": "system", "content": "VERIFY_ONLY"},
            {
                "role": "user",
                "content": [
                    image_block(media_id=old["media_id"]),
                    image_block(path=path, color="blue"),
                ],
            },
        ],
        response_format={
            "type": "json_schema",
            "schema": {"type": "object", "required": ["verdict"]},
        },
    )
    result = await invoke(svc, second)
    assert result["context"]["version"] == 2
    assert result["media"][0]["sha256"] == old["sha256"] != result["media"][1]["sha256"]
    assembled = backend.messages[1]
    assert "RECOGNITION_ONLY" not in json.dumps(assembled)
    assert "VERIFY_ONLY" in json.dumps(assembled)
    urls = [
        b["image_url"]["url"]
        for m in assembled
        if isinstance(m["content"], list)
        for b in m["content"]
        if b["type"] == "image_url"
    ]
    colors = [
        Image.open(io.BytesIO(base64.b64decode(u.split(",")[1]))).getpixel((0, 0)) for u in urls
    ]
    assert colors == [(255, 0, 0), (0, 0, 255)]
    state = svc.store.context("a", ctx)
    assert "candidate" not in state["data"]  # Model output was not silently adopted as fact.
    assert state["data"]["execution_report"]["source"] == "tool"


async def test_task_does_not_autoload_history_or_images(setup):
    backend = Backend()
    svc = setup(backend)
    first = await invoke(
        svc,
        request(context=task(), messages=[{"role": "user", "content": [image_block()]}]),
    )
    await invoke(
        svc,
        request("r2", context=task(version=1, context_id=first["context"]["context_id"])),
    )
    assert not any("image_url" in json.dumps(m) for m in backend.messages[1])
    assert "SELECTED HISTORICAL" not in json.dumps(backend.messages[1])


async def test_isolation_and_context_version_conflict(setup):
    svc = setup()
    one = await invoke(svc, request(context=task(updates={"private": "a"})))
    ctx = one["context"]["context_id"]
    for req, ns in [
        (request("r2", context=task(version=1, context_id=ctx)), "b"),
        (
            request("r3", context=task(task_id="different", version=1, context_id=ctx)),
            "a",
        ),
        (request("r4", context=task(version=0, context_id=ctx)), "a"),
    ]:
        with pytest.raises(ServiceError):
            await svc.submit(ns, req)
    other = await invoke(svc, request("new-task", context=task(task_id="t2")))
    assert "private" not in svc.store.context("a", other["context"]["context_id"])["data"]


async def test_cross_task_media_and_record_references_rejected(setup):
    svc = setup()
    first = await invoke(
        svc,
        request(context=task(), messages=[{"role": "user", "content": [image_block()]}]),
    )
    with pytest.raises(ServiceError, match="Media not found"):
        await svc.submit(
            "a",
            request(
                "r2",
                context=task(task_id="t2"),
                messages=[
                    {
                        "role": "user",
                        "content": [image_block(media_id=first["media"][0]["media_id"])],
                    }
                ],
            ),
        )
    other = await invoke(svc, request("r3", context=task(task_id="t3")))
    with pytest.raises(ServiceError, match="Selected record"):
        await svc.submit(
            "a",
            request(
                "r4",
                context=task(
                    task_id="t3",
                    version=1,
                    context_id=other["context"]["context_id"],
                    selection={"record_ids": [first["output"]["record_id"]]},
                ),
            ),
        )


async def test_cancel_is_terminal_and_does_not_commit_context(setup):
    gate = asyncio.Event()
    svc = setup(Backend(gate=gate))
    accepted = await svc.submit("a", request(context=task(updates={"not_committed": True})))
    await asyncio.sleep(0.01)
    with pytest.raises(ServiceError, match="Another inference"):
        await svc.submit("a", request("r2", context=task(context_id=accepted["context_id"])))
    await svc.cancel("a", "r1")
    gate.set()
    await asyncio.sleep(0.01)
    result = await svc.wait("a", "r1")
    assert result["execution_status"] == "cancelled"
    assert svc.store.context("a", accepted["context_id"])["version"] == 0
    assert svc.store.context("a", accepted["context_id"])["data"] == {}


async def test_deadline_includes_queue_and_leaves_version_unchanged(setup):
    gate = asyncio.Event()
    svc = setup(Backend(gate=gate), max_concurrency=1)
    await svc.submit("a", request())
    accepted = await svc.submit("a", request("r2", context=task(), limits={"deadline_ms": 10}))
    result = await svc.wait("a", "r2")
    assert result["execution_status"] == "timed_out"
    assert svc.store.context("a", accepted["context_id"])["version"] == 0
    await svc.cancel("a", "r1")


async def test_invalid_output_repairs_bounded_then_failure(setup):
    backend = Backend(["bad", '{"wrong":1}'])
    svc = setup(backend)
    result = await invoke(
        svc,
        request(
            context=task(updates={"goal": "uncommitted"}),
            response_format={
                "type": "json_schema",
                "schema": {"type": "object", "required": ["ok"]},
            },
        ),
    )
    assert result["error"]["code"] == "INVALID_OUTPUT" and len(backend.messages) == 2
    assert svc.store.context("a", result["context"]["context_id"])["data"] == {}


async def test_schema_repair_success_and_usage(setup):
    svc = setup(Backend(["bad", '{"ok":true}']))
    result = await invoke(
        svc,
        request(
            response_format={
                "type": "json_schema",
                "schema": {"type": "object", "required": ["ok"]},
            }
        ),
    )
    assert result["output"]["json"] == {"ok": True}
    assert result["provenance"]["repair_attempts"] == 1
    assert result["usage"]["input_tokens"] == 20


async def test_paths_mime_and_schema_references_checked(setup, tmp_path):
    svc = setup()
    for block in [
        image_block(path=Path("/etc/passwd")),
        {
            "type": "image",
            "image_id": "bad",
            "source": {"type": "inline_base64", "data": "not base64"},
        },
    ]:
        with pytest.raises(ServiceError):
            await svc.submit("a", request(messages=[{"role": "user", "content": [block]}]))
    with pytest.raises(ServiceError, match="External schema"):
        await svc.submit(
            "a",
            request(
                response_format={
                    "type": "json_schema",
                    "schema": {"$ref": "https://example.com/schema"},
                }
            ),
        )


async def test_file_change_conflicts_with_same_request_id(setup, tmp_path):
    svc = setup()
    p = tmp_path / "x.png"
    Image.new("RGB", (2, 2), "red").save(p)
    req = request(messages=[{"role": "user", "content": [image_block(path=p)]}])
    await invoke(svc, req)
    Image.new("RGB", (2, 2), "blue").save(p)
    with pytest.raises(ServiceError, match="different payload"):
        await svc.submit("a", req)


async def test_media_tampering_not_silently_accepted(setup):
    svc = setup()
    first = await invoke(
        svc,
        request(context=task(), messages=[{"role": "user", "content": [image_block()]}]),
    )
    ctx = first["context"]["context_id"]
    media = first["media"][0]
    stored = svc.store.media("a", ctx, media["media_id"])
    Path(stored["path"]).write_bytes(b"bad")
    with pytest.raises(ServiceError, match="missing or changed"):
        await svc.submit(
            "a",
            request(
                "r2",
                context=task(version=1, context_id=ctx),
                messages=[
                    {
                        "role": "user",
                        "content": [image_block(media_id=media["media_id"])],
                    }
                ],
            ),
        )


async def test_close_context_and_context_budget(setup):
    svc = setup()
    first = await invoke(svc, request(context=task()))
    ctx = first["context"]["context_id"]
    svc.store.close_context("a", ctx, 1)
    with pytest.raises(ServiceError, match="closed"):
        await svc.submit("a", request("r2", context=task(version=1, context_id=ctx)))
    svc.settings.routes["default"].context_tokens = 10
    result = await invoke(svc, request("r3"))
    assert result["error"]["code"] == "CONTEXT_TOO_LARGE"


async def test_queue_limit_and_deduplication_while_busy(setup):
    svc = setup(Backend(gate=asyncio.Event()), max_pending=1)
    await svc.submit("a", request())
    assert (await svc.submit("a", request()))["idempotent"]
    with pytest.raises(ServiceError, match="queue is full"):
        await svc.submit("a", request("r2"))
    await svc.cancel("a", "r1")


async def test_http_auth_and_errors_do_not_echo_image_or_credential(setup):
    svc = setup()
    svc.settings.clients = {"test-token": "a", "other-token": "b"}
    app = create_app(svc.settings, svc)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/v1/capabilities")).status_code == 401
        a = await client.post(
            "/v1/infer:wait",
            json=request().model_dump(by_alias=True),
            headers={"Authorization": "Bearer test-token"},
        )
        assert a.json()["output"]["text"] == "hello"
        b = await client.get("/v1/requests/r1", headers={"Authorization": "Bearer other-token"})
        assert b.status_code == 404
        bad = await client.post(
            "/v1/infer",
            json={"request_id": "x", "messages": "secret-input"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert bad.status_code == 422 and "secret-input" not in bad.text


async def test_service_restart_preserves_task_and_pinned_media(tmp_path):
    cfg = Settings(
        data_dir=tmp_path / "data",
        routes={"default": ModelRoute("test", "http://unused")},
    )
    first = InferenceService(cfg, Backend())
    result = await invoke(
        first,
        request(context=task(), messages=[{"role": "user", "content": [image_block()]}]),
    )
    ctx = result["context"]["context_id"]
    media = result["media"][0]["media_id"]
    await first.close()
    second = InferenceService(cfg, Backend())
    assert second.store.context("a", ctx)["version"] == 1
    assert second.store.media("a", ctx, media)["width"] == 20
    repeat = await second.submit(
        "a",
        request(context=task(), messages=[{"role": "user", "content": [image_block()]}]),
    )
    assert repeat["idempotent"]
    await second.close()


def test_interrupted_request_not_committed_on_restart(tmp_path):
    root = tmp_path / "data"
    store = Store(root, 86400)
    req = request(context=task(updates={"goal": "not committed"}))
    ctx = store.context_for("a", req)
    store.admit("a", req, ctx, {}, "hash")
    store.close()
    restarted = Store(root, 86400)
    assert restarted.request("a", "r1")["result"]["error"]["code"] == "SERVICE_RESTARTED"
    assert restarted.context("a", ctx["id"])["data"] == {}
    assert not restarted.context("a", ctx["id"])["busy"]
    restarted.close()


async def test_cancel_before_worker_starts_still_has_terminal_result(setup):
    svc = setup(Backend(gate=asyncio.Event()))
    await svc.submit("a", request())
    await svc.cancel("a", "r1")
    assert (await svc.wait("a", "r1"))["execution_status"] == "cancelled"


async def test_repair_snapshot_records_actual_second_prompt(setup):
    svc = setup(Backend(["bad", '{"ok":true}']))
    await invoke(
        svc,
        request(
            response_format={
                "type": "json_schema",
                "schema": {"type": "object", "required": ["ok"]},
            }
        ),
    )
    snapshot = svc.store.snapshot("a", "r1")
    assert len(snapshot["backend_calls"]) == 2
    assert snapshot["backend_calls"][1]["messages"][-2]["content"] == "bad"
    assert (
        "failed JSON/schema validation" in snapshot["backend_calls"][1]["messages"][-1]["content"]
    )


async def test_expiry_reclaims_task_text_media_and_blocks_recovery(setup):
    svc = setup()
    r = await invoke(
        svc,
        request(
            context=task(updates={"private": "do not retain forever"}),
            messages=[{"role": "user", "content": [image_block()]}],
        ),
    )
    ctx = r["context"]["context_id"]
    media = r["media"][0]["media_id"]
    path = Path(svc.store.media("a", ctx, media)["path"])
    svc.store.db.execute("UPDATE contexts SET updated=0 WHERE id=?", (ctx,))
    collected = svc.store.collect_expired()
    assert collected["deleted_media"] == 1 and not path.exists()
    row = svc.store.db.execute("SELECT data FROM contexts WHERE id=?", (ctx,)).fetchone()
    assert row["data"] == "{}"
    with pytest.raises(ServiceError, match="expired"):
        svc.store.request("a", "r1")
    with pytest.raises(ServiceError, match="expired"):
        svc.store.context("a", ctx)


def test_two_services_cannot_own_same_store(tmp_path):
    store = Store(tmp_path, 86400)
    with pytest.raises(ServiceError, match="Another service"):
        Store(tmp_path, 86400)
    store.close()
    other = Store(tmp_path, 86400)
    other.close()

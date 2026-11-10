"""Optional Forge control-plane adapter; inference service has no sensor/Dora ports."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid

import httpx


def run(
    url,
    token_env="VLM_SERVICE_TOKEN",
    service_config=None,
    profile="vlm",
    spawn_command=None,
):
    from dora import Node
    from forge_tool import (
        TOOL_ENDPOINT_PROTOCOL,
        ToolAccepted,
        ToolControlResponse,
        ToolEndpointDescriptor,
        ToolEndpointError,
        ToolEndpointHandler,
        ToolError,
        ToolEvent,
        ToolExecutionStatus,
        ToolOperationDescriptor,
        ToolResult,
        ToolResultResponse,
        endpoint_registry_response_from_payload,
        make_registration_envelope,
        make_unregister_envelope,
        validate_management_response_correlation,
    )

    from ._vendor.dora_binding import (
        DoraToolEndpointBinding,
        tool_envelope_to_message,
        tool_message_to_envelope,
    )
    from ._vendor.tool_message import ToolMessage

    child = None
    if service_config:
        spawn_command = [
            sys.executable,
            "-m",
            "adapter_vlm_policy.cli",
            "serve",
            "--config",
            service_config,
            "--port",
            str(httpx.URL(url).port or 80),
        ]
    if spawn_command:
        child = subprocess.Popen(spawn_command)
    try:
        for _ in range(200):
            if child and child.poll() is not None:
                raise RuntimeError("Managed service exited before ready")
            try:
                if httpx.get(url + "/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Managed service readiness timeout")
        endpoint_id = "reasoning.vlm" if profile == "vlm" else "sim.tabletop"
        query_operations = ["capabilities"] if profile == "vlm" else ["observe"]
        action_operations = ["infer"] if profile == "vlm" else ["prepare", "execute"]
        descriptor = ToolEndpointDescriptor(
            protocol_version=TOOL_ENDPOINT_PROTOCOL,
            endpoint_id=endpoint_id,
            operations=tuple(
                [
                    ToolOperationDescriptor(name=n, semantics="query", max_concurrency=4)
                    for n in query_operations
                ]
                + [
                    ToolOperationDescriptor(
                        name=n,
                        semantics="action",
                        cancellable=True,
                        status_supported=True,
                        max_concurrency=4 if profile == "vlm" else 1,
                    )
                    for n in action_operations
                ]
            ),
        )
        outgoing = queue.SimpleQueue()
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        headers = (
            {"Authorization": "Bearer " + os.environ[token_env]}
            if os.environ.get(token_env)
            else {}
        )
        client = httpx.AsyncClient(base_url=url, headers=headers, timeout=200)
        executions = {}
        watchers = set()

        async def http_error(response):
            if response.status_code >= 400:
                try:
                    error = response.json().get("error", {})
                except ValueError:
                    error = {}
                raise ToolEndpointError(
                    ToolError(
                        code=error.get("code", "SERVICE_ERROR"),
                        message=error.get("message", "Service request failed"),
                        retryable=False,
                    )
                )

        class Query:
            def __init__(self, operation):
                self.operation = operation

            async def query(self, request, context):
                response = await client.get(
                    "/v1/capabilities" if profile == "vlm" else "/v1/observe"
                )
                await http_error(response)
                return ToolResult(status="succeeded", outputs=response.json())

        def terminal_result(result):
            status = result["execution_status"]
            if status == "succeeded":
                return ToolResult(status="succeeded", outputs=result)
            if status == "cancelled":
                return ToolResult(status="cancelled", outputs=result)
            error = result.get("error", {})
            return ToolResult(
                status="failed",
                outputs=result,
                error=ToolError(
                    code=error.get("code", "INFERENCE_FAILED"),
                    message=error.get("message", "Inference failed"),
                    retryable=False,
                ),
            )

        async def monitor(key, request_id, emitter):
            try:
                response = await client.get(f"/v1/requests/{request_id}/result")
                await http_error(response)
                result = terminal_result(response.json())
                executions[key]["result"] = result
                event_type = {
                    "succeeded": "executor_completed",
                    "cancelled": "cancelled",
                }.get(result.status, "executor_failed")
                await emitter.emit(ToolEvent(type=event_type, data={"request_id": request_id}))
            except asyncio.CancelledError:
                raise
            except Exception:
                executions[key]["result"] = ToolResult(
                    status="unknown",
                    error=ToolError(
                        code="SERVICE_TRANSPORT_UNKNOWN",
                        message="Result transport failed; reconcile service request before retrying",
                        retryable=False,
                    ),
                )
                await emitter.emit(ToolEvent(type="executor_failed"))

        class Action:
            def __init__(self, operation):
                self.operation = operation

            async def start(self, request, context, events):
                key = context.execution_key
                payload = dict(request.arguments)
                payload.setdefault(
                    "request_id",
                    "forge-" + context.invocation_id + "-" + context.attempt_id,
                )
                if profile != "vlm":
                    payload["operation"] = self.operation
                signature = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                if key in executions:
                    if executions[key]["signature"] != signature:
                        raise ToolEndpointError(
                            ToolError(
                                code="REQUEST_CONFLICT",
                                message="Execution arguments changed",
                                retryable=False,
                            )
                        )
                    await executions[key]["admitted"].wait()
                    if executions[key].get("admission_error"):
                        raise executions[key]["admission_error"]
                    return ToolAccepted(
                        details={
                            "request_id": executions[key]["request_id"],
                            "idempotent": True,
                        }
                    )
                if context.deadline_ms is not None and profile == "vlm":
                    remaining = context.deadline_ms - int(time.time() * 1000)
                    if remaining <= 0:
                        raise ToolEndpointError(
                            ToolError(
                                code="DEADLINE_EXCEEDED",
                                message="Invocation deadline expired",
                                retryable=False,
                            )
                        )
                    payload.setdefault("limits", {})
                    payload["limits"]["deadline_ms"] = min(
                        remaining, payload["limits"].get("deadline_ms", 60000)
                    )
                # Reserve before the first await: status/result can arrive while
                # the service admission HTTP response is in flight.
                record = {
                    "request_id": payload["request_id"],
                    "signature": signature,
                    "result": None,
                    "admitted": asyncio.Event(),
                }
                executions[key] = record
                try:
                    response = await client.post(
                        "/v1/infer" if profile == "vlm" else "/v1/actions", json=payload
                    )
                    await http_error(response)
                    req_id = response.json()["request_id"]
                except Exception as exc:
                    record["admission_error"] = exc
                    record["result"] = ToolResult(
                        status="failed",
                        error=ToolError(
                            code="SERVICE_ADMISSION_FAILED",
                            message="Service rejected admission",
                            retryable=False,
                        ),
                    )
                    record["admitted"].set()
                    raise
                record["request_id"] = req_id
                record["admitted"].set()
                watch = asyncio.create_task(monitor(key, req_id, events))
                watchers.add(watch)
                watch.add_done_callback(watchers.discard)
                return ToolAccepted(details={"request_id": req_id, "idempotent": False})

            async def status(self, key):
                record = executions.get(key)
                if record is None:
                    return ToolExecutionStatus(
                        phase="unknown",
                        error=ToolError(code="REQUEST_NOT_FOUND", message="Unknown execution"),
                    )
                result = record["result"]
                if result is None:
                    return ToolExecutionStatus(
                        phase="running", details={"request_id": record["request_id"]}
                    )
                phase = {
                    "succeeded": "completed",
                    "cancelled": "cancelled",
                    "unknown": "unknown",
                }.get(result.status, "failed")
                return ToolExecutionStatus(
                    phase=phase,
                    error=result.error,
                    details={"request_id": record["request_id"]},
                )

            async def result(self, key):
                record = executions.get(key)
                if record is None:
                    return ToolResultResponse(status="not_found")
                if record["result"] is None:
                    return ToolResultResponse(status="pending")
                return ToolResultResponse(status="available", result=record["result"])

            async def cancel(self, key, reason=None):
                record = executions.get(key)
                if record is None:
                    return ToolControlResponse(
                        command="cancel",
                        status="rejected",
                        error=ToolError(code="REQUEST_NOT_FOUND", message="Unknown execution"),
                    )
                if record["result"] is not None:
                    return ToolControlResponse(command="cancel", status="terminal")
                await record["admitted"].wait()
                if record["result"] is not None:
                    return ToolControlResponse(command="cancel", status="terminal")
                response = await client.post(f"/v1/requests/{record['request_id']}/cancel")
                await http_error(response)
                return ToolControlResponse(command="cancel", status="accepted")

        instance = "adapter-vlm-policy-" + str(uuid.uuid4())
        handler = ToolEndpointHandler(
            descriptor,
            endpoint_instance_id=instance,
            operations={
                **{n: Query(n) for n in query_operations},
                **{n: Action(n) for n in action_operations},
            },
        )

        async def send(batch):
            outgoing.put(batch)

        binding = DoraToolEndpointBinding(handler, event_sink=send)
        node = Node()
        pending = None
        renew_at = 0
        response_deadline = 0
        registered = False

        def emit(envelope):
            node.send_output("tool_out", tool_envelope_to_message(envelope).to_arrow())

        async def dispatch(value):
            for batch in await binding.dispatch_input(value):
                outgoing.put(batch)

        def check_future(future):
            try:
                future.result()
            except Exception as exc:
                print("bridge dispatch error:", type(exc).__name__, flush=True)

        for event in node:
            now = time.monotonic()
            if event["type"] in ("STOP", "ERROR"):
                break
            if pending is not None and now >= response_deadline:
                pending = None
                renew_at = now + 0.1
            if pending is None and now >= renew_at:
                pending = make_registration_envelope(
                    descriptor,
                    endpoint_instance_id=instance,
                    request_id=str(uuid.uuid4()),
                )
                emit(pending)
                response_deadline = now + 1
            if event["type"] == "INPUT" and event.get("id") == "tool_in":
                value = event["value"]
                message = ToolMessage.from_arrow(value)
                envelope = tool_message_to_envelope(message)
                if envelope.message_type == "endpoint.registry.response":
                    if pending and envelope.request_id == pending.request_id:
                        validate_management_response_correlation(pending, envelope)
                        decision = endpoint_registry_response_from_payload(envelope.payload)
                        if decision.status == "accepted":
                            renew_at = now + decision.lease_ttl_ms / 3000
                            registered = True
                        else:
                            renew_at = now + 0.5
                        pending = None
                else:
                    f = asyncio.run_coroutine_threadsafe(dispatch(value), loop)
                    f.add_done_callback(check_future)
            while not outgoing.empty():
                node.send_output("tool_out", outgoing.get())
        if registered:
            emit(
                make_unregister_envelope(
                    endpoint_id=endpoint_id,
                    endpoint_instance_id=instance,
                    request_id=str(uuid.uuid4()),
                )
            )

        async def shutdown():
            # Request cancellation before dropping the control adapter.
            for record in executions.values():
                if record["result"] is None:
                    try:
                        await client.post(f"/v1/requests/{record['request_id']}/cancel")
                    except httpx.HTTPError:
                        pass
            for watch in list(watchers):
                watch.cancel()
            await asyncio.gather(*watchers, return_exceptions=True)
            await client.aclose()

        asyncio.run_coroutine_threadsafe(shutdown(), loop).result(timeout=10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
    finally:
        if child is not None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()

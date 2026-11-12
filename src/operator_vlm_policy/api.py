from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import Settings
from .errors import ServiceError
from .models import InferenceRequest
from .service import InferenceService


class BodyLimit:
    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        size = 0
        # Buffer only up to the bounded limit; authenticate routes never get unbounded bodies.
        events = []
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            size += len(event.get("body", b""))
            if size > self.limit:
                response = JSONResponse(
                    {
                        "error": {
                            "code": "REQUEST_TOO_LARGE",
                            "message": "Request exceeds configured size",
                        }
                    },
                    status_code=413,
                )
                return await response(scope, receive, send)
            events.append(event)
            if not event.get("more_body", False):
                break

        async def replay():
            if events:
                return events.pop(0)
            return await receive()

        return await self.app(scope, replay, send)


def create_app(settings: Settings, service: InferenceService | None = None):
    inference = service or InferenceService(settings)

    @asynccontextmanager
    async def lifespan(app):
        async def cleanup():
            while True:
                await asyncio.sleep(60)
                await asyncio.to_thread(inference.store.collect_expired)

        task = asyncio.create_task(cleanup())
        yield
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await inference.close()

    app = FastAPI(title="operator-vlm-policy", version="0.1.2", lifespan=lifespan)
    app.state.service = inference
    app.add_middleware(BodyLimit, limit=settings.max_request_bytes)

    def identity(request: Request):
        if not settings.clients:
            return "local"
        auth = request.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else ""
        for configured, namespace in settings.clients.items():
            if hmac.compare_digest(token, configured):
                return namespace
        raise HTTPException(401, "Invalid service credential")

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({"error": exc.to_dict()}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic errors normally include original input, which may contain images/secrets.
        errors = [{"loc": e["loc"], "message": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(
            {
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Request validation failed",
                    "fields": errors,
                }
            },
            status_code=422,
        )

    @app.get("/health")
    async def health():
        return {"status": "ok", "component": "operator-vlm-policy"}

    @app.get("/v1/capabilities")
    async def capabilities(namespace=Depends(identity)):
        return inference.capabilities()

    @app.post("/v1/infer", status_code=202)
    async def submit(req: InferenceRequest, namespace=Depends(identity)):
        return await inference.submit(namespace, req)

    @app.post("/v1/infer:wait")
    async def infer_wait(req: InferenceRequest, namespace=Depends(identity)):
        await inference.submit(namespace, req)
        return await inference.wait(namespace, req.request_id)

    @app.get("/v1/requests/{request_id}")
    async def status(request_id: str, namespace=Depends(identity)):
        return inference.store.request(namespace, request_id)

    @app.get("/v1/requests/{request_id}/result")
    async def result(request_id: str, namespace=Depends(identity)):
        return await inference.wait(namespace, request_id)

    @app.get("/v1/requests/{request_id}/snapshot")
    async def snapshot(request_id: str, namespace=Depends(identity)):
        return inference.store.snapshot(namespace, request_id)

    @app.post("/v1/requests/{request_id}/cancel")
    async def cancel(request_id: str, namespace=Depends(identity)):
        return await inference.cancel(namespace, request_id)

    @app.get("/v1/contexts/{context_id}")
    async def context(context_id: str, namespace=Depends(identity)):
        return inference.store.context(namespace, context_id)

    @app.post("/v1/contexts/{context_id}/close")
    async def close_context(context_id: str, body: dict, namespace=Depends(identity)):
        version = body.get("expected_version")
        if not isinstance(version, int):
            raise ServiceError("INVALID_REQUEST", "expected_version is required")
        return inference.store.close_context(namespace, context_id, version)

    return app

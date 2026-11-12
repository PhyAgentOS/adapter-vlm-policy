"""MuJoCo test executor; separate from operator-vlm-policy inference."""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from operator_vlm_policy.errors import ServiceError
from operator_vlm_policy.mujoco_scene import PRIMITIVES, TabletopScene
from operator_vlm_policy.store import digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--port", type=int, default=19121)
    args = parser.parse_args()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mujoco")
    scene = pool.submit(TabletopScene, Path(args.scene), Path(args.output)).result()
    frames = {}
    requests = {}
    active = None

    async def work(fn, *args):
        return await asyncio.get_running_loop().run_in_executor(pool, fn, *args)

    @asynccontextmanager
    async def life(app):
        yield
        scene.cancel_event.set()
        tasks = [r["task"] for r in requests.values() if not r["task"].done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await work(scene.close)
        pool.shutdown()

    app = FastAPI(lifespan=life)

    @app.exception_handler(ServiceError)
    async def error(request, exc):
        return JSONResponse({"error": exc.to_dict()}, status_code=exc.status)

    @app.get("/health")
    async def health():
        return {"status": "ok", "component": "mujoco-tabletop-executor"}

    def public(frame):
        return {k: v for k, v in frame.items() if k not in ("depth", "segmentation")}

    @app.get("/v1/observe")
    async def observe():
        frame = await work(scene.capture, "agent-observation")
        frames[frame["observation_id"]] = frame
        return {
            **public(frame),
            "image_size_wh": [960, 720],
            "available_primitives": PRIMITIVES,
        }

    @app.post("/v1/actions", status_code=202)
    async def submit(payload: dict):
        nonlocal active
        req_id = payload.get("request_id")
        operation = payload.get("operation")
        sha = digest(payload)
        if not isinstance(req_id, str) or not req_id or operation not in ("prepare", "execute"):
            raise ServiceError("INVALID_REQUEST", "request_id and operation are required")
        if req_id in requests:
            if requests[req_id]["hash"] != sha:
                raise ServiceError("REQUEST_CONFLICT", "Request payload changed", 409)
            return {
                "request_id": req_id,
                "status": requests[req_id]["status"],
                "idempotent": True,
            }
        if active is not None:
            raise ServiceError("BUSY", "Simulation has an active action", 409)
        if operation == "prepare":
            observation = frames.get(payload.get("observation_id"))
            if observation is None:
                raise ServiceError("STALE_OBSERVATION", "Observation not found", 409)
            if observation["sim_time_s"] != float(scene.data.time):
                raise ServiceError("STALE_OBSERVATION", "Simulation changed since observation", 409)
            for key in ("grasp_point_uv_px", "destination_point_uv_px"):
                point = payload.get(key)
                if not isinstance(point, list) or len(point) != 2:
                    raise ServiceError(
                        "INVALID_REQUEST", "Image points must each have two coordinates"
                    )
        else:
            operations = payload.get("primitives")
            if (
                not isinstance(operations, list)
                or not operations
                or any(op not in PRIMITIVES for op in operations)
            ):
                raise ServiceError(
                    "INVALID_PLAN", "Only declared simulation primitives are allowed"
                )
        record = {"hash": sha, "status": "running", "result": None}
        requests[req_id] = record
        active = req_id
        scene.cancel_event.clear()

        async def execute():
            nonlocal active
            try:
                if operation == "prepare":
                    output = await work(
                        scene.prepare,
                        payload["grasp_point_uv_px"],
                        payload["destination_point_uv_px"],
                        observation,
                    )
                else:
                    reports = []
                    for op in payload["primitives"]:
                        reports.append(await work(scene.execute, op))
                    output = {"execution_reports": reports}
                record["result"] = {
                    "request_id": req_id,
                    "execution_status": "succeeded",
                    "output": output,
                }
                record["status"] = "succeeded"
            except ServiceError as exc:
                record["status"] = "cancelled" if exc.code == "CANCELLED" else "failed"
                record["result"] = {
                    "request_id": req_id,
                    "execution_status": record["status"],
                    "error": exc.to_dict(),
                }
            except Exception:
                record["status"] = "failed"
                record["result"] = {
                    "request_id": req_id,
                    "execution_status": "failed",
                    "error": {
                        "code": "SIMULATION_FAILED",
                        "message": "Simulation execution failed",
                    },
                }
            finally:
                active = None

        record["task"] = asyncio.create_task(execute())
        return {"request_id": req_id, "status": "running"}

    @app.get("/v1/requests/{request_id}")
    async def status(request_id: str):
        if request_id not in requests:
            raise ServiceError("REQUEST_NOT_FOUND", "Unknown request", 404)
        return {k: v for k, v in requests[request_id].items() if k in ("status", "result")}

    @app.get("/v1/requests/{request_id}/result")
    async def result(request_id: str):
        if request_id not in requests:
            raise ServiceError("REQUEST_NOT_FOUND", "Unknown request", 404)
        await asyncio.shield(requests[request_id]["task"])
        return requests[request_id]["result"]

    @app.post("/v1/requests/{request_id}/cancel")
    async def cancel(request_id: str):
        if request_id not in requests:
            raise ServiceError("REQUEST_NOT_FOUND", "Unknown request", 404)
        record = requests[request_id]
        if not record["task"].done():
            scene.cancel_event.set()
        await asyncio.shield(record["task"])
        return {"status": record["status"], "result": record["result"]}

    @app.get("/evaluation-only")
    async def evaluate():
        # No Gateway Tool exposes this privileged route; benchmark evaluator only.
        return await work(scene.evaluate)

    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

"""Exercise actual Gateway-routed model + MuJoCo tools after PAOS Skill startup."""

import argparse
import json
import time
import uuid
from pathlib import Path

import httpx

from operator_vlm_policy.benchmark import GROUND, REVIEW
from operator_vlm_policy.mujoco_scene import PRIMITIVES

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
parser.add_argument("--max-output-tokens", type=int, default=1200)
parser.add_argument("--expected-model")
parser.add_argument("--expected-effort")
args = parser.parse_args()
out = Path(args.output)
out.mkdir(parents=True, exist_ok=True)
client = httpx.Client(base_url="http://127.0.0.1:19122", timeout=185)
cases = []
prefix = "gateway-" + uuid.uuid4().hex[:8]


def save():
    (out / "gateway-report.json").write_text(
        json.dumps({"run_id": prefix, "cases": cases}, ensure_ascii=False, indent=2)
    )


def query(endpoint, operation, arguments=None):
    t = time.perf_counter()
    r = client.post(f"/tools/{endpoint}/{operation}:invoke", json={"arguments": arguments or {}})
    r.raise_for_status()
    body = r.json()
    result = body["data"]["response"]["result"]
    assert result["status"] == "succeeded", result
    cases.append(
        {
            "tool": endpoint + "/" + operation,
            "elapsed_ms": round((time.perf_counter() - t) * 1000, 3),
            "passed": True,
        }
    )
    save()
    return result["outputs"]


def action(tool, arguments):
    t = time.perf_counter()
    r = client.post(
        "/tools/" + tool + ":invoke",
        json={"arguments": arguments, "timeout_ms": 180000, "caller_id": "acceptance"},
    )
    r.raise_for_status()
    body = r.json()
    admission = body["data"]
    invocation = admission["invocation_id"]
    (out / (invocation + "-admission.json")).write_text(
        json.dumps(body, ensure_ascii=False, indent=2)
    )
    for _ in range(1800):
        r = client.get("/invocations/" + invocation + "/result")
        r.raise_for_status()
        result = r.json()["data"]
        if result.get("status") == "available":
            break
        time.sleep(0.1)
    else:
        raise TimeoutError("Gateway result timeout; reconcile invocation " + invocation)
    (out / (invocation + "-result.json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
    payload = result["result"]
    passed = payload["status"] == "succeeded"
    cases.append(
        {
            "tool": tool,
            "invocation_id": invocation,
            "elapsed_ms": round((time.perf_counter() - t) * 1000, 3),
            "passed": passed,
            "status": payload["status"],
            **(
                {"provenance": payload.get("outputs", {}).get("provenance")}
                if tool == "vlm.infer"
                else {}
            ),
        }
    )
    save()
    assert passed, payload
    return payload["outputs"]


cap = query("reasoning.vlm", "capabilities")
assert cap["component"] == "operator-vlm-policy"
effective_route = cap["model_routes"]["default"]
if args.expected_model is not None:
    assert effective_route["model"] == args.expected_model, effective_route
if args.expected_effort is not None:
    assert effective_route["reasoning_effort"] == args.expected_effort, effective_route
frame = query("sim.tabletop", "observe")
request = {
    "request_id": prefix + "-recognize",
    "context": {
        "mode": "task",
        "task_id": prefix,
        "expected_version": 0,
        "updates": {"goal": "Place blue cube inside green tray; preserve red cube"},
    },
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Image is 960x720 pixels. Locate blue cube bbox, a grasp point on visible blue material, and a free point inside green tray. Use original pixels; exclude shadows.",
                },
                {
                    "type": "image",
                    "image_id": "before",
                    "source": {"type": "file", "path": frame["path"]},
                },
            ],
        }
    ],
    "response_format": {"type": "json_schema", "schema": GROUND},
    "limits": {"deadline_ms": 180000, "max_output_tokens": args.max_output_tokens},
}
recognized = action("vlm.infer", request)
points = recognized["output"]["json"]
action(
    "sim.prepare",
    {
        "observation_id": frame["observation_id"],
        "grasp_point_uv_px": points["grasp_point_uv_px"],
        "destination_point_uv_px": points["destination_point_uv_px"],
    },
)
action("sim.execute", {"primitives": PRIMITIVES})
after = query("sim.tabletop", "observe")
review = action(
    "vlm.infer",
    {
        "request_id": prefix + "-review",
        "context": {
            "mode": "task",
            "task_id": prefix,
            "context_id": recognized["context"]["context_id"],
            "expected_version": 1,
            "selection": {"record_ids": [recognized["output"]["record_id"]]},
            "updates": {"external_execution": {"status": "completed", "primitives": PRIMITIVES}},
        },
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Compare BEFORE and AFTER. Is the blue cube now released inside the green tray with gripper away, while red cube remains unchanged? Judge images, not execution status.",
                    },
                    {
                        "type": "image",
                        "image_id": "before",
                        "source": {
                            "type": "context_media",
                            "media_id": recognized["media"][0]["media_id"],
                        },
                    },
                    {
                        "type": "image",
                        "image_id": "after",
                        "source": {"type": "file", "path": after["path"]},
                    },
                ],
            }
        ],
        "response_format": {"type": "json_schema", "schema": REVIEW},
        "limits": {"deadline_ms": 180000, "max_output_tokens": args.max_output_tokens},
    },
)
native = httpx.get("http://127.0.0.1:19121/evaluation-only").json()
report = {
    "run_id": prefix,
    "cases": cases,
    "effective_route": effective_route,
    "model_review": review["output"]["json"],
    "native_evaluation": native,
    "context_versions": [
        recognized["context"]["version"],
        review["context"]["version"],
    ],
    "passed": all(c["passed"] for c in cases)
    and native["success"]
    and review["output"]["json"]["verdict"] == "pass",
}
(out / "gateway-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2))
assert report["passed"], report

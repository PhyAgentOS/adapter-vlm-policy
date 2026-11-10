"""Real-model MuJoCo acceptance; no scripted model answers or oracle-driven execution."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import time
import uuid
from pathlib import Path

import numpy as np

from .config import Settings
from .models import InferenceRequest
from .mujoco_scene import PRIMITIVES, TabletopScene
from .service import InferenceService


def obj(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": required or list(properties),
        "additionalProperties": False,
    }


POINT = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
BOX = {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}
GROUND = obj(
    {
        "description": {"type": "string"},
        "target_bbox_xyxy_px": BOX,
        "grasp_point_uv_px": POINT,
        "destination_point_uv_px": POINT,
    }
)
PLAN = obj(
    {
        "steps": {
            "type": "array",
            "items": obj(
                {
                    "step_id": {"type": "string"},
                    "purpose": {"type": "string"},
                    "precondition": {"type": "string"},
                    "verification": {"type": "string"},
                    "on_failure": {"type": "string"},
                }
            ),
        },
        "constraints": {"type": "array", "items": {"type": "string"}},
        "success_criteria": {"type": "array", "items": {"type": "string"}},
    }
)
ACTIONS = obj(
    {
        "actions": {
            "type": "array",
            "items": obj(
                {
                    "primitive": {"enum": PRIMITIVES},
                    "target": {"enum": ["blue_cube", "green_tray", "gripper", "home"]},
                }
            ),
        },
        "reason": {"type": "string"},
    }
)
REVIEW = obj(
    {
        "verdict": {"enum": ["pass", "fail", "unknown"]},
        "evidence": {"type": "string"},
        "unresolved": {"type": "array", "items": {"type": "string"}},
    }
)
TASK = obj(
    {
        "criteria": {
            "type": "array",
            "items": obj(
                {
                    "id": {
                        "enum": [
                            "correct_object",
                            "inside_tray",
                            "released",
                            "stable",
                            "neighbor_unchanged",
                        ]
                    },
                    "verdict": {"enum": ["pass", "fail", "unknown"]},
                    "evidence": {"type": "string"},
                }
            ),
        },
        "task_verdict": {"enum": ["pass", "fail", "unknown"]},
    }
)


class Benchmark:
    def __init__(self, service, scene_path, output, max_output_tokens=2400, deadline_ms=120000):
        self.service = service
        self.scene_path = scene_path
        self.output = output
        output.mkdir(parents=True, exist_ok=True)
        self.prefix = "bench-" + uuid.uuid4().hex[:10]
        self.records = []
        self.ctx = None
        self.version = 0
        self.task_id = self.prefix
        self.max_output_tokens = max_output_tokens
        self.deadline_ms = deadline_ms

    def images(self, frames):
        return [
            {
                "type": "image",
                "image_id": f["observation_id"],
                "source": {"type": "file", "path": str(Path(f["path"]).resolve())},
                "metadata": {
                    k: f[k]
                    for k in (
                        "observation_id",
                        "camera_id",
                        "capture_event_seq",
                        "sim_time_s",
                    )
                },
            }
            for f in frames
        ]

    async def call(
        self,
        name,
        prompt,
        schema=None,
        frames=(),
        task_mode=False,
        updates=None,
        select=(),
    ):
        raw = {
            "request_id": self.prefix + "-" + name,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}] + self.images(frames),
                }
            ],
            "limits": {
                "deadline_ms": self.deadline_ms,
                "max_output_tokens": self.max_output_tokens,
            },
            "metadata": {"case": name, "benchmark": self.prefix},
        }
        if schema:
            raw["response_format"] = {"type": "json_schema", "schema": schema}
        if task_mode:
            raw["context"] = {
                "mode": "task",
                "task_id": self.task_id,
                "expected_version": self.version,
                "updates": updates or {},
                "selection": {"record_ids": list(select)},
            }
            if self.ctx:
                raw["context"]["context_id"] = self.ctx
        started = time.perf_counter()
        await self.service.submit("acceptance", InferenceRequest.model_validate(raw))
        result = await self.service.wait("acceptance", raw["request_id"])
        wall_ms = round((time.perf_counter() - started) * 1000, 3)
        (self.output / (name + ".json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        record = {
            "case": name,
            "request_id": raw["request_id"],
            "wall_ms": wall_ms,
            "execution_status": result["execution_status"],
            "metrics": result.get("metrics"),
            "usage": result.get("usage"),
            "model": result.get("provenance", {}).get("model_id"),
            "requested_model": self.service.settings.routes[
                self.service.settings.default_route
            ].model,
            "reasoning_effort": self.service.settings.routes[
                self.service.settings.default_route
            ].reasoning_effort,
        }
        self.records.append(record)
        self.write_report()
        if result["execution_status"] != "succeeded":
            record["passed"] = False
            record["error"] = result.get("error")
            self.write_report()
            raise RuntimeError(f"{name}: {result.get('error')}")
        if task_mode:
            self.ctx = result["context"]["context_id"]
            self.version = result["context"]["version"]
        return result

    def verdict(self, name, passed, details):
        record = next(r for r in self.records if r["case"] == name)
        record.update(passed=bool(passed), evaluation=details)
        self.write_report()
        print(json.dumps(record, ensure_ascii=False), flush=True)

    def write_report(self):
        times = [r["wall_ms"] for r in self.records]
        report = {
            "benchmark_id": self.prefix,
            "host": platform.node(),
            "python": platform.python_version(),
            "scope": "Real configured model and MuJoCo; privileged truth restricted to independent evaluator.",
            "cases": self.records,
            "latency_ms": {
                "p50": statistics.median(times) if times else None,
                "p95": float(np.percentile(times, 95)) if times else None,
            },
            "passed": sum(r.get("passed") is True for r in self.records),
            "failed": sum(r.get("passed") is False for r in self.records),
        }
        (self.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        lines = [
            "# MuJoCo acceptance results / MuJoCo 验收结果",
            "",
            f"Host / 主机: {platform.node()}",
            f"Run / 运行: {self.prefix}",
            "",
            "| Case / 用例 | Result / 结果 | Wall ms / 耗时 |",
            "|---|---|---|",
        ]
        for r in self.records:
            lines.append(f"| {r['case']} | {r.get('passed', 'pending')} | {r['wall_ms']} |")
        lines += [
            "",
            "Real-model inference results are separate from mocked unit tests. / 真实模型测试与模拟后端单元测试分开统计。",
            "PAOS/Gateway Skill acceptance is recorded separately. / PAOS/Gateway Skill 验收单独记录。",
        ]
        (self.output / "REPORT.md").write_text("\n".join(lines) + "\n")

    async def run(self):
        scene = TabletopScene(self.scene_path, self.output / "frames", seed=7)
        try:
            before = scene.capture("before")
            chat = await self.call("01-chat", "Reply with exactly the integer result of 17 + 25.")
            self.verdict(
                "01-chat",
                chat["output"]["text"].strip() == "42",
                {"expected": "42", "actual": chat["output"]["text"]},
            )
            plan = await self.call(
                "02-full-plan",
                "Plan the COMPLETE task: move the blue cube into the green tray without disturbing the red cube. "
                "Describe observation, geometric validation, pregrasp, closure, test lift, transfer, release, retreat, action verification and final task checking. "
                "Every step needs precondition, verification and failure recovery; include final success criteria. You are planning, not executing.",
                PLAN,
                [before],
                True,
                {
                    "goal": "Move the blue cube into the green tray",
                    "constraints": ["Do not disturb red cube"],
                    "success_criteria": [
                        "correct_object",
                        "inside_tray",
                        "released",
                        "stable",
                        "neighbor_unchanged",
                    ],
                },
            )
            p = plan["output"]["json"]
            self.verdict(
                "02-full-plan",
                len(p["steps"]) >= 8
                and all(s["on_failure"] and s["verification"] for s in p["steps"]),
                {"steps": len(p["steps"]), "criteria": p["success_criteria"]},
            )
            ground = await self.call(
                "03-ground-and-grasp",
                "Image dimensions are 960 x 720. Locate the BLUE cube and GREEN tray. Return the cube bounding box, ONE grasp point "
                "on visible blue material near its center, and a free point INSIDE the tray. Use original image pixels, u right and v down. "
                "Exclude shadows. Describe the visible scene.",
                GROUND,
                [before],
                True,
                select=[plan["output"]["record_id"]],
            )
            g = ground["output"]["json"]
            evaluation = self.score_ground(scene, before, g)
            self.verdict(
                "03-ground-and-grasp",
                evaluation["grasp_on_target"]
                and evaluation["destination_inside_tray"]
                and evaluation["bbox_iou"] > 0.4,
                evaluation,
            )
            if not self.records[-1]["passed"]:
                raise RuntimeError("Grounding failed; refusing oracle substitution")
            actions = await self.call(
                "04-language-to-action-plan",
                "Compile the user task into executable steps using ONLY these simulation primitives: "
                + str(
                    [
                        "release",
                        "transfer",
                        "pregrasp",
                        "retreat",
                        "close_gripper",
                        "test_lift",
                        "descend",
                    ]
                )
                + ". "
                "Select and order the needed primitives to satisfy the ORIGINAL user goal. The catalogue is unordered. "
                "pregrasp opens and moves above the selected cube; descend approaches; close_gripper closes; "
                "test_lift lifts; transfer moves above the selected tray and lowers; release opens; retreat clears the view. "
                "The external executor will resolve your selected image points using measured depth. Include target names and reason.",
                ACTIONS,
                [],
                True,
                updates={
                    "adopted_target": "blue_cube",
                    "adopted_destination": "green_tray",
                },
                select=[plan["output"]["record_id"], ground["output"]["record_id"]],
            )
            a = actions["output"]["json"]
            ops = [v["primitive"] for v in a["actions"]]
            self.verdict("04-language-to-action-plan", ops == PRIMITIVES, {"primitives": ops})
            if ops != PRIMITIVES:
                raise RuntimeError("Invalid primitive order; do not replace model plan")
            hold_plan = await self.call(
                "04b-hold-only-language-plan",
                "User task: pick up the blue cube and KEEP HOLDING it above the table. Do not place it, release it, or retreat. "
                "Select and order only needed primitives from this UNORDERED catalogue: "
                "release, transfer, pregrasp, retreat, close_gripper, test_lift, descend. "
                "pregrasp opens and moves above the cube; descend approaches it; close_gripper closes; test_lift raises it; "
                "transfer moves it to the tray; release opens the gripper; retreat moves away. Return a bounded JSON plan.",
                ACTIONS,
                [before],
            )
            hold_ops = [v["primitive"] for v in hold_plan["output"]["json"]["actions"]]
            self.verdict(
                "04b-hold-only-language-plan", hold_ops == PRIMITIVES[:4], {"primitives": hold_ops}
            )
            if hold_ops != PRIMITIVES[:4]:
                raise RuntimeError("Hold-only task was not compiled correctly")
            geometry = scene.prepare(g["grasp_point_uv_px"], g["destination_point_uv_px"], before)
            (self.output / "geometry.json").write_text(json.dumps(geometry, indent=2))
            # Negative control: test-lift requested but not executed. Image is unchanged.
            no_op = scene.capture("no-op")
            review = await self.call(
                "05-review-negative",
                "The requested step was test_lift. Execution feedback reports NOT_EXECUTED. Compare before and current images: "
                "has the cube actually been lifted and held? Return fail when evidence contradicts completion, unknown when insufficient. "
                "Do not infer success from a plan.",
                REVIEW,
                [before, no_op],
                True,
                updates={
                    "last_attempt": {
                        "action": "test_lift",
                        "execution_status": "not_executed",
                    }
                },
                select=[ground["output"]["record_id"]],
            )
            self.verdict(
                "05-review-negative",
                review["output"]["json"]["verdict"] == "fail",
                review["output"]["json"],
            )
            for primitive in hold_ops:
                scene.execute(primitive)
            held_top = scene.capture("test-lift-top")
            held_angle = scene.capture("test-lift-angle", camera="angle")
            review = await self.call(
                "06-review-lift",
                "Review completed test_lift. First image is BEFORE top view, second AFTER top view, third AFTER oblique view. "
                "Determine whether the same blue cube is visibly held above the table. Be careful: images come from different views; "
                "the initial view has camera_id world. Tool feedback reports the gripper closed then moved upward, but this alone does not prove success.",
                REVIEW,
                [before, held_top, held_angle],
                True,
                updates={"execution_reports": scene.trace},
                select=[ground["output"]["record_id"]],
            )
            held_truth = bool(scene.data.body("target").xpos[2] > 0.12)
            self.verdict(
                "06-review-lift",
                review["output"]["json"]["verdict"] == "pass" and held_truth,
                {"model": review["output"]["json"], "native_held": held_truth},
            )
            incomplete = await self.call(
                "07-task-negative",
                "Check the ORIGINAL goal and all criteria against the CURRENT images and execution reports. "
                "Do not treat a successful lift as completion of placement. For each criterion give pass/fail/unknown.",
                TASK,
                [held_top, held_angle],
                True,
                select=[ground["output"]["record_id"], review["output"]["record_id"]],
            )
            self.verdict(
                "07-task-negative",
                incomplete["output"]["json"]["task_verdict"] != "pass",
                incomplete["output"]["json"],
            )
            for primitive in ops[4:]:
                scene.execute(primitive)
            after = scene.capture("after")
            after_angle = scene.capture("after-angle", camera="angle")
            native = scene.evaluate()
            (self.output / "native-evaluation.json").write_text(json.dumps(native, indent=2))
            (self.output / "execution-trace.json").write_text(json.dumps(scene.trace, indent=2))
            review = await self.call(
                "08-review-placement",
                "Review release and retreat. First image is BEFORE; following two are CURRENT top/oblique views. "
                "Is the same blue cube released inside the green tray, with gripper away? Judge visible evidence, not a claimed tool success.",
                REVIEW,
                [before, after, after_angle],
                True,
                updates={"execution_reports": scene.trace},
                select=[ground["output"]["record_id"]],
            )
            self.verdict(
                "08-review-placement",
                review["output"]["json"]["verdict"] == "pass" and native["success"],
                {
                    "model": review["output"]["json"],
                    "native_success": native["success"],
                },
            )
            final = await self.call(
                "09-task-check",
                "Check ALL original criteria using original and current top/oblique images plus caller execution reports. "
                "The current images were captured after release, retreat and a stability wait. Return each criterion once. "
                "Use unknown for unsupported claims, and pass overall only if every criterion is supported.",
                TASK,
                [before, after, after_angle],
                True,
                select=[ground["output"]["record_id"], review["output"]["record_id"]],
            )
            f = final["output"]["json"]
            criteria = {c["id"]: c["verdict"] for c in f["criteria"]}
            correct = set(criteria) == {
                "correct_object",
                "inside_tray",
                "released",
                "stable",
                "neighbor_unchanged",
            }
            self.verdict(
                "09-task-check",
                correct
                and all(v == "pass" for v in criteria.values())
                and f["task_verdict"] == "pass"
                and native["success"],
                {"model": f, "native_success": native["success"]},
            )
            # Different seeds validate localization separately; no oracle-corrected execution.
            for seed in [19, 37]:
                extra = TabletopScene(self.scene_path, self.output / f"seed-{seed}", seed=seed)
                try:
                    frame = extra.capture("initial")
                    found = await self.call(
                        f"10-ground-seed-{seed}",
                        "Locate BLUE cube and GREEN tray in this 960x720 image. Return cube bbox, grasp point on blue material, and free destination point inside green tray. Original pixels, u right, v down.",
                        GROUND,
                        [frame],
                    )
                    score = self.score_ground(extra, frame, found["output"]["json"])
                    self.verdict(
                        f"10-ground-seed-{seed}",
                        score["grasp_on_target"]
                        and score["destination_inside_tray"]
                        and score["bbox_iou"] > 0.4,
                        score,
                    )
                finally:
                    extra.close()
            self.service.store.close_context("acceptance", self.ctx, self.version)
            self.write_report()
        finally:
            scene.close()

    @staticmethod
    def score_ground(scene, frame, result):
        seg = frame["segmentation"][:, :, 0]
        target = seg == scene.model.geom("target_geom").id
        tray = seg == scene.model.geom("tray_floor").id
        v, u = np.nonzero(target)
        truth = [int(u.min()), int(v.min()), int(u.max() + 1), int(v.max() + 1)]
        predicted = np.asarray(result["target_bbox_xyxy_px"], float)
        a = predicted
        b = np.asarray(truth, float)
        overlap = np.maximum(0, np.minimum(a[2:], b[2:]) - np.maximum(a[:2], b[:2]))
        inter = float(np.prod(overlap))
        union = float(np.prod(np.maximum(0, a[2:] - a[:2])) + np.prod(b[2:] - b[:2]) - inter)

        def hit(mask, point):
            x, y = map(lambda v: int(round(v)), point)
            return bool(0 <= x < mask.shape[1] and 0 <= y < mask.shape[0] and mask[y, x])

        return {
            "bbox_iou": round(inter / union if union else 0, 4),
            "native_bbox": truth,
            "predicted_bbox": predicted.tolist(),
            "grasp_on_target": hit(target, result["grasp_point_uv_px"]),
            "destination_inside_tray": hit(tray, result["destination_point_uv_px"]),
        }


async def main_async(args):
    service = InferenceService(Settings.load(args.config))
    run = Benchmark(
        service, Path(args.scene), Path(args.output), args.max_output_tokens, args.deadline_ms
    )
    try:
        await run.run()
    finally:
        await service.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--max-output-tokens", type=int, default=2400)
    parser.add_argument("--deadline-ms", type=int, default=120000)
    parser.add_argument("--scene", default="examples/mujoco_skill/assets/scene.xml")
    parser.add_argument("--output", required=True)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()

"""External simulation test executor. Not a model backend or part of infer()."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image

from .errors import ServiceError

PRIMITIVES = [
    "pregrasp",
    "descend",
    "close_gripper",
    "test_lift",
    "transfer",
    "release",
    "retreat",
]


class TabletopScene:
    def __init__(self, scene_path: Path, output: Path, seed=0):
        self.model = mujoco.MjModel.from_xml_path(str(scene_path))
        self.data = mujoco.MjData(self.model)
        self.output = output
        output.mkdir(parents=True, exist_ok=True)
        self.renderer = None
        self.seq = 0
        self.targets = np.array([-0.32, -0.26, 0.25, 0.04, 0.04], dtype=float)
        for name, value in zip(["x", "y", "z", "left", "right"], self.targets, strict=True):
            joint = self.model.joint(name).id
            self.data.qpos[self.model.jnt_qposadr[joint]] = value
        rng = np.random.default_rng(seed)
        free = self.model.joint("target_free").id
        self.data.qpos[self.model.jnt_qposadr[free] : self.model.jnt_qposadr[free] + 2] += (
            rng.uniform(-0.015, 0.015, size=2)
        )
        self.data.ctrl[:] = self.targets
        mujoco.mj_forward(self.model, self.data)
        self.step(0.8)
        self.initial_neighbor = self.data.body("neighbor").xpos.copy()
        self.initial_target = self.data.body("target").xpos.copy()
        self.trace = []
        self.cancel_event = threading.Event()
        self.grasp_xyz = None
        self.destination_xyz = None

    def step(self, seconds):
        for _ in range(round(seconds / self.model.opt.timestep)):
            self._check_cancel()
            mujoco.mj_step(self.model, self.data)

    def _check_cancel(self):
        # Initialization runs before the cancellation event is installed.
        if hasattr(self, "cancel_event") and self.cancel_event.is_set():
            for i, name in enumerate(["x", "y", "z", "left", "right"]):
                self.targets[i] = self.data.qpos[self.model.jnt_qposadr[self.model.joint(name).id]]
            self.data.ctrl[:] = self.targets
            raise ServiceError("CANCELLED", "Simulation action stopped at a physics step")

    def move(self, xyz, seconds=1.2):
        xyz = np.asarray(xyz, float)
        if xyz.shape != (3,) or not np.isfinite(xyz).all():
            raise ServiceError("INVALID_PLAN", "Invalid position")
        if not (-0.38 <= xyz[0] <= 0.38 and -0.29 <= xyz[1] <= 0.29 and 0.085 <= xyz[2] <= 0.36):
            raise ServiceError("INVALID_PLAN", "Position outside simulation workspace")
        start = self.targets[:3].copy()
        count = round(seconds / self.model.opt.timestep)
        for i in range(count):
            self._check_cancel()
            t = (i + 1) / count
            t = t * t * (3 - 2 * t)
            self.targets[:3] = start + (xyz - start) * t
            self.data.ctrl[:] = self.targets
            mujoco.mj_step(self.model, self.data)
        self.step(0.2)

    def grip(self, opening):
        self.targets[3:] = opening
        self.data.ctrl[:] = self.targets
        self.step(0.65)

    def capture(self, label="frame", camera="world"):
        if self.renderer is None:
            self.renderer = mujoco.Renderer(self.model, height=720, width=960)
        self.seq += 1
        self.renderer.update_scene(self.data, camera=camera)
        rgb = self.renderer.render().copy()
        self.renderer.enable_depth_rendering()
        depth = self.renderer.render().copy()
        self.renderer.disable_depth_rendering()
        self.renderer.enable_segmentation_rendering()
        seg = self.renderer.render().copy()
        self.renderer.disable_segmentation_rendering()
        stem = f"{self.seq:04d}-{label}"
        path = self.output / (stem + ".png")
        Image.fromarray(rgb).save(path)
        np.savez_compressed(self.output / (stem + "-geometry.npz"), depth=depth, segmentation=seg)
        return {
            "path": str(path),
            "observation_id": stem,
            "camera_id": camera,
            "capture_event_seq": self.seq,
            "sim_time_s": float(self.data.time),
            "depth": depth,
            "segmentation": seg,
        }

    def unproject(self, u, v, frame):
        h, w = frame["depth"].shape
        if not (0 <= u < w and 0 <= v < h):
            raise ServiceError("INVALID_PLAN", "Pixel outside image")
        patch = frame["depth"][
            max(0, int(v) - 2) : min(h, int(v) + 3),
            max(0, int(u) - 2) : min(w, int(u) + 3),
        ]
        valid = patch[np.isfinite(patch) & (patch > 0)]
        if not valid.size:
            raise ServiceError("INVALID_PLAN", "No valid depth")
        z = float(np.median(valid))
        cam = self.model.camera("world").id
        f = h / (2 * np.tan(np.deg2rad(self.model.cam_fovy[cam]) / 2))
        optical = np.array([(u - w / 2) * z / f, (v - h / 2) * z / f, z])
        cv_to_world = self.data.cam_xmat[cam].reshape(3, 3) @ np.diag([1, -1, -1])
        return self.data.cam_xpos[cam] + cv_to_world @ optical

    def prepare(self, grasp_uv, destination_uv, frame):
        self.grasp_xyz = self.unproject(*grasp_uv, frame)
        self.destination_xyz = self.unproject(*destination_uv, frame)
        self.pick_tool_z = float(np.clip(self.grasp_xyz[2] + 0.04, 0.09, 0.14))
        self.place_tool_z = float(np.clip(self.destination_xyz[2] + 0.11, 0.105, 0.16))
        return {
            "grasp_surface_xyz_m": self.grasp_xyz.tolist(),
            "destination_surface_xyz_m": self.destination_xyz.tolist(),
            "depth_source": frame["observation_id"],
        }

    def execute(self, primitive):
        if self.grasp_xyz is None:
            raise ServiceError("INVALID_PLAN", "Prepare geometric targets first")
        started = time.perf_counter()
        g = self.grasp_xyz
        d = self.destination_xyz
        if primitive == "pregrasp":
            self.grip(0.04)
            self.move([g[0], g[1], 0.24])
        elif primitive == "descend":
            self.move([g[0], g[1], self.pick_tool_z], 1.0)
        elif primitive == "close_gripper":
            self.grip(0)
        elif primitive == "test_lift":
            self.move([g[0], g[1], 0.25], 1.4)
        elif primitive == "transfer":
            self.move([d[0], d[1], 0.25], 2.0)
            self.move([d[0], d[1], self.place_tool_z], 1.0)
        elif primitive == "release":
            self.grip(0.04)
            self.step(0.6)
        elif primitive == "retreat":
            self.move([d[0], d[1], 0.28])
            self.move([-0.32, -0.26, 0.28], 1.5)
            self.step(0.8)
        else:
            raise ServiceError("INVALID_PLAN", "Unknown simulation primitive")
        self.seq += 1
        report = {
            "primitive": primitive,
            "terminal_event_seq": self.seq,
            "sim_time_s": float(self.data.time),
            "wall_ms": round((time.perf_counter() - started) * 1000, 3),
            "tool_xyz_m": self.data.body("tool").xpos.tolist(),
            "gripper_gap_m": float(
                0.036
                + self.data.qpos[self.model.jnt_qposadr[self.model.joint("left").id]]
                + self.data.qpos[self.model.jnt_qposadr[self.model.joint("right").id]]
            ),
        }
        self.trace.append(report)
        return report

    def evaluate(self):
        # Privileged evaluator output must not enter online prompts/geometry selection.
        target = self.data.body("target").xpos.copy()
        tray = self.data.body("tray").xpos
        inside = bool(np.all(np.abs(target[:2] - tray[:2]) < 0.065) and 0.025 < target[2] < 0.09)
        neighbor = float(np.linalg.norm(self.data.body("neighbor").xpos - self.initial_neighbor))
        speed = float(
            np.linalg.norm(
                self.data.qvel[self.model.jnt_dofadr[self.model.joint("target_free").id] :][:3]
            )
        )
        return {
            "target_in_tray": inside,
            "neighbor_displacement_m": neighbor,
            "target_speed_m_s": speed,
            "success": inside and neighbor < 0.01 and speed < 0.01,
            "target_xyz_m": target.tolist(),
        }

    def close(self):
        if self.renderer:
            self.renderer.close()

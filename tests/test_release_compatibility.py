import sys
from pathlib import Path

import pytest

from operator_vlm_policy.cli import main


def test_bridge_explains_python_requirement_before_importing_optional_sdk(monkeypatch, capsys):
    monkeypatch.setattr(sys, "version_info", (3, 11, 0))
    monkeypatch.setattr(sys, "argv", ["operator-vlm-policy", "forge-bridge"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "Python 3.12+" in capsys.readouterr().err


def test_procedural_scene_compiles_and_advances_without_graphics():
    mujoco = pytest.importorskip("mujoco")
    scene = Path(__file__).resolve().parents[1] / "examples/mujoco_skill/assets/scene.xml"
    model = mujoco.MjModel.from_xml_path(str(scene))
    data = mujoco.MjData(model)
    mujoco.mj_step(model, data, nstep=10)
    assert data.time > 0
    assert model.geom("target_geom").id >= 0

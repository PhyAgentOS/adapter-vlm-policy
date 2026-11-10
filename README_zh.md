[English](README.md) | 简体中文

# adapter-vlm-policy

由 Agent 按需调用的通用 LLM/VLM 推理服务。输入文本、图片和输出格式，返回文本或经过 Schema 校验的 JSON。可选 Task 上下文在识别与后续复核之间保存明确选取的历史和不可变图片证据。

推理服务独立于机器人传感器数据流，不执行模型生成的机器人动作。示例中的 MuJoCo 执行器是独立测试组件。

## Node、Endpoint 与 Tool API

| 项目 | 实现 |
|---|---|
| 默认独立模式 | 一个 HTTP 推理服务；零个 Dora Node 或 Forge Endpoint |
| 生产 Dora Node | 一个可选 `forge-bridge --profile vlm` provider；[实现](src/adapter_vlm_policy/forge_bridge.py)，由同一 CLI 启动 |
| 辅助程序 | 示例仿真 provider、离线基准、请求客户端和 Skill 打包器；不计入生产 Node 数量 |
| Endpoint | 可选 VLM bridge 提供 `reasoning.vlm` |
| Operation | 两个：`capabilities` Query、`infer` Action；描述符每个 operation 并发为四，实际推理另受服务队列/并发配置约束 |
| 生命周期 | Infer 支持状态、结果与取消；控制请求被接受不代表已产生终态 |
| 直接 HTTP Tool API | Dora provider 为零，由 Gateway 路由 Tool；独立服务的 HTTP 路由见下文 |
| 示例部署 | 三个 Dora Node：外部 Gateway、VLM bridge、辅助仿真 bridge；两个业务 Endpoint、五个部署 Tool |

示例 [Gateway 配置](examples/mujoco_skill/profiles/mujoco/gateway.yaml) 定义以下绑定：

| Tool | Endpoint / operation | Provider |
|---|---|---|
| `vlm.capabilities` | `reasoning.vlm / capabilities`（Query） | VLM bridge |
| `vlm.infer` | `reasoning.vlm / infer`（Action） | VLM bridge |
| `sim.observe` | `sim.tabletop / observe`（Query） | 示例仿真 bridge |
| `sim.prepare` | `sim.tabletop / prepare`（Action） | 示例仿真 bridge |
| `sim.execute` | `sim.tabletop / execute`（Action） | 示例仿真 bridge |

这些 Tool ID 是示例绑定，不是新增 Endpoint。[请求类型](src/adapter_vlm_policy/models.py) 使用 `vlm.infer.request.v1`，结果使用 `vlm.infer.result.v1`。Forge wire 兼容性遵循固定版本公共 SDK 的 `TOOL_ENDPOINT_PROTOCOL`。本仓库不分发预构建 Node 或 Gateway 二进制。

## 安装与启动

服务要求 POSIX 上的 Python 3.11+；可选 Forge SDK 要求 Python 3.12+。Python 3.11 的依赖标记会跳过 Forge SDK，bridge CLI 会明确提示需要 Python 3.12+。使用独立虚拟环境；POSIX 存储锁尚不支持 Windows。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
cp config.example.yaml config.local.yaml
export VLM_API_BASE=https://your-provider.example/v1
export VLM_MODEL=your-vision-capable-model
# VLM_API_KEY 通过本机环境或凭据管理器提供。
.venv/bin/adapter-vlm-policy serve --config config.local.yaml
```

也可在模型路由配置 `provider_file: ~/.llm_config`。服务将文件中的 `base_url`、`api_key`、`model_name`、`model_effort` 作为简单赋值数据读取，不执行该文件。模型和 effort 的优先级为显式路由设置、`VLM_MODEL` / `VLM_REASONING_EFFORT`、文件值；未设置的环境变量占位符会回退到文件。文件也支持标准别名 `model`、`reasoning_effort`。effort 按 `reasoning_effort` 原值传给后端，不会因后端不支持而悄悄删除或换模型。capabilities、推理快照及成功结果 provenance 显示实际 effort。若文件保存站点根地址，可配置 `api_prefix: v1`。已有 PAOS JSON 配置可通过 `paos_config` 导入；密钥不进入报告或 capabilities。

通过 allowed_file_roots 配置服务主机可读的图片目录。默认监听 loopback；监听其他接口前必须配置凭据到 namespace 的映射。客户端使用 Authorization Bearer，不能通过请求 metadata 切换到其他 namespace。

## API

| 路由 | 用途 |
|---|---|
| GET /health | 进程就绪 |
| GET /v1/capabilities | 已实现的模型、媒体、上下文能力 |
| POST /v1/infer | 提交推理，HTTP 202 返回请求句柄 |
| POST /v1/infer:wait | 提交或对账，并等待终态 |
| GET /v1/requests/{id} | 查询状态及已产生结果 |
| GET /v1/requests/{id}/result | 等待或读取结果 |
| POST /v1/requests/{id}/cancel | 取消本次推理 |
| GET /v1/requests/{id}/snapshot | 实际组装的上下文、图片引用和修复请求 |
| GET /v1/contexts/{id} | 查询有权限的 Task 上下文及版本 |
| POST /v1/contexts/{id}/close | 通过 expected_version 关闭上下文 |

Agent 侧工具保持 vlm.infer 和 vlm.capabilities。状态、取消及上下文查询属于生命周期 API，不是按抓取业务拆分的推理工具。

```json
{
  "request_id": "question-1",
  "messages": [{"role": "user", "content": "简短解释当前任务。"}],
  "limits": {"deadline_ms": 60000, "max_output_tokens": 1200}
}
```

将请求保存为 request.json 后执行：

```bash
.venv/bin/adapter-vlm-policy infer request.json --url http://127.0.0.1:19120
```

图片内容块：

```json
{
  "type": "image",
  "image_id": "before",
  "source": {"type": "file", "path": "/controlled/frames/before.png"},
  "metadata": {"observation_id": "obs-118", "camera_id": "world"}
}
```

传图片字节时使用 source 的 inline_base64 类型，并提供 mime_type/data。Task 模式还支持 `{"type":"context_media","media_id":"<returned-media-id>"}`。仅有 observation_id 无法提供像素；服务不订阅相机，不回退到隐式 latest 图。保留原图，不隐式裁剪或缩放坐标。

response_format 默认 text。结构化结果使用 `{"type":"json_schema","schema":{...}}` 或已配置的 schema_id。只解析模型可见回复，不从 reasoning 流猜答案。输出无效时有限修复，仍失败则返回结构化错误。结构有效不等于识别或物理判断正确。

## 同一任务的多次调用

1. 首次 infer 传 context：mode=task、task_id、expected_version=0，以及 updates 中的目标与约束。
2. 读取返回的 context_id、version、output.record_id 和 media_id。
3. Agent 独立调用执行工具，再将实际执行报告和新图片传给下一次 infer。
4. 继续传入 context_id/task_id/expected_version，通过 selection.record_ids 选择历史，通过 updates 更新 Agent 信息。
5. 要比较旧图时，明确通过 context_media 再引用其像素。历史记录不会自动重新加载图片，也不会把旧 system prompt 变成当前指令。

成功 turn 只推进一次版本，模型输出保留为假设。失败、取消、超时不提交暂存 Task 更新。request_id 按不可变载荷去重，冲突复用会被拒绝；同一上下文只允许一个写入者，旧版本或并发写入返回明确错误。目前支持 main 分支，不声明自动摘要或 fork 已实现。

SQLite 记录和不可变媒体支持重启恢复；重启时在途请求标记 SERVICE_RESTARTED，不会悄悄提交。每个数据目录只允许一个服务进程。Idle TTL 清理上下文文本与媒体，恢复过期上下文会明确报错；活跃请求固定依赖媒体。队列、图片、输出、deadline 和上下文预算均有限额，超大上下文会拒绝而非静默删除证据。

## PAOS + Gateway + MuJoCo 示例

安装测试依赖及兼容的 Forge SDK：

```bash
.venv/bin/python -m pip install -e '.[test,forge]'
.venv/bin/python scripts/build_demo_skill.py
```

示例包含 Gateway、推理控制适配器和独立仿真控制适配器。适配器管理各自的 HTTP 子服务，不订阅相机流；图片由 Agent 显式传入推理服务。MuJoCo 执行器通过平行夹爪的真实接触物理抓放，提供观测、几何准备和受限仿真动作。特权评估接口不注册为 Agent Tool。

启动前配置：

```bash
export ADAPTER_VLM_PYTHON=/absolute/project/.venv/bin/python
export ADAPTER_VLM_WORKSPACE=/absolute/local/demo-output
export GATEWAY_BINARY=/absolute/path/to/compatible/gateway
# 此 profile 从 ~/.llm_config 读取 base_url、api_key、model_name 和 model_effort。
paos skill install dist/vlm-mujoco-demo-0.1.2.tar.gz --local --yes
paos skill start vlm-mujoco-demo --profile mujoco
paos skill status vlm-mujoco-demo
paos skill stop vlm-mujoco-demo
```

示例启动脚本会清空继承的 VLM 模型/effort 环境覆盖值，防止共享 Dora daemon 的旧值替换文件设置。如需主动覆盖示例模型，请修改 service YAML 中显式 model/reasoning_effort 路由设置。独立服务仍遵循上文的优先级。

若已有机器人 Runtime 在运行，使用独立 PAOS 实例。scripts/isolated_paos.py 使用官方配置路径机制：设置 ADAPTER_PAOS_CONFIG 为另一份配置文件，再用 PAOS 解释器运行相同 skill 命令。不会停止共享 Dora coordinator 或其他 flow。

## 验证

```bash
.venv/bin/python -m pytest -q --durations=10
MUJOCO_GL=egl .venv/bin/python -m adapter_vlm_policy.benchmark \
  --config config.local.yaml --output reports/my-run
# 实际通过 PAOS 启动 Skill 后：
.venv/bin/python scripts/gateway_acceptance.py --output reports/my-gateway-run
```

使用推理模型时，应在部署限额和请求中显式设置足够的输出预算。基准支持 `--max-output-tokens 8192 --deadline-ms 180000`，Gateway 脚本支持 `--max-output-tokens 8192`；这些参数不改变配置的 effort。即使没有产生可见文本，token 上限响应也会明确报告为截断。

验收使用真实模型、渲染 RGB-D、独立分割评分、模型选出的图像点和外部 MuJoCo 物理执行。包含动作/任务负例，并报告耗时、用量、原生评估和证据。模拟后端单元测试与真实模型测试分开统计。当前为桌面直角坐标夹爪基准，不能据此宣称任意物体或通用机械臂抓取已验证。

详见[验证摘要](docs/VALIDATION_zh.md)、[开发手册](DEVELOPING_zh.md)和示例 Skill 说明，了解实施范围及复现方式。详细运行记录、凭据文件与内部资料保持本地。

## 许可证与贡献

采用 Apache-2.0：[LICENSE](LICENSE)、[NOTICE](NOTICE)、[第三方声明](THIRD_PARTY_NOTICES.md)。另见[贡献指南](CONTRIBUTING.md)、[安全政策](SECURITY.md)和[开源审计](OPEN_SOURCE_AUDIT.md)。可用 `uv sync --frozen --all-extras --group dev` 创建可复现开发环境；依赖锁定到公共 PyPI 来源。

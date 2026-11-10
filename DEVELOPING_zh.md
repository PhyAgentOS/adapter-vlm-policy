[English](DEVELOPING.md) | 简体中文

# 开发手册

## 组件职责

- models.py：严格请求类型，无状态/Task 上下文，文本和 file/inline/context_media 图片输入。
- config.py：模型路由、限额、注册 Schema、凭据解析；凭据文件作为数据读取，不执行。模型/effort 优先级为显式路由→环境变量→文件，读取 model_name/model_effort；配置 effort 时后端请求发送 reasoning_effort。
- store.py：SQLite WAL 事务、进程占用、上下文写入预留、请求去重、不可变媒体、版本记录及过期清理。
- service.py：请求规范化、显式选择历史、预算检查、异步推理、Schema 修复、输入快照和终态提交。
- backend.py：兼容 Chat API 的通用后端，不执行模型工具调用，不提供机器人动作后端。
- api.py：HTTP 请求体限额、认证 namespace 和生命周期路由。
- forge_bridge.py：可选控制面适配器，无传感器输入；转发到独立推理服务并映射 invocation 生命周期。
- mujoco_scene.py、benchmark.py、examples/mujoco_skill/simulation_service.py：外部验收组件，不是推理服务行为。

## 状态转换

```text
请求：queued → running → succeeded / failed / cancelled / timed_out
上下文：active(version N) → 成功原子提交(version N+1)
        active → closed / expired
```

Task 写入在推理前占用上下文；只有输出有效时，暂存的 Agent 更新和模型 turn 才一起提交。模型结论保留为 model_hypothesis，不自动合入 Agent 事实。namespace 来自服务凭据，task_id 本身不授权访问；媒体、记录、上下文查询都检查 namespace。

每次推理固定像素内容、规范化载荷哈希、选定记录、父版本及实际输入。历史 system prompt 不会继续作为有效指令；旧图只有明确再次引用时才展开像素。snapshot.backend_calls 记录修复请求，图片用不可变媒体引用和哈希表示。

Deadline 覆盖准备、排队、推理与验证，预算由服务限额约束。上下文使用保守估算，超限不会静默裁掉证据。原生 JSON 约束输出为可选能力，请求 JSON 时本地 Schema 校验必做；禁止外部 Schema 引用。

## 并发与恢复

每个数据目录只允许一个服务进程，通过文件锁防止第二个实例将正在运行的请求误标为重启中断。SQLite 事务序列化受理和提交；每个 Task 只有一个在途写入者，无状态调用使用独立媒体容器。

请求去重范围为 namespace/request_id，以及包含解析后图片哈希的载荷；同一路径内容被覆盖不能悄悄成为同一个请求的新推理。状态查询不会重新提交。重启中断的请求失败并释放上下文预留；成功历史和媒体在保留期内继续可用。

取消先记录终态并使提交资格失效，晚到模型答案不能提交。后端不一定能停止计费；这也不等于停止机器人。独立仿真执行器在物理步边界检查取消，产生自己的终态。

## Forge 适配

基础服务不依赖 Dora/Forge。可选控制适配器由 PAOS Skill 启动，管理 HTTP 服务子进程；图片来自 Agent 调用，不来自相机订阅。

bridge 使用公开的核心 Forge Tool SDK 及 `_vendor` 下保留许可证的 carrier 子集，不安装完整 Forge 图片消息包，从而无需绕过依赖约束即可使用已修复的 Pillow 13。更新时保留 `_vendor/PROVENANCE.json` 的来源/文件摘要，并运行 carrier 兼容测试；具体 import 适配见 THIRD_PARTY_NOTICES.md。

Endpoint 为 reasoning.vlm，提供 capabilities Query 与 infer Action。状态、结果、取消映射到 Forge invocation 生命周期。适配器必须在第一次等待 HTTP 受理前建立执行记录，否则 Gateway 立即查询结果时会看到 not_found 而误判 unknown。验收脚本立即轮询，实际覆盖此竞态。

注册租约按 Gateway 返回 TTL 的三分之一续期。Dora 主线程收事件、排空输出队列；HTTP/推理控制在后台 asyncio 循环，避免阻塞心跳和取消。只有持有 Dora Node 的主线程写输出。

仿真 Endpoint 独立为 sim.tabletop，提供 observe Query、prepare/execute Action。模型生成的 JSON 动作步骤由外部执行器校验解释，不构成通用 LLM 节点的机器人动作输出模式。

## 测试与证据

单元测试使用明确的测试后端，不宣称验证真实模型能力。覆盖前后图片固定、当前提示和 Schema 隔离、Task/namespace 隔离、跨上下文引用拒绝、版本、去重、立即取消、队列超时、修复审计、重启、TTL 与进程占用。

真实验收使用实际 Provider、MuJoCo RGB-D 和接触式平行夹爪。物体真实位姿和 segmentation 仅进入独立评估器；在线几何从模型选出的像素和测量深度计算。不使用 oracle 坐标补救或 weld 强行附着。失败尝试与通过尝试均保留。

分割评分场景设置 offsamples=0：多重采样会混合边缘 ID 颜色，令目标 mask 被其他物体边缘污染，造成错误 bbox。应修复评分器，不能通过放宽模型阈值掩盖。

模型测试包含“动作没有执行”和“只抬起尚未完成”的负例。调用状态、语义结论、原生成功分别记录；完整规划、识别抓点、动作计划、动作复核、任务检查和总耗时分别报告。unknown/失败不隐藏。

## 可复现命令

```bash
.venv/bin/python -m pytest -q --durations=10 --junitxml=reports/unit-tests.xml
.venv/bin/python -m compileall -q src examples scripts tests
.venv/bin/python scripts/build_demo_skill.py
MUJOCO_GL=egl .venv/bin/python -m adapter_vlm_policy.benchmark \
  --config config.local.yaml --output reports/acceptance
.venv/bin/python scripts/gateway_acceptance.py --output reports/gateway
```

本地归档解释器、依赖版本、源码摘要、Gateway 摘要、路由/实际模型、场景种子、请求标识、时间及原始结构化结果。报告不含凭据或未处理的 Provider 私有响应。用量采用 Provider 返回值，没有实际价格/账单依据时不推算金额。

## 当前限制

- 仅 main 上下文分支，没有自动历史压缩和分支合并；超预算请求明确拒绝。
- 一个 SQLite 目录只支持一个进程，没有分布式上下文锁。
- 支持 inline/file/context_media，没有 multipart 上传或任意远程 URL 抓取。
- 首版是桌面直角坐标夹爪测试，不宣称任意物体抓取或通用机械臂避障已经验收。
- Gateway Wire 消息上限可能小于服务 HTTP 图片上限；经 Gateway 时优先受控同机路径或合适压缩的内联图片。
- 示例需要明确提供 PAOS、Dora、兼容 Gateway 与 Forge SDK。源码安装的版本也必须记录并实际联调，不能仅凭依赖声明判断兼容。

# 记录与启动约定

## 统一创建入口

Codex 根据实际任务准备一份 JSON 描述。用户在会话中说明实验即可。首次使用写完整阶段信息；后续同组实验用 `"stage": "01-baseline"` 复用该阶段。

```json
{
  "stage": {
    "id": "01-baseline",
    "title": "基线复现",
    "question": "确认现有方法在统一评估设置下的表现。",
    "settings_summary": "统一验证集与随机种子",
    "common_config": {"seed": 42},
    "metrics": [
      {"key": "score", "label": "得分", "direction": "up", "digits": 3},
      {"key": "loss", "label": "损失", "direction": "down", "digits": 4}
    ]
  },
  "run": {
    "name": "基线复现",
    "changes": "默认配置",
    "config": {"seed": 42, "steps": 10000, "learning_rate": 0.0001},
    "total": 10000,
    "implementation": [
      {"file": "train.py", "start": 20, "end": 35, "description": "实际训练循环与损失计算"}
    ],
    "artifacts": [{"label": "评估结果", "path": "outputs/baseline/metrics.json"}]
  },
  "command": ["python", "train.py", "--config", "configs/baseline.json"]
}
```

`config` 使用本次运行实际生效的完整配置，包括默认值与命令行覆盖项。`implementation` 的 `file` 相对项目根目录，`start`、`end` 为从 1 开始且包含端点的行号；入口自动保存代码片段。也可直接提供 `file`、`description`、`code`。命令使用参数数组，按原样执行，工作目录为项目根目录。配置文件与代码在登记后变动时，重新登记对应运行。

```sh
# 一次完成初始化、创建阶段、分配 ID、保存设置与代码、启动进程。
python3 "$SKILL_DIR/scripts/report.py" run --project "$PROJECT" --spec "$SPEC"

# 先登记待运行实验，再启动同一行。
python3 "$SKILL_DIR/scripts/report.py" new --project "$PROJECT" --spec "$SPEC"
python3 "$SKILL_DIR/scripts/report.py" start --project "$PROJECT" --stage 01-baseline --run exp-001
```

同阶段的运行 ID 自动分配为 `exp-001`、`exp-002` 等。并发登记使用独占创建分配 ID。新阶段默认以 `exp-001` 为基线，可在阶段 JSON 中指定其他运行。阶段 ID 使用简短英文字母、数字和连字符。

## 采集与进程状态

启动器将脚本目录加入 `PYTHONPATH`，并设置 `PROJECT_REPORT_RUN`。训练主 rank 使用：

```python
from report import Run

record = Run.from_env()
# 沿用训练现有的日志间隔记录进度。
record.update(step=step)
# 本次实际测得的指标；每个指标保留自己的采样 step 和时间。
record.update(step=step, metrics={"loss": loss})
# 异步评估返回较早 checkpoint 的结果时，保留评估所属 step。
record.update(metrics={"score": score}, metric_step=evaluated_step)
record.update(performance={
    "GPU": actual_device,
    "显存": actual_memory_usage,
    "吞吐": actual_throughput,
    "micro-batch": micro_batch,
    "梯度累积": accumulation_steps,
    "全局 batch": global_batch,
    "实验并发": concurrent_runs,
})
# 退出前通过 update 保存最终评估值；启动器根据进程退出结果收尾。
record.update(step=final_step, metrics={"score": final_score, "loss": final_loss})
```

同一 step 的采样合并该 step 的实测指标；其他 step 的指标各自保存。表格展示每项指标的最新采样步，曲线使用该指标实际出现的采样点。后到达的旧 step 结果保留在历史中，表格继续展示较新 step 的结果。

启动器每 5 秒更新一次进程监测心跳，记录主机、PID、退出码与日志。正常退出为 `completed`，异常退出或子进程被终止为 `failed`；向启动器发送 SIGINT/SIGTERM 时转发给整个任务进程组，记录为 `cancelled`。监测心跳超过 30 秒停止更新时，页面显示 `unknown`（待确认），保留真实的最近采集时间。重新读取到有效心跳或最终结果后恢复实际状态。

启动命令持续等待实际训练、评估或调度作业完成。长任务把整个启动器放进 tmux 或作业环境；报告网页服务单独运行。远端训练时，数据和启动器一起放在远端项目，通过端口转发打开报告。

已有日志采集器可使用 `Run(report_dir, stage_id, run_id)` 更新指标。自行管理生命周期时，由一个明确的进程或调度采集方创建 `status="running"` 的记录，使用 `update()` 和 `finish(status=..., exit_code=..., detail=...)`。此模式以最近采样时间判断失联，`create(stale_after_seconds=300)` 可按实际采样周期调整。数据来自多个 rank 时汇总到主 rank 写入。

## 写入归属与解释

```text
report/
  index.html                         # 总览快照
  data/project.json                  # 项目与当前阶段，Codex 维护
  data/stages/01-baseline.json        # 阶段设置、总结、基线、媒体
  data/stages/01-baseline.md          # 阶段解释与下一步
  data/runs/01-baseline/exp-001.json   # 实际设置、代码快照、训练采样
  data/lifecycle/01-baseline/exp-001.json  # 启动器独立维护状态
  data/analysis/01-baseline/exp-001.json   # Codex 独立维护解释
  logs/01-baseline/exp-001.log         # 启动进程的 stdout 与 stderr
  stages/01-baseline/index.html       # 阶段快照
  assets/                            # 图像与视频
  static/                            # 页面资源
```

采样、状态、解释分别由一个写入方维护，页面聚合展示。训练进程使用 `update()`；启动器负责收尾；Codex 使用以下命令更新解释：

```sh
python3 "$SKILL_DIR/scripts/report.py" annotate --project "$PROJECT" \
  --stage 01-baseline --run exp-001 \
  --comment "本轮得分提高，下一轮检验随机种子敏感性。" --basis measured
```

`basis` 使用 `measured`（实测）、`inferred`（推断）、`pending`（待验证）。解释更新时间单独保存。旧版运行记录继续读取；第一次 `annotate` 会建立独立解释文件，后续训练更新保持解释内容。

阶段 JSON 维护 `summary`、`analysis_at`、`state`（`active` 或 `completed`）、`baseline` 和共用设置。阶段 Markdown 支持二级标题、段落、短列表、粗体、行内代码；段落之间留空行。把发现、证据和下一步写成几句话。图片和视频保存在 `report/assets/`，在阶段的 `media` 数组中写 `type`（`image` 或 `video`）、相对报告根目录的 `path` 和 `caption`。

## 查看与保存

```sh
python3 "$SKILL_DIR/scripts/report.py" serve --project "$PROJECT" --port 8765
python3 "$SKILL_DIR/scripts/report.py" build --project "$PROJECT"
```

实时页面每 5 秒读取记录；每行显示最近采集时间。“每 5 秒更新”表示网页刷新频率，指标随实际评估更新。表格展示阶段定义的前两个指标，曲线使用第一个指标。只有实验和基线均完成、对应指标均为有效数值时才计算最终差值。

`new`、启动器结束、`annotate` 会生成离线快照；编辑阶段 JSON 或 Markdown 后运行 `build`。直接打开 HTML 展示快照中的状态与时间。整个 `report/` 可一起移动。示例阶段使用 `demo: true`，持续显示示例标记。

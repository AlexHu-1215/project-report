# 记录与启动约定

## 统一创建与启动

Codex 根据任务准备一份 JSON 描述。首次创建阶段写完整阶段信息，后续同组实验使用 `"stage": "01-baseline"`。

```json
{
  "stage": {
    "id": "01-baseline", "title": "基线复现",
    "question": "确认现有方法在统一评估设置下的表现。",
    "settings_summary": "统一验证集与随机种子",
    "common_config": {"seed": 42},
    "metrics": [
      {"key": "score", "label": "得分", "direction": "up", "digits": 3},
      {"key": "loss", "label": "损失", "direction": "down", "digits": 4}
    ]
  },
  "run": {
    "name": "基线复现", "changes": "默认配置",
    "config": {"seed": 42, "steps": 10000, "learning_rate": 0.0001},
    "config_file": "configs/baseline.json", "total": 10000,
    "implementation": [
      {"file": "train.py", "start": 20, "end": 35, "description": "实际训练循环与损失计算"}
    ],
    "artifacts": [{"label": "评估结果", "path": "outputs/baseline/metrics.json"}]
  },
  "command": ["python", "train.py", "--config", "configs/baseline.json"]
}
```

`config` 是登记时的完整计划配置；`config_file` 为可选的 JSON 配置文件，启动器在实际启动时重新读取。程序通过 `configure()` 提交默认值和命令行覆盖项解析后的实际配置。页面标明配置来源，保存计划与实际两份记录。

`implementation` 的文件相对项目根目录，`start`、`end` 是从 1 开始且包含端点的行号。登记和启动时分别保存代码内容，旧快照保留。也可直接提供 `file`、`description`、`code`。命令使用参数数组，工作目录为项目根目录。

```sh
python3 "$SKILL_DIR/scripts/report.py" run --project "$PROJECT" --spec "$SPEC"
# 先登记待运行实验，再启动同一行。
python3 "$SKILL_DIR/scripts/report.py" new --project "$PROJECT" --spec "$SPEC"
python3 "$SKILL_DIR/scripts/report.py" start --project "$PROJECT" --stage 01-baseline --run exp-001
```

同阶段 ID 自动分配为 `exp-001` 等，支持并发登记。新阶段以 `exp-001` 为默认基线，也可在阶段 JSON 中指定其他运行。ID 使用英文字母、数字和连字符。

## 实际配置、采样和最终评估

启动器将脚本目录加入 `PYTHONPATH` 并设置 `PROJECT_REPORT_RUN`。训练主 rank 接入：

```python
from report import Run
record = Run.from_env()
record.configure(actual_resolved_config)
# 沿用已有日志间隔采集进度和性能。
record.update(step=step, metrics={"loss": loss})
record.update(performance={"GPU": actual_device, "显存": actual_memory_usage,
    "吞吐": actual_throughput, "micro-batch": micro_batch,
    "梯度累积": accumulation_steps, "全局 batch": global_batch, "实验并发": concurrent_runs})
# 异步评估保留被评估 checkpoint 的真实 step。
record.update(metrics={"score": score}, metric_step=evaluated_step)
# 最终评估落盘后，明确提交所有阶段指标。
record.finalize(step=final_step, metrics={"score": final_score, "loss": final_loss})
```

每项指标保留自己的采样 step 和时间。同 step 的实测值合并；后到达的旧 step 结果进入历史，表格保留较新 step 的值。`finalize()` 提交独立的最终评估；提前停止时使用真实结束 step。后续新增训练进度或指标会使此前的最终评估重新等待提交。

进程退出状态与评估就绪分别保存。只有正常结束且最终评估包含阶段定义的全部有效数值指标，才参与最终排名和基线比较。正常退出且仍待最终评估的行显示“进程已结束 / 等待最终评估”。比较使用明确提交的最终指标。

启动器每 5 秒更新心跳，记录主机、PID、退出码及日志。正常退出为 `completed`，异常退出或子进程被终止为 `failed`；启动器收到 SIGINT/SIGTERM 时转发给任务进程组，记录为 `cancelled`。心跳超过 30 秒停止更新时，实时页面显示 `unknown`（待确认）。

长任务把整个启动器放进 tmux 或作业环境，启动命令持续等待实际作业结束。已有日志采集器可用 `Run(report_dir, stage_id, run_id)`；自行管理生命周期时，由同一进程或调度采集方创建 `status="running"` 的记录，使用 `finish(status=..., exit_code=..., detail=...)` 收尾。此模式通过最近采样判断失联，可用 `create(stale_after_seconds=300)` 调整采样周期。多个 rank 汇总到主 rank 写入。

## 文件归属与解释

```text
report/
  index.html                            # 总览快照
  data/project.json                     # 项目与当前阶段
  data/stages/01-baseline.json           # 阶段设置、总结、证据与已审阅结果
  data/stages/01-baseline.md             # 简短解释与下一步
  data/runs/01-baseline/exp-001.json      # 登记时的计划设置和实现
  data/launch/01-baseline/exp-001.json    # 启动时的配置文件与代码快照
  data/events/01-baseline/exp-001.jsonl   # 训练进程追加配置、采样和最终评估
  data/lifecycle/01-baseline/exp-001.json # 启动器维护状态
  data/analysis/01-baseline/exp-001.json  # Codex 维护解释
  logs/01-baseline/exp-001.log            # stdout 与 stderr
  stages/01-baseline/index.html          # 阶段快照
  assets/                               # 图像与视频
  static/                               # 页面资源
```

训练、启动器、Codex 分别维护自己的文件。采样按行追加，服务从上次读到的位置继续聚合。原始采样完整保存；页面曲线每个运行最多展示 240 个趋势点。运行时状态通过 `collect_run()` 或报告 API 读取。

```sh
python3 "$SKILL_DIR/scripts/report.py" annotate --project "$PROJECT" \
  --stage 01-baseline --run exp-001 --comment "本轮得分提高，下一轮检验种子敏感性。" --basis measured
python3 "$SKILL_DIR/scripts/report.py" conclude --project "$PROJECT" \
  --stage 01-baseline --summary "设置 B 在统一评估中提高得分。" --runs exp-001 exp-002
```

`annotate` 单独保存解释及时间，`basis` 使用 `measured`、`inferred` 或 `pending`。`conclude` 保存结论、引用实验的指标快照及当前已审阅的结果。结论依据可点击定位实验行；后续新评估使页面提示“有新结果，结论待更新”，首页列出需要关注的阶段与实验。

阶段 Markdown 支持二级标题、段落、短列表、粗体和行内代码，段落之间留空行。阶段 JSON 维护 `state`（`active` 或 `completed`）、`baseline` 和共用设置。媒体放入 `report/assets/`，在阶段 `media` 数组中写 `type`、相对报告根目录的 `path` 和 `caption`。

旧版 JSON 记录继续展示已有数据；新增采样写入事件文件。显式提交最终评估后进入最终比较。

## 查看与保存

```sh
python3 "$SKILL_DIR/scripts/report.py" serve --project "$PROJECT" --port 8765
python3 "$SKILL_DIR/scripts/report.py" build --project "$PROJECT"
```

实时页面每 5 秒查询当前阶段，首页只读取摘要。局部更新保留展开状态、视频播放、文字选择和阅读位置。表格展示前两个指标及相对基线的设置差异，完整变化见详情。网页刷新频率与实际采样周期分别展示。

`new`、启动器结束、`annotate` 和 `conclude` 生成快照。编辑阶段说明后执行 `build`。各阶段 HTML 保存本阶段的数据，整个 `report/` 可一起移动。直接打开 HTML 查看保存时的状态。示例阶段使用 `demo: true`，实测实验使用独立阶段。

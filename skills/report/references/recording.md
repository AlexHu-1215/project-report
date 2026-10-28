# 数据与采集约定

## 文件与更新单位

```text
report/
  index.html                      # 总览快照
  data/project.json               # 项目名称、当前阶段、时区
  data/stages/01-topic.json        # 阶段元信息与共用设置
  data/stages/01-topic.md          # 简短解释与下一步
  data/runs/01-topic/exp-001.json  # 一次运行的完整记录
  stages/01-topic/index.html      # 阶段快照
  assets/                        # 报告使用的图像、视频
  static/                        # 本地样式与交互
```

每个运行由一个采集方更新自己的 JSON 文件，多个运行使用不同文件。首页和阶段页读取同一份数据。`serve` 每次请求聚合最新记录，网页每 5 秒刷新数据；`build` 把当前数据写入 HTML，支持直接打开文件浏览。

JSON 记录指标、配置和状态，Markdown 记录解释，HTML 由脚本生成。编辑原始记录后执行 `build`。页面使用相对路径，可随整个 `report/` 文件夹移动。

## 新阶段

在 `data/stages/` 下创建同名 JSON 与 Markdown，并在 `data/project.json` 的 `current_stage` 指定阶段 ID。示例结构：

```json
{
  "id": "01-baseline",
  "title": "基线复现",
  "question": "确认现有方法在统一评估设置下的表现。",
  "state": "active",
  "demo": false,
  "baseline": "exp-001",
  "summary": "等待首轮评估结果。",
  "analysis_at": null,
  "settings_summary": "统一数据、评估步数与随机种子",
  "common_config": {},
  "metrics": [
    {"key": "score", "label": "得分", "direction": "up", "digits": 3},
    {"key": "loss", "label": "损失", "direction": "down", "digits": 4}
  ],
  "media": []
}
```

`state` 使用 `active` 或 `completed`。表格展示前两个指标，曲线使用第一个指标。阶段 ID 和运行 ID 使用简短的英文字母、数字和连字符。

Markdown 使用二级标题、段落、短列表、粗体和行内代码。保持简短，例如：

```markdown
## 发现与解释

当前观察及其证据。推断同时写明仍待验证的原因。

## 下一步

- 下一项具体实验，以及它要回答的问题。
```

图片或视频保存到 `report/assets/`，在 `media` 中填入 `type`（`image` 或 `video`）、相对报告根目录的 `path` 和 `caption`。

## 采集真实进度

将 skill 的 `scripts/` 加入实验的 `PYTHONPATH`，或将单文件 `report.py` 放到实验可导入的位置，然后接入已有训练/评估流程：

```python
from report import Run

record = Run("/path/to/project/report", "01-baseline", "exp-001").create(
    name="基线复现",
    changes="默认配置",
    config=actual_config,
    command=actual_command,
    implementation=[{
        "file": "train.py:train",
        "description": "使用实际训练入口和实现说明。",
        "code": "相关的关键实现片段"
    }],
    artifacts=[{"label": "训练日志", "path": actual_log_path}],
    total=total_steps,
)

# 在已有的进度回调或评估节点调用。
record.update(step=step, metrics={"score": score, "loss": loss})
record.update(performance={
    "GPU": actual_device,
    "显存": actual_memory_usage,
    "吞吐": actual_throughput,
    "micro-batch": micro_batch,
    "梯度累积": accumulation_steps,
    "全局 batch": global_batch,
    "实验并发": concurrent_runs,
})

# 正常结束时保存最终评估值。解释由 Codex 依据结果另行填写。
record.finish(metrics={"score": final_score, "loss": final_loss})
```

恢复已登记的实验时直接使用 `Run(report_dir, stage_id, run_id)` 继续更新。同一设置复跑使用新的运行 ID。记录数据使用有时区的 ISO 8601 时间。

运行状态为 `planned`、`running`、`completed`、`failed`。结论依据 `basis` 为 `pending`、`measured`、`inferred`。采集到数值时更新为 `measured`，解释的推断性质在文字中明确说明。

结束、异常或调度取消后，根据实际返回状态调用 `finish(status="failed", comment=...)` 或更新 JSON。失联时保留最后一次采集时间，由日志或调度系统确认状态。将同一训练进程的生命周期与采集调用放在一起。

## 整理结果

在运行记录中编辑 `comment` 和 `analysis_at`，在阶段 JSON 中编辑 `summary`、`analysis_at`，在同名 Markdown 中补充解释。保存运行时的完整配置、命令、关键实现说明与必要代码片段。产物路径和日志路径对应实际输出；页面以文本展示路径，复制到工作环境即可定位。

完成后执行：

```sh
python3 "$SKILL_DIR/scripts/report.py" build --project "$PROJECT"
```

演示阶段使用 `demo: true`，页面持续显示示例标记。示例配置、代码和路径用于说明排版与信息结构。

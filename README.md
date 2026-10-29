# Project Report

一个手动启用的 Codex skill，将实验进度、结果、设置和实现整理成可浏览的本地报告。

**一个总览首页 → 每个阶段一个页面 → 阶段内一张持续更新的实验表。**

![实验报告示例](docs/preview.png)

图中的实验、指标、配置和运行状态均为示例数据。

## 能做什么

- 总览当前阶段、正在运行的实验与最近更新时间。
- 一次运行对应一行，统一入口自动分配运行 ID、保存配置和关键代码；复跑保留独立记录。
- 启动器记录进程、日志和退出结果，监测中断时标记为“待确认”。
- 点击实验行查看完整配置、启动命令、关键实现、资源使用和产物位置。
- 每项指标展示真实采样 step，已完成实验与已完成基线计算差值。
- 用短篇 Markdown 保存阶段解释与下一步，支持附加图片和视频。
- 采样、进程状态和解释分别保存，本地网页每 5 秒读取最新记录，并展示最近采集时间。

## 安装与手动启用

在 Codex 中使用内置的 skill-installer：

```text
$skill-installer 安装 https://github.com/AlexHu-1215/project-report/tree/main/skills/report
```

安装后的技能名为 `report`。在 `/` 菜单中选择 `report`，或输入：

```text
$report
```

技能设置了 `allow_implicit_invocation: false`。用户在当前会话显式启用后持续记录，每个新会话由用户重新启用。

初始化时，Codex 会将自带的[项目原则](skills/report/assets/project-principles.md)合并到目标项目的 `AGENTS.md`，并创建 `report/`。原则覆盖精简实现、GPU 优先、根因修复、肯定表达和训练性能，可在安装前查看模板。

## 本地查看示例

运行环境为 Python 3 和现代浏览器；报告脚本使用 Python 标准库。

```sh
git clone https://github.com/AlexHu-1215/project-report.git
cd project-report
python3 skills/report/scripts/report.py init --project .
python3 skills/report/scripts/report.py demo --project .
python3 skills/report/scripts/report.py serve --project . --port 8765
```

打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。服务监听本机地址，随启动它的进程运行。`demo` 在首次演示时生成示例阶段。

需要保存当前可离线浏览的页面时运行：

```sh
python3 skills/report/scripts/report.py build --project .
```

生成的 `report/index.html` 可直接打开，整个 `report/` 文件夹可一起移动。

## 接入真实实验

在启用 `/report` 的会话中说明本轮实验，Codex 会根据实际设置准备一份实验描述，并调用统一入口：

```sh
python3 "$SKILL_DIR/scripts/report.py" run --project "$PROJECT" --spec "$SPEC"
```

它会初始化项目、创建或复用阶段、分配运行 ID、保存配置与代码片段，然后启动命令并保存输出日志。`new` 可先登记待运行实验，`start` 启动已登记的实验。启动器根据实际退出结果记录完成、失败或取消；心跳中断超过 30 秒时，实时页面标记为“待确认”。长任务把整个启动器放进 tmux 或作业环境。

训练主 rank 将采集接到已有的进度与评估回调：

```python
from report import Run
record = Run.from_env()
record.update(step=step, metrics={"loss": loss})
record.update(metrics={"score": score}, metric_step=evaluated_step)
```

训练进程维护采样文件，启动器维护状态文件，Codex 通过 `annotate` 维护独立解释文件。各指标按实际采样步保存，文字解释与训练更新独立。既有运行记录继续读取。

完整实验描述、代码快照、远端运行和采集方式见[记录与启动约定](skills/report/references/recording.md)。

## 开发验证

```sh
python3 -B -m unittest discover -s tests -v
node tests/test_ui.cjs
```

回归用例覆盖真实进程的退出与取消、监测中断、并发登记、采样与解释的独立写入、指标采样步和基线比较。Python 运行脚本使用标准库，前端回归用例使用 Node.js 内置模块。

## 仓库内容

```text
skills/report/
  SKILL.md                    # Codex 工作流程
  agents/openai.yaml          # 手动启用配置
  scripts/report.py           # 登记、采集、进程监测、生成与本地服务
  assets/                     # 页面样式、交互、原则和示例数据
  references/recording.md      # 数据结构与实验接入说明
docs/preview.png              # 页面预览
```

项目生成的 `report/` 和本地 `AGENTS.md` 由 `.gitignore` 排除。用于分发的原则模板与示例数据保存在 skill 的 `assets/` 中。

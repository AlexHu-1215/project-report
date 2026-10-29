"""Run two actual regressors and record the complete reporting workflow."""
import json
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT / "skills/report/scripts/report.py"
sys.path.insert(0, str(SCRIPT.parent))
from report import collect, conclude, new_experiment, write_json, write_text

root = PROJECT / "report"
stage_id = "02-gpu-regression"
common = {"seed": 42, "steps": 300, "batch_size": 65536, "validation_size": 32768,
          "features": 8, "learning_rate": 0.05, "eval_every": 30, "noise_std": 0.05,
          "concurrent_runs": 2}
stage = {"id": stage_id, "title": "真实小实验：二次回归",
         "question": "在含二次项的合成数据上，加入二次特征能否降低验证误差？",
         "settings_summary": "实际训练 · 相同种子 · 300 steps · 两组并发",
         "common_config": common,
         "metrics": [{"key": "validation_mse", "label": "验证 MSE", "direction": "down", "digits": 6},
                     {"key": "train_mse", "label": "训练 MSE", "direction": "down", "digits": 6}]}
runs = []
for degree, name in [(1, "线性基线"), (2, "加入二次项")]:
    config = {**common, "degree": degree}
    if degree == 2:
        config["batch_size"] = 16384
    config_file = Path("report/inputs") / stage_id / f"degree-{degree}.json"
    write_json(PROJECT / config_file, config)
    description = {
        "stage": stage,
        "run": {"name": name, "changes": f"特征次数 {degree}", "config": config,
                "config_file": str(config_file), "total": common["steps"],
                "implementation": [{"file": "examples/tiny-regression/train.py", "start": 23,
                                     "end": 60, "description": "真实数据生成、特征构造、SGD 训练及验证计算"}]},
        "command": [sys.executable, "examples/tiny-regression/train.py", "--config", str(config_file)],
    }
    runs.append(new_experiment(PROJECT, description))
    if degree == 2:
        # The launch snapshot and runtime configuration capture this actual change.
        write_json(PROJECT / config_file, {**config, "batch_size": common["batch_size"]})
conclude(root, stage_id, "两个配置已登记，等待训练与最终评估。", [r.id for r in runs])
processes = [subprocess.Popen([sys.executable, "-B", str(SCRIPT), "start", "--project", str(PROJECT),
                              "--stage", stage_id, "--run", r.id]) for r in runs]
for process in processes:
    if process.wait() != 0:
        raise RuntimeError("实验退出异常，详情见报告日志")
snapshot = next(s for s in collect(root, stage_id)["stages"] if s["id"] == stage_id)
measured = {r["id"]: r for r in snapshot["runs"]}
a, b = [measured[r.id] for r in runs]
assert a["evaluation_ready"] and b["evaluation_ready"]
assert snapshot["conclusion_stale"]
assert b["planned_config"]["batch_size"] == 16384 and b["config"]["batch_size"] == 65536
validation_a, validation_b = a["final_metrics"]["validation_mse"], b["final_metrics"]["validation_mse"]
assert validation_b < validation_a
summary = f"加入二次项后，验证 MSE 从 {validation_a:.6f} 降至 {validation_b:.6f}；结果来自本次合成数据上的实际训练。"
for run, data in zip(runs, (a, b)):
    run.annotate(f"最终验证 MSE {data['final_metrics']['validation_mse']:.6f}，使用 {data['performance']['张量设备']} 训练与评估。")
notes = (f"## 发现与解释\n\n{summary}\n\n"
         "目标函数包含二次项。两组使用相同随机种子、样本数量和训练步数；二次特征扩大了模型可表达的函数范围。\n\n"
         "这次实验用于验证记录流程，数据为程序生成的合成数据。指标由实际训练和独立验证集计算。\n\n"
         "## 下一步\n\n- 增加随机种子复跑，评估误差分布。\n- 在实际研究任务中接入相同的配置、采样与最终评估接口。\n")
write_text(root / "data/stages" / f"{stage_id}.md", notes)
conclude(root, stage_id, summary, [r.id for r in runs])
verification = {"summary": summary, "new_results_marked_conclusion_stale": True,
                "planned_and_actual_batch_preserved": True,
                "runs": [{"id": r["id"], "metrics": r["final_metrics"], "config": r["config"],
                          "performance": r["performance"], "evaluation_ready": r["evaluation_ready"]} for r in (a, b)]}
write_json(root / "artifacts" / stage_id / "verification.json", verification)
print(json.dumps(verification, ensure_ascii=False, indent=2))

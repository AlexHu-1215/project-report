"""Train and evaluate a small polynomial regressor using actual GPU tensors."""
import argparse
import json
from pathlib import Path
import time

import torch
from report import Run

parser = argparse.ArgumentParser()
parser.add_argument("--config", type=Path, required=True)
args = parser.parse_args()
config = {"seed": 42, "degree": 1, "steps": 300, "batch_size": 65536,
          "validation_size": 32768, "features": 8, "learning_rate": 0.05,
          "eval_every": 30, "noise_std": 0.05, "concurrent_runs": 1, **json.loads(args.config.read_text())}
record = Run.from_env()
device = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
config["device"] = str(device)
record.configure(config)
torch.manual_seed(config["seed"])

def dataset(size):
    x = torch.rand(size, config["features"], device=device) * 4 - 2
    y = 0.8 * x[:, :1] - 0.4 * x[:, 1:2] + 0.6 * x[:, :1].square()
    y += config["noise_std"] * torch.randn(size, 1, device=device)
    return x, y

def features(x):
    return torch.cat([x, x.square()], dim=1) if config["degree"] == 2 else x

def synchronize():
    if device.type == "mps":
        torch.mps.synchronize()
    elif device.type == "cuda":
        torch.cuda.synchronize()

train_x, train_y = dataset(config["batch_size"])
val_x, val_y = dataset(config["validation_size"])
train_x, val_x = features(train_x), features(val_x)
model = torch.nn.Linear(train_x.shape[1], 1, device=device)
with torch.no_grad():
    model.weight.zero_()
    model.bias.zero_()
optimizer = torch.optim.SGD(model.parameters(), lr=config["learning_rate"])
synchronize()
start = time.perf_counter()
for step in range(1, config["steps"] + 1):
    optimizer.zero_grad(set_to_none=True)
    loss = (model(train_x) - train_y).square().mean()
    loss.backward()
    optimizer.step()
    if step % config["eval_every"] == 0:
        with torch.no_grad():
            validation_mse = (model(val_x) - val_y).square().mean().item()
        record.update(step=step, metrics={"validation_mse": validation_mse, "train_mse": loss.item()})
        print(json.dumps({"step": step, "validation_mse": validation_mse}), flush=True)
synchronize()
elapsed = time.perf_counter() - start
with torch.no_grad():
    final = {"validation_mse": (model(val_x) - val_y).square().mean().item(),
             "train_mse": (model(train_x) - train_y).square().mean().item()}
performance = {"GPU": "Apple M4 / MPS" if device.type == "mps" else str(device),
               "张量设备": str(train_x.device), "模型设备": str(model.weight.device),
               "训练耗时 (s)": round(elapsed, 3),
               "吞吐 (samples/s)": round(config["steps"] * config["batch_size"] / elapsed),
               "micro-batch": config["batch_size"], "梯度累积": 1,
               "全局 batch": config["batch_size"], "实验并发": config["concurrent_runs"]}
if device.type == "mps":
    performance["GPU 张量内存 (MiB)"] = round(torch.mps.current_allocated_memory() / 2**20, 2)
    performance["GPU 驱动内存 (MiB)"] = round(torch.mps.driver_allocated_memory() / 2**20, 2)
record.update(performance=performance)
output = record.root / "artifacts" / record.stage_id / record.id
output.mkdir(parents=True, exist_ok=True)
(output / "metrics.json").write_text(json.dumps({"metrics": final, "config": config, "performance": performance}, indent=2))
torch.save(model.state_dict(), output / "model.pt")
record.finalize(step=config["steps"], metrics=final)
print(json.dumps({"final_metrics": final, "performance": performance}), flush=True)

#!/usr/bin/env python3
"""Create experiment records, supervise commands, and render a local report."""

import argparse
from datetime import datetime, timezone
from functools import partial
from html import escape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import itertools
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import tempfile

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path, content, *, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".report-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        if exclusive:
            os.link(temporary, path)
        else:
            temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, data, *, exclusive=False):
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", exclusive=exclusive)


class Run:
    """Measurements, process state, and analysis each have one independent writer."""

    def __init__(self, report_dir, stage_id, run_id):
        self.root = Path(report_dir).resolve()
        self.stage_id, self.id = stage_id, run_id
        self.path = self.root / "data/runs" / stage_id / f"{run_id}.json"
        self.lifecycle_path = self.root / "data/lifecycle" / stage_id / f"{run_id}.json"
        self.analysis_path = self.root / "data/analysis" / stage_id / f"{run_id}.json"

    @classmethod
    def from_env(cls):
        path = Path(os.environ["PROJECT_REPORT_RUN"])
        return cls(path.parents[3], path.parent.name, path.stem)

    def create(self, *, name, changes, config, command, implementation, artifacts=None,
               total=0, status="planned", argv=None, cwd=None, stale_after_seconds=300):
        stamp = now()
        data = {
            "id": self.id, "name": name, "changes": changes, "status": status,
            "progress": {"step": 0, "total": total}, "metrics": {}, "metric_steps": {},
            "metric_times": {}, "history": [], "config": config, "command": command,
            "implementation": implementation, "artifacts": artifacts or [], "performance": {},
            "argv": argv, "cwd": cwd, "stale_after_seconds": stale_after_seconds,
            "started_at": stamp if status == "running" else None,
            "updated_at": stamp, "sampled_at": None,
        }
        write_json(self.path, data, exclusive=True)
        return self

    def update(self, *, step=None, total=None, metrics=None, metric_step=None, performance=None):
        data = read_json(self.path)
        stamp = now()
        if step is not None:
            data["progress"]["step"] = step
        if total is not None:
            data["progress"]["total"] = total
        if metrics:
            sampled_step = data["progress"]["step"] if metric_step is None else metric_step
            point = next((p for p in data["history"] if p["step"] == sampled_step), None)
            if point is None:
                point = {"step": sampled_step}
                data["history"].append(point)
                data["history"].sort(key=lambda p: p["step"])
            point.update(metrics)
            steps, times = data.setdefault("metric_steps", {}), data.setdefault("metric_times", {})
            for key, value in metrics.items():
                if sampled_step >= steps.get(key, -1):
                    data["metrics"][key] = value
                    steps[key], times[key] = sampled_step, stamp
        if performance is not None:
            data["performance"].update(performance)
        data["updated_at"] = data["sampled_at"] = stamp
        write_json(self.path, data)
        return self

    def annotate(self, comment, *, basis="measured"):
        write_json(self.analysis_path, {"comment": comment, "basis": basis, "analysis_at": now()})
        return self

    def finish(self, *, status="completed", exit_code=None, detail=""):
        """Finalize callback/scheduler-owned runs; the launcher finalizes supervised runs."""
        lifecycle = read_json(self.lifecycle_path) if self.lifecycle_path.exists() else {}
        lifecycle.update(status=status, finished_at=now(), status_updated_at=now(),
                         exit_code=exit_code, status_detail=detail)
        write_json(self.lifecycle_path, lifecycle)
        return self


def init_project(project):
    template = (ASSETS / "project-principles.md").read_text(encoding="utf-8")
    agents = project / "AGENTS.md"
    content = agents.read_text(encoding="utf-8") if agents.exists() else ""
    principles, agreement = template.split("# Report 会话约定", 1)
    rules = [line for line in principles.splitlines() if line.startswith("- ")]
    missing = [line for line in rules if line.split("：", 1)[0] not in content]
    if missing:
        content = content.rstrip() + "\n\n# 项目原则\n\n" + "\n".join(missing) + "\n"
    if "# Report 会话约定" not in content:
        content = content.rstrip() + "\n\n# Report 会话约定" + agreement
    agents.write_text(content.lstrip(), encoding="utf-8")
    root = project / "report"
    for folder in ("data/stages", "data/runs", "data/lifecycle", "data/analysis", "stages", "static", "assets"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    metadata = root / "data/project.json"
    if not metadata.exists():
        write_json(metadata, {"title": project.name, "description": "记录每一轮探索的进度、结果、设置与实现。",
                              "current_stage": None, "timezone": "Asia/Shanghai"})
    build(root)
    print(f"已初始化：{root}")


def new_experiment(project, spec):
    root = project / "report"
    if not (root / "data/project.json").exists():
        init_project(project)
    stage_spec = spec["stage"]
    stage_id = stage_spec if isinstance(stage_spec, str) else stage_spec["id"]
    stage_path = root / "data/stages" / f"{stage_id}.json"
    if not stage_path.exists():
        stage = {"state": "active", "demo": False, "baseline": "exp-001",
                 "summary": "等待首轮评估结果。", "analysis_at": None,
                 "settings_summary": "", "common_config": {}, "media": [], **stage_spec}
        try:
            write_text(stage_path.with_suffix(".md"), "## 发现与解释\n\n等待实验结果。\n\n## 下一步\n\n- 完成本轮评估。\n", exclusive=True)
        except FileExistsError:
            pass
        try:
            write_json(stage_path, stage, exclusive=True)
        except FileExistsError:
            pass
    details = dict(spec["run"])
    snippets = []
    for source in details["implementation"]:
        item = dict(source)
        if "start" in item:
            lines = (project / item["file"]).read_text(encoding="utf-8").splitlines()
            item["code"] = "\n".join(lines[item["start"] - 1:item["end"]])
        snippets.append(item)
    details["implementation"] = snippets
    argv = spec["command"]
    details.update(command=shlex.join(argv), argv=argv, cwd=str(project))
    artifacts = details.pop("artifacts", [])
    for number in itertools.count(1):
        run = Run(root, stage_id, f"exp-{number:03d}")
        log = root / "logs" / stage_id / f"{run.id}.log"
        try:
            run.create(**details, artifacts=[*artifacts, {"label": "进程输出", "path": str(log)}])
        except FileExistsError:
            continue
        break
    project_data = read_json(root / "data/project.json")
    project_data["current_stage"] = stage_id
    write_json(root / "data/project.json", project_data)
    build(root)
    print(f"已登记：{stage_id}/{run.id} → {root / 'stages' / stage_id / 'index.html'}", flush=True)
    return run


def launch(run, *, heartbeat_seconds=5):
    record = read_json(run.path)
    log_path = run.root / "logs" / run.stage_id / f"{run.id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = now()
    lifecycle = {"status": "running", "started_at": stamp, "status_updated_at": stamp,
                 "heartbeat_at": stamp, "stale_after_seconds": heartbeat_seconds * 6,
                 "monitor": {"kind": "process", "host": socket.gethostname(), "pid": os.getpid()},
                 "status_detail": "启动器持续监测进程"}
    write_json(run.lifecycle_path, lifecycle, exclusive=True)
    env = dict(os.environ, PROJECT_REPORT_RUN=str(run.path))
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent) + os.pathsep + env.get("PYTHONPATH", "")
    child = None
    cancelled = None

    def cancel(signum, _frame):
        nonlocal cancelled
        cancelled = signum
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signum)

    handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        with log_path.open("x", encoding="utf-8") as log:
            try:
                child = subprocess.Popen(record["argv"], cwd=record["cwd"], env=env,
                                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as error:
                run.finish(status="failed", exit_code=127, detail=f"启动失败：{error}")
                return 127
            lifecycle["monitor"]["child_pid"] = child.pid
            write_json(run.lifecycle_path, lifecycle)
            if cancelled is not None:
                cancel(cancelled, None)
            while True:
                try:
                    returncode = child.wait(timeout=heartbeat_seconds)
                    break
                except subprocess.TimeoutExpired:
                    lifecycle["heartbeat_at"] = now()
                    write_json(run.lifecycle_path, lifecycle)
            status = "cancelled" if cancelled is not None else "completed" if returncode == 0 else "failed"
            detail = f"进程退出码 {returncode}；输出见日志"
            run.finish(status=status, exit_code=returncode, detail=detail)
            return 128 + cancelled if cancelled is not None else 128 - returncode if returncode < 0 else returncode
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        build(run.root)


def collect_run(path, *, demo=False):
    data = read_json(path)
    run = Run(path.parents[3], path.parent.name, path.stem)
    data.setdefault("sampled_at", data["updated_at"] if data["progress"]["step"] or data["metrics"] else None)
    data.setdefault("metric_steps", {})
    data.setdefault("metric_times", {})
    data.setdefault("comment", "等待结果解释。")
    data.setdefault("basis", "pending")
    data.setdefault("analysis_at", None)
    lifecycle = read_json(run.lifecycle_path) if run.lifecycle_path.exists() else {}
    data.update({key: lifecycle[key] for key in ("status", "started_at", "finished_at", "status_updated_at",
                                                "exit_code", "status_detail", "monitor") if key in lifecycle})
    if run.analysis_path.exists():
        data.update(read_json(run.analysis_path))
    if data["status"] == "running" and not demo:
        stamp = lifecycle.get("heartbeat_at") or data["sampled_at"] or data["updated_at"]
        limit = lifecycle.get("stale_after_seconds", data.get("stale_after_seconds", 300))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()
        if age > limit:
            data["status"] = "unknown"
            data["status_detail"] = f"运行监测超过 {limit} 秒未更新，等待确认；最后记录 {stamp}"
    return data


def collect(root):
    project = read_json(root / "data/project.json")
    stages = []
    for path in sorted((root / "data/stages").glob("*.json")):
        stage = read_json(path)
        stage["notes"] = path.with_suffix(".md").read_text(encoding="utf-8")
        stage["runs"] = [collect_run(run, demo=stage["demo"])
                         for run in sorted((root / "data/runs" / stage["id"]).glob("*.json"))]
        stamps = [r.get(key) for r in stage["runs"] for key in ("updated_at", "status_updated_at", "analysis_at")]
        stage["updated_at"] = max(filter(None, stamps), key=datetime.fromisoformat, default=None)
        stages.append(stage)
    stamps = [stage["updated_at"] for stage in stages if stage["updated_at"]]
    return {"project": project, "stages": stages, "updated_at": max(stamps, key=datetime.fromisoformat, default=None)}


def page(snapshot, stage_id=None):
    prefix = "../../" if stage_id else "./"
    title = snapshot["project"]["title"]
    payload = json.dumps(snapshot, ensure_ascii=False).replace("<", "\\u003c")
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} · Project Report</title>
  <link rel="stylesheet" href="{prefix}static/app.css">
</head>
<body data-root="{prefix}" data-stage="{escape(stage_id or '')}">
  <div id="app"></div>
  <script id="report-snapshot" type="application/json">{payload}</script>
  <script src="{prefix}static/app.js" defer></script>
</body>
</html>
'''


def build(root):
    snapshot = collect(root)
    for filename in ("app.js", "app.css"):
        write_text(root / "static" / filename, (ASSETS / filename).read_text(encoding="utf-8"))
    write_text(root / "index.html", page(snapshot))
    for stage in snapshot["stages"]:
        write_text(root / "stages" / stage["id"] / "index.html", page(snapshot, stage["id"]))


def demo(root):
    example = read_json(ASSETS / "demo.json")
    stage = example["stage"]
    path = root / "data/stages" / f"{stage['id']}.json"
    write_text(path.with_suffix(".md"), example["notes"], exclusive=True)
    write_json(path, stage, exclusive=True)
    for data in example["runs"]:
        run = Run(root, stage["id"], data["id"])
        analysis = {key: data.pop(key) for key in ("comment", "basis", "analysis_at")}
        data["sampled_at"] = data["updated_at"] if data["metrics"] else None
        data["metric_steps"] = {key: data["progress"]["step"] for key in data["metrics"]}
        write_json(run.path, data, exclusive=True)
        write_json(run.analysis_path, analysis, exclusive=True)
    project = read_json(root / "data/project.json")
    project.update(title="Project Report", current_stage=stage["id"])
    write_json(root / "data/project.json", project)
    build(root)
    print(f"已生成示例：{root / 'index.html'}")


class ReportHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] == "/api/report":
            content = json.dumps(collect(Path(self.directory)), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            super().do_GET()


def main():
    parser = argparse.ArgumentParser(description="创建实验、监测进程和浏览项目报告")
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("init", "build", "serve", "demo", "new", "run", "start", "annotate"):
        command = commands.add_parser(action)
        command.add_argument("--project", type=Path, default=Path.cwd())
        if action == "serve":
            command.add_argument("--port", type=int, default=8765)
        if action in ("new", "run"):
            command.add_argument("--spec", type=Path, required=True)
        if action in ("start", "annotate"):
            command.add_argument("--stage", required=True)
            command.add_argument("--run", required=True)
        if action == "annotate":
            command.add_argument("--comment", required=True)
            command.add_argument("--basis", choices=("measured", "inferred", "pending"), default="measured")
    args = parser.parse_args()
    project = args.project.resolve()
    root = project / "report"
    if args.action == "init":
        init_project(project)
    elif args.action == "demo":
        demo(root)
    elif args.action == "build":
        build(root)
        print(f"已生成：{root / 'index.html'}")
    elif args.action in ("new", "run"):
        run = new_experiment(project, read_json(args.spec))
        if args.action == "run":
            raise SystemExit(launch(run))
    elif args.action == "start":
        raise SystemExit(launch(Run(root, args.stage, args.run)))
    elif args.action == "annotate":
        Run(root, args.stage, args.run).annotate(args.comment, basis=args.basis)
        build(root)
    else:
        build(root)
        handler = partial(ReportHandler, directory=str(root))
        with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
            print(f"Project Report → http://127.0.0.1:{args.port}", flush=True)
            server.serve_forever()


if __name__ == "__main__":
    main()

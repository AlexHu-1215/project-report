#!/usr/bin/env python3
"""Create experiment records, supervise commands, and render a local report."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from functools import partial
from html import escape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import itertools
import json
import math
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


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
    """One append-only sampler, one process monitor, and one analysis writer."""

    def __init__(self, report_dir, stage_id, run_id):
        self.root = Path(report_dir).resolve()
        self.stage_id, self.id = stage_id, run_id
        self.path = self.root / "data/runs" / stage_id / f"{run_id}.json"
        self.events_path = self.root / "data/events" / stage_id / f"{run_id}.jsonl"
        self.launch_path = self.root / "data/launch" / stage_id / f"{run_id}.json"
        self.lifecycle_path = self.root / "data/lifecycle" / stage_id / f"{run_id}.json"
        self.analysis_path = self.root / "data/analysis" / stage_id / f"{run_id}.json"

    @classmethod
    def from_env(cls):
        path = Path(os.environ["PROJECT_REPORT_RUN"])
        return cls(path.parents[3], path.parent.name, path.stem)

    def create(self, *, name, changes, config, command, implementation, artifacts=None,
               total=0, status="planned", argv=None, cwd=None, stale_after_seconds=300,
               config_file=None, implementation_sources=None):
        stamp = now()
        data = {
            "id": self.id, "name": name, "changes": changes, "status": status,
            "progress": {"step": 0, "total": total}, "metrics": {}, "history": [],
            "config": config, "command": command, "implementation": implementation,
            "implementation_sources": implementation_sources or implementation,
            "config_file": config_file, "artifacts": artifacts or [], "performance": {},
            "argv": argv, "cwd": cwd, "stale_after_seconds": stale_after_seconds,
            "started_at": stamp if status == "running" else None,
            "updated_at": stamp, "sampled_at": None,
        }
        write_json(self.path, data, exclusive=True)
        return self

    def _append(self, event):
        event["at"] = now()
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        return self

    def configure(self, config):
        """Record the configuration resolved by the actual program."""
        return self._append({"kind": "config", "config": config})

    def update(self, *, step=None, total=None, metrics=None, metric_step=None, performance=None):
        return self._append({"kind": "sample", "step": step, "total": total,
                             "metrics": metrics or {}, "metric_step": metric_step,
                             "performance": performance or {}})

    def finalize(self, *, metrics, step):
        """Commit the actual final evaluation, including legitimate early stopping."""
        return self._append({"kind": "final", "step": step, "metrics": metrics})

    def annotate(self, comment, *, basis="measured"):
        write_json(self.analysis_path, {"comment": comment, "basis": basis, "analysis_at": now()})
        return self

    def finish(self, *, status="completed", exit_code=None, detail=""):
        """The process owner records exit state independently from final evaluation."""
        lifecycle = read_json(self.lifecycle_path) if self.lifecycle_path.exists() else {}
        lifecycle.update(status=status, finished_at=now(), status_updated_at=now(),
                         exit_code=exit_code, status_detail=detail)
        write_json(self.lifecycle_path, lifecycle)
        return self


def snapshot_sources(project, sources):
    snippets = []
    for source in sources:
        item = dict(source)
        if "start" in item:
            lines = (project / item["file"]).read_text(encoding="utf-8").splitlines()
            item["code"] = "\n".join(lines[item["start"] - 1:item["end"]])
        snippets.append(item)
    return snippets


def apply_event(state, event, revision):
    data, history = state["data"], state["history"]
    stamp = event["at"]
    data["updated_at"] = stamp
    if event["kind"] == "config":
        data.update(config=event["config"], config_confirmed=True, config_at=stamp,
                    config_origin="runtime", final_evaluation=None, result_revision=revision)
        return
    data["sampled_at"] = stamp
    if event["step"] is not None:
        data["progress"]["step"] = event["step"]
    if event.get("total") is not None:
        data["progress"]["total"] = event["total"]
    data["performance"].update(event.get("performance", {}))
    metrics = event["metrics"]
    if event["kind"] == "sample" and (metrics or event["step"] is not None):
        data["final_evaluation"] = None
    if metrics:
        step = event.get("metric_step")
        step = data["progress"]["step"] if step is None else step
        history.setdefault(step, {"step": step}).update(metrics)
        for key, value in metrics.items():
            if step >= data["metric_steps"].get(key, -1):
                data["metrics"][key] = value
                data["metric_steps"][key], data["metric_times"][key] = step, stamp
        data["result_revision"] = revision
        data["evaluation_at"] = stamp
    if event["kind"] == "final":
        data["final_evaluation"] = {"metrics": metrics, "step": event["step"],
                                    "recorded_at": stamp, "revision": revision}


class ReportStore:
    """Reuse folded state and read only newly appended complete event lines."""

    def __init__(self, root):
        self.root = Path(root)
        self.cache = {}
        self.lock = threading.RLock()

    def read(self, path, *, demo=False, history=True):
        with self.lock:
            return self._read(Path(path).resolve(), demo=demo, history=history)

    def _read(self, path, *, demo, history):
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        run = Run(self.root, path.parent.name, path.stem)
        state = self.cache.get(path)
        if state is None or state["signature"] != signature:
            data = read_json(path)
            points = {p["step"]: p for p in data.pop("history", [])}
            data.setdefault("metric_steps", {})
            data.setdefault("metric_times", {})
            data.setdefault("sampled_at", data["updated_at"] if data["metrics"] else None)
            data.setdefault("final_evaluation", None)
            data.setdefault("evaluation_at", data["sampled_at"] if data["metrics"] else None)
            data.setdefault("result_revision", data["evaluation_at"] or 0)
            data.update(planned_config=deepcopy(data["config"]),
                        planned_implementation=deepcopy(data["implementation"]),
                        config_confirmed=False, config_origin="planned")
            state = {"signature": signature, "data": data, "history": points, "offset": 0}
            self.cache[path] = state
        data = state["data"]
        if run.events_path.exists():
            with run.events_path.open("rb") as stream:
                stream.seek(state["offset"])
                body = stream.read()
            complete = body[:body.rfind(b"\n") + 1]
            for line in complete.splitlines(keepends=True):
                state["offset"] += len(line)
                apply_event(state, json.loads(line), state["offset"])
        result = deepcopy(data)
        if run.launch_path.exists():
            launched = read_json(run.launch_path)
            result.update(implementation=launched["implementation"], launch_at=launched["recorded_at"])
            if not result["config_confirmed"]:
                result.update(config=launched["config"], config_origin=launched["config_origin"])
        result.setdefault("comment", "等待结果解释。")
        result.setdefault("basis", "pending")
        result.setdefault("analysis_at", None)
        lifecycle = read_json(run.lifecycle_path) if run.lifecycle_path.exists() else {}
        result.update({key: lifecycle[key] for key in ("status", "started_at", "finished_at", "status_updated_at",
                       "exit_code", "status_detail", "monitor") if key in lifecycle})
        if run.analysis_path.exists():
            result.update(read_json(run.analysis_path))
        if result["status"] == "running" and not demo:
            stamp = lifecycle.get("heartbeat_at") or result["sampled_at"] or result["updated_at"]
            limit = lifecycle.get("stale_after_seconds", result.get("stale_after_seconds", 300))
            if (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds() > limit:
                result.update(status="unknown", status_detail=f"运行监测超过 {limit} 秒未更新，等待确认")
        final = result["final_evaluation"]
        result["evaluation_ready"] = bool(final)
        result["final_metrics"] = final["metrics"] if final else {}
        if history:
            points = [state["history"][step] for step in sorted(state["history"])]
            result["history"] = deepcopy(plot_history(points, history) if isinstance(history, str) else points)
        return result


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
    for folder in ("data/stages", "data/runs", "data/events", "data/launch", "data/lifecycle", "data/analysis", "stages", "static", "assets"):
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
    details["implementation_sources"] = deepcopy(details["implementation"])
    details["implementation"] = snapshot_sources(project, details["implementation_sources"])
    argv = spec["command"]
    details.update(command=shlex.join(argv), argv=argv, cwd=str(project))
    artifacts = details.pop("artifacts", [])
    for number in itertools.count(1):
        run = Run(root, stage_id, f"exp-{number:03d}")
        log = root / "logs" / stage_id / f"{run.id}.log"
        try:
            run.create(**details, artifacts=[*artifacts, {"label": "进程输出", "path": str(log)}, {"label": "原始采样", "path": str(run.events_path)}])
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
                project = Path(record["cwd"])
                config_file = record.get("config_file")
                write_json(run.launch_path, {
                    "recorded_at": now(), "config": read_json(project / config_file) if config_file else record["config"],
                    "config_origin": "launch_file" if config_file else "declared",
                    "implementation": snapshot_sources(project, record["implementation_sources"]),
                }, exclusive=True)
                child = subprocess.Popen(record["argv"], cwd=record["cwd"], env=env,
                                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            except (OSError, json.JSONDecodeError) as error:
                log.write(f"启动失败：{error}\n")
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


def collect_run(path, *, demo=False, store=None, history=True):
    return (store or ReportStore(path.parents[3])).read(path, demo=demo, history=history)


def plot_history(points, key, limit=240):
    selected = [p for p in points if key in p]
    if len(selected) <= limit:
        return selected
    return [selected[round(i * (len(selected) - 1) / (limit - 1))] for i in range(limit)]


def collect(root, stage_id=None, *, full=False, store=None):
    root = Path(root).resolve()
    store = store or ReportStore(root)
    project = read_json(root / "data/project.json")
    stages = []
    for path in sorted((root / "data/stages").glob("*.json")):
        stage = read_json(path)
        if stage_id is not None and stage["id"] != stage_id and not full:
            stage.update(runs=[], notes="", run_count=len(list((root / "data/runs" / stage["id"]).glob("*.json"))),
                         updated_at=stage.get("analysis_at"))
            for key in ("common_config", "media", "reviewed_results", "evidence"):
                stage.pop(key, None)
            stages.append(stage)
            continue
        detailed = full or stage["id"] == stage_id
        stage["notes"] = path.with_suffix(".md").read_text(encoding="utf-8") if detailed else ""
        runs = [collect_run(p, demo=stage["demo"], store=store, history=stage["metrics"][0]["key"] if detailed else False)
                for p in sorted((root / "data/runs" / stage["id"]).glob("*.json"))]
        required = [m["key"] for m in stage["metrics"]]
        for run in runs:
            run["evaluation_ready"] = run["evaluation_ready"] and all(
                isinstance(run["final_metrics"].get(key), (int, float))
                and math.isfinite(run["final_metrics"][key]) for key in required)
        reviewed = stage.get("reviewed_results", {})
        stage["unreviewed_runs"] = [r["id"] for r in runs if r["metrics"] and
                                    reviewed.get(r["id"]) != r["result_revision"]]
        stage["conclusion_stale"] = bool(stage["unreviewed_runs"])
        stage.setdefault("evidence", [])
        stamps = [r.get(key) for r in runs for key in ("updated_at", "status_updated_at", "analysis_at")]
        stamps.append(stage.get("analysis_at"))
        stage["updated_at"] = max(filter(None, stamps), key=datetime.fromisoformat, default=None)
        if detailed:
            for run in runs:
                run["history"] = plot_history(run["history"], stage["metrics"][0]["key"])
            stage["runs"] = runs
        else:
            keep = ("id", "name", "changes", "status", "progress", "metrics", "metric_steps", "sampled_at",
                    "updated_at", "evaluation_ready", "final_metrics", "comment", "status_detail")
            stage["runs"] = [{k: r[k] for k in keep if k in r} for r in runs]
            for key in ("common_config", "media"):
                stage.pop(key, None)
        if stage_id is not None and not detailed:
            stage["run_count"] = len(runs)
            stage["runs"] = []
        stages.append(stage)
    stamps = [s["updated_at"] for s in stages if s["updated_at"]]
    return {"project": project, "stages": stages, "updated_at": max(stamps, key=datetime.fromisoformat, default=None)}


def conclude(root, stage_id, summary, run_ids):
    stage_path = root / "data/stages" / f"{stage_id}.json"
    stage = read_json(stage_path)
    runs = {p.stem: collect_run(p, history=False)
            for p in (root / "data/runs" / stage_id).glob("*.json")}
    evidence = [{"run_id": run_id, "revision": runs[run_id]["result_revision"],
                 "metrics": runs[run_id]["final_metrics"] or runs[run_id]["metrics"],
                 "evaluation_at": runs[run_id]["evaluation_at"]} for run_id in run_ids]
    stage.update(summary=summary, analysis_at=now(), evidence=evidence,
                 reviewed_results={key: r["result_revision"] for key, r in runs.items()})
    write_json(stage_path, stage)
    build(root)
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


def scoped_snapshot(snapshot, stage_id=None):
    result = {"project": snapshot["project"], "updated_at": snapshot["updated_at"], "stages": []}
    for source in snapshot["stages"]:
        stage = deepcopy({k: v for k, v in source.items() if k != "runs"})
        if stage_id is not None and stage["id"] != stage_id:
            stage.update(run_count=len(source["runs"]), runs=[], notes="")
        elif stage_id is None:
            keep = ("id", "name", "changes", "status", "progress", "metrics", "metric_steps", "sampled_at",
                    "evaluation_ready", "final_metrics", "status_detail", "comment")
            stage["runs"] = [{k: r[k] for k in keep if k in r} for r in source["runs"]]
            stage["notes"] = ""
        else:
            stage["runs"] = deepcopy(source["runs"])
        if stage["id"] != stage_id:
            for key in ("common_config", "media"):
                stage.pop(key, None)
        result["stages"].append(stage)
    return result


def build(root):
    snapshot = collect(root, full=True)
    for filename in ("app.js", "app.css"):
        write_text(root / "static" / filename, (ASSETS / filename).read_text(encoding="utf-8"))
    write_text(root / "index.html", page(scoped_snapshot(snapshot)))
    for stage in snapshot["stages"]:
        write_text(root / "stages" / stage["id"] / "index.html", page(scoped_snapshot(snapshot, stage["id"]), stage["id"]))


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
        if data["status"] == "completed":
            data["final_evaluation"] = {"metrics": data["metrics"], "step": data["progress"]["step"],
                                        "recorded_at": data["updated_at"], "revision": data["updated_at"]}
        write_json(run.path, data, exclusive=True)
        write_json(run.analysis_path, analysis, exclusive=True)
    project = read_json(root / "data/project.json")
    project.update(title="Project Report", current_stage=stage["id"])
    write_json(root / "data/project.json", project)
    build(root)
    print(f"已生成示例：{root / 'index.html'}")


class ReportHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, store, **kwargs):
        self.store = store
        super().__init__(*args, **kwargs)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/api/report":
            stage_id = parse_qs(url.query).get("stage", [None])[0]
            content = json.dumps(collect(Path(self.directory), stage_id, store=self.store), ensure_ascii=False).encode("utf-8")
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
    for action in ("init", "build", "serve", "demo", "new", "run", "start", "annotate", "conclude"):
        command = commands.add_parser(action)
        command.add_argument("--project", type=Path, default=Path.cwd())
        if action == "serve":
            command.add_argument("--port", type=int, default=8765)
        if action in ("new", "run"):
            command.add_argument("--spec", type=Path, required=True)
        if action in ("start", "annotate", "conclude"):
            command.add_argument("--stage", required=True)
        if action in ("start", "annotate"):
            command.add_argument("--run", required=True)
        if action == "conclude":
            command.add_argument("--summary", required=True)
            command.add_argument("--runs", nargs="+", required=True)
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
    elif args.action == "conclude":
        conclude(root, args.stage, args.summary, args.runs)
    else:
        build(root)
        handler = partial(ReportHandler, directory=str(root), store=ReportStore(root))
        with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
            print(f"Project Report → http://127.0.0.1:{server.server_address[1]}", flush=True)
            server.serve_forever()


if __name__ == "__main__":
    main()

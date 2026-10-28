#!/usr/bin/env python3
"""Project Report: experiment records, static pages, and live progress."""

import argparse
from datetime import datetime, timezone
from functools import partial
from html import escape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class Run:
    """One writer per run; each experiment has its own data file."""

    def __init__(self, report_dir, stage_id, run_id):
        self.path = Path(report_dir).resolve() / "data" / "runs" / stage_id / f"{run_id}.json"

    def create(self, *, name, changes, config, command, implementation, artifacts=None,
               total=0, status="running"):
        stamp = now()
        data = {
            "id": self.path.stem, "name": name, "changes": changes,
            "status": status, "progress": {"step": 0, "total": total},
            "metrics": {}, "history": [], "config": config, "command": command,
            "implementation": implementation, "artifacts": artifacts or [],
            "performance": {}, "comment": "等待评估结果", "basis": "pending",
            "started_at": stamp, "updated_at": stamp, "analysis_at": None,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        return self

    def update(self, *, step=None, total=None, metrics=None, performance=None, **fields):
        data = read_json(self.path)
        if step is not None:
            data["progress"]["step"] = step
        if total is not None:
            data["progress"]["total"] = total
        if metrics is not None:
            data["metrics"].update(metrics)
            point = {"step": data["progress"]["step"], **data["metrics"]}
            if data["history"] and data["history"][-1]["step"] == point["step"]:
                data["history"][-1] = point
            else:
                data["history"].append(point)
            data["basis"] = "measured"
        if performance is not None:
            data["performance"].update(performance)
        data.update(fields)
        data["updated_at"] = now()
        write_json(self.path, data)
        return self

    def finish(self, *, metrics=None, comment=None, status="completed"):
        fields = {"status": status, "finished_at": now()}
        if comment is not None:
            fields.update(comment=comment, analysis_at=now())
        return self.update(metrics=metrics, **fields)


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
    for folder in ("data/stages", "data/runs", "stages", "static", "assets"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    metadata = root / "data/project.json"
    if not metadata.exists():
        write_json(metadata, {"title": project.name, "description": "记录每一轮探索的进度、结果、设置与实现。",
                              "current_stage": None, "timezone": "Asia/Shanghai"})
    build(root)
    print(f"已初始化：{root}")


def collect(root):
    project = read_json(root / "data/project.json")
    stages = []
    for path in sorted((root / "data/stages").glob("*.json")):
        stage = read_json(path)
        stage["notes"] = path.with_suffix(".md").read_text(encoding="utf-8")
        stage["runs"] = [read_json(run) for run in sorted((root / "data/runs" / stage["id"]).glob("*.json"))]
        stage["updated_at"] = max((r["updated_at"] for r in stage["runs"]), key=datetime.fromisoformat, default=None)
        stages.append(stage)
    timestamps = [run["updated_at"] for stage in stages for run in stage["runs"]]
    return {"project": project, "stages": stages, "updated_at": max(timestamps, key=datetime.fromisoformat, default=None)}


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
    (root / "static").mkdir(exist_ok=True)
    for filename in ("app.js", "app.css"):
        shutil.copyfile(ASSETS / filename, root / "static" / filename)
    (root / "index.html").write_text(page(snapshot), encoding="utf-8")
    for stage in snapshot["stages"]:
        directory = root / "stages" / stage["id"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.html").write_text(page(snapshot, stage["id"]), encoding="utf-8")


def demo(root):
    example = read_json(ASSETS / "demo.json")
    stage = example["stage"]
    path = root / "data/stages" / f"{stage['id']}.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(stage, stream, ensure_ascii=False, indent=2)
    path.with_suffix(".md").write_text(example["notes"], encoding="utf-8")
    for run in example["runs"]:
        write_json(root / "data/runs" / stage["id"] / f"{run['id']}.json", run)
    project = read_json(root / "data/project.json")
    project.update(title="Project Report", current_stage=stage["id"])
    write_json(root / "data/project.json", project)
    build(root)
    print(f"已生成示例：{root / 'stages' / stage['id'] / 'index.html'}")


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
    parser = argparse.ArgumentParser(description="初始化、生成和浏览项目实验报告")
    parser.add_argument("action", choices=("init", "build", "serve", "demo"))
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--port", type=int, default=8765)
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
    else:
        build(root)
        handler = partial(ReportHandler, directory=str(root))
        with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
            print(f"Project Report → http://127.0.0.1:{args.port}", flush=True)
            server.serve_forever()


if __name__ == "__main__":
    main()

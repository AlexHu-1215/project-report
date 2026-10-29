"""Regression tests for sampling, ownership, registration and real process exits."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/report/scripts/report.py"
spec = importlib.util.spec_from_file_location("report", SCRIPT)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="project-report-test-")
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.root = self.project / "report"
        (self.project / "train.py").write_text("# configuration\nloss = score + penalty\noptimizer.step()\n")

    def description(self, code="print('done')"):
        return {
            "stage": {"id": "01-study", "title": "实验", "question": "比较结果",
                      "metrics": [{"key": "score", "label": "得分", "direction": "up", "digits": 3}]},
            "run": {"name": "baseline", "changes": "default", "config": {"seed": 42}, "total": 1000,
                    "implementation": [{"file": "train.py", "start": 2, "end": 3, "description": "loss"}]},
            "command": [sys.executable, "-c", code],
        }

    def prepare(self, code="print('done')"):
        with contextlib.redirect_stdout(io.StringIO()):
            return report.new_experiment(self.project, self.description(code))

    def command(self, action, *args):
        return [sys.executable, "-B", str(SCRIPT), action, "--project", str(self.project), *args]

    def launch(self, code):
        run = self.prepare(code)
        process = subprocess.Popen(self.command("start", "--stage", run.stage_id, "--run", run.id),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)
        self.addCleanup(cleanup)
        return run, process

    def wait_started(self, run, process):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if run.lifecycle_path.exists():
                lifecycle = report.read_json(run.lifecycle_path)
                if lifecycle.get("monitor", {}).get("child_pid") and report.collect_run(run.path)["sampled_at"]:
                    return lifecycle
            self.assertIsNone(process.poll(), "launcher exited before the worker started")
            time.sleep(0.02)
        self.fail("worker did not start")

    def test_unified_registration_preserves_settings_code_and_existing_instructions(self):
        (self.project / "AGENTS.md").write_text("# Existing\n\nKeep these project instructions.\n")
        run = self.prepare()
        first = report.read_json(run.path)
        self.assertEqual(run.id, "exp-001")
        self.assertEqual(first["config"], {"seed": 42})
        self.assertEqual(first["implementation"][0]["code"], "loss = score + penalty\noptimizer.step()")
        (self.project / "train.py").write_text("modified source\n")
        self.assertEqual(report.read_json(run.path)["implementation"], first["implementation"])
        description = self.description()
        description["stage"] = "01-study"
        description["run"]["implementation"] = []
        with contextlib.redirect_stdout(io.StringIO()):
            second = report.new_experiment(self.project, description)
        self.assertEqual(second.id, "exp-002")
        snapshot = report.collect(self.root)
        self.assertEqual(snapshot["project"]["current_stage"], "01-study")
        self.assertEqual(snapshot["stages"][0]["baseline"], run.id)
        self.assertEqual(len(snapshot["stages"][0]["runs"]), 2)
        self.assertTrue((self.root / "stages/01-study/index.html").is_file())
        self.assertIn("Keep these project instructions.", (self.project / "AGENTS.md").read_text())

    def test_samples_keep_their_own_step_and_delayed_evaluations(self):
        run = self.prepare()
        run.update(step=100, metrics={"score": 0.8})
        run.update(step=200, metrics={"loss": 0.2})
        run.update(metrics={"score": 0.82}, metric_step=150)
        run.update(metrics={"score": 0.7}, metric_step=50)
        run.update(metrics={"accuracy": 0.9}, metric_step=200)
        data = report.collect_run(run.path)
        self.assertEqual(data["history"], [
            {"step": 50, "score": 0.7}, {"step": 100, "score": 0.8},
            {"step": 150, "score": 0.82}, {"step": 200, "loss": 0.2, "accuracy": 0.9},
        ])
        self.assertEqual(data["metric_steps"], {"score": 150, "loss": 200, "accuracy": 200})
        self.assertEqual(data["metrics"]["score"], 0.82)
        self.assertEqual(data["progress"]["step"], 200)

    def test_concurrent_analysis_and_training_preserve_both_writers(self):
        run = self.prepare()
        def samples():
            for step in range(1, 41):
                run.update(step=step, metrics={"score": step / 100})
        def explanations():
            for index in range(1, 41):
                run.annotate(f"解释 {index}", basis="inferred")
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda f: f(), [samples, explanations]))
        combined = report.collect_run(run.path)
        self.assertEqual(combined["comment"], "解释 40")
        self.assertEqual(combined["basis"], "inferred")
        self.assertEqual(combined["progress"]["step"], 40)
        self.assertEqual(len(combined["history"]), 40)
        self.assertNotIn("comment", report.read_json(run.path))
        sampled_at = combined["sampled_at"]
        run.annotate("新的解释")
        self.assertEqual(report.collect_run(run.path)["sampled_at"], sampled_at)

    def test_existing_records_use_separate_analysis(self):
        run = self.prepare()
        data = report.read_json(run.path)
        for key in ("sampled_at", "metric_steps", "metric_times"):
            data.pop(key, None)
        data.update(comment="旧解释", basis="pending", analysis_at=None)
        report.write_json(run.path, data)
        run.annotate("独立解释", basis="inferred")
        run.update(step=200, metrics={"score": 0.8})
        self.assertEqual(report.collect_run(run.path)["comment"], "独立解释")
        self.assertEqual(report.collect_run(run.path)["metric_steps"]["score"], 200)

    def test_parallel_registration_creates_stage_and_distinct_rows(self):
        with contextlib.redirect_stdout(io.StringIO()):
            report.init_project(self.project)
        description = self.description()
        spec_path = self.project / "experiment.json"
        report.write_json(spec_path, description)
        processes = [subprocess.Popen(self.command("new", "--spec", str(spec_path)),
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(5)]
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, stderr.decode())
        stage = report.collect(self.root)["stages"][0]
        self.assertEqual([r["id"] for r in stage["runs"]], [f"exp-{i:03d}" for i in range(1, 6)])
        self.assertEqual(stage["baseline"], "exp-001")

    def test_run_command_captures_success_and_actual_progress(self):
        description = self.description("from report import Run; Run.from_env().finalize(step=1000, metrics={'score':0.91}); print('evaluation saved')")
        spec_path = self.project / "experiment.json"
        report.write_json(spec_path, description)
        result = subprocess.run(self.command("run", "--spec", str(spec_path)), capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        run = report.Run(self.root, "01-study", "exp-001")
        data = report.collect_run(run.path)
        self.assertEqual((data["status"], data["exit_code"], data["progress"]["step"]), ("completed", 0, 1000))
        self.assertTrue(data["evaluation_ready"])
        self.assertIn("evaluation saved", (self.root / "logs/01-study/exp-001.log").read_text())
        self.assertIn('"status": "completed"', (self.root / "index.html").read_text())

    def test_nonzero_exit_and_launch_failure(self):
        run, process = self.launch("from report import Run; Run.from_env().update(step=20); raise RuntimeError('training failed')")
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 1, stderr)
        data = report.collect_run(run.path)
        self.assertEqual((data["status"], data["exit_code"]), ("failed", 1))
        self.assertIn("training failed", (self.root / "logs/01-study/exp-001.log").read_text())
        description = self.description()
        description["command"] = [str(self.project / "missing-executable")]
        with contextlib.redirect_stdout(io.StringIO()):
            missing = report.new_experiment(self.project, description)
        result = subprocess.run(self.command("start", "--stage", missing.stage_id, "--run", missing.id), capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 127)
        self.assertEqual(report.collect_run(missing.path)["status"], "failed")

    def test_killed_child_becomes_failed_and_keeps_last_progress(self):
        run, process = self.launch("from report import Run; import time; Run.from_env().update(step=100); time.sleep(120)")
        lifecycle = self.wait_started(run, process)
        os.kill(lifecycle["monitor"]["child_pid"], signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 137, stderr)
        data = report.collect_run(run.path)
        self.assertEqual((data["status"], data["exit_code"], data["progress"]["step"]), ("failed", -9, 100))

    def test_cancel_launcher_forwards_signal_and_finalizes(self):
        run, process = self.launch("from report import Run; import time; Run.from_env().update(step=50); time.sleep(120)")
        self.wait_started(run, process)
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 143, stderr)
        self.assertEqual(report.collect_run(run.path)["status"], "cancelled")

    def test_long_running_command_updates_monitor_heartbeat(self):
        run, process = self.launch("from report import Run; import time; Run.from_env().update(step=10); time.sleep(7)")
        first = self.wait_started(run, process)
        deadline = time.monotonic() + 6
        while report.read_json(run.lifecycle_path)["heartbeat_at"] == first["heartbeat_at"]:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.1)
        self.assertEqual(report.collect_run(run.path)["status"], "running")
        self.assertEqual(report.collect_run(run.path)["progress"]["step"], 10)
        process.communicate(timeout=5)
        self.assertEqual(report.collect_run(run.path)["status"], "completed")

    def test_lost_monitor_is_unknown_and_live_heartbeat_recovers(self):
        run, process = self.launch("from report import Run; import time; Run.from_env().update(step=25); time.sleep(120)")
        lifecycle = self.wait_started(run, process)
        child_pid = lifecycle["monitor"]["child_pid"]
        try:
            process.kill()
            process.communicate(timeout=10)
            lifecycle["heartbeat_at"] = (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat()
            report.write_json(run.lifecycle_path, lifecycle)
            self.assertEqual(report.collect_run(run.path)["status"], "unknown")
            lifecycle["heartbeat_at"] = report.now()
            report.write_json(run.lifecycle_path, lifecycle)
            self.assertEqual(report.collect_run(run.path)["status"], "running")
        finally:
            os.killpg(child_pid, signal.SIGTERM)

    def test_clean_exit_keeps_intermediate_result_out_of_final_comparison(self):
        run, process = self.launch("from report import Run; Run.from_env().update(step=100, metrics={'score':0.8})")
        process.communicate(timeout=10)
        data = report.collect(self.root, run.stage_id)["stages"][0]["runs"][0]
        self.assertEqual((data["status"], data["progress"]["step"]), ("completed", 100))
        self.assertFalse(data["evaluation_ready"])
        self.assertEqual(data["final_metrics"], {})

    def test_explicit_final_evaluation_supports_early_stopping_and_requires_metrics(self):
        run = self.prepare()
        run.update(step=100, metrics={"score": 0.8})
        run.finalize(step=100, metrics={"loss": 0.2})
        run.finish()
        data = report.collect(self.root, run.stage_id)["stages"][0]["runs"][0]
        self.assertFalse(data["evaluation_ready"])
        run.finalize(step=100, metrics={"score": 0.9})
        data = report.collect(self.root, run.stage_id)["stages"][0]["runs"][0]
        self.assertTrue(data["evaluation_ready"])
        self.assertEqual(data["progress"], {"step": 100, "total": 1000})
        self.assertEqual(data["final_metrics"], {"score": 0.9})
        run.update(step=200, metrics={"score": 0.91})
        self.assertFalse(report.collect_run(run.path)["evaluation_ready"])

    def test_launch_captures_current_files_and_runtime_resolved_config(self):
        config_path = self.project / "config.json"
        report.write_json(config_path, {"learning_rate": 0.1})
        description = self.description("from report import Run; import json; Run.from_env().configure({'learning_rate':json.load(open('config.json'))['learning_rate'], 'default_resolved':True})")
        description["run"]["config_file"] = "config.json"
        description["run"]["config"] = {"learning_rate": 0.1}
        with contextlib.redirect_stdout(io.StringIO()):
            run = report.new_experiment(self.project, description)
        report.write_json(config_path, {"learning_rate": 0.2})
        (self.project / "train.py").write_text("# current\nloss = updated_loss\noptimizer.step()\n")
        result = subprocess.run(self.command("start", "--stage", run.stage_id, "--run", run.id), capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = report.collect_run(run.path)
        self.assertEqual(data["planned_config"], {"learning_rate": 0.1})
        self.assertEqual(data["config"], {"learning_rate": 0.2, "default_resolved": True})
        self.assertTrue(data["config_confirmed"])
        self.assertIn("updated_loss", data["implementation"][0]["code"])
        self.assertIn("score + penalty", data["planned_implementation"][0]["code"])
        self.assertEqual(report.read_json(run.launch_path)["config"], {"learning_rate": 0.2})

    def test_invalid_launch_configuration_records_failed_state(self):
        description = self.description()
        description["run"]["config_file"] = "missing-config.json"
        with contextlib.redirect_stdout(io.StringIO()):
            run = report.new_experiment(self.project, description)
        result = subprocess.run(self.command("start", "--stage", run.stage_id, "--run", run.id), capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 127)
        self.assertEqual(report.collect_run(run.path)["status"], "failed")
        self.assertIn("missing-config.json", (self.root / "logs/01-study/exp-001.log").read_text())

    def test_conclusion_links_evidence_and_detects_subsequent_results(self):
        first = self.prepare()
        first.finalize(step=100, metrics={"score": 0.8})
        first.finish()
        second = self.prepare()
        second.finalize(step=100, metrics={"score": 0.9})
        second.finish()
        report.conclude(self.root, first.stage_id, "Second improves score", [first.id, second.id])
        stage = report.collect(self.root, first.stage_id)["stages"][0]
        self.assertFalse(stage["conclusion_stale"])
        self.assertEqual([e["run_id"] for e in stage["evidence"]], [first.id, second.id])
        self.assertEqual(stage["evidence"][1]["metrics"], {"score": 0.9})
        second.finalize(step=120, metrics={"score": 0.95})
        self.assertEqual(report.collect(self.root, first.stage_id)["stages"][0]["unreviewed_runs"], [second.id])
        report.conclude(self.root, first.stage_id, "Updated conclusion", [first.id, second.id])
        third = self.prepare()
        third.update(step=20, metrics={"score": 0.7})
        self.assertEqual(report.collect(self.root, first.stage_id)["stages"][0]["unreviewed_runs"], [third.id])

    def test_sampler_appends_and_reader_consumes_only_complete_new_events(self):
        run = self.prepare()
        metadata = run.path.read_bytes()
        run.update(step=10, metrics={"score": 0.8})
        prefix = run.events_path.read_bytes()
        store = report.ReportStore(self.root)
        self.assertEqual(store.read(run.path)["progress"]["step"], 10)
        offset = store.cache[run.path]["offset"]
        self.assertEqual(store.read(run.path)["progress"]["step"], 10)
        self.assertEqual(store.cache[run.path]["offset"], offset)
        event = json.dumps({"kind": "sample", "at": report.now(), "step": 20, "metrics": {"score": 0.9}}).encode()
        with run.events_path.open("ab") as stream:
            stream.write(event[:len(event)//2])
        self.assertEqual(store.read(run.path)["progress"]["step"], 10)
        with run.events_path.open("ab") as stream:
            stream.write(event[len(event)//2:] + b"\n")
        self.assertEqual(store.read(run.path)["progress"]["step"], 20)
        self.assertEqual(run.path.read_bytes(), metadata)
        self.assertTrue(run.events_path.read_bytes().startswith(prefix))
        self.assertEqual(len(store.read(run.path)["history"]), 2)

    def test_stage_queries_and_snapshots_keep_other_histories_separate(self):
        first = self.prepare()
        for step in range(1, 601):
            first.update(step=step, metrics={"score": step / 1000})
        description = self.description()
        description["stage"]["id"] = "02-other"
        description["run"]["implementation"] = [{"file": "foreign.py", "description": "Other", "code": "FOREIGN_CODE_MARKER"}]
        with contextlib.redirect_stdout(io.StringIO()):
            other = report.new_experiment(self.project, description)
        store = report.ReportStore(self.root)
        snapshot = report.collect(self.root, first.stage_id, store=store)
        self.assertEqual(set(store.cache), {first.path})
        self.assertLessEqual(len(snapshot["stages"][0]["runs"][0]["history"]), 240)
        self.assertEqual(snapshot["stages"][1]["runs"], [])
        self.assertEqual(len(report.collect_run(first.path)["history"]), 600)
        report.build(self.root)
        self.assertNotIn("FOREIGN_CODE_MARKER", (self.root / "stages/01-study/index.html").read_text())
        self.assertNotIn("FOREIGN_CODE_MARKER", (self.root / "index.html").read_text())



if __name__ == "__main__":
    unittest.main()

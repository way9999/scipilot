import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path


SCIPILOT_ROOT = Path(__file__).resolve().parents[2]
if str(SCIPILOT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCIPILOT_ROOT))

from sidecar.routers import writing


class FakeProcess:
    def __init__(self, *, start_error: Exception | None = None) -> None:
        self._start_error = start_error
        self.alive = False
        self.exitcode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.join_calls: list[float | int | None] = []

    def start(self) -> None:
        if self._start_error is not None:
            raise self._start_error
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.alive = False
        self.exitcode = -15

    def kill(self) -> None:
        self.kill_calls += 1
        self.alive = False
        self.exitcode = -9

    def join(self, timeout: float | int | None = None) -> None:
        self.join_calls.append(timeout)


class FakeContext:
    def __init__(self) -> None:
        self._start_errors: list[Exception] = []
        self.processes: list[FakeProcess] = []

    def queue_start_error(self, exc: Exception) -> None:
        self._start_errors.append(exc)

    def Process(self, target=None, args=(), daemon=None):  # noqa: N802 - mirrors multiprocessing API
        start_error = self._start_errors.pop(0) if self._start_errors else None
        process = FakeProcess(start_error=start_error)
        process.target = target
        process.args = args
        process.daemon = daemon
        self.processes.append(process)
        return process


class WritingTaskManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._project_root = writing.PROJECT_ROOT
        self._tempdir = SCIPILOT_ROOT / f"tmp_sidecar_tests_{uuid.uuid4().hex}"
        self._tempdir.mkdir(parents=True, exist_ok=False)
        writing.PROJECT_ROOT = self._tempdir

    def tearDown(self) -> None:
        writing.PROJECT_ROOT = self._project_root
        shutil.rmtree(self._tempdir, ignore_errors=True)

    def test_start_preserves_running_task_if_replacement_start_fails(self) -> None:
        manager = writing.WritingTaskManager()
        manager._ctx = FakeContext()

        first = manager.start("proposal", {"topic": "alpha"})
        first_task = manager._tasks[first["task_id"]]
        first_process = first_task["process"]

        manager._ctx.queue_start_error(RuntimeError("spawn failed"))

        with self.assertRaisesRegex(RuntimeError, "spawn failed"):
            manager.start("presentation", {"topic": "beta"})

        self.assertEqual(first_task["status"], "running")
        self.assertIsNone(first_task["error"])
        self.assertEqual(first_process.terminate_calls, 0)

    def test_start_supersedes_running_task_with_replacement_reason(self) -> None:
        manager = writing.WritingTaskManager()
        manager._ctx = FakeContext()

        first = manager.start("proposal", {"topic": "alpha"})
        first_task = manager._tasks[first["task_id"]]
        first_process = first_task["process"]

        second = manager.start("presentation", {"topic": "beta"})
        second_task = manager._tasks[second["task_id"]]

        self.assertEqual(second_task["status"], "running")
        self.assertEqual(first_task["status"], "canceled")
        self.assertEqual(first_task["replaced_by_task_id"], second["task_id"])
        self.assertIn(second["task_id"], first_task["error"])
        self.assertEqual(first_process.terminate_calls, 1)

    def test_worker_entry_tracks_section_progress_and_failure_label(self) -> None:
        result_path = self._tempdir / "paper-task" / "result.json"
        original_handler = writing.WORKER_HANDLERS["paper"]
        original_steps = writing.PROGRESS_STEPS["paper"]

        def failing_handler(payload: dict[str, str]) -> dict[str, str]:
            writing._report_step(3, "Draft sections")
            writing._section_progress_callback(1, 4, "Methods", "drafting")
            raise RuntimeError("boom")

        writing.WORKER_HANDLERS["paper"] = failing_handler
        writing.PROGRESS_STEPS["paper"] = [
            "Analyze topic",
            "Build outline",
            "Draft sections",
            "Format output",
        ]
        self.addCleanup(writing.WORKER_HANDLERS.__setitem__, "paper", original_handler)
        self.addCleanup(writing.PROGRESS_STEPS.__setitem__, "paper", original_steps)

        writing._worker_entry("paper", {}, str(result_path))

        progress = json.loads((result_path.parent / "progress.json").read_text(encoding="utf-8"))
        result = json.loads(result_path.read_text(encoding="utf-8"))

        self.assertEqual(progress["step"], 3)
        self.assertEqual(progress["total"], 4)
        self.assertIn("boom", progress["detail"])
        self.assertIn("Methods", progress["label"])
        self.assertFalse(result["success"])
        self.assertIn("Methods", result["error"])

    def test_worker_entry_uses_literature_review_progress_steps(self) -> None:
        result_path = self._tempdir / "review-task" / "result.json"
        original_handler = writing.WORKER_HANDLERS["literature_review"]

        def successful_handler(payload: dict[str, str]) -> dict[str, str]:
            return {"artifact": "ok"}

        writing.WORKER_HANDLERS["literature_review"] = successful_handler
        self.addCleanup(writing.WORKER_HANDLERS.__setitem__, "literature_review", original_handler)

        writing._worker_entry("literature_review", {}, str(result_path))

        progress = json.loads((result_path.parent / "progress.json").read_text(encoding="utf-8"))
        result = json.loads(result_path.read_text(encoding="utf-8"))

        self.assertEqual(progress["total"], len(writing.PROGRESS_STEPS["literature_review"]))
        self.assertEqual(progress["label"], writing.PROGRESS_STEPS["literature_review"][0])
        self.assertTrue(result["success"])

    def test_get_reports_exit_code_when_worker_exits_without_result(self) -> None:
        manager = writing.WritingTaskManager()
        manager._ctx = FakeContext()

        started = manager.start("proposal", {"topic": "alpha"})
        task = manager._tasks[started["task_id"]]
        process = task["process"]
        process.alive = False
        process.exitcode = 193

        payload = manager.get(started["task_id"])

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"], "Task exited unexpectedly (exit code 193).")


if __name__ == "__main__":
    unittest.main()

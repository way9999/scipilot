"""Deterministic regression gates for the previously reported SciPilot bugs.

This script is intentionally narrower than the full end-to-end flow. It checks
that each bug class the user reported has a stable regression guard before we
ask them to spend time on a full project-paper run.
"""

from __future__ import annotations

import argparse
import contextlib
import inspect
import io
import json
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCIPILOT_ROOT = ROOT / "scipilot"
for candidate in (ROOT, SCIPILOT_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


PASS = 0
FAIL = 0


def gate(name: str, fn) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"PASS  {name}")
    except Exception as exc:
        FAIL += 1
        print(f"FAIL  {name}: {exc}")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_frontend_project_paper_allows_blank_topic() -> None:
    source = (SCIPILOT_ROOT / "src" / "pages" / "Writing.tsx").read_text(encoding="utf-8")
    match = re.search(
        r"const runProjectPaper = async \(\) => \{(?P<body>.*?)\n\s*\}\n\n\s*const runProposal",
        source,
        flags=re.S,
    )
    _assert(match is not None, "runProjectPaper body not found")
    body = match.group("body")
    _assert("requireTopic()" not in body, "project paper still requires topic on frontend")
    _assert("startGeneratePaperFromProject(" in body, "project paper launch call missing")


def test_sidecar_project_paper_request_allows_blank_topic() -> None:
    from sidecar.routers.writing import ProjectPaperRequest

    payload = ProjectPaperRequest.model_validate({"source_project": "G:/demo-project"})
    _assert(payload.topic == "", "ProjectPaperRequest blank topic default regressed")


def test_paper_writer_blank_topic_fallback() -> None:
    from tools.paper_writer import _resolve_effective_topic

    derived_from_project = _resolve_effective_topic(
        "G:/sci",
        "",
        {
            "source_project_path": "G:/demo-project",
            "project_name": "demo-project",
            "topic": "",
        },
    )
    _assert(derived_from_project == "demo-project", "blank topic no longer falls back to project name")

    derived_from_reference = _resolve_effective_topic(
        "G:/sci",
        "",
        {"uploaded_references": [{"filename": "smart-grid-dispatch.pdf"}]},
    )
    _assert(
        derived_from_reference == "smart-grid-dispatch",
        "blank topic no longer falls back to reference filename",
    )


def test_project_runner_blocks_agent_edits_by_default() -> None:
    import tools.project_runner as project_runner

    root = ROOT / "output" / f"tmp-project-runner-safe-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    (root / "main.py").write_text("print('hello from test')\n", encoding="utf-8")

    original_run_python = project_runner._run_python
    original_collect_output_data = project_runner._collect_output_data
    original_agent_bridge = sys.modules.get("tools.agent_bridge")
    original_allow_env = os.environ.pop("SCIPILOT_AGENT_ALLOW_PROJECT_EDITS", None)

    run_calls = {"count": 0}
    agent_calls: list[dict[str, object]] = []

    try:
        def fake_run_python(project_root, entry_script, timeout=300):
            run_calls["count"] += 1
            return {"success": False, "stdout": "", "stderr": "boom", "returncode": 1}

        def fake_collect_output_data(project_root):
            return {"figures": [], "csv_files": [], "json_files": [], "log_lines": [], "metrics": {}}

        def fake_run_agent_task(**kwargs):
            agent_calls.append(kwargs)
            return {"success": True, "output": "fixed", "files_modified": ["main.py"]}

        fake_agent_bridge = types.ModuleType("tools.agent_bridge")
        fake_agent_bridge.agent_enabled = lambda: True
        fake_agent_bridge.run_agent_task = fake_run_agent_task
        fake_agent_bridge.results_sufficient = lambda collected, project_context=None: True
        fake_agent_bridge.build_fix_prompt = lambda run_result, project_type, entry_script=None: "fix"
        fake_agent_bridge.build_supplement_prompt = (
            lambda collected, project_type, project_context=None: "supplement"
        )
        fake_agent_bridge._agent_config = lambda: {"auto_fix": True, "auto_supplement": False}

        project_runner._run_python = fake_run_python
        project_runner._collect_output_data = fake_collect_output_data
        sys.modules["tools.agent_bridge"] = fake_agent_bridge

        result = project_runner.run_project(root)

        _assert(result["run_result"]["success"] is False, "project runner unexpectedly succeeded")
        _assert(run_calls["count"] == 1, "project runner retried unexpectedly without opt-in")
        _assert(agent_calls == [], "agent edits were triggered without opt-in")
    finally:
        project_runner._run_python = original_run_python
        project_runner._collect_output_data = original_collect_output_data
        if original_agent_bridge is None:
            sys.modules.pop("tools.agent_bridge", None)
        else:
            sys.modules["tools.agent_bridge"] = original_agent_bridge
        if original_allow_env is None:
            os.environ.pop("SCIPILOT_AGENT_ALLOW_PROJECT_EDITS", None)
        else:
            os.environ["SCIPILOT_AGENT_ALLOW_PROJECT_EDITS"] = original_allow_env
        shutil.rmtree(root, ignore_errors=True)


def test_project_runner_allows_opt_in_agent_edits() -> None:
    import tools.project_runner as project_runner

    root = ROOT / "output" / f"tmp-project-runner-opt-in-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    (root / "main.py").write_text("print('hello from test')\n", encoding="utf-8")

    original_run_python = project_runner._run_python
    original_collect_output_data = project_runner._collect_output_data
    original_agent_bridge = sys.modules.get("tools.agent_bridge")
    original_allow_env = os.environ.pop("SCIPILOT_AGENT_ALLOW_PROJECT_EDITS", None)

    responses = [
        {"success": False, "stdout": "", "stderr": "boom", "returncode": 1},
        {"success": True, "stdout": "ok", "stderr": "", "returncode": 0},
    ]
    run_calls = {"count": 0}
    agent_calls: list[dict[str, object]] = []

    try:
        def fake_run_python(project_root, entry_script, timeout=300):
            run_calls["count"] += 1
            return responses.pop(0)

        def fake_collect_output_data(project_root):
            return {"figures": [], "csv_files": [], "json_files": [], "log_lines": [], "metrics": {}}

        def fake_run_agent_task(**kwargs):
            agent_calls.append(kwargs)
            return {"success": True, "output": "fixed", "files_modified": ["main.py"]}

        fake_agent_bridge = types.ModuleType("tools.agent_bridge")
        fake_agent_bridge.agent_enabled = lambda: True
        fake_agent_bridge.run_agent_task = fake_run_agent_task
        fake_agent_bridge.results_sufficient = lambda collected, project_context=None: True
        fake_agent_bridge.build_fix_prompt = lambda run_result, project_type, entry_script=None: "fix"
        fake_agent_bridge.build_supplement_prompt = (
            lambda collected, project_type, project_context=None: "supplement"
        )
        fake_agent_bridge._agent_config = lambda: {"auto_fix": True, "auto_supplement": False}

        project_runner._run_python = fake_run_python
        project_runner._collect_output_data = fake_collect_output_data
        sys.modules["tools.agent_bridge"] = fake_agent_bridge

        result = project_runner.run_project(root, allow_agent_modifications=True)

        _assert(result["run_result"]["success"] is True, "opt-in agent fix did not recover the run")
        _assert(run_calls["count"] == 2, "project runner did not re-run after agent fix")
        _assert(len(agent_calls) == 1, "agent fix path did not run exactly once")
    finally:
        project_runner._run_python = original_run_python
        project_runner._collect_output_data = original_collect_output_data
        if original_agent_bridge is None:
            sys.modules.pop("tools.agent_bridge", None)
        else:
            sys.modules["tools.agent_bridge"] = original_agent_bridge
        if original_allow_env is None:
            os.environ.pop("SCIPILOT_AGENT_ALLOW_PROJECT_EDITS", None)
        else:
            os.environ["SCIPILOT_AGENT_ALLOW_PROJECT_EDITS"] = original_allow_env
        shutil.rmtree(root, ignore_errors=True)


def test_source_project_outputs_are_isolated() -> None:
    import tools.paper_writer as paper_writer

    captured: dict[str, object] = {}
    original_runner = sys.modules.get("tools.project_runner")
    original_legacy = paper_writer._legacy_generate_paper_package
    original_backfill = paper_writer._backfill_i18n_and_references

    tmp_root = ROOT / "output" / f"tmp-source-evidence-{uuid.uuid4().hex}"
    workspace_root = tmp_root / "workspace"
    source_root = tmp_root / "source-project"

    (workspace_root / "output" / "figures").mkdir(parents=True)
    (workspace_root / "output" / "results").mkdir(parents=True)
    (source_root / "output" / "figures").mkdir(parents=True)
    (source_root / "output" / "results").mkdir(parents=True)

    (workspace_root / "output" / "figures" / "stale-workspace.png").write_text("stale", encoding="utf-8")
    (workspace_root / "output" / "results" / "stale_metrics.csv").write_text(
        "metric,value\nstale,1\n",
        encoding="utf-8",
    )
    (source_root / "output" / "figures" / "source-proof.png").write_text("source", encoding="utf-8")
    (source_root / "output" / "results" / "source_metrics.csv").write_text(
        "metric,value\nsource,42\n",
        encoding="utf-8",
    )

    fake_runner = types.ModuleType("tools.project_runner")
    fake_runner.run_project = (
        lambda source_path, entry_script=None, timeout=300, dry_run=False, allow_agent_modifications=None: {
            "project_context": {},
            "collected": {"figures": []},
            "run_result": {"success": True},
            "project_type": "test",
        }
    )

    def fake_legacy_generate_paper_package(**kwargs):
        context = dict(kwargs["project_context"])
        captured["project_context"] = context
        return {
            "project_root": str(workspace_root),
            "dashboard_path": str(workspace_root / "output" / "paper-workbench.html"),
            "markdown_path": str(workspace_root / "drafts" / "paper.md"),
            "outline_path": str(workspace_root / "drafts" / "paper-outline.md"),
            "plan_path": str(workspace_root / "drafts" / "paper-plan.md"),
            "prompts_path": str(workspace_root / "drafts" / "paper-prompts.md"),
            "json_path": str(workspace_root / "output" / "paper.json"),
            "latex_path": str(workspace_root / "papers" / "paper.tex"),
            "bib_path": str(workspace_root / "papers" / "references.bib"),
            "artifact": {
                "language": "en",
                "sections": [],
                "references": [],
                "project_context": context,
            },
            "state": {},
        }

    try:
        sys.modules["tools.project_runner"] = fake_runner
        paper_writer._legacy_generate_paper_package = fake_legacy_generate_paper_package
        paper_writer._backfill_i18n_and_references = lambda result: None

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            paper_writer.generate_paper_package(
                project_root=workspace_root,
                topic="source evidence test",
                language="en",
                paper_type="general",
                project_context={
                    "source_project_path": str(source_root),
                    "skip_run": True,
                },
            )
        captured["copied_figure_exists"] = (workspace_root / "output" / "figures" / "source-proof.png").exists()
    finally:
        paper_writer._legacy_generate_paper_package = original_legacy
        paper_writer._backfill_i18n_and_references = original_backfill
        if original_runner is None:
            sys.modules.pop("tools.project_runner", None)
        else:
            sys.modules["tools.project_runner"] = original_runner
        shutil.rmtree(tmp_root, ignore_errors=True)

    project_context = captured["project_context"]
    result_clues = project_context["result_clues"]
    candidate_result_files = project_context["candidate_result_files"]

    _assert(any("source_metrics" in clue for clue in result_clues), "source metrics were not picked up")
    _assert(not any("stale_metrics" in clue for clue in result_clues), "workspace stale metrics leaked in")
    _assert(
        any("output/figures/source-proof.png" in item for item in candidate_result_files),
        "source figure missing from result files",
    )
    _assert(
        not any("stale-workspace.png" in item for item in candidate_result_files),
        "workspace stale figure leaked into candidate files",
    )
    _assert(
        project_context["paper_workspace_figure_files"] == ["source-proof.png"],
        "workspace figure allowlist is wrong",
    )
    _assert(captured["copied_figure_exists"] is True, "source figure was not copied into workspace")


def test_project_analysis_emits_structured_assets() -> None:
    from tools.project_paper_context import analyze_project_for_paper

    root = ROOT / "output" / f"tmp-project-analysis-assets-{uuid.uuid4().hex}"
    project = root / "demo-project"
    (root / "drafts").mkdir(parents=True)
    (root / "output").mkdir(parents=True)
    (project / "src").mkdir(parents=True)
    (project / "results").mkdir(parents=True)
    try:
        (project / "README.md").write_text(
            "# Demo Project\n\nThis project optimizes routing accuracy and latency.\n",
            encoding="utf-8",
        )
        (project / "src" / "model.py").write_text(
            "theta = 0.1\n"
            "alpha = 0.8\n"
            "loss = theta * alpha\n"
            "accuracy = 0.92\n",
            encoding="utf-8",
        )
        (project / "results" / "metrics.csv").write_text(
            "epoch,accuracy,loss\n1,0.81,0.40\n2,0.92,0.25\n",
            encoding="utf-8",
        )
        (project / "results" / "accuracy-curve.png").write_bytes(b"fake-png")
        prior = os.environ.get("SCIPILOT_AGENT_ENABLED")
        os.environ["SCIPILOT_AGENT_ENABLED"] = "0"
        try:
            context = analyze_project_for_paper(root, project, "Project paper regression gate")
        finally:
            if prior is None:
                os.environ.pop("SCIPILOT_AGENT_ENABLED", None)
            else:
                os.environ["SCIPILOT_AGENT_ENABLED"] = prior

        _assert(bool(context["figure_candidates"]), "project analysis did not extract figure candidates")
        _assert(bool(context["table_candidates"]), "project analysis did not extract table candidates")
        _assert(bool(context["equation_candidates"]), "project analysis did not extract equation candidates")
        _assert(bool(context["variable_inventory"]), "project analysis did not extract variable inventory")
        _assert(context["chapter_budget"]["total_figures"] >= 1, "chapter budget lost figure totals")
        _assert(context["chapter_budget"]["total_tables"] >= 1, "chapter budget lost table totals")
        _assert(Path(context["analysis_json_path"]).exists(), "analysis json was not written")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_writing_profile_plans_figures_tables_equations() -> None:
    from tools.writing_profiles import build_equation_plan, build_figure_plan, build_table_plan

    project_context = {
        "project_name": "Demo Planner",
        "project_summary": "Project contains figures, tables, equations, and metrics.",
        "stack": ["Python"],
        "candidate_source_files": ["src/model.py"],
        "candidate_config_files": ["config/settings.yaml"],
        "candidate_result_files": ["output/results/metrics.csv", "output/figures/accuracy-curve.png"],
        "method_clues": ["optimization objective", "loss function"],
        "result_clues": ["accuracy 0.92", "loss 0.25"],
        "figure_candidates": [
            {
                "caption": "Accuracy curve",
                "path": "output/figures/accuracy-curve.png",
                "section": "experiment",
                "role": "experiment",
            }
        ],
        "table_candidates": [
            {
                "caption": "Main metric comparison",
                "path": "output/results/metrics.csv",
                "section": "experiment",
                "headers": ["Metric", "Value"],
                "preview_rows": [["Accuracy", "0.92"]],
            }
        ],
        "equation_candidates": [
            {
                "focus": "loss function and update rule",
                "source": "src/model.py",
                "section": "theory",
            }
        ],
        "variable_inventory": [{"symbol": "theta", "evidence": "src/model.py"}],
        "metric_inventory": ["accuracy", "loss"],
        "chapter_budget": {
            "figures": {"experiment": 2},
            "tables": {"experiment": 2},
            "equations": {"theory": 2},
            "total_figures": 1,
            "total_tables": 1,
            "total_equations": 1,
        },
    }

    figure_plan = build_figure_plan(topic="Project paper test", language="zh", project_context=project_context)
    table_plan = build_table_plan(topic="Project paper test", language="zh", project_context=project_context)
    equation_plan = build_equation_plan(topic="Project paper test", language="zh", project_context=project_context)

    _assert(bool(figure_plan), "figure plan is empty")
    _assert(bool(table_plan), "table plan is empty")
    _assert(bool(equation_plan), "equation plan is empty")
    _assert(any("metrics.csv" in str(item.get("path") or "") for item in table_plan), "table plan lost project CSV")
    _assert(
        any("loss function" in str(item.get("focus") or "") for item in equation_plan),
        "equation plan lost project-derived focus",
    )


def test_writing_enhancer_root_base_url_uses_v1_chat_path() -> None:
    import tools.writing_enhancer as enhancer

    class DummyResponse:
        def __init__(self, url, payload):
            self.url = url
            self._payload = payload
            self.text = ""
            self.status_code = 200
            self.headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    calls: list[str] = []
    original_post = enhancer.requests.post
    try:
        def fake_post(url, **kwargs):
            calls.append(url)
            return DummyResponse(url, {"choices": [{"message": {"content": "ok"}}]})

        enhancer.requests.post = fake_post
        result = enhancer._call_model(
            {
                "base_url": "https://www.xiangluapi.com",
                "provider": "llm",
                "model": "gemini-3.1-pro-high",
                "api_key": "dummy",
            },
            "hello",
            "zh",
        )
        _assert(result == "ok", "call_model did not return payload content")
        _assert(calls and calls[-1].endswith("/v1/chat/completions"), "root base URL path regressed")
    finally:
        enhancer.requests.post = original_post


def test_writing_enhancer_plain_text_response_fallback() -> None:
    import tools.writing_enhancer as enhancer

    class DummyResponse:
        def __init__(self, url, text):
            self.url = url
            self.text = text
            self.status_code = 200
            self.headers = {"content-type": "text/plain; charset=utf-8"}

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("no json body")

    original_post = enhancer.requests.post
    try:
        enhancer.requests.post = lambda url, **kwargs: DummyResponse(url, "plain fallback answer")
        result = enhancer._call_model(
            {
                "base_url": "https://www.xiangluapi.com",
                "provider": "llm",
                "model": "gemini-3.1-pro-high",
                "api_key": "dummy",
            },
            "hello",
            "zh",
        )
        _assert(result == "plain fallback answer", "plain-text fallback regressed")
    finally:
        enhancer.requests.post = original_post


def test_surrogate_sanitization_pipeline() -> None:
    import tools.writing_enhancer as enhancer
    from tools.text_safety import contains_surrogates, sanitize_utf8_text

    repaired_pair = sanitize_utf8_text("A\ud835\udc00B")
    repaired_orphan = sanitize_utf8_text("A\ud835B")
    _assert(repaired_pair == f"A{chr(0x1D400)}B", "paired surrogate repair regressed")
    _assert(not contains_surrogates(repaired_pair), "paired surrogate still present")
    _assert(not contains_surrogates(repaired_orphan), "orphan surrogate still present")

    captured_payloads: list[dict] = []

    class DummyResponse:
        def __init__(self, url, payload):
            self.url = url
            self._payload = payload
            self.text = json.dumps(payload, ensure_ascii=False)
            self.status_code = 200
            self.headers = {"content-type": "application/json; charset=utf-8"}

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    original_post = enhancer.requests.post
    try:
        def fake_post(url, **kwargs):
            captured_payloads.append(kwargs["json"])
            return DummyResponse(url, {"choices": [{"message": {"content": "ok\ud835"}}]})

        enhancer.requests.post = fake_post
        result = enhancer._call_model(
            {
                "base_url": "https://www.xiangluapi.com",
                "provider": "llm",
                "model": "gemini-3.1-pro-high",
                "api_key": "dummy",
            },
            "hello\ud835world",
            "zh",
        )
        _assert(bool(captured_payloads), "no outbound payload captured")
        for message in captured_payloads[0]["messages"]:
            _assert(not contains_surrogates(message["content"]), "surrogate leaked into outbound payload")
        _assert(not contains_surrogates(result), "surrogate leaked into model result")
        _assert(result.startswith("ok"), "sanitized result content regressed")
    finally:
        enhancer.requests.post = original_post


def test_writing_enhancer_hits_floor_without_large_overshoot() -> None:
    import tools.writing_enhancer as enhancer

    root = ROOT / "output" / f"tmp-writing-enhancer-window-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        markdown_path = root / "draft.md"
        outline_path = root / "outline.md"
        plan_path = root / "plan.md"
        json_path = root / "draft.json"
        base_result = {
            "markdown_path": str(markdown_path),
            "outline_path": str(outline_path),
            "plan_path": str(plan_path),
            "json_path": str(json_path),
            "artifact": {
                "title": "target window test",
                "references": [],
            },
        }

        original_load = enhancer._load_llm_config
        original_draft = enhancer._draft_sections_with_llm
        original_expand = enhancer._expand_short_sections

        try:
            enhancer._load_llm_config = lambda *args, **kwargs: [
                {"provider": "test", "model": "test", "base_url": "http://example.invalid", "api_key": "x"}
            ]

            def fake_draft_sections_with_llm(**kwargs):
                return [
                    {"title": "1. Introduction", "content": ["### Background", "a" * 2500]},
                    {"title": "2. Foundation", "content": ["### Theory", "b" * 2600]},
                    {"title": "3. Design", "content": ["### Architecture", "c" * 2800]},
                    {"title": "4. Implementation", "content": ["### Modules", "d" * 3000]},
                    {"title": "5. Experiments", "content": ["### Results", "e" * 3200]},
                    {"title": "6. Conclusion", "content": ["### Summary", "f" * 1600]},
                ]

            enhancer._draft_sections_with_llm = fake_draft_sections_with_llm

            for target_words in (12000, 20000, 35000):
                expand_calls = {"count": 0}

                def fake_expand_short_sections(**kwargs):
                    expand_calls["count"] += 1
                    remaining_gap = int(kwargs.get("remaining_gap") or 0)
                    threshold = enhancer._final_fill_threshold(kwargs["target_words"], "zh")
                    if remaining_gap <= threshold:
                        addition = remaining_gap + 1200
                    else:
                        addition = max(1800, int(remaining_gap * 0.55))
                    sections = []
                    for section in kwargs["sections"]:
                        sections.append({"title": section["title"], "content": list(section["content"])})
                    sections[0]["content"].append(
                        f"round-{target_words}-{expand_calls['count']}-" + ("x" * addition)
                    )
                    return sections

                enhancer._expand_short_sections = fake_expand_short_sections
                result = enhancer.enhance_generated_paper_package(
                    base_result,
                    project_root=root,
                    topic="target window test",
                    language="zh",
                    paper_type="general",
                    project_context=None,
                    target_words=target_words,
                )

                actual_words = result["artifact"]["actual_words"]
                _assert(actual_words >= target_words, f"undershot target {target_words}: {actual_words}")
                _assert(
                    actual_words <= enhancer._target_max_words(target_words, "zh"),
                    f"overshot target {target_words}: {actual_words}",
                )
                _assert(
                    result["artifact"]["quality_meta"]["expansion_rounds"] == expand_calls["count"],
                    "expansion round accounting regressed",
                )
        finally:
            enhancer._load_llm_config = original_load
            enhancer._draft_sections_with_llm = original_draft
            enhancer._expand_short_sections = original_expand
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_sidecar_server_freeze_support_present() -> None:
    import sidecar.server as server

    source = inspect.getsource(server.main)
    _assert("freeze_support" in source, "sidecar main no longer calls freeze_support()")


def test_sidecar_task_manager_suite() -> None:
    import sidecar.tests.test_writing_task_manager as task_tests

    suite = unittest.defaultTestLoader.loadTestsFromModule(task_tests)
    stream = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    _assert(result.wasSuccessful(), stream.getvalue().strip() or "sidecar task manager tests failed")


def test_figure_numbering_and_reference_sync() -> None:
    from tools.paper_writer import _renumber_figures, _sync_nearby_figure_references

    md = (
        "## 1. Intro\n\n"
        "\u5982\u56fe9-9\u6240\u793a\uff0c\u7cfb\u7edf\u5177\u6709\u6e05\u6670\u7684\u7ed3\u6784\u5206\u5c42\u3002\n\n"
        "![\u7ed3\u6784\u6846\u56fe](a.png)\n"
        "\u56fe9-9 \u7ed3\u6784\u6846\u56fe\n\n"
        "![\u63a7\u5236\u6d41\u7a0b\u56fe](b.png)\n"
        "\u56fe9-10 \u63a7\u5236\u6d41\u7a0b\u56fe\n"
    )
    renumbered = _sync_nearby_figure_references(_renumber_figures(md, language="zh"), language="zh")

    _assert("\u56fe1-1 \u7ed3\u6784\u6846\u56fe" in renumbered, "first figure caption numbering regressed")
    _assert("\u56fe1-2 \u63a7\u5236\u6d41\u7a0b\u56fe" in renumbered, "second figure caption numbering regressed")
    _assert("\u56fe9-9" not in renumbered, "old figure number remained after renumbering")
    _assert("\u5982\u56fe1-1\u6240\u793a" in renumbered, "nearby figure reference did not sync")


def test_paper_writer_output_gates(draft_path: Path | None, write_preview: bool) -> None:
    from tools import paper_writer_gates as pwg

    pwg.test_formula_block_and_numbering()
    pwg.test_broken_formula_fragments()
    pwg.test_table_repair_and_caption()
    pwg.test_table_injection()
    pwg.test_figure_spread()
    pwg.test_placeholder_matching_and_injection()
    if draft_path and draft_path.exists():
        pwg.test_draft_scan(draft_path, write_preview)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic regression gates for previously reported bugs.")
    parser.add_argument(
        "--draft",
        type=Path,
        default=ROOT / "drafts" / "paper-draft.md",
        help="Optional generated draft to normalize-scan.",
    )
    parser.add_argument(
        "--write-preview",
        action="store_true",
        help="Write normalized draft preview when --draft exists.",
    )
    args = parser.parse_args()

    print("=== Regression Gates: Request/Launch ===")
    gate("frontend project-paper allows blank topic", test_frontend_project_paper_allows_blank_topic)
    gate("sidecar project-paper request allows blank topic", test_sidecar_project_paper_request_allows_blank_topic)
    gate("paper writer blank topic fallback", test_paper_writer_blank_topic_fallback)

    print("\n=== Regression Gates: Project Safety ===")
    gate("project runner blocks agent edits by default", test_project_runner_blocks_agent_edits_by_default)
    gate("project runner opt-in agent edits work", test_project_runner_allows_opt_in_agent_edits)
    gate("source-project evidence isolation", test_source_project_outputs_are_isolated)

    print("\n=== Regression Gates: Analysis And Planning ===")
    gate("project analysis emits structured assets", test_project_analysis_emits_structured_assets)
    gate("writing profile plans figures tables equations", test_writing_profile_plans_figures_tables_equations)

    print("\n=== Regression Gates: LLM Transport ===")
    gate("root base URL uses /v1/chat/completions", test_writing_enhancer_root_base_url_uses_v1_chat_path)
    gate("plain-text response fallback", test_writing_enhancer_plain_text_response_fallback)
    gate("surrogate sanitization pipeline", test_surrogate_sanitization_pipeline)
    gate("target floor with bounded overshoot", test_writing_enhancer_hits_floor_without_large_overshoot)

    print("\n=== Regression Gates: Sidecar Runtime ===")
    gate("sidecar server freeze_support present", test_sidecar_server_freeze_support_present)
    gate("sidecar task manager suite", test_sidecar_task_manager_suite)

    print("\n=== Regression Gates: Final Manuscript ===")
    gate("figure numbering and nearby reference sync", test_figure_numbering_and_reference_sync)
    gate(
        f"paper writer output gates ({args.draft})",
        lambda: test_paper_writer_output_gates(args.draft, args.write_preview),
    )

    print("=" * 52)
    print(f"PASS: {PASS}  FAIL: {FAIL}")
    print("=" * 52)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

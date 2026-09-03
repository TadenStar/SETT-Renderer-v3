"""Тесты core/job_runner.py на поддельном рендере: пачки, resume, ретраи, пауза."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from qt_helpers import FakePlanBuilder, wait_until

from brm.core.capabilities import Capabilities
from brm.core.job_runner import RUN_FAILED, RUN_PAUSED, RUN_STOPPED, RUN_SUCCESS, JobRunner
from brm.core.models import RenderJob
from brm.core.project_probe import ProjectInfo, SceneInfo
from brm.core.storage import AppSettings


@pytest.fixture
def caps() -> Capabilities:
    return Capabilities(blender_version=(5, 0, 1), engines=["CYCLES"], blender_path="blender.exe")


@pytest.fixture
def project() -> ProjectInfo:
    return ProjectInfo(file_path="D:/shots/cave.blend", scenes=[SceneInfo(name="Scene", frame_end=3)], active_scene="Scene")


def make_runner(qapp, builder: FakePlanBuilder) -> JobRunner:
    return JobRunner(plan_builder=builder, kill_delay_ms=200)


def run_to_end(qapp, runner: JobRunner, timeout: float = 30) -> None:
    wait_until(qapp, lambda: runner.status is not None, timeout)


def test_single_chunk_success_writes_stats(qapp, tmp_path: Path, caps, project) -> None:
    builder = FakePlanBuilder(tmp_path)
    runner = make_runner(qapp, builder)
    lines: list[str] = []
    runner.line_received.connect(lines.append)
    runner.start(RenderJob(blend_path=project.file_path, resume=False), caps, AppSettings(), project, tmp_dir=tmp_path)
    run_to_end(qapp, runner)
    assert runner.status == RUN_SUCCESS and runner.message == "All frames rendered"
    assert runner.tracker.progress.frames_done == [1, 2, 3]
    assert builder.chunk_calls == [[1, 2, 3]]
    assert not any(line.startswith("[BRM] chunk") for line in lines)
    stats = json.loads(runner.plans[0].stats_path.read_text(encoding="utf-8"))
    assert stats["frames_done"] == [1, 2, 3] and stats["chunks"] == 1 and stats["retries"] == []


def test_chunks_run_sequentially_with_one_tracker(qapp, tmp_path: Path, caps, project) -> None:
    builder = FakePlanBuilder(tmp_path, base_frames=[1, 2, 3, 4, 5])
    runner = make_runner(qapp, builder)
    lines: list[str] = []
    runner.line_received.connect(lines.append)
    plans = []
    runner.chunk_started.connect(plans.append)
    runner.start(RenderJob(blend_path=project.file_path, resume=False, chunk_size=2), caps, AppSettings(), project, tmp_dir=tmp_path)
    run_to_end(qapp, runner)
    assert runner.status == RUN_SUCCESS
    assert builder.chunk_calls == [[1, 2], [3, 4], [5]]
    assert len(plans) == 3 and len(runner.plans) == 3
    assert runner.tracker.progress.frames_done == [1, 2, 3, 4, 5]
    assert runner.tracker.progress.frame_times() == [(f, 0.1) for f in range(1, 6)]
    assert [line for line in lines if line.startswith("[BRM] chunk")] == [
        "[BRM] chunk 1/3: frames 1..2 (2)",
        "[BRM] chunk 2/3: frames 3..4 (2)",
        "[BRM] chunk 3/3: frame 5",
    ]


def test_resume_skips_frames_already_on_disk(qapp, tmp_path: Path, caps, project) -> None:
    builder = FakePlanBuilder(tmp_path)
    builder.output_dir.mkdir(parents=True)
    (builder.output_dir / "0001.png").write_bytes(b"x" * 5000)
    (builder.output_dir / "0002.png").write_bytes(b"x" * 10)  # битый: меньше порога
    runner = make_runner(qapp, builder)
    lines: list[str] = []
    runner.line_received.connect(lines.append)
    runner.start(RenderJob(blend_path=project.file_path, resume=True, min_frame_kb=1), caps, AppSettings(), project, tmp_dir=tmp_path)
    run_to_end(qapp, runner)
    assert runner.status == RUN_SUCCESS
    assert runner.skipped_existing == [1]
    assert builder.chunk_calls == [[2, 3]]
    assert runner.tracker.progress.frames_expected == [2, 3]
    assert lines[0] == "[BRM] resume: 1 frame(s) already on disk, 2 to render"


def test_resume_with_everything_rendered_finishes_immediately(qapp, tmp_path: Path, caps, project) -> None:
    builder = FakePlanBuilder(tmp_path)
    builder.output_dir.mkdir(parents=True)
    for frame in (1, 2, 3):
        (builder.output_dir / f"{frame:04d}.png").write_bytes(b"x" * 5000)
    runner = make_runner(qapp, builder)
    statuses: list[str] = []
    runner.finished.connect(statuses.append)
    runner.start(RenderJob(blend_path=project.file_path, resume=True), caps, AppSettings(), project, tmp_dir=tmp_path)
    assert statuses == [RUN_SUCCESS] and "already rendered" in runner.message
    assert builder.chunk_calls == [] and not runner.is_running()


def test_out_of_memory_retries_with_lighter_settings(qapp, tmp_path: Path, caps, project) -> None:
    builder = FakePlanBuilder(tmp_path, failures={1: "oom", 2: "oom"})
    runner = make_runner(qapp, builder)
    lines: list[str] = []
    runner.line_received.connect(lines.append)
    runner.start(RenderJob(blend_path=project.file_path, resume=False, overrides={"cycles.samples": 8}), caps, AppSettings(), project, tmp_dir=tmp_path)
    run_to_end(qapp, runner)
    assert runner.status == RUN_SUCCESS
    assert builder.chunk_calls == [[1, 2, 3], [1, 2, 3], [1, 2, 3]]
    assert runner.retry_notes == ["texture limit 2048", "texture limit 2048, tile size 512"]
    third_job = builder.jobs[-1]
    assert third_job.overrides["cycles.samples"] == 8
    assert third_job.overrides["cycles.texture_limit_render"] == "2048" and third_job.overrides["cycles.tile_size"] == 512
    assert [line for line in lines if line.startswith("[BRM] retry after out of memory")] == [
        "[BRM] retry after out of memory: texture limit 2048",
        "[BRM] retry after out of memory: texture limit 2048, tile size 512",
    ]
    stats = json.loads(runner.plans[0].stats_path.read_text(encoding="utf-8"))
    assert stats["retries"] == runner.retry_notes and len(stats["log_files"]) == 3


def test_out_of_memory_gives_up_after_all_steps(qapp, tmp_path: Path, caps, project) -> None:
    builder = FakePlanBuilder(tmp_path, failures={1: "oom", 2: "oom", 3: "oom", 4: "oom", 5: "oom"})
    runner = make_runner(qapp, builder)
    runner.start(RenderJob(blend_path=project.file_path, resume=False), caps, AppSettings(), project, tmp_dir=tmp_path)
    run_to_end(qapp, runner)
    assert runner.status == RUN_FAILED and "lightest settings" in runner.message
    assert len(builder.chunk_calls) == 5


def test_crash_retries_once_then_fails(qapp, tmp_path: Path, caps, project) -> None:
    builder = FakePlanBuilder(tmp_path, failures={1: "crash"})
    runner = make_runner(qapp, builder)
    lines: list[str] = []
    runner.line_received.connect(lines.append)
    runner.start(RenderJob(blend_path=project.file_path, resume=False), caps, AppSettings(), project, tmp_dir=tmp_path)
    run_to_end(qapp, runner)
    assert runner.status == RUN_SUCCESS and len(builder.chunk_calls) == 2
    assert any(line.startswith("[BRM] retry 1/1 after failed (exit code 3)") for line in lines)

    builder = FakePlanBuilder(tmp_path / "second", failures={1: "crash", 2: "crash"})
    runner = make_runner(qapp, builder)
    runner.start(RenderJob(blend_path=project.file_path, resume=False), caps, AppSettings(), project, tmp_dir=tmp_path)
    run_to_end(qapp, runner)
    assert runner.status == RUN_FAILED and "exit code 3" in runner.message and runner.exit_code == 3


def test_pause_then_resume_keeps_overall_progress(qapp, tmp_path: Path, caps, project) -> None:
    builder = FakePlanBuilder(tmp_path, base_frames=[1, 2, 3, 4], delay=0.3)
    runner = make_runner(qapp, builder)
    runner.start(RenderJob(blend_path=project.file_path, resume=False, chunk_size=2), caps, AppSettings(), project, tmp_dir=tmp_path)
    wait_until(qapp, lambda: runner.tracker.progress.frames_done_count >= 1, 20)
    runner.pause()
    run_to_end(qapp, runner)
    assert runner.status == RUN_PAUSED and runner.is_paused()
    done_before = list(runner.tracker.progress.frames_done)
    assert runner.paused_frames == [f for f in [1, 2, 3, 4] if f not in done_before]

    runner.resume()
    assert runner.status is None and runner.is_running()
    run_to_end(qapp, runner)
    assert runner.status == RUN_SUCCESS
    assert runner.tracker.progress.frames_done == [1, 2, 3, 4]  # общий трекер пережил паузу
    assert runner.tracker.progress.frame_times() == [(f, 0.1) for f in [1, 2, 3, 4]]


def test_stop_reports_stopped(qapp, tmp_path: Path, caps, project) -> None:
    builder = FakePlanBuilder(tmp_path, base_frames=[1, 2, 3], delay=1.0)
    runner = make_runner(qapp, builder)
    runner.start(RenderJob(blend_path=project.file_path, resume=False), caps, AppSettings(), project, tmp_dir=tmp_path)
    wait_until(qapp, runner.is_running, 10)
    runner.stop()
    run_to_end(qapp, runner)
    assert runner.status == RUN_STOPPED and not runner.is_paused()
    assert "left" in runner.message


def test_start_while_running_raises(qapp, tmp_path: Path, caps, project) -> None:
    builder = FakePlanBuilder(tmp_path, delay=1.0)
    runner = make_runner(qapp, builder)
    job = RenderJob(blend_path=project.file_path, resume=False)
    runner.start(job, caps, AppSettings(), project, tmp_dir=tmp_path)
    wait_until(qapp, runner.is_running, 10)
    with pytest.raises(RuntimeError):
        runner.start(job, caps, AppSettings(), project, tmp_dir=tmp_path)
    runner.stop()
    run_to_end(qapp, runner)

"""Тесты core/models.py: задача рендера и шаблон вывода."""
from __future__ import annotations

import os

from brm.core.frame_range import FrameRangeMode
from brm.core.models import DEFAULT_OUTPUT_TEMPLATE, RenderJob, expand_output_template, safe_component


def test_render_job_defaults() -> None:
    job = RenderJob(blend_path=r"D:\shots\cave_v3.blend")
    assert job.project_name == "cave_v3"
    assert job.scene is None
    assert job.frame_range.mode is FrameRangeMode.FROM_FILE
    assert job.output_template == DEFAULT_OUTPUT_TEMPLATE
    assert len(job.id) == 8


def test_render_job_round_trips_through_json() -> None:
    job = RenderJob(blend_path="x.blend", scene="Scene", preset="Balanced")
    assert RenderJob.model_validate_json(job.model_dump_json()) == job


def test_expand_output_template_basic() -> None:
    result = expand_output_template(
        DEFAULT_OUTPUT_TEMPLATE, output_dir=r"D:\out", project="cave", scene="Scene", preset=None
    )
    assert result == os.path.normpath(r"D:\out\cave\Scene\####")


def test_expand_keeps_hashes_and_unknown_placeholders() -> None:
    result = expand_output_template("{output_dir}/{project}/{unknown}/{preset}/####", output_dir="C:/o", project="p")
    assert result.endswith(os.path.normpath("p/{unknown}/default/####"))


def test_expand_with_cyrillic_and_unsafe_scene_name() -> None:
    result = expand_output_template("{output_dir}/{project}/{scene}/####", output_dir=r"D:\Рендер", project="Пещера v2", scene="Cam: 01?")
    assert result == os.path.normpath(r"D:\Рендер\Пещера v2\Cam_ 01_\####")


def test_safe_component() -> None:
    assert safe_component("  ...  ") == "_"
    assert safe_component('a<b>c:d"e|f?g*h') == "a_b_c_d_e_f_g_h"

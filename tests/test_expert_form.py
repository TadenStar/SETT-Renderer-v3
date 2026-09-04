"""Тесты ui/expert_form.py на фикстуре capabilities Blender 5.0.1 (offscreen Qt)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from brm.core.capabilities import Capabilities
from brm.core.preset_resolver import resolve_preset
from brm.core.presets import load_presets
from brm.ui.expert_form import ExpertForm
from brm.ui.field_modes import MODE_CUSTOM, MODE_PRESET, MODE_SKIP


@pytest.fixture
def caps(fixtures_dir: Path) -> Capabilities:
    data = json.loads((fixtures_dir / "capabilities_blender_5.0.1.json").read_text(encoding="utf-8"))
    return Capabilities.model_validate(data)


def test_empty_without_capabilities_or_engine(qapp) -> None:
    form = ExpertForm()
    assert form.field_count() == 0
    assert not form.placeholder.isHidden()
    assert form._scroll.isHidden()
    assert form.search_edit.isEnabled() is False


def test_builds_rows_once_capabilities_and_engine_arrive(qapp, caps: Capabilities) -> None:
    form = ExpertForm()
    form.set_capabilities(caps)
    assert form.field_count() == 0  # ещё нет движка
    form.set_engine("CYCLES")
    assert form.field_count() > 150
    assert form.placeholder.isHidden()
    assert not form._scroll.isHidden()
    assert "cycles.samples" in form.rows
    assert "render.image_settings.file_format" in form.rows
    assert "render.engine" not in form.rows  # исключено явно


def test_switching_engine_rebuilds_with_different_fields(qapp, caps: Capabilities) -> None:
    form = ExpertForm()
    form.set_capabilities(caps)
    form.set_engine("CYCLES")
    cycles_paths = set(form.rows)
    assert any(p.startswith("cycles.") for p in cycles_paths)
    assert not any(p.startswith("eevee.") for p in cycles_paths)

    form.set_engine("BLENDER_EEVEE")
    eevee_paths = set(form.rows)
    assert any(p.startswith("eevee.") for p in eevee_paths)
    assert not any(p.startswith("cycles.") for p in eevee_paths)


def test_same_engine_does_not_rebuild_and_keeps_row_state(qapp, caps: Capabilities) -> None:
    form = ExpertForm()
    form.set_capabilities(caps)
    form.set_engine("CYCLES")
    row = form.rows["cycles.samples"]
    row.set_mode(MODE_CUSTOM)
    row.set_value(777)

    form.set_capabilities(caps)  # тот же объект — не должно пересоздавать строки
    form.set_engine("CYCLES")
    assert form.rows["cycles.samples"] is row
    assert row.mode() == MODE_CUSTOM and row.value() == 777


def test_three_states_and_value_types(qapp, caps: Capabilities) -> None:
    form = ExpertForm()
    form.set_capabilities(caps)
    form.set_engine("CYCLES")

    samples = form.rows["cycles.samples"]  # INT
    denoise = form.rows["cycles.use_denoising"]  # BOOLEAN
    denoiser = form.rows["cycles.denoiser"]  # ENUM (динамический)

    for row in (samples, denoise, denoiser):
        assert row.mode() == MODE_PRESET
        assert not row.widget.isEnabled()

    samples.show_preset_value(256)
    assert samples.value() == 256  # виден пресетный дефолт, пока режим Preset

    samples.set_mode(MODE_CUSTOM)
    assert samples.widget.isEnabled()
    samples.set_value(64)
    assert samples.value() == 64

    denoise.set_mode(MODE_CUSTOM)
    denoise.set_value(True)
    assert denoise.value() is True

    denoiser.show_preset_value("OPENIMAGEDENOISE")
    denoiser.set_mode(MODE_CUSTOM)
    denoiser.set_value("OPTIX")
    assert denoiser.value() == "OPTIX"

    custom = form.custom_values()
    assert custom["cycles.samples"] == 64
    assert custom["cycles.use_denoising"] is True
    assert custom["cycles.denoiser"] == "OPTIX"
    assert "cycles.max_bounces" not in custom  # остальные строки не тронуты

    samples.set_mode(MODE_SKIP)
    assert "cycles.samples" in form.untouched_paths()
    assert "cycles.samples" not in form.custom_values()


def test_int_and_float_ranges_come_from_probe(qapp, caps: Capabilities) -> None:
    form = ExpertForm()
    form.set_capabilities(caps)
    form.set_engine("CYCLES")
    samples = form.rows["cycles.samples"]
    assert samples.widget.minimum() == 1  # soft_min из пробы
    threshold = form.rows["cycles.adaptive_threshold"]
    lo, hi = threshold.widget.minimum(), threshold.widget.maximum()
    assert 0.0 <= lo < hi <= 1.0


def test_highlight_marks_value_different_from_preset(qapp, caps: Capabilities) -> None:
    form = ExpertForm()
    form.set_capabilities(caps)
    form.set_engine("CYCLES")
    row = form.rows["cycles.samples"]
    row.show_preset_value(128)
    row.set_mode(MODE_CUSTOM)
    assert row.note.text() == ""  # только что скопировали значение пресета — совпадает
    row.set_value(999)
    assert row.note.text() == "≠ preset"
    row.set_value(128)
    assert row.note.text() == ""


def test_show_resolved_updates_preset_values_and_skipped_stays_out(qapp, caps: Capabilities, tmp_path: Path) -> None:
    presets = {p.name: p for p in load_presets(user_dir=tmp_path / "none")}
    form = ExpertForm()
    form.set_capabilities(caps)
    form.set_engine("CYCLES")

    resolved = resolve_preset(presets["Super"], caps, "CYCLES")
    form.show_resolved(resolved)
    assert form.rows["cycles.samples"].value() == 512
    assert form.rows["render.use_persistent_data"].value() is True
    # denoiser пресета — {"prefer": [...]}, resolved.value() уже даёт первый доступный:
    assert form.rows["cycles.denoiser"].value() == "OPENIMAGEDENOISE"

    form.show_resolved(None)
    assert form.rows["cycles.samples"].note.text() == "not set by preset"


def test_search_filters_rows_and_hides_empty_sections(qapp, caps: Capabilities) -> None:
    form = ExpertForm()
    form.set_capabilities(caps)
    form.set_engine("CYCLES")

    form.search_edit.setText("max_bounces")
    assert not form._row_containers["cycles.max_bounces"].isHidden()
    assert form._row_containers["cycles.samples"].isHidden()
    # substring-поиск: тут же ловится и "transparent_max_bounces" — это верно, не баг.
    assert form.count_label.text().split(" / ")[0] == "2"

    form.search_edit.setText("")
    assert not form._row_containers["cycles.samples"].isHidden()
    assert form.count_label.text().endswith("properties")

    form.search_edit.setText("no such property xyz")
    assert all(w.isHidden() for w in form._row_containers.values())
    assert all(h.isHidden() for h in form._section_headers.values())

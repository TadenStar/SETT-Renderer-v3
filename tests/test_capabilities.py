"""Тесты core/capabilities.py на сохранённом выводе реальной пробы (tests/fixtures)."""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from brm.core import capabilities as caps_mod
from brm.core.blender_process import BlenderResult
from brm.core.capabilities import (
    Capabilities,
    CapabilitiesError,
    blender_fingerprint,
    cache_file,
    get_capabilities,
    load_cached,
    run_probe,
    save_cache,
    support_problem,
)

FIXTURE = "capabilities_blender_5.0.1.json"


@pytest.fixture
def caps_fixture(fixtures_dir: Path) -> dict:
    return json.loads((fixtures_dir / FIXTURE).read_text(encoding="utf-8"))


def _fake_run(fixture: dict, *, ok: bool = True, write: bool = True):
    """Подмена run_blender: пишет фикстуру в файл, который идёт последним аргументом."""

    def fake(blender_path, args, **kwargs):
        out = Path(str(args[-1]))
        if write:
            out.write_text(json.dumps(fixture), encoding="utf-8")
        return BlenderResult(argv=[str(blender_path), *map(str, args)], returncode=0 if ok else 1, stdout="[BRM] fake", duration=0.1)

    return fake


def test_fixture_parses_into_model(caps_fixture: dict) -> None:
    caps = Capabilities.model_validate(caps_fixture)
    assert caps.blender_version >= (4, 2, 0)
    assert caps.has_engine("CYCLES")
    assert caps.eevee_engine_id in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT")
    samples = caps.property("cycles", "samples")
    assert samples is not None and samples.type == "INT"
    denoiser = caps.property("cycles", "denoiser")
    assert denoiser.enum_dynamic and "OPENIMAGEDENOISE" in denoiser.enum_identifiers()
    assert not caps.property("cycles", "device").enum_dynamic
    assert caps.has_property("render", "use_persistent_data")
    assert not caps.has_property("cycles", "no_such_property")
    assert caps.property("nope", "samples") is None


def test_best_cycles_device_prefers_optix(caps_fixture: dict) -> None:
    caps = Capabilities.model_validate(caps_fixture)
    assert caps.best_cycles_device() == "OPTIX"


def test_best_cycles_device_falls_back_to_cpu() -> None:
    caps = Capabilities(blender_version=(5, 0, 1))
    assert caps.best_cycles_device() == "CPU"


def test_support_problem() -> None:
    assert support_problem(Capabilities(blender_version=(4, 2, 0), engines=["CYCLES"])) is None
    problem = support_problem(Capabilities(blender_version=(4, 1, 1), version_string="4.1.1", engines=["CYCLES"]))
    assert problem is not None and "4.1.1" in problem and "4.2.0" in problem
    assert "engines" in support_problem(Capabilities(blender_version=(5, 0, 0)))


def test_fingerprint_changes_with_mtime_and_size(fake_blender: Path) -> None:
    first = blender_fingerprint(fake_blender)
    assert len(first) == 16
    assert blender_fingerprint(str(fake_blender)) == first

    fake_blender.write_bytes(b"MZ fake but longer")
    assert blender_fingerprint(fake_blender) != first

    stamp = time.time() + 100
    os.utime(fake_blender, (stamp, stamp))
    second = blender_fingerprint(fake_blender)
    assert second != first


def test_cache_round_trip(tmp_path: Path, fake_blender: Path, caps_fixture: dict) -> None:
    cache = tmp_path / "cache"
    assert load_cached(cache, fake_blender) is None

    caps = Capabilities.model_validate(caps_fixture)
    caps.fingerprint = blender_fingerprint(fake_blender)
    path = save_cache(cache, caps)
    assert path == cache_file(cache, caps.fingerprint)
    assert not path.with_name(path.name + ".tmp").exists()

    loaded = load_cached(cache, fake_blender)
    assert loaded is not None
    assert loaded.engines == caps.engines
    assert loaded.property("cycles", "samples").factory_value == caps.property("cycles", "samples").factory_value


def test_cache_ignored_when_binary_changed(tmp_path: Path, fake_blender: Path, caps_fixture: dict) -> None:
    caps = Capabilities.model_validate(caps_fixture)
    caps.fingerprint = blender_fingerprint(fake_blender)
    save_cache(tmp_path, caps)
    fake_blender.write_bytes(b"MZ reinstalled build")
    assert load_cached(tmp_path, fake_blender) is None


def test_corrupt_cache_is_ignored(tmp_path: Path, fake_blender: Path) -> None:
    cache_file(tmp_path, blender_fingerprint(fake_blender)).write_text("{ nope", encoding="utf-8")
    assert load_cached(tmp_path, fake_blender) is None


def test_run_probe_reads_output_and_cleans_tmp(tmp_path: Path, fake_blender: Path, caps_fixture: dict, monkeypatch) -> None:
    monkeypatch.setattr(caps_mod, "run_blender", _fake_run(caps_fixture))
    caps = run_probe(fake_blender, tmp_dir=tmp_path / "tmp")
    assert caps.blender_path == str(fake_blender)
    assert caps.fingerprint == blender_fingerprint(fake_blender)
    assert caps.probed_at
    assert list((tmp_path / "tmp").iterdir()) == []


def test_run_probe_passes_expected_arguments(tmp_path: Path, fake_blender: Path, caps_fixture: dict, monkeypatch) -> None:
    seen: dict = {}

    def spy(blender_path, args, **kwargs):
        seen["args"] = [str(a) for a in args]
        seen["timeout"] = kwargs.get("timeout")
        return _fake_run(caps_fixture)(blender_path, args, **kwargs)

    monkeypatch.setattr(caps_mod, "run_blender", spy)
    run_probe(fake_blender, tmp_dir=tmp_path, timeout=42)
    args = seen["args"]
    assert args[:4] == ["-b", "--factory-startup", "--python-exit-code", "1"]
    assert args[4] == "--python" and args[5].endswith("probe_caps.py")
    assert args[6] == "--" and args[7].endswith(".json")
    assert seen["timeout"] == 42


def test_run_probe_failure_raises_with_log(tmp_path: Path, fake_blender: Path, caps_fixture: dict, monkeypatch) -> None:
    monkeypatch.setattr(caps_mod, "run_blender", _fake_run(caps_fixture, ok=False))
    with pytest.raises(CapabilitiesError, match="exit code 1"):
        run_probe(fake_blender, tmp_dir=tmp_path)


def test_run_probe_missing_output_raises(tmp_path: Path, fake_blender: Path, caps_fixture: dict, monkeypatch) -> None:
    monkeypatch.setattr(caps_mod, "run_blender", _fake_run(caps_fixture, write=False))
    with pytest.raises(CapabilitiesError, match="no output"):
        run_probe(fake_blender, tmp_dir=tmp_path)


def test_run_probe_unreadable_output_raises(tmp_path: Path, fake_blender: Path, monkeypatch) -> None:
    monkeypatch.setattr(caps_mod, "run_blender", _fake_run({"blender_version": "not a version"}))
    with pytest.raises(CapabilitiesError, match="unreadable"):
        run_probe(fake_blender, tmp_dir=tmp_path)


def test_get_capabilities_uses_cache_on_second_call(tmp_path: Path, fake_blender: Path, caps_fixture: dict, monkeypatch) -> None:
    calls = {"n": 0}

    def counting(blender_path, args, **kwargs):
        calls["n"] += 1
        return _fake_run(caps_fixture)(blender_path, args, **kwargs)

    monkeypatch.setattr(caps_mod, "run_blender", counting)
    kwargs = {"cache_dir": tmp_path / "cache", "tmp_dir": tmp_path / "tmp"}
    first = get_capabilities(fake_blender, **kwargs)
    second = get_capabilities(fake_blender, **kwargs)
    assert calls["n"] == 1
    assert second.fingerprint == first.fingerprint

    get_capabilities(fake_blender, force=True, **kwargs)
    assert calls["n"] == 2


def test_get_capabilities_without_cache_dir_files(tmp_path: Path, fake_blender: Path, caps_fixture: dict, monkeypatch) -> None:
    monkeypatch.setattr(caps_mod, "run_blender", _fake_run(caps_fixture))
    cache = tmp_path / "deep" / "cache"
    get_capabilities(fake_blender, cache_dir=cache, tmp_dir=tmp_path / "tmp")
    assert list(cache.glob("capabilities_*.json"))
    shutil.rmtree(cache)

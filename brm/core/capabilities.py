"""Проба возможностей Blender и кэш результата (раздел 3.2 спеки).

Из JSON, который пишет ``scripts/probe_caps.py``, строится модель
``Capabilities``. Кэш лежит в ``capabilities_<fingerprint>.json``, где
fingerprint считается от пути, mtime и размера blender.exe: переустановили
Blender — проба повторится сама.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from brm.core.blender_process import describe_failure, run_blender, script_path

log = logging.getLogger(__name__)

MIN_SUPPORTED_VERSION = (4, 2, 0)
PROBE_SCRIPT = "probe_caps.py"
# Порядок предпочтения устройств Cycles. Для RTX 5070 правильный выбор — OPTIX.
DEVICE_PREFERENCE = ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL")


class EnumItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    identifier: str
    name: str = ""
    description: str = ""


class PropertyInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    identifier: str
    type: str
    name: str = ""
    description: str = ""
    subtype: str = ""
    is_readonly: bool = False
    is_array: bool = False
    array_length: int = 0
    hard_min: float | None = None
    hard_max: float | None = None
    soft_min: float | None = None
    soft_max: float | None = None
    step: float | None = None
    precision: int | None = None
    enum_items: list[EnumItem] = Field(default_factory=list)
    # True — элементы получены пробным присваиванием, а не из статического RNA.
    enum_dynamic: bool = False
    # Значение при factory-startup — «дефолт» с точки зрения пресетов.
    factory_value: Any = None

    def enum_identifiers(self) -> list[str]:
        return [item.identifier for item in self.enum_items]


class PropertyGroup(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rna_type: str = ""
    path: str = ""
    properties: dict[str, PropertyInfo] = Field(default_factory=dict)


class DeviceInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    type: str
    id: str = ""


class CyclesInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    available: bool = False
    devices: list[DeviceInfo] = Field(default_factory=list)
    compute_device_types: list[str] = Field(default_factory=list)


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="ignore")

    probe_version: int = 1
    blender_version: tuple[int, int, int]
    version_string: str = ""
    build_date: str = ""
    build_hash: str = ""
    binary_path: str = ""
    engines: list[str] = Field(default_factory=list)
    eevee_engine_id: str | None = None
    cycles: CyclesInfo = Field(default_factory=CyclesInfo)
    groups: dict[str, PropertyGroup] = Field(default_factory=dict)
    log: list[str] = Field(default_factory=list)
    # Добавляется приложением, а не пробой.
    blender_path: str = ""
    fingerprint: str = ""
    probed_at: str = ""

    def has_engine(self, identifier: str) -> bool:
        return identifier in self.engines

    def property(self, group: str, identifier: str) -> PropertyInfo | None:
        grp = self.groups.get(group)
        if grp is None:
            return None
        return grp.properties.get(identifier)

    def has_property(self, group: str, identifier: str) -> bool:
        return self.property(group, identifier) is not None

    def version_supported(self) -> bool:
        return tuple(self.blender_version) >= MIN_SUPPORTED_VERSION

    def best_cycles_device(self) -> str:
        """Лучший доступный тип устройства для ``--cycles-device``; ``CPU``, если GPU нет."""
        present = {d.type for d in self.cycles.devices}
        allowed = set(self.cycles.compute_device_types) or present
        for candidate in DEVICE_PREFERENCE:
            if candidate in present and candidate in allowed:
                return candidate
        return "CPU"


COMPUTE_AUTO = "auto"
COMPUTE_GPU = "gpu"
COMPUTE_GPU_CPU = "gpu_cpu"
COMPUTE_CPU = "cpu"
COMPUTE_MODES = (COMPUTE_AUTO, COMPUTE_GPU, COMPUTE_GPU_CPU, COMPUTE_CPU)


def device_for_mode(mode: str, caps: "Capabilities") -> tuple[str, bool]:
    """(тип устройства для ``--cycles-device``, помогает ли CPU) из режима.

    Спека: GPU по умолчанию, CPU — по явному выбору. Если GPU в системе нет,
    любой режим честно сваливается на CPU, а не притворяется, что считает на карте.
    """
    if mode == COMPUTE_CPU:
        return "CPU", False
    best = caps.best_cycles_device()
    if mode == COMPUTE_GPU_CPU:
        # На CPU-only машине «GPU + CPU» — это просто CPU.
        return best, best != "CPU"
    return best, False


class CapabilitiesError(RuntimeError):
    """Проба не удалась: Blender не запустился, упал или вернул неразборчивый JSON."""


def version_str(version: tuple[int, int, int] | list[int]) -> str:
    return ".".join(str(v) for v in version)


def blender_fingerprint(blender_path: str | os.PathLike[str]) -> str:
    """Отпечаток бинарника: путь + mtime + размер. Меняется при переустановке."""
    path = Path(blender_path).resolve()
    stat = path.stat()
    raw = f"{str(path).lower()}|{stat.st_mtime_ns}|{stat.st_size}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def cache_file(cache_dir: str | os.PathLike[str], fingerprint: str) -> Path:
    return Path(cache_dir) / f"capabilities_{fingerprint}.json"


def load_cached(
    cache_dir: str | os.PathLike[str], blender_path: str | os.PathLike[str]
) -> Capabilities | None:
    """Кэш для этого бинарника, если он есть, читается и не протух."""
    fingerprint = blender_fingerprint(blender_path)
    path = cache_file(cache_dir, fingerprint)
    if not path.is_file():
        return None
    try:
        caps = Capabilities.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError) as exc:
        log.warning("Ignoring unreadable capabilities cache %s: %s", path, exc)
        return None
    if caps.fingerprint != fingerprint:
        return None
    return caps


def save_cache(cache_dir: str | os.PathLike[str], caps: Capabilities) -> Path:
    path = cache_file(cache_dir, caps.fingerprint)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(caps.model_dump_json(indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path


def run_probe(
    blender_path: str | os.PathLike[str],
    *,
    tmp_dir: str | os.PathLike[str],
    timeout: float = 120.0,
    cancel: threading.Event | None = None,
) -> Capabilities:
    """Запускает probe_caps.py внутри Blender и разбирает результат."""
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out = tmp_dir / f"_brm_caps_{uuid.uuid4().hex}.json"
    args = [
        "-b",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        script_path(PROBE_SCRIPT),
        "--",
        out,
    ]
    result = run_blender(blender_path, args, timeout=timeout, cancel=cancel)
    try:
        if not result.ok:
            raise CapabilitiesError(describe_failure("Capabilities probe", result))
        if not out.is_file():
            raise CapabilitiesError(
                "Capabilities probe produced no output file\n" + result.tail()
            )
        try:
            data = json.loads(out.read_text(encoding="utf-8"))
            caps = Capabilities.model_validate(data)
        except (OSError, ValueError, ValidationError) as exc:
            raise CapabilitiesError(f"Capabilities probe output is unreadable: {exc}") from exc
    finally:
        try:
            out.unlink()
        except OSError:
            pass
    caps.blender_path = str(blender_path)
    caps.fingerprint = blender_fingerprint(blender_path)
    caps.probed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return caps


def get_capabilities(
    blender_path: str | os.PathLike[str],
    *,
    cache_dir: str | os.PathLike[str],
    tmp_dir: str | os.PathLike[str],
    force: bool = False,
    timeout: float = 120.0,
    cancel: threading.Event | None = None,
) -> Capabilities:
    """Кэш, если он свежий, иначе проба с записью в кэш."""
    if not force:
        cached = load_cached(cache_dir, blender_path)
        if cached is not None:
            return cached
    caps = run_probe(blender_path, tmp_dir=tmp_dir, timeout=timeout, cancel=cancel)
    save_cache(cache_dir, caps)
    return caps


def support_problem(caps: Capabilities) -> str | None:
    """Почему этот Blender не годится, или None, если всё в порядке."""
    if not caps.version_supported():
        return (
            f"Blender {caps.version_string or version_str(caps.blender_version)} is not "
            f"supported, {version_str(MIN_SUPPORTED_VERSION)} or newer is required"
        )
    if not caps.engines:
        return "Blender reported no render engines"
    return None

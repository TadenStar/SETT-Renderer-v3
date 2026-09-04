"""Подстройка пресета под конкретную машину (раздел 9 документа о настройках).

Пресет описывает намерение («ночной рендер», «тяжёлая симуляция») и обязан
оставаться переносимым между машинами, поэтому JSON не переписывается: тюнер
возвращает копию с урезанными значениями и список того, что изменил.

Два правила, из которых следует всё остальное.

**Не замедлять.** Первая версия понижала tile size под объём VRAM, и это оказалось
ошибкой: кадр 1920x1920 целиком помещается в тайл 2048, а в 1024 бьётся на четыре
куска, и замер на живом Blender дал +6.6% ко времени кадра на пустой сцене (на
насыщенной больше). Пиксели при этом те же — но приложение не имеет права
замедлять рендер ради страховки, которая на этой карте не нужна. Тайлы теперь
не трогаются вовсе: их задаёт сцена или явный выбор пользователя.

**Меняем только то, что не меняет картинку.** ``texture_limit_render`` уменьшает
разрешение текстур, и документ о настройках предупреждает: «Для крупных планов —
не трогать». Тихо ужать текстуры на пресете Final значит отдать клиенту не тот
рендер, который заказывали, поэтому texture limit сюда не входит. Он остаётся
в ``chunking.OOM_RETRY_STEPS``, где альтернатива не «чуть хуже текстуры», а
упавший рендер.

**Только урезаем, никогда не поднимаем.** Спека: «Дефолты должны быть безопасны
по VRAM и RAM. Никаких предположений о рендер-ферме». Включить обратно то, что
автор пресета выключил осознанно, нельзя: у Heavy Scene Persistent Data выключен
не от бедности железа, а потому что сцена тяжёлая. Отсюда же идемпотентность:
подстроенный пресет, поданный второй раз, не меняется.

Свойства — те же, что в ``chunking.OOM_RETRY_STEPS``, чтобы проактивная
подстройка и ретрай после нехватки памяти не тянули в разные стороны.
Чистые функции без Qt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from brm.core.hardware import HardwareInfo, round_gb
from brm.core.presets import Preset

DENOISING_USE_GPU = "cycles.denoising_use_gpu"
PERSISTENT_DATA = "render.use_persistent_data"

# Ниже этого объёма RAM Persistent Data держать нельзя: он занимает память
# постоянно, а BVH тяжёлой сцены её не оставляет.
MIN_RAM_GB_FOR_PERSISTENT_DATA = 16


@dataclass(frozen=True)
class VramTier:
    """Что урезаем на картах этого класса."""

    max_gb: int | None
    denoise_on_cpu: bool = False


# Проверяются сверху вниз по первому подходящему max_gb. Денойз на GPU занимает
# заметную VRAM сверх рендера (документ о настройках, раздел 4), поэтому на
# совсем маленьких картах он уходит на CPU: сам алгоритм OIDN тот же.
VRAM_TIERS: tuple[VramTier, ...] = (
    VramTier(max_gb=7, denoise_on_cpu=True),
    # 8 ГБ и больше: трогать нечего. Тайлы не наш вопрос — см. заголовок модуля.
    VramTier(max_gb=None),
)


@dataclass
class TuningResult:
    preset: Preset
    notes: list[str] = field(default_factory=list)
    changes: dict[str, Any] = field(default_factory=dict)

    def changed(self) -> bool:
        return bool(self.changes)

    def summary(self) -> str:
        return ", ".join(self.notes)


def tier_for_vram(vram_gb: int) -> VramTier:
    for tier in VRAM_TIERS:
        if tier.max_gb is None or vram_gb <= tier.max_gb:
            return tier
    return VRAM_TIERS[-1]


def tune_preset(preset: Preset, hardware: HardwareInfo, engine: str | None = None) -> TuningResult:
    """Копия пресета под это железо плюс объяснение каждого изменения.

    Железо неизвестно — возвращается тот же пресет без изменений: гадать
    вреднее, чем ничего не делать. ``engine`` — уже разрешённый движок сцены:
    на EEVEE секция Cycles всё равно не применяется, и обещать в интерфейсе
    урезание тайлов там нечестно.
    """
    cycles = dict(preset.cycles)
    common = dict(preset.common)
    notes: list[str] = []
    changes: dict[str, Any] = {}

    def apply(group: dict[str, Any], path: str, value: Any, note: str) -> None:
        group[path] = value
        changes[path] = value
        notes.append(note)

    is_cycles = engine is None or engine == "CYCLES"
    if hardware.vram_mb is not None and is_cycles:
        vram_gb = round_gb(hardware.vram_mb)
        tier = tier_for_vram(vram_gb)
        source = f"{vram_gb} GB VRAM"

        if tier.denoise_on_cpu and cycles.get(DENOISING_USE_GPU) is not False:
            apply(cycles, DENOISING_USE_GPU, False, f"denoising on CPU ({source})")

    if hardware.ram_mb is not None:
        ram_gb = round_gb(hardware.ram_mb)
        if ram_gb < MIN_RAM_GB_FOR_PERSISTENT_DATA and common.get(PERSISTENT_DATA) is not False:
            apply(common, PERSISTENT_DATA, False, f"Persistent Data off ({ram_gb} GB RAM)")

    if not changes:
        return TuningResult(preset=preset)
    return TuningResult(
        preset=preset.model_copy(update={"cycles": cycles, "common": common}),
        notes=notes,
        changes=changes,
    )

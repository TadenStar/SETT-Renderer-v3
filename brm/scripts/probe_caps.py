"""Проба возможностей Blender (раздел 3.2 спеки). Запускается ВНУТРИ Blender:

    blender.exe -b --factory-startup --python-exit-code 1 --python probe_caps.py -- <out.json>

Пишет JSON в файл, путь к которому приходит после «--». В stdout печатает только
строки с префиксом ``[BRM]``. Ничего не хардкодим: движки определяем пробным
присваиванием, свойства снимаем с живых RNA-объектов (в 5.x класса
``bpy.types.CyclesRenderSettings`` нет, а у ``HydraRenderEngine`` нет ``bl_idname``).
"""
import json
import sys
import traceback

import bpy

PROBE_VERSION = 1

# Кандидаты в движки. Реальный список — те, что удалось присвоить.
ENGINE_CANDIDATES = [
    "CYCLES",
    "BLENDER_EEVEE_NEXT",
    "BLENDER_EEVEE",
    "BLENDER_WORKBENCH",
    "HYDRA_STORM",
]
DEVICE_TYPE_CANDIDATES = ["NONE", "CUDA", "OPTIX", "HIP", "ONEAPI", "METAL"]
SIMPLE_TYPES = {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}

# Группы свойств: имя → (корень, путь атрибутов). Отсутствующие группы пропускаются.
GROUP_PATHS = {
    "render": ("scene", "render"),
    "image_settings": ("scene", "render.image_settings"),
    "ffmpeg": ("scene", "render.ffmpeg"),
    "view_settings": ("scene", "view_settings"),
    "cycles": ("scene", "cycles"),
    "eevee": ("scene", "eevee"),
    "eevee_ray_tracing": ("scene", "eevee.ray_tracing_options"),
    "view_layer_cycles": ("view_layer", "cycles"),
}


def out_path_from_argv():
    argv = sys.argv
    if "--" in argv:
        rest = argv[argv.index("--") + 1 :]
        if rest:
            return rest[0]
    raise RuntimeError("probe_caps: output path missing after '--'")


def _decode(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _jsonable(value):
    """Приводит значение RNA к JSON-совместимому виду."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    try:
        return [_jsonable(v) for v in value]
    except TypeError:
        return str(value)


def _resolve(root, path):
    obj = root
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


# Enum, чьи элементы Blender строит динамически (зависят от устройств и версии):
# статически они пустые. Кандидаты проверяются присваиванием, остаются только принятые.
DYNAMIC_ENUM_CANDIDATES = {
    "denoiser": ["OPENIMAGEDENOISE", "OPTIX"],
    "preview_denoiser": ["AUTO", "OPENIMAGEDENOISE", "OPTIX"],
    "sampling_pattern": [
        "AUTOMATIC",
        "SOBOL_BURLEY",
        "TABULATED_SOBOL",
        "BLUE_NOISE",
        "BLUE_NOISE_PURE",
        "BLUE_NOISE_FIRST",
        "BLUE_NOISE_ROUND",
    ],
    "compute_device_type": DEVICE_TYPE_CANDIDATES,
    # Color Management (раздел 6 настроек): список видов и look'ов зависит от
    # загруженного OCIO-конфига, статический RNA отдаёт не пустой список, а один
    # служебный элемент-заглушку — надёжнее реального пробного присваивания.
    "view_transform": ["AgX", "Standard", "Filmic", "Filmic Log", "AgX Punchy", "False Color", "Raw", "None"],
    "look": [
        "None",
        "AgX - Punchy",
        "AgX - Greyscale",
        "AgX - Very High Contrast",
        "AgX - High Contrast",
        "AgX - Medium High Contrast",
        "AgX - Base Contrast",
        "AgX - Medium Low Contrast",
        "AgX - Low Contrast",
        "AgX - Very Low Contrast",
    ],
}


def probe_dynamic_enum(owner, prop, log):
    """Элементы динамического enum через пробное присваивание, с восстановлением значения."""
    try:
        original = getattr(owner, prop.identifier)
    except Exception as exc:
        log.append(f"dynamic enum {prop.identifier}: value unreadable: {exc}")
        return []
    candidates = list(DYNAMIC_ENUM_CANDIDATES.get(prop.identifier, []))
    if isinstance(original, str) and original not in candidates:
        candidates.insert(0, original)
    accepted = []
    for ident in candidates:
        try:
            setattr(owner, prop.identifier, ident)
        except Exception:
            continue
        accepted.append({"identifier": ident, "name": ident, "description": ""})
    try:
        setattr(owner, prop.identifier, original)
    except Exception as exc:
        log.append(f"dynamic enum {prop.identifier}: could not restore {original!r}: {exc}")
    return accepted


def describe_property(owner, prop, log):
    info = {
        "identifier": prop.identifier,
        "type": prop.type,
        "name": prop.name,
        "description": prop.description,
        "subtype": getattr(prop, "subtype", ""),
        "is_readonly": bool(prop.is_readonly),
        "is_array": bool(getattr(prop, "is_array", False)),
        "array_length": int(getattr(prop, "array_length", 0) or 0),
    }
    if prop.type in ("INT", "FLOAT"):
        for key in ("hard_min", "hard_max", "soft_min", "soft_max", "step"):
            if hasattr(prop, key):
                info[key] = getattr(prop, key)
        if prop.type == "FLOAT" and hasattr(prop, "precision"):
            info["precision"] = prop.precision
    if prop.type == "ENUM":
        try:
            info["enum_items"] = [
                {"identifier": item.identifier, "name": item.name, "description": item.description}
                for item in prop.enum_items
            ]
        except Exception as exc:
            info["enum_items"] = []
            log.append(f"enum_items unavailable for {prop.identifier}: {exc}")
        # Некоторые динамические enum (OCIO view/look, устройство Cycles) статически
        # отдают не пустой список, а один служебный элемент-заглушку — count(enum_items)
        # не признак надёжности, для известных по имени свойств пробуем кандидатов всегда.
        needs_probe = not info["enum_items"] or prop.identifier in DYNAMIC_ENUM_CANDIDATES
        if needs_probe and not prop.is_readonly:
            probed = probe_dynamic_enum(owner, prop, log)
            if probed and (not info["enum_items"] or len(probed) > 1):
                info["enum_items"] = probed
                info["enum_dynamic"] = True
    if prop.type in SIMPLE_TYPES:
        # Значение при factory-startup. Это и есть «дефолт» с точки зрения пресетов.
        try:
            info["factory_value"] = _jsonable(getattr(owner, prop.identifier))
        except Exception as exc:
            log.append(f"factory value unavailable for {prop.identifier}: {exc}")
    return info


def describe_group(owner, log):
    props = {}
    for prop in owner.bl_rna.properties:
        if prop.identifier == "rna_type":
            continue
        try:
            props[prop.identifier] = describe_property(owner, prop, log)
        except Exception as exc:
            log.append(f"skip property {prop.identifier}: {exc}")
    return props


def probe_engines(scene, log):
    """Список движков через пробное присваивание. Возвращает то, что реально принялось."""
    render = scene.render
    original = render.engine
    candidates = []
    try:
        candidates.extend(i.identifier for i in render.bl_rna.properties["engine"].enum_items)
    except Exception as exc:
        log.append(f"engine enum_items unavailable: {exc}")
    for cls in bpy.types.RenderEngine.__subclasses__():
        ident = getattr(cls, "bl_idname", None)
        if ident:
            candidates.append(ident)
    candidates.extend(ENGINE_CANDIDATES)

    available = []
    for ident in candidates:
        if ident in available:
            continue
        try:
            render.engine = ident
        except Exception:
            continue
        available.append(ident)
    try:
        render.engine = original
    except Exception as exc:
        log.append(f"could not restore engine {original}: {exc}")
    return available


def probe_cycles(context, log):
    """Устройства Cycles и доступные типы compute_device_type."""
    result = {"available": False, "devices": [], "compute_device_types": []}
    addon = context.preferences.addons.get("cycles")
    if addon is None:
        log.append("cycles addon is not enabled")
        return result, None
    prefs = addon.preferences
    result["available"] = True
    try:
        prefs.refresh_devices()
    except Exception as exc:
        log.append(f"refresh_devices failed: {exc}")
    try:
        result["devices"] = [
            {"name": d.name, "type": d.type, "id": _decode(getattr(d, "id", ""))}
            for d in prefs.devices
        ]
    except Exception as exc:
        log.append(f"devices unavailable: {exc}")

    original = None
    try:
        original = prefs.compute_device_type
    except Exception as exc:
        log.append(f"compute_device_type unreadable: {exc}")
    for ident in DEVICE_TYPE_CANDIDATES:
        try:
            prefs.compute_device_type = ident
        except Exception:
            continue
        result["compute_device_types"].append(ident)
    if original is not None:
        try:
            prefs.compute_device_type = original
        except Exception as exc:
            log.append(f"could not restore compute_device_type {original}: {exc}")
    return result, prefs


def probe_groups(context, prefs, log):
    roots = {"scene": context.scene, "view_layer": context.view_layer}
    groups = {}
    for name, (root_name, path) in GROUP_PATHS.items():
        try:
            owner = _resolve(roots[root_name], path)
        except AttributeError as exc:
            log.append(f"group {name} unavailable: {exc}")
            continue
        groups[name] = {
            "rna_type": owner.bl_rna.identifier,
            "path": f"{root_name}.{path}",
            "properties": describe_group(owner, log),
        }
    if prefs is not None:
        groups["cycles_preferences"] = {
            "rna_type": prefs.bl_rna.identifier,
            "path": "preferences.addons['cycles'].preferences",
            "properties": describe_group(prefs, log),
        }
    return groups


def main():
    out_path = out_path_from_argv()
    log = []
    context = bpy.context
    engines = probe_engines(context.scene, log)
    cycles, prefs = probe_cycles(context, log)
    eevee_id = next((e for e in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE") if e in engines), None)

    data = {
        "probe_version": PROBE_VERSION,
        "blender_version": list(bpy.app.version),
        "version_string": bpy.app.version_string,
        "build_date": _decode(bpy.app.build_date),
        "build_hash": _decode(bpy.app.build_hash),
        "binary_path": bpy.app.binary_path,
        "engines": engines,
        "eevee_engine_id": eevee_id,
        "cycles": cycles,
        "groups": probe_groups(context, prefs, log),
        "log": log,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    print(f"[BRM] capabilities written: {out_path}")


try:
    main()
except Exception:
    for line in traceback.format_exc().splitlines():
        print(f"[BRM] {line}")
    raise

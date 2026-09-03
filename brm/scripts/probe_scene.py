"""Проба проекта (раздел 3.3 спеки). Запускается ВНУТРИ Blender:

    blender.exe -b "<file.blend>" --python-exit-code 1 --python probe_scene.py -- <out.json>

Без ``--factory-startup``: аддоны пользователя должны загрузиться, иначе часть
данных файла (FLIP Fluids, кастомные ноды) прочитается неправильно. Ничего не
рендерит и не сохраняет. Все обращения к RNA защищены: отсутствие свойства в
конкретной версии даёт запись в log, а не падение.
"""
import json
import os
import sys
import traceback

import bpy

PROBE_VERSION = 1

# Свойства, которые полезно показать в сводке «как в файле». Отсутствующие пропускаются.
SUMMARY_PATHS = (
    "cycles.samples",
    "cycles.adaptive_threshold",
    "cycles.use_adaptive_sampling",
    "cycles.use_denoising",
    "cycles.device",
    "eevee.taa_render_samples",
    "render.use_persistent_data",
    "render.use_simplify",
    "render.use_motion_blur",
)


def out_path_from_argv():
    argv = sys.argv
    if "--" in argv:
        rest = argv[argv.index("--") + 1 :]
        if rest:
            return rest[0]
    raise RuntimeError("probe_scene: output path missing after '--'")


def safe(fn, default, log, label):
    try:
        return fn()
    except Exception as exc:
        log.append(f"{label}: {exc}")
        return default


def _resolve(root, path):
    obj = root
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def compositor_tree(scene):
    """5.x: ``scene.compositing_node_group``; 4.x: ``scene.node_tree``. Проверяем оба."""
    for attr in ("compositing_node_group", "node_tree"):
        if hasattr(scene, attr):
            return getattr(scene, attr)
    return None


def sequencer_strip_count(scene):
    editor = getattr(scene, "sequence_editor", None)
    if editor is None:
        return 0
    for attr in ("strips_all", "strips", "sequences_all", "sequences"):
        collection = getattr(editor, attr, None)
        if collection is not None:
            return len(collection)
    return 0


def describe_scene(scene, log):
    name = scene.name
    render = scene.render
    image = render.image_settings
    tree = compositor_tree(scene)

    summary = {}
    for path in SUMMARY_PATHS:
        try:
            value = _resolve(scene, path)
        except AttributeError:
            continue
        if isinstance(value, (bool, int, float, str)):
            summary[path] = value

    return {
        "name": name,
        "engine": render.engine,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "frame_step": scene.frame_step,
        "frame_current": scene.frame_current,
        "fps": safe(lambda: render.fps / render.fps_base, 0.0, log, f"{name}.fps"),
        "resolution_x": render.resolution_x,
        "resolution_y": render.resolution_y,
        "resolution_percentage": render.resolution_percentage,
        "output_path": render.filepath,
        "file_format": safe(lambda: image.file_format, "", log, f"{name}.file_format"),
        "color_mode": safe(lambda: image.color_mode, "", log, f"{name}.color_mode"),
        "color_depth": safe(lambda: image.color_depth, "", log, f"{name}.color_depth"),
        "active_camera": scene.camera.name if scene.camera else None,
        "cameras": safe(
            lambda: sorted(o.name for o in scene.objects if o.type == "CAMERA"),
            [],
            log,
            f"{name}.cameras",
        ),
        "view_layers": safe(
            lambda: [{"name": vl.name, "use": bool(vl.use)} for vl in scene.view_layers],
            [],
            log,
            f"{name}.view_layers",
        ),
        "markers": safe(
            lambda: [
                {"name": m.name, "frame": m.frame, "camera": m.camera.name if m.camera else None}
                for m in scene.timeline_markers
            ],
            [],
            log,
            f"{name}.markers",
        ),
        "use_compositing": bool(render.use_compositing),
        "has_compositor_tree": bool(tree is not None and len(tree.nodes) > 0),
        "use_sequencer": bool(render.use_sequencer),
        "sequencer_strips": safe(lambda: sequencer_strip_count(scene), 0, log, f"{name}.sequencer"),
        "render_summary": summary,
    }


def missing_libraries(log):
    missing = []
    for lib in bpy.data.libraries:
        try:
            if not os.path.exists(bpy.path.abspath(lib.filepath)):
                missing.append(lib.filepath)
        except Exception as exc:
            log.append(f"library {lib.name}: {exc}")
    return missing


def main():
    out_path = out_path_from_argv()
    log = []
    scenes = []
    for scene in bpy.data.scenes:
        try:
            scenes.append(describe_scene(scene, log))
        except Exception as exc:
            log.append(f"scene {scene.name}: {exc}")

    active = None
    try:
        active = bpy.context.scene.name
    except Exception as exc:
        log.append(f"active scene: {exc}")
    if active is None and scenes:
        active = scenes[0]["name"]

    data = {
        "probe_version": PROBE_VERSION,
        "file_path": bpy.data.filepath,
        # bpy.data.version — версия ФОРМАТА файла (major, minor, subversion), а не релиз.
        # Сравнивать её можно только с bpy.app.version_file того же рода.
        "saved_with_version": list(bpy.data.version),
        "blender_version": list(bpy.app.version),
        "blender_version_file": list(bpy.app.version_file),
        "active_scene": active,
        "scenes": scenes,
        "enabled_addons": sorted(bpy.context.preferences.addons.keys()),
        "missing_libraries": missing_libraries(log),
        "log": log,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    print(f"[BRM] project info written: {out_path}")


try:
    main()
except Exception:
    for line in traceback.format_exc().splitlines():
        print(f"[BRM] {line}")
    raise

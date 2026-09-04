"""Анализ сцены: сколько объектов, геометрии и инстансов. Запускается ВНУТРИ Blender:

    blender.exe -b "<file.blend>" --python-exit-code 1 --python probe_stats.py -- <out.json> [scene]

Отдельно от probe_scene.py и только по кнопке: оценка геометрии считает
depsgraph и треугольники, а это на тяжёлой сцене небыстро. Открытие проекта
из-за этого тормозить не должно.

Треугольники считаются на render-уровне: у Subdivision viewport и render дают
разное число, а память ест именно render. Уровни поднимаются в памяти
запущенного процесса, исходный .blend не изменяется и не сохраняется.
"""
import json
import sys
import traceback

import bpy

PROBE_VERSION = 1
# Модификаторы, у которых viewport и render считают разное количество геометрии.
LEVEL_MODIFIERS = ("SUBSURF", "MULTIRES")


def raise_to_render_levels(scene, log):
    """Ставит модификаторам render-уровень. Только в памяти этого процесса."""
    raised = 0
    for obj in scene.objects:
        for modifier in getattr(obj, "modifiers", []) or []:
            if modifier.type not in LEVEL_MODIFIERS:
                continue
            render_levels = getattr(modifier, "render_levels", None)
            if render_levels is None or not hasattr(modifier, "levels"):
                continue
            if modifier.levels != render_levels:
                try:
                    modifier.levels = render_levels
                    raised += 1
                except Exception as exc:  # noqa: BLE001 — проба не должна падать
                    log.append(f"SKIP {obj.name}.{modifier.name}: {exc}")
    if raised:
        log.append(f"OK   raised {raised} modifier(s) to render levels")
    return raised


def triangles_of(obj, log):
    """Треугольники вычисленного объекта; 0 — не меш или геометрию не прочитать."""
    if obj.type != "MESH":
        return 0
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return 0
    try:
        mesh.calc_loop_triangles()
        return len(mesh.loop_triangles)
    except Exception as exc:  # noqa: BLE001 — битый меш не должен ронять анализ
        log.append(f"SKIP triangles of {obj.name}: {exc}")
        return 0


def collect(scene, log):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    real = instanced = triangles = instanced_triangles = 0
    meshes = 0
    by_type = {}
    for entry in depsgraph.object_instances:
        obj = entry.object
        tris = triangles_of(obj, log)
        if entry.is_instance:
            instanced += 1
            instanced_triangles += tris
        else:
            real += 1
            by_type[obj.type] = by_type.get(obj.type, 0) + 1
            if obj.type == "MESH":
                meshes += 1
        triangles += tris

    culled = sum(
        1
        for obj in scene.objects
        if getattr(getattr(obj, "cycles", None), "use_camera_cull", False)
    )
    return {
        "objects": len(scene.objects),
        "evaluated_objects": real,
        "instances": instanced,
        "meshes": meshes,
        "triangles": triangles,
        "instanced_triangles": instanced_triangles,
        "objects_by_type": by_type,
        "camera_culled_objects": culled,
    }


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    out_path = argv[0]
    scene_name = argv[1] if len(argv) > 1 else ""
    scene = bpy.data.scenes.get(scene_name) or bpy.context.scene

    log = []
    raise_to_render_levels(scene, log)
    try:
        bpy.context.view_layer.update()
    except Exception as exc:  # noqa: BLE001
        log.append(f"SKIP view_layer.update: {exc}")

    data = {"probe_version": PROBE_VERSION, "scene": scene.name, "log": log}
    data.update(collect(scene, log))
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    print(f"[BRM] scene stats written: {out_path}")


try:
    main()
except Exception:
    for line in traceback.format_exc().splitlines():
        print(f"[BRM] {line}")
    raise

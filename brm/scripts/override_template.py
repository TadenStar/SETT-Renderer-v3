"""Override-скрипт BRM: выполняется ВНУТРИ Blender после загрузки .blend и до рендера.

``core/override_builder.py`` дописывает перед этим текстом строку ``SETTINGS = ...``.
Исходный .blend не изменяется, правки живут только в памяти процесса.
Каждое присваивание идёт через ``safe_set()`` и печатается с префиксом ``[BRM]``:
пользователь видит, что применилось, а что эта версия Blender не поддержала.

Модуль импортируется и вне Blender (юнит-тесты safe_set), поэтому ``bpy`` опционален.
"""
try:
    import bpy
except ImportError:  # вне Blender — только для тестов
    bpy = None

OK, SKIP, FAIL = "OK", "SKIP", "FAIL"


def safe_set(obj, attr, value, log, label=None):
    """Присваивание RNA-свойства с проверкой ``hasattr`` и записью в лог (раздел 3.1 спеки)."""
    name = label or attr
    if obj is None:
        log.append(f"{SKIP} {name}: owner is None")
        return False
    if not hasattr(obj, attr):
        log.append(f"{SKIP} {name}: not available in this Blender build")
        return False
    try:
        setattr(obj, attr, value)
    except Exception as exc:
        log.append(f"{FAIL} {name}: {exc}")
        return False
    log.append(f"{OK}   {name} = {value!r}")
    return True


def safe_set_prefer(obj, attr, candidates, log, label=None):
    """Присваивает первый принятый вариант из списка: enum различаются между версиями."""
    name = label or attr
    if obj is None:
        log.append(f"{SKIP} {name}: owner is None")
        return False
    if not hasattr(obj, attr):
        log.append(f"{SKIP} {name}: not available in this Blender build")
        return False
    last_error = ""
    for index, candidate in enumerate(candidates):
        try:
            setattr(obj, attr, candidate)
        except Exception as exc:
            last_error = str(exc)
            continue
        note = "" if index == 0 else f" (fallback, {candidates[0]!r} rejected)"
        log.append(f"{OK}   {name} = {candidate!r}{note}")
        return True
    log.append(f"{FAIL} {name}: none of {list(candidates)!r} accepted: {last_error}")
    return False


def resolve_owner(roots, path):
    """``'scene.cycles.samples'`` → (roots['scene'].cycles, 'samples'). None, если цепочка оборвалась."""
    parts = path.split(".")
    if len(parts) < 2 or parts[0] not in roots:
        return None, parts[-1]
    obj = roots[parts[0]]
    for part in parts[1:-1]:
        obj = getattr(obj, part, None)
        if obj is None:
            return None, parts[-1]
    return obj, parts[-1]


def apply_assignments(roots, assignments, log):
    """Список ``[путь, значение]`` из пресета. Возвращает число удачных присваиваний."""
    applied = 0
    for path, value in assignments:
        owner, attr = resolve_owner(roots, path)
        if isinstance(value, dict) and "prefer" in value:
            ok = safe_set_prefer(owner, attr, list(value["prefer"]), log, label=path)
        else:
            ok = safe_set(owner, attr, value, log, label=path)
        if ok:
            applied += 1
    return applied


def summarize(log):
    counts = {OK: 0, SKIP: 0, FAIL: 0}
    for line in log:
        for key in counts:
            if line.startswith(key):
                counts[key] += 1
                break
    return f"override applied: ok={counts[OK]} skip={counts[SKIP]} fail={counts[FAIL]}"


def pick_scene(name):
    if name and name in bpy.data.scenes:
        return bpy.data.scenes[name]
    return bpy.context.scene


def configure_cycles(scene, settings, log):
    """Устройство Cycles: дублирует ``--cycles-device``, но виден результат в логе."""
    device_type = settings.get("compute_device_type")
    if not device_type:
        return
    cycles = getattr(scene, "cycles", None)
    safe_set(cycles, "device", "CPU" if device_type == "CPU" else "GPU", log, "scene.cycles.device")
    if device_type == "CPU":
        return
    addon = bpy.context.preferences.addons.get("cycles")
    prefs = addon.preferences if addon is not None else None
    if not safe_set(prefs, "compute_device_type", device_type, log, "cycles_preferences.compute_device_type"):
        return
    try:
        prefs.refresh_devices()
        use_cpu = bool(settings.get("cycles_use_cpu", False))
        for device in prefs.devices:
            device.use = device.type == device_type or (use_cpu and device.type == "CPU")
        enabled = [d.name for d in prefs.devices if d.use]
        log.append(f"{OK}   cycles devices enabled = {enabled!r}")
    except Exception as exc:
        log.append(f"{FAIL} cycles devices: {exc}")


def restrict_view_layers(scene, name, log):
    """Рендерить только выбранный view layer: у остальных снимаем ``use``."""
    if not name or name not in scene.view_layers:
        log.append(f"{SKIP} view_layer {name!r}: not found in scene {scene.name!r}")
        return
    for layer in scene.view_layers:
        safe_set(layer, "use", layer.name == name, log, f"view_layers[{layer.name!r}].use")


def main():
    settings = globals().get("SETTINGS", {})
    log = []
    scene = pick_scene(settings.get("scene"))
    log.append(f"{OK}   scene = {scene.name!r}")
    layer_name = settings.get("only_view_layer")
    view_layer = scene.view_layers.get(layer_name) if layer_name else None
    if view_layer is None and len(scene.view_layers):
        view_layer = scene.view_layers[0]
    roots = {
        "scene": scene,
        "render": scene.render,
        "cycles": getattr(scene, "cycles", None),
        "eevee": getattr(scene, "eevee", None),
        "view_layer": view_layer,
        "preferences": bpy.context.preferences,
    }
    if layer_name:
        restrict_view_layers(scene, layer_name, log)
    if settings.get("engine"):
        safe_set(scene.render, "engine", settings["engine"], log, "scene.render.engine")
    if settings.get("disable_sequencer", True):
        # Мусор в секвенсоре подменяет картинку 3D-сцены (раздел 7 спеки).
        safe_set(scene.render, "use_sequencer", False, log, "scene.render.use_sequencer")
    if "use_compositing" in settings:
        safe_set(scene.render, "use_compositing", bool(settings["use_compositing"]), log, "scene.render.use_compositing")
    if scene.render.engine == "CYCLES":
        configure_cycles(scene, settings, log)
    apply_assignments(roots, settings.get("assignments", []), log)

    for line in log:
        print(f"[BRM] {line}", flush=True)
    print(f"[BRM] {summarize(log)}", flush=True)
    failed = sum(1 for line in log if line.startswith(FAIL))
    if failed and settings.get("strict"):
        raise RuntimeError(f"{failed} override assignment(s) failed")


if bpy is not None:
    main()

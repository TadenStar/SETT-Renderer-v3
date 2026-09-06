"""Генератор тестовой сцены для замеров скорости. Запускается ВНУТРИ Blender:

    blender.exe -b --factory-startup --python tools/make_bench_scene.py -- <out.blend> [seed]

Зачем генератор, а не готовый .blend: сцена воспроизводится по seed, не лежит
бинарником в репозитории и одинаково собирается на любой версии Blender, на
которой мы меряем (5.0.1 и 5.3 Alpha с DLSS дают разные результаты, сравнивать
их нужно на одной и той же геометрии).

Состав по заказу Павла: сто объектов из Сюзанн, кубов и цилиндров, около сорока
уникальных материалов, нормали, умеренный дисплейсмент на нескольких объектах
и десять источников света. Числа собраны в константах ниже — их можно крутить,
не разбираясь в коде.
"""
import random
import sys

import bpy

OBJECTS = 100
MATERIALS = 40
LIGHTS = 10
# Дисплейсмент дорогой по памяти и времени, поэтому его немного: он должен быть
# заметен в замерах, но не превращать сцену в симуляцию.
DISPLACED = 4
# Каждый пятый материал получает настоящую картинку нормалей: процедурный bump
# ничего не весит, а текстуры занимают VRAM — в реальных сценах Павла их много.
IMAGE_NORMAL_EVERY = 5
NORMAL_MAP_SIZE = 512
SUBSURF_RENDER_LEVELS = 2
FIELD = 9.0  # половина стороны площадки, по которой раскиданы объекты


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.textures):
        for item in list(collection):
            collection.remove(item)


def make_normal_image(name, rng):
    """Картинка нормалей: ровный синеватый фон плюс шум по X и Y.

    Генерируется в памяти, а не читается с диска: файл пришлось бы хранить
    в репозитории, а нам нужна только нагрузка на память и шейдер.
    """
    image = bpy.data.images.new(name, NORMAL_MAP_SIZE, NORMAL_MAP_SIZE, alpha=False, float_buffer=False)
    image.colorspace_settings.name = "Non-Color"
    pixels = []
    for _ in range(NORMAL_MAP_SIZE * NORMAL_MAP_SIZE):
        pixels += [0.5 + rng.uniform(-0.25, 0.25), 0.5 + rng.uniform(-0.25, 0.25), 1.0, 1.0]
    image.pixels = pixels
    image.pack()
    return image


def make_material(index, rng):
    material = bpy.data.materials.new(f"Bench_{index:02d}")
    material.use_nodes = True
    tree = material.node_tree
    bsdf = tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rng.random(), rng.random(), rng.random(), 1.0)
    bsdf.inputs["Roughness"].default_value = rng.uniform(0.08, 0.95)
    bsdf.inputs["Metallic"].default_value = rng.choice([0.0, 0.0, 0.0, 1.0])

    if index % IMAGE_NORMAL_EVERY == 0:
        # Настоящая карта нормалей через Normal Map — путь с текстурой в памяти.
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.image = make_normal_image(f"Normal_{index:02d}", rng)
        normal_map = tree.nodes.new("ShaderNodeNormalMap")
        normal_map.inputs["Strength"].default_value = rng.uniform(0.4, 1.2)
        tree.links.new(texture.outputs["Color"], normal_map.inputs["Color"])
        tree.links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])
    else:
        # Процедурный рельеф через Bump: геометрии не добавляет, шейдер грузит.
        noise = tree.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = rng.uniform(4.0, 40.0)
        bump = tree.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = rng.uniform(0.1, 0.6)
        tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
        tree.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return material


def add_object(kind, location, rng):
    if kind == "monkey":
        bpy.ops.mesh.primitive_monkey_add(size=rng.uniform(0.8, 1.6), location=location)
    elif kind == "cube":
        bpy.ops.mesh.primitive_cube_add(size=rng.uniform(0.8, 2.0), location=location)
    else:
        bpy.ops.mesh.primitive_cylinder_add(
            radius=rng.uniform(0.3, 0.9), depth=rng.uniform(1.0, 2.8), vertices=rng.choice([16, 32, 64]), location=location
        )
    obj = bpy.context.object
    obj.rotation_euler = (rng.uniform(0, 3.14), rng.uniform(0, 3.14), rng.uniform(0, 3.14))
    return obj


def add_displacement(obj, index, rng):
    texture = bpy.data.textures.new(f"Disp_{index}", type="CLOUDS")
    texture.noise_scale = rng.uniform(0.3, 1.2)
    # Дисплейсменту нужна геометрия, иначе смещать нечего.
    subdivide = obj.modifiers.new(f"DispSubdiv_{index}", type="SUBSURF")
    subdivide.levels = 1
    subdivide.render_levels = 3
    displace = obj.modifiers.new(f"Displace_{index}", type="DISPLACE")
    displace.texture = texture
    displace.strength = rng.uniform(0.15, 0.45)  # умеренный, как просили


def build(out_path, seed):
    rng = random.Random(seed)
    clear_scene()
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.render.resolution_x = scene.render.resolution_y = 1440
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.frame_start, scene.frame_end = 1, 240

    materials = [make_material(i, rng) for i in range(MATERIALS)]

    bpy.ops.mesh.primitive_plane_add(size=FIELD * 5)
    floor = bpy.context.object
    floor.data.materials.append(materials[0])

    kinds = ["monkey", "cube", "cylinder"]
    objects = []
    for index in range(OBJECTS):
        kind = kinds[index % len(kinds)]
        location = (rng.uniform(-FIELD, FIELD), rng.uniform(-FIELD, FIELD), rng.uniform(0.4, 6.0))
        obj = add_object(kind, location, rng)
        obj.data.materials.append(rng.choice(materials))
        if kind == "monkey":
            modifier = obj.modifiers.new("Subdivision", type="SUBSURF")
            modifier.levels = 1
            modifier.render_levels = SUBSURF_RENDER_LEVELS
        objects.append(obj)

    for index, obj in enumerate(rng.sample(objects, DISPLACED)):
        add_displacement(obj, index, rng)

    for index in range(LIGHTS):
        kind = ("AREA", "POINT", "SPOT")[index % 3]
        bpy.ops.object.light_add(
            type=kind,
            location=(rng.uniform(-FIELD, FIELD), rng.uniform(-FIELD, FIELD), rng.uniform(5.0, 14.0)),
        )
        light = bpy.context.object.data
        light.energy = rng.uniform(500, 4000) if kind != "AREA" else rng.uniform(2000, 9000)
        light.color = (rng.uniform(0.6, 1.0), rng.uniform(0.6, 1.0), rng.uniform(0.6, 1.0))

    bpy.ops.object.camera_add(location=(FIELD * 1.7, -FIELD * 1.7, FIELD * 1.1), rotation=(1.05, 0.0, 0.78))
    scene.camera = bpy.context.object

    bpy.ops.wm.save_as_mainfile(filepath=out_path)
    print(f"[BENCH] scene saved: {out_path}")
    print(f"[BENCH] objects={len(scene.objects)} materials={len(bpy.data.materials)} images={len(bpy.data.images)}")


argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
build(argv[0], int(argv[1]) if len(argv) > 1 else 20260906)

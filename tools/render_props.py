"""aiparty props v2 — Cycles renders. Usage: python3 render_props2.py <dice|cup|revolver> <out.png>"""
import bpy, math, sys

SCENE, OUT = sys.argv[-2], sys.argv[-1]

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
sc.render.engine = 'CYCLES'
sc.cycles.samples = 128
sc.cycles.use_denoising = True
sc.render.resolution_x = 1280
sc.render.resolution_y = 960
sc.view_settings.look = 'AgX - Punchy'

def mat(name, **kw):
    m = bpy.data.materials.new(name); m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    for k, v in kw.items():
        try: b.inputs[k].default_value = v
        except Exception: pass
    return m

def obj_add(mesh_op, name, **kw):
    mesh_op(**kw)
    o = bpy.context.active_object; o.name = name
    return o

def set_mat(o, m):
    o.data.materials.clear(); o.data.materials.append(m)

def bevel(o, w=0.13, seg=5):
    md = o.modifiers.new('bv', 'BEVEL'); md.width = w; md.segments = seg
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.modifier_apply(modifier='bv')

def shade_smooth(o):
    bpy.context.view_layer.objects.active = o
    o.select_set(True); bpy.ops.object.shade_smooth(); o.select_set(False)

def felt_ground():
    m = bpy.data.materials.new('feltmat'); m.use_nodes = True
    nt = m.node_tree; b = nt.nodes["Principled BSDF"]
    b.inputs['Base Color'].default_value = (0.032, 0.098, 0.058, 1)
    b.inputs['Roughness'].default_value = 0.95
    b.inputs['Sheen Weight'].default_value = 0.7
    noise = nt.nodes.new('ShaderNodeTexNoise')
    noise.inputs['Scale'].default_value = 900; noise.inputs['Detail'].default_value = 8
    bump = nt.nodes.new('ShaderNodeBump'); bump.inputs['Strength'].default_value = 0.25
    nt.links.new(noise.outputs['Fac'], bump.inputs['Height'])
    nt.links.new(bump.outputs['Normal'], b.inputs['Normal'])
    g = obj_add(bpy.ops.mesh.primitive_plane_add, 'ground', size=140)
    set_mat(g, m)

def lights_and_cam(cam_loc, cam_target=(0,0,1.0), fstop=4.0, key_power=2200, lens=85):
    key = obj_add(bpy.ops.object.light_add, 'L_key', type='AREA', location=(14,-10,16))
    key.data.size = 22; key.data.energy = key_power; key.data.color = (1.0, 0.96, 0.9)
    fill = obj_add(bpy.ops.object.light_add, 'L_fill', type='AREA', location=(-16,6,12))
    fill.data.size = 30; fill.data.energy = 600; fill.data.color = (0.85, 0.9, 1.0)
    rim_l = obj_add(bpy.ops.object.light_add, 'L_rim', type='AREA', location=(-4,14,10))
    rim_l.data.size = 10; rim_l.data.energy = 750
    sc.world = bpy.data.worlds.new('w'); sc.world.use_nodes = True
    sc.world.node_tree.nodes['Background'].inputs['Color'].default_value = (0.01,0.012,0.011,1)
    tgt = obj_add(bpy.ops.object.empty_add, 'cam_target', location=cam_target)
    for l in (key, fill, rim_l):
        c = l.constraints.new('TRACK_TO'); c.target = tgt
    cam = obj_add(bpy.ops.object.camera_add, 'cam', location=cam_loc)
    cam.data.lens = lens; cam.data.dof.use_dof = True
    cam.data.dof.focus_object = tgt; cam.data.dof.aperture_fstop = fstop
    tc = cam.constraints.new('TRACK_TO'); tc.target = tgt
    sc.camera = cam

PIP = {1:[(0,0)],2:[(-1,-1),(1,1)],3:[(-1,-1),(0,0),(1,1)],
       4:[(-1,-1),(-1,1),(1,-1),(1,1)],5:[(-1,-1),(-1,1),(0,0),(1,-1),(1,1)],
       6:[(-1,-1),(-1,0),(-1,1),(1,-1),(1,0),(1,1)]}
FACES = [((0,0,1),1),((0,0,-1),6),((0,1,0),3),((0,-1,0),4),((1,0,0),5),((-1,0,0),2)]

def build_die(loc, rot_z, m_body, m_black, m_red):
    a = 1.0
    d = obj_add(bpy.ops.mesh.primitive_cube_add, 'die', size=2*a, location=(0,0,0))
    bevel(d, w=0.16, seg=7); shade_smooth(d)
    set_mat(d, m_body)
    parts = [d]; g = 0.52
    for n, val in FACES:
        red = val in (1,4); big = (val == 1)
        for (u,v) in PIP[val]:
            r = 0.30 if big else 0.155
            if n[2]: p = (u*g, v*g, n[2]*a)
            elif n[1]: p = (u*g, n[1]*a, v*g)
            else: p = (n[0]*a, u*g, v*g)
            pip = obj_add(bpy.ops.mesh.primitive_uv_sphere_add, 'pip', radius=r,
                          location=p, segments=24, ring_count=12)
            s = [1,1,1]; ax = 0 if n[0] else (1 if n[1] else 2)
            s[ax] = 0.32; pip.scale = s
            off = [0,0,0]; off[ax] = -0.034 * (1 if sum(n) > 0 else -1)
            pip.location = (p[0]+off[0], p[1]+off[1], p[2]+off[2])
            shade_smooth(pip); set_mat(pip, m_red if red else m_black)
            parts.append(pip)
    for o in parts: o.select_set(True)
    bpy.context.view_layer.objects.active = d
    bpy.ops.object.join()
    d.rotation_euler = (0, 0, rot_z)
    d.location = (loc[0], loc[1], a)
    return d

def dice_mats():
    ivory = mat('ivory', **{'Base Color':(0.93,0.91,0.85,1),'Roughness':0.32,'Coat Weight':0.4})
    blk = mat('pip_black', **{'Base Color':(0.015,0.015,0.017,1),'Roughness':0.25})
    red = mat('pip_red', **{'Base Color':(0.45,0.022,0.014,1),'Roughness':0.25})
    return ivory, blk, red

def scene_dice():
    felt_ground()
    ivory, blk, red = dice_mats()
    build_die((-2.3, 1.2), math.radians(18), ivory, blk, red)
    build_die((0.4, -0.6), math.radians(-31), ivory, blk, red)
    build_die((2.6, 1.8), math.radians(64), ivory, blk, red)
    lights_and_cam(cam_loc=(0.4, -14.5, 9.5), cam_target=(0.2, 0.4, 0.8), fstop=3.2)

def scene_cup():
    felt_ground()
    body = obj_add(bpy.ops.mesh.primitive_cone_add, 'cup',
                   vertices=96, radius1=4.3, radius2=3.55, depth=9.4, location=(0,0,4.7))
    shade_smooth(body)
    mcup = bpy.data.materials.new('cupblack'); mcup.use_nodes = True
    nt = mcup.node_tree; b = nt.nodes['Principled BSDF']
    b.inputs['Base Color'].default_value = (0.04,0.04,0.045,1)
    b.inputs['Roughness'].default_value = 0.5
    b.inputs['Coat Weight'].default_value = 0.25
    noise = nt.nodes.new('ShaderNodeTexNoise')
    noise.inputs['Scale'].default_value = 260; noise.inputs['Detail'].default_value = 6
    bump = nt.nodes.new('ShaderNodeBump'); bump.inputs['Strength'].default_value = 0.06
    nt.links.new(noise.outputs['Fac'], bump.inputs['Height'])
    nt.links.new(bump.outputs['Normal'], b.inputs['Normal'])
    set_mat(body, mcup)
    ring = obj_add(bpy.ops.mesh.primitive_torus_add, 'mouthring',
                   major_radius=4.18, minor_radius=0.22, location=(0,0,0.16),
                   major_segments=96, minor_segments=24)
    shade_smooth(ring)
    mred = mat('feltred', **{'Base Color':(0.30,0.02,0.02,1),'Roughness':0.9,'Sheen Weight':0.8})
    set_mat(ring, mred)
    cap = obj_add(bpy.ops.mesh.primitive_cylinder_add, 'cap',
                  vertices=96, radius=3.72, depth=0.5, location=(0,0,9.55))
    bevel(cap, w=0.12, seg=4); shade_smooth(cap)
    set_mat(cap, mcup)
    ivory, blk, red = dice_mats()
    build_die((6.6,-1.6), math.radians(40), ivory, blk, red)
    lights_and_cam(cam_loc=(4.5, -30, 10.5), cam_target=(1.6, 0, 3.8), fstop=5.5, key_power=3800, lens=60)

def metal_mat():
    m = bpy.data.materials.new('gunmetal'); m.use_nodes = True
    b = m.node_tree.nodes['Principled BSDF']
    b.inputs['Base Color'].default_value = (0.36,0.37,0.40,1)
    b.inputs['Metallic'].default_value = 1.0
    b.inputs['Roughness'].default_value = 0.42
    b.inputs['Anisotropic'].default_value = 0.3
    return m

def wood_mat():
    m = bpy.data.materials.new('walnut'); m.use_nodes = True
    nt = m.node_tree; b = nt.nodes['Principled BSDF']
    b.inputs['Roughness'].default_value = 0.38
    b.inputs['Coat Weight'].default_value = 0.5
    wave = nt.nodes.new('ShaderNodeTexWave')
    wave.inputs['Scale'].default_value = 3.2
    wave.inputs['Distortion'].default_value = 6.5
    wave.inputs['Detail'].default_value = 3
    ramp = nt.nodes.new('ShaderNodeValToRGB')
    ramp.color_ramp.elements[0].color = (0.16,0.065,0.025,1)
    ramp.color_ramp.elements[1].color = (0.35,0.16,0.06,1)
    nt.links.new(wave.outputs['Fac'], ramp.inputs['Fac'])
    nt.links.new(ramp.outputs['Color'], b.inputs['Base Color'])
    return m

def scene_revolver():
    felt_ground()
    mm, mw = metal_mat(), wood_mat()
    P = []  # (obj, material)
    def add(o, m=None): P.append((o, m or mm)); return o
    def rx(o): o.rotation_euler = (0, math.radians(90), 0)
    # profile built in XZ plane, y = thickness
    bar = add(obj_add(bpy.ops.mesh.primitive_cylinder_add, 'barrel', vertices=64,
                      radius=0.52, depth=6.2, location=(-5.5, 0, 0.35)))
    rx(bar); bar.scale[1] = 0.85; shade_smooth(bar)
    lug = add(obj_add(bpy.ops.mesh.primitive_cylinder_add, 'lug', vertices=48,
                      radius=0.26, depth=4.4, location=(-5.0, 0, -0.40)))
    rx(lug); shade_smooth(lug)
    sight = add(obj_add(bpy.ops.mesh.primitive_cube_add, 'sight', size=1, location=(-8.3, 0, 0.78)))
    sight.scale = (0.42, 0.18, 0.26)
    cyl = add(obj_add(bpy.ops.mesh.primitive_cylinder_add, 'cyl', vertices=96,
                      radius=1.25, depth=2.8, location=(-0.55, 0, 0)))
    rx(cyl); shade_smooth(cyl)
    for i in range(6):
        a = i * math.pi/3 + math.pi/6
        fl = obj_add(bpy.ops.mesh.primitive_cylinder_add, 'fl', vertices=32,
                     radius=0.2, depth=1.7,
                     location=(-0.55, math.cos(a)*1.42, math.sin(a)*1.42))
        rx(fl)
        md = cyl.modifiers.new('b','BOOLEAN'); md.object = fl; md.operation = 'DIFFERENCE'
        bpy.context.view_layer.objects.active = cyl
        bpy.ops.object.modifier_apply(modifier='b')
        bpy.data.objects.remove(fl, do_unlink=True)
    fr = add(obj_add(bpy.ops.mesh.primitive_cube_add, 'frame', size=1, location=(1.55, 0, 0.05)))
    fr.scale = (1.8, 0.62, 1.35); bevel(fr, w=0.18, seg=4); shade_smooth(fr)
    top = add(obj_add(bpy.ops.mesh.primitive_cube_add, 'top', size=1, location=(-0.5, 0, 1.30)))
    top.scale = (3.2, 0.5, 0.2); bevel(top, w=0.07, seg=3)
    nose = add(obj_add(bpy.ops.mesh.primitive_cube_add, 'nose', size=1, location=(-2.5, 0, -0.85)))
    nose.scale = (1.0, 0.55, 0.5); bevel(nose, w=0.12, seg=3)
    hm = add(obj_add(bpy.ops.mesh.primitive_cube_add, 'hammer', size=1, location=(2.3, 0, 0.78)))
    hm.scale = (0.55, 0.28, 0.5); hm.rotation_euler = (0, math.radians(-15), 0)
    bevel(hm, w=0.07, seg=3)
    tg = add(obj_add(bpy.ops.mesh.primitive_torus_add, 'guard',
                     major_radius=0.9, minor_radius=0.14, location=(1.0, 0, -1.35),
                     major_segments=64, minor_segments=16))
    tg.rotation_euler = (math.radians(90), 0, 0); shade_smooth(tg)
    trg = add(obj_add(bpy.ops.mesh.primitive_cube_add, 'trigger', size=1, location=(0.95, 0, -1.05)))
    trg.scale = (0.14, 0.18, 0.5); trg.rotation_euler = (0, math.radians(15), 0)
    gr = add(obj_add(bpy.ops.mesh.primitive_cube_add, 'grip', size=1, location=(2.55, 0, -1.3)), mw)
    gr.scale = (0.85, 0.55, 1.8); gr.rotation_euler = (0, math.radians(-22), 0)
    bevel(gr, w=0.28, seg=6); shade_smooth(gr)
    md2 = add(obj_add(bpy.ops.mesh.primitive_cylinder_add, 'medal', vertices=32,
                      radius=0.2, depth=0.62, location=(2.95, 0, -1.6)))
    md2.rotation_euler = (math.radians(90), 0, 0); shade_smooth(md2)
    for o, m in P: set_mat(o, m)
    # lay flat on felt: parent to empty, rotate -90 about X, lift by cylinder radius
    root = obj_add(bpy.ops.object.empty_add, 'gunroot', location=(0,0,0))
    for o, _ in P: o.parent = root
    root.rotation_euler = (math.radians(-90), 0, math.radians(6))
    root.location = (0.6, 0, 1.27)
    lights_and_cam(cam_loc=(-0.9, -3.5, 23.5), cam_target=(-1.5, -0.3, 0.4),
                   fstop=7.0, key_power=3600, lens=45)

{'dice': scene_dice, 'cup': scene_cup, 'revolver': scene_revolver}[SCENE]()
sc.render.filepath = OUT
bpy.ops.render.render(write_still=True)
print('rendered', SCENE, '->', OUT)

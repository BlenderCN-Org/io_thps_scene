#############################################
# THUG2 SCENE EXPORT
#############################################
import bpy
import bmesh
import struct
import mathutils
import math
from bpy.props import *
from . helpers import *
from . material import *
from bpy_extras.io_utils import ExportHelper
from . export_shared import *

# METHODS
#############################################
#----------------------------------------------------------------------------------

def export_scn_sectors_ug2(output_file, operator=None):
    def w(fmt, *args):
        output_file.write(struct.pack(fmt, *args))

    exported_checksums = {}

    bm = bmesh.new()
    p = Printer()
    out_objects = [o for o in bpy.data.objects
                   if (o.type == "MESH"
                    and getattr(o, 'thug_export_scene', True)
                    and not o.get('thug_autosplit_object_no_export_hack', False))]

    object_counter = 0
    object_amount_offset = output_file.tell()
    w("i", 0)
    for ob in out_objects:
        LOG.debug("exporting object: {}".format(ob))
        # bpy.ops.object.mode_set(mode="OBJECT")
        original_object = ob
        original_object_name = ob.name
        is_levelobject = ob.thug_object_class == "LevelObject"
        if is_levelobject:
            lo_matrix = mathutils.Matrix.Identity(4)
            lo_matrix[0][0] = ob.scale[0]
            lo_matrix[1][1] = ob.scale[1]
            lo_matrix[2][2] = ob.scale[2]
        ob.name = "TEMP_OBJECT___"
        try:
            final_mesh = ob.to_mesh(bpy.context.scene, True, 'PREVIEW')
            # LOG.debug("object vc layers: {}", len(ob.data.vertex_colors))
            temporary_object = _make_temp_obj(final_mesh)
            temporary_object.name = original_object_name
            try:
                bpy.context.scene.objects.link(temporary_object)
                temporary_object.matrix_world = ob.matrix_world

                if (operator and
                    operator.generate_vertex_color_shading and
                    len(temporary_object.data.polygons) != 0 and
                    not ob.get("thug_this_is_autosplit_temp_object")):
                    _generate_lambert_shading(temporary_object)

                if _need_to_flip_normals(ob):
                    _flip_normals(temporary_object)

                ob = temporary_object

                object_counter += 1
                final_mesh = ob.data

                bm.clear()
                # final_mesh.calc_normals()

                bm.from_mesh(final_mesh)
                bmesh.ops.triangulate(bm, faces=bm.faces)
                bm.to_mesh(final_mesh)
                final_mesh.calc_normals_split()
                bm.clear()
                bm.from_mesh(final_mesh)

                flags = 0 if not is_levelobject else SECFLAGS_HAS_VERTEX_NORMALS
                # flags = 0 # SECFLAGS_HAS_VERTEX_NORMALS
                if True or len(bm.loops.layers.uv):
                    flags |= SECFLAGS_HAS_TEXCOORDS
                if True or len(bm.loops.layers.color):
                    flags |= SECFLAGS_HAS_VERTEX_COLORS
                if len(original_object.vertex_groups):
                    flags |= SECFLAGS_HAS_VERTEX_WEIGHTS
                    flags |= SECFLAGS_HAS_VERTEX_NORMALS

                mats_to_faces = {}
                for face in bm.faces:
                    face_list = mats_to_faces.get(face.material_index)
                    if face_list:
                        face_list.append(face)
                    else:
                        mats_to_faces[face.material_index] = [face]
                ob_checksum = crc_from_string(bytes(get_clean_name(ob), 'ascii'))
                LOG.debug("the checksum is {}".format(ob_checksum))
                if ob_checksum in exported_checksums:
                    if operator:
                        operator.report({"WARNING"}, "Object {} and {} have the same checksum: {}".format(
                            ob.name, exported_checksums[ob_checksum], ob_checksum))
                else:
                    exported_checksums[ob_checksum] = ob.name
                w("I", ob_checksum)  # checksum
                w("i", -1)  # bone index
                w("I", flags)  # flags
                w("I", len([fs for fs in mats_to_faces.values() if fs]))  # number of meshes
                if is_levelobject:
                    # bbox = get_bbox2(final_mesh.vertices, mathutils.Matrix.Identity(4))
                    bbox = get_bbox2(final_mesh.vertices, lo_matrix)
                else:
                    bbox = get_bbox2(final_mesh.vertices, ob.matrix_world)
                w("6f",
                    bbox[0][0], bbox[0][1], bbox[0][2],
                    bbox[1][0], bbox[1][1], bbox[1][2])  # bbox
                bsphere = get_sphere_from_bbox(bbox)
                w("4f", *bsphere)  # bounding sphere

                for mat_index, mat_faces in mats_to_faces.items():
                    if len(mat_faces) == 0: continue
                    # TODO fix this
                    # should recalc bbox for this mesh
                    w("4f", *bsphere)
                    w("6f",
                        bbox[0][0], bbox[0][1], bbox[0][2],
                        bbox[1][0], bbox[1][1], bbox[1][2])  # bbox
                    w("I", 0) # 131072)  # flags/type?
                    the_material = len(ob.material_slots) and ob.material_slots[mat_index].material
                    if not the_material:
                        the_material = bpy.data.materials["_THUG_DEFAULT_MATERIAL_"]
                    mat_checksum = crc_from_string(bytes(the_material.name, 'ascii'))
                    w("I", mat_checksum)  # material checksum
                    w("I", 1)  # num of index lod levels

                    nonsplit_verts = {vert for face in mat_faces for vert in face.verts}
                    split_verts = make_split_verts(
                        final_mesh,
                        bm,
                        flags,
                        verts=nonsplit_verts)

                    strip = get_triangle_strip(final_mesh, bm, mat_faces, split_verts, flags)
                    w("I", len(strip))
                    w(str(len(strip)) + "H", *strip)
                    w("H", len(strip))
                    w(str(len(strip)) + "H", *strip)

                    # padding?
                    w("14x")

                    passes = [tex_slot for tex_slot in the_material.texture_slots
                              if tex_slot and tex_slot.use][:4]

                    vert_normal_offset = 0
                    vert_color_offset = 0
                    vert_texcoord_offset = 0
                    vert_data_stride = 12
                    if flags & SECFLAGS_HAS_VERTEX_WEIGHTS:
                        vert_data_stride += 12
                        if flags & SECFLAGS_HAS_VERTEX_NORMALS:
                            vert_normal_offset = vert_data_stride
                            vert_data_stride += 4 # packed normals
                    elif flags & SECFLAGS_HAS_VERTEX_NORMALS:
                        vert_normal_offset = vert_data_stride
                        vert_data_stride += 12

                    if flags & SECFLAGS_HAS_VERTEX_COLORS:
                        vert_color_offset = vert_data_stride
                        vert_data_stride += 4

                    if flags & SECFLAGS_HAS_TEXCOORDS:
                        # FIXME?
                        vert_texcoord_offset = vert_data_stride
                        vert_data_stride += 8 * (len(passes) or 1)

                    w("B", vert_data_stride) # byte size per vertex
                    w("h", len(split_verts))
                    w("h", 1) # num vert bufs
                    vert_data_size = len(split_verts) * vert_data_stride # total array byte size
                    w("I", vert_data_size)
                    VC_MULT = 128
                    FULL_WHITE = (1.0, 1.0, 1.0, 1.0)
                    for v in split_verts.keys():
                        if is_levelobject:
                            w("3f", *to_thug_coords(lo_matrix * v.co))
                        else:
                            w("3f", *to_thug_coords(ob.matrix_world * v.co))

                        if flags & SECFLAGS_HAS_VERTEX_WEIGHTS:
                            packed_weights = (
                                (int(v.weights[0][1] * 1023.0) & 0x7FF),
                                ((int(v.weights[1][1] * 1023.0) & 0x7FF) << 11),
                                ((int(v.weights[2][1] * 511.0) & 0x3FF) << 22))
                            packed_weights = packed_weights[0] | packed_weights[1] | packed_weights[2]
                            w("I", packed_weights)
                            for group, weight in v.weights:
                                w("H", int(original_object.vertex_groups[group].name) * 3)
                                # w("H", 29 * 3)
                            if flags & SECFLAGS_HAS_VERTEX_NORMALS:
                                packed_normal = (
                                    (int(v.normal[0] * 1023.0) & 0x7FF) |
                                    ((int(v.normal[2] * 1023.0) & 0x7FF) << 11) |
                                    ((int(-v.normal[1] * 511.0) & 0x3FF) << 22))
                                # packed_normal = (1023 & 0x7FF) << 11
                                w("I", packed_normal)
                        elif flags & SECFLAGS_HAS_VERTEX_NORMALS:
                            w("3f", *to_thug_coords_ns(v.normal)) # *to_thug_coords_rot(v.normal))
                            # w("3f", 0, 1, 0)

                        if flags & SECFLAGS_HAS_VERTEX_COLORS:
                            r, g, b, a = v.vc or FULL_WHITE
                            a = (int(a * VC_MULT) & 0xff) << 24
                            r = (int(r * VC_MULT) & 0xff) << 16
                            g = (int(g * VC_MULT) & 0xff) << 8
                            b = (int(b * VC_MULT) & 0xff) << 0
                            w("I", a | r | g | b)

                        # w("i", (len(passes) or 1) if flags & SECFLAGS_HAS_TEXCOORDS else 0)
                        if flags & SECFLAGS_HAS_TEXCOORDS:
                            for tex_slot in passes:
                                uv_index = 0
                                if tex_slot.uv_layer:
                                    uv_index = get_index(
                                        bm.loops.layers.uv.values(),
                                        tex_slot.uv_layer,
                                        lambda layer: layer.name)
                                w("2f", *v.uvs[uv_index])
                            if not passes:
                                w("2f", *v.uvs[0])


                    if flags & SECFLAGS_HAS_VERTEX_WEIGHTS:
                        vertex_shader = 1 # D3DFVF_RESERVED0
                        # vertex_shader |= D3DFVF_XYZ
                    else:
                        vertex_shader = D3DFVF_XYZ
                        if flags & SECFLAGS_HAS_VERTEX_NORMALS:
                            vertex_shader |= D3DFVF_NORMAL
                        if flags & SECFLAGS_HAS_VERTEX_COLORS:
                            vertex_shader |= D3DFVF_DIFFUSE
                        if flags & SECFLAGS_HAS_TEXCOORDS:
                            vertex_shader |= {
                                0: D3DFVF_TEX0,
                                1: D3DFVF_TEX1,
                                2: D3DFVF_TEX2,
                                3: D3DFVF_TEX3,
                                4: D3DFVF_TEX4,
                            }.get(len(passes), 0)

                    w("i", vertex_shader)
                    w("i", 0) # vertex shader 2?
                    # w("B", 12) # vert normal offset
                    # w("B", 24) # vert color offset
                    # w("B", 28) # vert texcoord offset
                    w("B", vert_normal_offset) # vert normal offset
                    w("B", vert_color_offset) # vert color offset
                    w("B", vert_texcoord_offset) # vert texcoord offset
                    w("B", 0) # has color wibble data

                    w("I", 1) # num index sets

                    pixel_shader = 1
                    w("I", pixel_shader)
                    if pixel_shader == 1:
                        w("I", 0)
                        w("I", 0)
            finally:
                safe_mode_set("OBJECT")
                ob = temporary_object
                ob_data = ob.data
                bpy.context.scene.objects.unlink(ob)
                bpy.data.objects.remove(ob)
                bpy.data.meshes.remove(ob_data)
        finally:
            original_object.name = original_object_name
    _saved_offset = output_file.tell()
    output_file.seek(object_amount_offset)
    w("i", object_counter)
    output_file.seek(_saved_offset)
    bm.free()



# OPERATORS
#############################################
class SceneToTHUG2Files(bpy.types.Operator): #, ExportHelper):
    bl_idname = "export.scene_to_thug2_xbx"
    bl_label = "Scene to THUG2 level files"
    # bl_options = {'REGISTER', 'UNDO'}

    def report(self, category, message):
        LOG.debug("OP: {}: {}".format(category, message))
        super().report(category, message)


    filename = StringProperty(name="File Name")
    directory = StringProperty(name="Directory")

    generate_vertex_color_shading = BoolProperty(name="Generate vertex color shading", default=False)
    use_vc_hack = BoolProperty(name="Vertex color hack",default=False, options={'HIDDEN'})
    autosplit_everything = BoolProperty(name="Autosplit All"
        , description = "Applies the autosplit setting to all objects in the scene, with default settings."
        , default=False)
    is_park_editor = BoolProperty(
        name="Is Park Editor",
        description="Use this option when exporting a park editor dictionary.",
        default=False)
    generate_tex_file = BoolProperty(
        name="Generate a .tex file",
        description="If you have already generated a .tex file, and didn't change/add any new images in meantime, you can uncheck this.",
        default=True)
    generate_scn_file = BoolProperty(
        name="Generate a .scn file",
        default=True)
    pack_scn = BoolProperty(
        name="Pack the scene .prx",
        default=True)
    generate_col_file = BoolProperty(
        name="Generate a .col file",
        default=True)
    pack_col = BoolProperty(
        name="Pack the col .prx",
        default=True)
    generate_scripts_files = BoolProperty(
        name="Generate scripts",
        default=True)
    pack_scripts = BoolProperty(
        name="Pack the scripts .prx",
        default=True)
#    filepath = StringProperty()

    skybox_name = StringProperty(name="Skybox name", default="THUG2_Sky")
    export_scale = FloatProperty(name="Export scale", default=1)
    mipmap_offset = IntProperty(
        name="Mipmap offset",
        description="Offsets generation of mipmaps (default is 0). For example, setting this to 1 will make the base texture 1/4 the size. Use when working with very large textures.",
        min=0, max=4, default=0)

    def execute(self, context):
        return do_export(self, context, "THUG2")

    def invoke(self, context, event):
        wm = bpy.context.window_manager
        wm.fileselect_add(self)

        return {'RUNNING_MODAL'}


#----------------------------------------------------------------------------------
class SceneToTHUG2Model(bpy.types.Operator): #, ExportHelper):
    bl_idname = "export.scene_to_thug2_model"
    bl_label = "Scene to THUG2 model"
    # bl_options = {'REGISTER', 'UNDO'}

    def report(self, category, message):
        LOG.debug("OP: {}: {}".format(category, message))
        super().report(category, message)

    filename = StringProperty(name="File Name")
    directory = StringProperty(name="Directory")

    generate_vertex_color_shading = BoolProperty(name="Generate vertex color shading", default=True)
    use_vc_hack = BoolProperty(name="Vertex color hack",default=False, options={'HIDDEN'})
    autosplit_everything = BoolProperty(name="Autosplit All"
        , description = "Applies the autosplit setting to all objects in the scene, with default settings."
        , default=False)
    is_park_editor = BoolProperty(name="Is Park Editor", default=False, options={'HIDDEN'})
    generate_scripts_files = BoolProperty(
        name="Generate scripts",
        default=True)
    export_scale = FloatProperty(name="Export scale", default=1)
    mipmap_offset = IntProperty(
        name="Mipmap offset",
        description="Offsets generation of mipmaps (default is 0). For example, setting this to 1 will make the base texture 1/4 the size. Use when working with very large textures.",
        min=0, max=4, default=0)
        
    def execute(self, context):
        return do_export_model(self, context, "THUG2")

    def invoke(self, context, event):
        wm = bpy.context.window_manager
        wm.fileselect_add(self)

        return {'RUNNING_MODAL'}
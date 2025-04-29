import bpy
import math
import bmesh

blender_ver = bpy.app.version

# auto normals were replaced with a modifier / geometry node in blender 4.1
auto_normals_supported = blender_ver[0] < 4 or (blender_ver[0] == 4 and blender_ver[1] <= 1)

# api for mesh data changed in blender 4
vertex_mask_uses_attributes = blender_ver[0] > 3


class UtilOpMeshOperator(bpy.types.Operator):
    def invoke(self, context, event):

        # make sure the current contet is a mesh
        t = bpy.context.object.type
        if t != 'MESH': return {'FINISHED'}

        # run pre edit
        self.pre_edit(context, event)

        # prepare bmesh
        in_edit_mode = bpy.context.object.mode == 'EDIT'

        mesh = bpy.context.object.data
        bm = bmesh.new() if not in_edit_mode else bmesh.from_edit_mesh(mesh)
        if not in_edit_mode: bm.from_mesh(mesh)

        # run mesh edit
        self.do_mesh_edit(context, event, bm, in_edit_mode)

        # free bmesh
        if not in_edit_mode:
            bm.to_mesh(mesh)
            bm.free()
        else:
            bmesh.update_edit_mesh(mesh)

        # run post edit
        self.post_edit(context, event)

        return {'FINISHED'}

    def pre_edit(self, context, event):
        pass

    def do_mesh_edit(self, context, event, bm, in_edit_mode):
        pass

    def post_edit(self, context, event):
        pass

class RipEdgesToCurve(bpy.types.Operator):
    bl_idname = "neo.model_ripedgestocurve"
    bl_label = "Rip Edges To NURBS Curve"
    bl_description = "Rips the selected edges to a new curve with the provided settings and then switches out to Object mode."
    bl_options = {"REGISTER", "UNDO"}

    tilt: bpy.props.FloatProperty(name="Tilt", default=90)
    use_z_up: bpy.props.BoolProperty(name="Use Z Up", default=True)
    close_spline: bpy.props.BoolProperty(name="Close", default=False)

    def execute(self, context):

        # perform the separation
        before_sel = bpy.context.selected_objects
        bpy.ops.mesh.select_mode(type="EDGE")
        bpy.ops.mesh.separate(type="SELECTED")
        after_sel = bpy.context.selected_objects

        # select the new object
        for sel in before_sel:
            sel.select_set(False)
            after_sel.remove(sel)

        curve = after_sel[0]
        bpy.context.view_layer.objects.active = curve

        # update the curve data
        bpy.ops.object.convert(target="CURVE")
        curve_data: bpy.types.Curve = curve.data

        if self.use_z_up:
            curve_data.twist_mode = "Z_UP"

        # update tilt
        tilt_as_rad = self.tilt * math.pi / 180.0
        for s in curve_data.splines:
            s.type = "NURBS"
            if self.close_spline:
                s.use_cyclic_u = True

            for p in s.points:
                p.tilt = tilt_as_rad

        bpy.ops.object.mode_set(mode="OBJECT")

        return {"FINISHED"}


if auto_normals_supported:
    class SetupAutoNormals(bpy.types.Operator):
        bl_idname = "neo.model_setupautonormals"
        bl_label = "Setup Normal Auto Smooth"
        bl_description = "Marks the selected objects as smooth shaded and then configures auto normals with a 180 degree angle"
        bl_options = {"REGISTER", "UNDO"}

        def execute(self, context):
            bpy.ops.object.shade_smooth()

            sel = bpy.context.selected_objects

            for s in sel:
                if s.type != "MESH":
                    continue

            mesh: bpy.types.Mesh = s.data
            mesh.auto_smooth_angle = 180
            mesh.use_auto_smooth = True

            return {"FINISHED"}


class SetupCurveArray(bpy.types.Operator):
    bl_idname = "neo.model_setupcurvearray"
    bl_label = "Setup Curve Array"
    bl_description = "Quickly configures an object with an array and curve modifier.\n" \
                     "* The object will be setup so the Y axis points along the curves direction.\n" \
                     "* The curves pivot will be moved to the object."
    bl_options = {"REGISTER", "UNDO"}

    def show_error_message(self):
        self.report({"ERROR"}, "A mesh and curve must be selected to use this operator.")

    @staticmethod
    def get_type(sel, t):
        for s in sel:
            if s.type == t:
                return s

        return None

    def execute(self, context):
        sel = bpy.context.selected_objects

        # sanity check / get objects
        if len(sel) != 2:
            self.show_error_message()
            return {"CANCELLED"}

        mesh = self.get_type(sel, "MESH")
        curve = self.get_type(sel, "CURVE")

        if mesh is None or curve is None:
            self.show_error_message()
            return {"CANCELLED"}

        # setup modifiers
        array_mod: bpy.types.ArrayModifier = mesh.modifiers.new("Curve Array", "ARRAY")
        array_mod.fit_type = "FIT_CURVE"
        array_mod.use_merge_vertices = True
        array_mod.relative_offset_displace[0] = 0
        array_mod.relative_offset_displace[1] = 1
        array_mod.relative_offset_displace[2] = 0
        array_mod.curve = curve

        curve_mod: bpy.types.CurveModifier = mesh.modifiers.new("Curve Deform", "CURVE")
        curve_mod.deform_axis = "POS_Y"
        curve_mod.object = curve

        # move curve pivot
        target_loc = mesh.location
        old_cursor_loc = bpy.context.scene.cursor.location
        bpy.context.scene.cursor.location = target_loc

        bpy.ops.object.origin_set(type="ORIGIN_CURSOR")

        bpy.context.scene.cursor.location = old_cursor_loc

        return {"FINISHED"}

class NeoVertSelectionToSculptMask(UtilOpMeshOperator):
    bl_idname = "neo.model_vertextomask"
    bl_label = "Vertex To Mask"
    bl_description = "Converts the vertex selection into a sculpting mask."

    def do_mesh_edit(self, context, event, bm, in_edit_mode):
        if not in_edit_mode:
            return

        vert_mask = [v.select for v in bm.verts]

        if vertex_mask_uses_attributes:
            sculpt_mask = ".sculpt_mask"
            if bm.verts.layers.float.get(sculpt_mask) is None:
                layer_mask = bm.verts.layers.float.new(sculpt_mask)
        else:
            layer_mask = bm.verts.layers.paint_mask.new() if not bm.verts.layers.paint_mask else bm.verts.layers.paint_mask[0]

        for vert, mask in zip(bm.verts, vert_mask):
            vert[layer_mask] = mask


class NEO_MT_setup_menu(bpy.types.Menu):
    bl_idname = "NEO_MT_setup_menu"
    bl_label = "Helpers"

    def draw(self, context):
        self.layout.operator(
            SetupCurveArray.bl_idname,
            text=SetupCurveArray.bl_label,
        )

        if auto_normals_supported:
            self.layout.operator(
                SetupAutoNormals.bl_idname,
                text=SetupAutoNormals.bl_label,
            )

class NEO_MT_edge_menu(bpy.types.Menu):
    bl_idname = "NEO_MT_edge_menu"
    bl_label = "Helpers"

    def draw(self, context):
        self.layout.operator(
            RipEdgesToCurve.bl_idname,
            text=RipEdgesToCurve.bl_label,
        )

class NEO_MT_vertex_menu(bpy.types.Menu):
    bl_idname = "NEO_MT_vertex_menu"
    bl_label = "Helpers"

    def draw(self, context):
        self.layout.operator(
            NeoVertSelectionToSculptMask.bl_idname,
            text=NeoVertSelectionToSculptMask.bl_label,
        )


classes = [
    NEO_MT_setup_menu,
    SetupCurveArray,

    NEO_MT_edge_menu,
    RipEdgesToCurve,

    NEO_MT_vertex_menu,
    NeoVertSelectionToSculptMask,
]

if auto_normals_supported:
    classes.append(SetupAutoNormals)


def setup_menu(self, context):
    self.layout.separator()

    self.layout.label(
        text="Neognosis"
    )

    self.layout.menu(NEO_MT_setup_menu.bl_idname)


def edge_menu(self, context):
    self.layout.separator()

    self.layout.label(
        text="Neognosis"
    )

    self.layout.menu(NEO_MT_edge_menu.bl_idname)


def vertex_menu(self, context):
    self.layout.separator()

    self.layout.label(
        text="Neognosis"
    )

    self.layout.menu(NEO_MT_vertex_menu.bl_idname)


def register():
    for c in classes:
        bpy.utils.register_class(c)

    bpy.types.VIEW3D_MT_object.append(setup_menu)
    bpy.types.VIEW3D_MT_edit_mesh_edges.append(edge_menu)
    bpy.types.VIEW3D_MT_edit_mesh_vertices.append(vertex_menu)


def unregister():
    for c in classes:
        bpy.utils.unregister_class(c)

    bpy.types.VIEW3D_MT_object.remove(setup_menu)
    bpy.types.VIEW3D_MT_edit_mesh_edges.remove(edge_menu)
    bpy.types.VIEW3D_MT_edit_mesh_vertices.remove(vertex_menu)
from PyQt6.QtWidgets import QMenu

from gui.components.workspace_items import Node, Edge


class WorkspaceContextMenu:
    """Context menu logic extracted from WorkspaceView."""

    def __init__(self, view):
        self.view = view

    def _get_base_item(self, item):
        # Walk up until we hit our graph item types.
        while item and not isinstance(item, (Node, Edge)):
            item = item.parentItem()
        return item

    def context_menu_event(self, event):
        v = self.view
        item = self._get_base_item(v.itemAt(event.pos()))
        selected_nodes = [n for n in v.scene_obj.selectedItems() if isinstance(n, Node)]

        if (
            len(selected_nodes) > 1
            and isinstance(item, Node)
            and item in selected_nodes
        ):
            menu = QMenu(v)
            del_action = menu.addAction("🗑️ Delete Selected Nodes")
            declutter_action = menu.addAction("🧹 Declutter Selected Nodes")

            menu.addSeparator()
            ai_menu = v.create_ai_menu(menu)
            menu.addMenu(ai_menu)

            action = menu.exec(event.globalPos())
            if action == del_action:
                v.save_state_for_undo()
                for n in selected_nodes:
                    v.delete_node(n)
            elif action == declutter_action:
                v.trigger_declutter()
            return

        if isinstance(item, Node):
            menu = QMenu(v)
            edit_action = menu.addAction("✏️ Edit Note Text")
            color_action = menu.addAction("🎨 Change Color")

            connect_action = None
            if len(selected_nodes) == 1 and item != selected_nodes[0]:
                connect_action = menu.addAction("🔗 Connect Selected Node to This")

            del_action = menu.addAction("🗑️ Delete Note")
            declutter_action = menu.addAction("🧹 Declutter Selected Node")

            menu.addSeparator()
            ai_menu = v.create_ai_menu(menu)
            menu.addMenu(ai_menu)

            action = menu.exec(event.globalPos())
            if action == edit_action:
                item.trigger_edit()
            elif action == color_action:
                item.trigger_color_change()
            elif connect_action and action == connect_action:
                v.save_state_for_undo()
                v.connecting_node = selected_nodes[0]
                v.finish_connection(item)
            elif action == del_action:
                v.save_state_for_undo()
                v.delete_node(item)
            elif action == declutter_action:
                v.trigger_declutter()
            return

        if isinstance(item, Edge):
            menu = QMenu(v)
            edit_action = menu.addAction("✏️ Edit Connection Text")
            color_action = menu.addAction("🎨 Change Line Color")
            weight_action = menu.addAction("📏 Change Line Weight")
            del_action = menu.addAction("🗑️ Delete Connection")

            menu.addSeparator()
            ai_menu = v.create_ai_menu(menu)
            menu.addMenu(ai_menu)

            action = menu.exec(event.globalPos())
            if action == edit_action:
                item.trigger_edit()
            elif action == color_action:
                item.trigger_color_change()
            elif action == weight_action:
                item.trigger_weight_change()
            elif action == del_action:
                v.save_state_for_undo()
                v.delete_edge(item)
            return

        if item is None:
            # Empty canvas: allow full tools menu.
            menu = QMenu(v)
            declutter_action = menu.addAction("🧹 Declutter All Notes")

            menu.addSeparator()
            ai_menu = v.create_ai_menu(menu)
            menu.addMenu(ai_menu)

            action = menu.exec(event.globalPos())
            if action == declutter_action:
                v.trigger_declutter()
            return

        # Fallback to default Qt behavior for anything unexpected.
        v._fallback_context_menu_event(event)


# gui/components/workspace_view_helpers.py
from PySide6.QtWidgets import QMenu
from core.utils.workspace_utils import build_pdf_display_name
from core.events.event_bus import EventBus
from core.events.domains.workspace_events import WorkspaceEvent, WorkspaceEventPayload

def build_ai_menu(view, parent_widget):
    menu = QMenu("🤖 AI Tools", parent_widget)
    menu.setTitle("🤖 AI Tools")

    # 1. Reliably check AI status using the view's explicitly passed main_window reference
    ai_enabled = False
    try:
        llm = view._llm()
        ai_enabled = llm.ai_enabled if llm else False
    except Exception:
        pass

    if not ai_enabled:
        disabled_action = menu.addAction("⚠️ AI Not Installed")
        disabled_action.setEnabled(False)
        menu.setEnabled(False)
        menu.setToolTip("Run the installer to download local AI models.")
        return menu

    # 2. Fetch the newly centralized registry from MainWindow, not the View!
    registry = getattr(view.main_window, "workspace_ai_tools_registry", None)

    if registry:
        bus = EventBus.get_instance()
        tools = list(registry.iter_mount("workspace_context_menu"))

        for tool in tools:
            action = menu.addAction(tool.label)
            action.setToolTip(tool.description)

            # 3. Fire the true Phase 4 intent, capturing the current selection!
            action.triggered.connect(
                lambda checked=False, t_id=tool.id: bus.run_ai_tool.emit(
                    WorkspaceEvent.RUN_AI_TOOL,
                    WorkspaceEventPayload(
                        tool_id=t_id,
                        workspace_id=view.current_workspace_id,
                        selected_ids=[n.node_id for n in view._selected_nodes()],
                    ),
                )
            )
    else:
        # Failsafe if the registry hasn't mounted yet
        disabled_action = menu.addAction("⚠️ Tool Registry Missing")
        disabled_action.setEnabled(False)

    return menu

def workspace_toolbar_stylesheet(theme):
    return f"""
        QFrame#WorkspaceToolbar {{ background-color: {theme['bg_panel']}; border: 1px solid {theme['border']}; border-radius: 8px; }}
        QLabel {{ color: {theme['text_main']}; font-weight: bold; }}
        QComboBox {{ background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 4px; border-radius: 4px; font-weight: bold; min-width: 150px; }}
        QComboBox QAbstractItemView {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; }}
        QComboBox QAbstractItemView::item {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; padding: 4px; }}
        QComboBox QAbstractItemView::item:selected {{ background-color: {theme['accent']}; color: #ffffff; }}
        QPushButton {{ background-color: {theme['accent']}; color: #ffffff; border: none; padding: 6px 12px; border-radius: 4px; font-weight: bold; }}
        QPushButton:hover {{ background-color: {theme['accent_hover']}; }}
        QPushButton::menu-indicator {{ image: none; }}
        QLabel#CollapsingIcon {{ background-color: {theme['accent']}; color: #ffffff; padding: 6px 12px; border-radius: 4px; font-weight: bold; }}
        QCheckBox {{ color: {theme['text_main']}; font-weight: bold; background: transparent; }}
        QSlider::groove:horizontal {{ height: 4px; background: {theme['border']}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ background: {theme['accent']}; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}
        QMenu {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; font-weight: bold; padding: 5px; }}
        QMenu::item {{ padding: 6px 20px 6px 20px; border-radius: 4px; }}
        QMenu::item:selected {{ background-color: {theme['accent']}; color: #ffffff; }}
    """


def populate_pdf_filter_combo(filter_combo, pdfs, checked_data):
    filter_combo.clear()
    filter_combo.addItem("All PDFs", "ALL", checked=("ALL" in checked_data))
    for pdf in pdfs:
        filter_combo.addItem(build_pdf_display_name(pdf), pdf, checked=(pdf in checked_data))


def populate_tag_filter_combo(tag_filter_combo, tags, checked_data):
    tag_filter_combo.clear()
    tag_filter_combo.addItem("All Tags", "ALL_TAGS", checked=("ALL_TAGS" in checked_data))
    for tag in tags:
        tag_name = tag.get("name")
        if tag_name:
            tag_filter_combo.addItem(tag_name, tag_name, checked=(tag_name in checked_data))


def build_selected_nodes_context_menu(view, parent_widget, selected_nodes):
    menu = QMenu(parent_widget)
    remove_action = menu.addAction("🗑️ Remove Selected from Workspace")
    delete_action = menu.addAction("🔥 Delete Selected Highlights Permanently")
    color_action = menu.addAction("🎨 Change Color for Selected Nodes")
    manage_tags_action = menu.addAction("🏷️ Manage Tags for Selected Nodes")
    declutter_action = menu.addAction("🧹 Declutter Selected Nodes")

    remove_action.triggered.connect(view.delete_selected_nodes)
    menu.addSeparator()
    menu.addMenu(build_ai_menu(view, menu))

    return menu, delete_action, color_action, manage_tags_action, declutter_action


def build_node_context_menu(view, parent_widget, node, selected_nodes, connect_source):
    menu = QMenu(parent_widget)
    edit_action = menu.addAction("✏️ Edit Note Text")
    color_action = menu.addAction("🎨 Change Color")
    manage_tags_action = menu.addAction("🏷️ Manage Tags")
    cite_action = menu.addAction("📋 Copy In-Text Citation")
    connect_action = menu.addAction("🔗 Connect to Selected Node") if connect_source else None
    remove_action = menu.addAction("🗑️ Remove Selected from Workspace")
    delete_highlight_action = menu.addAction("🔥 Delete Highlight Permanently") if node.highlight_id else None
    declutter_action = menu.addAction("🧹 Declutter Selected Node")

    remove_action.triggered.connect(view.delete_selected_nodes)
    menu.addSeparator()
    menu.addMenu(build_ai_menu(view, menu))

    return menu, edit_action, color_action, manage_tags_action, cite_action, connect_action, delete_highlight_action, declutter_action


def build_edge_context_menu(view, parent_widget, edge):
    menu = QMenu(parent_widget)
    edit_action = menu.addAction("✏️ Edit Connection Text")
    color_action = menu.addAction("🎨 Change Line Color")
    weight_action = menu.addAction("📏 Change Line Weight")
    del_action = menu.addAction("🗑️ Delete Connection")

    menu.addSeparator()
    menu.addMenu(build_ai_menu(view, menu))

    return menu, edit_action, color_action, weight_action, del_action


def build_canvas_context_menu(view, parent_widget):
    menu = QMenu(parent_widget)
    declutter_action = menu.addAction("🧹 Declutter All Notes")
    analysis_menu = menu.addMenu("Related to Tag")

    pm = view._pm()
    current_tags = pm.get_all_tags() if pm else []
    if current_tags:
        for tag in current_tags:
            tag_name = tag.get("name")
            if tag_name:
                tag_sub = analysis_menu.addMenu(f"'{tag_name}'")
                tag_sub.addAction("🔍 Find Relatives").triggered.connect(lambda checked, t=tag_name: view.trigger_find_tag_relatives(t))
                tag_sub.addAction("⚖️ Find Opposing Views").triggered.connect(lambda checked, t=tag_name: view.trigger_tag_opposing_views(t))
    else:
        analysis_menu.addAction("No tags created yet").setEnabled(False)

    menu.addSeparator()
    menu.addMenu(build_ai_menu(view, menu))
    return menu, declutter_action

"""Main window: resizable master-detail (tree sidebar + wrapping thumbnail
grid), a status bar with live background-scan progress, and a full-image view
with a filmstrip, prev/next arrows, and left/right keyboard navigation.

The sidebar is a real expand/collapse tree (Gtk.TreeListModel + Gtk.ListView).
All access to user folders is read-only; derivatives go only to the cache.
"""

from __future__ import annotations

import os
import threading
import time

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from .. import APP_NAME, ICON_NAME, __version__
from ..cache import (
    Index,
    cache_status,
    clear_cache,
    generate_derivatives,
    human_bytes,
    sync_index,
)
from ..models import FolderNode
from ..platform_utils import reveal_in_file_manager
from ..tree import ancestor_chain, build_tree, find_node
from ..ui_prefs import density_spec, format_count
from .gtk4_actions import install_actions, set_enabled
from .gtk4_items import FolderItem

# Thumbnail grid cell (image scaled to fit inside, preserving aspect ratio).
THUMB_CELL = 190
# Filmstrip thumbnail height (width follows aspect ratio).
FILMSTRIP_H = 72
# How often the scan worker refreshes the visible tree/grid while running.
LIVE_REFRESH_SECONDS = 0.6


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application, config):
        super().__init__(application=application)
        self.app = application
        self.config = config
        self._actions: dict[str, Gio.SimpleAction] = {}

        self.index = Index()
        self.index.load()

        self.tree_root: FolderNode | None = None
        self.focus_path: str = os.path.sep      # "/" == no focus (default)
        self.selected_folder: FolderNode | None = None
        self.selected_image_path: str | None = None

        # Expansion memory: folder paths the user has expanded.
        self._expanded: set[str] = set()

        # Full-view / filmstrip state.
        self._full_images: list = []
        self._full_index: int = -1

        self._scanning = False
        self._status_default = "Ready"
        self._scanned_folders_win: Adw.Window | None = None

        win = self.config.get("window", {"width": 1100, "height": 720})
        self.set_default_size(int(win.get("width", 1100)), int(win.get("height", 720)))
        self.set_title(APP_NAME)
        self.set_icon_name(ICON_NAME)

        install_actions(self)
        self._build_ui()
        self._apply_density()
        self._rebuild_tree()

        GLib.idle_add(self._start_background_scan)

    # ================================================================= UI
    def _build_ui(self) -> None:
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._build_gallery_page(), "gallery")
        self.stack.add_named(self._build_full_page(), "full")
        self.stack.set_visible_child_name("gallery")
        self.set_content(self.stack)

    def _primary_menu(self) -> Gio.Menu:
        menu = Gio.Menu()

        folders = Gio.Menu()
        # "Add scanned folder" intentionally removed; use the Scanned folders
        # window (also bound to Ctrl+O).
        folders.append("Scanned folders…", "win.manage-folders")
        menu.append_section(None, folders)

        cache = Gio.Menu()
        cache.append("Cache status…", "win.cache-status")
        cache.append("Regenerate all caches", "win.regenerate-cache")
        menu.append_section(None, cache)

        end = Gio.Menu()
        end.append("Preferences", "win.preferences")
        end.append("Keyboard Shortcuts", "win.shortcuts")
        end.append(f"About {APP_NAME}", "win.about")
        menu.append_section(None, end)
        return menu

    def _build_gallery_page(self) -> Gtk.Widget:
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_primary(True)
        menu_btn.set_menu_model(self._primary_menu())
        header.pack_end(menu_btn)
        toolbar_view.add_top_bar(header)

        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.set_wide_handle(True)
        self.paned.set_position(self.config.sidebar_width)
        self.paned.connect("notify::position", self._on_paned_moved)
        self.paned.set_start_child(self._build_sidebar())
        self.paned.set_resize_start_child(False)
        self.paned.set_shrink_start_child(False)
        self.paned.set_end_child(self._build_detail())
        self.paned.set_resize_end_child(True)
        toolbar_view.set_content(self.paned)

        self.status_label = Gtk.Label(label=self._status_default)
        self.status_label.set_xalign(0.0)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.status_label.add_css_class("tadaima-status")
        status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        status_bar.add_css_class("tadaima-statusbar")
        status_bar.append(self.status_label)
        toolbar_view.add_bottom_bar(status_bar)
        return toolbar_view

    # ------------------------------------------------------ sidebar (tree)
    def _build_sidebar(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_size_request(180, -1)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        # Root store holds the top-level rows (ancestors + focused node).
        self._root_store = Gio.ListStore(item_type=FolderItem)
        self._tree_model = Gtk.TreeListModel.new(
            self._root_store,
            False,          # passthrough
            False,          # autoexpand
            self._create_child_model,
        )
        self._selection = Gtk.SingleSelection(model=self._tree_model)
        self._selection.set_autoselect(False)
        self._selection.set_can_unselect(True)
        self._selection.connect("notify::selected", self._on_tree_selected)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._tree_setup)
        factory.connect("bind", self._tree_bind)

        self.tree_view = Gtk.ListView(model=self._selection, factory=factory)
        self.tree_view.add_css_class("navigation-sidebar")
        self.tree_view.add_css_class("tadaima-sidebar")
        scroller.set_child(self.tree_view)

        self.sidebar_placeholder = Adw.StatusPage()
        self.sidebar_placeholder.set_icon_name("folder-symbolic")
        self.sidebar_placeholder.set_title("No folders yet")
        self.sidebar_placeholder.set_description(
            "Add a folder from Scanned folders to browse its photos. "
            "Folders are only ever read."
        )
        ph_btn = Gtk.Button(label="Scanned folders…")
        ph_btn.add_css_class("suggested-action")
        ph_btn.add_css_class("pill")
        ph_btn.set_halign(Gtk.Align.CENTER)
        ph_btn.set_action_name("win.manage-folders")
        self.sidebar_placeholder.set_child(ph_btn)

        self.sidebar_stack = Gtk.Stack()
        self.sidebar_stack.add_named(scroller, "list")
        self.sidebar_stack.add_named(self.sidebar_placeholder, "empty")
        outer.append(self.sidebar_stack)
        return outer

    def _create_child_model(self, item: FolderItem):
        """TreeListModel child-model callback: children of *item*, or None if a
        leaf. Ancestors (flattened header) are leaves — not expandable."""
        if item.style == "ancestor":
            return None
        node = item.node
        if not node.children:
            return None
        store = Gio.ListStore(item_type=FolderItem)
        for child in node.sorted_children():
            store.append(FolderItem(child, style="normal"))
        return store

    def _tree_setup(self, factory, list_item) -> None:
        expander = Gtk.TreeExpander()
        expander.set_indent_for_icon(True)
        expander.set_indent_for_depth(True)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        icon = Gtk.Image.new_from_icon_name("folder-symbolic")
        label = Gtk.Label(xalign=0.0)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        label.add_css_class("tadaima-row-label")
        count = Gtk.Label(xalign=1.0)
        count.add_css_class("tadaima-count")
        count.add_css_class("tadaima-row-label")
        count.add_css_class("numeric")
        box.append(icon)
        box.append(label)
        box.append(count)
        expander.set_child(box)

        list_item.set_child(expander)
        list_item._icon = icon
        list_item._label = label
        list_item._count = count
        list_item._expander = expander

        # Right-click context menu on the whole row.
        gesture = Gtk.GestureClick()
        gesture.set_button(3)
        gesture.connect("pressed", self._on_row_right_click, list_item)
        expander.add_controller(gesture)

    def _tree_bind(self, factory, list_item) -> None:
        row = list_item.get_item()              # Gtk.TreeListRow
        item: FolderItem = row.get_item()        # FolderItem
        node = item.node

        list_item._expander.set_list_row(row)

        px = getattr(self, "_sidebar_icon_px", 16)
        square = None
        for rec in node.own_images:
            if rec.square_path and os.path.exists(rec.square_path):
                square = rec.square_path
                break
        if square:
            list_item._icon.set_from_file(square)
        else:
            list_item._icon.set_from_icon_name("folder-symbolic")
        list_item._icon.set_pixel_size(px)

        name = node.name if node.name != os.path.sep else "/"
        list_item._label.set_text(name)
        list_item._label.remove_css_class("tadaima-ancestor")
        list_item._label.remove_css_class("tadaima-focused")
        if item.style == "ancestor":
            list_item._label.add_css_class("tadaima-ancestor")
        elif item.style == "focused":
            list_item._label.add_css_class("tadaima-focused")

        list_item._count.set_text(format_count(node.image_count))

    # ------------------------------------------------------------- detail
    def _build_detail(self) -> Gtk.Widget:
        self.detail_scroller = Gtk.ScrolledWindow()
        self.detail_scroller.set_hexpand(True)
        self.detail_scroller.set_vexpand(True)
        self.detail_scroller.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )

        self.flow = Gtk.FlowBox()
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_max_children_per_line(100)
        self.flow.set_min_children_per_line(1)
        self.flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.flow.set_homogeneous(True)
        self.flow.set_row_spacing(14)
        self.flow.set_column_spacing(14)
        self.flow.set_margin_top(14)
        self.flow.set_margin_bottom(14)
        self.flow.set_margin_start(14)
        self.flow.set_margin_end(14)
        self.flow.connect("child-activated", self._on_thumb_activated)
        self.flow.connect("selected-children-changed", self._on_thumb_selected)

        self.detail_placeholder = Adw.StatusPage()
        self.detail_placeholder.set_icon_name("emblem-photos-symbolic")
        self.detail_placeholder.set_title("No photos to show")
        self.detail_placeholder.set_description(
            "Select a folder on the left to see the photos inside it."
        )

        self.detail_stack = Gtk.Stack()
        self.detail_scroller.set_child(self.flow)
        self.detail_stack.add_named(self.detail_scroller, "grid")
        self.detail_stack.add_named(self.detail_placeholder, "empty")
        self.detail_stack.set_visible_child_name("empty")
        return self.detail_stack

    # ---------------------------------------------------------- full view
    def _build_full_page(self) -> Gtk.Widget:
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        back_btn = Gtk.Button(label="Back to Library")
        back_btn.set_tooltip_text("Back to Library")
        back_btn.set_action_name("win.back")
        header.pack_start(back_btn)
        toolbar_view.add_top_bar(header)

        strip_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        strip_row.add_css_class("tadaima-filmstrip-bar")

        self.prev_btn = Gtk.Button()
        self.prev_btn.set_icon_name("go-previous-symbolic")
        self.prev_btn.set_tooltip_text("Previous photo")
        self.prev_btn.add_css_class("flat")
        self.prev_btn.set_valign(Gtk.Align.CENTER)
        self.prev_btn.set_action_name("win.prev-photo")
        strip_row.append(self.prev_btn)

        strip_scroller = Gtk.ScrolledWindow()
        strip_scroller.set_hexpand(True)
        strip_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        strip_scroller.set_min_content_height(FILMSTRIP_H + 12)
        self.filmstrip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.filmstrip.set_margin_top(6)
        self.filmstrip.set_margin_bottom(6)
        self.filmstrip.set_margin_start(6)
        self.filmstrip.set_margin_end(6)
        strip_scroller.set_child(self.filmstrip)
        self._strip_scroller = strip_scroller
        strip_row.append(strip_scroller)

        self.next_btn = Gtk.Button()
        self.next_btn.set_icon_name("go-next-symbolic")
        self.next_btn.set_tooltip_text("Next photo")
        self.next_btn.add_css_class("flat")
        self.next_btn.set_valign(Gtk.Align.CENTER)
        self.next_btn.set_action_name("win.next-photo")
        strip_row.append(self.next_btn)

        toolbar_view.add_top_bar(strip_row)

        self.full_picture = Gtk.Picture()
        self.full_picture.set_can_shrink(True)
        self.full_picture.set_hexpand(True)
        self.full_picture.set_vexpand(True)
        self.full_picture.set_content_fit(Gtk.ContentFit.CONTAIN)

        self.full_caption = Gtk.Label()
        self.full_caption.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.full_caption.set_justify(Gtk.Justification.CENTER)
        self.full_caption.set_halign(Gtk.Align.CENTER)
        self.full_caption.add_css_class("tadaima-caption")
        caption_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        caption_bar.add_css_class("tadaima-caption-bar")
        self.full_caption.set_hexpand(True)
        caption_bar.append(self.full_caption)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.full_picture)
        content.append(caption_bar)
        toolbar_view.set_content(content)

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_full_key)
        toolbar_view.add_controller(key)
        return toolbar_view

    # ============================================================ density
    def _apply_density(self) -> None:
        spec = density_spec(self.config.density)
        self._sidebar_icon_px = int(spec["icon_px"])
        css = f"""
        .tadaima-sidebar row {{
            padding-top: {int(spec['row_pad_v'])}px;
            padding-bottom: {int(spec['row_pad_v'])}px;
            padding-left: {int(spec['row_pad_h'])}px;
            padding-right: {int(spec['row_pad_h'])}px;
            min-height: 0;
        }}
        .tadaima-sidebar .tadaima-row-label {{
            font-size: {spec['font_scale']:.2f}em;
        }}
        /* Shadow + selection border live on the picture itself, not the cell. */
        picture.tadaima-thumb {{
            box-shadow: 0 2px 6px rgba(0,0,0,0.45);
            border-radius: 3px;
            background: transparent;
        }}
        flowboxchild.tadaima-cell {{
            padding: 4px;
            background: transparent;
            box-shadow: none;
        }}
        flowboxchild.tadaima-cell:selected {{
            background: transparent;
            box-shadow: none;
            outline: none;
        }}
        flowboxchild.tadaima-cell:selected picture.tadaima-thumb {{
            outline: 3px solid @accent_color;
            outline-offset: 2px;
        }}
        .tadaima-count {{ opacity: 0.55; }}
        .tadaima-ancestor {{ font-style: italic; opacity: 0.7; }}
        .tadaima-focused {{ font-weight: bold; }}
        .tadaima-statusbar {{
            padding: 2px 10px;
            border-top: 1px solid alpha(currentColor, 0.12);
            min-height: 22px;
        }}
        .tadaima-status {{ font-size: 0.85em; opacity: 0.8; }}
        .tadaima-caption-bar {{
            padding: 6px 10px;
            border-top: 1px solid alpha(currentColor, 0.12);
        }}
        .tadaima-caption {{ opacity: 0.85; }}
        .tadaima-filmstrip-bar {{
            padding: 2px 4px;
            border-bottom: 1px solid alpha(currentColor, 0.12);
        }}
        .tadaima-film-thumb {{
            border-radius: 2px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.4);
        }}
        .tadaima-film-thumb-current {{
            outline: 2px solid @accent_color;
            outline-offset: 1px;
        }}
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        display = Gdk.Display.get_default()
        if getattr(self, "_css_provider", None) is not None:
            Gtk.StyleContext.remove_provider_for_display(display, self._css_provider)
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._css_provider = provider
        if getattr(self, "tree_root", None) is not None:
            self._populate_sidebar()

    # ============================================================== tree
    def _rebuild_tree(self) -> None:
        roots = self.config.scanned_folders
        records = list(self.index.records.values())
        self.tree_root = build_tree(records, roots)
        self._populate_sidebar()

    def _populate_sidebar(self) -> None:
        if not hasattr(self, "_root_store"):
            return
        roots = self.config.scanned_folders
        if not roots:
            self.sidebar_stack.set_visible_child_name("empty")
            self._refresh_actions()
            return
        self.sidebar_stack.set_visible_child_name("list")

        assert self.tree_root is not None
        focus_node = find_node(self.tree_root, self.focus_path) or self.tree_root
        focused_is_root = os.path.normpath(self.focus_path) == os.path.normpath(
            self.tree_root.path
        )

        self._root_store.remove_all()

        # Flattened, italic ancestor rows above the focused node.
        if not focused_is_root:
            for anc in ancestor_chain(self.tree_root, focus_node.path):
                self._root_store.append(FolderItem(anc, style="ancestor"))

        # The focused node itself, bold and expandable.
        self._root_store.append(FolderItem(focus_node, style="focused"))
        self._refresh_actions()

    def _find_tree_row(self, path: str):
        """Return the Gtk.TreeListRow whose FolderItem matches *path*, or None."""
        target = os.path.normpath(path)
        n = self._tree_model.get_n_items()
        for i in range(n):
            row = self._tree_model.get_item(i)
            item = row.get_item()
            if os.path.normpath(item.node.path) == target:
                return row, i
        return None, -1

    # ============================================= sidebar interactions
    def _on_tree_selected(self, selection, _param) -> None:
        row = selection.get_selected_item()   # Gtk.TreeListRow or None
        if row is None:
            return
        item: FolderItem = row.get_item()
        node = item.node
        if item.style == "ancestor":
            # Clicking a flattened ancestor (incl. "/") re-focuses there.
            print(f"[tadaima] re-focus via ancestor click: {node.path}", flush=True)
            self.set_focus_folder(node.path)
            return
        self.selected_folder = node
        self._show_folder_photos(node)

    def _on_row_right_click(self, gesture, n_press, x, y, list_item) -> None:
        row = list_item.get_item()
        item: FolderItem = row.get_item()
        node = item.node
        print(f"[tadaima] right-click on folder: {node.path}", flush=True)
        self._install_context_actions(node)

        popover = Gtk.PopoverMenu()
        m = Gio.Menu()
        m.append("Focus on folder", "win.ctx-focus")
        m.append("Locate on disk", "win.ctx-locate")
        popover.set_menu_model(m)
        popover.set_parent(gesture.get_widget())
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: p.unparent())
        popover.popup()

    def _install_context_actions(self, node: FolderNode) -> None:
        for name in ("ctx-focus", "ctx-locate"):
            if name in self._actions:
                self.remove_action(name)
                del self._actions[name]

        target = node.path

        def do_focus(_a, _p):
            print(f"[tadaima] Focus on folder chosen: {target}", flush=True)
            self.set_focus_folder(target)

        focus = Gio.SimpleAction.new("ctx-focus", None)
        focus.connect("activate", do_focus)
        self.add_action(focus)
        self._actions["ctx-focus"] = focus

        locate = Gio.SimpleAction.new("ctx-locate", None)
        locate.connect(
            "activate", lambda a, p, path=target: reveal_in_file_manager(path)
        )
        self.add_action(locate)
        self._actions["ctx-locate"] = locate

    def set_focus_folder(self, path: str) -> None:
        norm = os.path.normpath(path)
        print(f"[tadaima] set_focus_folder -> {norm}", flush=True)
        self.focus_path = norm
        self._populate_sidebar()
        self._set_status(
            f"Focused on {os.path.basename(norm) or '/'}", transient=True
        )

    # ============================================== detail (thumbnails)
    def _show_folder_photos(self, node: FolderNode) -> None:
        child = self.flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.flow.remove(child)
            child = nxt

        images = node.own_images
        if not images:
            self.detail_stack.set_visible_child_name("empty")
            return
        self.detail_stack.set_visible_child_name("grid")
        for rec in images:
            self.flow.append(self._make_thumb(rec))

    def _make_thumb(self, rec) -> Gtk.Widget:
        fbchild = Gtk.FlowBoxChild()
        fbchild._image_path = rec.path
        fbchild._rec = rec
        fbchild.add_css_class("tadaima-cell")

        pic = Gtk.Picture()
        pic.set_size_request(THUMB_CELL, THUMB_CELL)
        pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        pic.set_can_shrink(True)
        pic.set_halign(Gtk.Align.CENTER)
        pic.set_valign(Gtk.Align.CENTER)
        pic.add_css_class("tadaima-thumb")
        src = None
        if rec.thumb_path and os.path.exists(rec.thumb_path):
            src = rec.thumb_path
        elif os.path.exists(rec.path):
            src = rec.path
        if src:
            pic.set_filename(src)
        pic.set_tooltip_text(rec.name)
        pic.set_cursor(Gdk.Cursor.new_from_name("pointer"))

        fbchild.set_child(pic)
        return fbchild

    def _on_thumb_selected(self, flow) -> None:
        sel = flow.get_selected_children()
        self.selected_image_path = (
            getattr(sel[0], "_image_path", None) if sel else None
        )

    def _on_thumb_activated(self, flow, child) -> None:
        rec = getattr(child, "_rec", None)
        if rec is None:
            return
        self.selected_image_path = rec.path
        self._full_images = (
            list(self.selected_folder.own_images) if self.selected_folder else [rec]
        )
        self._full_index = next(
            (i for i, r in enumerate(self._full_images) if r.path == rec.path), 0
        )
        self._build_filmstrip()
        self._show_full_at(self._full_index)
        self.stack.set_visible_child_name("full")
        self._refresh_actions()

    # ================================================ full view + filmstrip
    def _build_filmstrip(self) -> None:
        child = self.filmstrip.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.filmstrip.remove(child)
            child = nxt

        self._film_widgets = []
        for i, rec in enumerate(self._full_images):
            btn = Gtk.Button()
            btn.add_css_class("flat")
            src = (
                rec.thumb_path
                if (rec.thumb_path and os.path.exists(rec.thumb_path))
                else rec.path
            )
            pic = Gtk.Picture()
            pic.set_content_fit(Gtk.ContentFit.CONTAIN)
            pic.set_size_request(-1, FILMSTRIP_H)
            pic.add_css_class("tadaima-film-thumb")
            if src and os.path.exists(src):
                pic.set_filename(src)
            pic.set_cursor(Gdk.Cursor.new_from_name("pointer"))
            btn.set_child(pic)
            btn.set_tooltip_text(rec.name)
            btn.connect("clicked", lambda b, idx=i: self._show_full_at(idx))
            self.filmstrip.append(btn)
            self._film_widgets.append((btn, pic))

    def _show_full_at(self, idx: int) -> None:
        if not self._full_images:
            return
        idx = max(0, min(idx, len(self._full_images) - 1))
        self._full_index = idx
        rec = self._full_images[idx]

        show = (
            rec.screen_path
            if (rec.screen_path and os.path.exists(rec.screen_path))
            else rec.path
        )
        if os.path.exists(show):
            self.full_picture.set_filename(show)
        self.full_caption.set_text(
            f"{rec.name}    ·    {idx + 1} of {len(self._full_images)}"
        )
        self.selected_image_path = rec.path

        for i, (btn, pic) in enumerate(getattr(self, "_film_widgets", [])):
            if i == idx:
                pic.add_css_class("tadaima-film-thumb-current")
            else:
                pic.remove_css_class("tadaima-film-thumb-current")

        self.prev_btn.set_sensitive(idx > 0)
        self.next_btn.set_sensitive(idx < len(self._full_images) - 1)

    def on_prev_photo(self) -> None:
        self._show_full_at(self._full_index - 1)

    def on_next_photo(self) -> None:
        self._show_full_at(self._full_index + 1)

    def _on_full_key(self, controller, keyval, keycode, state) -> bool:
        if keyval == Gdk.KEY_Left:
            self.on_prev_photo()
            return True
        if keyval == Gdk.KEY_Right:
            self.on_next_photo()
            return True
        return False

    # ============================================================ actions
    def on_back(self) -> None:
        self.stack.set_visible_child_name("gallery")
        if self.selected_image_path is not None:
            child = self.flow.get_first_child()
            while child is not None:
                if getattr(child, "_image_path", None) == self.selected_image_path:
                    self.flow.select_child(child)
                    break
                child = child.get_next_sibling()
        self._refresh_actions()

    def on_add_folder(self) -> None:
        """Kept for the file-picker flow used by the Scanned folders window."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Add a folder to scan (read-only)")

        def done(dlg, result):
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return
            if folder is None:
                return
            path = folder.get_path()
            if path and self.config.add_scanned_folder(path):
                self._rebuild_tree()
                self._start_background_scan()
                if self._scanned_folders_win is not None:
                    self._refresh_scanned_folders_window()

        dialog.select_folder(self, None, done)

    def on_manage_folders(self) -> None:
        # Bring an existing window forward rather than stacking duplicates.
        if self._scanned_folders_win is not None:
            self._scanned_folders_win.present()
            return

        win = Adw.Window()
        win.set_modal(True)
        win.set_transient_for(self)
        win.set_default_size(560, 420)
        win.set_title("Scanned folders")
        self._scanned_folders_win = win

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        add_btn = Gtk.Button(label="Add…")
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", lambda b: self.on_add_folder())
        header.pack_start(add_btn)
        tv.add_top_bar(header)

        self._scanned_group_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        clamp = Adw.Clamp()
        clamp.set_child(self._scanned_group_holder)
        clamp.set_margin_top(18)
        clamp.set_margin_bottom(18)
        clamp.set_margin_start(12)
        clamp.set_margin_end(12)
        tv.set_content(clamp)
        win.set_content(tv)

        # Esc closes the window.
        key = Gtk.EventControllerKey()
        key.connect(
            "key-pressed",
            lambda c, kv, kc, st: (win.close() or True)
            if kv == Gdk.KEY_Escape
            else False,
        )
        win.add_controller(key)
        win.connect("close-request", self._on_scanned_window_closed)

        self._refresh_scanned_folders_window()
        win.present()

    def _on_scanned_window_closed(self, win) -> bool:
        self._scanned_folders_win = None
        return False

    def _refresh_scanned_folders_window(self) -> None:
        holder = self._scanned_group_holder
        child = holder.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            holder.remove(child)
            child = nxt

        group = Adw.PreferencesGroup()
        group.set_title("Folders scanned for photos")
        group.set_description("These folders are only ever read, never modified.")

        folders = self.config.scanned_folders
        if not folders:
            row = Adw.ActionRow()
            row.set_title("No folders added yet")
            group.add(row)
        for path in folders:
            row = Adw.ActionRow()
            row.set_title(os.path.basename(path) or path)
            row.set_subtitle(path)
            remove = Gtk.Button()
            remove.set_icon_name("user-trash-symbolic")
            remove.set_valign(Gtk.Align.CENTER)
            remove.add_css_class("flat")
            remove.connect("clicked", lambda b, p=path: self._remove_folder(p))
            row.add_suffix(remove)
            group.add(row)
        holder.append(group)

    def _remove_folder(self, path: str) -> None:
        self.config.remove_scanned_folder(path)
        self._rebuild_tree()
        self._refresh_scanned_folders_window()

    def on_cache_status(self) -> None:
        st = cache_status(self.index)
        lines = [
            f"Indexed images: {st['indexed_images']:,}",
            f"Thumbnails generated: {st['thumbs_generated']:,}",
            f"Screen previews generated: {st['screens_generated']:,}",
            f"Cache files: {st['cache_files']:,}",
            f"Cache size: {human_bytes(st['cache_bytes'])}",
            "",
            f"Location: {st['cache_dir']}",
            f"Pillow available: {'yes' if st['pillow_available'] else 'no'}",
            f"HEIC support: {'yes' if st['heif_available'] else 'no'}",
        ]
        dlg = Adw.MessageDialog(
            transient_for=self, heading="Cache status", body="\n".join(lines)
        )
        dlg.add_response("regen", "Regenerate all caches")
        dlg.add_response("close", "Close")
        dlg.set_default_response("close")
        dlg.set_close_response("close")
        dlg.set_response_appearance("regen", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect(
            "response",
            lambda d, r: self.on_regenerate_cache() if r == "regen" else None,
        )
        dlg.present()

    def on_regenerate_cache(self) -> None:
        if self._scanning:
            return
        clear_cache()
        self.index = Index()
        self._rebuild_tree()
        self._start_background_scan(force=True)

    def on_preferences(self) -> None:
        from .gtk4_preferences import PreferencesWindow

        PreferencesWindow(self).present()

    def on_shortcuts(self) -> None:
        from .gtk4_shortcuts import build_shortcuts_window

        build_shortcuts_window(self).present()

    def on_about(self) -> None:
        about = Adw.AboutWindow(
            transient_for=self,
            application_name=APP_NAME,
            application_icon=ICON_NAME,
            version=__version__,
            comments="A read-only photo browser for folders you choose.",
            developer_name="QDVC",
            license_type=Gtk.License.MIT_X11,
        )
        about.present()

    def on_quit(self) -> None:
        self.close()

    # ============================================= background scanning
    def _start_background_scan(self, force: bool = False) -> bool:
        if self._scanning:
            return False
        roots = self.config.scanned_folders
        if not roots:
            return False
        self._scanning = True
        self._refresh_actions()
        thread = threading.Thread(
            target=self._scan_worker, args=(roots, force), daemon=True
        )
        thread.start()
        return False

    def _scan_worker(self, roots: list[str], force: bool) -> None:
        try:
            self._post_status("Scanning folders for changes…")
            # sync_index updates self.index in place. Do a first tree refresh so
            # subfolders appear as soon as the walk has found them.
            new_or_changed, _removed = sync_index(self.index, roots)
            GLib.idle_add(self._live_refresh)

            square_targets: set[str] = set()
            by_dir: dict[str, list] = {}
            for rec in self.index.records.values():
                by_dir.setdefault(os.path.dirname(rec.path), []).append(rec)
            for d, recs in by_dir.items():
                recs.sort(key=lambda r: r.name.lower())
                if recs:
                    square_targets.add(recs[0].path)

            work = new_or_changed if not force else list(self.index.records.values())
            total = len(work)
            last_status = 0.0
            last_refresh = time.monotonic()
            for i, rec in enumerate(work, start=1):
                now = time.monotonic()
                if now - last_status > 0.03 or i == total:
                    self._post_status(f"Processing {i} of {total}: {rec.name}")
                    last_status = now
                want_square = rec.path in square_targets
                generate_derivatives(rec, want_square=want_square)
                # Periodically push freshly-thumbnailed images into the UI.
                if now - last_refresh > LIVE_REFRESH_SECONDS:
                    GLib.idle_add(self._live_refresh)
                    last_refresh = now

            for path in square_targets:
                rec = self.index.records.get(path)
                if rec and not rec.square_path:
                    generate_derivatives(rec, want_square=True)

            self._post_status("Saving index…")
            self.index.save()
        finally:
            GLib.idle_add(self._scan_finished)

    def _post_status(self, text: str) -> None:
        GLib.idle_add(self._set_status, text)

    def _set_status(self, text: str, transient: bool = False) -> bool:
        self.status_label.set_text(text)
        if transient:
            GLib.timeout_add(1500, self._reset_status_if_idle)
        return False

    def _reset_status_if_idle(self) -> bool:
        if not self._scanning:
            self.status_label.set_text(self._status_default)
        return False

    def _live_refresh(self) -> bool:
        """Rebuild the tree from the current index and refresh the open folder's
        thumbnails, preserving selection and focus. Called on the main thread
        while a scan is still running."""
        prev = self.selected_folder.path if self.selected_folder else None
        self.tree_root = build_tree(list(self.index.records.values()),
                                    self.config.scanned_folders)
        self._populate_sidebar()
        if prev and self.tree_root:
            node = find_node(self.tree_root, prev)
            if node:
                self.selected_folder = node
                self._show_folder_photos(node)
        return False

    def _scan_finished(self) -> bool:
        self._scanning = False
        n = len(self.index.records)
        self._status_default = f"{format_count(n)} photos indexed"
        self.status_label.set_text(self._status_default)
        self._live_refresh()
        self._refresh_actions()
        return False

    # ============================================================ misc
    def _on_paned_moved(self, paned, _param) -> None:
        pos = paned.get_position()
        if pos > 0:
            self.config.sidebar_width = pos

    def _refresh_actions(self) -> None:
        has_folders = bool(self.config.scanned_folders)
        set_enabled(self, "regenerate-cache", has_folders and not self._scanning)
        in_full = self.stack.get_visible_child_name() == "full"
        set_enabled(self, "back", in_full)
        set_enabled(self, "prev-photo", in_full and self._full_index > 0)
        set_enabled(
            self,
            "next-photo",
            in_full and self._full_index < len(self._full_images) - 1,
        )

    def do_close_request(self) -> bool:
        w = self.get_width()
        h = self.get_height()
        if w > 0 and h > 0:
            self.config.set("window", {"width": w, "height": h})
        return False

"""Main window: resizable master-detail (sidebar + wrapping thumbnail grid), a
status bar with live background-scan progress, and a full-image view with a
filmstrip, prev/next arrows, and left/right keyboard navigation.

All access to user folders is read-only. The window never writes to a scanned
folder; derivatives go only to the cache directory.
"""

from __future__ import annotations

import os
import threading
import time

from gi.repository import Adw, Gdk, GLib, Gio, Gtk, Pango

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

# Thumbnail grid: the box each thumbnail is allocated. The image is scaled to
# fit inside this box preserving aspect ratio, so portraits/landscapes keep
# their true proportions and simply under-fill the cell (never cropped).
THUMB_CELL = 190
# Filmstrip thumbnail height (width follows aspect ratio).
FILMSTRIP_H = 72


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

        # Full-view / filmstrip state.
        self._full_images: list = []            # ImageRecord list for current folder
        self._full_index: int = -1              # index within _full_images

        self._scanning = False
        self._status_default = "Ready"

        win = self.config.get("window", {"width": 1100, "height": 720})
        self.set_default_size(int(win.get("width", 1100)), int(win.get("height", 720)))
        self.set_title(APP_NAME)
        self.set_icon_name(ICON_NAME)

        install_actions(self)
        self._build_ui()
        self._apply_density()
        self._rebuild_tree()

        # Kick off a quiet background scan on launch.
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
        folders.append("Add scanned folder…", "win.add-folder")
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
        # No add button in the top-left corner; the menu (top-right) holds it.
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_primary(True)
        menu_btn.set_menu_model(self._primary_menu())
        header.pack_end(menu_btn)
        toolbar_view.add_top_bar(header)

        # Master-detail: resizable paned split.
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

        # Status bar at the bottom of the main window.
        self.status_label = Gtk.Label(label=self._status_default)
        self.status_label.set_xalign(0.0)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.status_label.add_css_class("tadaima-status")
        status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        status_bar.add_css_class("tadaima-statusbar")
        status_bar.append(self.status_label)
        toolbar_view.add_bottom_bar(status_bar)

        return toolbar_view

    def _build_sidebar(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_size_request(180, -1)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.sidebar_list = Gtk.ListBox()
        self.sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.sidebar_list.add_css_class("navigation-sidebar")
        self.sidebar_list.add_css_class("tadaima-sidebar")
        self.sidebar_list.connect("row-activated", self._on_sidebar_row_activated)
        scroller.set_child(self.sidebar_list)

        self.sidebar_placeholder = Adw.StatusPage()
        self.sidebar_placeholder.set_icon_name("folder-symbolic")
        self.sidebar_placeholder.set_title("No folders yet")
        self.sidebar_placeholder.set_description(
            "Add a folder to browse its photos. Folders are only ever read."
        )
        ph_btn = Gtk.Button(label="Add scanned folder")
        ph_btn.add_css_class("suggested-action")
        ph_btn.add_css_class("pill")
        ph_btn.set_halign(Gtk.Align.CENTER)
        ph_btn.set_action_name("win.add-folder")
        self.sidebar_placeholder.set_child(ph_btn)

        self.sidebar_stack = Gtk.Stack()
        self.sidebar_stack.add_named(scroller, "list")
        self.sidebar_stack.add_named(self.sidebar_placeholder, "empty")
        outer.append(self.sidebar_stack)
        return outer

    def _build_detail(self) -> Gtk.Widget:
        self.detail_scroller = Gtk.ScrolledWindow()
        self.detail_scroller.set_hexpand(True)
        self.detail_scroller.set_vexpand(True)
        self.detail_scroller.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )

        # FlowBox lays thumbnails out as a wrapping grid (not one long strip):
        # multiple children per line, growing to as many columns as fit.
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

    def _build_full_page(self) -> Gtk.Widget:
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        back_btn = Gtk.Button()
        back_btn.set_icon_name("go-previous-symbolic")
        back_btn.set_tooltip_text("Back to gallery")
        back_btn.set_action_name("win.back")
        header.pack_start(back_btn)
        # Window title stays as-is; header shows no per-photo title.
        toolbar_view.add_top_bar(header)

        # Filmstrip at the top: prev arrow | scrollable strip | next arrow.
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
        strip_scroller.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER
        )
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

        # Main picture.
        self.full_picture = Gtk.Picture()
        self.full_picture.set_can_shrink(True)
        self.full_picture.set_hexpand(True)
        self.full_picture.set_vexpand(True)
        self.full_picture.set_content_fit(Gtk.ContentFit.CONTAIN)

        # Caption bar beneath the picture: filename, centered.
        self.full_caption = Gtk.Label()
        self.full_caption.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.full_caption.set_justify(Gtk.Justification.CENTER)
        self.full_caption.set_halign(Gtk.Align.CENTER)
        self.full_caption.add_css_class("tadaima-caption")
        caption_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        caption_bar.add_css_class("tadaima-caption-bar")
        caption_bar.set_halign(Gtk.Align.FILL)
        self.full_caption.set_hexpand(True)
        caption_bar.append(self.full_caption)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.full_picture)
        content.append(caption_bar)
        toolbar_view.set_content(content)

        # Left/right arrow keys navigate the full view.
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
        .tadaima-thumb {{
            box-shadow: 0 2px 6px rgba(0,0,0,0.45);
            border-radius: 3px;
            background: transparent;
        }}
        flowboxchild.tadaima-cell {{
            padding: 4px;
            border-radius: 4px;
        }}
        flowboxchild.tadaima-cell:selected {{
            outline: 2px solid @accent_color;
            outline-offset: -1px;
            background: alpha(@accent_color, 0.18);
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
        # Re-render sidebar so icon sizes/indent pick up the new spec.
        if getattr(self, "tree_root", None) is not None:
            self._populate_sidebar()

    # ============================================================== tree
    def _rebuild_tree(self) -> None:
        roots = self.config.scanned_folders
        records = list(self.index.records.values())
        self.tree_root = build_tree(records, roots)
        self._populate_sidebar()

    def _populate_sidebar(self) -> None:
        child = self.sidebar_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.sidebar_list.remove(child)
            child = nxt

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

        # Ancestors above the focused node: flat italic list (clickable).
        if not focused_is_root:
            for anc in ancestor_chain(self.tree_root, focus_node.path):
                self.sidebar_list.append(
                    self._make_row(anc, depth=0, style="ancestor")
                )

        # Focused node (bold), then its subtree.
        self.sidebar_list.append(self._make_row(focus_node, depth=0, style="focused"))
        for c in focus_node.sorted_children():
            self._append_subtree(c, depth=1)

        self._refresh_actions()

    def _append_subtree(self, node: FolderNode, depth: int) -> None:
        self.sidebar_list.append(self._make_row(node, depth=depth, style="normal"))
        for c in node.sorted_children():
            self._append_subtree(c, depth + 1)

    def _make_row(self, node: FolderNode, depth: int, style: str) -> Gtk.ListBoxRow:
        spec = density_spec(self.config.density)
        row = Gtk.ListBoxRow()
        row._node = node
        row._style = style

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        box.set_margin_start(int(spec["indent"]) * depth)

        icon_widget = self._folder_icon(node)
        box.append(icon_widget)

        label = Gtk.Label(label=node.name if node.name != os.path.sep else "/")
        label.set_xalign(0.0)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        label.add_css_class("tadaima-row-label")
        if style == "ancestor":
            label.add_css_class("tadaima-ancestor")
        elif style == "focused":
            label.add_css_class("tadaima-focused")
        box.append(label)

        count = Gtk.Label(label=format_count(node.image_count))
        count.add_css_class("tadaima-count")
        count.add_css_class("tadaima-row-label")
        count.add_css_class("numeric")
        count.set_xalign(1.0)
        box.append(count)

        row.set_child(box)

        gesture = Gtk.GestureClick()
        gesture.set_button(3)
        gesture.connect("pressed", self._on_row_right_click, node)
        row.add_controller(gesture)
        return row

    def _folder_icon(self, node: FolderNode) -> Gtk.Widget:
        px = getattr(self, "_sidebar_icon_px", 16)
        square = None
        for rec in node.own_images:
            if rec.square_path and os.path.exists(rec.square_path):
                square = rec.square_path
                break
        if square:
            img = Gtk.Image.new_from_file(square)
        else:
            img = Gtk.Image.new_from_icon_name("folder-symbolic")
        img.set_pixel_size(px)
        return img

    # ============================================= sidebar interactions
    def _on_sidebar_row_activated(self, listbox, row) -> None:
        """Single click (activate) selects a folder, or re-focuses when the row
        is a flattened ancestor. Both selection and focus routing live here so a
        click always does something visible."""
        if row is None:
            return
        node: FolderNode = getattr(row, "_node", None)
        style = getattr(row, "_style", "normal")
        if node is None:
            return
        if style == "ancestor":
            # Clicking a flattened ancestor (incl. top-level "/") re-focuses.
            self.set_focus_folder(node.path)
            return
        # Focused row or a normal subtree row: show its photos.
        self.selected_folder = node
        self._show_folder_photos(node)

    def _on_row_right_click(self, gesture, n_press, x, y, node: FolderNode) -> None:
        self._install_context_actions(node)

        popover = Gtk.PopoverMenu()
        m = Gio.Menu()
        m.append("Focus on folder", "win.ctx-focus")
        m.append("Locate on disk", "win.ctx-locate")
        popover.set_menu_model(m)

        widget = gesture.get_widget()
        popover.set_parent(widget)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        # Clean up the popover once dismissed so parents don't accumulate.
        popover.connect("closed", lambda p: p.unparent())
        popover.popup()

    def _install_context_actions(self, node: FolderNode) -> None:
        for name in ("ctx-focus", "ctx-locate"):
            if name in self._actions:
                self.remove_action(name)
                del self._actions[name]

        focus = Gio.SimpleAction.new("ctx-focus", None)
        focus.connect("activate", lambda a, p, path=node.path: self.set_focus_folder(path))
        self.add_action(focus)
        self._actions["ctx-focus"] = focus

        locate = Gio.SimpleAction.new("ctx-locate", None)
        locate.connect(
            "activate", lambda a, p, path=node.path: reveal_in_file_manager(path)
        )
        self.add_action(locate)
        self._actions["ctx-locate"] = locate

    def set_focus_folder(self, path: str) -> None:
        self.focus_path = os.path.normpath(path)
        self._populate_sidebar()
        self._set_status(
            f"Focused on {os.path.basename(self.focus_path) or '/'}", transient=True
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
        # Fixed cell; image scales to fit inside preserving aspect ratio, so it
        # is never cropped and may under-fill the cell.
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

        # Link (hand) cursor on hover.
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
        # Build the full-view sequence from the current folder's images.
        self._full_images = list(self.selected_folder.own_images) if self.selected_folder else [rec]
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
            src = rec.thumb_path if (rec.thumb_path and os.path.exists(rec.thumb_path)) else rec.path
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

        # Update filmstrip highlight.
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
        # Re-select the image we were viewing in the grid.
        if self.selected_image_path is not None:
            child = self.flow.get_first_child()
            while child is not None:
                if getattr(child, "_image_path", None) == self.selected_image_path:
                    self.flow.select_child(child)
                    break
                child = child.get_next_sibling()
        self._refresh_actions()

    def on_add_folder(self) -> None:
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

        dialog.select_folder(self, None, done)

    def on_manage_folders(self) -> None:
        win = Adw.Window()
        win.set_modal(True)
        win.set_transient_for(self)
        win.set_default_size(560, 420)
        win.set_title("Scanned folders")

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        add_btn = Gtk.Button(label="Add…")
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", lambda b: self.on_add_folder())
        header.pack_start(add_btn)
        tv.add_top_bar(header)

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
            remove.connect(
                "clicked",
                lambda b, p=path, w=win: self._remove_folder_and_refresh(p, w),
            )
            row.add_suffix(remove)
            group.add(row)

        clamp = Adw.Clamp()
        clamp.set_child(group)
        clamp.set_margin_top(18)
        clamp.set_margin_bottom(18)
        clamp.set_margin_start(12)
        clamp.set_margin_end(12)
        tv.set_content(clamp)
        win.set_content(tv)
        win.present()

    def _remove_folder_and_refresh(self, path: str, win) -> None:
        self.config.remove_scanned_folder(path)
        self._rebuild_tree()
        win.close()
        self.on_manage_folders()

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
            new_or_changed, _removed = sync_index(self.index, roots)

            # Choose one representative image per directory for its square icon.
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
            last_post = 0.0
            for i, rec in enumerate(work, start=1):
                # Update the status bar frequently so it never looks stuck.
                now = time.monotonic()
                if now - last_post > 0.03 or i == total:
                    self._post_status(
                        f"Processing {i} of {total}: {rec.name}"
                    )
                    last_post = now
                want_square = rec.path in square_targets
                generate_derivatives(rec, want_square=want_square)

            for path in square_targets:
                rec = self.index.records.get(path)
                if rec and not rec.square_path:
                    generate_derivatives(rec, want_square=True)

            self._post_status("Saving index…")
            self.index.save()
        finally:
            GLib.idle_add(self._scan_finished)

    def _post_status(self, text: str) -> None:
        """Thread-safe status update."""
        GLib.idle_add(self._set_status, text)

    def _set_status(self, text: str, transient: bool = False) -> bool:
        self.status_label.set_text(text)
        if transient:
            # Revert to the resting status shortly after a one-off message.
            GLib.timeout_add(1500, self._reset_status_if_idle)
        return False

    def _reset_status_if_idle(self) -> bool:
        if not self._scanning:
            self.status_label.set_text(self._status_default)
        return False

    def _scan_finished(self) -> bool:
        self._scanning = False
        n = len(self.index.records)
        self._status_default = f"{format_count(n)} photos indexed"
        self.status_label.set_text(self._status_default)

        prev = self.selected_folder.path if self.selected_folder else None
        self._rebuild_tree()
        if prev and self.tree_root:
            node = find_node(self.tree_root, prev)
            if node:
                self.selected_folder = node
                self._show_folder_photos(node)
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

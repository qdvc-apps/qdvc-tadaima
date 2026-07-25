"""Main window: Adw.OverlaySplitView (sidebar + gallery), full-image view,
header bar + primary menu, and background scanning/thumbnailing.

All access to user folders is read-only. The window never writes to a scanned
folder; derivatives go only to the cache directory.
"""

from __future__ import annotations

import os
import threading

from gi.repository import Adw, Gdk, GLib, Gio, Gtk, Pango

from .. import APP_NAME, __version__
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
        self._scanning = False

        win = self.config.get("window", {"width": 1100, "height": 720})
        self.set_default_size(int(win.get("width", 1100)), int(win.get("height", 720)))
        self.set_title(APP_NAME)
        self.set_icon_name("image-x-generic")

        install_actions(self)
        self._build_ui()
        self._apply_density()
        self._rebuild_tree()

        # Kick off a quiet background scan on launch.
        GLib.idle_add(self._start_background_scan)

    # ================================================================= UI
    def _build_ui(self) -> None:
        # Root: a ViewStack so we can swap gallery <-> full image.
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

        # Add-folder button promoted to the header.
        add_btn = Gtk.Button()
        add_btn.set_icon_name("list-add-symbolic")
        add_btn.set_tooltip_text("Add scanned folder")
        add_btn.set_action_name("win.add-folder")
        header.pack_start(add_btn)

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
        self.sidebar_list.connect("row-selected", self._on_sidebar_row_selected)
        scroller.set_child(self.sidebar_list)

        # Empty-state placeholder shown when no folders are scanned.
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

        self.flow = Gtk.FlowBox()
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_max_children_per_line(30)
        self.flow.set_min_children_per_line(1)
        self.flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.flow.set_homogeneous(False)
        self.flow.set_row_spacing(16)
        self.flow.set_column_spacing(16)
        self.flow.set_margin_top(16)
        self.flow.set_margin_bottom(16)
        self.flow.set_margin_start(16)
        self.flow.set_margin_end(16)
        self.flow.connect("child-activated", self._on_thumb_activated)
        self.flow.connect("selected-children-changed", self._on_thumb_selected)

        self.detail_placeholder = Adw.StatusPage()
        self.detail_placeholder.set_icon_name("image-x-generic-symbolic")
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

        self.full_title = Gtk.Label()
        self.full_title.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        header.set_title_widget(self.full_title)

        toolbar_view.add_top_bar(header)

        self.full_picture = Gtk.Picture()
        self.full_picture.set_can_shrink(True)
        self.full_picture.set_hexpand(True)
        self.full_picture.set_vexpand(True)
        self.full_picture.set_content_fit(Gtk.ContentFit.CONTAIN)

        frame = Gtk.Box()
        frame.append(self.full_picture)
        frame.set_hexpand(True)
        frame.set_vexpand(True)
        toolbar_view.set_content(frame)
        return toolbar_view

    # ============================================================ density
    def _apply_density(self) -> None:
        spec = density_spec(self.config.density)
        css = f"""
        .navigation-sidebar row {{
            padding-top: {spec['row_pad_v']}px;
            padding-bottom: {spec['row_pad_v']}px;
            padding-left: {spec['row_pad_h']}px;
            padding-right: {spec['row_pad_h']}px;
        }}
        .tadaima-thumb {{
            box-shadow: 0 2px 6px rgba(0,0,0,0.35);
            border-radius: 3px;
        }}
        .tadaima-thumb-selected {{
            outline: 3px solid @accent_color;
            outline-offset: 1px;
        }}
        .tadaima-count {{ opacity: 0.6; }}
        .tadaima-ancestor {{ font-style: italic; opacity: 0.75; }}
        .tadaima-focused {{ font-weight: bold; }}
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

    # ============================================================== tree
    def _rebuild_tree(self) -> None:
        roots = self.config.scanned_folders
        records = list(self.index.records.values())
        self.tree_root = build_tree(records, roots)
        self._populate_sidebar()

    def _populate_sidebar(self) -> None:
        # Clear existing rows.
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

        # Ancestors above the focused node: flat italic list.
        if os.path.normpath(self.focus_path) != os.path.normpath(self.tree_root.path):
            for anc in ancestor_chain(self.tree_root, focus_node.path):
                self.sidebar_list.append(
                    self._make_row(anc, depth=0, style="ancestor")
                )

        # Focused node itself (bold), then its subtree.
        self.sidebar_list.append(self._make_row(focus_node, depth=0, style="focused"))
        for c in focus_node.sorted_children():
            self._append_subtree(c, depth=1)

        self._refresh_actions()

    def _append_subtree(self, node: FolderNode, depth: int) -> None:
        self.sidebar_list.append(self._make_row(node, depth=depth, style="normal"))
        for c in node.sorted_children():
            self._append_subtree(c, depth + 1)

    def _make_row(self, node: FolderNode, depth: int, style: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row._node = node  # attach for handlers
        row._style = style

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_start(6 + depth * 14)

        # Icon: photo-crop square if the folder has direct images and a square
        # derivative exists; otherwise the themed folder icon (same size).
        icon_widget = self._folder_icon(node)
        box.append(icon_widget)

        label = Gtk.Label(label=node.name if node.name != os.path.sep else "/")
        label.set_xalign(0.0)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        if style == "ancestor":
            label.add_css_class("tadaima-ancestor")
        elif style == "focused":
            label.add_css_class("tadaima-focused")
        box.append(label)

        count = Gtk.Label(label=format_count(node.image_count))
        count.add_css_class("tadaima-count")
        count.add_css_class("numeric")
        count.set_xalign(1.0)
        box.append(count)

        row.set_child(box)

        # Right-click context menu.
        gesture = Gtk.GestureClick()
        gesture.set_button(3)
        gesture.connect("pressed", self._on_row_right_click, node)
        row.add_controller(gesture)
        return row

    def _folder_icon(self, node: FolderNode) -> Gtk.Widget:
        square = None
        for rec in node.own_images:
            if rec.square_path and os.path.exists(rec.square_path):
                square = rec.square_path
                break
        if square:
            img = Gtk.Image.new_from_file(square)
            img.set_pixel_size(20)
            return img
        icon = Gtk.Image.new_from_icon_name("folder-symbolic")
        icon.set_pixel_size(20)
        return icon

    # ============================================= sidebar interactions
    def _on_sidebar_row_selected(self, listbox, row) -> None:
        if row is None:
            return
        node: FolderNode = getattr(row, "_node", None)
        style = getattr(row, "_style", "normal")
        if node is None:
            return
        # Clicking a flattened ancestor (or the top root) re-focuses.
        if style in ("ancestor", "focused") and node.children:
            # Ancestors re-focus; the focused row itself just selects.
            if style == "ancestor":
                self.set_focus_folder(node.path)
                return
        self.selected_folder = node
        self._show_folder_photos(node)

    def _on_row_right_click(self, gesture, n_press, x, y, node: FolderNode) -> None:
        menu = Gio.Menu()
        # Store the target on the window so actions can read it.
        self._context_node = node

        popover = Gtk.PopoverMenu()
        m = Gio.Menu()
        m.append("Focus on folder", "win.ctx-focus")
        m.append("Locate on disk", "win.ctx-locate")
        popover.set_menu_model(m)

        # Install one-shot context actions.
        self._install_context_actions(node)

        widget = gesture.get_widget()
        popover.set_parent(widget)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    def _install_context_actions(self, node: FolderNode) -> None:
        for name in ("ctx-focus", "ctx-locate"):
            if name in self._actions:
                self.remove_action(name)
                del self._actions[name]

        focus = Gio.SimpleAction.new("ctx-focus", None)
        focus.connect("activate", lambda a, p: self.set_focus_folder(node.path))
        self.add_action(focus)
        self._actions["ctx-focus"] = focus

        locate = Gio.SimpleAction.new("ctx-locate", None)
        locate.connect("activate", lambda a, p: reveal_in_file_manager(node.path))
        self.add_action(locate)
        self._actions["ctx-locate"] = locate

    def set_focus_folder(self, path: str) -> None:
        self.focus_path = os.path.normpath(path)
        self._populate_sidebar()

    # ============================================== detail (thumbnails)
    def _show_folder_photos(self, node: FolderNode) -> None:
        # Clear the flowbox.
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
        fbchild._screen_path = rec.screen_path

        pic = Gtk.Picture()
        pic.set_size_request(180, 180)
        pic.set_content_fit(Gtk.ContentFit.COVER)
        pic.add_css_class("tadaima-thumb")
        if rec.thumb_path and os.path.exists(rec.thumb_path):
            pic.set_filename(rec.thumb_path)
        elif os.path.exists(rec.path):
            # Fall back to the source image if no thumbnail yet.
            pic.set_filename(rec.path)

        # Link (hand) cursor on hover.
        motion = Gtk.EventControllerMotion()
        pic.add_controller(motion)
        pic.set_cursor(Gdk.Cursor.new_from_name("pointer"))

        fbchild.set_child(pic)
        return fbchild

    def _on_thumb_selected(self, flow) -> None:
        sel = flow.get_selected_children()
        if not sel:
            self.selected_image_path = None
            return
        self.selected_image_path = getattr(sel[0], "_image_path", None)

    def _on_thumb_activated(self, flow, child) -> None:
        path = getattr(child, "_image_path", None)
        screen = getattr(child, "_screen_path", None)
        if not path:
            return
        self.selected_image_path = path
        self._open_full(path, screen)

    def _open_full(self, path: str, screen: str | None) -> None:
        show = screen if (screen and os.path.exists(screen)) else path
        if os.path.exists(show):
            self.full_picture.set_filename(show)
        self.full_title.set_text(os.path.basename(path))
        self.stack.set_visible_child_name("full")
        self._refresh_actions()

    # ============================================================ actions
    def on_back(self) -> None:
        self.stack.set_visible_child_name("gallery")
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
            transient_for=self,
            heading="Cache status",
            body="\n".join(lines),
        )
        dlg.add_response("regen", "Regenerate all caches")
        dlg.add_response("close", "Close")
        dlg.set_default_response("close")
        dlg.set_close_response("close")
        dlg.set_response_appearance("regen", Adw.ResponseAppearance.DESTRUCTIVE)

        def resp(d, response):
            if response == "regen":
                self.on_regenerate_cache()

        dlg.connect("response", resp)
        dlg.present()

    def on_regenerate_cache(self) -> None:
        if self._scanning:
            return
        clear_cache()
        self.index = Index()  # fresh, empty
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
            application_icon="image-x-generic",
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
        thread = threading.Thread(
            target=self._scan_worker, args=(roots, force), daemon=True
        )
        thread.start()
        return False  # for idle_add: run once

    def _scan_worker(self, roots: list[str], force: bool) -> None:
        try:
            new_or_changed, _removed = sync_index(self.index, roots)

            # Decide which folders need a square icon: those whose direct images
            # currently lack a square derivative. We generate square for the
            # first direct image of each such folder.
            square_targets: set[str] = set()
            by_dir: dict[str, list] = {}
            for rec in self.index.records.values():
                by_dir.setdefault(os.path.dirname(rec.path), []).append(rec)
            for d, recs in by_dir.items():
                recs.sort(key=lambda r: r.name.lower())
                if recs:
                    square_targets.add(recs[0].path)

            work = new_or_changed if not force else list(self.index.records.values())
            for rec in work:
                want_square = rec.path in square_targets or (
                    force and rec.path in square_targets
                )
                generate_derivatives(rec, want_square=want_square)
            # Ensure square exists for the chosen representative even if it was
            # not in the changed set.
            for path in square_targets:
                rec = self.index.records.get(path)
                if rec and not rec.square_path:
                    generate_derivatives(rec, want_square=True)

            self.index.save()
        finally:
            GLib.idle_add(self._scan_finished)

    def _scan_finished(self) -> bool:
        self._scanning = False
        # Preserve current selection where possible.
        prev = self.selected_folder.path if self.selected_folder else None
        self._rebuild_tree()
        if prev and self.tree_root:
            node = find_node(self.tree_root, prev)
            if node:
                self.selected_folder = node
                self._show_folder_photos(node)
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

    def do_close_request(self) -> bool:
        # Persist window size.
        w = self.get_width()
        h = self.get_height()
        if w > 0 and h > 0:
            self.config.set("window", {"width": w, "height": h})
        return False

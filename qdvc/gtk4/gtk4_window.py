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

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango

from .. import APP_NAME, ICON_NAME, __version__
from ..cache import (
    Index,
    cache_status,
    clear_cache,
    generate_derivatives,
    has_all_derivatives,
    human_bytes,
    sync_index,
)
from ..config import normalize_scanned_folders
from ..metadata import human_size, read_metadata
from ..models import FolderNode
from ..platform_utils import reveal_in_file_manager
from ..tree import ancestor_chain, build_tree, find_node
from ..ui_prefs import density_spec, format_count
from .gtk4_actions import install_actions, set_enabled
from .gtk4_items import FolderItem

# Filmstrip thumbnail size — matches the 64px square derivative it displays.
FILMSTRIP_H = 64

# Thumbnail grid: each image occupies a SLOT. As many slots as fit are packed
# per row; rows reflow; each row's height is the tallest slot in it; the
# thumbnail is centred within its slot. SLOT_WIDTH caps BOTH width and height of
# a thumbnail, so a very tall image scales down to fit (becoming narrow) rather
# than producing a super-tall row. Configurable; 150px for now.
SLOT_WIDTH = 150
SLOT_SPACING = 12


def _within(ancestor: str, descendant: str) -> bool:
    """True if *descendant* is *ancestor* or lies beneath it."""
    a = os.path.normpath(ancestor)
    d = os.path.normpath(descendant)
    if a == d:
        return True
    return d.startswith(a.rstrip(os.sep) + os.sep)


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
        # Restore focus from the previous session ("/" == no focus).
        self.focus_path: str = self.config.focus_folder or os.path.sep
        self.selected_folder: FolderNode | None = None
        self.selected_image_path: str | None = None
        self._selected_rec = None

        # Expansion memory: folder paths the user has expanded. Seeded from the
        # previous session so the tree opens where the user left it.
        self._expanded: set[str] = set(self.config.expanded_folders)
        # Guard so programmatic restore doesn't thrash the persisted state.
        self._restoring_state = False
        self._closing = False

        # Full-view / filmstrip state.
        self._full_images: list = []
        self._full_index: int = -1

        self._scanning = False
        self._status_default = "Ready"
        self._scanned_folders_win: Adw.Window | None = None
        self._thumb_zoom = self.config.thumb_zoom
        self._pending_selected = self.config.selected_folder

        win = self.config.get("window", {"width": 1100, "height": 720})
        self.set_default_size(int(win.get("width", 1100)), int(win.get("height", 720)))
        self.set_title(APP_NAME)
        self.set_icon_name(ICON_NAME)

        install_actions(self)
        self._build_ui()
        self._apply_density()
        self._rebuild_tree()
        # Restore the saved expansion/selection once the tree is populated.
        GLib.idle_add(self._restore_session_state)

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

        # Info button (left of the menu): shows the selected photo's metadata.
        self.gallery_info_btn = Gtk.Button()
        self.gallery_info_btn.set_icon_name("dialog-information-symbolic")
        self.gallery_info_btn.set_tooltip_text("Photo information")
        self.gallery_info_btn.set_action_name("win.photo-info")
        header.pack_end(self.gallery_info_btn)
        toolbar_view.add_top_bar(header)

        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.set_wide_handle(True)
        self.paned.set_position(self.config.sidebar_width)
        self.paned.connect("notify::position", self._on_paned_moved)
        self.paned.set_start_child(self._build_sidebar())
        self.paned.set_resize_start_child(False)
        self.paned.set_shrink_start_child(False)

        # Detail area + a right-hand info sidebar (a revealer that slides in).
        detail_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        detail_widget = self._build_detail()
        detail_widget.set_hexpand(True)
        detail_row.append(detail_widget)

        self.info_revealer = Gtk.Revealer()
        self.info_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_LEFT
        )
        self.info_revealer.set_reveal_child(False)
        self.info_revealer.set_hexpand(False)
        self.info_revealer.set_child(self._build_info_panel())
        detail_row.append(self.info_revealer)

        self.paned.set_end_child(detail_row)
        self.paned.set_resize_end_child(True)
        toolbar_view.set_content(self.paned)

        # Filename bar for the currently-selected photo, sitting directly above
        # the status bar. Mirrors the caption in the full-photo viewer.
        self.gallery_caption = Gtk.Label(label="")
        self.gallery_caption.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.gallery_caption.set_justify(Gtk.Justification.CENTER)
        self.gallery_caption.set_halign(Gtk.Align.CENTER)
        self.gallery_caption.set_hexpand(True)
        self.gallery_caption.add_css_class("tadaima-caption")
        caption_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        caption_bar.add_css_class("tadaima-caption-bar")
        caption_bar.append(self.gallery_caption)

        self.status_label = Gtk.Label(label=self._status_default)
        self.status_label.set_xalign(0.0)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.status_label.add_css_class("tadaima-status")
        status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        status_bar.add_css_class("tadaima-statusbar")
        status_bar.append(self.status_label)

        # Added filename bar first so it renders above the status bar.
        toolbar_view.add_bottom_bar(caption_bar)
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

        # Right arrow expands the selected folder; Left collapses it.
        tkey = Gtk.EventControllerKey()
        tkey.connect("key-pressed", self._on_sidebar_key)
        self.tree_view.add_controller(tkey)

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

        # Right-click context menu on the whole row. Capture phase so the
        # ListView's own click handling doesn't swallow button-3 first.
        gesture = Gtk.GestureClick()
        gesture.set_button(3)
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
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

        # Track expansion so it can be persisted. Disconnect any handler from a
        # previous bind of this reused list_item first.
        prev = getattr(list_item, "_exp_handler", None)
        prev_row = getattr(list_item, "_exp_row", None)
        if prev is not None and prev_row is not None:
            try:
                prev_row.disconnect(prev)
            except (TypeError, RuntimeError):
                pass
        handler = row.connect("notify::expanded", self._on_row_expanded_changed)
        list_item._exp_handler = handler
        list_item._exp_row = row

    def _on_row_expanded_changed(self, row, _param) -> None:
        item = row.get_item()
        if item is None:
            return
        path = os.path.normpath(item.node.path)
        if row.get_expanded():
            self._expanded.add(path)
        else:
            self._expanded.discard(path)
        if not self._restoring_state:
            self._persist_sidebar_state()

    def _persist_sidebar_state(self) -> None:
        self.config.expanded_folders = sorted(self._expanded)
        self.config.selected_folder = (
            self.selected_folder.path if self.selected_folder else None
        )
        self.config.focus_folder = (
            None if self.focus_path == os.path.sep else self.focus_path
        )

    def _build_info_panel(self) -> Gtk.Widget:
        """Right-hand information sidebar for the selected photo."""
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        panel.set_size_request(280, -1)
        panel.add_css_class("tadaima-info-panel")

        # Small header with a title and a close button.
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        head.add_css_class("tadaima-info-head")
        title = Gtk.Label(label="Information")
        title.set_xalign(0.0)
        title.set_hexpand(True)
        title.add_css_class("heading")
        close = Gtk.Button()
        close.set_icon_name("window-close-symbolic")
        close.add_css_class("flat")
        close.set_tooltip_text("Close information")
        close.connect("clicked", lambda b: self._set_info_visible(False))
        head.append(title)
        head.append(close)
        panel.append(head)

        # Metadata fields only — no image preview in the info panel.
        self.info_group = Adw.PreferencesGroup()
        self.info_group.set_margin_top(12)
        self.info_group.set_margin_bottom(12)
        self.info_group.set_margin_start(12)
        self.info_group.set_margin_end(12)
        self._info_rows: list = []
        for key in ("Name", "Date taken", "Dimensions", "File size", "Path"):
            row = Adw.ActionRow()
            row.set_title(key)
            row.set_subtitle("—")
            row.set_subtitle_selectable(True)
            self.info_group.add(row)
            self._info_rows.append(row)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.info_group)
        panel.append(scroller)
        return panel

    def _update_info_panel(self) -> None:
        rec = getattr(self, "_selected_rec", None)
        if rec is None or not hasattr(self, "_info_rows"):
            for row in getattr(self, "_info_rows", []):
                row.set_subtitle("—")
            return
        md = read_metadata(rec.path)
        values = [
            rec.name,
            md.date_str,
            md.dimensions_str,
            human_size(md.size_bytes),
            rec.path,
        ]
        for row, val in zip(self._info_rows, values):
            row.set_subtitle(val)

    def _set_info_visible(self, visible: bool) -> None:
        self.info_revealer.set_reveal_child(visible)
        if visible:
            self._update_info_panel()

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
        self.flow.set_halign(Gtk.Align.FILL)
        self.flow.set_hexpand(True)
        self.flow.set_max_children_per_line(10000)
        self.flow.set_min_children_per_line(1)
        self.flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        # Single click selects only; double-click (or Enter) opens the viewer.
        self.flow.set_activate_on_single_click(False)
        # Slot model: every child is a fixed SLOT_WIDTH-wide slot of natural
        # height, so FlowBox packs floor(width / (SLOT_WIDTH + spacing)) slots
        # per row and sets each row's height to its tallest slot. NOT
        # homogeneous — homogeneous would force a uniform height across all rows.
        self.flow.set_homogeneous(False)
        self.flow.set_row_spacing(SLOT_SPACING)
        self.flow.set_column_spacing(SLOT_SPACING)
        self.flow.set_margin_top(SLOT_SPACING)
        self.flow.set_margin_bottom(SLOT_SPACING)
        self.flow.set_margin_start(SLOT_SPACING)
        self.flow.set_margin_end(SLOT_SPACING)
        self.flow.connect("child-activated", self._on_thumb_activated)
        self.flow.connect("selected-children-changed", self._on_thumb_selected)

        # Enter opens the selected thumbnail in the viewer.
        fkey = Gtk.EventControllerKey()
        fkey.connect("key-pressed", self._on_grid_key)
        self.flow.add_controller(fkey)

        self.detail_placeholder = Adw.StatusPage()
        self.detail_placeholder.set_icon_name("emblem-photos-symbolic")
        self.detail_placeholder.set_title("No photos to show")
        self.detail_placeholder.set_description(
            "Select a folder on the left to see the photos inside it."
        )

        self.detail_stack = Gtk.Stack()
        self.detail_stack.set_hexpand(True)
        self.detail_stack.set_vexpand(True)
        self.detail_scroller.set_child(self.flow)
        # Don't let the FlowBox's small natural width shrink the pane; let it
        # fill whatever width the paned gives it (so columns aren't capped).
        self.detail_scroller.set_propagate_natural_width(False)
        self.detail_stack.add_named(self.detail_scroller, "grid")
        self.detail_stack.add_named(self.detail_placeholder, "empty")
        self.detail_stack.set_visible_child_name("empty")
        return self.detail_stack

    # ---------------------------------------------------------- full view
    def _build_full_page(self) -> Gtk.Widget:
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        self.full_back_btn = Gtk.Button()
        back_content = Adw.ButtonContent()
        back_content.set_icon_name("go-previous-symbolic")
        back_content.set_label("Back to Library")
        self.full_back_btn.set_child(back_content)
        self.full_back_btn.set_tooltip_text("Back to Library")
        self.full_back_btn.set_action_name("win.back")
        # Keep it focusable for keyboard users, but don't let it grab focus the
        # moment the viewer opens (see _on_thumb_activated, which moves focus to
        # the picture instead).
        self.full_back_btn.set_focus_on_click(False)
        header.pack_start(self.full_back_btn)

        # Menu button, also present in the viewer.
        full_menu_btn = Gtk.MenuButton()
        full_menu_btn.set_icon_name("open-menu-symbolic")
        full_menu_btn.set_menu_model(self._primary_menu())
        header.pack_end(full_menu_btn)

        # Info button, left of the menu.
        full_info_btn = Gtk.Button()
        full_info_btn.set_icon_name("dialog-information-symbolic")
        full_info_btn.set_tooltip_text("Photo information")
        full_info_btn.set_action_name("win.photo-info")
        header.pack_end(full_info_btn)

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
        self.full_picture.set_focusable(True)

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
        /* Shadow + selection border live on the picture itself, which is now
           sized to hug the real image (see _make_thumb), so they sit on the
           photo's true edges rather than a letterboxed cell. */
        picture.tadaima-thumb {{
            box-shadow: 0 2px 8px rgba(0,0,0,0.5);
            border-radius: 3px;
            background: transparent;
        }}
        flowboxchild.tadaima-cell {{
            padding: 0;
            background: transparent;
            box-shadow: none;
        }}
        flowboxchild.tadaima-cell:selected {{
            background: transparent;
            box-shadow: none;
            outline: none;
        }}
        flowboxchild.tadaima-cell:selected picture.tadaima-thumb {{
            /* A box-shadow ring renders reliably on a Picture where CSS
               `outline` often does not. Keeps the drop shadow and adds an
               accent-coloured selection ring. */
            box-shadow: 0 0 0 3px @accent_color, 0 2px 8px rgba(0,0,0,0.5);
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
        .tadaima-info-panel {{
            border-left: 1px solid alpha(currentColor, 0.15);
            background: alpha(currentColor, 0.03);
        }}
        .tadaima-info-head {{
            padding: 8px 8px 8px 12px;
            border-bottom: 1px solid alpha(currentColor, 0.12);
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

        # Re-apply the remembered expansion and selection after the model
        # materialises. Rebuilding the store (here, and on every live refresh
        # during a scan) collapses everything, so this restore must run each
        # time — self._expanded is the single source of truth.
        GLib.idle_add(self._reapply_sidebar_state)

    def _reapply_sidebar_state(self) -> bool:
        """Expand remembered folders and re-select the remembered folder after
        the tree store has been (re)built."""
        if self._closing:
            return False
        self._restoring_state = True
        try:
            if self._expanded:
                self._restore_expanded(set(self._expanded))
            target = (
                self.selected_folder.path
                if self.selected_folder
                else self._pending_selected
            )
            if target:
                res = self._find_tree_row(target)
                row = res[0] if res else None
                if row is not None:
                    self._selection.set_selected(res[1])
                    node = row.get_item().node
                    self.selected_folder = node
        finally:
            self._restoring_state = False
        return False

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

    def _snapshot_expanded(self) -> set[str]:
        """Absolute paths of every currently-expanded row in the tree."""
        expanded: set[str] = set()
        n = self._tree_model.get_n_items()
        for i in range(n):
            row = self._tree_model.get_item(i)
            if row is not None and row.get_expanded():
                item = row.get_item()
                expanded.add(os.path.normpath(item.node.path))
        return expanded

    def _restore_session_state(self) -> bool:
        """Re-apply the previous session's expanded folders and selection, and
        load the selected folder's photos, at startup."""
        if not self.config.scanned_folders:
            return False
        self._reapply_sidebar_state()
        if self.selected_folder is not None:
            self._show_folder_photos(self.selected_folder)
        return False

    def _restore_expanded(self, paths: set[str]) -> bool:
        """Expand rows for *paths*, top-down so parents materialise children
        before we reach them. Repeats until no further expansion is possible
        (each pass can reveal a new, deeper level)."""
        changed = True
        guard = 0
        while changed and guard < 64:
            changed = False
            guard += 1
            n = self._tree_model.get_n_items()
            for i in range(n):
                row = self._tree_model.get_item(i)
                if row is None:
                    continue
                item = row.get_item()
                p = os.path.normpath(item.node.path)
                if p in paths and row.is_expandable() and not row.get_expanded():
                    row.set_expanded(True)
                    changed = True
        return False

    def _on_sidebar_key(self, controller, keyval, keycode, state) -> bool:
        """Right arrow expands the selected folder, Left collapses it."""
        row = self._selection.get_selected_item()  # Gtk.TreeListRow or None
        if row is None:
            return False
        if keyval == Gdk.KEY_Right:
            if row.is_expandable() and not row.get_expanded():
                row.set_expanded(True)
                return True
            return False
        if keyval == Gdk.KEY_Left:
            if row.get_expanded():
                row.set_expanded(False)
                return True
            return False
        return False

    # ============================================= sidebar interactions
    def _on_tree_selected(self, selection, _param) -> None:
        row = selection.get_selected_item()   # Gtk.TreeListRow or None
        if row is None:
            return
        item: FolderItem = row.get_item()
        node = item.node
        if item.style == "ancestor":
            # Clicking a flattened ancestor (incl. "/") re-focuses there while
            # keeping every currently-visible folder visible in its current
            # open/closed state — the whole hierarchy just shifts right.
            print(f"[tadaima] re-focus via ancestor click: {node.path}", flush=True)
            self.set_focus_folder(node.path, preserve_expansion=True)
            return
        self.selected_folder = node
        self._show_folder_photos(node)
        if not self._restoring_state:
            self._persist_sidebar_state()

    def _on_row_right_click(self, gesture, n_press, x, y, list_item) -> None:
        row = list_item.get_item()
        if row is None:
            print("[tadaima] right-click: no row bound to list_item", flush=True)
            return
        item = row.get_item()
        if item is None:
            print("[tadaima] right-click: no item on row", flush=True)
            return
        node = item.node
        print(f"[tadaima] right-click on folder: {node.path}", flush=True)

        # Plain popover with real buttons that call methods directly — no
        # Gio.Action indirection (that indirection was the earlier focus bug).
        popover = Gtk.Popover()
        popover.set_has_arrow(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)
        box.set_margin_end(4)

        focus_btn = Gtk.Button(label="Focus on folder")
        focus_btn.add_css_class("flat")
        focus_btn.set_halign(Gtk.Align.FILL)
        focus_btn.set_has_frame(False)
        focus_child = focus_btn.get_child()
        if isinstance(focus_child, Gtk.Label):
            focus_child.set_xalign(0.0)

        locate_btn = Gtk.Button(label="Locate on disk")
        locate_btn.add_css_class("flat")
        locate_btn.set_has_frame(False)
        locate_child = locate_btn.get_child()
        if isinstance(locate_child, Gtk.Label):
            locate_child.set_xalign(0.0)

        target = node.path

        def on_focus_clicked(_b):
            print(f"[tadaima] Focus on folder clicked: {target}", flush=True)
            popover.popdown()
            self.set_focus_folder(target)

        def on_locate_clicked(_b):
            print(f"[tadaima] Locate on disk clicked: {target}", flush=True)
            popover.popdown()
            reveal_in_file_manager(target)

        focus_btn.connect("clicked", on_focus_clicked)
        locate_btn.connect("clicked", on_locate_clicked)
        box.append(focus_btn)
        box.append(locate_btn)
        popover.set_child(box)

        popover.set_parent(gesture.get_widget())
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: p.unparent())
        popover.popup()

    def set_focus_folder(self, path: str, preserve_expansion: bool = False) -> None:
        norm = os.path.normpath(path)
        print(
            f"[tadaima] set_focus_folder -> {norm} "
            f"(preserve_expansion={preserve_expansion})",
            flush=True,
        )

        # Snapshot what's expanded now, plus the chain from the new focus down to
        # the old focus, so the same folders stay visible after the shift.
        keep_expanded: set[str] = set()
        if preserve_expansion:
            keep_expanded = self._snapshot_expanded()
            old_focus = self.focus_path
            if self.tree_root is not None:
                for anc in ancestor_chain(self.tree_root, old_focus):
                    if _within(norm, anc.path):
                        keep_expanded.add(os.path.normpath(anc.path))
            keep_expanded.add(os.path.normpath(old_focus))

        self.focus_path = norm
        self._populate_sidebar()

        if preserve_expansion and keep_expanded:
            # Also expand the new focus so the chain is reachable.
            keep_expanded.add(norm)
            GLib.idle_add(self._restore_expanded, keep_expanded)
        else:
            # Drill-down focus: just open the focused node so it's obvious.
            GLib.idle_add(self._expand_focused_row)

        self._set_status(
            f"Focused on {os.path.basename(norm) or '/'}", transient=True
        )
        if not self._restoring_state:
            self._persist_sidebar_state()

    def _expand_focused_row(self) -> bool:
        result = self._find_tree_row(self.focus_path)
        row = result[0] if result else None
        if row is not None and row.is_expandable():
            row.set_expanded(True)
            print(f"[tadaima] expanded focused row: {self.focus_path}", flush=True)
        return False

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

        slot_w = self._thumb_zoom
        # Cap BOTH dimensions at the slot size: a tall image scales down to fit
        # (becoming narrow) rather than producing a super-tall row.
        max_h = slot_w

        # Source, in order of preference — always favour the CACHED thumbnail
        # derivative over the full-resolution original. The square derivative is
        # deliberately NOT used here: it is a centre-crop, and grid thumbnails
        # must preserve aspect. The original is only a transient fallback used
        # before the background scan has generated the thumbnail.
        src = None
        if rec.thumb_path and os.path.exists(rec.thumb_path):
            src = rec.thumb_path
        elif os.path.exists(rec.path):
            src = rec.path

        disp_w, disp_h = slot_w, slot_w
        paintable = None
        if src:
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file(src)
                w, h = pb.get_width(), pb.get_height()
                if w > 0 and h > 0:
                    scale = min(slot_w / w, max_h / h, 1.0)
                    disp_w = max(1, int(round(w * scale)))
                    disp_h = max(1, int(round(h * scale)))
                    pb = pb.scale_simple(
                        disp_w, disp_h, GdkPixbuf.InterpType.BILINEAR
                    )
                    paintable = Gdk.Texture.new_for_pixbuf(pb)
            except Exception:
                paintable = None

        pic = Gtk.Picture()
        pic.set_can_shrink(False)
        pic.set_content_fit(Gtk.ContentFit.FILL)
        pic.set_halign(Gtk.Align.CENTER)
        pic.set_valign(Gtk.Align.CENTER)
        pic.set_hexpand(False)
        pic.set_vexpand(False)
        pic.add_css_class("tadaima-thumb")
        if paintable is not None:
            pic.set_paintable(paintable)
        pic.set_size_request(disp_w, disp_h)
        pic.set_tooltip_text(rec.name)
        pic.set_cursor(Gdk.Cursor.new_from_name("pointer"))

        # Slot: exactly slot_w wide (uniform → predictable column count), tall
        # enough for this thumbnail, thumbnail centred within it.
        slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        slot.set_size_request(slot_w, disp_h)
        slot.set_halign(Gtk.Align.CENTER)
        slot.set_valign(Gtk.Align.CENTER)
        slot.set_hexpand(False)
        slot.set_vexpand(False)
        slot.append(pic)

        fbchild.set_child(slot)
        fbchild.set_halign(Gtk.Align.CENTER)
        fbchild.set_valign(Gtk.Align.START)
        fbchild.set_hexpand(False)
        fbchild.set_vexpand(False)
        return fbchild

    def _metadata_line(self, rec) -> str:
        """One-line summary: name · date taken · dimensions · size."""
        md = read_metadata(rec.path)
        return (
            f"{rec.name}    ·    {md.date_str}    ·    "
            f"{md.dimensions_str}    ·    {human_size(md.size_bytes)}"
        )

    def _on_thumb_selected(self, flow) -> None:
        sel = flow.get_selected_children()
        if sel:
            rec = getattr(sel[0], "_rec", None)
            self.selected_image_path = getattr(sel[0], "_image_path", None)
            self.gallery_caption.set_text(self._metadata_line(rec) if rec else "")
            self._selected_rec = rec
        else:
            self.selected_image_path = None
            self.gallery_caption.set_text("")
            self._selected_rec = None
        self._refresh_actions()
        if (
            getattr(self, "info_revealer", None) is not None
            and self.info_revealer.get_reveal_child()
        ):
            self._update_info_panel()

    def _on_grid_key(self, controller, keyval, keycode, state) -> bool:
        """Enter opens the currently-selected thumbnail in the viewer."""
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            sel = self.flow.get_selected_children()
            if sel:
                self._on_thumb_activated(self.flow, sel[0])
                return True
        return False

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
        # Move focus onto the picture so the Back button isn't auto-focused, and
        # so the ←/→ key controller on the full page receives keystrokes.
        GLib.idle_add(self.full_picture.grab_focus)
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
            # Filmstrip uses the square derivative (every image now has one).
            src = None
            if rec.square_path and os.path.exists(rec.square_path):
                src = rec.square_path
            elif rec.thumb_path and os.path.exists(rec.thumb_path):
                src = rec.thumb_path
            else:
                src = rec.path
            pic = Gtk.Picture()
            pic.set_content_fit(Gtk.ContentFit.COVER)
            pic.set_size_request(FILMSTRIP_H, FILMSTRIP_H)
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
            f"{self._metadata_line(rec)}    ·    "
            f"{idx + 1} of {len(self._full_images)}"
        )
        self.selected_image_path = rec.path
        self._selected_rec = rec

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

    # ============================================================ zoom
    def on_zoom_in(self) -> None:
        self._set_zoom(self._thumb_zoom + 30)

    def on_zoom_out(self) -> None:
        self._set_zoom(self._thumb_zoom - 30)

    def on_zoom_reset(self) -> None:
        self._set_zoom(SLOT_WIDTH)  # default slot width

    def _set_zoom(self, value: int) -> None:
        value = max(80, min(420, int(value)))
        if value == self._thumb_zoom:
            return
        self._thumb_zoom = value
        self.config.thumb_zoom = value
        # Re-render the currently shown folder at the new cell size.
        if self.selected_folder is not None:
            self._show_folder_photos(self.selected_folder)
        self._set_status(f"Thumbnail size: {value}px", transient=True)

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
        """Add a folder to the *pending* list in the Scanned folders window.

        Nothing is committed or scanned until the user presses Save."""
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
            if not path:
                return
            norm = os.path.normpath(path)
            if norm not in self._pending_folders:
                self._pending_folders.append(norm)
                self._mark_folders_dirty()
                self._refresh_scanned_folders_window()

        dialog.select_folder(self, None, done)

    def on_manage_folders(self) -> None:
        if self._scanned_folders_win is not None:
            self._scanned_folders_win.present()
            return

        # Working copy of the committed list; edits stay here until Save.
        self._pending_folders = list(self.config.scanned_folders)
        self._folders_dirty = False

        win = Adw.Window()
        win.set_modal(True)
        win.set_transient_for(self)
        win.set_default_size(600, 460)
        win.set_title("Scanned folders")
        self._scanned_folders_win = win

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)

        add_btn = Gtk.Button(label="Add…")
        add_btn.connect("clicked", lambda b: self.on_add_folder())
        header.pack_start(add_btn)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: win.close())
        header.pack_end(cancel_btn)

        self._folders_save_btn = Gtk.Button(label="Save")
        self._folders_save_btn.add_css_class("suggested-action")
        self._folders_save_btn.set_sensitive(False)
        self._folders_save_btn.connect("clicked", lambda b: self._save_scanned_folders())
        header.pack_end(self._folders_save_btn)
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

        # Esc closes (discards) the window.
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

    def _mark_folders_dirty(self) -> None:
        self._folders_dirty = True
        if getattr(self, "_folders_save_btn", None) is not None:
            self._folders_save_btn.set_sensitive(True)

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
        group.set_description(
            "These folders are only ever read, never modified. "
            "Changes are applied when you press Save. Adding a folder that "
            "contains one already listed absorbs the child."
        )

        # Preview absorption so the user sees what Save will keep.
        preview = normalize_scanned_folders(self._pending_folders)
        absorbed = [p for p in self._pending_folders if p not in preview]

        if not self._pending_folders:
            row = Adw.ActionRow()
            row.set_title("No folders added yet")
            group.add(row)
        for path in self._pending_folders:
            row = Adw.ActionRow()
            row.set_title(os.path.basename(path) or path)
            if path in absorbed:
                row.set_subtitle(f"{path}\nWill be absorbed by a parent folder on Save")
                row.add_css_class("dim-label")
            else:
                row.set_subtitle(path)
            remove = Gtk.Button()
            remove.set_icon_name("user-trash-symbolic")
            remove.set_valign(Gtk.Align.CENTER)
            remove.add_css_class("flat")
            remove.connect("clicked", lambda b, p=path: self._remove_pending_folder(p))
            row.add_suffix(remove)
            group.add(row)
        holder.append(group)

    def _remove_pending_folder(self, path: str) -> None:
        if path in self._pending_folders:
            self._pending_folders.remove(path)
            self._mark_folders_dirty()
            self._refresh_scanned_folders_window()

    def _save_scanned_folders(self) -> None:
        """Commit the pending list (with absorption) and scan only genuinely
        new coverage. Existing derivatives are reused automatically because they
        are keyed by each image's absolute path."""
        before = set(self.config.scanned_folders)
        self.config.set_scanned_folders(self._pending_folders)
        after = set(self.config.scanned_folders)
        print(f"[tadaima] scanned folders saved: {sorted(after)}", flush=True)

        self._rebuild_tree()
        if self._scanned_folders_win is not None:
            self._scanned_folders_win.close()

        # Only scan if coverage actually expanded (a new root not within the old
        # set). Pure removals/absorption need no scan.
        new_coverage = [p for p in after if p not in before]
        if new_coverage:
            self._start_background_scan()

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

    def on_photo_info(self) -> None:
        # The info sidebar lives in the gallery page; if invoked from the
        # viewer, return to the gallery first so the panel is visible.
        if self.stack.get_visible_child_name() == "full":
            self.on_back()
        visible = not self.info_revealer.get_reveal_child()
        self._set_info_visible(visible)

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

            if force:
                work = list(self.index.records.values())
            else:
                # Only (re)generate derivatives that are actually missing —
                # never regenerate the whole library on a normal launch.
                work = [
                    rec
                    for rec in self.index.records.values()
                    if not has_all_derivatives(rec)
                ]
            total = len(work)
            last_status = 0.0
            last_refresh = time.monotonic()
            for i, rec in enumerate(work, start=1):
                now = time.monotonic()
                if now - last_status > 0.03 or i == total:
                    self._post_status(f"Processing {i} of {total}: {rec.name}")
                    last_status = now
                # Every image gets all derivatives (incl. a square for the
                # sidebar icon and the filmstrip).
                generate_derivatives(rec, want_square=True)
                if now - last_refresh > LIVE_REFRESH_SECONDS:
                    # Grid-only refresh: do NOT rebuild the sidebar tree, or the
                    # user's expanded subfolders would collapse mid-scan.
                    GLib.idle_add(self._live_refresh, False)
                    last_refresh = now

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

    def _live_refresh(self, rebuild_tree: bool = True) -> bool:
        """Refresh the UI from the current index during a running scan.

        When *rebuild_tree* is True (structure may have changed, e.g. right
        after the folder walk) the sidebar tree is rebuilt. Otherwise only the
        open folder's thumbnail grid is refreshed — rebuilding the tree store
        mid-scan would collapse whatever subfolders the user has expanded (and
        made it impossible to dig into folders while derivatives generate)."""
        if self._closing:
            return False
        prev = self.selected_folder.path if self.selected_folder else None
        if rebuild_tree:
            self.tree_root = build_tree(
                list(self.index.records.values()), self.config.scanned_folders
            )
            self._populate_sidebar()
        else:
            # Keep the tree_root's data current so counts/icons are fresh, but
            # do NOT touch the tree store (that would reset expansion).
            self.tree_root = build_tree(
                list(self.index.records.values()), self.config.scanned_folders
            )
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
        # Grid-only refresh so the user's expanded folders stay expanded.
        self._live_refresh(rebuild_tree=False)
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
        set_enabled(self, "photo-info", getattr(self, "_selected_rec", None) is not None)

    def do_close_request(self) -> bool:
        w = self.get_width()
        h = self.get_height()
        if w > 0 and h > 0:
            self.config.set("window", {"width": w, "height": h})
        self._persist_sidebar_state()
        self._closing = True
        # Explicitly quit so the process exits even if a stray GSource or the
        # daemon scan thread is still around; returning False also lets the
        # window destroy normally.
        app = self.get_application()
        if app is not None:
            app.quit()
        return False

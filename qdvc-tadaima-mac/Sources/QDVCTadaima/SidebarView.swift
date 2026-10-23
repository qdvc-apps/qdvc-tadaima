import AppKit
import SwiftUI
import TadaimaCore

/// The folder sidebar: the scanned folders' hierarchy, with photo counts and
/// a crop of each folder's first photo as its icon. It follows the stream as
/// you scroll (the current folder, or its nearest visible parent, is
/// highlighted) and choosing a folder scrolls the stream to it.
///
/// The rows are a flat list from `Sidebar.rows` (indented, with disclosure
/// chevrons) rather than an OutlineGroup, so the app knows exactly which
/// folders are visible: that is what the scrollspy needs.
struct SidebarView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let rows = model.sidebarRows
        let enclosing = rows.filter { $0.kind == .enclosing }
        let tree = rows.filter { $0.kind != .enclosing }
        ScrollViewReader { proxy in
            List(selection: Binding(get: { model.highlightedRowID },
                                    set: { model.userSelectedRow($0) })) {
                if !enclosing.isEmpty {
                    Section("Enclosing Folders") {
                        ForEach(enclosing) { row in
                            SidebarRowView(row: row).tag(row.id)
                        }
                    }
                }
                if !model.scannedFolders.isEmpty {
                    Section(model.isFocused ? "Focus" : "Folders") {
                        ForEach(tree) { row in
                            SidebarRowView(row: row).tag(row.id)
                        }
                    }
                }
            }
            .listStyle(.sidebar)
            .onChange(of: model.highlightedRowID) { _, id in
                guard let id else { return }
                proxy.scrollTo(id)  // as little as needed to show it
            }
            .onKeyPress(.rightArrow) { expandHighlighted(true) }
            .onKeyPress(.leftArrow) { expandHighlighted(false) }
        }
        .overlay {
            if model.scannedFolders.isEmpty {
                ContentUnavailableView("No Folders", systemImage: "folder",
                                       description: Text("Add a folder of photos to start."))
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            SidebarFooter()
        }
    }

    /// → opens the highlighted folder, ← closes it (as in Finder's list view).
    private func expandHighlighted(_ open: Bool) -> KeyPress.Result {
        guard let id = model.highlightedRowID,
              let row = model.sidebarRows.first(where: { $0.id == id }),
              row.kind != .enclosing, row.hasChildren, row.isExpanded != open else { return .ignored }
        model.setExpanded(row.path, open)
        return .handled
    }
}

struct SidebarRowView: View {
    @Environment(AppModel.self) private var model
    let row: SidebarRow

    var body: some View {
        HStack(spacing: 5) {
            if row.kind != .enclosing {
                Spacer().frame(width: CGFloat(row.depth) * 12)
                if row.hasChildren {
                    Button { model.toggleExpanded(row.path) } label: {
                        Image(systemName: "chevron.right")
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(.secondary)
                            .rotationEffect(.degrees(row.isExpanded ? 90 : 0))
                            .frame(width: 12, height: 16)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .help(row.isExpanded ? "Collapse" : "Expand")
                } else {
                    Spacer().frame(width: 12)
                }
            }
            FolderIcon(record: row.icon)
            Text(row.name)
                .lineLimit(1)
                .truncationMode(.middle)
                .italic(row.kind == .enclosing)
                .fontWeight(row.kind == .focused ? .semibold : .regular)
                .foregroundStyle(row.kind == .enclosing ? .secondary : .primary)
            Spacer(minLength: 4)
            Text(Formats.count(row.count))
                .font(.callout)
                .monospacedDigit()
                .foregroundStyle(.secondary)
        }
        .help(row.path)
        // A click also on the row that is already highlighted goes there (the
        // scrollspy highlights rows too, so the selection may not change).
        .simultaneousGesture(TapGesture().onEnded {
            if row.kind != .enclosing && model.highlightedRowID == row.id { model.goTo(folder: row.path) }
        })
        .contextMenu {
            if row.kind == .enclosing {
                Button("Focus Here") { model.setFocus(row.path, keepCurrentFolder: true) }
            } else {
                Button("Focus on Folder") { model.setFocus(row.path) }
                    .disabled(row.kind == .focused)
            }
            Button("Show in Finder") { model.revealFolder(row.path) }
            if model.isScannedFolder(row.path) {
                Divider()
                Button("Stop Scanning This Folder\u{2026}") { model.confirmRemoval = row.path }
            }
        }
    }
}

/// A folder's icon: a square crop of its first photo, or a folder symbol.
struct FolderIcon: View {
    let record: ImageRecord?
    @State private var image: CGImage?

    var body: some View {
        Group {
            if let image {
                Image(decorative: image, scale: 2)
                    .resizable()
                    .interpolation(.medium)
                    .clipShape(RoundedRectangle(cornerRadius: 3))
            } else {
                Image(systemName: "folder")
                    .foregroundStyle(Color.accentColor)
            }
        }
        .frame(width: 18, height: 18)
        .task(id: record?.versionKey) {
            guard let record else {
                image = nil
                return
            }
            image = await ImageCache.shared.square(record)
        }
    }
}

/// Add a folder, and the indexing status.
struct SidebarFooter: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        HStack(spacing: 8) {
            Button { model.chooseFoldersToAdd() } label: {
                Image(systemName: "plus")
                    .frame(width: 20, height: 20)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.borderless)
            .help("Add a folder of photos (\u{2318}O)")
            Spacer()
            if model.isScanning {
                ProgressView().controlSize(.small)
                    .help(model.scanStatus)
            } else if model.photoCount > 0 {
                Text("\(Formats.count(model.photoCount)) photos")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(.bar)
    }
}

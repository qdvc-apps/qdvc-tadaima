import AppKit
import SwiftUI
import TadaimaCore

/// The photo stream: every photo under the focused folder in one scrolling
/// grid, a heading per folder (its name, and dimmed beside it where it is).
///
/// Rows come from the model (`AppModel.streamRows`: a heading, then rows of as
/// many photos as fit), in a lazy stack, so only what is near the visible
/// area is built and a library of tens of thousands of photos scrolls as
/// easily as a few. The top row, read with `scrollPosition(id:)`, drives the
/// sidebar's scrollspy; jumps go through `ScrollViewReader`.
struct StreamView: View {
    @Environment(AppModel.self) private var model
    @State private var topRowID: String?
    @State private var viewSize: CGSize = .zero
    @State private var lastToken = 0
    @FocusState private var focused: Bool

    var body: some View {
        GeometryReader { geo in
            let columns = StreamLayout.columns(width: Int(geo.size.width), slot: model.thumbnailSize)
            let rows = model.streamRows(columns: columns)
            ScrollViewReader { proxy in
                ScrollView(.vertical) {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(rows) { row in
                            StreamRowView(row: row, isFirst: row.id == rows.first?.id, focused: $focused)
                                .id(row.id)
                        }
                    }
                    .scrollTargetLayout()
                    .padding(CGFloat(StreamMetrics.margin))
                }
                .scrollPosition(id: $topRowID, anchor: .top)
                .onChange(of: topRowID) { _, id in model.streamTopRowChanged(id) }
                .onChange(of: model.scrollRequest) { _, request in
                    perform(request, proxy: proxy, columns: columns, rows: rows)
                }
                .onChange(of: columns) { _, newColumns in
                    keepPlace(proxy: proxy, columns: newColumns)
                }
                .onAppear {
                    // A request made before the stream was on screen (the
                    // folder restored at launch). Let the rows lay out first.
                    DispatchQueue.main.async {
                        perform(model.scrollRequest, proxy: proxy, columns: columns, rows: rows)
                    }
                }
            }
            .onChange(of: geo.size, initial: true) { _, size in viewSize = size }
        }
        .focusable()
        .focused($focused)
        .focusEffectDisabled()
        .onKeyPress(keys: [.upArrow, .downArrow, .leftArrow, .rightArrow, .home, .end, .pageUp, .pageDown,
                           .return, .space], phases: [.down, .repeat]) { press in
            handle(press)
        }
        .onAppear { focused = true }
        // Back from the viewer: the arrow keys work in the grid again.
        .onChange(of: model.viewer == nil) { _, closed in
            if closed { focused = true }
        }
    }

    // MARK: scrolling

    private func perform(_ request: ScrollRequest?, proxy: ScrollViewProxy, columns: Int, rows: [StreamRow]) {
        guard let request, request.token != lastToken else { return }
        lastToken = request.token
        switch request.target {
        case .top:
            if let first = rows.first { proxy.scrollTo(first.id, anchor: .top) }
        case .folder(let folder):
            proxy.scrollTo(StreamRow.headerID(folder), anchor: .top)
            model.didScrollToFolder()
        case .photo(let photo):
            if let id = model.rowID(forPhoto: photo, columns: columns) {
                proxy.scrollTo(id)  // as little as needed to show it
            }
        }
    }

    /// After the column count changes (the window or the thumbnails were
    /// resized), put the photo that was at the top back at the top.
    private func keepPlace(proxy: ScrollViewProxy, columns: Int) {
        guard let top = topRowID else { return }
        let target: String?
        if top.hasPrefix("h|") {
            target = top  // headings keep their ids
        } else {
            target = model.anchorPhoto(forRow: top).flatMap { model.rowID(forPhoto: $0, columns: columns) }
        }
        guard let target else { return }
        DispatchQueue.main.async { proxy.scrollTo(target, anchor: .top) }
    }

    // MARK: keyboard

    private func handle(_ press: KeyPress) -> KeyPress.Result {
        if press.modifiers.contains(.command) || press.modifiers.contains(.option) { return .ignored }
        let stream = model.stream
        guard !stream.isEmpty else { return .ignored }
        let move: StreamMove
        switch press.key {
        case .return:
            model.openSelected()
            return .handled
        case .space:
            model.quickLook()
            return .handled
        case .leftArrow: move = .left
        case .rightArrow: move = .right
        case .upArrow: move = .up
        case .downArrow: move = .down
        case .home: move = .home
        case .end: move = .end
        case .pageUp: move = .pageUp
        case .pageDown: move = .pageDown
        default: return .ignored
        }
        let layout = StreamLayout(stream: stream, width: Int(viewSize.width), slot: model.thumbnailSize)
        let target: Int
        if let path = model.selectedPhoto, let flat = stream.indexOf(photo: path) {
            let pageRows = max(1, Int(viewSize.height) / layout.pitch - 1)
            target = layout.neighbour(flat, move, pageRows: pageRows)
        } else {
            // Nothing selected yet: start with the first photo in view.
            target = topRowID.flatMap { model.anchorPhoto(forRow: $0) }.flatMap { stream.indexOf(photo: $0) } ?? 0
        }
        let path = stream.records[target].path
        model.select(path)
        model.requestScroll(.photo(path))
        return .handled
    }
}

/// One row: a folder's heading, or a row of its photos.
struct StreamRowView: View {
    @Environment(AppModel.self) private var model
    let row: StreamRow
    let isFirst: Bool
    var focused: FocusState<Bool>.Binding

    var body: some View {
        let stream = model.stream
        let slot = model.thumbnailSize
        if row.section < stream.sections.count {
            let section = stream.sections[row.section]
            switch row.kind {
            case .header:
                SectionHeading(section: section)
                    .padding(.top, isFirst ? 0 : CGFloat(StreamMetrics.sectionGap))
            case .photos(_, let range):
                HStack(spacing: CGFloat(StreamMetrics.spacing)) {
                    ForEach(range, id: \.self) { i in
                        let rec = section.records[i]
                        ThumbnailCell(record: rec, slot: slot, selected: model.selectedPhoto == rec.path)
                            .simultaneousGesture(TapGesture().onEnded {
                                model.select(rec.path)
                                focused.wrappedValue = true
                            })
                            .onTapGesture(count: 2) { model.open(rec) }
                            .contextMenu { PhotoMenu(record: rec) }
                    }
                }
                .frame(height: CGFloat(slot + StreamMetrics.spacing), alignment: .top)
            }
        }
    }
}

/// A folder's heading: its name, where it is (dimmed) and its photo count.
struct SectionHeading: View {
    @Environment(AppModel.self) private var model
    let section: StreamSection

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(section.name)
                .font(.title3.weight(.semibold))
                .lineLimit(1)
            if !section.context.isEmpty {
                Text(section.context)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer(minLength: 8)
            Text(section.records.count == 1 ? "1 photo" : "\(Formats.count(section.records.count)) photos")
                .font(.callout)
                .foregroundStyle(.tertiary)
        }
        .padding(.bottom, 8)
        .frame(height: CGFloat(StreamMetrics.headerHeight), alignment: .bottom)
        .help(section.path)
        .contextMenu {
            Button("Focus on Folder") { model.setFocus(section.path) }
            Button("Show in Finder") { model.revealFolder(section.path) }
        }
    }
}

/// A photo in the grid: the thumbnail at its own proportions, centred in a
/// square slot, with a soft shadow and the selection ring on its real edges.
/// Until it has loaded, a faint tile of the same size holds its place.
struct ThumbnailCell: View {
    let record: ImageRecord
    let slot: Int
    let selected: Bool
    @Environment(\.displayScale) private var displayScale
    @State private var image: CGImage?

    var body: some View {
        let size = Formats.fitWithin(width: record.thumbWidth, height: record.thumbHeight, box: slot)
        let shape = RoundedRectangle(cornerRadius: 3)
        ZStack {
            if let image {
                Image(decorative: image, scale: displayScale)
                    .resizable()
                    .interpolation(.high)
            } else {
                shape.fill(.quaternary)
            }
        }
        .frame(width: CGFloat(size.width), height: CGFloat(size.height))
        .clipShape(shape)
        .shadow(color: .black.opacity(image == nil ? 0 : 0.35), radius: 3, y: 1.5)
        .overlay {
            if selected {
                RoundedRectangle(cornerRadius: 5)
                    .strokeBorder(Color.accentColor, lineWidth: 3)
                    .padding(-4)
            }
        }
        .frame(width: CGFloat(slot), height: CGFloat(slot))
        .contentShape(Rectangle())
        .help(record.name)
        .onDrag { NSItemProvider(object: URL(fileURLWithPath: record.path) as NSURL) }
        .task(id: "\(record.versionKey)|\(slot)|\(displayScale)") {
            let pixels = Int((Double(max(size.width, size.height)) * displayScale).rounded(.up))
            image = await ImageCache.shared.thumbnail(record, pixels: pixels)
        }
    }
}

/// The photo commands, for context menus.
struct PhotoMenu: View {
    @Environment(AppModel.self) private var model
    let record: ImageRecord

    var body: some View {
        Button("Open") { model.open(record) }
        Button("Quick Look") {
            model.select(record.path)
            model.quickLook()
        }
        Divider()
        Button("Show in Finder") { Platform.reveal([URL(fileURLWithPath: record.path)]) }
        Button("Open with Default App") { NSWorkspace.shared.open(URL(fileURLWithPath: record.path)) }
        Button("Copy Photo") { Platform.copyFiles([URL(fileURLWithPath: record.path)]) }
        Divider()
        Button("Get Info") {
            model.select(record.path)
            model.showInspector = true
        }
    }
}

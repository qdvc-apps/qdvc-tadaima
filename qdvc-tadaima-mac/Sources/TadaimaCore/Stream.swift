import Foundation

/// The photo stream: every photo under the focused folder as one continuous,
/// sectioned grid, a heading per folder with photos of its own. A port of the
/// GTK edition's `qdvc/stream.py`; `StreamLayout`'s geometry is shared too,
/// and the Mac grid uses its columns, rows and keyboard moves.

/// Geometry defaults (points).
public enum StreamMetrics {
    public static let margin = 12        // around the whole stream
    public static let spacing = 12       // between slots, both ways
    public static let headerHeight = 44  // a section heading
    public static let sectionGap = 16    // above every heading but the first
}

/// One folder's own photos in the stream.
public struct StreamSection: Identifiable, Hashable, Sendable {
    public let path: String
    public let name: String
    /// Dimmed text beside the name: the parent path from the scanned folder
    /// ("holiday-photos-copenhagen / nyhavn"), or, for a scanned folder, the
    /// directory it is in ("~/Desktop"). Empty for none.
    public let context: String
    public let records: [ImageRecord]
    public var id: String { path }
}

/// The sections, plus flat indexing: photo *i* of the whole stream.
public struct PhotoStream: Sendable {
    public let sections: [StreamSection]
    /// Flat index of each section's first photo.
    public let starts: [Int]
    public let records: [ImageRecord]
    private let flatOfPath: [String: Int]
    private let sectionOfPath: [String: Int]

    public init(sections: [StreamSection]) {
        self.sections = sections
        var starts: [Int] = []
        var records: [ImageRecord] = []
        var flat: [String: Int] = [:]
        var bySection: [String: Int] = [:]
        for (i, section) in sections.enumerated() {
            starts.append(records.count)
            bySection[Paths.normpath(section.path)] = i
            for rec in section.records {
                flat[rec.path] = records.count
                records.append(rec)
            }
        }
        self.starts = starts
        self.records = records
        self.flatOfPath = flat
        self.sectionOfPath = bySection
    }

    public static let empty = PhotoStream(sections: [])

    public var count: Int { records.count }
    public var isEmpty: Bool { records.isEmpty }

    /// Sections for every folder at or below *focus* with photos of its own,
    /// in the sidebar's order (pre-order: a folder's photos, then its
    /// subfolders' by name).
    public static func build(focus: FolderNode, home: String? = NSHomeDirectory()) -> PhotoStream {
        var sections: [StreamSection] = []
        func visit(_ node: FolderNode, _ chain: [String]?) {
            let context: String
            let below: [String]?
            if node.isScannedRoot || chain == nil {
                let parent = Paths.dirname(node.path)
                context = parent == node.path ? "" : Paths.tilde(parent, home: home)
                below = node.isScannedRoot ? [node.name] : nil
            } else {
                context = chain!.joined(separator: " / ")
                below = chain! + [node.name]
            }
            if !node.ownImages.isEmpty {
                sections.append(StreamSection(path: node.path, name: node.name,
                                              context: context, records: node.ownImages))
            }
            for child in node.sortedChildren { visit(child, below) }
        }
        visit(focus, nil)
        return PhotoStream(sections: sections)
    }

    /// Changes whenever anything shown changes.
    public var signature: [String] {
        sections.map { s in
            ([s.path, s.context] + s.records.map { "\($0.versionKey)|\($0.thumbWidth)x\($0.thumbHeight)" })
                .joined(separator: "\u{1F}")
        }
    }

    /// Python's `bisect_right(starts, flat) - 1`.
    public func sectionOf(_ flat: Int) -> Int { bisectRight(starts, flat) - 1 }

    public func indexOf(photo path: String) -> Int? { flatOfPath[path] }

    public func sectionIndex(folder path: String) -> Int? { sectionOfPath[Paths.normpath(path)] }

    /// The section to show for a sidebar folder: its own, or (for a folder
    /// with only subfolders) its first descendant's.
    public func firstSection(within folder: String) -> Int? {
        if let exact = sectionIndex(folder: folder) { return exact }
        return sections.firstIndex { Paths.isWithin(folder, $0.path) }
    }
}

/// An anchor that keeps the view's place across a relayout.
public enum StreamAnchor: Equatable, Sendable {
    /// The row starting with this photo, *fraction* of a row pitch down it.
    case photo(path: String, fraction: Double)
    /// A section's heading, *offset* points below its top.
    case section(path: String, offset: Double)
}

/// A keyboard move in the grid.
public enum StreamMove: String, CaseIterable, Sendable {
    case left, right, up, down, pageUp = "page_up", pageDown = "page_down", home, end
}

/// Geometry of a `PhotoStream` at a canvas *width* and *slot* size: a
/// heading, then rows of *slot*-square cells, per section. All of it is
/// arithmetic per section, so a relayout costs O(folders).
public struct StreamLayout: Sendable {
    public let stream: PhotoStream
    public let width: Int
    public let slot: Int
    public let spacing: Int
    public let margin: Int
    public let headerHeight: Int
    public let pitch: Int
    public let columns: Int
    public let headerYs: [Int]
    public let rowsYs: [Int]
    public let endYs: [Int]
    public let rowCounts: [Int]
    public let height: Int

    public init(stream: PhotoStream, width: Int, slot: Int,
                spacing: Int = StreamMetrics.spacing, margin: Int = StreamMetrics.margin,
                headerHeight: Int = StreamMetrics.headerHeight,
                sectionGap: Int = StreamMetrics.sectionGap) {
        self.stream = stream
        self.width = width
        self.slot = slot
        self.spacing = spacing
        self.margin = margin
        self.headerHeight = headerHeight
        let pitch = slot + spacing
        self.pitch = pitch
        let usable = max(0, width - 2 * margin)
        self.columns = max(1, (usable + spacing) / pitch)
        var headerYs: [Int] = [], rowsYs: [Int] = [], endYs: [Int] = [], rowCounts: [Int] = []
        var y = margin
        for (i, section) in stream.sections.enumerated() {
            if i > 0 { y += sectionGap }
            headerYs.append(y)
            y += headerHeight
            let n = section.records.count
            let rows = (n + columns - 1) / columns
            rowsYs.append(y)
            rowCounts.append(rows)
            y += rows * pitch - (rows > 0 ? spacing : 0)
            endYs.append(y)
        }
        self.headerYs = headerYs
        self.rowsYs = rowsYs
        self.endYs = endYs
        self.rowCounts = rowCounts
        self.height = y + margin
    }

    /// The column count for a *width* (without building a layout).
    public static func columns(width: Int, slot: Int,
                               spacing: Int = StreamMetrics.spacing,
                               margin: Int = StreamMetrics.margin) -> Int {
        max(1, (max(0, width - 2 * margin) + spacing) / (slot + spacing))
    }

    // MARK: geometry

    /// (x, y, w, h) of photo *flat*'s cell.
    public func cell(_ flat: Int) -> (x: Int, y: Int, w: Int, h: Int) {
        let (s, row, col) = rowOf(flat)
        return (margin + col * pitch, rowsYs[s] + row * pitch, slot, slot)
    }

    /// (section, row within the section, column) of photo *flat*.
    public func rowOf(_ flat: Int) -> (section: Int, row: Int, column: Int) {
        let s = stream.sectionOf(flat)
        let local = flat - stream.starts[s]
        return (s, local / columns, local % columns)
    }

    /// The photo whose cell contains (x, y), if any.
    public func photoAt(x: Double, y: Double) -> Int? {
        let s = bisectRight(rowsYs.map(Double.init), y) - 1
        if s < 0 || y >= Double(endYs[s]) { return nil }
        let yOff = Int(y - Double(rowsYs[s]))
        let row = yOff / pitch, dy = yOff % pitch
        if x < Double(margin) { return nil }
        let xOff = Int(x - Double(margin))
        let col = xOff / pitch, dx = xOff % pitch
        if dy >= slot || dx >= slot || col >= columns { return nil }
        let local = row * columns + col
        if local >= stream.sections[s].records.count { return nil }
        return stream.starts[s] + local
    }

    /// The section showing at height *y*: the last whose heading starts at or
    /// above it (the scrollspy's "current" folder).
    public func sectionAt(y: Double) -> Int? {
        if headerYs.isEmpty { return nil }
        return max(0, bisectRight(headerYs.map(Double.init), y) - 1)
    }

    /// Sections with any part in [y0, y1).
    public func sectionsBetween(_ y0: Double, _ y1: Double) -> Range<Int> {
        if headerYs.isEmpty { return 0..<0 }
        let first = max(0, bisectRight(endYs.map(Double.init), y0 - 1))
        let last = bisectRight(headerYs.map(Double.init), y1 - 1)
        return first..<max(first, last)
    }

    /// Flat indices of the photos whose cells overlap [y0, y1), in order.
    public func photosBetween(_ y0: Double, _ y1: Double) -> [Int] {
        var out: [Int] = []
        let p = Double(pitch)
        for s in sectionsBetween(y0, y1) {
            let n = stream.sections[s].records.count
            if n == 0 { continue }
            let top = Double(rowsYs[s])
            let r0 = max(0, Int(((y0 - top) / p).rounded(.down)))
            let r1 = min(rowCounts[s], Int(((y1 - top - 1) / p).rounded(.down)) + 1)
            if r1 <= r0 { continue }
            let start = stream.starts[s]
            out.append(contentsOf: (start + r0 * columns)..<(start + min(n, r1 * columns)))
        }
        return out
    }

    // MARK: rows (for views that lay out rows themselves)

    /// The local index ranges of each row of section *s*.
    public func rows(inSection s: Int) -> [Range<Int>] {
        let n = stream.sections[s].records.count
        return stride(from: 0, to: n, by: columns).map { $0..<min(n, $0 + columns) }
    }

    // MARK: keyboard

    /// The photo reached from *flat* by *move*: left/right step through the
    /// whole stream; up/down go to the row above/below in the same column
    /// (into the neighbouring section at a section's edge, clamped to a short
    /// row); page moves repeat that *pageRows* times; home/end.
    public func neighbour(_ flat: Int, _ move: StreamMove, pageRows: Int = 1) -> Int {
        let total = stream.count
        if total == 0 { return flat }
        switch move {
        case .left: return max(0, flat - 1)
        case .right: return min(total - 1, flat + 1)
        case .home: return 0
        case .end: return total - 1
        case .up, .down, .pageUp, .pageDown:
            let steps = (move == .pageUp || move == .pageDown) ? pageRows : 1
            let up = move == .up || move == .pageUp
            var current = flat
            for _ in 0..<max(1, steps) {
                let next = vertical(current, up: up)
                if next == current { break }
                current = next
            }
            return current
        }
    }

    private func vertical(_ flat: Int, up: Bool) -> Int {
        let (s, row, col) = rowOf(flat)
        let starts = stream.starts
        let sections = stream.sections
        if up {
            if row > 0 { return flat - columns }
            var prev = s - 1
            while prev >= 0 && sections[prev].records.isEmpty { prev -= 1 }
            if prev < 0 { return flat }
            let n = sections[prev].records.count
            let lastRow = (n - 1) / columns
            return starts[prev] + min(n - 1, lastRow * columns + col)
        }
        let n = sections[s].records.count
        if (row + 1) * columns < n { return starts[s] + min(n - 1, (row + 1) * columns + col) }
        var next = s + 1
        while next < starts.count && sections[next].records.isEmpty { next += 1 }
        if next >= starts.count { return flat }
        return starts[next] + min(sections[next].records.count - 1, col)
    }

    // MARK: anchors

    /// What to keep at height *y* across a relayout: the photo row there, or
    /// the section heading. Nil for an empty stream.
    public func anchor(at y: Double) -> StreamAnchor? {
        guard let s = sectionAt(y: y) else { return nil }
        let section = stream.sections[s]
        if y >= Double(rowsYs[s]) && !section.records.isEmpty {
            let p = Double(pitch)
            let row = min(rowCounts[s] - 1, Int(((y - Double(rowsYs[s])) / p).rounded(.down)))
            let fraction = (y - Double(rowsYs[s]) - Double(row * pitch)) / p
            return .photo(path: section.records[row * columns].path, fraction: fraction)
        }
        return .section(path: section.path, offset: y - Double(headerYs[s]))
    }

    public func y(for anchor: StreamAnchor?) -> Double? {
        switch anchor {
        case nil: return nil
        case let .photo(path, fraction)?:
            guard let flat = stream.indexOf(photo: path) else { return nil }
            return Double(cell(flat).y) + fraction * Double(pitch)
        case let .section(path, offset)?:
            guard let s = stream.sectionIndex(folder: path) else { return nil }
            return Double(headerYs[s]) + offset
        }
    }

    public func sectionTop(_ s: Int) -> Int { headerYs[s] }
}

/// Python's `bisect.bisect_right`.
func bisectRight<T: Comparable>(_ a: [T], _ x: T) -> Int {
    var lo = 0, hi = a.count
    while lo < hi {
        let mid = (lo + hi) / 2
        if x < a[mid] { hi = mid } else { lo = mid + 1 }
    }
    return lo
}

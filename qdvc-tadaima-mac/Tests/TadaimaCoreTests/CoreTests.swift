import XCTest
@testable import TadaimaCore

/// What the parity fixture does not cover: the scanner on real files, the
/// index, the sidebar's rows and scrollspy rule, row ranges and EXIF dates.
final class CoreTests: XCTestCase {
    private var tmp: URL!

    override func setUpWithError() throws {
        tmp = FileManager.default.temporaryDirectory.appendingPathComponent("tadaima-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmp)
    }

    private func write(_ rel: String, _ bytes: [UInt8]) throws {
        let url = tmp.appendingPathComponent(rel)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(bytes).write(to: url)
    }

    private let jpeg: [UInt8] = [0xFF, 0xD8, 0xFF, 0xE0] + Array(repeating: 0, count: 28)
    private let png: [UInt8] = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A] + Array(repeating: 0, count: 8)

    func testScannerFindsRealPhotosOnly() throws {
        try write("trip/a.jpg", jpeg)
        try write("trip/B.PNG", png)
        try write("trip/._a.jpg", [0x00, 0x05, 0x16, 0x07] + Array(repeating: 0, count: 12))  // AppleDouble
        try write("trip/notes.txt", jpeg)                   // right bytes, wrong extension
        try write("trip/fake.jpg", Array("not an image".utf8))
        try write("trip/day2/c.jpeg", jpeg)
        try write(".hidden/d.jpg", jpeg)                    // hidden folders count, as in the GTK edition
        // A folder symlink back up the tree must not loop or duplicate.
        try FileManager.default.createSymbolicLink(atPath: tmp.appendingPathComponent("trip/day2/loop").path,
                                                   withDestinationPath: tmp.appendingPathComponent("trip").path)
        let found = PhotoScanner.scan([tmp.path]).map { Paths.basename($0.path) }.sorted()
        XCTAssertEqual(found, ["B.PNG", "a.jpg", "c.jpeg", "d.jpg"])
        XCTAssertTrue(PhotoScanner.scan([tmp.appendingPathComponent("missing").path]).isEmpty)
    }

    func testIndexSyncAndRoundTrip() throws {
        let index = PhotoIndex(file: tmp.appendingPathComponent("cache/index.json"))
        let a = PhotoScanner.Found(path: "/p/a.jpg", mtime: 1, size: 10)
        let b = PhotoScanner.Found(path: "/p/b.jpg", mtime: 1, size: 20)
        var result = index.sync(with: [a, b])
        XCTAssertEqual(result.changed.map(\.path).sorted(), ["/p/a.jpg", "/p/b.jpg"])
        XCTAssertEqual(index.records["/p/a.jpg"]?.key, CacheKey.forPath("/p/a.jpg"))
        // Derive one, then save and load.
        var rec = index.records["/p/a.jpg"]!
        rec.thumbWidth = 500
        rec.thumbHeight = 375
        rec.derived = true
        index.update(rec)
        try index.save()
        let again = PhotoIndex(file: index.file)
        again.load()
        XCTAssertEqual(again.records["/p/a.jpg"], rec)
        XCTAssertEqual(again.displayable.map(\.path), ["/p/a.jpg"])
        XCTAssertEqual(again.pending.map(\.path), ["/p/b.jpg"])
        // b changes, a is unchanged, c appears, and nothing else is there.
        let b2 = PhotoScanner.Found(path: "/p/b.jpg", mtime: 2, size: 20)
        let c = PhotoScanner.Found(path: "/p/c.jpg", mtime: 1, size: 5)
        result = again.sync(with: [a, b2, c])
        XCTAssertEqual(result.changed.map(\.path).sorted(), ["/p/b.jpg", "/p/c.jpg"])
        XCTAssertTrue(again.records["/p/a.jpg"]!.derived)       // untouched
        result = again.sync(with: [c])
        XCTAssertEqual(result.removed.map(\.path).sorted(), ["/p/a.jpg", "/p/b.jpg"])
        // An older format starts empty.
        try Data(#"{"version": 0, "records": []}"#.utf8).write(to: index.file)
        again.load()
        XCTAssertTrue(again.records.isEmpty)
    }

    private func library() -> FolderNode {
        let paths = ["/home/james/Pictures/2024/a.jpg", "/home/james/Pictures/2024/b.jpg",
                     "/home/james/Other/c.jpg", "/home/james/Pictures/d.jpg"]
        let records = paths.map { ImageRecord(path: $0, mtime: 0, size: 1, key: $0, derived: true) }
        return FolderTree.build(records: records, scannedRoots: ["/home/james"])
    }

    func testSidebarRowsFollowExpansionAndFocus() {
        let root = library()
        var rows = Sidebar.rows(root: root, focus: "/", expanded: [])
        XCTAssertEqual(rows.map(\.path), ["/"])
        rows = Sidebar.rows(root: root, focus: "/", expanded: ["/", "/home", "/home/james"])
        XCTAssertEqual(rows.map(\.path), ["/", "/home", "/home/james", "/home/james/Other", "/home/james/Pictures"])
        XCTAssertEqual(rows.map(\.depth), [0, 1, 2, 3, 3])
        XCTAssertEqual(rows.last?.count, 3)
        XCTAssertEqual(rows.last?.icon?.name, "d.jpg")      // its own first photo
        XCTAssertTrue(rows.last!.hasChildren && !rows.last!.isExpanded)
        // Focused: the enclosing folders come first, flat, and are never
        // the focus's subtree.
        rows = Sidebar.rows(root: root, focus: "/home/james", expanded: ["/home/james"])
        XCTAssertEqual(rows.map(\.kind), [.enclosing, .enclosing, .focused, .folder, .folder])
        XCTAssertEqual(rows.map(\.depth), [0, 0, 0, 1, 1])
        XCTAssertEqual(Set(rows.map(\.id)).count, rows.count)
    }

    func testScrollspyPicksTheNearestVisibleParent() {
        let root = library()
        let rows = Sidebar.rows(root: root, focus: "/", expanded: ["/", "/home", "/home/james"])
        XCTAssertEqual(Sidebar.visibleRow(for: "/home/james/Pictures/2024", in: rows, focus: "/")?.path,
                       "/home/james/Pictures")
        XCTAssertEqual(Sidebar.visibleRow(for: "/home/james/Other", in: rows, focus: "/")?.path,
                       "/home/james/Other")
        let focused = Sidebar.rows(root: root, focus: "/home/james/Pictures", expanded: [])
        XCTAssertEqual(Sidebar.visibleRow(for: "/home/james/Pictures/2024", in: focused,
                                          focus: "/home/james/Pictures")?.kind, .focused)
        XCTAssertNil(Sidebar.visibleRow(for: "/home/james/Other", in: focused, focus: "/home/james/Pictures"))
    }

    func testRowRangesAndColumns() {
        let root = library()
        let stream = PhotoStream.build(focus: root, home: "/home/james")
        XCTAssertEqual(stream.sections.map(\.name), ["Other", "Pictures", "2024"])
        let layout = StreamLayout(stream: stream, width: 1000, slot: 150)
        XCTAssertEqual(layout.rows(inSection: 2), [0..<2])
        let narrow = StreamLayout(stream: stream, width: 174, slot: 150)
        XCTAssertEqual(narrow.columns, 1)
        XCTAssertEqual(narrow.rows(inSection: 2), [0..<1, 1..<2])
        XCTAssertEqual(StreamLayout.columns(width: 1000, slot: 150), layout.columns)
    }

    func testExifDates() {
        let utc = TimeZone(identifier: "UTC")!
        let date = CameraDetails.parseExifDate("2024:05:31 18:42:07\u{0}", timeZone: utc)
        XCTAssertEqual(date?.timeIntervalSince1970, 1_717_180_927)
        XCTAssertNotNil(CameraDetails.parseExifDate("2024-05-31 18:42:07", timeZone: utc))
        XCTAssertNil(CameraDetails.parseExifDate("0000:00:00 00:00:00", timeZone: utc))
        var e = ExifValues()
        e.dateTime = "2024:06:01 10:00:00"
        e.dateTimeOriginal = "2024:05:31 18:42:07"
        let taken = CameraDetails.dateTaken(e)
        XCTAssertEqual(taken, CameraDetails.parseExifDate("2024:05:31 18:42:07"))  // the original wins
    }
}

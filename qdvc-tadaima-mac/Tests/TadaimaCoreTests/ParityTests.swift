import XCTest
@testable import TadaimaCore

/// Replays Fixtures/parity.json, recorded from the GTK 4 edition's Python by
/// tools/make_fixtures.py. Any failure is either a change in the Python to
/// port, or a bug in the port.
final class ParityTests: XCTestCase {
    static let fixture: [String: Any] = {
        guard let url = Bundle.module.url(forResource: "parity", withExtension: "json", subdirectory: "Fixtures"),
              let data = try? Data(contentsOf: url),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            fatalError("Fixtures/parity.json is missing; run tools/make_fixtures.py")
        }
        return json
    }()

    private func cases(_ key: String) -> [[Any]] {
        Self.fixture[key] as? [[Any]] ?? []
    }

    private func optString(_ v: Any) -> String? { v as? String }
    private func optInt(_ v: Any) -> Int? { (v as? NSNumber)?.intValue }
    private func double(_ v: Any) -> Double { (v as! NSNumber).doubleValue }
    private func ints(_ v: Any) -> [Int] { (v as! [NSNumber]).map(\.intValue) }

    // MARK: paths

    func testPaths() {
        for c in Self.fixture["paths"] as! [[String: String]] {
            let p = c["in"]!
            XCTAssertEqual(Paths.normpath(p), c["normpath"], "normpath(\(p))")
            XCTAssertEqual(Paths.dirname(p), c["dirname"], "dirname(\(p))")
            XCTAssertEqual(Paths.basename(p), c["basename"], "basename(\(p))")
        }
        for c in cases("within") {
            XCTAssertEqual(Paths.isWithin(c[0] as! String, c[1] as! String), c[2] as! Bool, "\(c)")
        }
        for c in cases("tilde") {
            XCTAssertEqual(Paths.tilde(c[0] as! String, home: optString(c[1])), c[2] as! String, "\(c)")
        }
    }

    func testCacheKeysExtensionsAndSniffing() {
        for c in cases("cache_keys") {
            XCTAssertEqual(CacheKey.forPath(c[0] as! String), c[1] as! String, "\(c[0])")
        }
        for c in cases("extensions") {
            XCTAssertEqual(ImageSniffer.hasSupportedExtension(c[0] as! String), c[1] as! Bool, "\(c[0])")
        }
        for c in cases("sniff") {
            let hex = c[1] as! String
            var bytes: [UInt8] = []
            var i = hex.startIndex
            while i < hex.endIndex {
                let j = hex.index(i, offsetBy: 2)
                bytes.append(UInt8(hex[i..<j], radix: 16)!)
                i = j
            }
            XCTAssertEqual(ImageSniffer.looksLikeImage(header: bytes), c[2] as! Bool, "\(c[0])")
        }
    }

    func testScannedFolderAbsorption() {
        for c in cases("absorb") {
            XCTAssertEqual(ScannedFolders.normalize(c[0] as! [String]), c[1] as! [String], "\(c[0])")
        }
    }

    // MARK: tree and stream

    private func library(_ name: String) -> (FolderNode, [String: Any]) {
        let lib = (Self.fixture["libraries"] as! [String: [String: Any]])[name]!
        let records = (lib["records"] as! [String]).map {
            ImageRecord(path: $0, mtime: 0, size: 1, key: $0, thumbWidth: 500, thumbHeight: 375, derived: true)
        }
        return (FolderTree.build(records: records, scannedRoots: lib["roots"] as! [String]), lib)
    }

    private func assertTree(_ node: FolderNode, _ expected: [String: Any], file: StaticString = #filePath,
                            line: UInt = #line) {
        XCTAssertEqual(node.path, expected["path"] as? String, file: file, line: line)
        XCTAssertEqual(node.name, expected["name"] as? String, "name of \(node.path)", file: file, line: line)
        XCTAssertEqual(node.imageCount, expected["count"] as? Int, "count of \(node.path)", file: file, line: line)
        XCTAssertEqual(node.isScannedRoot, expected["scanned"] as? Bool, "scanned \(node.path)", file: file, line: line)
        XCTAssertEqual(node.ownImages.map(\.name), expected["images"] as? [String], "images of \(node.path)",
                       file: file, line: line)
        let children = expected["children"] as! [[String: Any]]
        XCTAssertEqual(node.children.map(\.path), children.map { $0["path"] as! String },
                       "children of \(node.path)", file: file, line: line)
        for (child, exp) in zip(node.children, children) { assertTree(child, exp, file: file, line: line) }
    }

    func testFolderTrees() {
        for name in (Self.fixture["libraries"] as! [String: Any]).keys.sorted() {
            let (root, lib) = library(name)
            assertTree(root, lib["tree"] as! [String: Any])
        }
    }

    func testStreamsAndLayouts() {
        var layoutsChecked = 0
        for s in Self.fixture["streams"] as! [[String: Any]] {
            let (root, _) = library(s["library"] as! String)
            let focus = s["focus"] as! String
            let stream = PhotoStream.build(focus: FolderTree.find(root, focus) ?? root, home: s["home"] as? String)
            let sections = s["sections"] as! [[String: Any]]
            XCTAssertEqual(stream.sections.map(\.path), sections.map { $0["path"] as! String }, "focus \(focus)")
            for (got, exp) in zip(stream.sections, sections) {
                XCTAssertEqual(got.name, exp["name"] as? String)
                XCTAssertEqual(got.context, exp["context"] as? String, "context of \(got.path)")
                XCTAssertEqual(got.records.map(\.path), exp["records"] as? [String], "records of \(got.path)")
            }
            for c in s["first_within"] as! [[Any]] {
                XCTAssertEqual(stream.firstSection(within: c[0] as! String), optInt(c[1]), "first within \(c[0])")
            }
            for l in s["layouts"] as! [[String: Any]] {
                checkLayout(stream, l, label: "\(focus) @ \(l["width"]!)/\(l["slot"]!)")
                layoutsChecked += 1
            }
        }
        XCTAssertGreaterThan(layoutsChecked, 20)
    }

    private func checkLayout(_ stream: PhotoStream, _ l: [String: Any], label: String) {
        let layout = StreamLayout(stream: stream, width: l["width"] as! Int, slot: l["slot"] as! Int)
        XCTAssertEqual(layout.columns, l["columns"] as? Int, label)
        XCTAssertEqual(layout.height, l["height"] as? Int, label)
        XCTAssertEqual(layout.headerYs, ints(l["header_ys"]!), label)
        XCTAssertEqual(layout.rowsYs, ints(l["rows_ys"]!), label)
        XCTAssertEqual(layout.endYs, ints(l["end_ys"]!), label)
        XCTAssertEqual(layout.rowCounts, ints(l["row_counts"]!), label)
        for c in l["cells"] as! [[Int]] {
            let cell = layout.cell(c[0])
            XCTAssertEqual([cell.x, cell.y], [c[1], c[2]], "\(label) cell \(c[0])")
        }
        for c in l["photo_at"] as! [[Any]] {
            XCTAssertEqual(layout.photoAt(x: double(c[0]), y: double(c[1])), optInt(c[2]),
                           "\(label) photo_at \(c[0]),\(c[1])")
        }
        for c in l["section_at"] as! [[Any]] {
            XCTAssertEqual(layout.sectionAt(y: double(c[0])), optInt(c[1]), "\(label) section_at \(c[0])")
        }
        for c in l["sections_between"] as! [[Any]] {
            let r = layout.sectionsBetween(double(c[0]), double(c[1]))
            XCTAssertEqual([r.lowerBound, r.upperBound], ints(c[2]), "\(label) sections_between \(c[0])")
        }
        for c in l["photos_between"] as! [[Any]] {
            XCTAssertEqual(layout.photosBetween(double(c[0]), double(c[1])), ints(c[2]),
                           "\(label) photos_between \(c[0])")
        }
        for c in l["neighbours"] as! [[Any]] {
            let move = StreamMove(rawValue: c[1] as! String)!
            XCTAssertEqual(layout.neighbour(c[0] as! Int, move, pageRows: c[2] as! Int), c[3] as! Int,
                           "\(label) \(c[1]) from \(c[0])")
        }
        for c in l["anchors"] as! [[Any]] {
            let y = double(c[0])
            let anchor = layout.anchor(at: y)
            let other = StreamLayout(stream: stream, width: c[3] as! Int, slot: c[4] as! Int)
            switch (anchor, c[1] as? [Any]) {
            case (nil, nil):
                break
            case let (.photo(path, fraction)?, exp?):
                XCTAssertEqual(exp[0] as? String, "photo", "\(label) anchor \(y)")
                XCTAssertEqual(path, exp[1] as? String, "\(label) anchor \(y)")
                XCTAssertEqual(fraction, double(exp[2]), accuracy: 1e-9, "\(label) anchor \(y)")
            case let (.section(path, offset)?, exp?):
                XCTAssertEqual(exp[0] as? String, "section", "\(label) anchor \(y)")
                XCTAssertEqual(path, exp[1] as? String, "\(label) anchor \(y)")
                XCTAssertEqual(offset, double(exp[2]), accuracy: 1e-9, "\(label) anchor \(y)")
            default:
                XCTFail("\(label) anchor at \(y): \(String(describing: anchor)) vs \(c[1])")
            }
            if let expected = c[2] as? NSNumber {
                XCTAssertEqual(other.y(for: anchor) ?? .nan, expected.doubleValue, accuracy: 1e-9,
                               "\(label) y_for_anchor \(y)")
            } else {
                XCTAssertNil(other.y(for: anchor), "\(label) y_for_anchor \(y)")
            }
        }
    }

    func testRevealAndForget() {
        for c in cases("reveal") {
            XCTAssertEqual(FolderTree.revealChain(focus: c[0] as! String, target: c[1] as! String),
                           c[2] as! [String], "\(c)")
        }
        for c in cases("forget") {
            let got = FolderTree.forgetSubtree(Set(c[0] as! [String]), c[1] as! String)
            XCTAssertEqual(got.sorted(), c[2] as! [String], "\(c)")
        }
    }

    // MARK: formats and camera details

    func testFormats() {
        for c in cases("fit_within") {
            let got = Formats.fitWithin(width: c[0] as! Int, height: c[1] as! Int, box: c[2] as! Int)
            XCTAssertEqual([got.width, got.height], ints(c[3]), "\(c)")
        }
        for c in cases("counts") { XCTAssertEqual(Formats.count(c[0] as! Int), c[1] as! String) }
        for c in cases("sizes") { XCTAssertEqual(Formats.humanSize(c[0] as! Int), c[1] as! String) }
        for c in cases("exposure") {
            XCTAssertEqual(CameraDetails.formatExposureTime(double(c[0])), c[1] as! String, "\(c[0])")
        }
        for c in cases("camera_names") {
            XCTAssertEqual(CameraDetails.formatCamera(make: c[0] as! String, model: c[1] as! String),
                           c[2] as! String)
        }
        for c in cases("coordinates") {
            XCTAssertEqual(CameraDetails.formatCoordinates(latitude: double(c[0]), longitude: double(c[1])),
                           c[2] as! String)
        }
        for c in cases("trim") {
            XCTAssertEqual(CameraDetails.trim(double(c[0]), places: c[1] as! Int), c[2] as! String, "\(c)")
        }
    }

    func testCameraDetails() {
        for c in Self.fixture["camera"] as! [[String: Any]] {
            let v = c["values"] as! [String: Any]
            var e = ExifValues()
            e.make = v["make"] as? String
            e.model = v["model"] as? String
            e.lensModel = v["lensModel"] as? String
            func d(_ k: String) -> Double? { (v[k] as? NSNumber)?.doubleValue }
            e.focalLength = d("focalLength")
            e.focalLength35mm = d("focalLength35mm")
            e.fNumber = d("fNumber")
            e.exposureTime = d("exposureTime")
            e.iso = d("iso")
            e.exposureBias = d("exposureBias")
            e.flash = (v["flash"] as? NSNumber)?.intValue
            e.latitude = d("latitude")
            e.longitude = d("longitude")
            e.altitude = d("altitude")
            e.altitudeBelowSeaLevel = v["altitudeBelowSeaLevel"] as? Bool ?? false
            let (rows, location) = CameraDetails.rows(e)
            let expected = (c["rows"] as! [[String]]).map { CameraDetails.Row($0[0], $0[1]) }
            XCTAssertEqual(rows, expected, "\(v)")
            if let loc = c["location"] as? [NSNumber] {
                XCTAssertEqual(location?.latitude ?? .nan, loc[0].doubleValue, accuracy: 1e-12)
                XCTAssertEqual(location?.longitude ?? .nan, loc[1].doubleValue, accuracy: 1e-12)
            } else {
                XCTAssertNil(location)
            }
        }
    }
}

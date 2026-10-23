import Foundation

/// Stable cache keys: the SHA-1 of the normalised absolute path, as the GTK
/// edition's `naming.cache_key` computes them (lower-case hex).
public enum CacheKey {
    public static func forPath(_ path: String) -> String {
        SHA1.hex(Array(Paths.normpath(path).utf8))
    }

    /// `<key>.<kind>.jpg`, the derivative's file name.
    public static func derivativeName(_ key: String, _ kind: String, ext: String = "jpg") -> String {
        "\(key).\(kind).\(ext)"
    }
}

/// The disposable index of every photo found: path → record. Saved as JSON in
/// the cache folder; safe to delete (it is rebuilt by the next scan).
public final class PhotoIndex {
    public static let version = 1
    public private(set) var records: [String: ImageRecord] = [:]
    public let file: URL

    public init(file: URL) {
        self.file = file
    }

    private struct Stored: Codable {
        var version: Int
        var records: [ImageRecord]
    }

    /// Load the saved index; a missing, unreadable or older-format file
    /// starts empty.
    public func load() {
        records = [:]
        guard let data = try? Data(contentsOf: file),
              let stored = try? JSONDecoder().decode(Stored.self, from: data),
              stored.version == Self.version else { return }
        for rec in stored.records { records[rec.path] = rec }
    }

    public func save() throws {
        let stored = Stored(version: Self.version,
                            records: records.values.sorted { $0.path < $1.path })
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        let data = try JSONEncoder().encode(stored)
        try data.write(to: file, options: .atomic)
    }

    public func update(_ rec: ImageRecord) { records[rec.path] = rec }

    public func remove(_ paths: [String]) {
        for path in paths { records[path] = nil }
    }

    public func removeAll() { records = [:] }

    /// Reconcile with what a scan *found*: new or changed files get fresh
    /// records (to be derived again); files no longer there are dropped.
    /// Returns (new or changed, removed).
    @discardableResult
    public func sync(with found: [PhotoScanner.Found]) -> (changed: [ImageRecord], removed: [ImageRecord]) {
        var changed: [ImageRecord] = []
        var current = Set<String>()
        for f in found {
            current.insert(f.path)
            if let existing = records[f.path], existing.mtime == f.mtime, existing.size == f.size {
                continue
            }
            let rec = ImageRecord(path: f.path, mtime: f.mtime, size: f.size, key: CacheKey.forPath(f.path))
            records[f.path] = rec
            changed.append(rec)
        }
        let removed = records.values.filter { !current.contains($0.path) }
        for rec in removed { records[rec.path] = nil }
        return (changed, removed)
    }

    /// Photos that can be shown: fully derived.
    public var displayable: [ImageRecord] { records.values.filter(\.derived) }

    /// Photos still needing derivatives, in path order.
    public var pending: [ImageRecord] {
        records.values.filter { !$0.derived }.sorted { $0.path < $1.path }
    }
}

/// SHA-1 (FIPS 180-4), for cache keys only. CryptoKit is not available on
/// Linux, where the core is also built and tested.
enum SHA1 {
    static func hex(_ message: [UInt8]) -> String {
        digest(message).map { String(format: "%02x", $0) }.joined()
    }

    static func digest(_ message: [UInt8]) -> [UInt8] {
        var h: [UInt32] = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0]
        var data = message
        let bitLength = UInt64(message.count) * 8
        data.append(0x80)
        while data.count % 64 != 56 { data.append(0) }
        for i in (0..<8).reversed() { data.append(UInt8((bitLength >> (UInt64(i) * 8)) & 0xFF)) }
        var w = [UInt32](repeating: 0, count: 80)
        for chunk in stride(from: 0, to: data.count, by: 64) {
            for i in 0..<16 {
                let b = chunk + i * 4
                w[i] = UInt32(data[b]) << 24 | UInt32(data[b + 1]) << 16 | UInt32(data[b + 2]) << 8 | UInt32(data[b + 3])
            }
            for i in 16..<80 {
                let x = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]
                w[i] = (x << 1) | (x >> 31)
            }
            var (a, b, c, d, e) = (h[0], h[1], h[2], h[3], h[4])
            for i in 0..<80 {
                let f: UInt32, k: UInt32
                switch i {
                case 0..<20: f = (b & c) | (~b & d); k = 0x5A827999
                case 20..<40: f = b ^ c ^ d; k = 0x6ED9EBA1
                case 40..<60: f = (b & c) | (b & d) | (c & d); k = 0x8F1BBCDC
                default: f = b ^ c ^ d; k = 0xCA62C1D6
                }
                let temp = ((a << 5) | (a >> 27)) &+ f &+ e &+ k &+ w[i]
                e = d
                d = c
                c = (b << 30) | (b >> 2)
                b = a
                a = temp
            }
            h[0] = h[0] &+ a; h[1] = h[1] &+ b; h[2] = h[2] &+ c; h[3] = h[3] &+ d; h[4] = h[4] &+ e
        }
        return h.flatMap { v in (0..<4).reversed().map { UInt8((v >> (UInt32($0) * 8)) & 0xFF) } }
    }
}

import Foundation

/// The scanned folders, finding photos in them, and the rules for what
/// counts as one. Ports of the GTK edition's `config.normalize_scanned_folders`
/// and `cache.looks_like_image` / `scan_images`.
public enum ScannedFolders {
    /// Dedupe and absorb: drop any folder that lies within another in the
    /// list (adding `~/Pictures` when `~/Pictures/2024` is listed keeps only
    /// `~/Pictures`). First-appearance order is kept.
    public static func normalize(_ paths: [String]) -> [String] {
        var seen = Set<String>()
        var unique: [String] = []
        for p in paths {
            let norm = Paths.normpath(p)
            if seen.insert(norm).inserted { unique.append(norm) }
        }
        return unique.filter { p in !unique.contains { isStrictAncestor($0, of: p) } }
    }

    private static func strippedSlash(_ s: String) -> String {
        var s = s
        while s.hasSuffix("/") { s.removeLast() }
        return s
    }

    /// Python's `_is_ancestor_of`: a strict parent directory.
    static func isStrictAncestor(_ ancestor: String, of descendant: String) -> Bool {
        if ancestor == descendant { return false }
        return descendant.hasPrefix(strippedSlash(ancestor) + "/")
    }
}

public enum ImageSniffer {
    /// Extensions considered at all (then confirmed by content).
    public static let extensions: Set<String> = ["jpg", "jpeg", "png", "heic"]
    /// HEIF brands accepted (bytes 8–12 of an ISO-BMFF `ftyp` box).
    static let heifBrands: Set<String> = ["heic", "heix", "heif", "mif1", "msf1", "hevc", "hevx"]
    static let appleDouble: [UInt8] = [0x00, 0x05, 0x16, 0x07]

    public static func hasSupportedExtension(_ name: String) -> Bool {
        guard let dot = name.lastIndex(of: "."), dot != name.startIndex else { return false }
        return extensions.contains(name[name.index(after: dot)...].lowercased())
    }

    /// True if the file's first bytes are a supported image's. A correct
    /// extension is not enough: macOS AppleDouble sidecars (`._photo.jpg`)
    /// carry one but hold no image.
    public static func looksLikeImage(header head: [UInt8]) -> Bool {
        if head.count < 4 { return false }
        if Array(head[0..<4]) == appleDouble { return false }
        if head.count >= 3 && head[0] == 0xFF && head[1] == 0xD8 && head[2] == 0xFF { return true }
        if head.count >= 8 && Array(head[0..<8]) == [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A] {
            return true
        }
        if head.count >= 12 && Array(head[4..<8]) == Array("ftyp".utf8) {
            let brand = String(decoding: head[8..<12], as: UTF8.self)
            return heifBrands.contains(brand)
        }
        return false
    }

    public static func fileLooksLikeImage(_ path: String) -> Bool {
        guard let handle = FileHandle(forReadingAtPath: path) else { return false }
        defer { try? handle.close() }
        let data = (try? handle.read(upToCount: 16)) ?? Data()
        return looksLikeImage(header: [UInt8](data))
    }
}

/// Finds photos under the scanned folders. Read only: directory listings, a
/// stat and a 16-byte header read per candidate.
public enum PhotoScanner {
    public struct Found: Hashable, Sendable {
        public let path: String
        public let mtime: Double
        public let size: Int
    }

    /// Supported photos under *roots*, recursively. Directory symlinks are
    /// not followed (no loops). With *skipPackages* (the Mac edition), the
    /// insides of bundles such as a Photos library are left alone, as Finder
    /// shows them as single files. *progress* is called with each folder.
    public static func scan(_ roots: [String], skipPackages: Bool = true,
                            progress: ((String) -> Void)? = nil) -> [Found] {
        var found: [Found] = []
        var seenDirs = Set<String>()
        let fm = FileManager.default
        let keys: [URLResourceKey] = [.isDirectoryKey, .isSymbolicLinkKey, .isPackageKey,
                                      .contentModificationDateKey, .fileSizeKey, .isRegularFileKey]
        for root in roots {
            let rootPath = Paths.normpath(root)
            var isDir: ObjCBool = false
            guard fm.fileExists(atPath: rootPath, isDirectory: &isDir), isDir.boolValue else { continue }
            var stack = [rootPath]
            while let dir = stack.popLast() {
                let real = (dir as NSString).resolvingSymlinksInPath
                if !seenDirs.insert(real).inserted { continue }
                progress?(dir)
                guard let entries = try? fm.contentsOfDirectory(
                    at: URL(fileURLWithPath: dir, isDirectory: true),
                    includingPropertiesForKeys: keys, options: []) else { continue }
                var subdirs: [String] = []
                for url in entries {
                    let values = try? url.resourceValues(forKeys: Set(keys))
                    let name = url.lastPathComponent
                    let path = Paths.join(dir, name)
                    if values?.isSymbolicLink == true {
                        // A symlinked file still counts (Python stats through
                        // it); a symlinked folder is not followed.
                        var targetIsDir: ObjCBool = false
                        if fm.fileExists(atPath: path, isDirectory: &targetIsDir), !targetIsDir.boolValue,
                           ImageSniffer.hasSupportedExtension(name), ImageSniffer.fileLooksLikeImage(path),
                           let attrs = try? fm.attributesOfItem(atPath: path) {
                            found.append(Found(path: path,
                                               mtime: (attrs[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0,
                                               size: (attrs[.size] as? NSNumber)?.intValue ?? 0))
                        }
                        continue
                    }
                    if values?.isDirectory == true {
                        if skipPackages && values?.isPackage == true { continue }
                        subdirs.append(path)
                        continue
                    }
                    guard ImageSniffer.hasSupportedExtension(name), ImageSniffer.fileLooksLikeImage(path) else {
                        continue
                    }
                    found.append(Found(path: path,
                                       mtime: values?.contentModificationDate?.timeIntervalSince1970 ?? 0,
                                       size: values?.fileSize ?? 0))
                }
                // Depth first, in name order, like a walk would read them.
                stack.append(contentsOf: subdirs.sorted().reversed())
            }
        }
        return found
    }
}

import Foundation

/// A single image on disk and what the cache knows about it.
/// (Python: `qdvc.models.ImageRecord`.)
public struct ImageRecord: Hashable, Codable, Sendable {
    /// Absolute source path (read only; never modified).
    public var path: String
    /// Modification time and size, for change detection.
    public var mtime: Double
    public var size: Int
    /// Stable cache key (`CacheKey.forPath`).
    public var key: String
    /// Pixel size of the cached thumbnail (0 until it exists), so the grid can
    /// lay a photo out at its proportions before decoding anything.
    public var thumbWidth: Int
    public var thumbHeight: Int
    /// True once every cached derivative of this photo has been written.
    public var derived: Bool

    public init(path: String, mtime: Double, size: Int, key: String,
                thumbWidth: Int = 0, thumbHeight: Int = 0, derived: Bool = false) {
        self.path = path
        self.mtime = mtime
        self.size = size
        self.key = key
        self.thumbWidth = thumbWidth
        self.thumbHeight = thumbHeight
        self.derived = derived
    }

    public var name: String { Paths.basename(path) }
    public var folder: String { Paths.dirname(path) }

    /// Identifies this version of the file: a changed file gets a new one.
    public var versionKey: String { "\(path)|\(mtime)|\(size)" }
}

/// A folder in the filtered sidebar hierarchy. (Python: `FolderNode`.)
///
/// `imageCount` is recursive; `ownImages` are the photos directly inside.
public final class FolderNode: Identifiable {
    public let path: String
    public let name: String
    public internal(set) var children: [FolderNode] = []
    public internal(set) var ownImages: [ImageRecord] = []
    public internal(set) var imageCount = 0
    /// Explicitly added by the user (not merely on the way to one).
    public internal(set) var isScannedRoot = false

    public var id: String { path }

    public init(path: String, name: String) {
        self.path = path
        self.name = name
    }

    /// Children in display order (name, case-insensitively).
    public var sortedChildren: [FolderNode] {
        children.stableSorted { pythonLowerLess($0.name, $1.name) }
    }

    /// The photo to show as this folder's sidebar icon: its first own photo.
    public var iconImage: ImageRecord? { ownImages.first }
}

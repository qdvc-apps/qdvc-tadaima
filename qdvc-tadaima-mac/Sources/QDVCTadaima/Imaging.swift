import AppKit
import ImageIO
import TadaimaCore
import UniformTypeIdentifiers

/// Where the cache lives: `~/Library/Caches/org.qdvc.tadaima.mac/`, holding
/// `index.json` and `derivatives/` (one small thumbnail and one square crop
/// per photo). Safe to delete at any time; the next scan rebuilds it. The
/// scanned folders themselves are only ever read.
enum CacheLocation {
    static let root: URL = {
        let base = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Caches")
        return base.appendingPathComponent("org.qdvc.tadaima.mac", isDirectory: true)
    }()
    static let derivatives = root.appendingPathComponent("derivatives", isDirectory: true)
    static let indexFile = root.appendingPathComponent("index.json")

    static func thumbnail(_ rec: ImageRecord) -> URL {
        derivatives.appendingPathComponent(CacheKey.derivativeName(rec.key, "thumb"))
    }

    static func square(_ rec: ImageRecord) -> URL {
        derivatives.appendingPathComponent(CacheKey.derivativeName(rec.key, "square"))
    }

    /// Bytes used and number of files in the cache.
    static func usage() -> (bytes: Int, files: Int) {
        guard let items = try? FileManager.default.contentsOfDirectory(
            at: derivatives, includingPropertiesForKeys: [.fileSizeKey]) else { return (0, 0) }
        let bytes = items.reduce(0) { $0 + ((try? $1.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
        return (bytes, items.count)
    }
}

/// ImageIO helpers. Everything here reads the original file and writes only
/// into the cache.
enum Imaging {
    /// The cached thumbnail's longest side, in pixels (as in the GTK edition).
    static let thumbnailMax = 500
    /// The sidebar icon and filmstrip crop, in pixels.
    static let squareSize = 128

    /// The image at *url*, upright (EXIF orientation applied) and no larger
    /// than *maxPixel* on its longest side.
    static func downsample(_ url: URL, maxPixel: Int) -> CGImage? {
        let sourceOptions = [kCGImageSourceShouldCache: false] as CFDictionary
        guard let source = CGImageSourceCreateWithURL(url as CFURL, sourceOptions) else { return nil }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceThumbnailMaxPixelSize: max(1, maxPixel),
        ]
        return CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
    }

    /// Make *rec*'s cached thumbnail and square crop. Returns the record
    /// with its thumbnail size filled in and marked derived, or nil if the
    /// file couldn't be read as an image.
    static func derive(_ rec: ImageRecord) -> ImageRecord? {
        let url = URL(fileURLWithPath: rec.path)
        guard let thumb = downsample(url, maxPixel: thumbnailMax) else { return nil }
        try? FileManager.default.createDirectory(at: CacheLocation.derivatives, withIntermediateDirectories: true)
        guard writeJPEG(thumb, to: CacheLocation.thumbnail(rec), quality: 0.6),
              let square = squareCrop(thumb, side: squareSize),
              writeJPEG(square, to: CacheLocation.square(rec), quality: 0.7) else { return nil }
        var out = rec
        out.thumbWidth = thumb.width
        out.thumbHeight = thumb.height
        out.derived = true
        return out
    }

    static func removeDerivatives(_ rec: ImageRecord) {
        try? FileManager.default.removeItem(at: CacheLocation.thumbnail(rec))
        try? FileManager.default.removeItem(at: CacheLocation.square(rec))
    }

    /// The centre square of *image*, scaled to *side* pixels.
    static func squareCrop(_ image: CGImage, side: Int) -> CGImage? {
        let edge = min(image.width, image.height)
        let rect = CGRect(x: (image.width - edge) / 2, y: (image.height - edge) / 2, width: edge, height: edge)
        guard let cropped = image.cropping(to: rect),
              let space = CGColorSpace(name: CGColorSpace.sRGB),
              let context = CGContext(data: nil, width: side, height: side, bitsPerComponent: 8, bytesPerRow: 0,
                                      space: space, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else {
            return nil
        }
        context.interpolationQuality = .high
        context.draw(cropped, in: CGRect(x: 0, y: 0, width: side, height: side))
        return context.makeImage()
    }

    /// Write atomically: a temporary file beside the target, then a rename.
    static func writeJPEG(_ image: CGImage, to url: URL, quality: Double) -> Bool {
        let tmp = url.deletingLastPathComponent().appendingPathComponent(".\(UUID().uuidString).tmp")
        guard let dest = CGImageDestinationCreateWithURL(tmp as CFURL, UTType.jpeg.identifier as CFString, 1, nil)
        else { return false }
        CGImageDestinationAddImage(dest, image, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
        guard CGImageDestinationFinalize(dest) else {
            try? FileManager.default.removeItem(at: tmp)
            return false
        }
        _ = try? FileManager.default.removeItem(at: url)
        do {
            try FileManager.default.moveItem(at: tmp, to: url)
            return true
        } catch {
            try? FileManager.default.removeItem(at: tmp)
            return false
        }
    }
}

/// Decoded images for the views, cached in memory. Its async methods run off
/// the main thread (a non-isolated class), and give up early when the view
/// that asked has gone (task cancellation).
final class ImageCache: @unchecked Sendable {
    static let shared = ImageCache()

    private final class Box {
        let image: CGImage
        init(_ image: CGImage) { self.image = image }
    }

    private let cache = NSCache<NSString, Box>()

    private init() {
        cache.totalCostLimit = 320 * 1024 * 1024
    }

    func clear() { cache.removeAllObjects() }

    /// A grid thumbnail *pixels* wide on its longest side: from the cached
    /// thumbnail, or from the original when the grid is zoomed in beyond it.
    func thumbnail(_ rec: ImageRecord, pixels: Int) async -> CGImage? {
        let key = "t|\(rec.versionKey)|\(pixels)" as NSString
        if let hit = cache.object(forKey: key) { return hit.image }
        if Task.isCancelled { return nil }
        var image: CGImage?
        if pixels > Imaging.thumbnailMax {
            image = Imaging.downsample(URL(fileURLWithPath: rec.path), maxPixel: pixels)
        }
        if image == nil {
            image = Imaging.downsample(CacheLocation.thumbnail(rec), maxPixel: pixels)
        }
        guard let image else { return nil }
        cache.setObject(Box(image), forKey: key, cost: image.bytesPerRow * image.height)
        return image
    }

    /// The square crop (sidebar icons, the viewer's filmstrip).
    func square(_ rec: ImageRecord) async -> CGImage? {
        let key = "s|\(rec.versionKey)" as NSString
        if let hit = cache.object(forKey: key) { return hit.image }
        if Task.isCancelled { return nil }
        guard let image = Imaging.downsample(CacheLocation.square(rec), maxPixel: Imaging.squareSize) else {
            return nil
        }
        cache.setObject(Box(image), forKey: key, cost: image.bytesPerRow * image.height)
        return image
    }

    /// The photo itself for the viewer, at most *pixels* on its longest side.
    func screen(_ rec: ImageRecord, pixels: Int) async -> CGImage? {
        let key = "f|\(rec.versionKey)|\(pixels)" as NSString
        if let hit = cache.object(forKey: key) { return hit.image }
        if Task.isCancelled { return nil }
        guard let image = Imaging.downsample(URL(fileURLWithPath: rec.path), maxPixel: pixels) else { return nil }
        cache.setObject(Box(image), forKey: key, cost: image.bytesPerRow * image.height)
        return image
    }
}

/// A photo's details for the caption and the information panel.
struct PhotoDetails: Equatable, Sendable {
    struct Coordinates: Equatable, Sendable {
        let latitude: Double
        let longitude: Double
    }

    var width: Int?
    var height: Int?
    var sizeBytes = 0
    var dateTaken: Date?
    /// False when the date is the file's modification date.
    var dateIsExif = false
    var camera: [CameraDetails.Row] = []
    var location: Coordinates?

    var dimensions: String { Formats.dimensions(width: width, height: height) }

    var dateText: String {
        guard let dateTaken else { return "Unknown date" }
        let text = dateTaken.formatted(date: .abbreviated, time: .shortened)
        return dateIsExif ? text : "\(text) (file date)"
    }

    /// Read from the file's own properties with ImageIO (which reads HEIC
    /// natively). Off the main thread; read only.
    static func read(_ path: String) -> PhotoDetails {
        var details = PhotoDetails()
        let attrs = try? FileManager.default.attributesOfItem(atPath: path)
        details.sizeBytes = (attrs?[.size] as? NSNumber)?.intValue ?? 0
        var values = ExifValues()
        if let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
           let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [String: Any] {
            let w = int(props[kCGImagePropertyPixelWidth as String])
            let h = int(props[kCGImagePropertyPixelHeight as String])
            // Sizes as displayed: orientations 5–8 turn the picture a quarter.
            let turned = (5...8).contains(int(props[kCGImagePropertyOrientation as String]) ?? 1)
            details.width = turned ? h : w
            details.height = turned ? w : h
            let tiff = props[kCGImagePropertyTIFFDictionary as String] as? [String: Any] ?? [:]
            let exif = props[kCGImagePropertyExifDictionary as String] as? [String: Any] ?? [:]
            let gps = props[kCGImagePropertyGPSDictionary as String] as? [String: Any] ?? [:]
            values.make = tiff[kCGImagePropertyTIFFMake as String] as? String
            values.model = tiff[kCGImagePropertyTIFFModel as String] as? String
            values.dateTime = tiff[kCGImagePropertyTIFFDateTime as String] as? String
            values.dateTimeOriginal = exif[kCGImagePropertyExifDateTimeOriginal as String] as? String
            values.lensModel = exif[kCGImagePropertyExifLensModel as String] as? String
            values.focalLength = number(exif[kCGImagePropertyExifFocalLength as String])
            values.focalLength35mm = number(exif[kCGImagePropertyExifFocalLenIn35mmFilm as String])
            values.fNumber = number(exif[kCGImagePropertyExifFNumber as String])
            values.exposureTime = number(exif[kCGImagePropertyExifExposureTime as String])
            values.iso = number(exif[kCGImagePropertyExifISOSpeedRatings as String])
            values.exposureBias = number(exif[kCGImagePropertyExifExposureBiasValue as String])
            values.flash = int(exif[kCGImagePropertyExifFlash as String])
            if let lat = number(gps[kCGImagePropertyGPSLatitude as String]),
               let lon = number(gps[kCGImagePropertyGPSLongitude as String]) {
                let south = (gps[kCGImagePropertyGPSLatitudeRef as String] as? String)?.uppercased() == "S"
                let west = (gps[kCGImagePropertyGPSLongitudeRef as String] as? String)?.uppercased() == "W"
                values.latitude = south ? -abs(lat) : lat
                values.longitude = west ? -abs(lon) : lon
                values.altitude = number(gps[kCGImagePropertyGPSAltitude as String])
                values.altitudeBelowSeaLevel = int(gps[kCGImagePropertyGPSAltitudeRef as String]) == 1
            }
        }
        let (rows, location) = CameraDetails.rows(values)
        details.camera = rows
        details.location = location.map { Coordinates(latitude: $0.latitude, longitude: $0.longitude) }
        if let taken = CameraDetails.dateTaken(values) {
            details.dateTaken = taken
            details.dateIsExif = true
        } else {
            details.dateTaken = attrs?[.modificationDate] as? Date
        }
        return details
    }

    /// An ImageIO number (NSNumber, or the first of an array of them).
    private static func number(_ value: Any?) -> Double? {
        if let array = value as? [Any] { return number(array.first) }
        return (value as? NSNumber)?.doubleValue
    }

    private static func int(_ value: Any?) -> Int? {
        if let array = value as? [Any] { return int(array.first) }
        return (value as? NSNumber)?.intValue
    }
}

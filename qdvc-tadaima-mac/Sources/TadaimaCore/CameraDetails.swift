import Foundation

/// The EXIF values the information panel uses, already decoded. The Mac app
/// fills this from ImageIO's property dictionaries (`{TIFF}`, `{Exif}`,
/// `{GPS}`); the GTK edition reads the same tags with Pillow.
public struct ExifValues: Equatable, Sendable {
    public var make: String?
    public var model: String?
    public var lensModel: String?
    public var focalLength: Double?
    public var focalLength35mm: Double?
    public var fNumber: Double?
    public var exposureTime: Double?
    public var iso: Double?
    public var exposureBias: Double?
    public var flash: Int?
    /// Signed decimal degrees (south and west negative).
    public var latitude: Double?
    public var longitude: Double?
    public var altitude: Double?
    public var altitudeBelowSeaLevel = false
    public var dateTimeOriginal: String?
    public var dateTime: String?

    public init() {}
}

/// A photo's camera details for the information panel: a port of the GTK
/// edition's `metadata.camera_details` and its formatters.
///
/// The selection: what took the photo (camera, lens), how it was exposed
/// (focal length with its 35 mm equivalent, aperture, shutter speed, ISO,
/// exposure compensation, flash) and where (position and altitude).
public enum CameraDetails {
    public struct Row: Equatable, Sendable, Identifiable {
        public let label: String
        public let value: String
        public var id: String { label }
        public init(_ label: String, _ value: String) {
            self.label = label
            self.value = value
        }
    }

    public static func rows(_ e: ExifValues) -> (rows: [Row], location: (latitude: Double, longitude: Double)?) {
        var rows: [Row] = []
        let camera = formatCamera(make: clean(e.make), model: clean(e.model))
        if !camera.isEmpty { rows.append(Row("Camera", camera)) }
        let lens = clean(e.lensModel)
        if !lens.isEmpty { rows.append(Row("Lens", lens)) }

        let focal = usable(e.focalLength)
        let f35 = usable(e.focalLength35mm)
        if let focal, focal != 0 {
            var text = "\(trim(focal)) mm"
            if let f35, f35 != 0, abs(f35 - focal) >= 1 {
                text += " (\(trim(f35, places: 0)) mm in 35 mm terms)"
            }
            rows.append(Row("Focal length", text))
        } else if let f35, f35 != 0 {
            rows.append(Row("Focal length", "\(trim(f35, places: 0)) mm in 35 mm terms"))
        }

        if let f = usable(e.fNumber), f != 0 { rows.append(Row("Aperture", "ƒ/\(trim(f))")) }
        if let t = usable(e.exposureTime), t > 0 { rows.append(Row("Shutter speed", formatExposureTime(t))) }
        if let iso = usable(e.iso), iso != 0 { rows.append(Row("ISO", "\(Int(iso))")) }
        if let bias = usable(e.exposureBias), abs(bias) >= 0.05 {
            rows.append(Row("Exposure compensation", "\(bias > 0 ? "+" : "\u{2212}")\(trim(abs(bias))) EV"))
        }
        if let flash = e.flash, flash & 0x20 == 0 {  // 0x20: the camera has no flash
            rows.append(Row("Flash", flash & 1 != 0 ? "Fired" : "Did not fire"))
        }

        var location: (latitude: Double, longitude: Double)?
        if let lat = usable(e.latitude), let lon = usable(e.longitude), lat != 0 || lon != 0 {
            location = (lat, lon)
            var text = formatCoordinates(latitude: lat, longitude: lon)
            if let alt = usable(e.altitude) {
                let minus = e.altitudeBelowSeaLevel && alt != 0 ? "\u{2212}" : ""
                text += " · \(minus)\(String(format: "%.0f", alt)) m"
            }
            rows.append(Row("Location", text))
        }
        return (rows, location)
    }

    /// When the photo was taken: `DateTimeOriginal`, else `DateTime`, as a
    /// local wall-clock date (EXIF names no zone).
    public static func dateTaken(_ e: ExifValues) -> Date? {
        for raw in [e.dateTimeOriginal, e.dateTime] {
            if let raw, let date = parseExifDate(raw) { return date }
        }
        return nil
    }

    /// "2024:05:31 18:42:07" (or with dashes) → a date in the current zone.
    public static func parseExifDate(_ raw: String, timeZone: TimeZone = .current) -> Date? {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines.union(CharacterSet(charactersIn: "\u{0}")))
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        for format in ["yyyy:MM:dd HH:mm:ss", "yyyy-MM-dd HH:mm:ss"] {
            formatter.dateFormat = format
            if let date = formatter.date(from: text) { return date }
        }
        return nil
    }

    // MARK: formatters (shared with the GTK edition; see the parity fixture)

    /// "Apple" + "iPhone 15 Pro" → "Apple iPhone 15 Pro"; "Canon" + "Canon EOS
    /// R5" → "Canon EOS R5"; "NIKON CORPORATION" + "NIKON Z 6" → "NIKON Z 6".
    public static func formatCamera(make: String, model: String) -> String {
        let make = make.trimmingCharacters(in: .whitespaces)
        let model = model.trimmingCharacters(in: .whitespaces)
        if make.isEmpty { return model }
        if model.isEmpty { return make }
        let firstWord = make.split(separator: " ").first.map(String.init) ?? make
        if model.lowercased().hasPrefix(firstWord.lowercased()) { return model }
        return "\(make) \(model)"
    }

    /// 1/250 s, 1/3 s, 2.5 s. Halves round to even, as Python's `round` does.
    public static func formatExposureTime(_ seconds: Double) -> String {
        if seconds >= 1 { return "\(trim(seconds)) s" }
        let denominator = (1 / seconds).rounded(.toNearestOrEven)
        return "1/\(Int(denominator)) s"
    }

    public static func formatCoordinates(latitude: Double, longitude: Double) -> String {
        "\(String(format: "%.5f", abs(latitude)))° \(latitude >= 0 ? "N" : "S"), "
            + "\(String(format: "%.5f", abs(longitude)))° \(longitude >= 0 ? "E" : "W")"
    }

    /// Python's `f"{x:.{places}f}".rstrip("0").rstrip(".")`.
    public static func trim(_ x: Double, places: Int = 1) -> String {
        var s = String(format: "%.\(places)f", x)
        if s.contains(".") {
            while s.hasSuffix("0") { s.removeLast() }
            if s.hasSuffix(".") { s.removeLast() }
        }
        return s
    }

    private static func usable(_ x: Double?) -> Double? {
        guard let x, x.isFinite else { return nil }
        return x
    }

    private static func clean(_ s: String?) -> String {
        (s ?? "").trimmingCharacters(in: .whitespacesAndNewlines.union(CharacterSet(charactersIn: "\u{0}")))
    }
}

/// Small display formats shared with the GTK edition.
public enum Formats {
    /// Python's `f"{n:,}"`.
    public static func count(_ n: Int) -> String {
        let digits = String(abs(n))
        var out = ""
        for (i, ch) in digits.enumerated() {
            if i > 0 && (digits.count - i) % 3 == 0 { out.append(",") }
            out.append(ch)
        }
        return n < 0 ? "-" + out : out
    }

    /// 1.5 KB, 3.2 MB (binary units, one decimal; bytes whole).
    public static func humanSize(_ n: Int) -> String {
        var value = Double(n)
        for unit in ["B", "KB", "MB", "GB", "TB"] {
            if value < 1024 {
                return "\(String(format: unit == "B" ? "%.0f" : "%.1f", value)) \(unit)"
            }
            value /= 1024
        }
        return String(format: "%.1f PB", value)
    }

    /// A *width* × *height* image shrunk (never enlarged) to fit a *box*
    /// square, proportions kept, at least 1 × 1. Python's `fit_within`
    /// (`round` there rounds halves to even).
    public static func fitWithin(width: Int, height: Int, box: Int) -> (width: Int, height: Int) {
        if width <= 0 || height <= 0 { return (box, box) }
        let scale = min(Double(box) / Double(width), Double(box) / Double(height), 1.0)
        let w = max(1, Int((Double(width) * scale).rounded(.toNearestOrEven)))
        let h = max(1, Int((Double(height) * scale).rounded(.toNearestOrEven)))
        return (w, h)
    }

    public static func dimensions(width: Int?, height: Int?) -> String {
        guard let width, let height, width > 0, height > 0 else { return "Unknown dimensions" }
        return "\(width) × \(height) px"
    }
}

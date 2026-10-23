import Foundation

/// How the app looks.
enum Appearance: String, CaseIterable, Identifiable {
    case system, light, dark
    var id: String { rawValue }

    var title: String {
        switch self {
        case .system: return "Match System"
        case .light: return "Light"
        case .dark: return "Dark"
        }
    }
}

/// Preferences and remembered state, in the standard defaults domain
/// (`defaults read org.qdvc.tadaima.mac`). There is no workspace: the
/// scanned folders are only ever read, and everything derived from them is
/// in the cache folder.
enum Prefs {
    enum Key {
        static let scannedFolders = "scannedFolders"
        static let expandedFolders = "expandedFolders"
        static let currentFolder = "currentFolder"
        static let focusFolder = "focusFolder"
        static let thumbnailSize = "thumbnailSize"
        static let showInspector = "showInspector"
        static let appearance = "appearance"
    }

    private static var defaults: UserDefaults { .standard }

    static let defaultThumbnailSize = 150
    static let thumbnailSizes = 80...420
    static let thumbnailStep = 30

    static var scannedFolders: [String] {
        get { defaults.stringArray(forKey: Key.scannedFolders) ?? [] }
        set { defaults.set(newValue, forKey: Key.scannedFolders) }
    }

    static var expandedFolders: Set<String> {
        get { Set(defaults.stringArray(forKey: Key.expandedFolders) ?? []) }
        set { defaults.set(newValue.sorted(), forKey: Key.expandedFolders) }
    }

    /// The folder you were looking at, restored at launch.
    static var currentFolder: String? {
        get { defaults.string(forKey: Key.currentFolder) }
        set { defaults.set(newValue, forKey: Key.currentFolder) }
    }

    /// The focused folder; "/" (or nothing) is the unfocused view.
    static var focusFolder: String {
        get { defaults.string(forKey: Key.focusFolder) ?? "/" }
        set { defaults.set(newValue == "/" ? nil : newValue, forKey: Key.focusFolder) }
    }

    static var thumbnailSize: Int {
        get {
            let v = defaults.object(forKey: Key.thumbnailSize) as? Int ?? defaultThumbnailSize
            return min(max(v, thumbnailSizes.lowerBound), thumbnailSizes.upperBound)
        }
        set { defaults.set(newValue, forKey: Key.thumbnailSize) }
    }

    static var showInspector: Bool {
        get { defaults.bool(forKey: Key.showInspector) }
        set { defaults.set(newValue, forKey: Key.showInspector) }
    }

    static var appearance: Appearance {
        get { Appearance(rawValue: defaults.string(forKey: Key.appearance) ?? "") ?? .system }
        set { defaults.set(newValue.rawValue, forKey: Key.appearance) }
    }
}

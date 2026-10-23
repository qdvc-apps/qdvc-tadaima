import AppKit

/// Thin wrappers over AppKit services.
enum Platform {
    static func reveal(_ urls: [URL]) {
        NSWorkspace.shared.activateFileViewerSelecting(urls)
    }

    /// Put files on the pasteboard, as Finder's Copy does.
    static func copyFiles(_ urls: [URL]) {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.writeObjects(urls as [NSURL])
    }

    static func copy(_ text: String) {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(text, forType: .string)
    }

    /// Show a place in Maps. Only ever on the user's request: this is the one
    /// thing that sends anything about a photo off the Mac.
    static func showInMaps(latitude: Double, longitude: Double, name: String) {
        var components = URLComponents(string: "https://maps.apple.com/")!
        components.queryItems = [
            URLQueryItem(name: "ll", value: String(format: "%.6f,%.6f", latitude, longitude)),
            URLQueryItem(name: "q", value: name),
        ]
        if let url = components.url { NSWorkspace.shared.open(url) }
    }
}

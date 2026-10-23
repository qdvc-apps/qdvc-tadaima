import Foundation

/// POSIX path helpers with Python's `posixpath` semantics.
///
/// The folder tree and the stream's headings are built from these, so they
/// must agree with the GTK edition's `os.path` calls exactly (the parity
/// fixture checks them): Foundation's own path APIs resolve symlinks, treat
/// trailing slashes differently and drop a leading `//`.
public enum Paths {
    public static let separator = "/"

    /// `os.path.normpath`: collapse `//`, `.` and `..`; no trailing slash.
    public static func normpath(_ path: String) -> String {
        if path.isEmpty { return "." }
        let absolute = path.hasPrefix("/")
        // POSIX allows exactly two leading slashes to mean something else;
        // Python keeps them, and collapses three or more to one.
        var initialSlashes = absolute ? 1 : 0
        if absolute && path.hasPrefix("//") && !path.hasPrefix("///") {
            initialSlashes = 2
        }
        var parts: [Substring] = []
        for comp in path.split(separator: "/", omittingEmptySubsequences: true) {
            if comp == "." { continue }
            if comp == ".." {
                if (initialSlashes == 0 && parts.isEmpty) || (parts.last == "..") {
                    parts.append(comp)
                } else if !parts.isEmpty {
                    parts.removeLast()
                }
                continue
            }
            parts.append(comp)
        }
        let joined = parts.joined(separator: "/")
        let result = String(repeating: "/", count: initialSlashes) + joined
        return result.isEmpty ? "." : result
    }

    /// `os.path.dirname`.
    public static func dirname(_ path: String) -> String {
        guard let slash = path.lastIndex(of: "/") else { return "" }
        var head = String(path[..<path.index(after: slash)])
        if !head.isEmpty && head != String(repeating: "/", count: head.count) {
            while head.hasSuffix("/") { head.removeLast() }
        }
        return head
    }

    /// `os.path.basename`.
    public static func basename(_ path: String) -> String {
        guard let slash = path.lastIndex(of: "/") else { return path }
        return String(path[path.index(after: slash)...])
    }

    /// `os.path.join` for two parts.
    public static func join(_ a: String, _ b: String) -> String {
        if b.hasPrefix("/") { return b }
        if a.isEmpty || a.hasSuffix("/") { return a + b }
        return a + "/" + b
    }

    /// True if *descendant* is *ancestor* or lies beneath it.
    public static func isWithin(_ ancestor: String, _ descendant: String) -> Bool {
        let a = normpath(ancestor)
        let d = normpath(descendant)
        if a == d { return true }
        var prefix = a
        while prefix.hasSuffix("/") { prefix.removeLast() }
        return d.hasPrefix(prefix + "/")
    }

    /// *path* with the home folder written as `~`.
    public static func tilde(_ path: String, home: String?) -> String {
        guard let home, !home.isEmpty, isWithin(home, path) else { return path }
        let h = normpath(home)
        let p = normpath(path)
        if p == h { return "~" }
        let rest = p.dropFirst(h.hasSuffix("/") ? h.count : h.count + 1)
        return "~/" + rest
    }
}

/// Python's ordering for `sorted(key=str.lower)`: lower-cased, then by code
/// point. (Swift's own `<` on strings compares canonically equivalent forms,
/// which can order differently.)
public func pythonLowerLess(_ a: String, _ b: String) -> Bool {
    let x = a.lowercased().unicodeScalars.map(\.value)
    let y = b.lowercased().unicodeScalars.map(\.value)
    return x.lexicographicallyPrecedes(y)
}

extension Array {
    /// A stable sort, as Python's `sorted` is (Swift's `sort` is not
    /// guaranteed to be).
    func stableSorted(by less: (Element, Element) -> Bool) -> [Element] {
        enumerated()
            .sorted { l, r in
                if less(l.element, r.element) { return true }
                if less(r.element, l.element) { return false }
                return l.offset < r.offset
            }
            .map(\.element)
    }
}

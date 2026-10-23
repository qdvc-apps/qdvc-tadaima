import AppKit
import Observation
import SwiftUI
import TadaimaCore

/// A row of the photo stream as the grid lays it out: a folder's heading, or
/// a row of its photos (a range of the section's photos).
struct StreamRow: Identifiable, Equatable {
    enum Kind: Equatable {
        case header(section: Int)
        case photos(section: Int, range: Range<Int>)
    }

    let id: String
    let kind: Kind

    var section: Int {
        switch kind {
        case .header(let s), .photos(let s, _): return s
        }
    }

    static func headerID(_ folder: String) -> String { "h|" + folder }
    static func photosID(firstPhoto: String) -> String { "p|" + firstPhoto }
}

/// Something for the stream view to scroll to; a new token each time, so
/// asking twice for the same place still scrolls.
struct ScrollRequest: Equatable {
    enum Target: Equatable {
        case top
        /// A folder's heading, to the top of the view.
        case folder(String)
        /// A photo, scrolled into view (as little as possible).
        case photo(String)
    }

    let target: Target
    let token: Int
}

/// The photo viewer: one folder's photos, and which is showing.
struct ViewerState: Equatable {
    var records: [ImageRecord]
    var index: Int
    var current: ImageRecord { records[index] }
}

struct AlertInfo: Identifiable {
    let id = UUID()
    let title: String
    let message: String
}

/// All of the app's state and actions.
///
/// The sidebar and the stream are kept in step both ways, as in the GTK
/// edition: scrolling makes the folder at the top of the stream "current",
/// and the sidebar highlights its row — or its nearest visible parent's, never
/// expanding anything; choosing a folder in the sidebar scrolls its photos to
/// the top. `highlightedRowID` is what the sidebar's selection shows: the
/// model changes it for the scrollspy, and only the user's clicks go through
/// `userSelectedRow`, so highlighting is never mistaken for navigating.
@MainActor
@Observable
final class AppModel {
    // MARK: folders, tree and stream

    private(set) var scannedFolders: [String] = ScannedFolders.normalize(Prefs.scannedFolders)
    private(set) var tree = FolderNode(path: "/", name: "/")
    private(set) var stream = PhotoStream.empty
    /// Bumped whenever the stream's contents change.
    private(set) var streamGeneration = 0
    private(set) var sidebarRows: [SidebarRow] = []
    private(set) var focus: String = Paths.normpath(Prefs.focusFolder)
    private(set) var expanded: Set<String> = Prefs.expandedFolders

    /// The sidebar row shown as selected.
    var highlightedRowID: String?
    /// The folder you're looking at (the stream's top folder, or the folder
    /// last chosen in the sidebar while the stream is still showing it).
    private(set) var currentFolder: String? = Prefs.currentFolder

    // MARK: photos

    var selectedPhoto: String?
    var viewer: ViewerState?
    var quickLookURL: URL?
    var showInspector: Bool = Prefs.showInspector {
        didSet { Prefs.showInspector = showInspector }
    }
    var thumbnailSize: Int = Prefs.thumbnailSize {
        didSet { Prefs.thumbnailSize = thumbnailSize }
    }
    var appearance: Appearance = Prefs.appearance {
        didSet {
            Prefs.appearance = appearance
            applyAppearance()
        }
    }

    // MARK: status

    private(set) var scrollRequest: ScrollRequest?
    private(set) var isScanning = false
    private(set) var scanStatus = ""
    /// False until the first scan since launch has finished.
    private(set) var hasScanned = false
    var alert: AlertInfo?
    /// A scanned folder the user asked to stop scanning (awaiting confirmation).
    var confirmRemoval: String?

    @ObservationIgnored let index = PhotoIndex(file: CacheLocation.indexFile)
    @ObservationIgnored private var scanTask: Task<Void, Never>?
    @ObservationIgnored private var persistTask: Task<Void, Never>?
    @ObservationIgnored private var lastScan = Date.distantPast
    @ObservationIgnored private var started = false
    @ObservationIgnored private var scrollToken = 0
    /// The folder chosen in the sidebar, kept highlighted until the user
    /// scrolls: until `holdUntil`, the stream's reports are of the jump.
    @ObservationIgnored private var clickedFolder: String?
    @ObservationIgnored private var holdUntil = Date.distantPast
    @ObservationIgnored private var lastSignature: [String] = []
    @ObservationIgnored private var rowsCache: (generation: Int, columns: Int, rows: [StreamRow],
                                                 sectionOfRow: [String: Int])?

    // MARK: start-up

    func startUp() {
        guard !started else { return }
        started = true
        applyAppearance()
        index.load()
        rebuild()
        restoreSession()
        startScan()
    }

    /// Open the way to the folder you were last looking at, highlight it and
    /// scroll the stream to it.
    private func restoreSession() {
        guard let folder = currentFolder, FolderTree.find(tree, folder) != nil,
              Paths.isWithin(focus, folder) else {
            currentFolder = stream.sections.first?.path
            rehighlight()
            return
        }
        for parent in FolderTree.revealChain(focus: focus, target: folder) { expanded.insert(parent) }
        Prefs.expandedFolders = expanded
        refreshSidebarRows()
        goTo(folder: folder)
    }

    func appBecameActive() {
        // Pick up changes made while away, but not on every switch back.
        if started && !isScanning && Date().timeIntervalSince(lastScan) > 600 { startScan() }
    }

    func applyAppearance() {
        switch appearance {
        case .system: NSApp.appearance = nil
        case .light: NSApp.appearance = NSAppearance(named: .aqua)
        case .dark: NSApp.appearance = NSAppearance(named: .darkAqua)
        }
    }

    // MARK: rebuilding

    /// Rebuild the tree, the stream and the sidebar rows from the index.
    private func rebuild() {
        tree = FolderTree.build(records: index.displayable, scannedRoots: scannedFolders)
        if FolderTree.find(tree, focus) == nil {
            focus = "/"
            Prefs.focusFolder = "/"
        }
        let next = PhotoStream.build(focus: FolderTree.find(tree, focus) ?? tree)
        let signature = next.signature
        if signature != lastSignature {
            stream = next
            lastSignature = signature
            streamGeneration += 1
            if let path = selectedPhoto, next.indexOf(photo: path) == nil { selectedPhoto = nil }
        }
        refreshSidebarRows()
    }

    private func refreshSidebarRows() {
        sidebarRows = Sidebar.rows(root: tree, focus: focus, expanded: expanded)
        rehighlight()
    }

    /// Highlight the row for the current folder (or the folder being held).
    private func rehighlight() {
        let target = (Date() < holdUntil ? clickedFolder : nil) ?? currentFolder
        guard let target else { return }
        highlightedRowID = Sidebar.visibleRow(for: target, in: sidebarRows, focus: focus)?.id
    }

    // MARK: the stream's rows

    /// The grid's rows at *columns* across (cached per stream and width).
    func streamRows(columns: Int) -> [StreamRow] {
        if let cache = rowsCache, cache.generation == streamGeneration, cache.columns == columns {
            return cache.rows
        }
        var rows: [StreamRow] = []
        var sectionOfRow: [String: Int] = [:]
        for (s, section) in stream.sections.enumerated() {
            let header = StreamRow(id: StreamRow.headerID(section.path), kind: .header(section: s))
            rows.append(header)
            sectionOfRow[header.id] = s
            for range in stride(from: 0, to: section.records.count, by: columns)
                .map({ $0..<min(section.records.count, $0 + columns) }) {
                let row = StreamRow(id: StreamRow.photosID(firstPhoto: section.records[range.lowerBound].path),
                                    kind: .photos(section: s, range: range))
                rows.append(row)
                sectionOfRow[row.id] = s
            }
        }
        rowsCache = (streamGeneration, columns, rows, sectionOfRow)
        return rows
    }

    /// The id of the row holding *photo* at *columns* across.
    func rowID(forPhoto photo: String, columns: Int) -> String? {
        guard let flat = stream.indexOf(photo: photo) else { return nil }
        let s = stream.sectionOf(flat)
        let local = flat - stream.starts[s]
        let first = stream.sections[s].records[(local / columns) * columns]
        return StreamRow.photosID(firstPhoto: first.path)
    }

    /// The first photo in a row, or a heading's folder (to keep the view's
    /// place when the column count changes).
    func anchorPhoto(forRow id: String) -> String? {
        if id.hasPrefix("p|") { return String(id.dropFirst(2)) }
        if id.hasPrefix("h|") {
            let folder = String(id.dropFirst(2))
            return stream.sectionIndex(folder: folder).flatMap { stream.sections[$0].records.first?.path }
        }
        return nil
    }

    // MARK: scrollspy

    /// The stream's top row changed (it scrolled, or jumped).
    func streamTopRowChanged(_ rowID: String?) {
        guard let rowID, let s = rowsCache?.sectionOfRow[rowID], s < stream.sections.count else { return }
        if clickedFolder != nil {
            if Date() < holdUntil {
                rehighlight()
                return
            }
            clickedFolder = nil  // the user has scrolled since: the scrollspy takes over
        }
        let folder = stream.sections[s].path
        guard folder != currentFolder else { return }
        currentFolder = folder
        rehighlight()
        schedulePersist()
    }

    // MARK: sidebar

    /// The user chose a row (click or arrow keys).
    func userSelectedRow(_ id: String?) {
        guard let id, let row = sidebarRows.first(where: { $0.id == id }) else { return }
        highlightedRowID = id
        if row.kind == .enclosing {
            setFocus(row.path, keepCurrentFolder: true)
        } else {
            goTo(folder: row.path)
        }
    }

    /// Show *folder*'s photos (or its first subfolder's) at the top of the
    /// stream, and keep it highlighted until the user scrolls.
    func goTo(folder: String) {
        let path = Paths.normpath(folder)
        clickedFolder = path
        currentFolder = path
        holdUntil = Date().addingTimeInterval(0.8)
        rehighlight()
        persistNow()
        guard let s = stream.firstSection(within: path) else {
            scanStatusFlash("No photos in \(Paths.basename(path).isEmpty ? path : Paths.basename(path))")
            return
        }
        requestScroll(.folder(stream.sections[s].path))
    }

    /// The stream has performed a jump to a folder: hold the clicked folder
    /// from now (the request may have waited for the stream to appear).
    func didScrollToFolder() {
        if clickedFolder != nil { holdUntil = Date().addingTimeInterval(0.8) }
    }

    func toggleExpanded(_ path: String) {
        setExpanded(path, !expanded.contains(Paths.normpath(path)))
    }

    func setExpanded(_ path: String, _ open: Bool) {
        let p = Paths.normpath(path)
        if open { expanded.insert(p) } else { expanded = FolderTree.forgetSubtree(expanded, p) }
        Prefs.expandedFolders = expanded
        refreshSidebarRows()
    }

    /// Make *path* the top of the sidebar and the stream ("/" shows all).
    func setFocus(_ path: String, keepCurrentFolder: Bool = false) {
        let p = Paths.normpath(path)
        let oldFocus = focus
        let current = currentFolder
        focus = p
        Prefs.focusFolder = p
        expanded.insert(p)
        if keepCurrentFolder {
            // Stepping up: the folders on the way down to the old focus stay open.
            for parent in FolderTree.revealChain(focus: p, target: oldFocus) { expanded.insert(parent) }
            expanded.insert(oldFocus)
        }
        Prefs.expandedFolders = expanded
        rebuild()
        if keepCurrentFolder, let current, Paths.isWithin(p, current) {
            goTo(folder: current)
        } else {
            clickedFolder = nil
            currentFolder = stream.sections.first?.path
            rehighlight()
            requestScroll(.top)
            persistNow()
        }
    }

    var isFocused: Bool { focus != "/" }

    func focusOnEnclosingFolder() {
        guard isFocused else { return }
        setFocus(Paths.dirname(focus), keepCurrentFolder: true)
    }

    func showAllFolders() { setFocus("/", keepCurrentFolder: true) }

    func focusOnCurrentFolder() {
        if let folder = currentFolder { setFocus(folder) }
    }

    // MARK: photos

    var selectedRecord: ImageRecord? {
        guard let path = selectedPhoto, let flat = stream.indexOf(photo: path) else { return nil }
        return stream.records[flat]
    }

    /// The photo the inspector and the Photo menu are about.
    var activePhoto: ImageRecord? { viewer?.current ?? selectedRecord }

    func select(_ path: String?) { selectedPhoto = path }

    /// Open *rec* in the viewer, which steps through its folder's photos.
    func open(_ rec: ImageRecord) {
        let photos = FolderTree.find(tree, rec.folder)?.ownImages ?? [rec]
        selectedPhoto = rec.path
        viewer = ViewerState(records: photos, index: photos.firstIndex { $0.path == rec.path } ?? 0)
    }

    func openSelected() {
        if let rec = selectedRecord { open(rec) }
    }

    func closeViewer() {
        guard let v = viewer else { return }
        selectedPhoto = v.current.path
        viewer = nil
        requestScroll(.photo(v.current.path))
    }

    func step(_ delta: Int) {
        guard var v = viewer else { return }
        let next = v.index + delta
        guard v.records.indices.contains(next) else {
            NSSound.beep()
            return
        }
        v.index = next
        viewer = v
        selectedPhoto = v.current.path
    }

    /// Show the folder's *index*th photo (a click in the filmstrip).
    func showInViewer(index: Int) {
        guard var v = viewer, v.records.indices.contains(index) else { return }
        v.index = index
        viewer = v
        selectedPhoto = v.current.path
    }

    func quickLook() {
        if let rec = activePhoto { quickLookURL = URL(fileURLWithPath: rec.path) }
    }

    /// Quick Look steps through the photo's folder with the arrow keys.
    var quickLookItems: [URL] {
        guard let rec = activePhoto, let folder = FolderTree.find(tree, rec.folder) else { return [] }
        return folder.ownImages.map { URL(fileURLWithPath: $0.path) }
    }

    func showInFinder() {
        if let rec = activePhoto {
            Platform.reveal([URL(fileURLWithPath: rec.path)])
        } else if let folder = currentFolder {
            Platform.reveal([URL(fileURLWithPath: folder, isDirectory: true)])
        }
    }

    func revealFolder(_ path: String) {
        Platform.reveal([URL(fileURLWithPath: path, isDirectory: true)])
    }

    /// Copy the photo's file (to paste into Finder, Mail, …).
    func copyPhoto() {
        if let rec = activePhoto { Platform.copyFiles([URL(fileURLWithPath: rec.path)]) }
    }

    func openInDefaultApp() {
        if let rec = activePhoto { NSWorkspace.shared.open(URL(fileURLWithPath: rec.path)) }
    }

    // MARK: zoom

    func zoomIn() { setThumbnailSize(thumbnailSize + Prefs.thumbnailStep) }
    func zoomOut() { setThumbnailSize(thumbnailSize - Prefs.thumbnailStep) }
    func actualSize() { setThumbnailSize(Prefs.defaultThumbnailSize) }

    func setThumbnailSize(_ size: Int) {
        thumbnailSize = min(max(size, Prefs.thumbnailSizes.lowerBound), Prefs.thumbnailSizes.upperBound)
    }

    // MARK: scrolling

    func requestScroll(_ target: ScrollRequest.Target) {
        scrollToken += 1
        scrollRequest = ScrollRequest(target: target, token: scrollToken)
    }

    // MARK: scanned folders

    func chooseFoldersToAdd() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = true
        panel.prompt = "Add"
        panel.message = "Choose folders of photos. Tadaima only ever reads them."
        panel.begin { [weak self] response in
            guard response == .OK else { return }
            let urls = panel.urls
            Task { @MainActor in self?.addFolders(urls) }
        }
    }

    /// Add folders (from the open panel, a drop, or the Dock). A folder
    /// inside one already listed is covered by it; a folder containing
    /// listed ones absorbs them.
    @discardableResult
    func addFolders(_ urls: [URL]) -> Bool {
        let paths = urls.compactMap { url -> String? in
            var isDir: ObjCBool = false
            let path = url.standardizedFileURL.path
            return FileManager.default.fileExists(atPath: path, isDirectory: &isDir) && isDir.boolValue
                ? Paths.normpath(path) : nil
        }
        guard !paths.isEmpty else { return false }
        let before = scannedFolders
        let after = ScannedFolders.normalize(before + paths)
        let covered = paths.filter { !after.contains($0) }
        if !covered.isEmpty {
            let names = covered.map { ($0 as NSString).abbreviatingWithTildeInPath }.joined(separator: ", ")
            alert = AlertInfo(title: "Already included",
                              message: "\(names) is inside a folder Tadaima already scans, so its photos are already here.")
        }
        guard after != before else { return true }
        scannedFolders = after
        Prefs.scannedFolders = after
        rebuild()
        startScan()
        return true
    }

    /// Stop scanning *path*. Its photos leave the app (and their thumbnails
    /// the cache); the folder itself is untouched.
    func removeFolder(_ path: String) {
        scannedFolders.removeAll { $0 == path }
        Prefs.scannedFolders = scannedFolders
        let gone = index.records.values.filter { rec in !scannedFolders.contains { Paths.isWithin($0, rec.path) } }
        index.remove(gone.map(\.path))
        try? index.save()
        Task.detached(priority: .utility) { gone.forEach(Imaging.removeDerivatives) }
        if let folder = currentFolder, !scannedFolders.contains(where: { Paths.isWithin($0, folder) }) {
            currentFolder = nil
        }
        rebuild()
        if currentFolder == nil { currentFolder = stream.sections.first?.path }
        rehighlight()
    }

    func isScannedFolder(_ path: String) -> Bool { scannedFolders.contains(Paths.normpath(path)) }

    // MARK: scanning

    /// Look for new, changed and removed photos, then make the missing
    /// thumbnails. Photos appear as their thumbnails are made.
    func startScan() {
        scanTask?.cancel()
        guard !scannedFolders.isEmpty else {
            isScanning = false
            hasScanned = true
            scanStatus = ""
            return
        }
        isScanning = true
        lastScan = Date()
        scanStatus = "Looking for photos\u{2026}"
        let roots = scannedFolders
        scanTask = Task { [weak self] in
            let found = await Task.detached(priority: .utility) { PhotoScanner.scan(roots) }.value
            guard let self, !Task.isCancelled else { return }
            let (_, removed) = self.index.sync(with: found)
            if !removed.isEmpty {
                Task.detached(priority: .utility) { removed.forEach(Imaging.removeDerivatives) }
            }
            self.rebuild()
            await self.derivePending()
            guard !Task.isCancelled else { return }
            try? self.index.save()
            self.rebuild()
            self.isScanning = false
            self.hasScanned = true
            self.scanStatus = ""
        }
    }

    /// Make thumbnails for every photo missing them, four at a time.
    private func derivePending() async {
        let pending = index.pending
        guard !pending.isEmpty else { return }
        var queue = pending[...]
        var done = 0
        var lastUpdate = Date.distantPast
        var lastRebuild = Date()
        await withTaskGroup(of: ImageRecord?.self) { group in
            var running = 0
            while running < 4, let rec = queue.popFirst() {
                group.addTask(priority: .utility) { Imaging.derive(rec) }
                running += 1
            }
            while let derived = await group.next() {
                running -= 1
                done += 1
                // A photo ImageIO can't read stays pending (and hidden); the
                // next scan tries it again, as the GTK edition does.
                if let derived { index.update(derived) }
                let now = Date()
                if now.timeIntervalSince(lastUpdate) > 0.2 || done == pending.count {
                    scanStatus = "Making thumbnails: \(Formats.count(done)) of \(Formats.count(pending.count))"
                    lastUpdate = now
                }
                if now.timeIntervalSince(lastRebuild) > 1.5 {
                    rebuild()
                    lastRebuild = now
                }
                if done % 500 == 0 { try? index.save() }
                if !Task.isCancelled, let rec = queue.popFirst() {
                    group.addTask(priority: .utility) { Imaging.derive(rec) }
                    running += 1
                }
            }
        }
    }

    private func scanStatusFlash(_ text: String) {
        guard !isScanning else { return }
        scanStatus = text
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(2))
            guard let self, !self.isScanning, self.scanStatus == text else { return }
            self.scanStatus = ""
        }
    }

    // MARK: cache

    /// Throw away every thumbnail and the index, and make them again.
    func regenerateCache() {
        scanTask?.cancel()
        index.removeAll()
        try? FileManager.default.removeItem(at: CacheLocation.derivatives)
        try? FileManager.default.removeItem(at: CacheLocation.indexFile)
        ImageCache.shared.clear()
        rebuild()
        startScan()
    }

    var photoCount: Int { tree.imageCount }

    // MARK: window text

    var windowSubtitle: String {
        if !scanStatus.isEmpty { return scanStatus }
        if let folder = currentFolder, let node = FolderTree.find(tree, folder) {
            return node.name
        }
        return stream.isEmpty ? "" : "\(Formats.count(stream.count)) photos"
    }

    // MARK: persistence

    private func schedulePersist() {
        persistTask?.cancel()
        persistTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(1.5))
            guard !Task.isCancelled else { return }
            self?.persistNow()
        }
    }

    func persistNow() {
        Prefs.currentFolder = currentFolder
        Prefs.focusFolder = focus
        Prefs.expandedFolders = expanded
    }
}


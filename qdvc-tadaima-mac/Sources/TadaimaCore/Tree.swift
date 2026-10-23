import Foundation

/// The filtered folder hierarchy shown in the sidebar, and "Focus on Folder"
/// as data. A port of the GTK edition's `qdvc/tree.py`.
public enum FolderTree {
    /// The full hierarchy rooted at `/`, filtered to the folders on the way
    /// to, and beneath, the scanned folders. Counts are recursive; children
    /// and photos are sorted by name, case-insensitively.
    public static func build(records: [ImageRecord], scannedRoots: [String]) -> FolderNode {
        let root = FolderNode(path: "/", name: "/")
        var nodes: [String: FolderNode] = [root.path: root]
        let scanned = Set(scannedRoots.map(Paths.normpath))

        func ensure(_ raw: String) -> FolderNode {
            let path = Paths.normpath(raw)
            if let node = nodes[path] { return node }
            let parentPath = Paths.dirname(path)
            if parentPath == path {  // the filesystem root
                let node = FolderNode(path: path, name: path)
                nodes[path] = node
                return node
            }
            let parent = ensure(parentPath)
            let base = Paths.basename(path)
            let node = FolderNode(path: path, name: base.isEmpty ? path : base)
            if scanned.contains(path) { node.isScannedRoot = true }
            parent.children.append(node)
            nodes[path] = node
            return node
        }

        // Every scanned folder exists even if it has no photos.
        for scannedRoot in scannedRoots { _ = ensure(scannedRoot) }
        for rec in records { ensure(Paths.dirname(rec.path)).ownImages.append(rec) }
        computeCounts(root)
        sortRecursive(root)
        return root
    }

    @discardableResult
    private static func computeCounts(_ node: FolderNode) -> Int {
        var total = node.ownImages.count
        for child in node.children { total += computeCounts(child) }
        node.imageCount = total
        return total
    }

    private static func sortRecursive(_ node: FolderNode) {
        node.children = node.children.stableSorted { pythonLowerLess($0.name, $1.name) }
        node.ownImages = node.ownImages.stableSorted { pythonLowerLess($0.name, $1.name) }
        node.children.forEach(sortRecursive)
    }

    /// The node at *path* within *root*.
    public static func find(_ root: FolderNode, _ path: String) -> FolderNode? {
        let target = Paths.normpath(path)
        if Paths.normpath(root.path) == target { return root }
        var stack = [root]
        while let node = stack.popLast() {
            if Paths.normpath(node.path) == target { return node }
            stack.append(contentsOf: node.children)
        }
        return nil
    }

    /// The nodes from the root down to (not including) *focusPath*: the flat
    /// "enclosing folders" shown above a focused folder.
    public static func ancestorChain(_ root: FolderNode, _ focusPath: String) -> [FolderNode] {
        let target = Paths.normpath(focusPath)
        var chain: [FolderNode] = []
        func dfs(_ node: FolderNode, _ acc: [FolderNode]) -> Bool {
            if Paths.normpath(node.path) == target {
                chain = acc
                return true
            }
            for child in node.children where dfs(child, acc + [node]) { return true }
            return false
        }
        _ = dfs(root, [])
        return chain
    }

    /// Folders to expand, top-down, so *target* has a row in a tree whose
    /// top row is *focusPath*: the focus itself down to *target*'s parent.
    public static func revealChain(focus focusPath: String, target rawTarget: String) -> [String] {
        let focus = Paths.normpath(focusPath)
        let target = Paths.normpath(rawTarget)
        if target == focus || !Paths.isWithin(focus, target) { return [] }
        var chain: [String] = []
        var p = Paths.dirname(target)
        while Paths.isWithin(focus, p) {
            chain.append(p)
            let parent = Paths.dirname(p)
            if p == focus || parent == p { break }
            p = parent
        }
        return chain.reversed()
    }

    /// *expanded* without *path* and every folder beneath it: collapsing a
    /// folder forgets what was open inside it.
    public static func forgetSubtree(_ expanded: Set<String>, _ path: String) -> Set<String> {
        expanded.filter { !Paths.isWithin(path, $0) }
    }
}

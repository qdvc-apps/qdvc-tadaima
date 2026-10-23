import AppKit
import SwiftUI
import TadaimaCore

/// Settings (⌘,). Every change applies at once, as in Apple's apps; the window
/// closes with ⌘W or its close button.
struct SettingsView: View {
    var body: some View {
        TabView {
            GeneralSettings()
                .tabItem { Label("General", systemImage: "gearshape") }
            FolderSettings()
                .tabItem { Label("Folders", systemImage: "folder") }
            CacheSettings()
                .tabItem { Label("Cache", systemImage: "internaldrive") }
        }
        .frame(width: 540)
        .padding(20)
    }
}

private struct GeneralSettings: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Form {
            Picker("Appearance:", selection: $model.appearance) {
                ForEach(Appearance.allCases) { Text($0.title).tag($0) }
            }
            .help("Photos often look best on a dark background.")
            LabeledContent("Thumbnail size:") {
                HStack {
                    Slider(value: Binding(get: { Double(model.thumbnailSize) },
                                          set: { model.setThumbnailSize(Int($0.rounded())) }),
                           in: Double(Prefs.thumbnailSizes.lowerBound)...Double(Prefs.thumbnailSizes.upperBound),
                           step: Double(Prefs.thumbnailStep))
                        .frame(width: 200)
                    Text("\(model.thumbnailSize) pt")
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}

/// The scanned folders, as a list with + and − buttons beneath it.
private struct FolderSettings: View {
    @Environment(AppModel.self) private var model
    @State private var selection: String?
    @State private var confirm: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Tadaima shows the photos in these folders and every folder inside them. "
                 + "It only ever reads them; thumbnails go in its own cache.")
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            VStack(spacing: 0) {
                List(model.scannedFolders, id: \.self, selection: $selection) { path in
                    HStack(spacing: 8) {
                        Image(systemName: "folder.fill").foregroundStyle(Color.accentColor)
                        VStack(alignment: .leading, spacing: 1) {
                            Text((path as NSString).lastPathComponent)
                            Text((path as NSString).abbreviatingWithTildeInPath)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                    }
                    .contextMenu {
                        Button("Show in Finder") { model.revealFolder(path) }
                    }
                }
                .frame(height: 220)
                Divider()
                HStack(spacing: 0) {
                    Button { model.chooseFoldersToAdd() } label: {
                        Image(systemName: "plus").frame(width: 24, height: 20)
                    }
                    .help("Add a folder")
                    Divider().frame(height: 16)
                    Button { confirm = selection } label: {
                        Image(systemName: "minus").frame(width: 24, height: 20)
                    }
                    .disabled(selection == nil)
                    .help("Stop scanning the selected folder")
                    Spacer()
                }
                .buttonStyle(.borderless)
                .padding(4)
            }
            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Color(nsColor: .separatorColor)))
            Text("Adding a folder that holds one already listed replaces it; a folder inside one already listed is already covered.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .confirmationDialog(confirm.map { "Stop scanning \u{201C}\(($0 as NSString).lastPathComponent)\u{201D}?" } ?? "",
                            isPresented: Binding(get: { confirm != nil }, set: { if !$0 { confirm = nil } }),
                            titleVisibility: .visible) {
            Button("Stop Scanning", role: .destructive) {
                if let path = confirm {
                    model.removeFolder(path)
                    selection = nil
                }
                confirm = nil
            }
            Button("Cancel", role: .cancel) { confirm = nil }
        } message: {
            Text("Its photos leave Tadaima and their thumbnails are deleted from the cache. The folder itself is not touched.")
        }
    }
}

private struct CacheSettings: View {
    @Environment(AppModel.self) private var model
    @State private var usage: (bytes: Int, files: Int) = (0, 0)
    @State private var confirmRegenerate = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 12, verticalSpacing: 8) {
                row("Location") {
                    HStack {
                        Text((CacheLocation.root.path as NSString).abbreviatingWithTildeInPath)
                            .textSelection(.enabled)
                            .lineLimit(1)
                            .truncationMode(.middle)
                        Button("Show in Finder") {
                            try? FileManager.default.createDirectory(at: CacheLocation.root,
                                                                     withIntermediateDirectories: true)
                            Platform.reveal([CacheLocation.root])
                        }
                        .controlSize(.small)
                    }
                }
                row("Photos") { Text(Formats.count(model.photoCount)) }
                row("Thumbnails") {
                    Text("\(Formats.count(usage.files)) files, \(Formats.humanSize(usage.bytes))")
                }
            }
            Text("The cache is safe to delete at any time: Tadaima makes it again. HEIC photos are read by macOS itself.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("Scan for Changes") { model.startScan() }
                    .disabled(model.scannedFolders.isEmpty || model.isScanning)
                Button("Regenerate All Thumbnails\u{2026}") { confirmRegenerate = true }
                    .disabled(model.scannedFolders.isEmpty)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .task(id: model.isScanning) {
            usage = await Task.detached(priority: .utility) { CacheLocation.usage() }.value
        }
        .confirmationDialog("Regenerate all thumbnails?", isPresented: $confirmRegenerate,
                            titleVisibility: .visible) {
            Button("Regenerate", role: .destructive) { model.regenerateCache() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Every thumbnail is deleted and made again from your photos. Your photos are not touched.")
        }
    }

    private func row<V: View>(_ label: String, @ViewBuilder _ value: () -> V) -> some View {
        GridRow {
            Text(label).foregroundStyle(.secondary).gridColumnAlignment(.trailing)
            value()
        }
    }
}

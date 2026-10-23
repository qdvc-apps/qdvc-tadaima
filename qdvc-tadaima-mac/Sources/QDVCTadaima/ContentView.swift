import AppKit
import QuickLook
import SwiftUI
import TadaimaCore

/// The window: the folder sidebar and, beside it, the photo stream (or the
/// viewer, laid over it so the stream keeps its place), with the Info
/// inspector on the right.
struct ContentView: View {
    @Environment(AppModel.self) private var model
    @State private var dropTargeted = false

    var body: some View {
        @Bindable var model = model
        NavigationSplitView {
            SidebarView()
                .navigationSplitViewColumnWidth(min: 200, ideal: 250, max: 380)
        } detail: {
            DetailView()
                .inspector(isPresented: $model.showInspector) {
                    InspectorView()
                        .inspectorColumnWidth(min: 260, ideal: 300, max: 440)
                }
        }
        .navigationTitle("QDVC Tadaima")
        .navigationSubtitle(model.windowSubtitle)
        .toolbar { MainToolbar(model: model) }
        // Folders dropped from Finder are added to the scanned folders.
        .dropDestination(for: URL.self) { urls, _ in
            model.addFolders(urls)  // false (refused) unless a folder was dropped
        } isTargeted: { dropTargeted = $0 }
        .overlay {
            if dropTargeted {
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(Color.accentColor, lineWidth: 3)
                    .padding(4)
                    .allowsHitTesting(false)
            }
        }
        .quickLookPreview($model.quickLookURL, in: model.quickLookItems)
        .alert(model.alert?.title ?? "",
               isPresented: Binding(get: { model.alert != nil }, set: { if !$0 { model.alert = nil } })) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(model.alert?.message ?? "")
        }
        .confirmationDialog(removalTitle,
                            isPresented: Binding(get: { model.confirmRemoval != nil },
                                                 set: { if !$0 { model.confirmRemoval = nil } }),
                            titleVisibility: .visible) {
            Button("Stop Scanning", role: .destructive) {
                if let path = model.confirmRemoval { model.removeFolder(path) }
                model.confirmRemoval = nil
            }
            Button("Cancel", role: .cancel) { model.confirmRemoval = nil }
        } message: {
            Text("Its photos leave Tadaima and their thumbnails are deleted from the cache. "
                 + "The folder and its photos are not touched.")
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            model.appBecameActive()
        }
    }
}

extension ContentView {
    private var removalTitle: String {
        let name = model.confirmRemoval.map { ($0 as NSString).lastPathComponent } ?? ""
        return "Stop scanning \u{201C}\(name)\u{201D}?"
    }
}

/// The stream, its empty states, and the viewer over it.
struct DetailView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ZStack {
            if model.scannedFolders.isEmpty {
                WelcomeView()
            } else if model.stream.isEmpty {
                if model.isScanning || !model.hasScanned {
                    ContentUnavailableView {
                        Label("Looking for Photos", systemImage: "photo.stack")
                    } description: {
                        Text("Photos appear here as their thumbnails are made.")
                    } actions: {
                        ProgressView().controlSize(.small)
                    }
                } else {
                    ContentUnavailableView("No Photos Here", systemImage: "photo.on.rectangle.angled",
                                           description: Text("There are no JPEG, PNG or HEIC photos in this folder."))
                }
            } else {
                // Kept in place under the viewer, so it comes back where it was.
                StreamView()
                    .opacity(model.viewer == nil ? 1 : 0)
                    .disabled(model.viewer != nil)
                    .accessibilityHidden(model.viewer != nil)
            }
            if let viewer = model.viewer {
                ViewerView(state: viewer)
            }
        }
    }
}

struct WelcomeView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ContentUnavailableView {
            Label("Welcome to Tadaima", systemImage: "photo.on.rectangle.angled")
        } description: {
            Text("Add the folders where your photos live, or drag them here. "
                 + "Tadaima only ever reads them: thumbnails go in its own cache.")
        } actions: {
            Button("Add Folder\u{2026}") { model.chooseFoldersToAdd() }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
        }
    }
}

/// The toolbar: Back while viewing a photo; zoom, Show All and Info.
struct MainToolbar: ToolbarContent {
    @Bindable var model: AppModel

    var body: some ToolbarContent {
        ToolbarItem(placement: .navigation) {
            if model.viewer != nil {
                Button { model.closeViewer() } label: {
                    Label("Back to Library", systemImage: "chevron.backward")
                }
                .help("Back to the library (Esc)")
            }
        }
        ToolbarItemGroup(placement: .primaryAction) {
            if model.viewer != nil {
                ControlGroup {
                    Button { model.step(-1) } label: { Label("Previous", systemImage: "chevron.left") }
                        .help("Previous photo (\u{2190})")
                        .disabled((model.viewer?.index ?? 0) == 0)
                    Button { model.step(1) } label: { Label("Next", systemImage: "chevron.right") }
                        .help("Next photo (\u{2192})")
                        .disabled((model.viewer.map { $0.index >= $0.records.count - 1 }) ?? true)
                }
            } else {
                Slider(value: Binding(get: { Double(model.thumbnailSize) },
                                      set: { model.setThumbnailSize(Int($0.rounded())) }),
                       in: Double(Prefs.thumbnailSizes.lowerBound)...Double(Prefs.thumbnailSizes.upperBound),
                       step: Double(Prefs.thumbnailStep)) {
                    Text("Zoom")
                } minimumValueLabel: {
                    Image(systemName: "photo").imageScale(.small)
                } maximumValueLabel: {
                    Image(systemName: "photo").imageScale(.large)
                }
                .frame(width: 150)
                .help("Thumbnail size (\u{2318}+ and \u{2318}\u{2212})")
                .disabled(model.stream.isEmpty)
            }
            if model.isFocused {
                Button { model.showAllFolders() } label: {
                    Label("Show All Folders", systemImage: "arrow.up.left.and.arrow.down.right")
                }
                .help("Stop focusing on \(Paths.basename(model.focus))")
            }
            Button { model.showInspector.toggle() } label: {
                Label("Info", systemImage: "info.circle")
            }
            .help("Show or hide photo information (\u{2318}I)")
        }
    }
}

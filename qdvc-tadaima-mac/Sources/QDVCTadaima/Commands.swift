import AppKit
import SwiftUI
import TadaimaCore

/// Menu-bar commands. Standard items (Edit, Window, Help, Settings…, Quit,
/// Hide, full screen) come from the system; these add the folder, view and
/// photo actions.
@MainActor
struct TadaimaCommands: Commands {
    @Bindable var model: AppModel

    var body: some Commands {
        SidebarCommands()

        CommandGroup(replacing: .newItem) {
            Button("Add Folder\u{2026}") { model.chooseFoldersToAdd() }
                .keyboardShortcut("o")
            Divider()
            Button("Scan for Changes") { model.startScan() }
                .keyboardShortcut("r")
                .disabled(model.scannedFolders.isEmpty || model.isScanning)
        }

        CommandGroup(after: .toolbar) {
            Button(model.showInspector ? "Hide Info" : "Show Info") { model.showInspector.toggle() }
                .keyboardShortcut("i")
            Divider()
            Button("Zoom In") { model.zoomIn() }
                .keyboardShortcut("+")
                .disabled(model.thumbnailSize >= Prefs.thumbnailSizes.upperBound)
            Button("Zoom Out") { model.zoomOut() }
                .keyboardShortcut("-")
                .disabled(model.thumbnailSize <= Prefs.thumbnailSizes.lowerBound)
            Button("Actual Size") { model.actualSize() }
                .keyboardShortcut("0")
            Divider()
            Button("Focus on Current Folder") { model.focusOnCurrentFolder() }
                .disabled(model.currentFolder == nil || model.currentFolder == model.focus)
            Button("Focus on Enclosing Folder") { model.focusOnEnclosingFolder() }
                .keyboardShortcut(.upArrow, modifiers: .command)
                .disabled(!model.isFocused)
            Button("Show All Folders") { model.showAllFolders() }
                .keyboardShortcut(.upArrow, modifiers: [.command, .shift])
                .disabled(!model.isFocused)
            Divider()
        }

        CommandMenu("Photo") {
            let photo = model.activePhoto
            Button("Open") { model.openSelected() }
                .keyboardShortcut(.downArrow, modifiers: .command)
                .disabled(model.viewer != nil || model.selectedRecord == nil)
            Button("Back to Library") { model.closeViewer() }
                .keyboardShortcut("[", modifiers: .command)
                .disabled(model.viewer == nil)
            Divider()
            Button("Quick Look") { model.quickLook() }
                .keyboardShortcut("y")
                .disabled(photo == nil)
            Button("Show in Finder") { model.showInFinder() }
                .keyboardShortcut("r", modifiers: [.command, .option])
                .disabled(photo == nil)
            Button("Open with Default App") { model.openInDefaultApp() }
                .disabled(photo == nil)
            Button("Copy Photo") { model.copyPhoto() }
                .keyboardShortcut("c", modifiers: [.command, .option])
                .disabled(photo == nil)
        }
    }
}

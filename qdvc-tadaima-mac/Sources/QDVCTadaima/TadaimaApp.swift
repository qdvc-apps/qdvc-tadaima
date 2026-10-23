import AppKit
import SwiftUI

@main
struct TadaimaApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @State private var model = AppModel()

    var body: some Scene {
        // One window, like Photos.
        Window("QDVC Tadaima", id: "main") {
            ContentView()
                .environment(model)
                .frame(minWidth: 760, minHeight: 480)
                .onAppear {
                    model.startUp()
                    appDelegate.attach(model)
                }
        }
        .defaultSize(width: 1280, height: 820)
        .commands {
            TadaimaCommands(model: model)
        }

        Settings {
            SettingsView()
                .environment(model)
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private weak var model: AppModel?
    /// Folders handed over before the window was up.
    private var pendingFolders: [URL] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Needed when launched as a bare executable (`swift run`) rather than
        // from the .app bundle, so the app gets a Dock icon and menu bar.
        NSApp.setActivationPolicy(.regular)
        NSApp.activate()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        model?.persistNow()
    }

    /// Folders dropped on the Dock icon, or opened with "Open With": add
    /// them to the scanned folders.
    func application(_ application: NSApplication, open urls: [URL]) {
        if let model {
            model.addFolders(urls)
        } else {
            pendingFolders += urls
        }
    }

    func attach(_ model: AppModel) {
        self.model = model
        if !pendingFolders.isEmpty {
            model.addFolders(pendingFolders)
            pendingFolders = []
        }
    }
}

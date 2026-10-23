import AppKit
import SwiftUI
import TadaimaCore

/// The Info inspector (⌘I): the selected photo's (or the viewer's) file
/// details and camera details. No picture: the grid and viewer show that.
struct InspectorView: View {
    @Environment(AppModel.self) private var model
    @State private var details: PhotoDetails?
    @State private var detailsPath: String?

    var body: some View {
        Group {
            if let rec = model.activePhoto {
                Form {
                    Section("File") {
                        LabeledContent("Name") { value(rec.name) }
                        LabeledContent("Date Taken") { value(loaded(rec)?.dateText ?? "\u{2026}") }
                        LabeledContent("Dimensions") { value(loaded(rec)?.dimensions ?? "\u{2026}") }
                        LabeledContent("Size") {
                            value(loaded(rec).map { Formats.humanSize($0.sizeBytes) } ?? "\u{2026}")
                        }
                        LabeledContent("Folder") { value((rec.folder as NSString).abbreviatingWithTildeInPath) }
                        Button("Show in Finder") { Platform.reveal([URL(fileURLWithPath: rec.path)]) }
                    }
                    Section("Camera") {
                        if let d = loaded(rec) {
                            if d.camera.isEmpty {
                                Text("No camera details in this photo.")
                                    .foregroundStyle(.secondary)
                            }
                            ForEach(d.camera) { row in
                                LabeledContent(row.label) { value(row.value) }
                            }
                            if let place = d.location {
                                Button("Show in Maps") {
                                    Platform.showInMaps(latitude: place.latitude, longitude: place.longitude,
                                                        name: rec.name)
                                }
                                .help("Opens Maps at this spot (the only time Tadaima sends anything about a photo off your Mac)")
                            }
                        } else {
                            ProgressView().controlSize(.small)
                        }
                    }
                }
                .formStyle(.grouped)
                .task(id: rec.versionKey) {
                    let path = rec.path
                    let read = await Task.detached(priority: .userInitiated) { PhotoDetails.read(path) }.value
                    details = read
                    detailsPath = path
                }
            } else {
                ContentUnavailableView("No Photo Selected", systemImage: "info.circle",
                                       description: Text("Select a photo to see its details."))
            }
        }
    }

    /// The details, if they are this photo's (not the previous one's).
    private func loaded(_ rec: ImageRecord) -> PhotoDetails? {
        detailsPath == rec.path ? details : nil
    }

    private func value(_ text: String) -> some View {
        Text(text)
            .multilineTextAlignment(.trailing)
            .textSelection(.enabled)
    }
}

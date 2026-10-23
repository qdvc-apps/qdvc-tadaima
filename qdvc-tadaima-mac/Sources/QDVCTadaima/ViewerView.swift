import AppKit
import SwiftUI
import TadaimaCore

/// One photo, fitted to the window, with a caption beneath and a filmstrip
/// of its folder along the bottom. ← and → step through the folder; Esc (or
/// Back) returns to the stream, which is where you left it.
struct ViewerView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.displayScale) private var displayScale
    let state: ViewerState
    @State private var image: CGImage?
    @State private var shownPath: String?
    @State private var details: PhotoDetails?
    @FocusState private var focused: Bool

    var body: some View {
        let rec = state.current
        VStack(spacing: 0) {
            GeometryReader { geo in
                let pixels = Self.bucket(Double(max(geo.size.width, geo.size.height)) * displayScale)
                ZStack {
                    if let image {
                        Image(decorative: image, scale: displayScale)
                            .resizable()
                            .interpolation(.high)
                            .scaledToFit()
                            .shadow(color: .black.opacity(0.3), radius: 8, y: 3)
                            .padding(20)
                    }
                    if shownPath != rec.path {
                        ProgressView().controlSize(.small)
                    }
                }
                .frame(width: geo.size.width, height: geo.size.height)
                .contentShape(Rectangle())
                .onTapGesture(count: 2) { model.closeViewer() }
                .contextMenu { PhotoMenu(record: rec) }
                .task(id: "\(rec.versionKey)|\(pixels)") {
                    // The previous photo stays up until this one is ready.
                    if let loaded = await ImageCache.shared.screen(rec, pixels: pixels) {
                        image = loaded
                        shownPath = rec.path
                    }
                    // Have the neighbours ready for → and ←.
                    for i in [state.index + 1, state.index - 1] where state.records.indices.contains(i) {
                        if Task.isCancelled { break }
                        _ = await ImageCache.shared.screen(state.records[i], pixels: pixels)
                    }
                }
            }
            caption(rec)
            Divider()
            Filmstrip(state: state)
        }
        .background(.background)
        .focusable()
        .focused($focused)
        .focusEffectDisabled()
        .onKeyPress(.leftArrow, phases: [.down, .repeat]) { _ in
            model.step(-1)
            return .handled
        }
        .onKeyPress(.rightArrow, phases: [.down, .repeat]) { _ in
            model.step(1)
            return .handled
        }
        .onKeyPress(.space) {
            model.quickLook()
            return .handled
        }
        .onExitCommand { model.closeViewer() }
        .onAppear { focused = true }
        .task(id: rec.path) {
            let path = rec.path
            details = nil
            details = await Task.detached(priority: .userInitiated) { PhotoDetails.read(path) }.value
        }
    }

    private func caption(_ rec: ImageRecord) -> some View {
        var parts = [rec.name]
        if let details {
            parts += [details.dateText, details.dimensions, Formats.humanSize(details.sizeBytes)]
        }
        parts.append("\(state.index + 1) of \(state.records.count)")
        return Text(parts.joined(separator: "  \u{00B7}  "))
            .font(.callout)
            .foregroundStyle(.secondary)
            .lineLimit(1)
            .truncationMode(.middle)
            .textSelection(.enabled)
            .padding(.horizontal, 16)
            .padding(.vertical, 7)
            .frame(maxWidth: .infinity)
    }

    /// Decode at a few fixed sizes, not one per window size.
    static func bucket(_ needed: Double) -> Int {
        let step = 512.0
        return Int(min(8192, max(step, (needed / step).rounded(.up) * step)))
    }
}

/// The folder's photos as small squares; the current one is outlined and
/// kept in view. Click one to show it.
struct Filmstrip: View {
    @Environment(AppModel.self) private var model
    let state: ViewerState

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: 6) {
                    ForEach(Array(state.records.enumerated()), id: \.element.path) { i, rec in
                        FilmstripThumb(record: rec, current: i == state.index)
                            .onTapGesture { model.showInViewer(index: i) }
                            .id(rec.path)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
            }
            .frame(height: 72)
            .onChange(of: state.index) { _, _ in
                withAnimation(.easeOut(duration: 0.15)) { proxy.scrollTo(state.current.path, anchor: .center) }
            }
            .onAppear { proxy.scrollTo(state.current.path, anchor: .center) }
        }
        .background(.bar)
    }
}

struct FilmstripThumb: View {
    let record: ImageRecord
    let current: Bool
    @State private var image: CGImage?

    var body: some View {
        ZStack {
            if let image {
                Image(decorative: image, scale: 2).resizable()
            } else {
                Rectangle().fill(.quaternary)
            }
        }
        .frame(width: 56, height: 56)
        .clipShape(RoundedRectangle(cornerRadius: 3))
        .overlay {
            if current {
                RoundedRectangle(cornerRadius: 4)
                    .strokeBorder(Color.accentColor, lineWidth: 2.5)
                    .padding(-3)
            }
        }
        .opacity(current ? 1 : 0.8)
        .help(record.name)
        .task(id: record.versionKey) { image = await ImageCache.shared.square(record) }
    }
}

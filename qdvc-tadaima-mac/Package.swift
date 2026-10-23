// swift-tools-version: 5.10
//
// QDVC Tadaima for macOS — a native SwiftUI edition of the read-only photo
// browser. It shows the photos in folders you choose as one continuous,
// sectioned stream, with a folder sidebar that follows along as you scroll.
// It never writes to those folders: thumbnails and its index live in its
// own cache. The core is a port of the GTK 4 edition's pure Python modules
// (../qdvc-tadaima-gtk4/qdvc), checked against them by a parity fixture.
//
// Build:   swift build            (or open this folder in Xcode)
// Test:    swift test
// Bundle:  scripts/build-app.sh   (ad-hoc signed .app, no Apple account needed)

import PackageDescription

var products: [Product] = [
    .library(name: "TadaimaCore", targets: ["TadaimaCore"]),
]
var targets: [Target] = [
    // Pure model layer: Foundation only, no AppKit/SwiftUI, unit-testable.
    .target(name: "TadaimaCore"),
    .testTarget(
        name: "TadaimaCoreTests",
        dependencies: ["TadaimaCore"],
        resources: [.copy("Fixtures")]
    ),
]

#if os(macOS)
// The SwiftUI/AppKit front-end. Declared on macOS only, so the core and its
// tests also build with a Linux Swift toolchain (docs/MAINTENANCE.md).
products.insert(.executable(name: "QDVCTadaima", targets: ["QDVCTadaima"]), at: 0)
targets.insert(.executableTarget(name: "QDVCTadaima", dependencies: ["TadaimaCore"]), at: 1)
#endif

let package = Package(
    name: "QDVCTadaima",
    platforms: [.macOS(.v14)],
    products: products,
    targets: targets
)

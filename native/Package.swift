// swift-tools-version: 6.4
import PackageDescription

let package = Package(
    name: "FreeCCRNative",
    platforms: [
        .macOS(.v14)
    ],
    dependencies: [
        .package(url: "https://github.com/pvieito/PythonKit.git", branch: "main"),
    ],
    targets: [
        // Embedded-Python bridge: owns the interpreter bootstrap and the
        // single serial queue every PythonKit call must go through (see
        // Phase 1 finding re: GIL). No SwiftUI/Metal here, so it can be unit
        // tested on its own.
        .target(
            name: "PythonBridge",
            dependencies: [
                .product(name: "PythonKit", package: "PythonKit"),
            ]
        ),
        // The actual SwiftUI app: Metal preview canvas + a sliders panel —
        // Phase 3's first slice (image_preview.py + sliders_panel.py analogs).
        .executableTarget(
            name: "FreeCCRNative",
            dependencies: ["PythonBridge"]
        ),
        .testTarget(
            name: "FreeCCRNativeTests",
            dependencies: ["FreeCCRNative"]
        ),
    ]
)

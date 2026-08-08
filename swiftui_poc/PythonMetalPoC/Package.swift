// swift-tools-version: 6.4
import PackageDescription

let package = Package(
    name: "PythonMetalPoC",
    platforms: [
        .macOS(.v13)
    ],
    dependencies: [
        .package(url: "https://github.com/pvieito/PythonKit.git", branch: "main"),
    ],
    targets: [
        .executableTarget(
            name: "PythonMetalPoC",
            dependencies: [
                .product(name: "PythonKit", package: "PythonKit"),
            ]
        ),
        .testTarget(
            name: "PythonMetalPoCTests",
            dependencies: ["PythonMetalPoC"]
        ),
    ]
)

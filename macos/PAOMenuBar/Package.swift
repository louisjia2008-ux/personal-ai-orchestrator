// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "PAOMenuBar",
    platforms: [.macOS(.v13)],
    targets: [
        .target(
            name: "PAOControlKit",
            path: "Sources/PAOControlKit"
        ),
        .executableTarget(
            name: "PAOMenuBar",
            dependencies: ["PAOControlKit"],
            path: "Sources/PAOMenuBar"
        ),
        .testTarget(
            name: "PAOControlKitTests",
            dependencies: ["PAOControlKit"],
            path: "Tests/PAOControlKitTests"
        ),
    ]
)

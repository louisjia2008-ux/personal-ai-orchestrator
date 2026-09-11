// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "PAOMenuBar",
    defaultLocalization: "en",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "PAOControlKit", targets: ["PAOControlKit"]),
        .executable(name: "PAOWidgetExtension", targets: ["PAOWidgetExtension"]),
    ],
    targets: [
        .target(
            name: "PAOControlKit",
            path: "Sources/PAOControlKit",
            resources: [
                // .copy preserves the canonical "zh-Hans.lproj" casing; .process
                // lowercases it, which CFBundle then fails to match case-sensitively.
                .copy("Resources/en.lproj"),
                .copy("Resources/zh-Hans.lproj"),
            ]
        ),
        .executableTarget(
            name: "PAOMenuBar",
            dependencies: ["PAOControlKit"],
            path: "Sources/PAOMenuBar"
        ),
        .executableTarget(
            name: "PAOWidgetExtension",
            dependencies: ["PAOControlKit"],
            path: "Sources/PAOWidgetExtension"
        ),
        .testTarget(
            name: "PAOControlKitTests",
            dependencies: ["PAOControlKit"],
            path: "Tests/PAOControlKitTests",
            resources: [
                // The routing fixtures are the executable half of
                // docs/ROUTING_ROLE_CONTRACT.md. .copy keeps the directory
                // structure so they stay readable as wire examples.
                .copy("Fixtures"),
            ]
        ),
    ]
)

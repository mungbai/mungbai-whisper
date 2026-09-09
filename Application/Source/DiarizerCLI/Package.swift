// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ProjectWhisperDiarizer",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(path: "../../../Library/Vendor/FluidAudio")
    ],
    targets: [
        .executableTarget(
            name: "ProjectWhisperDiarizer",
            dependencies: [
                .product(name: "FluidAudio", package: "FluidAudio")
            ],
            path: "Sources"
        )
    ]
)

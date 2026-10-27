// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "TGSignals",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "TGSignals", targets: ["SignalsApp"])],
    targets: [
        .target(name: "SignalsCore", resources: [.process("Resources/discovered-companies.json")]),
        .executableTarget(name: "SignalsApp", dependencies: ["SignalsCore"], resources: [.copy("Resources/Board")]),
        .testTarget(name: "SignalsCoreTests", dependencies: ["SignalsCore"])
    ],
    swiftLanguageModes: [.v5]
)

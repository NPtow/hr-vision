import SwiftUI
import AppKit

@main
struct TGSignalsApp: App {
    @NSApplicationDelegateAdaptor(SignalsAppDelegate.self) private var delegate
    @StateObject private var workspace = SignalsWorkspace()

    var body: some Scene {
        Window("TG Рассылщик", id: "workspace") {
            WorkspaceView()
                .environmentObject(workspace)
                .frame(minWidth: 1000, minHeight: 680)
                .tint(Color(red: 0.22, green: 0.35, blue: 0.77))
        }
        .defaultSize(width: 1360, height: 880)
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .newItem) { }
            CommandGroup(after: .newItem) {
                Button("Сервисы поиска") { workspace.showSources = true }
                    .keyboardShortcut("s", modifiers: [.command, .shift])
                Button("История Telegram") { workspace.showTelemetr = true }
                    .keyboardShortcut("t", modifiers: [.command, .shift])
            }
            CommandGroup(after: .help) {
                Button("О приложении") { workspace.showAbout = true }
            }
        }
    }
}

final class SignalsAppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.regular)
        NSApplication.shared.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

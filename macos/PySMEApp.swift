// The macOS shell of PySME-GUI.
//
// All science and the whole interface live in the Python package. This program
// only does what needs to be native: set up the private Python environment on
// first launch, start the local server, show it in a window, and provide file
// panels, menus and document opening.

import Cocoa
import WebKit

let appName = "PySME"

struct Paths {
    let home: URL
    let resources: URL

    var source: URL { resources.appendingPathComponent("app") }
    var uv: URL { resources.appendingPathComponent("bin/uv") }
    var current: URL { home.appendingPathComponent("current.json") }
    var settings: URL { home.appendingPathComponent("settings.json") }
    var log: URL { home.appendingPathComponent("logs/app.log") }

    func slot(_ id: String) -> URL { home.appendingPathComponent("slots/\(id)") }
}

func readJSON(_ url: URL) -> [String: Any] {
    guard let data = try? Data(contentsOf: url),
          let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else { return [:] }
    return object
}

/// A JavaScript literal for a string, or "null".
func jsString(_ value: String?) -> String {
    guard let value = value,
          let data = try? JSONSerialization.data(withJSONObject: [value]),
          let text = String(data: data, encoding: .utf8)
    else { return "null" }
    return String(text.dropFirst().dropLast())
}

/// Collects child process output and hands over complete lines.
final class LineReader {
    private var buffer = Data()
    private let lock = NSLock()
    private let onLine: (String) -> Void

    init(_ pipe: Pipe, onLine: @escaping (String) -> Void) {
        self.onLine = onLine
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; return }
            self?.append(data)
        }
    }

    private func append(_ data: Data) {
        lock.lock(); defer { lock.unlock() }
        buffer.append(data)
        while let end = buffer.firstIndex(of: 0x0A) {
            let line = String(decoding: buffer[buffer.startIndex..<end], as: UTF8.self)
            buffer.removeSubrange(buffer.startIndex...end)
            DispatchQueue.main.async { self.onLine(line) }
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate,
    WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {

    var paths: Paths!
    var window: NSWindow!
    var web: WKWebView!
    var setupView: NSView!
    var statusLabel: NSTextField!
    var spinner: NSProgressIndicator!
    var logView: NSTextView!

    var installer: Process?
    var server: Process?
    var readers: [LineReader] = []
    var serverURL: URL?
    var pageReady = false
    var pendingFile: String?
    var recent: [String] = []
    var logHandle: FileHandle?
    var startGeneration = 0
    var quitConfirmed = false

    let environment = ProcessInfo.processInfo.environment

    // MARK: - launch

    func applicationDidFinishLaunching(_ notification: Notification) {
        let home: URL
        if let custom = environment["PYSME_GUI_HOME"], !custom.isEmpty {
            home = URL(fileURLWithPath: custom)
        } else {
            home = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("PySME-GUI")
        }
        paths = Paths(home: home, resources: Bundle.main.resourceURL!)
        openLog()
        buildMenu()
        buildWindow()
        NSApp.activate(ignoringOtherApps: true)
        boot()
    }

    func openLog() {
        let directory = paths.log.deletingLastPathComponent()
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let size = (try? FileManager.default.attributesOfItem(atPath: paths.log.path))?[.size] as? Int ?? 0
        if !FileManager.default.fileExists(atPath: paths.log.path) || size > 2_000_000 {
            FileManager.default.createFile(atPath: paths.log.path, contents: nil)
        }
        logHandle = try? FileHandle(forWritingTo: paths.log)
        _ = try? logHandle?.seekToEnd()
    }

    func record(_ line: String, show: Bool = false) {
        // The access token must not end up in a log file.
        let safe = line.hasPrefix("PYSME_GUI_URL") ? "PYSME_GUI_URL (hidden)" : line
        logHandle?.write(Data((safe + "\n").utf8))
        recent.append(safe)
        if recent.count > 60 { recent.removeFirst() }
        if show {
            logView.textStorage?.append(NSAttributedString(string: safe + "\n", attributes: [
                .font: NSFont.monospacedSystemFont(ofSize: 11, weight: .regular),
                .foregroundColor: NSColor.secondaryLabelColor,
            ]))
            logView.scrollToEndOfDocument(nil)
        }
    }

    func buildWindow() {
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let size = NSSize(width: min(1500, screen.width * 0.94), height: min(960, screen.height * 0.94))
        window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = appName
        window.minSize = NSSize(width: 900, height: 600)
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()
        window.setFrameAutosaveName("PySMEMainWindow")

        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(self, name: "native")
        web = WKWebView(frame: .zero, configuration: configuration)
        web.navigationDelegate = self
        web.uiDelegate = self

        spinner = NSProgressIndicator()
        spinner.style = .spinning
        statusLabel = NSTextField(wrappingLabelWithString: "")
        statusLabel.font = .systemFont(ofSize: 15, weight: .medium)
        statusLabel.alignment = .center
        let scroll = NSTextView.scrollableTextView()
        logView = (scroll.documentView as! NSTextView)
        logView.isEditable = false

        let stack = NSStackView(views: [spinner, statusLabel, scroll])
        stack.orientation = .vertical
        stack.spacing = 14
        stack.translatesAutoresizingMaskIntoConstraints = false
        setupView = NSView()
        setupView.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: setupView.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: setupView.centerYAnchor),
            stack.widthAnchor.constraint(equalToConstant: 640),
            statusLabel.widthAnchor.constraint(equalTo: stack.widthAnchor),
            scroll.widthAnchor.constraint(equalTo: stack.widthAnchor),
            scroll.heightAnchor.constraint(equalToConstant: 220),
        ])
        window.contentView = setupView
        window.makeKeyAndOrderFront(nil)
    }

    func showSetup(_ text: String) {
        statusLabel.stringValue = text
        spinner.startAnimation(nil)
        if window.contentView !== setupView { window.contentView = setupView }
    }

    // MARK: - environment

    func activeSlot() -> (id: String, info: [String: Any])? {
        guard let id = readJSON(paths.current)["current"] as? String else { return nil }
        let info = readJSON(paths.slot(id).appendingPathComponent("slot.json"))
        let python = paths.slot(id).appendingPathComponent("venv/bin/python").path
        if info.isEmpty || !FileManager.default.isExecutableFile(atPath: python) { return nil }
        return (id, info)
    }

    var bundleVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0"
    }

    var bundleID: String {
        bundleVersion + "+" + (Bundle.main.infoDictionary?["PySMEBuild"] as? String ?? "0")
    }

    func childEnvironment() -> [String: String] {
        var env = environment
        for key in ["PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"] { env.removeValue(forKey: key) }
        env["PYSME_GUI_HOME"] = paths.home.path
        env["PYSME_GUI_UV"] = paths.uv.path
        env["UV_PYTHON_INSTALL_DIR"] = paths.home.appendingPathComponent("python").path
        env["UV_CACHE_DIR"] = paths.home.appendingPathComponent("cache").path
        env["PYTHONUNBUFFERED"] = "1"
        env["MPLBACKEND"] = "Agg"
        return env
    }

    func boot() {
        serverURL = nil
        pageReady = false
        recent = []
        logView.string = ""
        record("--- start \(Date()) build \(bundleID) ---")

        guard let slot = activeSlot() else {
            install(reason: "Setting up PySME for the first time.\nThis downloads Python and PySME (about 200 MB) and happens only once.")
            return
        }
        // A newer app dragged into Applications brings newer GUI code with it.
        let installedFrom = readJSON(paths.current)["bundle_id"] as? String
        let slotGUI = slot.info["gui_version"] as? String ?? "0"
        if installedFrom != bundleID,
           bundleVersion.compare(slotGUI, options: .numeric) != .orderedAscending {
            install(reason: "Updating PySME to match this version of the app…")
            return
        }
        startServer(slot: slot.id)
    }

    func install(reason: String) {
        showSetup(reason)
        let process = Process()
        process.executableURL = paths.uv
        process.arguments = [
            "run", "--no-project", "--managed-python", "--python", "3.12",
            paths.source.appendingPathComponent("pysme_gui/envmanager.py").path,
            "install", "--source", paths.source.path, "--bundle-id", bundleID,
        ]
        var env = childEnvironment()
        env["PYTHONDONTWRITEBYTECODE"] = "1"   // keep the signed bundle unchanged
        process.environment = env
        process.currentDirectoryURL = paths.home
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        readers = [LineReader(pipe) { [weak self] line in
            guard let self = self else { return }
            self.record(line, show: true)
            if line.hasPrefix("STEP ") { self.statusLabel.stringValue = reason + "\n\nStep " + line.dropFirst(5) }
        }]
        process.terminationHandler = { [weak self] finished in
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                guard let self = self, self.installer === finished else { return }
                self.installer = nil
                if finished.terminationStatus == 0, let slot = self.activeSlot() {
                    self.startServer(slot: slot.id)
                } else {
                    self.installFailed()
                }
            }
        }
        installer = process
        do { try process.run() } catch {
            installer = nil
            record("Could not start the installer: \(error.localizedDescription)", show: true)
            installFailed()
        }
    }

    func installFailed() {
        spinner.stopAnimation(nil)
        let old = activeSlot()
        var buttons = ["Try again"]
        if old != nil { buttons.append("Use the installed version") }
        buttons.append("Quit")
        let choice = ask("Setup did not finish",
                         "Check the internet connection and try again.\n\n" + tail(), buttons)
        if choice == "Try again" { boot() }
        else if choice == "Use the installed version", let slot = old { startServer(slot: slot.id) }
        else { NSApp.terminate(nil) }
    }

    func tail() -> String { recent.suffix(10).joined(separator: "\n") }

    func ask(_ title: String, _ text: String, _ buttons: [String]) -> String {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = text
        buttons.forEach { alert.addButton(withTitle: $0) }
        let index = alert.runModal().rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue
        return buttons.indices.contains(index) ? buttons[index] : buttons.last!
    }

    func startServer(slot: String) {
        showSetup("Starting PySME…")
        let custom = readJSON(paths.settings)["python"] as? String
        let python = (custom?.isEmpty == false)
            ? custom!
            : paths.slot(slot).appendingPathComponent("venv/bin/python").path
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        var arguments = ["-m", "pysme_gui", "--app-mode", "--parent-pid", String(getpid())]
        if let file = pendingFile {
            arguments.append(file)
            pendingFile = nil
        }
        process.arguments = arguments
        var env = childEnvironment()
        env["PYTHONPATH"] = paths.slot(slot).appendingPathComponent("app").path
        process.environment = env
        process.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        startGeneration += 1
        let generation = startGeneration
        readers = [LineReader(pipe) { [weak self] line in
            guard let self = self, generation == self.startGeneration else { return }
            self.record(line)
            if line.hasPrefix("PYSME_GUI_URL "), let url = URL(string: String(line.dropFirst(14))) {
                self.serverURL = url
                self.web.load(URLRequest(url: url))
            }
        }]
        process.terminationHandler = { [weak self] finished in
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                guard let self = self, self.server === finished else { return }
                self.server = nil
                self.serverStopped(custom: custom)
            }
        }
        server = process
        do { try process.run() } catch {
            server = nil
            record("Could not start \(python): \(error.localizedDescription)")
            serverStopped(custom: custom)
            return
        }
        // The first import after an install compiles bytecode and can be slow.
        DispatchQueue.main.asyncAfter(deadline: .now() + 180) { [weak self] in
            guard let self = self, generation == self.startGeneration, self.serverURL == nil else { return }
            self.server?.terminate()
        }
    }

    func serverStopped(custom: String?) {
        spinner.stopAnimation(nil)
        showSetup("The PySME process stopped.")
        spinner.stopAnimation(nil)
        var buttons = ["Restart"]
        if custom?.isEmpty == false { buttons.append("Use the app's own PySME") }
        if readJSON(paths.current)["previous"] is String { buttons.append("Go back to previous version") }
        buttons.append(contentsOf: ["Reinstall", "Quit"])
        switch ask("PySME stopped unexpectedly", tail(), buttons) {
        case "Restart":
            boot()
        case "Use the app's own PySME":
            var settings = readJSON(paths.settings)
            settings.removeValue(forKey: "python")
            if let data = try? JSONSerialization.data(withJSONObject: settings) {
                try? data.write(to: paths.settings, options: .atomic)
            }
            boot()
        case "Go back to previous version":
            var record = readJSON(paths.current)
            let previous = record["previous"]
            record["previous"] = record["current"]
            record["current"] = previous
            if let data = try? JSONSerialization.data(withJSONObject: record) {
                try? data.write(to: paths.current, options: .atomic)
            }
            boot()
        case "Reinstall":
            install(reason: "Reinstalling PySME…")
        default:
            NSApp.terminate(nil)
        }
    }

    func stopServer() {
        guard let process = server else { return }
        server = nil
        process.terminate()
        let deadline = Date().addingTimeInterval(3)
        while process.isRunning && Date() < deadline { usleep(50_000) }
        if process.isRunning { kill(process.processIdentifier, SIGKILL) }
    }

    // MARK: - web view

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        spinner.stopAnimation(nil)
        window.contentView = web
        window.makeFirstResponder(web)
        pageReady = true
        if let file = pendingFile {
            pendingFile = nil
            // The page needs a moment to receive its first state.
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { self.openInPage(file) }
        }
        if let target = environment["PYSME_GUI_SELFTEST"] { runSelfTest(into: target) }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { return decisionHandler(.cancel) }
        if url.host == "127.0.0.1" && url.port == serverURL?.port {
            decisionHandler(.allow)
        } else if url.scheme == "https" || url.scheme == "http" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        } else {
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        _ = ask(appName, message, ["OK"])
        completionHandler()
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if let url = serverURL { web.load(URLRequest(url: url)) }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        // Only the local interface may use the native bridge.
        guard message.frameInfo.securityOrigin.host == "127.0.0.1",
              let body = message.body as? [String: Any],
              let type = body["type"] as? String
        else { return }
        let id = body["id"] as? Int ?? 0
        let title = body["title"] as? String ?? ""
        let name = body["name"] as? String ?? ""
        switch type {
        case "open":
            let panel = NSOpenPanel()
            panel.message = title
            panel.canChooseDirectories = false
            panel.allowsMultipleSelection = false
            panel.treatsFilePackagesAsDirectories = true
            panel.beginSheetModal(for: window) { response in
                self.reply(id, response == .OK ? panel.url?.path : nil)
            }
        case "save":
            let panel = NSSavePanel()
            panel.message = title
            panel.nameFieldStringValue = name
            panel.beginSheetModal(for: window) { response in
                self.reply(id, response == .OK ? panel.url?.path : nil)
            }
        case "savePng":
            guard let text = body["data"] as? String, let data = Data(base64Encoded: text) else { return }
            let panel = NSSavePanel()
            panel.nameFieldStringValue = name
            panel.beginSheetModal(for: window) { response in
                if response == .OK, let url = panel.url { try? data.write(to: url) }
            }
        case "restart":
            startGeneration += 1
            stopServer()
            boot()
        default:
            break
        }
    }

    func reply(_ id: Int, _ path: String?) {
        web.evaluateJavaScript("window.__nativeReply(\(id), \(jsString(path)))")
    }

    func openInPage(_ path: String) {
        web.evaluateJavaScript("window.openFromFinder(\(jsString(path)))")
    }

    // MARK: - self test

    /// Used by the build script: run the solar example inside the real WebKit
    /// window, write a screenshot and a short report, then quit.
    func runSelfTest(into directory: String) {
        let script = """
        const wait = (ms) => new Promise((r) => setTimeout(r, ms));
        const errors = [];
        window.addEventListener('error', (e) => errors.push(String(e.message)));
        for (let i = 0; i < 100 && !(S && options.atmospheres.length); i++) await wait(200);
        await send('example');
        await send('run', { kind: 'synth' });
        for (let i = 0; i < 600 && S.job.state === 'running'; i++) await wait(500);
        await wait(1500);
        return JSON.stringify({
          native: NATIVE, job: S.job.state, error: S.job.error || null,
          pixels: spectrum.segments.reduce((n, s) => n + s.wave.length, 0),
          minFlux: Math.min(...(spectrum.segments[0]?.synth || [NaN])),
          traces: document.querySelector('#plot').data?.length || 0,
          atmospheres: options.atmospheres.length, errors,
        });
        """
        let output = URL(fileURLWithPath: directory)
        try? FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
            self.web.callAsyncJavaScript(script, arguments: [:], in: nil, in: .page) { result in
                var report: String
                switch result {
                case .success(let value): report = value as? String ?? "no result"
                case .failure(let error): report = "{\"failure\": \(jsString(error.localizedDescription))}"
                }
                try? report.write(to: output.appendingPathComponent("report.json"), atomically: true, encoding: .utf8)
                self.web.takeSnapshot(with: nil) { image, _ in
                    if let tiff = image?.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
                       let png = rep.representation(using: .png, properties: [:]) {
                        try? png.write(to: output.appendingPathComponent("window.png"))
                    }
                    self.quitConfirmed = true
                    NSApp.terminate(nil)
                }
            }
        }
    }

    // MARK: - documents and quitting

    func application(_ application: NSApplication, open urls: [URL]) {
        guard let path = urls.first?.path else { return }
        if pageReady { openInPage(path) } else { pendingFile = path }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        NSApp.terminate(nil)
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitConfirmed || !pageReady || server == nil { return .terminateNow }
        web.evaluateJavaScript("window.hasUnsavedChanges()") { value, _ in
            if (value as? Bool) == true,
               self.ask("Quit without saving?", "Changes that were not saved will be lost.", ["Quit", "Cancel"]) == "Cancel" {
                sender.reply(toApplicationShouldTerminate: false)
            } else {
                sender.reply(toApplicationShouldTerminate: true)
            }
        }
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let process = installer, process.isRunning {
            installer = nil
            process.terminate()
        }
        stopServer()
    }

    // MARK: - menus

    @objc func command(_ sender: NSMenuItem) {
        guard pageReady, let name = sender.representedObject as? String else { return }
        web.evaluateJavaScript("window.guiCommand(\(jsString(name)))")
    }

    @objc func reload(_ sender: Any?) {
        if let url = serverURL { web.load(URLRequest(url: url)) }
    }

    @objc func showDataFolder(_ sender: Any?) {
        NSWorkspace.shared.open(paths.home)
    }

    @objc func repair(_ sender: Any?) {
        if installer != nil { return }
        if ask("Reinstall PySME?", "A fresh environment is built. Unsaved changes in the window are lost. Your files and ~/.sme are not touched.", ["Reinstall", "Cancel"]) == "Reinstall" {
            startGeneration += 1
            stopServer()
            pageReady = false
            install(reason: "Reinstalling PySME…")
        }
    }

    func buildMenu() {
        let main = NSMenu()

        func menu(_ title: String) -> NSMenu {
            let item = NSMenuItem()
            let submenu = NSMenu(title: title)
            item.submenu = submenu
            main.addItem(item)
            return submenu
        }
        @discardableResult
        func add(_ menu: NSMenu, _ title: String, _ action: Selector?, _ key: String = "",
                 _ modifiers: NSEvent.ModifierFlags = .command, target: AnyObject? = nil) -> NSMenuItem {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
            item.keyEquivalentModifierMask = modifiers
            item.target = target
            menu.addItem(item)
            return item
        }
        func gui(_ menu: NSMenu, _ title: String, _ name: String, _ key: String = "",
                 _ modifiers: NSEvent.ModifierFlags = .command) {
            add(menu, title, #selector(command(_:)), key, modifiers, target: self).representedObject = name
        }

        let app = menu(appName)
        add(app, "About \(appName)", #selector(NSApplication.orderFrontStandardAboutPanel(_:)))
        app.addItem(.separator())
        gui(app, "Updates & Settings…", "settings", ",")
        app.addItem(.separator())
        add(app, "Hide \(appName)", #selector(NSApplication.hide(_:)), "h")
        add(app, "Quit \(appName)", #selector(NSApplication.terminate(_:)), "q")

        let file = menu("File")
        gui(file, "New", "new", "n")
        gui(file, "Open…", "open", "o")
        gui(file, "Import Observed Spectrum…", "import", "i")
        gui(file, "Load VALD Line List…", "linelist", "l")
        file.addItem(.separator())
        gui(file, "Save", "save", "s")
        gui(file, "Save As…", "saveAs", "s", [.command, .shift])
        gui(file, "Export Spectrum as CSV…", "exportCsv", "e")
        gui(file, "Export Plot as PNG…", "exportPng", "e", [.command, .shift])
        file.addItem(.separator())
        gui(file, "Load Solar Example", "example")
        add(file, "Close Window", #selector(NSWindow.performClose(_:)), "w")

        let edit = menu("Edit")
        add(edit, "Undo", Selector(("undo:")), "z")
        add(edit, "Redo", Selector(("redo:")), "z", [.command, .shift])
        edit.addItem(.separator())
        add(edit, "Cut", #selector(NSText.cut(_:)), "x")
        add(edit, "Copy", #selector(NSText.copy(_:)), "c")
        add(edit, "Paste", #selector(NSText.paste(_:)), "v")
        add(edit, "Select All", #selector(NSText.selectAll(_:)), "a")

        let run = menu("Run")
        gui(run, "Synthesize", "synth", "\r")
        gui(run, "Fit", "fit", "\r", [.command, .shift])
        gui(run, "Stop", "stop", ".")

        let view = menu("View")
        add(view, "Reload Interface", #selector(reload(_:)), "r", target: self)
        add(view, "Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control])

        let windowMenu = menu("Window")
        add(windowMenu, "Minimize", #selector(NSWindow.performMiniaturize(_:)), "m")
        add(windowMenu, "Zoom", #selector(NSWindow.performZoom(_:)))
        NSApp.windowsMenu = windowMenu

        let help = menu("Help")
        add(help, "Show App Data and Logs", #selector(showDataFolder(_:)), target: self)
        add(help, "Reinstall PySME…", #selector(repair(_:)), target: self)

        NSApp.mainMenu = main
    }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()

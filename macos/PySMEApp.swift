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

    // The interface and PySME are installed separately (see envmanager.py).
    func gui(_ id: String) -> URL { home.appendingPathComponent("gui/\(id)") }
    func env(_ id: String) -> URL { home.appendingPathComponent("envs/\(id)") }
    func python(_ env: String) -> URL { self.env(env).appendingPathComponent("venv/bin/python") }
}

// BEGIN version order (tests/test_releases.py compiles this part on its own)
/// 0.3.0a2 < 0.3.0a10 < 0.3.0b1 < 0.3.0rc1 < 0.3.0, as envmanager.version_key
/// orders them. A plain numeric string comparison gets the last step wrong.
func versionKey(_ text: String) -> [Int] {
    let pattern = #"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:(a|b|rc)(\d+))?"#
    guard let regex = try? NSRegularExpression(pattern: pattern),
          let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text))
    else { return [0, 0, 0, 0, 0] }
    func group(_ i: Int) -> String? {
        Range(match.range(at: i), in: text).map { String(text[$0]) }
    }
    let stage = ["a": 1, "b": 2, "rc": 3][group(4) ?? ""] ?? 4
    return [1, 2, 3].map { Int(group($0) ?? "0") ?? 0 } + [stage, Int(group(5) ?? "0") ?? 0]
}

func isOlder(_ a: String, than b: String) -> Bool {
    versionKey(a).lexicographicallyPrecedes(versionKey(b))
}
// END version order

/// The interface and PySME environment in use.
struct Selection {
    let gui: String
    let env: String
    let guiVersion: String
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
    var setupView: WKWebView!           // setup.html: setup, start-up, failures
    var setupReady = false
    var setupQueue: [String] = []

    var installer: Process?
    var server: Process?
    var readers: [LineReader] = []
    var serverURL: URL?
    var pageReady = false
    var pendingFile: String?
    var themeItems: [String: NSMenuItem] = [:]
    var zoom: CGFloat = 1
    let zoomSteps: [CGFloat] = [0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.4, 1.6, 1.8, 2.0]
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
        // Before the window appears, so that the setup screen follows it too.
        applyAppearance(readJSON(paths.settings)["theme"] as? String ?? "auto")
        buildWindow()
        // The build check must not take the keyboard away from other programs.
        if environment["PYSME_GUI_SELFTEST"] == nil { NSApp.activate(ignoringOtherApps: true) }
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
        if show { setupCall("line(\(jsString(safe)))") }
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

        let setupConfiguration = WKWebViewConfiguration()
        setupConfiguration.userContentController.add(self, name: "setup")
        setupView = WKWebView(frame: .zero, configuration: setupConfiguration)
        setupView.navigationDelegate = self
        let page = paths.resources.appendingPathComponent("setup.html")
        setupView.loadFileURL(page, allowingReadAccessTo: paths.resources)

        // The build check shares these defaults with the installed app: it neither
        // reads nor changes the user's zoom.
        if environment["PYSME_GUI_SELFTEST"] == nil {
            let saved = CGFloat(UserDefaults.standard.double(forKey: "pageZoom"))
            if zoomSteps.contains(where: { abs($0 - saved) < 0.001 }) { setZoom(saved) }
        }

        window.contentView = setupView
        window.makeKeyAndOrderFront(nil)
    }

    // MARK: - zoom

    /// Scales the whole interface, as a browser does.
    func setZoom(_ value: CGFloat) {
        zoom = value
        web.pageZoom = value
        setupView.pageZoom = value
        if environment["PYSME_GUI_SELFTEST"] == nil {
            UserDefaults.standard.set(Double(value), forKey: "pageZoom")
        }
    }

    @objc func zoomIn(_ sender: Any?) {
        setZoom(zoomSteps.first { $0 > zoom + 0.001 } ?? zoomSteps[zoomSteps.count - 1])
    }

    @objc func zoomOut(_ sender: Any?) {
        setZoom(zoomSteps.last { $0 < zoom - 0.001 } ?? zoomSteps[0])
    }

    @objc func actualSize(_ sender: Any?) {
        setZoom(1)
    }

    /// Run `window.setup.<call>`, waiting for the page when necessary.
    func setupCall(_ call: String) {
        if setupReady { setupView.evaluateJavaScript("window.setup.\(call)") }
        else { setupQueue.append(call) }
    }

    func showSetup(_ mode: String, _ title: String, _ note: String = "") {
        setupCall("show(\(jsString(mode)), \(jsString(title)), \(jsString(note)))")
        if window.contentView !== setupView { window.contentView = setupView }
    }

    // MARK: - environment

    func activeSelection() -> Selection? {
        guard let current = readJSON(paths.current)["current"] as? [String: Any],
              let gui = current["gui"] as? String, let env = current["env"] as? String,
              let version = readJSON(paths.gui(gui).appendingPathComponent("gui.json"))["version"] as? String,
              !readJSON(paths.env(env).appendingPathComponent("env.json")).isEmpty,
              FileManager.default.isExecutableFile(atPath: paths.python(env).path)
        else { return nil }
        return Selection(gui: gui, env: env, guiVersion: version)
    }

    /// The full version, such as 0.3.0a1. The system's own version fields may
    /// hold numbers only.
    var bundleVersion: String {
        Bundle.main.infoDictionary?["PySMEVersion"] as? String ?? "0"
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
        record("--- start \(Date()) build \(bundleID) ---")

        guard let active = activeSelection() else {
            install("Setting up PySME", "This downloads Python and PySME (about 120 MB). It happens only once.")
            return
        }
        // A newer app dragged into Applications brings a newer interface with it.
        let installedFrom = readJSON(paths.current)["bundle_id"] as? String
        if installedFrom != bundleID, !isOlder(bundleVersion, than: active.guiVersion) {
            install("Updating PySME", "Installing the interface that comes with this app. "
                    + "The PySME version you chose is kept if the interface supports it.")
            return
        }
        startServer(active)
    }

    func install(_ title: String, _ note: String, repair: Bool = false) {
        showSetup("install", title, note)
        // Downloading Python happens before the installer prints its first step.
        setupCall("step(1)")
        let process = Process()
        process.executableURL = paths.uv
        process.arguments = [
            "run", "--no-project", "--managed-python", "--python", "3.12",
            paths.source.appendingPathComponent("pysme_gui/envmanager.py").path,
            "install", "--source", paths.source.path, "--bundle-id", bundleID,
        ] + (repair ? ["--repair"] : [])
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
            if line.hasPrefix("STEP "), let number = line.dropFirst(5).first?.wholeNumberValue {
                self.setupCall("step(\(number))")
                self.snapshotSetup("setup-step-\(number)")
            }
        }]
        process.terminationHandler = { [weak self] finished in
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                guard let self = self, self.installer === finished else { return }
                self.installer = nil
                if finished.terminationStatus == 0, let active = self.activeSelection() {
                    self.startServer(active)
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
        setupCall("fail()")
        let old = activeSelection()
        var buttons = ["Try again"]
        if old != nil { buttons.append("Use the installed version") }
        buttons.append("Quit")
        buttons.insert(reportButton, at: buttons.count - 1)
        if let target = environment["PYSME_GUI_SELFTEST"] {
            // The build check can force this path; keep its report with the results.
            _ = writeShellReport("Setup did not finish.", in: URL(fileURLWithPath: target))
            snapshotSetup("setup-failed") { NSApp.terminate(nil) }
            return
        }
        let choice = ask("Setup did not finish",
                         "Check the internet connection and try again.\n\n" + tail(), buttons)
        if choice == reportButton {
            reportFromShell("Setup did not finish.")
            installFailed()
        }
        else if choice == "Try again" { boot() }
        else if choice == "Use the installed version", let active = old { startServer(active) }
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

    func startServer(_ active: Selection) {
        showSetup("start", "Starting PySME")
        let custom = readJSON(paths.settings)["python"] as? String
        let python = (custom?.isEmpty == false)
            ? custom!
            : paths.python(active.env).path
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        var arguments = ["-m", "pysme_gui", "--app-mode", "--parent-pid", String(getpid())]
        if let file = pendingFile {
            arguments.append(file)
            pendingFile = nil
        }
        process.arguments = arguments
        var env = childEnvironment()
        env["PYTHONPATH"] = paths.gui(active.gui).path
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
        showSetup("stopped", "PySME stopped", "The PySME process is no longer running.")
        for line in recent.suffix(30) { setupCall("line(\(jsString(line)))") }
        var buttons = ["Restart"]
        if custom?.isEmpty == false { buttons.append("Use the app's own PySME") }
        let goBack = "Go back to previous setup"     // interface and PySME, before the last change
        if readJSON(paths.current)["previous"] is [String: Any] { buttons.append(goBack) }
        buttons.append(contentsOf: ["Reinstall", reportButton, "Quit"])
        switch ask("PySME stopped unexpectedly", tail(), buttons) {
        case reportButton:
            reportFromShell("The PySME process stopped unexpectedly.")
            serverStopped(custom: custom)
        case "Restart":
            boot()
        case "Use the app's own PySME":
            var settings = readJSON(paths.settings)
            settings.removeValue(forKey: "python")
            if let data = try? JSONSerialization.data(withJSONObject: settings) {
                try? data.write(to: paths.settings, options: .atomic)
            }
            boot()
        case goBack:
            var record = readJSON(paths.current)
            let previous = record["previous"]
            record["previous"] = record["current"]
            record["current"] = previous
            if let data = try? JSONSerialization.data(withJSONObject: record) {
                try? data.write(to: paths.current, options: .atomic)
            }
            boot()
        case "Reinstall":
            install("Reinstalling PySME", "Rebuilding with the same package versions.", repair: true)
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

    // MARK: - problem reports

    // The interface writes the full report. This smaller one is for when it
    // cannot run: setup failed, or the PySME process does not start.

    let reportButton = "Report a problem…"

    var feedbackEmail: String {
        Bundle.main.infoDictionary?["PySMEFeedbackEmail"] as? String ?? ""
    }

    /// Remove user names from paths; the token is already kept out of the log.
    func redact(_ text: String) -> String {
        var result = text.replacingOccurrences(of: NSHomeDirectory(), with: "~")
        result = result.replacingOccurrences(
            of: "(/Users/)(?!Shared\\b)[^/\\n'\":]+", with: "$1<user>", options: .regularExpression)
        return result.replacingOccurrences(
            of: "(token=)[A-Za-z0-9_\\-]+", with: "$1<hidden>", options: .regularExpression)
    }

    func writeShellReport(_ problem: String, in folder: URL? = nil) -> URL? {
        func ascii(_ path: String) -> String { path.allSatisfy(\.isASCII) ? "yes" : "NO" }
        var system = utsname()
        uname(&system)
        let machine = withUnsafeBytes(of: &system.machine) {
            String(decoding: $0.prefix { $0 != 0 }, as: UTF8.self)
        }
        let log = (try? Data(contentsOf: paths.log)).map { String(decoding: $0.suffix(200_000), as: UTF8.self) } ?? ""
        let current = (try? String(contentsOf: paths.current, encoding: .utf8)) ?? "-"
        let lines: [String] = [
            "PySME-GUI problem report (written by the Mac app, not by the interface)",
            "Created: \(ISO8601DateFormatter.string(from: Date(), timeZone: .current, formatOptions: .withInternetDateTime))",
            "", "== What happened ==", problem,
            "(Please also describe in your email what you were doing.)",
            "", "== Versions ==",
            "App: \(bundleID)",
            "", "== System ==",
            "macOS: \(ProcessInfo.processInfo.operatingSystemVersionString)",
            "Machine: \(machine)",
            "Locale: \(Locale.current.identifier)",
            "Home path is plain ASCII: \(ascii(NSHomeDirectory()))",
            "App data path is plain ASCII: \(ascii(paths.home.path))",
            "App location is plain ASCII: \(ascii(Bundle.main.bundlePath))",
            "App location: \(Bundle.main.bundlePath)",
            "", "== Installation ==",
            "Custom Python setting: \(readJSON(paths.settings)["python"] as? String ?? "-")",
            current,
            "", "== Application log ==",
        ] + log.split(separator: "\n", omittingEmptySubsequences: false).suffix(300).map(String.init)
        let text = redact(lines.joined(separator: "\n") + "\n")

        let stamp = DateFormatter()
        stamp.locale = Locale(identifier: "en_US_POSIX")
        stamp.dateFormat = "yyyyMMdd-HHmmss"
        let name = "PySME-report-\(stamp.string(from: Date())).txt"
        let manager = FileManager.default
        let folders = folder.map { [$0] } ?? [
            manager.urls(for: .downloadsDirectory, in: .userDomainMask).first,
            manager.homeDirectoryForCurrentUser,
        ].compactMap { $0 }
        for directory in folders {
            try? manager.createDirectory(at: directory, withIntermediateDirectories: true)
            let url = directory.appendingPathComponent(name)
            if (try? text.write(to: url, atomically: true, encoding: .utf8)) != nil { return url }
        }
        return nil
    }

    /// Start an email with the report attached. Without a mail program that can
    /// take attachments, open a plain message and show the file instead.
    func composeMail(_ file: URL, to: String, subject: String, body: String) {
        let recipients = to.isEmpty ? [] : [to]
        if let service = NSSharingService(named: .composeEmail), service.canPerform(withItems: [body, file]) {
            service.recipients = recipients
            service.subject = subject
            service.perform(withItems: [body, file])
            return
        }
        var link = URLComponents()
        link.scheme = "mailto"
        link.path = to
        link.queryItems = [URLQueryItem(name: "subject", value: subject), URLQueryItem(name: "body", value: body)]
        if let url = link.url { NSWorkspace.shared.open(url) }
        NSWorkspace.shared.activateFileViewerSelecting([file])
    }

    /// A pre-filled GitHub issue. Only versions go into the address; the user
    /// attaches the file.
    func openIssue(_ file: URL) {
        let repo = Bundle.main.infoDictionary?["PySMEIssueRepo"] as? String ?? ""
        var link = URLComponents(string: "https://github.com/\(repo)/issues/new")
        link?.queryItems = [
            URLQueryItem(name: "title", value: "PySME-GUI problem report (app \(bundleVersion), macOS)"),
            URLQueryItem(name: "body", value: "**What happened?**\n\n\n**Report file:** please drag `\(file.lastPathComponent)` into this box. Issues are public.\n\nApp: \(bundleID)\nmacOS: \(ProcessInfo.processInfo.operatingSystemVersionString)"),
        ]
        if let url = link?.url { NSWorkspace.shared.open(url) }
        NSWorkspace.shared.activateFileViewerSelecting([file])
    }

    func reportFromShell(_ problem: String) {
        guard let file = writeShellReport(problem) else {
            _ = ask("Could not save the report", tail(), ["OK"])
            return
        }
        let address = feedbackEmail.isEmpty
            ? "Send this file to the person who gave you the program."
            : "Send this file to \(feedbackEmail)."
        let choice = ask("Report saved",
                         "\(file.path)\n\nIt has not been sent yet.\n\n"
                         + "Email: \(address) “Write email” tries to attach the file; "
                         + "with webmail, attach it yourself.\n\n"
                         + "GitHub issue: needs a GitHub account. Drag the file into the page. Issues are public.",
                         ["Write email", "Open GitHub issue", "Show file", "Back"])
        if choice == "Back" { return }
        if choice == "Show file" {
            NSWorkspace.shared.activateFileViewerSelecting([file])
        } else if choice == "Open GitHub issue" {
            openIssue(file)
        } else {
            composeMail(file, to: feedbackEmail, subject: "PySME-GUI problem report (app \(bundleVersion), macOS)",
                        body: "My problem report is in the file \(file.lastPathComponent).\n(If it is not attached yet, please attach it before sending.)\n")
        }
        // Come back to this message, so that the other program is not covered
        // by the next question straight away.
        _ = ask("Continue", "Press Continue when you are done.", ["Continue"])
    }

    @objc func reportProblem(_ sender: Any?) {
        let fallback = { self.reportFromShell("Reported from the Help menu while the interface was not available.") }
        guard pageReady else { return fallback() }
        web.evaluateJavaScript("typeof window.guiCommand === 'function' && (window.guiCommand('report'), true)") { value, _ in
            if (value as? Bool) != true { fallback() }
        }
    }

    // MARK: - web view

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if webView === setupView {
            setupReady = true
            setupQueue.forEach { setupCall($0) }
            setupQueue = []
            return
        }
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
        if webView === setupView {
            // It only ever shows the page bundled with the app.
            let bundled = url.isFileURL && url.lastPathComponent == "setup.html"
            return decisionHandler(bundled ? .allow : .cancel)
        }
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
        if webView === setupView {
            setupReady = false
            setupView.reload()
        } else if let url = serverURL { web.load(URLRequest(url: url)) }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        if controller === setupView.configuration.userContentController {
            if message.body as? String == "report" {
                reportFromShell("Setup is taking very long.")
            }
            return
        }
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
        case "mailReport":
            let file = URL(fileURLWithPath: body["path"] as? String ?? "")
            // Only a saved report may be attached.
            guard file.lastPathComponent.hasPrefix("PySME-report-"), file.pathExtension == "txt",
                  FileManager.default.fileExists(atPath: file.path) else { break }
            composeMail(file, to: body["to"] as? String ?? "",
                        subject: body["subject"] as? String ?? "", body: body["body"] as? String ?? "")
        case "theme":
            applyAppearance(body["value"] as? String ?? "auto")
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

    /// During the build check, keep pictures of the setup page.
    func snapshotSetup(_ name: String, then done: (() -> Void)? = nil) {
        guard let target = environment["PYSME_GUI_SELFTEST"] else { done?(); return }
        let output = URL(fileURLWithPath: target)
        try? FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) {
            self.setupView.takeSnapshot(with: nil) { image, _ in
                self.writePNG(image, to: output.appendingPathComponent("\(name).png"))
                done?()
            }
        }
    }

    func writePNG(_ image: NSImage?, to url: URL) {
        if let tiff = image?.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
           let png = rep.representation(using: .png, properties: [:]) {
            try? png.write(to: url)
        }
    }

    /// Used by the build script: run the solar example inside the real WebKit
    /// window, write a screenshot and a short report, then quit.
    func runSelfTest(into directory: String) {
        let script = """
        const windowAppearance = async () => appearanceName;
        const wait = (ms) => new Promise((r) => setTimeout(r, ms));
        const errors = [];
        window.addEventListener('error', (e) => errors.push(String(e.message)));
        for (let i = 0; i < 100 && !(S && options.atmospheres.length); i++) await wait(200);
        await send('example');
        await send('run', { kind: 'synth' });
        for (let i = 0; i < 600 && S.job.state === 'running'; i++) await wait(500);
        await wait(1500);
        const report = await request('report/preview', {});
        await showSettings();
        await wait(500);
        const settingsBody = document.querySelector('#dialog .dialog-body').clientHeight;
        document.querySelector('#dialog').close();
        const about = await request('settings');
        return JSON.stringify({
          python: about.python, managed: about.managed, runningPySME: about.pysme,
          restartDue: about.restart,
          theme: currentTheme(), windowAppearance: await windowAppearance(), settingsBody,
          reportOk: report.text.includes('== Versions ==') && report.text.includes('Application log'),
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
            let dark = self.window.effectiveAppearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            self.web.callAsyncJavaScript(script, arguments: ["appearanceName": dark ? "dark" : "light"],
                                         in: nil, in: .page) { result in
                var report: String
                switch result {
                case .success(let value): report = value as? String ?? "no result"
                case .failure(let error): report = "{\"failure\": \(jsString(error.localizedDescription))}"
                }
                try? report.write(to: output.appendingPathComponent("report.json"), atomically: true, encoding: .utf8)
                self.web.takeSnapshot(with: nil) { image, _ in
                    self.writePNG(image, to: output.appendingPathComponent("window.png"))
                    self.checkZoom(into: output, keys: ["", "=", "+", "-", "0"], seen: [])
                }
            }
        }
    }

    /// Press the zoom keys through the menu and note how wide the page is, in
    /// its own pixels, after each; a picture is taken at the largest zoom.
    func checkZoom(into output: URL, keys: [String], seen: [String]) {
        guard let key = keys.first else {
            try? "[\(seen.joined(separator: ", "))]".write(
                to: output.appendingPathComponent("zoom.json"), atomically: true, encoding: .utf8)
            quitConfirmed = true
            NSApp.terminate(nil)
            return
        }
        if !key.isEmpty, let event = NSEvent.keyEvent(
            with: .keyDown, location: .zero, modifierFlags: .command, timestamp: 0,
            windowNumber: window.windowNumber, context: nil, characters: key,
            charactersIgnoringModifiers: key, isARepeat: false, keyCode: 0) {
            _ = NSApp.mainMenu?.performKeyEquivalent(with: event)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
            self.web.evaluateJavaScript("window.innerWidth") { width, _ in
                let line = "{\"key\": \"\(key)\", \"zoom\": \(self.zoom), \"width\": \(width as? Int ?? 0)}"
                let next = {
                    self.checkZoom(into: output, keys: Array(keys.dropFirst()), seen: seen + [line])
                }
                if key == "+" {
                    self.web.takeSnapshot(with: nil) { image, _ in
                        self.writePNG(image, to: output.appendingPathComponent("window-zoomed.png"))
                        next()
                    }
                } else {
                    next()
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

    /// "light" or "dark" overrides the system for the whole app; anything else
    /// follows the system.
    func applyAppearance(_ theme: String) {
        let names: [String: NSAppearance.Name] = ["light": .aqua, "dark": .darkAqua]
        NSApp.appearance = names[theme].flatMap { NSAppearance(named: $0) }
        let chosen = names[theme] == nil ? "auto" : theme
        for (name, item) in themeItems { item.state = name == chosen ? .on : .off }
    }

    @objc func showAbout(_ sender: Any?) {
        NSApp.orderFrontStandardAboutPanel(options: [.applicationVersion: bundleVersion, .version: ""])
    }

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
        if ask("Reinstall PySME?", "The environment in use is rebuilt with the same package versions. Unsaved changes in the window are lost. Your files and ~/.sme are not touched.", ["Reinstall", "Cancel"]) == "Reinstall" {
            startGeneration += 1
            stopServer()
            pageReady = false
            install("Reinstalling PySME", "Rebuilding with the same package versions.", repair: true)
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
        add(app, "About \(appName)", #selector(showAbout(_:)), target: self)
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
        let appearance = NSMenu(title: "Appearance")
        let appearanceItem = NSMenuItem(title: "Appearance", action: nil, keyEquivalent: "")
        appearanceItem.submenu = appearance
        view.addItem(appearanceItem)
        for (theme, title, command) in [("auto", "Match the System", "themeAuto"),
                                        ("light", "Light", "themeLight"), ("dark", "Dark", "themeDark")] {
            gui(appearance, title, command)
            themeItems[theme] = appearance.items.last
        }
        view.addItem(.separator())
        add(view, "Actual Size", #selector(actualSize(_:)), "0", target: self)
        add(view, "Zoom In", #selector(zoomIn(_:)), "+", target: self)
        // ⌘= is the same key without Shift.
        let unshifted = add(view, "Zoom In", #selector(zoomIn(_:)), "=", target: self)
        unshifted.isHidden = true
        unshifted.allowsKeyEquivalentWhenHidden = true
        add(view, "Zoom Out", #selector(zoomOut(_:)), "-", target: self)
        view.addItem(.separator())
        add(view, "Reload Interface", #selector(reload(_:)), "r", target: self)
        add(view, "Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control])

        let windowMenu = menu("Window")
        add(windowMenu, "Minimize", #selector(NSWindow.performMiniaturize(_:)), "m")
        add(windowMenu, "Zoom", #selector(NSWindow.performZoom(_:)))
        NSApp.windowsMenu = windowMenu

        let help = menu("Help")
        add(help, "Report a Problem…", #selector(reportProblem(_:)), target: self)
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

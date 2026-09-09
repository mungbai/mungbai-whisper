@preconcurrency import AppKit
import Foundation

private let supportedExtensions: Set<String> = [
    "aac", "aiff", "flac", "m4a", "m4v", "mov", "mp3", "mp4", "mpeg",
    "mpga", "oga", "ogg", "opus", "wav", "webm", "wma",
]

private final class CardView: NSView {
    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.cornerRadius = 10
        layer?.borderWidth = 1
        updateColors()
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateColors()
    }

    private func updateColors() {
        layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
        layer?.borderColor = NSColor.separatorColor.withAlphaComponent(0.55).cgColor
    }
}

private final class DropView: NSView {
    var onFiles: (([URL]) -> Void)?
    private var highlighted = false

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        registerForDraggedTypes([.fileURL])
        wantsLayer = true
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let drawingBounds = bounds.insetBy(dx: 2, dy: 2)
        let path = NSBezierPath(roundedRect: drawingBounds, xRadius: 12, yRadius: 12)
        let fill = highlighted
            ? NSColor.controlAccentColor.withAlphaComponent(0.10)
            : NSColor.controlBackgroundColor
        fill.setFill()
        path.fill()
        (highlighted ? NSColor.controlAccentColor : NSColor.separatorColor).setStroke()
        path.lineWidth = highlighted ? 2.25 : 1.25
        path.setLineDash([7, 5], count: 2, phase: 0)
        path.stroke()

        let symbol = NSImage(
            systemSymbolName: "waveform.badge.plus",
            accessibilityDescription: "Add audio"
        )
        let configuration = NSImage.SymbolConfiguration(pointSize: 27, weight: .medium)
        let configured = symbol?.withSymbolConfiguration(configuration)
        configured?.isTemplate = true
        NSColor.controlAccentColor.set()
        configured?.draw(
            in: NSRect(x: drawingBounds.midX - 17, y: drawingBounds.midY + 12, width: 34, height: 30)
        )

        let centered = NSMutableParagraphStyle()
        centered.alignment = .center
        "Drop recordings here".draw(
            in: NSRect(x: drawingBounds.minX + 20, y: drawingBounds.midY - 12, width: drawingBounds.width - 40, height: 24),
            withAttributes: [
                .font: NSFont.systemFont(ofSize: 15, weight: .semibold),
                .foregroundColor: NSColor.labelColor,
                .paragraphStyle: centered,
            ]
        )
        "MP3, M4A, WAV, MP4 and other common formats".draw(
            in: NSRect(x: drawingBounds.minX + 20, y: drawingBounds.midY - 36, width: drawingBounds.width - 40, height: 20),
            withAttributes: [
                .font: NSFont.systemFont(ofSize: 12),
                .foregroundColor: NSColor.secondaryLabelColor,
                .paragraphStyle: centered,
            ]
        )
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        highlighted = true
        needsDisplay = true
        return .copy
    }

    override func draggingExited(_ sender: NSDraggingInfo?) {
        highlighted = false
        needsDisplay = true
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        highlighted = false
        needsDisplay = true
        let urls = sender.draggingPasteboard.readObjects(forClasses: [NSURL.self]) as? [URL] ?? []
        let audio = urls.filter { supportedExtensions.contains($0.pathExtension.lowercased()) }
        guard !audio.isEmpty else { return false }
        onFiles?(audio)
        return true
    }
}

private enum JobState {
    case queued, running, completed, failed, cancelled
}

private final class TranscriptionJob {
    let id = UUID()
    let source: String
    let displayName: String
    let submissionDate = Date()
    var state: JobState = .queued
    var stage = "Queued"
    var progress = 0.0
    var startedAt: Date?
    var finishedAt: Date?
    var estimatedFinishAt: Date?
    var outputURL: URL?
    var process: Process?
    var outputBuffer = Data()
    var lastError: String?

    init(source: String, displayName: String) {
        self.source = source
        self.displayName = displayName
    }
}

private final class AppDelegate: NSObject, NSApplicationDelegate, NSTextFieldDelegate,
    NSTableViewDataSource, NSTableViewDelegate {
    private var window: NSWindow!
    private var dropView: DropView!
    private var fileCard: CardView!
    private var fileLabel: NSTextField!
    private var chooseButton: NSButton!
    private var youtubeField: NSTextField!
    private var addYouTubeButton: NSButton!
    private var speakerPopup: NSPopUpButton!
    private var languagePopup: NSPopUpButton!
    private var qualityPopup: NSPopUpButton!
    private var startButton: NSButton!
    private var cancelButton: NSButton!
    private var openLatestButton: NSButton!
    private var showResultsButton: NSButton!
    private var statusIcon: NSImageView!
    private var statusLabel: NSTextField!
    private var elapsedLabel: NSTextField!
    private var progress: NSProgressIndicator!
    private var taskTable: NSTableView!
    private var clearFinishedButton: NSButton!

    private var maximumConcurrentJobs: Int {
        let memoryGB = ProcessInfo.processInfo.physicalMemory / 1_073_741_824
        let cores = ProcessInfo.processInfo.activeProcessorCount
        if memoryGB >= 32, cores >= 10 { return 4 }
        if memoryGB >= 16, cores >= 8 { return 2 }
        return 1
    }
    private var jobs: [TranscriptionJob] = []
    private var currentRunJobIDs: Set<UUID> = []
    private var completedOutputs: [URL] = []
    private var startedAt: Date?
    private var timer: Timer?

    private var queueIsActive: Bool {
        timer != nil || jobs.contains { $0.state == .running }
    }

    private var projectRoot: URL {
        let bundleURL = Bundle.main.bundleURL
        if bundleURL.pathExtension == "app" {
            return bundleURL.deletingLastPathComponent()
        }
        return URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
    }

    private var outputRoot: URL {
        projectRoot.appendingPathComponent("02 Transcripts", isDirectory: true)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenus()
        buildWindow()
        loadInbox()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        for job in jobs where job.state == .running {
            job.process?.terminate()
        }
    }

    private func buildMenus() {
        let mainMenu = NSMenu()

        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu(title: "Project Whisper")
        appMenuItem.submenu = appMenu
        appMenu.addItem(
            withTitle: "About Project Whisper",
            action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)),
            keyEquivalent: ""
        )
        appMenu.addItem(.separator())
        appMenu.addItem(
            withTitle: "Quit Project Whisper",
            action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q"
        )

        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "Edit")
        editMenuItem.submenu = editMenu
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        NSApp.mainMenu = mainMenu
    }

    private func buildWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 700, height: 850),
            styleMask: [.titled, .closable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Project Whisper"
        window.center()
        window.isReleasedWhenClosed = false
        window.backgroundColor = .windowBackgroundColor

        guard let content = window.contentView else { return }

        let title = NSTextField(labelWithString: "Transcribe audio locally")
        title.font = .systemFont(ofSize: 25, weight: .bold)
        title.frame = NSRect(x: 36, y: 793, width: 628, height: 34)
        content.addSubview(title)

        let subtitle = NSTextField(
            labelWithString: "Private, on-device transcription with automatic speaker labels"
        )
        subtitle.textColor = .secondaryLabelColor
        subtitle.font = .systemFont(ofSize: 13)
        subtitle.frame = NSRect(x: 36, y: 767, width: 628, height: 21)
        content.addSubview(subtitle)

        dropView = DropView(frame: NSRect(x: 36, y: 625, width: 628, height: 122))
        dropView.onFiles = { [weak self] files in self?.setFiles(files) }
        content.addSubview(dropView)

        chooseButton = NSButton(title: "Choose Audio…", target: self, action: #selector(chooseAudio))
        chooseButton.bezelStyle = .rounded
        chooseButton.frame = NSRect(x: 36, y: 583, width: 130, height: 32)
        content.addSubview(chooseButton)

        let inboxButton = NSButton(title: "Open Inbox", target: self, action: #selector(openInbox))
        inboxButton.bezelStyle = .rounded
        inboxButton.frame = NSRect(x: 176, y: 583, width: 112, height: 32)
        content.addSubview(inboxButton)

        let selectedTitle = NSTextField(labelWithString: "TASKS ADDED")
        selectedTitle.font = .systemFont(ofSize: 10, weight: .semibold)
        selectedTitle.textColor = .secondaryLabelColor
        selectedTitle.frame = NSRect(x: 310, y: 590, width: 200, height: 18)
        content.addSubview(selectedTitle)

        fileCard = CardView(frame: NSRect(x: 310, y: 551, width: 354, height: 34))
        content.addSubview(fileCard)
        fileLabel = NSTextField(labelWithString: "No recordings selected")
        fileLabel.font = .systemFont(ofSize: 12)
        fileLabel.textColor = .secondaryLabelColor
        fileLabel.lineBreakMode = .byTruncatingMiddle
        fileLabel.frame = NSRect(x: 14, y: 7, width: 326, height: 20)
        fileCard.addSubview(fileLabel)

        let youtubeTitle = NSTextField(labelWithString: "YOUTUBE OR BILIBILI VIDEO LINKS")
        youtubeTitle.font = .systemFont(ofSize: 10, weight: .semibold)
        youtubeTitle.textColor = .secondaryLabelColor
        youtubeTitle.frame = NSRect(x: 36, y: 525, width: 320, height: 18)
        content.addSubview(youtubeTitle)

        youtubeField = NSTextField(frame: NSRect(x: 36, y: 486, width: 500, height: 32))
        youtubeField.placeholderString = "Paste one or more YouTube or Bilibili links"
        youtubeField.font = .systemFont(ofSize: 13)
        youtubeField.delegate = self
        youtubeField.target = self
        youtubeField.action = #selector(addYouTubeLinks)
        content.addSubview(youtubeField)

        addYouTubeButton = NSButton(title: "Add Links", target: self, action: #selector(addYouTubeLinks))
        addYouTubeButton.bezelStyle = .rounded
        addYouTubeButton.frame = NSRect(x: 546, y: 486, width: 118, height: 32)
        content.addSubview(addYouTubeButton)

        let settingsTitle = NSTextField(labelWithString: "TRANSCRIPTION SETTINGS")
        settingsTitle.font = .systemFont(ofSize: 10, weight: .semibold)
        settingsTitle.textColor = .secondaryLabelColor
        settingsTitle.frame = NSRect(x: 36, y: 453, width: 220, height: 18)
        content.addSubview(settingsTitle)

        let speakerTitle = NSTextField(labelWithString: "Speakers")
        speakerTitle.frame = NSRect(x: 36, y: 422, width: 148, height: 20)
        content.addSubview(speakerTitle)
        speakerPopup = NSPopUpButton(frame: NSRect(x: 36, y: 385, width: 148, height: 32))
        speakerPopup.addItems(withTitles: ["Automatic (1–3)", "Exactly 1", "Exactly 2", "Exactly 3"])
        content.addSubview(speakerPopup)

        let languageTitle = NSTextField(labelWithString: "Language")
        languageTitle.frame = NSRect(x: 198, y: 422, width: 148, height: 20)
        content.addSubview(languageTitle)
        languagePopup = NSPopUpButton(frame: NSRect(x: 198, y: 385, width: 148, height: 32))
        languagePopup.addItems(withTitles: ["Automatic", "English", "中文 (Chinese)"])
        content.addSubview(languagePopup)

        let qualityTitle = NSTextField(labelWithString: "Quality")
        qualityTitle.frame = NSRect(x: 360, y: 422, width: 140, height: 20)
        content.addSubview(qualityTitle)
        qualityPopup = NSPopUpButton(frame: NSRect(x: 360, y: 385, width: 140, height: 32))
        qualityPopup.addItems(withTitles: ["Best", "Faster"])
        content.addSubview(qualityPopup)

        startButton = NSButton(title: "Start Queue", target: self, action: #selector(startTranscription))
        startButton.bezelStyle = .rounded
        startButton.keyEquivalent = "\r"
        startButton.frame = NSRect(x: 514, y: 385, width: 150, height: 32)
        content.addSubview(startButton)

        let taskTitle = NSTextField(
            labelWithString: "TASK MANAGER  •  UP TO \(maximumConcurrentJobs) RUNNING AT ONCE"
        )
        taskTitle.font = .systemFont(ofSize: 10, weight: .semibold)
        taskTitle.textColor = .secondaryLabelColor
        taskTitle.frame = NSRect(x: 36, y: 350, width: 360, height: 18)
        content.addSubview(taskTitle)

        clearFinishedButton = NSButton(
            title: "Clear Finished",
            target: self,
            action: #selector(clearFinished)
        )
        clearFinishedButton.bezelStyle = .rounded
        clearFinishedButton.frame = NSRect(x: 544, y: 343, width: 120, height: 30)
        content.addSubview(clearFinishedButton)

        taskTable = NSTableView()
        taskTable.delegate = self
        taskTable.dataSource = self
        taskTable.usesAlternatingRowBackgroundColors = true
        taskTable.rowHeight = 26
        taskTable.target = self
        taskTable.doubleAction = #selector(openSelectedTask)
        for (identifier, title, width) in [
            ("source", "Source", 180.0),
            ("stage", "Current step", 210.0),
            ("progress", "Progress", 70.0),
            ("eta", "Estimated finish", 112.0),
        ] {
            let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(identifier))
            column.title = title
            column.width = width
            taskTable.addTableColumn(column)
        }
        let taskScroll = NSScrollView(frame: NSRect(x: 36, y: 185, width: 628, height: 150))
        taskScroll.documentView = taskTable
        taskScroll.hasVerticalScroller = true
        taskScroll.borderType = .bezelBorder
        content.addSubview(taskScroll)

        let separator = NSBox(frame: NSRect(x: 36, y: 172, width: 628, height: 1))
        separator.boxType = .separator
        content.addSubview(separator)

        statusIcon = NSImageView(frame: NSRect(x: 36, y: 125, width: 26, height: 26))
        statusIcon.imageScaling = .scaleProportionallyUpOrDown
        statusIcon.contentTintColor = .systemGreen
        statusIcon.isHidden = true
        content.addSubview(statusIcon)

        statusLabel = NSTextField(labelWithString: "Ready")
        statusLabel.font = .systemFont(ofSize: 14, weight: .semibold)
        statusLabel.frame = NSRect(x: 36, y: 128, width: 495, height: 24)
        content.addSubview(statusLabel)

        elapsedLabel = NSTextField(labelWithString: "")
        elapsedLabel.alignment = .right
        elapsedLabel.textColor = .secondaryLabelColor
        elapsedLabel.font = .monospacedDigitSystemFont(ofSize: 12, weight: .regular)
        elapsedLabel.frame = NSRect(x: 520, y: 128, width: 144, height: 22)
        content.addSubview(elapsedLabel)

        progress = NSProgressIndicator(frame: NSRect(x: 36, y: 104, width: 628, height: 10))
        progress.style = .bar
        progress.minValue = 0
        progress.maxValue = 1
        progress.doubleValue = 0
        progress.isHidden = true
        content.addSubview(progress)

        cancelButton = NSButton(title: "Cancel", target: self, action: #selector(cancelTranscription))
        cancelButton.bezelStyle = .rounded
        cancelButton.frame = NSRect(x: 36, y: 70, width: 90, height: 32)
        cancelButton.isHidden = true
        content.addSubview(cancelButton)

        showResultsButton = NSButton(
            title: "Show All Results",
            target: self,
            action: #selector(showResults)
        )
        showResultsButton.bezelStyle = .rounded
        showResultsButton.frame = NSRect(x: 362, y: 70, width: 142, height: 32)
        showResultsButton.isHidden = true
        content.addSubview(showResultsButton)

        openLatestButton = NSButton(
            title: "Open Latest Transcript",
            target: self,
            action: #selector(openLatest)
        )
        openLatestButton.bezelStyle = .rounded
        openLatestButton.frame = NSRect(x: 514, y: 70, width: 150, height: 32)
        openLatestButton.isHidden = true
        content.addSubview(openLatestButton)

        let privacy = NSTextField(
            labelWithString: "Whisper runs privately on this Mac • no upload • no per-minute fee"
        )
        privacy.textColor = .secondaryLabelColor
        privacy.font = .systemFont(ofSize: 11)
        privacy.frame = NSRect(x: 36, y: 24, width: 628, height: 20)
        content.addSubview(privacy)
    }

    private func transcriptInDirectory(_ directory: URL) -> URL? {
        let dated = directory.appendingPathComponent(directory.lastPathComponent).appendingPathExtension("md")
        if FileManager.default.fileExists(atPath: dated.path) { return dated }
        let legacy = directory.appendingPathComponent("transcript.md")
        return FileManager.default.fileExists(atPath: legacy.path) ? legacy : nil
    }

    private func hasExistingTranscript(for recording: URL) -> Bool {
        let directories = (try? FileManager.default.contentsOfDirectory(
            at: outputRoot,
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        )) ?? []
        return directories.contains { directory in
            let candidates = [
                directory.appendingPathComponent(directory.lastPathComponent).appendingPathExtension("json"),
                directory.appendingPathComponent("transcript.json"),
            ]
            return candidates.contains { candidate in
                guard let data = try? Data(contentsOf: candidate),
                      let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let metadata = object["metadata"] as? [String: Any],
                      let sourcePath = metadata["source_path"] as? String
                else { return false }
                return URL(fileURLWithPath: sourcePath).standardizedFileURL == recording.standardizedFileURL
            }
        }
    }

    private func allExistingTranscripts() -> [URL] {
        let directories = (try? FileManager.default.contentsOfDirectory(
            at: outputRoot,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles]
        )) ?? []
        return directories.compactMap { transcriptInDirectory($0) }.sorted {
            let left = (try? $0.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            let right = (try? $1.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            return left < right
        }
    }

    private func loadInbox() {
        let inbox = projectRoot.appendingPathComponent("01 Audio Inbox", isDirectory: true)
        let files = (try? FileManager.default.contentsOfDirectory(
            at: inbox,
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        )) ?? []
        let audio = files
            .filter { supportedExtensions.contains($0.pathExtension.lowercased()) }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        let unprocessed = audio.filter { !hasExistingTranscript(for: $0) }
        completedOutputs = allExistingTranscripts()
        addFiles(unprocessed)
        if unprocessed.isEmpty, !completedOutputs.isEmpty {
            showAvailableResults()
        } else {
            showReadyState()
        }
    }

    private func setFiles(_ files: [URL]) {
        addFiles(files)
        if queueIsActive { scheduleJobs() }
        showReadyState()
    }

    private func addFiles(_ files: [URL]) {
        for file in files where !jobs.contains(where: { $0.source == file.path }) {
            let job = TranscriptionJob(source: file.path, displayName: file.lastPathComponent)
            jobs.append(job)
            if queueIsActive { currentRunJobIDs.insert(job.id) }
        }
        updateFileSummary()
        taskTable?.reloadData()
    }

    private func updateFileSummary() {
        let available = jobs.filter { $0.state == .queued || $0.state == .running }.count
        switch available {
        case 0:
            fileLabel.stringValue = "No queued tasks"
            fileLabel.textColor = .secondaryLabelColor
        case 1:
            fileLabel.stringValue = "1 task in manager"
            fileLabel.textColor = .labelColor
        default:
            fileLabel.stringValue = "\(available) tasks in manager"
            fileLabel.textColor = .labelColor
        }
    }

    private func updateControls() {
        let active = queueIsActive
        chooseButton.isEnabled = true
        youtubeField.isEnabled = true
        addYouTubeButton.isEnabled = !youtubeField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        speakerPopup.isEnabled = !active
        languagePopup.isEnabled = !active
        qualityPopup.isEnabled = !active
        startButton.isEnabled = !active && jobs.contains { $0.state == .queued }
        cancelButton.isHidden = !active
        clearFinishedButton.isEnabled = jobs.contains {
            $0.state == .completed || $0.state == .failed || $0.state == .cancelled
        }
    }

    private func onlineVideoPlatform(_ value: String) -> String? {
        guard let components = URLComponents(string: value),
              ["http", "https"].contains(components.scheme?.lowercased() ?? ""),
              let host = components.host?.lowercased()
        else { return nil }
        if ["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"].contains(host) {
            return "YouTube"
        }
        if ["bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv", "www.b23.tv"].contains(host) {
            return "Bilibili"
        }
        return nil
    }

    private func queuedYouTubeValues() -> [String] {
        let separators = CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn: ","))
        return youtubeField.stringValue.components(separatedBy: separators).filter { !$0.isEmpty }
    }

    @discardableResult
    private func queueYouTubeField(showError: Bool) -> Bool {
        let values = queuedYouTubeValues()
        guard !values.isEmpty else { return true }
        if values.contains(where: { onlineVideoPlatform($0) == nil }) {
            if showError {
                let alert = NSAlert()
                alert.alertStyle = .warning
                alert.messageText = "Invalid video link"
                alert.informativeText = "Paste YouTube, youtu.be, Bilibili, or b23.tv video links, separated by spaces or new lines."
                alert.runModal()
            }
            return false
        }
        for value in values where !jobs.contains(where: { $0.source == value }) {
            let identifier = URLComponents(string: value)?.queryItems?.first(where: { $0.name == "v" })?.value
                ?? URL(string: value)?.lastPathComponent
                ?? "video"
            let platform = onlineVideoPlatform(value) ?? "Video"
            let job = TranscriptionJob(source: value, displayName: "\(platform) • \(identifier)")
            jobs.append(job)
            if queueIsActive { currentRunJobIDs.insert(job.id) }
        }
        youtubeField.stringValue = ""
        updateFileSummary()
        taskTable.reloadData()
        updateControls()
        if queueIsActive { scheduleJobs() }
        return true
    }

    @objc private func addYouTubeLinks() {
        _ = queueYouTubeField(showError: true)
        if !queueIsActive { showReadyState() }
    }

    func controlTextDidChange(_ notification: Notification) {
        updateControls()
    }

    private func showReadyState() {
        guard !queueIsActive else { return }
        let count = jobs.filter { $0.state == .queued }.count
        statusIcon.isHidden = true
        statusLabel.frame.origin.x = 36
        statusLabel.stringValue = count == 0
            ? "Add local recordings, YouTube, or Bilibili links"
            : "\(count) task\(count == 1 ? "" : "s") ready • up to \(maximumConcurrentJobs) run together"
        elapsedLabel.stringValue = ""
        progress.isHidden = count == 0
        progress.doubleValue = 0
        openLatestButton.isHidden = true
        showResultsButton.isHidden = true
        updateControls()
    }

    private func showAvailableResults() {
        let count = completedOutputs.count
        statusIcon.image = NSImage(
            systemSymbolName: "checkmark.circle.fill",
            accessibilityDescription: "Complete"
        )
        statusIcon.isHidden = false
        statusLabel.frame.origin.x = 72
        statusLabel.stringValue = "\(count) previous transcript\(count == 1 ? "" : "s") available"
        elapsedLabel.stringValue = ""
        progress.isHidden = true
        openLatestButton.isHidden = false
        showResultsButton.isHidden = false
        updateControls()
    }

    @objc private func chooseAudio() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.prompt = "Choose"
        panel.message = "Choose one or more audio recordings"
        if panel.runModal() == .OK {
            setFiles(panel.urls.filter { supportedExtensions.contains($0.pathExtension.lowercased()) })
        }
    }

    @objc private func openInbox() {
        NSWorkspace.shared.open(projectRoot.appendingPathComponent("01 Audio Inbox", isDirectory: true))
    }

    @objc private func startTranscription() {
        guard queueYouTubeField(showError: true) else { return }
        let queued = jobs.filter { $0.state == .queued }
        guard !queued.isEmpty else { return }
        currentRunJobIDs = Set(queued.map(\.id))
        startedAt = Date()
        statusIcon.isHidden = true
        statusLabel.frame.origin.x = 36
        openLatestButton.isHidden = true
        showResultsButton.isHidden = true
        progress.isHidden = false
        progress.doubleValue = 0
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            self?.refreshTaskDisplay()
        }
        updateControls()
        scheduleJobs()
    }

    private func scheduleJobs() {
        var slots = maximumConcurrentJobs - jobs.filter { $0.state == .running }.count
        while slots > 0, let job = jobs.first(where: { $0.state == .queued }) {
            run(job)
            slots -= 1
        }
        updateFileSummary()
        refreshTaskDisplay()
        if !jobs.contains(where: { $0.state == .queued || $0.state == .running }) {
            finishAll()
        }
    }

    private func run(_ job: TranscriptionJob) {
        job.state = .running
        job.stage = job.source.hasPrefix("http") ? "Checking video link" : "Preparing audio"
        job.startedAt = Date()
        job.progress = 0
        job.outputBuffer.removeAll(keepingCapacity: true)
        job.lastError = nil

        let process = Process()
        process.executableURL = projectRoot.appendingPathComponent("Library/Python/bin/python")
        let speakerValues = ["auto", "1", "2", "3"]
        let speaker = speakerValues[max(0, min(3, speakerPopup.indexOfSelectedItem))]
        let languageValues = ["auto", "en", "zh"]
        let language = languageValues[max(0, min(2, languagePopup.indexOfSelectedItem))]
        let quality = qualityPopup.indexOfSelectedItem == 1 ? "fast" : "recommended"
        let dateFormatter = DateFormatter()
        dateFormatter.locale = Locale(identifier: "en_US_POSIX")
        dateFormatter.dateFormat = "yyyyMMdd"
        process.arguments = [
            "-m", "whisper_workflow",
            "--speakers", speaker,
            "--quality", quality,
            "--language", language,
            "--submission-date", dateFormatter.string(from: job.submissionDate),
            job.source,
        ]
        var environment = ProcessInfo.processInfo.environment
        environment["PROJECT_WHISPER_ROOT"] = projectRoot.path
        environment["PYTHONPATH"] = projectRoot.appendingPathComponent("Application/Source/Backend").path
        environment["PATH"] = "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        process.environment = environment
        process.currentDirectoryURL = projectRoot

        let outputPipe = Pipe()
        let errorPipe = Pipe()
        process.standardOutput = outputPipe
        process.standardError = errorPipe
        outputPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            DispatchQueue.main.async { self?.consumeOutput(data, for: job) }
        }
        errorPipe.fileHandleForReading.readabilityHandler = { handle in
            _ = handle.availableData
        }
        process.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                outputPipe.fileHandleForReading.readabilityHandler = nil
                errorPipe.fileHandleForReading.readabilityHandler = nil
                self?.processEnded(job, exitCode: process.terminationStatus)
            }
        }

        do {
            job.process = process
            try process.run()
        } catch {
            job.process = nil
            job.state = .failed
            job.stage = error.localizedDescription
            job.finishedAt = Date()
            scheduleJobs()
        }
    }

    private func consumeOutput(_ data: Data, for job: TranscriptionJob) {
        job.outputBuffer.append(data)
        while let newline = job.outputBuffer.firstIndex(of: 10) {
            let lineData = job.outputBuffer.prefix(upTo: newline)
            job.outputBuffer.removeSubrange(...newline)
            guard let object = try? JSONSerialization.jsonObject(with: lineData) as? [String: Any],
                  let type = object["type"] as? String
            else { continue }
            switch type {
            case "file":
                if let name = object["name"] as? String {
                    job.stage = "Preparing \(name)"
                }
            case "status":
                if let message = object["message"] as? String {
                    job.stage = message
                }
            case "download_progress":
                let value = object["value"] as? Double ?? 0
                job.progress = max(job.progress, value * 0.02)
                let platform = object["platform"] as? String ?? "video"
                job.stage = "Downloading \(platform) audio"
                if let eta = object["eta"] as? Double {
                    job.estimatedFinishAt = Date().addingTimeInterval(eta)
                }
            case "media":
                if let remaining = object["estimated_remaining"] as? Double {
                    let activeWorkers = max(1, jobs.filter { $0.state == .running }.count)
                    let contention = 1.0 + 0.22 * Double(activeWorkers - 1)
                    job.estimatedFinishAt = Date().addingTimeInterval(remaining * contention)
                }
            case "progress":
                let value = object["value"] as? Double ?? 0
                let message = object["message"] as? String
                job.progress = max(job.progress, value)
                if let message {
                    job.stage = stepDescription(message)
                }
            case "complete":
                if let path = object["output"] as? String {
                    job.outputURL = URL(fileURLWithPath: path)
                }
            case "error":
                job.lastError = object["message"] as? String
            default:
                break
            }
        }
        refreshTaskDisplay()
    }

    private func stepDescription(_ message: String) -> String {
        switch message {
        case "Finding speech and speakers", "Detecting speakers":
            return "Audio ready ✓ • Detecting speakers"
        case "Loading Whisper":
            return "Audio ✓ • Speakers ✓ • Loading Whisper"
        case "Whisper transcription complete":
            return "Audio ✓ • Speakers ✓ • Transcript ready ✓"
        case "Building transcript":
            return "Transcript ready ✓ • Saving files"
        case "Complete":
            return "All steps complete ✓"
        default:
            return message
        }
    }

    private func processEnded(_ job: TranscriptionJob, exitCode: Int32) {
        job.process = nil
        job.finishedAt = Date()
        if job.state != .cancelled {
            if exitCode == 0 {
                job.state = .completed
                job.progress = 1
                job.stage = "All steps complete ✓"
                if let output = job.outputURL, !completedOutputs.contains(output) {
                    completedOutputs.append(output)
                }
            } else {
                job.state = .failed
                job.stage = job.lastError ?? "Failed • See Logs"
            }
        }
        scheduleJobs()
    }

    private func finishAll() {
        guard timer != nil else { return }
        timer?.invalidate()
        timer = nil
        taskTable.reloadData()
        let elapsed = startedAt.map { Date().timeIntervalSince($0) } ?? 0
        let current = currentRunJobIDs.compactMap { id in jobs.first { $0.id == id } }
        let completed = current.filter { $0.state == .completed }.count
        let failed = current.filter { $0.state == .failed }.count
        statusIcon.image = NSImage(systemSymbolName: failed == 0 ? "checkmark.circle.fill" : "exclamationmark.triangle.fill", accessibilityDescription: "Finished")
        statusIcon.isHidden = false
        statusLabel.frame.origin.x = 72
        statusLabel.stringValue = failed == 0
            ? "\(completed) transcript\(completed == 1 ? "" : "s") ready"
            : "\(completed) completed • \(failed) failed"
        elapsedLabel.stringValue = "Completed in \(durationText(elapsed))"
        openLatestButton.isHidden = completedOutputs.isEmpty
        showResultsButton.isHidden = completedOutputs.isEmpty
        updateControls()
        NSSound(named: "Glass")?.play()
    }

    @objc private func clearFinished() {
        jobs.removeAll { $0.state == .completed || $0.state == .failed || $0.state == .cancelled }
        taskTable.reloadData()
        updateFileSummary()
        if !queueIsActive { showReadyState() }
    }

    @objc private func openLatest() {
        guard let result = completedOutputs.last else { return }
        NSWorkspace.shared.open(result)
    }

    @objc private func cancelTranscription() {
        let running = jobs.filter { $0.state == .running }
        for job in jobs where job.state == .queued || job.state == .running {
            job.state = .cancelled
            job.stage = "Cancelled"
            job.finishedAt = Date()
        }
        for job in running { job.process?.terminate() }
        timer?.invalidate()
        timer = nil
        progress.isHidden = true
        statusLabel.stringValue = "Queue cancelled"
        elapsedLabel.stringValue = ""
        taskTable.reloadData()
        updateFileSummary()
        updateControls()
    }

    @objc private func showResults() {
        NSWorkspace.shared.open(outputRoot)
    }

    @objc private func openSelectedTask() {
        let row = taskTable.clickedRow >= 0 ? taskTable.clickedRow : taskTable.selectedRow
        guard row >= 0, row < jobs.count, let output = jobs[row].outputURL else { return }
        NSWorkspace.shared.open(output)
    }

    func numberOfRows(in tableView: NSTableView) -> Int {
        jobs.count
    }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        guard row < jobs.count, let tableColumn else { return nil }
        let identifier = NSUserInterfaceItemIdentifier("\(tableColumn.identifier.rawValue)-cell")
        let cell = (tableView.makeView(withIdentifier: identifier, owner: self) as? NSTextField)
            ?? NSTextField(labelWithString: "")
        cell.identifier = identifier
        cell.font = tableColumn.identifier.rawValue == "source"
            ? .systemFont(ofSize: 11, weight: .medium)
            : .systemFont(ofSize: 11)
        cell.lineBreakMode = .byTruncatingTail
        let job = jobs[row]
        cell.toolTip = job.stage
        switch tableColumn.identifier.rawValue {
        case "source": cell.stringValue = job.displayName
        case "stage": cell.stringValue = job.stage
        case "progress": cell.stringValue = progressText(for: job)
        case "eta": cell.stringValue = etaText(for: job)
        default: cell.stringValue = ""
        }
        return cell
    }

    private func displayedProgress(for job: TranscriptionJob) -> Double {
        guard job.state == .running,
              let started = job.startedAt,
              let finish = job.estimatedFinishAt
        else { return job.progress }
        let total = max(1, finish.timeIntervalSince(started))
        let timed = Date().timeIntervalSince(started) / total
        return max(job.progress, min(0.92, timed))
    }

    private func progressText(for job: TranscriptionJob) -> String {
        switch job.state {
        case .queued: return "—"
        case .failed: return "Failed"
        case .cancelled: return "Cancelled"
        case .completed: return "100%"
        case .running: return "\(Int(displayedProgress(for: job) * 100))%"
        }
    }

    private func etaText(for job: TranscriptionJob) -> String {
        switch job.state {
        case .queued: return "Queued"
        case .failed, .cancelled: return "—"
        case .completed:
            guard let start = job.startedAt, let finish = job.finishedAt else { return "Done" }
            return durationText(finish.timeIntervalSince(start))
        case .running:
            guard let finish = job.estimatedFinishAt else { return "Calculating…" }
            let formatter = DateFormatter()
            formatter.dateFormat = "HH:mm"
            return "\(formatter.string(from: finish)) (\(shortDuration(finish.timeIntervalSinceNow)))"
        }
    }

    private func refreshTaskDisplay() {
        taskTable.reloadData()
        guard queueIsActive, let startedAt else { return }
        elapsedLabel.stringValue = durationText(Date().timeIntervalSince(startedAt))
        let current = currentRunJobIDs.compactMap { id in jobs.first { $0.id == id } }
        if !current.isEmpty {
            progress.doubleValue = current.map(displayedProgress).reduce(0, +) / Double(current.count)
        }
        let running = jobs.filter { $0.state == .running }.count
        let queued = jobs.filter { $0.state == .queued }.count
        statusLabel.stringValue = "\(running) running • \(queued) queued • \(Int(progress.doubleValue * 100))% overall"
        updateControls()
    }

    private func shortDuration(_ seconds: TimeInterval) -> String {
        let total = max(0, Int(seconds.rounded()))
        return total < 60 ? "\(total)s" : "\((total + 59) / 60)m"
    }

    private func durationText(_ seconds: TimeInterval) -> String {
        let total = max(0, Int(seconds))
        return String(format: "%02d:%02d", total / 60, total % 60)
    }
}

private let application = NSApplication.shared
private let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()

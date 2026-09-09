import CoreML
import FluidAudio
import Foundation

private struct SegmentOutput: Codable {
    let speaker: String
    let start: Double
    let end: Double
    let quality: Double
}

private struct DiarizationOutput: Codable {
    let speakerCount: Int
    let processingSeconds: Double
    let segments: [SegmentOutput]
}

private enum CLIError: LocalizedError {
    case usage

    var errorDescription: String? {
        switch self {
        case .usage:
            return "Usage: ProjectWhisperDiarizer <audio> <output-json> <models-dir> <speaker-count|auto>"
        }
    }
}

private func emit(_ payload: [String: Any]) {
    guard JSONSerialization.isValidJSONObject(payload),
          let data = try? JSONSerialization.data(withJSONObject: payload),
          let line = String(data: data, encoding: .utf8)
    else { return }
    FileHandle.standardOutput.write(Data((line + "\n").utf8))
}

@main
struct ProjectWhisperDiarizer {
    static func main() async {
        do {
            try await run()
        } catch {
            emit(["type": "error", "message": error.localizedDescription])
            FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
            exit(1)
        }
    }

    private static func run() async throws {
        let arguments = Array(CommandLine.arguments.dropFirst())
        guard arguments.count == 4 else { throw CLIError.usage }

        let audioURL = URL(fileURLWithPath: arguments[0]).standardizedFileURL
        let outputURL = URL(fileURLWithPath: arguments[1]).standardizedFileURL
        let modelsURL = URL(fileURLWithPath: arguments[2], isDirectory: true).standardizedFileURL
        let speakerSetting = arguments[3]

        var config = OfflineDiarizerConfig()
        if let exactCount = Int(speakerSetting), (1...3).contains(exactCount) {
            config = config.withSpeakers(exactly: exactCount)
        } else {
            config = config.withSpeakers(min: 1, max: 3)
        }

        try FileManager.default.createDirectory(
            at: modelsURL,
            withIntermediateDirectories: true
        )

        emit(["type": "status", "message": "Preparing speaker model"])
        let manager = OfflineDiarizerManager(config: config)
        let modelConfiguration = MLModelConfigurationUtils.defaultConfiguration(
            computeUnits: .cpuAndNeuralEngine
        )
        try await manager.prepareModels(
            directory: modelsURL,
            configuration: modelConfiguration
        )

        emit(["type": "status", "message": "Detecting speakers"])
        let sourceResult = try AudioSourceFactory().makeDiskBackedSource(
            from: audioURL,
            targetSampleRate: config.segmentation.sampleRate
        )
        let source = sourceResult.source
        defer { source.cleanup() }

        let startedAt = Date()
        let result = try await manager.process(
            audioSource: source,
            audioLoadingSeconds: sourceResult.loadDuration
        ) { completed, total in
            emit([
                "type": "progress",
                "phase": "diarization",
                "completed": completed,
                "total": total,
            ])
        }
        let elapsed = Date().timeIntervalSince(startedAt)

        let segments = result.segments.map {
            SegmentOutput(
                speaker: $0.speakerId,
                start: Double($0.startTimeSeconds),
                end: Double($0.endTimeSeconds),
                quality: Double($0.qualityScore)
            )
        }
        let output = DiarizationOutput(
            speakerCount: Set(segments.map(\.speaker)).count,
            processingSeconds: elapsed,
            segments: segments
        )

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(output).write(to: outputURL, options: .atomic)
        emit([
            "type": "status",
            "message": "Speaker detection complete",
            "speakerCount": output.speakerCount,
        ])
    }
}

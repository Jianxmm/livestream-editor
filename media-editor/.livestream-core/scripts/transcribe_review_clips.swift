import AVFAudio
import CryptoKit
import Foundation
import Speech

struct ClipTranscript: Codable {
    let filename: String
    let audioSHA256: String
    let text: String
    let error: String?

    enum CodingKeys: String, CodingKey {
        case filename
        case audioSHA256 = "audio_sha256"
        case text
        case error
    }
}

struct TranscriptBundle: Codable {
    let version: Int
    let engine: String
    let role: String
    let manifestSHA256: String
    let clips: [ClipTranscript]

    enum CodingKeys: String, CodingKey {
        case version
        case engine
        case role
        case manifestSHA256 = "manifest_sha256"
        case clips
    }
}

enum TranscriptionError: Error, CustomStringConvertible {
    case usage
    case unavailable
    case unsupportedLocale
    case localeNotInstalled(String)

    var description: String {
        switch self {
        case .usage:
            return "用法：transcribe_review_clips <review_clips目录> [输出JSON]"
        case .unavailable:
            return "当前系统不支持 SpeechTranscriber"
        case .unsupportedLocale:
            return "当前系统不支持简体中文语音识别"
        case .localeNotInstalled(let locale):
            return "简体中文离线语音模型尚未安装：\(locale)；本工具不会自动下载"
        }
    }
}

@available(macOS 26.0, *)
func transcribe(_ url: URL, locale: Locale) async -> ClipTranscript {
    let audioHash: String
    do {
        audioHash = sha256(try Data(contentsOf: url))
    } catch {
        return ClipTranscript(
            filename: url.lastPathComponent,
            audioSHA256: "",
            text: "",
            error: String(describing: error)
        )
    }
    do {
        let transcriber = SpeechTranscriber(locale: locale, preset: .transcriptionWithAlternatives)
        let audioFile = try AVAudioFile(forReading: url)
        let analyzer = try await SpeechAnalyzer(
            inputAudioFile: audioFile,
            modules: [transcriber],
            finishAfterFile: true
        )
        var lines: [String] = []
        for try await result in transcriber.results {
            let text = String(result.text.characters)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !text.isEmpty {
                lines.append(text)
            }
        }
        _ = analyzer
        return ClipTranscript(
            filename: url.lastPathComponent,
            audioSHA256: audioHash,
            text: lines.joined(separator: "\n"),
            error: nil
        )
    } catch {
        return ClipTranscript(
            filename: url.lastPathComponent,
            audioSHA256: audioHash,
            text: "",
            error: String(describing: error)
        )
    }
}

func sha256(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

@main
struct TranscribeReviewClips {
    static func main() async throws {
        guard CommandLine.arguments.count == 2 || CommandLine.arguments.count == 3 else {
            throw TranscriptionError.usage
        }
        guard #available(macOS 26.0, *), SpeechTranscriber.isAvailable else {
            throw TranscriptionError.unavailable
        }

        let directory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        let output = CommandLine.arguments.count == 3
            ? URL(fileURLWithPath: CommandLine.arguments[2])
            : directory.appendingPathComponent("machine_transcripts.json")
        guard let locale = await SpeechTranscriber.supportedLocale(
            equivalentTo: Locale(identifier: "zh-CN")
        ) else {
            throw TranscriptionError.unsupportedLocale
        }
        let installed = await SpeechTranscriber.installedLocales
        guard installed.contains(where: { $0.identifier == locale.identifier }) else {
            throw TranscriptionError.localeNotInstalled(locale.identifier)
        }

        let files = try FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: nil
        )
        .filter { $0.pathExtension.lowercased() == "m4a" }
        .sorted { $0.lastPathComponent < $1.lastPathComponent }
        let manifestURL = directory.appendingPathComponent("manifest.json")
        let manifestHash = sha256(try Data(contentsOf: manifestURL))

        var clips: [ClipTranscript] = []
        for file in files {
            clips.append(await transcribe(file, locale: locale))
        }
        let report = TranscriptBundle(
            version: 1,
            engine: "macOS SpeechTranscriber \(locale.identifier)",
            role: "机器辅助证据；不能替代真人回听或关闭P1",
            manifestSHA256: manifestHash,
            clips: clips
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(report).write(to: output, options: .atomic)
        let failures = clips.filter { $0.error != nil }.count
        print("MACHINE_TRANSCRIPTS=PASS files=\(clips.count) failures=\(failures) output=\(output.path)")
        if failures > 0 {
            throw NSError(
                domain: "TranscribeReviewClips",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "部分音频转写失败"]
            )
        }
    }
}

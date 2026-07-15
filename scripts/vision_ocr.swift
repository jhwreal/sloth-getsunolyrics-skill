#!/usr/bin/env swift

import CoreGraphics
import Foundation
import ImageIO
import Vision

struct Observation: Codable {
    let text: String
    let confidence: Float
    let x: Double
    let y: Double
    let width: Double
    let height: Double
    let brightRatio: Double
}

struct FrameResult: Codable {
    let timeMs: Int
    let observations: [Observation]
}

struct Options {
    var framesDirectory = ""
    var languages = ["zh-Hans", "en-US"]
    var filenameScale = 1
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data(("error: \(message)\n").utf8))
    exit(2)
}

func parseOptions() -> Options {
    var options = Options()
    var args = Array(CommandLine.arguments.dropFirst())
    while !args.isEmpty {
        let flag = args.removeFirst()
        func value() -> String {
            guard !args.isEmpty else { fail("missing value after \(flag)") }
            return args.removeFirst()
        }
        switch flag {
        case "--frames-dir": options.framesDirectory = value()
        case "--languages": options.languages = value().split(separator: ",").map(String.init)
        case "--filename-scale": options.filenameScale = Int(value()) ?? 0
        default: fail("unknown argument \(flag)")
        }
    }
    guard !options.framesDirectory.isEmpty else { fail("--frames-dir is required") }
    guard options.filenameScale > 0 else { fail("--filename-scale must be positive") }
    return options
}

func recognize(image: CGImage, timeMs: Int, languages: [String], encoder: JSONEncoder) {
    do {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        request.recognitionLanguages = languages
        request.minimumTextHeight = 0.012
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        try handler.perform([request])
        let observations = (request.results ?? []).compactMap { item -> Observation? in
            guard let candidate = item.topCandidates(1).first else { return nil }
            let box = item.boundingBox
            return Observation(
                text: candidate.string,
                confidence: candidate.confidence,
                x: box.minX,
                y: box.minY,
                width: box.width,
                height: box.height,
                brightRatio: brightPixelRatio(in: image, box: box)
            )
        }
        let result = FrameResult(timeMs: timeMs, observations: observations)
        if let data = try? encoder.encode(result) {
            FileHandle.standardOutput.write(data)
            FileHandle.standardOutput.write(Data([0x0A]))
        }
    } catch {
        FileHandle.standardError.write(Data(("warning: frame \(timeMs): \(error)\n").utf8))
    }
}

func brightPixelRatio(in image: CGImage, box: CGRect) -> Double {
    let imageWidth = image.width
    let imageHeight = image.height
    let x = max(0, min(imageWidth - 1, Int(box.minX * Double(imageWidth))))
    let top = max(0, min(imageHeight - 1, Int((1.0 - box.maxY) * Double(imageHeight))))
    let width = max(1, min(imageWidth - x, Int(box.width * Double(imageWidth))))
    let height = max(1, min(imageHeight - top, Int(box.height * Double(imageHeight))))
    guard let crop = image.cropping(to: CGRect(x: x, y: top, width: width, height: height)) else { return 0 }

    let sampleWidth = min(160, crop.width)
    let sampleHeight = max(1, Int(Double(crop.height) * Double(sampleWidth) / Double(crop.width)))
    var pixels = [UInt8](repeating: 0, count: sampleWidth * sampleHeight * 4)
    guard let context = CGContext(
        data: &pixels,
        width: sampleWidth,
        height: sampleHeight,
        bitsPerComponent: 8,
        bytesPerRow: sampleWidth * 4,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else { return 0 }
    context.draw(crop, in: CGRect(x: 0, y: 0, width: sampleWidth, height: sampleHeight))

    var bright = 0
    let pixelCount = sampleWidth * sampleHeight
    for index in stride(from: 0, to: pixels.count, by: 4) {
        let red = Double(pixels[index])
        let green = Double(pixels[index + 1])
        let blue = Double(pixels[index + 2])
        let luma = 0.2126 * red + 0.7152 * green + 0.0722 * blue
        if luma >= 205 { bright += 1 }
    }
    return Double(bright) / Double(pixelCount)
}

let options = parseOptions()
let encoder = JSONEncoder()
let directory = URL(fileURLWithPath: options.framesDirectory, isDirectory: true)
guard let files = try? FileManager.default.contentsOfDirectory(
    at: directory,
    includingPropertiesForKeys: nil,
    options: [.skipsHiddenFiles]
) else { fail("cannot read frames directory: \(directory.path)") }
let imageFiles = files.filter { ["jpg", "jpeg", "png"].contains($0.pathExtension.lowercased()) }.sorted {
    $0.lastPathComponent < $1.lastPathComponent
}
for file in imageFiles {
    autoreleasepool {
        let digits = file.deletingPathExtension().lastPathComponent.filter(\.isNumber)
        guard let rawTime = Int(digits),
              let source = CGImageSourceCreateWithURL(file as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
            FileHandle.standardError.write(Data(("warning: cannot read \(file.path)\n").utf8))
            return
        }
        recognize(
            image: image,
            timeMs: rawTime * options.filenameScale,
            languages: options.languages,
            encoder: encoder
        )
    }
}

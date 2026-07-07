import Vision
import AppKit
let path = CommandLine.arguments[1]
guard let img = NSImage(contentsOfFile: path),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { exit(1) }
let req = VNRecognizeTextRequest { req, _ in
    guard let obs = req.results as? [VNRecognizedTextObservation] else { return }
    for o in obs {
        guard let t = o.topCandidates(1).first else { continue }
        let b = o.boundingBox  // 정규화, 좌하단 원점
        let s = t.string.replacingOccurrences(of:"\\", with:"\\\\").replacingOccurrences(of:"\"", with:"\\\"")
        // 좌상단 원점 y로 변환: top = 1 - (y+h)
        print("{\"t\":\"\(s)\",\"x\":\(b.origin.x),\"y\":\(1-(b.origin.y+b.height)),\"w\":\(b.width),\"h\":\(b.height)}")
    }
}
req.recognitionLevel = .accurate
req.recognitionLanguages = ["ko-KR","en-US"]
req.usesLanguageCorrection = true
try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])

import Vision
import AppKit

let path = CommandLine.arguments[1]
guard let img = NSImage(contentsOfFile: path),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { exit(1) }

// Vision 요청을 한 번 수행하고 관찰값을 돌려준다. 완료 핸들러는 perform() 안에서 동기 호출된다.
func recognize(minTextHeight: Float?) -> [VNRecognizedTextObservation] {
    var found: [VNRecognizedTextObservation] = []
    let req = VNRecognizeTextRequest { r, _ in
        found = (r.results as? [VNRecognizedTextObservation]) ?? []
    }
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["ko-KR", "en-US"]
    req.usesLanguageCorrection = true
    if let m = minTextHeight { req.minimumTextHeight = m }
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    return found
}

// 기본 설정으로 먼저 시도한다. 일부 이미지(작은 글자·저대비 스캔)에서 Vision은
// 에러 없이 관찰값 0개를 반환하는데, 그때 minimumTextHeight를 명시하면 검출된다.
// 반대로 정상 검출되는 문서에 이 값을 전역으로 걸면 정확도가 떨어지므로(실측 98.1%→96.0%),
// 0개일 때만 재시도한다.
var obs = recognize(minTextHeight: nil)
if obs.isEmpty { obs = recognize(minTextHeight: 0.03125) }

for o in obs {
    guard let t = o.topCandidates(1).first else { continue }
    let b = o.boundingBox  // 정규화, 좌하단 원점
    let s = t.string.replacingOccurrences(of:"\\", with:"\\\\").replacingOccurrences(of:"\"", with:"\\\"")
    // 좌상단 원점 y로 변환: top = 1 - (y+h)
    print("{\"t\":\"\(s)\",\"x\":\(b.origin.x),\"y\":\(1-(b.origin.y+b.height)),\"w\":\(b.width),\"h\":\(b.height)}")
}

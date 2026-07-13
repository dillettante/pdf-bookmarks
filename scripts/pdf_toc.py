#!/usr/bin/env python3
"""OCR한 PDF의 '인쇄된 목차' 페이지를 읽어 PDF 책갈피(outline)를 생성한다.

인쇄 쪽번호 != PDF 물리 페이지 이므로 상수 오프셋을 자동/수동으로 보정한다.

사용법:
  # 1) 목차가 몇 페이지에 있는지 눈으로 찾기 (앞 15쪽 텍스트 덤프)
  pdf_toc.py extract book.pdf
  pdf_toc.py extract book.pdf --pages 1-20

  # 2) 목차 페이지 범위를 주고 책갈피 생성
  pdf_toc.py apply book.pdf --toc-pages 5-8              # 오프셋 자동감지
  pdf_toc.py apply book.pdf --toc-pages 5-8 --offset 12  # 오프셋 수동
  pdf_toc.py apply book.pdf --toc-pages 5-8 --dry-run    # 파싱 결과만 미리보기

OCR 스위치 (extract/apply 공통):
  --ocr off   기본. 텍스트 레이어가 이미 있음(PDF Expert 등으로 OCR 완료). OCR 안 함.
  --ocr auto  텍스트 레이어가 없거나 빈약하면 그때만 ocrmypdf 실행(생스캔 대비).
  --ocr force 무조건 ocrmypdf 재실행(--force-ocr, 기존 OCR 무시).
  --lang kor+eng  OCR 언어(기본). ocrmypdf가 이 결과를 <원본>_ocr.pdf 로 저장.
offset = (해당 제목의 실제 PDF 페이지) - (목차에 인쇄된 쪽번호).
--offset auto(기본): 첫 항목 제목을 본문 전체에서 검색해 오프셋 1회 산출 후 전체 적용.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import fitz  # PyMuPDF

# 줄 끝의 쪽번호를 잡는다:  "제1장 서론 ............ 15"  ->  ("제1장 서론", 15)
LINE_RE = re.compile(r"^(.*?\S)[\s.·…]{2,}(\d{1,5})\s*$")
# 점선 리더가 없는 경우:  "제1장 서론   15"
LINE_RE_LOOSE = re.compile(r"^(.*?\S)\s+(\d{1,5})\s*$")


def has_text_layer(doc, min_chars=100):
    """스캔본(텍스트 레이어 없음) 판별. 앞 30쪽만 봐도 충분."""
    total = sum(len(doc[p].get_text().strip()) for p in range(min(30, doc.page_count)))
    return total >= min_chars


# 네이티브 고품질 OCR 백엔드(플랫폼별). 각 페이지를 렌더 이미지+투명 한글텍스트로 재구성.
VISION_FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"   # macOS 한글폰트
WIN_FONT = r"C:\Windows\Fonts\malgun.ttf"                    # Windows 한글폰트(맑은 고딕)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _visionbox_run(png):
    """macOS Vision: 이미지 → 박스 JSONL. visionbox.swift를 머신별 1회 컴파일."""
    binp, srcf = os.path.join(SCRIPT_DIR, "visionbox"), os.path.join(SCRIPT_DIR, "visionbox.swift")
    if not os.path.exists(binp):
        if sys.platform != "darwin" or not shutil.which("swiftc"):
            sys.exit("[OCR vision] macOS + swiftc 필요(Vision은 macOS 전용). 다른 환경은 --ocr auto.")
        if not os.path.exists(srcf) or subprocess.run(["swiftc", "-O", srcf, "-o", binp]).returncode != 0:
            sys.exit("[OCR vision] visionbox 컴파일 실패.")
    return subprocess.run([binp, png], capture_output=True, text=True).stdout


def _winocr_run(png):
    """Windows.Media.Ocr: 이미지 → 박스 JSONL. scripts/winocr.ps1(PowerShell) 호출."""
    ps1 = os.path.join(SCRIPT_DIR, "winocr.ps1")
    if sys.platform != "win32":
        sys.exit("[OCR winocr] Windows 전용. macOS는 --ocr vision, 그 외는 --ocr auto.")
    if not os.path.exists(ps1):
        sys.exit(f"[OCR winocr] {ps1} 없음.")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, png],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"[OCR winocr] 실패: {r.stderr.strip()[:200]}\n"
                 "→ Windows 10/11 + 한국어 OCR 언어팩 필요(설정>언어>한국어>선택적기능>광학문자인식).")
    return r.stdout


def _ocr_overlay_pdf(path, run_ocr, font, tag, dpi=220, jpg_quality=72):
    """공통: 각 페이지 렌더 → run_ocr(png)로 박스 얻기 → 이미지(JPEG)+투명텍스트로 검색가능 PDF 재구성."""
    if not os.path.exists(font):
        sys.exit(f"[OCR {tag}] 한글폰트 {font} 없음.")
    out_path = f"{os.path.splitext(path)[0]}_ocr.pdf"
    src, out = fitz.open(path), fitz.open()
    total_boxes = 0   # 한 글자도 못 찾았는데 "완료"로 끝나는 것을 막기 위한 카운터
    print(f"[OCR {tag}] {src.page_count}쪽 OCR 중… (~{src.page_count * 0.9 / 60:.0f}분)")
    for i in range(src.page_count):
        pix = src[i].get_pixmap(dpi=dpi)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            png = tf.name
        pix.save(png)
        try:
            stdout = run_ocr(png)
        finally:
            os.remove(png)
        W, H = pix.width * 72 / dpi, pix.height * 72 / dpi
        p = out.new_page(width=W, height=H)
        p.insert_image(p.rect, stream=pix.tobytes("jpg", jpg_quality=jpg_quality))  # JPEG 압축(무압축시 GB급 폭증)
        for line in stdout.splitlines():
            if not line.strip():
                continue
            b = json.loads(line)
            x, y, h = b["x"] * W, b["y"] * H, b["h"] * H
            try:
                p.insert_text((x, y + h * 0.85), b["t"], fontsize=max(4, h * 0.9),
                              fontname="ko", fontfile=font, render_mode=3)
                total_boxes += 1
            except Exception:
                pass
        if (i + 1) % 50 == 0:
            print(f"  … {i + 1}/{src.page_count}쪽")
    # OCR 엔진이 글자를 하나도 못 찾아도 이미지만 든 PDF는 만들어진다.
    # 그걸 "완료"로 반환하면 호출자가 빈 껍데기를 원본으로 덮어쓴다 → 여기서 끊는다.
    if total_boxes == 0:
        sys.exit(f"[OCR {tag}] 전 페이지에서 글자를 하나도 찾지 못했습니다. "
                 f"결과 PDF를 만들지 않습니다(원본 보존). 다른 백엔드를 시도하세요.")
    out.save(out_path, deflate=True, garbage=4)
    print(f"[OCR {tag}] 완료 → {out_path} "
          f"({os.path.getsize(out_path) // 1048576}MB, 텍스트 {total_boxes}줄)")
    return out_path


def vision_ocr_pdf(path):
    if sys.platform != "darwin":
        sys.exit("[OCR vision] macOS 전용. Windows는 --ocr winocr, 그 외는 --ocr auto(tesseract).")
    return _ocr_overlay_pdf(path, _visionbox_run, VISION_FONT, "vision")


def win_ocr_pdf(path):
    if sys.platform != "win32":
        sys.exit("[OCR winocr] Windows 전용. macOS는 --ocr vision, 그 외는 --ocr auto(tesseract).")
    return _ocr_overlay_pdf(path, _winocr_run, WIN_FONT, "winocr")


def ensure_text(path, mode, lang):
    """--ocr 모드에 따라 필요시 OCR 실행. 실제 읽을 PDF 경로를 반환.
    off -> 원본. auto -> 텍스트 없을 때만 tesseract. force -> 무조건 tesseract. vision -> macOS Vision(고품질)."""
    if mode == "off":
        return path
    if mode == "vision":
        return vision_ocr_pdf(path)
    if mode == "winocr":
        return win_ocr_pdf(path)
    doc = fitz.open(path)
    need = mode == "force" or not has_text_layer(doc)
    doc.close()
    if not need:
        print("[OCR] 텍스트 레이어 있음 → 건너뜀")
        return path
    if not shutil.which("ocrmypdf"):
        sys.exit("[OCR] ocrmypdf 미설치. `brew install ocrmypdf tesseract-lang` 필요.")
    out = f"{os.path.splitext(path)[0]}_ocr.pdf"
    cmd = ["ocrmypdf", "-l", lang, "--output-type", "pdf"]
    cmd += ["--force-ocr"] if mode == "force" else ["--skip-text"]
    cmd += [path, out]
    print(f"[OCR] 실행: {' '.join(cmd)}")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"[OCR] ocrmypdf 실패(exit {r.returncode}).")
    print(f"[OCR] 완료 → {out}")
    return out


def parse_range(spec, npages):
    """'5-8' 또는 '5' -> 0-based 페이지 인덱스 리스트."""
    if "-" in spec:
        a, b = spec.split("-", 1)
        a, b = int(a), int(b)
    else:
        a = b = int(spec)
    return [p - 1 for p in range(a, b + 1) if 1 <= p <= npages]


def guess_level(title):
    """제목 앞머리 번호로 목차 깊이를 추정. 못 잡으면 1."""
    t = title.strip()
    if re.match(r"^(제\s*\d+\s*[편부장]|Chapter|CHAPTER|PART)\b", t):
        return 1
    m = re.match(r"^(\d+(?:\.\d+)*)", t)  # 1  /  1.2  /  1.2.3
    if m:
        return m.group(1).count(".") + 1
    return 1


def parse_toc_text(text):
    """목차 페이지 텍스트 -> [(level, title, printed_page), ...]"""
    entries = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = LINE_RE.match(line) or LINE_RE_LOOSE.match(line)
        if not m:
            continue
        title = m.group(1).strip(" .·…").strip()
        page = int(m.group(2))
        if not title or page == 0:
            continue
        entries.append((guess_level(title), title, page))
    return entries


def detect_offset(doc, entries, start_page=0):
    """첫 항목 제목을 본문에서 검색해 상수 오프셋(pdf_page - printed_page) 산출.
    start_page 이전(목차 페이지 자체 등)은 건너뛴다 — 목차에도 제목이 있어 오탐하므로."""
    for level, title, printed in entries:
        needle = re.sub(r"^(제\s*\d+\s*[편부장]|\d+(?:\.\d+)*)\s*", "", title).strip()
        needle = needle or (title.split()[0] if " " in title else title)
        for pno in range(start_page, doc.page_count):
            if doc[pno].search_for(needle):
                return (pno + 1) - printed  # 1-based
    return None


# ---- 폰트 기반 추출 (2단 목차 등 정규식이 안 되는 책용) ----
WORD_RE = re.compile(r"[0-9A-Za-z가-힣]")


def parse_font_levels(spec):
    """'36-45:1,16.5-23:2' -> [(36.0,45.0,1),(16.5,23.0,2)] (밴드→레벨)."""
    bands = []
    for part in spec.split(","):
        rng, lvl = part.split(":")
        lo, hi = rng.split("-")
        bands.append((float(lo), float(hi), int(lvl)))
    return bands


def level_for(size, bands):
    for lo, hi, lvl in bands:
        if lo <= size <= hi:
            return lvl
    return None


def is_wordy(t):
    """장식 OCR 쓰레기('|jlzj|D' 등) 제거. 실제 단어 문자 비율로 판정."""
    core = WORD_RE.findall(t)
    compact = t.replace(" ", "")
    return len(core) >= 3 and len(core) >= 0.5 * max(1, len(compact))


def line_size_text(line):
    spans = line["spans"]
    return max(s["size"] for s in spans), "".join(s["text"] for s in spans).strip()


def scan_by_font(doc, bands, body_start):
    """본문에서 폰트 밴드에 드는 줄을 헤딩으로 수집. 같은 페이지·레벨 연속 줄은 제목으로 합침."""
    raw = []
    for pno in range(body_start - 1, doc.page_count):
        for b in doc[pno].get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                size, txt = line_size_text(l)
                if not txt:
                    continue
                lvl = level_for(size, bands)
                if lvl is not None:
                    raw.append((pno + 1, lvl, txt))
    merged = []
    for page, lvl, txt in raw:
        if merged and merged[-1][0] == page and merged[-1][1] == lvl:
            merged[-1][2] += " " + txt
        else:
            merged.append([page, lvl, txt])
    toc, seen = [], set()
    l1_pages = {p for p, lvl, _ in merged if lvl == 1}
    for page, lvl, txt in merged:
        title = re.sub(r"\s+", " ", txt).strip()
        if not is_wordy(title) or (page, lvl) in seen:
            continue
        if re.fullmatch(r"[\d\s.\-–|]+", title):   # 순수 쪽번호/기호 조각
            continue
        if title.count("|") >= 2:                   # 장식 페이지 OCR 쓰레기
            continue
        if lvl > 1 and page in l1_pages:            # 부와 같은 페이지의 부제 중복
            continue
        if lvl > 1 and (len(WORD_RE.findall(title)) < 5 or re.match(r"^[\-–—•·]", title)):
            continue  # 하위레벨의 저자명·그림라벨 등 너무 짧거나 대시로 시작하는 조각
        seen.add((page, lvl))
        toc.append([lvl, title, page])
    toc.sort(key=lambda e: e[2])
    return toc


def normalize_levels(toc):
    """set_toc 규칙 충족: 첫 항목 레벨1, 레벨 점프는 +1까지만."""
    out, prev = [], 0
    for lvl, title, page in toc:
        lvl = 1 if prev == 0 else min(lvl, prev + 1)
        out.append([lvl, title, page])
        prev = lvl
    return out


TOC_MARK = re.compile(r"목\s*차|차\s*례|table of contents", re.I)


def find_toc_page(doc, start, end):
    """앞부분 [start,end) 페이지에서 '목차/차례/Contents'가 있는 첫 페이지(1-based) 반환."""
    for pno in range(max(0, start), min(end, doc.page_count)):
        if TOC_MARK.search(doc[pno].get_text()):
            return pno + 1
    return None


def detect_page_offset(doc):
    """PDF 물리쪽 − 책 인쇄쪽 오프셋 자동감지(책마다 다름, 하드코딩 금지).
    회전·포맷 무관: PDF쪽이 1 늘 때 같이 1 느는 수만 진짜 쪽번호 → 그 오프셋 C가 일정하게 누적.
    연도(2026)·'총서 17' 같은 상수는 C가 흩어져 탈락. 신뢰도 낮으면 None."""
    n = doc.page_count
    lo = max(3, int(n * 0.05))
    hi = min(lo + 80, max(lo + 1, int(n * 0.85)))
    from collections import Counter
    votes = Counter()
    for pno in range(lo, hi):
        for num in {int(t) for t in re.findall(r"\b\d{1,4}\b", doc[pno].get_text())}:
            if 1 <= num <= n:
                votes[(pno + 1) - num] += 1
    if not votes:
        return None
    common = votes.most_common(2)
    (C, cnt) = common[0]
    second = common[1][1] if len(common) > 1 else 0
    if C < 0 or cnt < (hi - lo) * 0.3 or cnt < second * 2:  # 표본의 30%↑ & 2위의 2배↑
        return None
    return C


def page_label_rules(offset):
    """오프셋 C → set_page_labels 규칙: 앞머리 C쪽은 로마숫자(i..), 본문은 아라비아(1..)."""
    rules = []
    if offset > 0:
        rules.append({"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1})
    rules.append({"startpage": offset, "prefix": "", "style": "D", "firstpagenum": 1})
    return rules


def cmd_extract(args):
    doc = fitz.open(ensure_text(args.pdf, args.ocr, args.lang))
    pages = parse_range(args.pages, doc.page_count) if args.pages else range(min(15, doc.page_count))
    if args.fonts:  # 폰트 크기 히스토그램 (--by-font 밴드 고르기용)
        from collections import Counter
        sizes, sample = Counter(), {}
        for p in pages:
            for b in doc[p].get_text("dict")["blocks"]:
                for l in b.get("lines", []):
                    size, txt = line_size_text(l)
                    if not txt:
                        continue
                    sz = round(size)
                    sizes[sz] += 1
                    sample.setdefault(sz, txt[:45])
        print("size   count | 예시")
        for sz in sorted(sizes, reverse=True):
            print(f"{sz:4}  {sizes[sz]:6} | {sample[sz]}")
        return
    for p in pages:
        print(f"\n===== PDF page {p + 1} =====")
        print(doc[p].get_text().rstrip())


def cmd_apply(args):
    src = ensure_text(args.pdf, args.ocr, args.lang)  # OCR 했으면 <원본>_ocr.pdf
    doc = fitz.open(src)

    if args.from_list:  # 저품질 OCR 등 자동 불가: 목차를 사람이 읽어 목록 제공
        C = detect_page_offset(doc) if args.offset == "auto" else int(args.offset)
        if C is None:
            sys.exit("오프셋 자동감지 실패 — --offset N(=PDF물리쪽−책인쇄쪽)으로 지정.")
        print(f"[from-list] 오프셋 {C} 적용 (인쇄쪽+{C}=PDF쪽; '='접두는 절대 PDF쪽)")
        toc = []
        for line in open(args.from_list, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [x.strip() for x in line.split("|")]
            lvl, title, pg = int(parts[0]), parts[1], parts[2]
            pdf = int(pg[1:]) if pg.startswith("=") else int(pg) + C  # '='=절대PDF쪽
            if 1 <= pdf <= doc.page_count:
                toc.append([lvl, title, pdf])
            else:
                print(f"  [건너뜀] '{title}' -> PDF {pdf}쪽(범위 밖)", file=sys.stderr)
    elif args.by_font:  # 폰트 기반 (2단 목차 등 정규식 불가 책)
        if not args.font_levels:
            sys.exit("--by-font 에는 --font-levels 필요. 예: --font-levels '36-45:1,16.5-23:2' "
                     "(먼저 `extract --fonts`로 크기 확인)")
        toc = scan_by_font(doc, parse_font_levels(args.font_levels), int(args.body_start))
        if not toc:
            sys.exit("폰트 헤딩을 못 찾음. --font-levels 밴드나 --body-start를 조정하세요.")
    else:  # 인쇄목차 파싱 (기본)
        if not args.toc_pages:
            sys.exit("--toc-pages 필요 (또는 2단 목차면 --by-font).")
        toc_idx = parse_range(args.toc_pages, doc.page_count)
        entries = parse_toc_text("\n".join(doc[p].get_text() for p in toc_idx))
        if not entries:
            sys.exit("목차 항목을 하나도 못 찾음. --toc-pages 확인, 2단 목차면 --by-font 사용.")
        if args.offset == "auto":
            offset = detect_offset(doc, entries, start_page=max(toc_idx) + 1)
            if offset is None:
                sys.exit("오프셋 자동감지 실패. --offset N 으로 직접 지정하세요.")
            print(f"[감지된 오프셋] {offset}  (인쇄 1쪽 = PDF {1 + offset}쪽)")
        else:
            offset = int(args.offset)
        toc = []
        for level, title, printed in entries:
            pdf_page = printed + offset
            if 1 <= pdf_page <= doc.page_count:
                toc.append([level, title, pdf_page])
            else:
                print(f"  [건너뜀] '{title}' -> PDF {pdf_page}쪽(범위 밖)", file=sys.stderr)

    # 폰트 모드: 첫 부(part) 이전의 레벨2(머리말 등 앞머리 섹션)는 장이 아니므로 최상위로 승격
    if args.by_font and toc:
        part_pages = [p for lvl, _, p in toc if lvl == 1]
        if part_pages:
            first_part = min(part_pages)
            for e in toc:
                if e[2] < first_part and e[0] > 1:
                    e[0] = 1

    # #1: 목차/차례/Contents 페이지를 최상위(레벨1) 책갈피로 자동 추가 (from-list는 사용자가 명시하므로 제외)
    if not args.from_list:
        toc_pg = find_toc_page(doc, 0, int(args.body_start) - 1) if args.by_font else min(toc_idx) + 1
        if toc_pg:
            toc = [e for e in toc if e[1] not in ("목차", "목 차", "차례")]  # 중복 방지
            toc.append([1, "목차", toc_pg])
    toc.sort(key=lambda e: e[2])
    toc = normalize_levels(toc)

    print(f"\n{len(toc)}개 책갈피:")
    for level, title, pg in toc:
        print(f"  {'  ' * (level - 1)}{title}  ->  p.{pg}")

    if args.dry_run:
        print("\n(dry-run: 저장 안 함)")
        return

    doc.set_toc(toc)

    # 페이지 라벨: 뷰어 페이지번호를 책 인쇄쪽번호에 맞춤(오프셋 자동감지, 책마다 다름)
    if args.page_labels:
        C = int(args.label_offset) if args.label_offset is not None else detect_page_offset(doc)
        if C is None:
            print("[페이지라벨] 오프셋 자동감지 실패(신뢰도 낮음) — 라벨 생략. 필요시 --label-offset N 지정.")
        else:
            doc.set_page_labels(page_label_rules(C))
            print(f"[페이지라벨] 오프셋 {C} 적용 (PDF {C + 1}쪽 = 인쇄 1쪽, 뷰어가 인쇄번호 표시)")

    # #3: 항상 별도 파일 '<원본>-marked.pdf'로 저장(사용자가 -o로 지정하면 그쪽).
    #     제자리 저장을 피하므로 클라우드 동기화 폴더의 되돌림 문제도 원천 차단.
    if args.output:
        out = args.output
    else:
        stem, ext = os.path.splitext(src)
        out = f"{stem}-marked{ext}"
    doc.save(out, encryption=fitz.PDF_ENCRYPT_KEEP)  # full save, 새 파일
    print(f"\n저장: {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_ocr_args(p):
        p.add_argument("--ocr", choices=["off", "auto", "force", "vision", "winocr"], default="off",
                       help="off/auto(tesseract, 크로스플랫폼)/force/vision(macOS 고품질)/winocr(Windows 고품질)")
        p.add_argument("--lang", default="kor+eng", help="OCR 언어(기본: kor+eng)")

    e = sub.add_parser("extract", help="목차 위치 찾기용 텍스트 덤프")
    e.add_argument("pdf")
    e.add_argument("--pages", help="예: 1-20 (기본: 앞 15쪽)")
    e.add_argument("--fonts", action="store_true", help="텍스트 대신 폰트 크기 히스토그램 출력(--by-font 밴드용)")
    add_ocr_args(e)
    e.set_defaults(func=cmd_extract)

    a = sub.add_parser("apply", help="책갈피 생성")
    a.add_argument("pdf")
    a.add_argument("--toc-pages", help="인쇄목차가 있는 PDF 페이지 범위, 예: 5-8 (기본 모드)")
    a.add_argument("--offset", default="auto", help="'auto'(기본) 또는 정수")
    a.add_argument("--by-font", action="store_true",
                   help="2단 목차 등 정규식 불가 책: 본문 폰트 크기로 헤딩 추출")
    a.add_argument("--from-list",
                   help="저품질 OCR 등: 사람이 읽은 목차 목록파일(줄마다 'level | 제목 | 인쇄쪽', "
                        "'=N'은 절대PDF쪽). 오프셋 자동적용")
    a.add_argument("--font-levels", help="--by-font 밴드, 예: '36-45:1,16.5-23:2'")
    a.add_argument("--body-start", default="1", help="--by-font 스캔 시작 페이지(1-based, 목차 뒤 본문)")
    a.add_argument("--page-labels", action="store_true",
                   help="뷰어 페이지번호를 책 인쇄쪽번호에 맞춤(오프셋 자동감지)")
    a.add_argument("--label-offset", help="페이지라벨 오프셋 수동지정(PDF−인쇄). 미지정 시 자동감지")
    add_ocr_args(a)
    a.add_argument("-o", "--output", help="출력 파일(기본: 원본에 증분 저장)")
    a.add_argument("--dry-run", action="store_true", help="결과만 출력, 저장 안 함")
    a.set_defaults(func=cmd_apply)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

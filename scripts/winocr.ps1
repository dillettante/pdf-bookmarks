# winocr.ps1 — 이미지 1장을 Windows.Media.Ocr(OS 내장)로 OCR → 줄마다 박스 JSONL 출력.
# 출력 형식은 visionbox.swift와 동일: {"t":"텍스트","x":..,"y":..,"w":..,"h":..}  (정규화, 좌상단 원점)
# 요구사항: Windows 10/11 + 한국어 OCR 언어팩(설정>언어>한국어>선택적 기능>광학 문자 인식).
param([Parameter(Mandatory = $true)][string]$ImagePath)
$ErrorActionPreference = "Stop"

# --- WinRT 타입 로드 ---
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

# --- IAsyncOperation<T> 동기 대기 헬퍼 ---
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $t) {
    $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    $task.Result
}

# --- 이미지 로드 → SoftwareBitmap(Bgra8) ---
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bmp = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$bmp = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert($bmp, [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8)
$W = [double]$decoder.PixelWidth
$H = [double]$decoder.PixelHeight

# --- OCR 엔진(한국어 우선, 실패 시 사용자 프로필 언어) ---
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new("ko"))
if ($null -eq $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if ($null -eq $engine) {
    [Console]::Error.WriteLine("OCR 엔진 생성 실패: 한국어 OCR 언어팩을 설치하세요.")
    exit 1
}

$result = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])

$out = [System.Text.StringBuilder]::new()
foreach ($line in $result.Lines) {
    # 줄 bbox = 단어 사각형들의 합집합
    $x1 = [double]::MaxValue; $y1 = [double]::MaxValue; $x2 = 0.0; $y2 = 0.0
    foreach ($wd in $line.Words) {
        $r = $wd.BoundingRect
        if ($r.X -lt $x1) { $x1 = $r.X }
        if ($r.Y -lt $y1) { $y1 = $r.Y }
        if (($r.X + $r.Width) -gt $x2) { $x2 = $r.X + $r.Width }
        if (($r.Y + $r.Height) -gt $y2) { $y2 = $r.Y + $r.Height }
    }
    if ($x2 -le $x1) { continue }
    $t = $line.Text.Replace('\', '\\').Replace('"', '\"')
    $nx = $x1 / $W; $ny = $y1 / $H; $nw = ($x2 - $x1) / $W; $nh = ($y2 - $y1) / $H
    [void]$out.AppendLine('{"t":"' + $t + '","x":' + $nx + ',"y":' + $ny + ',"w":' + $nw + ',"h":' + $nh + '}')
}
[Console]::Out.Write($out.ToString())

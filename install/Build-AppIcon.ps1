# Audion Browsers Portable - the program icon from its vector source.
#
#   system_core\icons\app.svg     the icon, drawn anew for every size
#   system_core\icons\app-16.svg  optional: the 16 px frame drawn by hand on the
#                                 pixel grid, for when the big drawing shrunk to
#                                 16 px loses its shape
#   -> system_core\icons\app.ico  the window, and Start.exe once it is rebuilt
#   -> system_core\icons\app.png  the same drawing at 1024 px
#
# The SVG is drawn by Microsoft Edge without a window: a full SVG engine, so a new
# version of the drawing needs no change here. Every frame sits on one page at
# exact pixels, and one screenshot with a transparent background is cut into the
# frames. Edge runs with a throwaway profile - the owner's Edge is not touched.
#
# The frames are 16, 20, 24, 32, 40, 48, 64 and 256: 20 and 40 are what a display
# at 125% takes where 16 and 32 would be taken at 100%, and without them Windows
# scales a neighbour. Frames up to 64 px go into the .ico as classic 32-bit
# images and only 256 px as PNG - small frames stored as PNG are read by Explorer
# and misread elsewhere.
#
# Run:   powershell -NoProfile -ExecutionPolicy Bypass -File install\Build-AppIcon.ps1
# Then:  install\Build-StartLauncher.cmd   (Start.exe carries its own copy of the icon)

param([string]$ProjectRoot = "")

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) { $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }
Add-Type -AssemblyName System.Drawing

$icons = Join-Path $ProjectRoot "system_core\icons"
$svg = Join-Path $icons "app.svg"
$svg16 = Join-Path $icons "app-16.svg"
if (-not (Test-Path -LiteralPath $svg)) { throw "system_core\icons\app.svg is missing." }
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $edge) { throw "Microsoft Edge is not found: it draws the SVG." }

$work = Join-Path $ProjectRoot "._runtime\icon"
if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
New-Item -ItemType Directory -Force -Path $work | Out-Null

# ------------------------------------------------ one page, every frame at exact pixels
$sizes = @(16, 20, 24, 32, 40, 48, 64, 256)
$posterSize = 1024
$frames = @()
$x = 8
foreach ($size in $sizes) {
    $source = if ($size -eq 16 -and (Test-Path -LiteralPath $svg16)) { $svg16 } else { $svg }
    $frames += [pscustomobject]@{ Size = $size; X = $x; Y = 8; Source = $source }
    $x += $size + 8
}
# The 1024 px picture goes under the row of frames.
$poster = [pscustomobject]@{ Size = $posterSize; X = 8; Y = 256 + 16; Source = $svg }
$width = [Math]::Max($x, $posterSize + 16)
$height = $poster.Y + $posterSize + 8
$images = (@($frames) + $poster | ForEach-Object {
    $url = ([Uri]$_.Source).AbsoluteUri
    "<img src=`"$url`" style=`"position:absolute;left:$($_.X)px;top:$($_.Y)px;width:$($_.Size)px;height:$($_.Size)px`">"
}) -join "`n"
$page = Join-Path $work "frames.html"
[IO.File]::WriteAllText($page, "<!doctype html><html><head><style>html,body{margin:0;background:transparent;overflow:hidden}</style></head><body>`n$images`n</body></html>", (New-Object Text.UTF8Encoding($false)))

$shot = Join-Path $work "frames.png"
$edgeProfile = Join-Path $work "edge-profile"
$arguments = @("--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--force-device-scale-factor=1",
    "--default-background-color=00000000", "--user-data-dir=`"$edgeProfile`"", "--window-size=$width,$height",
    "--screenshot=`"$shot`"", "`"$(([Uri]$page).AbsoluteUri)`"")
$edgeRun = Start-Process -FilePath $edge -ArgumentList $arguments -PassThru -WindowStyle Hidden
if (-not $edgeRun.WaitForExit(60000)) { $edgeRun.Kill(); throw "Edge did not finish drawing in 60 s." }
if (-not (Test-Path -LiteralPath $shot)) { throw "Edge made no screenshot." }

# ------------------------------------------------ cut the frames
$sheet = New-Object Drawing.Bitmap $shot
if ($sheet.Width -lt $width -or $sheet.Height -lt $height) { throw "The screenshot is $($sheet.Width)x$($sheet.Height), smaller than the page ($width x $height)." }
$cut = @()
foreach ($frame in @($frames) + $poster) {
    $bitmap = $sheet.Clone((New-Object Drawing.Rectangle $frame.X, $frame.Y, $frame.Size, $frame.Size), [Drawing.Imaging.PixelFormat]::Format32bppArgb)
    # A see-through corner proves the background came out transparent, not white.
    if ($bitmap.GetPixel(0, 0).A -ge 128) { throw "The $($frame.Size) px frame has no transparent background." }
    # And a drawing in the middle proves the SVG was drawn at all.
    if ($bitmap.GetPixel([int]($frame.Size / 2), [int]($frame.Size / 2)).A -lt 128) { throw "The $($frame.Size) px frame came out empty." }
    $bitmap.Save((Join-Path $work "app-$($frame.Size).png"), [Drawing.Imaging.ImageFormat]::Png)
    $cut += [pscustomobject]@{ Size = $frame.Size; Bitmap = $bitmap }
}
$sheet.Dispose()
$posterBitmap = ($cut | Where-Object { $_.Size -eq $posterSize }).Bitmap
$cut = @($cut | Where-Object { $_.Size -ne $posterSize })

# ------------------------------------------------ the .ico
function Get-IconImage([Drawing.Bitmap]$Bitmap, [int]$Size) {
    $stream = New-Object IO.MemoryStream
    if ($Size -ge 256) {
        $Bitmap.Save($stream, [Drawing.Imaging.ImageFormat]::Png)
        return ,$stream.ToArray()
    }
    # BITMAPINFOHEADER; the height counts the colour rows and the mask rows together.
    $maskRow = [int]([Math]::Floor(($Size + 31) / 32) * 4)
    $writer = New-Object IO.BinaryWriter $stream
    $writer.Write([uint32]40); $writer.Write([int32]$Size); $writer.Write([int32]($Size * 2))
    $writer.Write([uint16]1); $writer.Write([uint16]32); $writer.Write([uint32]0)
    $writer.Write([uint32]($Size * $Size * 4 + $maskRow * $Size))
    $writer.Write([int32]0); $writer.Write([int32]0); $writer.Write([uint32]0); $writer.Write([uint32]0)
    for ($y = $Size - 1; $y -ge 0; $y--) {
        for ($x = 0; $x -lt $Size; $x++) {
            $c = $Bitmap.GetPixel($x, $y)
            $writer.Write([byte]$c.B); $writer.Write([byte]$c.G); $writer.Write([byte]$c.R); $writer.Write([byte]$c.A)
        }
    }
    # The AND mask: a set bit - transparent pixel. Rows bottom-up, padded to 4 bytes.
    for ($y = $Size - 1; $y -ge 0; $y--) {
        $row = New-Object byte[] $maskRow
        for ($x = 0; $x -lt $Size; $x++) {
            if ($Bitmap.GetPixel($x, $y).A -eq 0) { $row[[int][Math]::Floor($x / 8)] = $row[[int][Math]::Floor($x / 8)] -bor (0x80 -shr ($x % 8)) }
        }
        $writer.Write($row)
    }
    $writer.Flush()
    return ,$stream.ToArray()
}

$data = @($cut | ForEach-Object { ,(Get-IconImage $_.Bitmap $_.Size) })
$ico = New-Object IO.MemoryStream
$writer = New-Object IO.BinaryWriter $ico
$writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]$cut.Count)
$offset = 6 + 16 * $cut.Count
for ($i = 0; $i -lt $cut.Count; $i++) {
    # 256 does not fit a byte and is written as 0.
    $side = if ($cut[$i].Size -ge 256) { 0 } else { $cut[$i].Size }
    $writer.Write([byte]$side); $writer.Write([byte]$side); $writer.Write([byte]0); $writer.Write([byte]0)
    $writer.Write([uint16]1); $writer.Write([uint16]32)
    $writer.Write([uint32]$data[$i].Length); $writer.Write([uint32]$offset)
    $offset += $data[$i].Length
}
foreach ($bytes in $data) { $writer.Write([byte[]]$bytes) }
$writer.Flush()

# The new icon must open as an icon before it replaces the old one.
$built = Join-Path $work "app.ico"
[IO.File]::WriteAllBytes($built, $ico.ToArray())
$check = New-Object Drawing.Icon $built, 32, 32
if ($check.Width -ne 32) { throw "The built icon does not give its 32 px frame." }
$check.Dispose()

Copy-Item -LiteralPath $built -Destination (Join-Path $icons "app.ico") -Force
$posterBitmap.Save((Join-Path $icons "app.png"), [Drawing.Imaging.ImageFormat]::Png)
$posterBitmap.Dispose()
foreach ($frame in $cut) { $frame.Bitmap.Dispose() }
Remove-Item -LiteralPath $edgeProfile -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ("[OK] app.ico: {0} frames ({1}), {2:N0} KB -> system_core\icons\app.ico" -f $cut.Count, ($sizes -join ", "), ($ico.Length / 1KB))
Write-Host ("[OK] app.png: {0} px -> system_core\icons\app.png" -f $posterSize)

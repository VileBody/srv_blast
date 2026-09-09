Add-Type -AssemblyName System.Drawing

$root = Join-Path $PSScriptRoot '..\frontend\public\assets\figma'
$items = @(
  @{ Source = 'pr-head-blast.png'; Target = 'pr-number-blast.png'; ChipX = 179; ChipY = 100 },
  @{ Source = 'pr-head-glow.png'; Target = 'pr-number-glow.png'; ChipX = 179; ChipY = 98 },
  @{ Source = 'pr-head-impulse.png'; Target = 'pr-number-impulse.png'; ChipX = 181; ChipY = 98 }
)

foreach ($item in $items) {
  $sourcePath = Join-Path $root $item.Source
  $targetPath = Join-Path $root $item.Target
  $source = [System.Drawing.Bitmap]::FromFile($sourcePath)
  $target = New-Object System.Drawing.Bitmap $source.Width, $source.Height, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)

  for ($y = 0; $y -lt $source.Height; $y++) {
    for ($x = 0; $x -lt $source.Width; $x++) {
      if ($x -ge $item.ChipX -and $x -lt ($item.ChipX + 150) -and $y -ge $item.ChipY -and $y -lt ($item.ChipY + 60)) {
        $target.SetPixel($x, $y, [System.Drawing.Color]::Transparent)
        continue
      }

      $pixel = $source.GetPixel($x, $y)
      $minimum = [Math]::Min($pixel.R, [Math]::Min($pixel.G, $pixel.B))
      $maximum = [Math]::Max($pixel.R, [Math]::Max($pixel.G, $pixel.B))
      $chroma = $maximum - $minimum
      $alpha = [Math]::Max(0, [Math]::Min(255, [int](($minimum - 82) * 2.35)))
      if ($chroma -gt 58) { $alpha = [int]($alpha * 0.28) }
      if ($alpha -lt 8) { $alpha = 0 }
      $target.SetPixel($x, $y, [System.Drawing.Color]::FromArgb($alpha, $pixel.R, $pixel.G, $pixel.B))
    }
  }

  $target.Save($targetPath, [System.Drawing.Imaging.ImageFormat]::Png)
  $target.Dispose()
  $source.Dispose()
}

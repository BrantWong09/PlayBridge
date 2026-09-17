Add-Type -AssemblyName System.Drawing
$L = [int]$args[0]; $T = [int]$args[1]; $R = [int]$args[2]; $B = [int]$args[3]
$w = $R - $L; $h = $B - $T
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($L, $T, 0, 0, (New-Object System.Drawing.Size($w, $h)))
$out = Join-Path $PSScriptRoot "mpv_shot.png"
$bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output ("saved {0} {1}x{2}" -f $out, $w, $h)

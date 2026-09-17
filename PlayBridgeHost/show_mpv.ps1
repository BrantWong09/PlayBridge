Add-Type -AssemblyName System.Drawing

$sig = @"
using System;
using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr FindWindow(string cls, string title);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
}
"@
Add-Type -TypeDefinition $sig

$h = [W]::FindWindow($null, "PlayBridge")
if ($h -eq [IntPtr]::Zero) { Write-Output "NOTFOUND"; exit 1 }
Write-Output ("hwnd={0} iconic={1}" -f $h, [W]::IsIconic($h))

[void][W]::ShowWindow($h, 9)
[void][W]::BringWindowToTop($h)
[void][W]::SetForegroundWindow($h)
Start-Sleep -Milliseconds 1500

$r = New-Object W+RECT
[void][W]::GetWindowRect($h, [ref]$r)
Write-Output ("rect={0},{1},{2},{3} iconic={4}" -f $r.L, $r.T, $r.R, $r.B, [W]::IsIconic($h))

$w = $r.R - $r.L; $ht = $r.B - $r.T
if ($w -le 0 -or $ht -le 0) { Write-Output "BADSIZE"; exit 1 }
$bmp = New-Object System.Drawing.Bitmap($w, $ht)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.L, $r.T, 0, 0, (New-Object System.Drawing.Size($w, $ht)))
$out = Join-Path $PSScriptRoot "mpv_shot.png"
$bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output ("saved={0} {1}x{2}" -f $out, $w, $ht)

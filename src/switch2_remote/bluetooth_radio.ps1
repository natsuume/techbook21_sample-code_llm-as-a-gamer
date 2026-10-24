# Windows の Bluetooth 無線をオフにし、待ってからオンに戻す (Windows PowerShell 5.1)
# このファイルは BOM なし UTF-8 で、PowerShell 5.1 は CP932 として読むため、日本語を含む行は ASCII の文字で終える (改行がコメントに取り込まれるのを防ぐ)
# 起動引数: -OffSeconds <秒> (オフにしてからオンに戻すまでの待ち。既定 5)
# 処理: WinRT の Windows.Devices.Radios を使う (Radio)
#   1. [Windows.Devices.Radios.Radio]::RequestAccessAsync() の結果が Allowed でなければ失敗 (radio access denied: <結果>)
#   2. [Windows.Devices.Radios.Radio]::GetRadiosAsync() から Kind が Bluetooth の Radio を選ぶ。無ければ失敗 (no bluetooth radio)
#   3. SetStateAsync(Off) -> -OffSeconds 秒待つ -> SetStateAsync(On)
#   WinRT の非同期の結果は System.WindowsRuntimeSystemExtensions.AsTask で .NET の Task にして待つ (await)
# 出力: 標準出力の文字コードは BOM なし UTF-8 で、1 行だけ出力する (UTF-8 without BOM)
#   成功: "ok" (終了コード 0)
#   失敗: "error <reason>" (終了コード 1。例: error radio access denied: DeniedByUser / error no bluetooth radio)
param([int]$OffSeconds = 5)
$ErrorActionPreference = "Stop"

# [Console]::Out は OutputEncoding を設定すると作り直されるため、設定してから取り出す (UTF-8 without BOM)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$out = [Console]::Out

$out.WriteLine("error not implemented"); $out.Flush()
exit 1

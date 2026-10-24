# Windows の Bluetooth 無線をオフにし、待ってからオンに戻す (Windows PowerShell 5.1)
# このファイルは BOM なし UTF-8 で、PowerShell 5.1 は CP932 として読むため、日本語を含む行は ASCII の文字で終える (改行がコメントに取り込まれるのを防ぐ)
# 起動引数: -OffSeconds <秒> (オフになったことを確かめてからオンに戻すまでの待ち。既定 5)
# 処理: WinRT の Windows.Devices.Radios を使う (Radio)
#   1. [Windows.Devices.Radios.Radio]::RequestAccessAsync() の結果が Allowed でなければ失敗 (radio access denied: <結果>)
#   2. [Windows.Devices.Radios.Radio]::GetRadiosAsync() から Kind が Bluetooth の Radio を選ぶ。無ければ失敗 (no bluetooth radio)
#   3. SetStateAsync(Off) -> State が Off になるまで待つ -> -OffSeconds 秒待つ -> SetStateAsync(On) -> State が On になるまで待つ (wait for each state)
#      SetStateAsync の結果 (RadioAccessStatus) が Allowed でなければ失敗 (radio state change denied: <結果>)
#      SetStateAsync の後は Radio の State を $StatePollMs ごとに確かめ、$StateTimeoutSeconds 以内に目的の状態にならなければ失敗 (timeout)
#      (radio did not turn <off|on>: <その時点の State>)
#      オフの SetStateAsync が拒否されたら、オンは試さずに終える (the radio was not turned off)
#      オフの SetStateAsync が Allowed なら、オフの待ちの期限切れや例外があってもオンを必ず試す (finally)
#      オフの待ちが期限切れで、オンも失敗したときの理由は "radio did not turn off: <State>; <オンの失敗の理由>" とする (both reasons)
#   WinRT の非同期の結果は System.WindowsRuntimeSystemExtensions.AsTask で .NET の Task にして待つ (await)
#   想定外の例外は、そのメッセージを 1 行にして失敗として出力する (error <message>)
# 出力: 標準出力の文字コードは BOM なし UTF-8 で、1 行だけ出力する (UTF-8 without BOM)
#   成功: "ok" (終了コード 0)
#   失敗: "error <reason>" (終了コード 1。例: error radio access denied: DeniedByUser / error no bluetooth radio)
param([int]$OffSeconds = 5)
$ErrorActionPreference = "Stop"
$StatePollMs = 500  # SetStateAsync の後に State を確かめる間隔 (ms)
$StateTimeoutSeconds = 10  # SetStateAsync の後に State が目的の状態になるまで待つ上限 (秒)

# [Console]::Out は OutputEncoding を設定すると作り直されるため、設定してから取り出す (UTF-8 without BOM)
# コンソールの無い環境では設定が例外になることがあるため、失敗しても既定の文字コードのまま続ける (keep the default encoding)
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch { }
$out = [Console]::Out

# WinRT の IAsyncOperation<T> を .NET の Task<T> にして完了まで待ち、結果を返す (AsTask)
function Wait-AsyncOperation($Operation, [Type]$ResultType) {
    $asTask = $script:AsTaskGeneric.MakeGenericMethod($ResultType)
    $task = $asTask.Invoke($null, @($Operation))
    $task.Wait(-1) | Out-Null
    return $task.Result
}

# Radio の State が Target になるまで $StatePollMs ごとに確かめる。$StateTimeoutSeconds 以内になれば $true (poll)
function Wait-RadioState($Radio, $Target) {
    $deadline = [DateTime]::UtcNow.AddSeconds($StateTimeoutSeconds)
    while ($Radio.State -ne $Target) {
        if ([DateTime]::UtcNow -ge $deadline) { return $false }
        Start-Sleep -Milliseconds $StatePollMs
    }
    return $true
}

# 無線を Target (On / Off) にし、State が Target になるまで待つ。失敗なら理由、成功なら $null を返す (set and wait)
function Set-RadioState($Radio, $Target, [string]$Name) {
    $status = Wait-AsyncOperation ($Radio.SetStateAsync($Target)) ([Windows.Devices.Radios.RadioAccessStatus])
    if ($status -ne [Windows.Devices.Radios.RadioAccessStatus]::Allowed) {
        return "radio state change denied: $status"
    }
    if (-not (Wait-RadioState $Radio $Target)) {
        return "radio did not turn ${Name}: $($Radio.State)"
    }
    return $null
}

$result = "error unknown"
$exitCode = 1
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    # 引数 1 つの AsTask(IAsyncOperation<T>) を探す (同名の多重定義が複数あるため)
    $script:AsTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq "AsTask" -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]
    # WinRT の型を読み込む (型名だけを書くと読み込まれる)
    [Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
    [Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
    [Windows.Devices.Radios.RadioState,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
    [Windows.Devices.Radios.RadioKind,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null

    $access = Wait-AsyncOperation ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
    if ($access -ne [Windows.Devices.Radios.RadioAccessStatus]::Allowed) {
        $result = "error radio access denied: $access"
    } else {
        $radios = Wait-AsyncOperation ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
        $bluetooth = $radios | Where-Object { $_.Kind -eq [Windows.Devices.Radios.RadioKind]::Bluetooth } | Select-Object -First 1
        if ($null -eq $bluetooth) {
            $result = "error no bluetooth radio"
        } else {
            $offStatus = Wait-AsyncOperation ($bluetooth.SetStateAsync([Windows.Devices.Radios.RadioState]::Off)) ([Windows.Devices.Radios.RadioAccessStatus])
            if ($offStatus -ne [Windows.Devices.Radios.RadioAccessStatus]::Allowed) {
                $result = "error radio state change denied: $offStatus"
            } else {
                $offError = $null
                $onError = "radio was not turned on"
                try {
                    if (Wait-RadioState $bluetooth ([Windows.Devices.Radios.RadioState]::Off)) {
                        Start-Sleep -Seconds $OffSeconds
                    } else {
                        $offError = "radio did not turn off: $($bluetooth.State)"
                    }
                }
                finally {
                    # 無線をオフのまま残さないよう、オフの待ちが失敗してもオンに戻す (always turn the radio back on)
                    $onError = Set-RadioState $bluetooth ([Windows.Devices.Radios.RadioState]::On) "on"
                }
                if ($null -ne $offError -and $null -ne $onError) {
                    $result = "error ${offError}; $onError"
                } elseif ($null -ne $offError) {
                    $result = "error $offError"
                } elseif ($null -ne $onError) {
                    $result = "error $onError"
                } else {
                    $result = "ok"
                    $exitCode = 0
                }
            }
        }
    }
}
catch {
    $message = $_.Exception.GetBaseException().Message -replace "[\r\n]+", " "
    $result = "error $message"
    $exitCode = 1
}
$out.WriteLine($result); $out.Flush()
exit $exitCode

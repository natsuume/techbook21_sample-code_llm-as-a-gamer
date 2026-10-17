# Pico 2 W (PlayAbility Receiver) へ Switch 用ゲームパッドレポートを送る常駐ブリッジ。
# 標準入力から 1 行 1 コマンドを受け取り、標準出力へ応答を返す。
#   tap <BUTTON> [押下ms]   BUTTON: A B X Y L R ZL ZR PLUS MINUS HOME UP RIGHT DOWN LEFT LR
#   quit
# 応答: "ack <BUTTON> <epoch_ms>" (送信開始直前) / "done <BUTTON> <epoch_ms>" (ニュートラル送信完了後)
param([string]$PortName = "COM3")
$ErrorActionPreference = "Stop"

Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.IO.Ports;
public static class PicoSwitchSender
{
    private const byte END = 0xC0; private const byte ESC = 0xDB; private const byte ESC_END = 0xDC; private const byte ESC_ESC = 0xDD;
    private static uint Crc32(byte[] data) { uint crc = 0xFFFFFFFF; foreach (byte v in data) { crc ^= v; for (int i = 0; i < 8; i++) { crc = ((crc & 1) != 0) ? (crc >> 1) ^ 0xEDB88320 : crc >> 1; } } return ~crc; }
    private static void AppendEscaped(List<byte> o, byte v) { if (v == END) { o.Add(ESC); o.Add(ESC_END); } else if (v == ESC) { o.Add(ESC); o.Add(ESC_ESC); } else { o.Add(v); } }
    public static byte[] EncodeReport(byte[] report)
    {
        byte[] payload = new byte[12]; payload[0] = 1; payload[1] = 2; payload[2] = 8; payload[3] = 0; Array.Copy(report, 0, payload, 4, 8);
        uint crc = Crc32(payload); List<byte> p = new List<byte>(); p.Add(END);
        foreach (byte v in payload) AppendEscaped(p, v);
        for (int i = 0; i < 4; i++) AppendEscaped(p, (byte)((crc >> (i * 8)) & 0xFF));
        p.Add(END); return p.ToArray();
    }
    public static void SendReport(SerialPort port, byte[] report) { byte[] pk = EncodeReport(report); port.Write(pk, 0, pk.Length); }
}
"@

function New-Report([byte]$b1 = 0, [byte]$b2 = 0, [byte]$dpad = 15) { return [byte[]]@($b1, $b2, $dpad, 128, 128, 128, 128, 0) }
$reports = @{
    "A" = New-Report 0x04; "B" = New-Report 0x02; "X" = New-Report 0x08; "Y" = New-Report 0x01
    "L" = New-Report 0x10; "R" = New-Report 0x20; "ZL" = New-Report 0x40; "ZR" = New-Report 0x80; "LR" = New-Report 0x30
    "MINUS" = New-Report 0 0x01; "PLUS" = New-Report 0 0x02; "HOME" = New-Report 0 0x10
    "UP" = New-Report 0 0 0; "RIGHT" = New-Report 0 0 2; "DOWN" = New-Report 0 0 4; "LEFT" = New-Report 0 0 6
}
$neutral = New-Report
function Send-ForDuration([System.IO.Ports.SerialPort]$port, [byte[]]$report, [int]$ms) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $ms) { [PicoSwitchSender]::SendReport($port, $report); Start-Sleep -Milliseconds 10 }
}
function Now-Ms { return [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() }

$port = [System.IO.Ports.SerialPort]::new($PortName, 115200, [System.IO.Ports.Parity]::None, 8, [System.IO.Ports.StopBits]::One)
$port.WriteTimeout = 1000
$out = [Console]::Out
try {
    $port.Open()
    Send-ForDuration $port $neutral 200
    $out.WriteLine("ready $PortName $(Now-Ms)"); $out.Flush()
    while ($null -ne ($line = [Console]::In.ReadLine())) {
        $parts = $line.Trim().Split(" ")
        if ($parts[0] -eq "quit") { break }
        if ($parts[0] -eq "tap" -and $parts.Length -ge 2 -and $reports.ContainsKey($parts[1])) {
            $name = $parts[1]; $hold = if ($parts.Length -ge 3) { [int]$parts[2] } else { 120 }
            $out.WriteLine("ack $name $(Now-Ms)"); $out.Flush()
            Send-ForDuration $port $reports[$name] $hold
            Send-ForDuration $port $neutral 100
            $out.WriteLine("done $name $(Now-Ms)"); $out.Flush()
        } else {
            $out.WriteLine("error unknown command: $line"); $out.Flush()
        }
    }
}
finally {
    if ($port.IsOpen) {
        try { Send-ForDuration $port $neutral 200 } catch { $out.WriteLine("warning: neutral report failed: $($_.Exception.Message)") }
        try { $port.Close() } catch { $out.WriteLine("warning: port close failed: $($_.Exception.Message)") }
    }
    try { $port.Dispose() } catch { $out.WriteLine("warning: port dispose failed: $($_.Exception.Message)") }
    $out.WriteLine("closed"); $out.Flush()
}

# Pico 2 W (PlayAbility Receiver) へ Switch 用の 8 バイトのゲームパッドレポートを SLIP + CRC32 で送る常駐ブリッジ (Windows PowerShell 5.1)
# このファイルは BOM なし UTF-8 で、PowerShell 5.1 は CP932 として読むため、日本語を含む行は ASCII の文字で終える (改行がコメントに取り込まれるのを防ぐ)
# 標準入力から 1 行 1 コマンドを受け取り、標準出力へ 1 行で応答する (stdin / stdout)
# 起動引数: -PortName <PORT> (既定 COM3)、-KeepSendingNeutral (中立のレポートも書き込み続ける。既定は無効 = off)
# 標準出力の文字コードは BOM なし UTF-8 (BOM が付くと最初の ready の行の判定が崩れるため。UTF-8 without BOM)
# 起動時: COM ポート (既定 COM3, 115200bps) を開き、中立を 200ms 送ってから出力: "ready <PORT> <epoch_ms>"
#   set <HEX>   HEX は 8 バイトのレポートを表す 16 桁の小文字 16 進 (例: A の押下は 04000f8080808000)
#               現在のレポートを差し替え、送信スレッドが新しいレポートを 1 回書き込んだ後に出力: "ack <HEX> <epoch_ms>"
#               不正な行への出力: "error <reason>"
#               新しいレポートを $SetReportTimeoutMs 以内に書き込めなかったときの出力: "error report was not written in time: <HEX>"
#   quit        送信スレッドを止め、中立を 200ms 送り、ポートを閉じてから出力: "closed" (標準入力の EOF でも同じ)
# 送信スレッド (Add-Type の C# クラス内の Thread):
#   現在のレポートが中立以外なら、10ms ごとに書き込み続ける (SEND_INTERVAL_MS。Windows の既定のタイマー分解能では実際の間隔は約 15ms)
#   中立に変わったら中立を 100ms 書き込み、次の set まで書き込みを止める (NEUTRAL_TAIL_MS)
#   -KeepSendingNeutral を付けて起動すると、中立も 10ms ごとに書き込み続ける (NEUTRAL_TAIL_MS で止めない)
#   書き込みが例外になったら出力: "fatal <message>" (中立を 200ms 送ることを試みてから、プロセスを終了コード 1 で終える)
#   C# クラスが受け取る書き込み先: System.IO.Stream (実機では $port.BaseStream、偽の Stream でも動く)
#   C# のスレッドからは PowerShell のスクリプトブロックを呼ばない (スレッドに Runspace が無いため)
param([string]$PortName = "COM3", [switch]$KeepSendingNeutral)
$ErrorActionPreference = "Stop"
$SetReportTimeoutMs = 1000  # set の後、送信スレッドが新しいレポートを書き込むまで待つ上限 (ms)
$EdgeNeutralMs = 200  # 起動時と終了時に中立を書き込む時間 (ms)

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Threading;

public class PicoReportSender
{
    public const int SEND_INTERVAL_MS = 10;
    public const int NEUTRAL_TAIL_MS = 100;
    public const int NEUTRAL_ON_FATAL_MS = 200;

    private const byte END = 0xC0; private const byte ESC = 0xDB; private const byte ESC_END = 0xDC; private const byte ESC_ESC = 0xDD;

    private readonly Stream stream;
    private readonly TextWriter output;
    private readonly bool exitOnFatal;
    private readonly object sync = new object();
    private readonly Stopwatch clock = Stopwatch.StartNew();
    private byte[] current = NeutralReport();
    private long version = 0;         // incremented by every SetReport
    private long writtenVersion = 0;  // latest version written at least once
    private long tailEndMs = 0;       // neutral is written until this time
    private bool keepSendingNeutral = false;
    private bool stopping = false;
    private string fatalMessage = null;
    private Thread thread;

    public PicoReportSender(Stream stream, TextWriter output, bool exitOnFatal)
    {
        this.stream = stream;
        this.output = output;
        this.exitOnFatal = exitOnFatal;
    }

    public bool KeepSendingNeutral
    {
        get { lock (sync) { return keepSendingNeutral; } }
        set { lock (sync) { keepSendingNeutral = value; Monitor.PulseAll(sync); } }
    }

    public string FatalMessage
    {
        get { lock (sync) { return fatalMessage; } }
    }

    public static byte[] NeutralReport() { return new byte[] { 0x00, 0x00, 0x0F, 0x80, 0x80, 0x80, 0x80, 0x00 }; }

    public static bool IsNeutral(byte[] report)
    {
        byte[] neutral = NeutralReport();
        for (int i = 0; i < neutral.Length; i++) { if (report[i] != neutral[i]) { return false; } }
        return true;
    }

    public static byte[] ParseHex(string hex)
    {
        if (hex.Length != 16) { throw new ArgumentException("report must be 16 hex digits: " + hex); }
        byte[] report = new byte[8];
        for (int i = 0; i < 8; i++) { report[i] = Convert.ToByte(hex.Substring(i * 2, 2), 16); }
        return report;
    }

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

    public static void WriteReport(Stream stream, byte[] report)
    {
        byte[] packet = EncodeReport(report);
        stream.Write(packet, 0, packet.Length);
        stream.Flush();
    }

    // Writes the report every SEND_INTERVAL_MS for durationMs on the calling thread (used before Start and after Stop).
    public static void SendFor(Stream stream, byte[] report, int durationMs)
    {
        Stopwatch elapsed = Stopwatch.StartNew();
        while (elapsed.ElapsedMilliseconds < durationMs)
        {
            WriteReport(stream, report);
            Thread.Sleep(SEND_INTERVAL_MS);
        }
    }

    public void Start()
    {
        thread = new Thread(Run);
        thread.IsBackground = true;
        thread.Start();
    }

    // Replaces the current report and waits until the sending thread has written it once.
    // Returns false if it was not written within timeoutMs, or if the sender stopped or failed.
    public bool SetReport(byte[] report, int timeoutMs)
    {
        Stopwatch waited = Stopwatch.StartNew();
        lock (sync)
        {
            if (stopping || fatalMessage != null) { return false; }
            current = (byte[])report.Clone();
            version++;
            long requested = version;
            Monitor.PulseAll(sync);
            while (writtenVersion < requested)
            {
                if (stopping || fatalMessage != null) { return false; }
                long remaining = timeoutMs - waited.ElapsedMilliseconds;
                if (remaining <= 0) { return false; }
                Monitor.Wait(sync, (int)remaining);
            }
            return true;
        }
    }

    public void Stop()
    {
        lock (sync)
        {
            stopping = true;
            Monitor.PulseAll(sync);
        }
        if (thread != null)
        {
            thread.Join();
            thread = null;
        }
    }

    private bool IsIdle()
    {
        return writtenVersion == version && IsNeutral(current) && !keepSendingNeutral && clock.ElapsedMilliseconds >= tailEndMs;
    }

    private void Run()
    {
        while (true)
        {
            byte[] report;
            long reportVersion;
            lock (sync)
            {
                while (!stopping && IsIdle()) { Monitor.Wait(sync); }
                if (stopping) { return; }
                report = current;
                reportVersion = version;
            }
            try
            {
                WriteReport(stream, report);
            }
            catch (Exception e)
            {
                Fail(e.Message);
                return;
            }
            lock (sync)
            {
                if (reportVersion > writtenVersion)
                {
                    writtenVersion = reportVersion;
                    if (IsNeutral(report)) { tailEndMs = clock.ElapsedMilliseconds + NEUTRAL_TAIL_MS; }
                    Monitor.PulseAll(sync);
                }
                // SetReport and Stop pulse the monitor, so a new report is written without waiting for the interval.
                if (!stopping && version == reportVersion) { Monitor.Wait(sync, SEND_INTERVAL_MS); }
            }
        }
    }

    private void Fail(string message)
    {
        string oneLine = message.Replace("\r", " ").Replace("\n", " ");
        lock (sync)
        {
            fatalMessage = oneLine;
            Monitor.PulseAll(sync);
        }
        try
        {
            output.WriteLine("fatal " + oneLine);
            output.Flush();
        }
        catch (Exception)
        {
        }
        if (exitOnFatal)
        {
            // Environment.Exit skips the PowerShell finally block, so try to leave the controller neutral here first.
            try { SendFor(stream, NeutralReport(), NEUTRAL_ON_FATAL_MS); } catch (Exception) { }
            Environment.Exit(1);
        }
    }
}
'@

function Now-Ms { return [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() }

$port = [System.IO.Ports.SerialPort]::new($PortName, 115200, [System.IO.Ports.Parity]::None, 8, [System.IO.Ports.StopBits]::One)
$port.WriteTimeout = 1000
# [Console]::Out は OutputEncoding を設定すると作り直されるため、設定してから取り出す (UTF-8 without BOM)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$out = [Console]::Out
$sender = $null
try {
    $port.Open()
    [PicoReportSender]::SendFor($port.BaseStream, [PicoReportSender]::NeutralReport(), $EdgeNeutralMs)
    # 書き込みの失敗では、送信スレッドが fatal を出力してプロセスを終了コード 1 で終える (exit 1)
    $sender = [PicoReportSender]::new($port.BaseStream, $out, $true)
    $sender.KeepSendingNeutral = $KeepSendingNeutral.IsPresent
    $sender.Start()
    $out.WriteLine("ready $PortName $(Now-Ms)"); $out.Flush()
    while ($null -ne ($line = [Console]::In.ReadLine())) {
        $parts = $line.Trim() -split "\s+"
        if ($parts.Count -eq 1 -and $parts[0] -ceq "quit") { break }
        if ($parts.Count -eq 2 -and $parts[0] -ceq "set" -and $parts[1] -cmatch "^[0-9a-f]{16}$") {
            $hex = $parts[1]
            if ($sender.SetReport([PicoReportSender]::ParseHex($hex), $SetReportTimeoutMs)) {
                $out.WriteLine("ack $hex $(Now-Ms)")
            } else {
                $out.WriteLine("error report was not written in time: $hex")
            }
        } else {
            $out.WriteLine("error invalid command: $line")
        }
        $out.Flush()
    }
}
finally {
    # quit と標準入力の EOF のどちらでも、送信スレッドを止めてから中立を送る (stop -> neutral -> close)
    if ($null -ne $sender) {
        try { $sender.Stop() } catch { $out.WriteLine("warning: sender stop failed: $($_.Exception.Message)") }
    }
    if ($port.IsOpen) {
        try { [PicoReportSender]::SendFor($port.BaseStream, [PicoReportSender]::NeutralReport(), $EdgeNeutralMs) } catch { $out.WriteLine("warning: neutral report failed: $($_.Exception.Message)") }
        try { $port.Close() } catch { $out.WriteLine("warning: port close failed: $($_.Exception.Message)") }
    }
    try { $port.Dispose() } catch { $out.WriteLine("warning: port dispose failed: $($_.Exception.Message)") }
    $out.WriteLine("closed"); $out.Flush()
}

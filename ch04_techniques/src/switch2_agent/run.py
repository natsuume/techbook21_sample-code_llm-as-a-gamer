"""第4章のエージェント (switch2-agent) を起動する。

1 つのプロセスで、キャプチャー (CaptureWithHistory)、コントローラーのブリッジ (BridgeSupervisor)、
押下状態の送信 (PadOutput)、advisor (ClaudeCodeHeadlessAdvisor)、Decision Model、判断のループ (Pipeline) を動かす。
Ctrl+C で止まる。

起動引数:
    --game NAME                  ゲーム名 (必須)。ゲームの情報は game_notes/<NAME>/ に置く
    --model {jev,decisions}      Decision Model (必須)。jev は TypeSafe Jev、decisions は OpenAI Decisions API
    --ffmpeg-fps N               ffmpeg の出力のフレームレート (整数 1〜60、既定 30)
    --process-fps N              間引いて残すフレームレート (整数 1〜ffmpeg_fps、既定 10)
    --history-seconds S          フレームの履歴を持つ秒数 (0 より大きい値、既定 5.0)
    --image-width N              要求に載せる画像の幅 (整数 498〜1280、既定 1280)
    --jpeg-quality N             要求に載せる画像の JPEG の品質 (整数 1〜100、既定 85)
    --threshold-press P          押下状態の問いのしきい値 (0〜1、既定 0.5)
    --threshold-tap P            短押しの問いのしきい値 (0〜1、既定 0.5)
    --threshold-stick P          スティックの問いのしきい値 (0〜1、既定 0.5)
    --threshold-advisor P        advisor の問いのしきい値 (0〜1、既定 0.5)
    --valley-ratio R             鮮明度の谷とみなす比 (0 と 1 を含まない範囲、既定 0.5)
    --similarity-threshold D     意味のあるフレームが似ているとみなす差分の上限 (0〜255、既定 0.15)
    --advisor-timeout S          advisor の 1 回の実行の上限 (秒。0 より大きい値、既定 300)
    --max-consecutive-errors N   Decision Model の失敗がこの回数続いたら止まる (整数 1 以上、既定 5)
    範囲外の値と、--process-fps が --ffmpeg-fps より大きい組み合わせは起動時にエラーにする

使い方 (ch04_techniques ディレクトリーで実行する):
    uv run switch2-agent --game <名前> --model jev
"""

import argparse

from switch2_remote.capture import DEVICE_FPS, DEVICE_WIDTH

FFMPEG_FPS_MIN, FFMPEG_FPS_MAX = 1, DEVICE_FPS  # 第2章の daemon と同じ範囲
PROCESS_FPS_MIN = 1  # 上限は --ffmpeg-fps
# 画像の幅の範囲。Jev の画像は各辺 280px 以上が必要で、16:9 なら幅 498 以上。nv12_to_jpeg は縮小だけ行う
IMAGE_WIDTH_MIN, IMAGE_WIDTH_MAX = 498, DEVICE_WIDTH
JPEG_QUALITY_MIN, JPEG_QUALITY_MAX = 1, 100
# 意味のあるフレームの差分 (image_metrics.mean_abs_diff) が取る範囲
SIMILARITY_THRESHOLD_MIN, SIMILARITY_THRESHOLD_MAX = 0.0, 255.0
MODEL_CHOICES = ("jev", "decisions")
RUNS_DIR_NAME = "runs"  # 実行のログを置くディレクトリー (ch04_techniques の下)
NOTES_DIR_NAME = "game_notes"  # ゲームの情報を置くディレクトリー (ch04_techniques の下)
RUN_LOG_FILE_NAME = "run.jsonl"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """起動引数を解釈し、次の属性を持つ Namespace を返す。

    game (str)、model ("jev" / "decisions")、ffmpeg_fps (int、既定 30)、process_fps (int、既定 10)、
    history_seconds (float、既定 5.0)、image_width (int、既定 1280)、jpeg_quality (int、既定 85)、
    threshold_press / threshold_tap / threshold_stick / threshold_advisor (float、既定 0.5)、
    valley_ratio (float、既定 0.5)、similarity_threshold (float、既定 0.15)、advisor_timeout (float、既定 300.0)、
    max_consecutive_errors (int、既定 5)。

    argv が None なら sys.argv[1:] を使う。--game と --model は必須。範囲 (モジュールの docstring の起動引数) の外の値、
    数として読めない値、--process-fps が --ffmpeg-fps より大きい組み合わせは、第2章の daemon.parse_args と同じく
    parser.error で使い方とともに表示して終了する (SystemExit、終了コード 2)。
    """
    raise NotImplementedError


def main(argv: list[str] | None = None) -> None:
    """起動引数を解釈し、部品を用意して Pipeline を動かす。

    作業ディレクトリーは ch04_techniques を前提にする。Path.cwd() / ".claude" / "skills" が無ければ、
    ch04_techniques で実行するよう説明して終了する (SystemExit)。
    API キーは --model に応じた環境変数 (decisions_api.API_KEY_ENV / jev.API_KEY_ENV) から読み、
    無ければ説明して終了する。ゲーム名が不正 (game_notes.game_notes_dir の ValueError) なら説明して終了する。

    起動の順:
        1. ゲームの情報のファイルを用意する (game_notes.ensure_game_notes(cwd / NOTES_DIR_NAME, game))
        2. BridgeSupervisor(start_bridge=Bridge, reset_radio=reset_bluetooth_radio)。最初のブリッジの起動に
           失敗したら (BridgeStartError)、そのメッセージで終了する
        3. PadOutput(supervisor)
        4. 意味のあるフレームの判定 (SharpnessValleyDetector(valley_ratio, MeanAbsDiffSimilarity(similarity_threshold)))
           と保管 (MeaningfulFrameStore)
        5. CaptureWithHistory(ffmpeg_fps, process_fps, FrameHistory(history_seconds), on_frame)。on_frame は
           判定の feed が返したフレームを保管に add する
        6. ClaudeCodeHeadlessAdvisor(cwd, ゲームの情報のディレクトリー, 実行のディレクトリー, game, advisor_timeout, log=RunLog.write)
        7. Decision Model (JevModel / DecisionsApiModel)
        8. Pipeline (encode は image_width と jpeg_quality で nv12_to_jpeg する関数を包んだ JpegCache)
    ログは cwd / RUNS_DIR_NAME / <YYYYmmdd-HHMMSS> / RUN_LOG_FILE_NAME に RunLog で書く (advisor の入出力も同じ実行のディレクトリーに置く)。
    Ctrl+C (KeyboardInterrupt) で stop を立てて止まる。
    終了時は、作れた部品だけを output.neutral()、output.close()、advisor.close()、capture.close()、
    supervisor.close() の順に閉じる。
    """
    raise NotImplementedError


if __name__ == "__main__":
    main()

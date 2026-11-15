"""コマンド switch2-layers の入口。サブコマンド run / middle-once / deep-once / publish-note を持つ。

    switch2-layers run [--runs-dir DIR] [--interval-fps N] [--interval-width N] [--max-surface-errors N]
                       [--middle-interval S] [--middle-window S] [--max-middle-errors N]
                       [--deep-min-start-interval S] [--deep-auto-seconds S] [--deep-timeout S] [--allow-home]
        実機で 3 層を動かす。各層はフェイク (surface.fake_surface・middle.fake_middle・deep.fake_deep) を使う
    switch2-layers middle-once --run-dir DIR [--middle-window S]
        保存済みの実行に対して middle を 1 回動かし、middle の出力ファイルを書く
    switch2-layers deep-once --run-dir DIR --doc KIND [--scene SCENE_ID]
        保存済みの実行に対して deep を 1 回動かす (きっかけ manual)
    switch2-layers publish-note --run-dir DIR --note PATH --base-sha256 HASH|none --from FILE
        deep が編集した文書を本体と入れ替える (notes.publish_note)

引数の形の誤り (知らない引数、数として読めない値、--doc の知らない種類など) は argparse が使い方とともに表示し、
SystemExit (終了コード 2) にする。値の範囲の誤りは、各サブコマンドの処理が標準エラー出力に書いて終了コード 2 を返す。
"""

import argparse
from collections.abc import Callable
from pathlib import Path

from switch2_layers.deep.deep_types import DOCUMENT_KINDS
from switch2_layers.settings import (
    DEFAULT_DEEP_AUTO_SECONDS,
    DEFAULT_DEEP_MIN_START_INTERVAL_S,
    DEFAULT_DEEP_TIMEOUT_S,
    DEFAULT_INTERVAL_FPS,
    DEFAULT_INTERVAL_WIDTH,
    DEFAULT_MAX_MIDDLE_ERRORS,
    DEFAULT_MAX_SURFACE_ERRORS,
    DEFAULT_MIDDLE_INTERVAL_S,
    DEFAULT_MIDDLE_WINDOW_S,
    DEFAULT_RUNS_DIR,
    INTERVAL_FPS_MAX,
    INTERVAL_WIDTH_MAX,
    INTERVAL_WIDTH_MIN,
    MIDDLE_INTERVAL_MAX_S,
    MIDDLE_INTERVAL_MIN_S,
    DeepOnceSettings,
    MiddleOnceSettings,
    PublishNoteSettings,
    RunSettings,
)

PROG = "switch2-layers"
# 範囲の誤りで返す終了コード (argparse の使い方の誤りと同じ)
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    """4 つのサブコマンドの引数を定義した ArgumentParser を返す。

    各サブコマンドの Namespace には、処理の関数 (handler: Namespace → 終了コード) を持たせる。
    サブコマンドは必須 (無ければ argparse が SystemExit 2 にする)。
    """
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "surface・middle・deep の 3 層のエージェントで Nintendo Switch 2 のゲームを遊ぶ"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    add_run_parser(commands)
    add_middle_once_parser(commands)
    add_deep_once_parser(commands)
    add_publish_note_parser(commands)
    return parser


def add_run_parser(
    commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """サブコマンド run の引数を定義する。既定値は settings の DEFAULT_* と同じ。"""
    parser = commands.add_parser(
        "run",
        help="実機で 3 層を動かす (各層はフェイク)",
        description="実機で 3 層を動かす。各層はフェイクで動く。Ctrl+C で止まる",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=DEFAULT_RUNS_DIR,
        help=f"実行ディレクトリーを作る親 (既定 {DEFAULT_RUNS_DIR})",
    )
    parser.add_argument(
        "--interval-fps",
        type=float,
        default=DEFAULT_INTERVAL_FPS,
        help=(
            "一定間隔のフレームを frames/interval/ に保存するフレームレート "
            f"(0 より大きく {INTERVAL_FPS_MAX:g} 以下。既定 {DEFAULT_INTERVAL_FPS:g})"
        ),
    )
    parser.add_argument(
        "--interval-width",
        type=int,
        default=DEFAULT_INTERVAL_WIDTH,
        help=(
            "一定間隔のフレームを縮小する幅 "
            f"({INTERVAL_WIDTH_MIN}〜{INTERVAL_WIDTH_MAX}。既定 {DEFAULT_INTERVAL_WIDTH})"
        ),
    )
    parser.add_argument(
        "--max-surface-errors",
        type=int,
        default=DEFAULT_MAX_SURFACE_ERRORS,
        help=(
            "surface の失敗がこの回数続いたら全体を止める "
            f"(1 以上。既定 {DEFAULT_MAX_SURFACE_ERRORS})"
        ),
    )
    parser.add_argument(
        "--middle-interval",
        type=float,
        default=DEFAULT_MIDDLE_INTERVAL_S,
        help=(
            "middle の前回の応答から次を始めるまでの秒数 "
            f"({MIDDLE_INTERVAL_MIN_S:g}〜{MIDDLE_INTERVAL_MAX_S:g}。"
            f"既定 {DEFAULT_MIDDLE_INTERVAL_S:g})"
        ),
    )
    add_middle_window_argument(parser)
    parser.add_argument(
        "--max-middle-errors",
        type=int,
        default=DEFAULT_MAX_MIDDLE_ERRORS,
        help=(
            "middle の失敗がこの回数続いたら全体を止める "
            f"(1 以上。既定 {DEFAULT_MAX_MIDDLE_ERRORS})"
        ),
    )
    parser.add_argument(
        "--deep-min-start-interval",
        type=float,
        default=DEFAULT_DEEP_MIN_START_INTERVAL_S,
        help=(
            "同じ文書の deep を、前回の起動からこの秒数たつまで起動しない "
            f"(0 以上。既定 {DEFAULT_DEEP_MIN_START_INTERVAL_S:g})"
        ),
    )
    parser.add_argument(
        "--deep-auto-seconds",
        type=float,
        default=DEFAULT_DEEP_AUTO_SECONDS,
        help=(
            "同じ文書の前回の deep の終了からこの秒数たったら自動で deep を呼ぶ "
            f"(0 より大きい値。既定 {DEFAULT_DEEP_AUTO_SECONDS:g})"
        ),
    )
    parser.add_argument(
        "--deep-timeout",
        type=float,
        default=DEFAULT_DEEP_TIMEOUT_S,
        help=(
            "deep のジョブの実行時間の上限 (秒) "
            f"(0 より大きい値。既定 {DEFAULT_DEEP_TIMEOUT_S:g})"
        ),
    )
    parser.add_argument(
        "--allow-home",
        action="store_true",
        help="押せるボタンに HOME を足す (既定では HOME を押さない)",
    )
    parser.set_defaults(handler=run_command)


def add_middle_window_argument(parser: argparse.ArgumentParser) -> None:
    """--middle-window の引数を parser に足す (run と middle-once で共通)。"""
    parser.add_argument(
        "--middle-window",
        type=float,
        default=DEFAULT_MIDDLE_WINDOW_S,
        help=(
            "middle に渡す一定間隔のフレームの範囲 (直近の秒数。0 より大きい値。"
            f"既定 {DEFAULT_MIDDLE_WINDOW_S:g})"
        ),
    )


def add_middle_once_parser(
    commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """サブコマンド middle-once の引数を定義する。"""
    parser = commands.add_parser(
        "middle-once",
        help="保存済みの実行に対して middle を 1 回動かす",
        description="保存済みの実行に対して middle を 1 回動かし、middle の出力ファイルを書く",
    )
    parser.add_argument(
        "--run-dir", type=Path, required=True, help="保存済みの実行ディレクトリー"
    )
    add_middle_window_argument(parser)
    parser.set_defaults(handler=middle_once_command)


def add_deep_once_parser(
    commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """サブコマンド deep-once の引数を定義する。--doc は DocumentKind の値から選ぶ。"""
    parser = commands.add_parser(
        "deep-once",
        help="保存済みの実行に対して deep を 1 回動かす",
        description="保存済みの実行に対して deep を 1 回動かす (きっかけ manual)",
    )
    parser.add_argument(
        "--run-dir", type=Path, required=True, help="保存済みの実行ディレクトリー"
    )
    parser.add_argument(
        "--doc",
        required=True,
        choices=[kind.value for kind in DOCUMENT_KINDS],
        help="文書の種類",
    )
    parser.add_argument(
        "--scene",
        metavar="SCENE_ID",
        default=None,
        help=(
            "場面 ID (scene-001 の形。controls と tips だけ)。"
            "付けなければ middle/state.json の現在の場面 (無ければ共通の文書) を対象にする"
        ),
    )
    parser.set_defaults(handler=deep_once_command)


def add_publish_note_parser(
    commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """サブコマンド publish-note の引数を定義する。--from の値は Namespace の source に入れる。"""
    parser = commands.add_parser(
        "publish-note",
        help="deep が編集した文書を本体と入れ替える",
        description=(
            "本体の今の SHA-256 (無ければ none) が --base-sha256 と一致したら、--from の内容で本体を置き換える。"
            "終了コード: 0 置き換えた、3 一致しなかった、2 引数の誤り"
        ),
    )
    parser.add_argument(
        "--run-dir", type=Path, required=True, help="実行ディレクトリー"
    )
    parser.add_argument(
        "--note", required=True, help="置き換える文書の notes/ からの相対パス"
    )
    parser.add_argument(
        "--base-sha256",
        required=True,
        metavar="HASH|none",
        help="編集を始めたときの文書の SHA-256。文書が無かったなら none",
    )
    parser.add_argument(
        "--from",
        dest="source",
        type=Path,
        required=True,
        metavar="FILE",
        help="編集したファイル (同じ実行の deep/<種類>-<連番>/work/ の中に限る)",
    )
    parser.set_defaults(handler=publish_note_command)


def run_settings_from_args(args: argparse.Namespace) -> RunSettings:
    """run の Namespace を RunSettings にする (範囲は確かめない)。"""
    raise NotImplementedError


def middle_once_settings_from_args(args: argparse.Namespace) -> MiddleOnceSettings:
    """middle-once の Namespace を MiddleOnceSettings にする (範囲は確かめない)。"""
    raise NotImplementedError


def deep_once_settings_from_args(args: argparse.Namespace) -> DeepOnceSettings:
    """deep-once の Namespace を DeepOnceSettings にする (--doc は DocumentKind にする。範囲は確かめない)。"""
    raise NotImplementedError


def publish_note_settings_from_args(args: argparse.Namespace) -> PublishNoteSettings:
    """publish-note の Namespace を PublishNoteSettings にする。"""
    raise NotImplementedError


def run_command(args: argparse.Namespace) -> int:
    """run の処理。終了コードを返す。

    1. run_settings_from_args → settings.run_settings_errors。誤りがあれば 1 行ずつ標準エラー出力に書き、EXIT_USAGE を返す
       (実機には触れない)
    2. switch2_remote.bridge_supervisor.BridgeSupervisor(start_bridge=controller_bridge.Bridge,
       reset_radio=bluetooth_radio.reset_bluetooth_radio) を作る。最初のブリッジの起動に失敗したら
       (controller_bridge.BridgeStartError) メッセージを標準エラー出力に書いて 1 を返す
    3. switch2_remote.capture.Capture(CAPTURE_FFMPEG_FPS, CAPTURE_PROCESS_FPS) を作る
    4. app.LayersApp(settings, LayersParts(frame_reader=SwitchCaptureReader(capture), report_sender=supervisor,
       bridge_state=supervisor の state を返す関数, surface=FakeSurfaceDecider(), middle=FakeMiddleAnalyzer(),
       deep=FakeDeepRunner())).run() で動かし、止まるまで待つ
    5. 止まったら、作れた部品を capture.close()、supervisor.close() の順に閉じる
    止まった理由が app.REASON_INTERRUPTED (Ctrl+C) なら 0、ほかの理由なら 1 を返す。止まった理由は標準エラー出力に 1 行で書く。
    """
    raise NotImplementedError


def middle_once_command(args: argparse.Namespace) -> int:
    """middle-once の処理。終了コードを返す。

    1. middle_once_settings_from_args → settings.middle_once_settings_errors。誤りがあれば標準エラー出力に書き、EXIT_USAGE を返す
    2. --run-dir がディレクトリーでなければ、標準エラー出力に書いて EXIT_USAGE を返す
    3. middle.middle_loop.run_middle_once(run_dir, FakeMiddleAnalyzer(), middle_window_s) を呼ぶ
    4. 標準出力に結果の JSON 1 行 {"status": "ok" | "error", "scene_id": 場面 ID か null, "new_scene": 真偽,
       "deep_requests": [種類...], "error": 説明か null} を書く
    成功なら 0、analyze か書き込みに失敗したら 1 を返す。
    """
    raise NotImplementedError


def deep_once_command(args: argparse.Namespace) -> int:
    """deep-once の処理。終了コードを返す。

    1. deep_once_settings_from_args → settings.deep_once_settings_errors。誤りがあれば標準エラー出力に書き、EXIT_USAGE を返す
    2. --run-dir がディレクトリーでなければ、標準エラー出力に書いて EXIT_USAGE を返す
    3. deep.deep_scheduler.run_deep_once(run_dir, kind, scene_id, FakeDeepRunner()) を呼ぶ
    4. 標準出力に結果の JSON 1 行 {"job": 実行ディレクトリーからのジョブのディレクトリーの相対パス, "status": deep_end の status,
       "message": 説明} を書く
    status が "ok" なら 0、それ以外なら 1 を返す。
    """
    raise NotImplementedError


def publish_note_command(args: argparse.Namespace) -> int:
    """publish-note の処理。notes.publish_note.publish_note を呼び、終了コードを返す。

    標準出力には PublishResult.stdout_json() があればその JSON を 1 行で書く
    ({"status": "published", "sha256": ...} か {"status": "conflict", "current_sha256": ...})。
    引数の誤り (終了コード 2) のときは、標準出力に何も書かず、理由を標準エラー出力に 1 行で書く。
    終了コードは PublishResult.exit_code() (0 / 2 / 3)。
    """
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    """起動引数 argv (None なら sys.argv[1:]) を解釈し、サブコマンドの処理を呼んで終了コードを返す。

    --help は使い方を表示して SystemExit(0)、引数の形の誤りは SystemExit(2) にする (argparse)。
    """
    args = build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    return handler(args)

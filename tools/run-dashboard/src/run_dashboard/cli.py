"""ダッシュボードのサーバーを起動する (コマンド run-dashboard)。

起動引数:
    --runs-dir DIR   実行の記録を置くディレクトリー (カレントディレクトリーからの相対。既定 ../../ch04_techniques/runs)
    --port N         待ち受けるポート (整数 1〜65535、既定 8765)
待ち受けるアドレスは 127.0.0.1 だけで、変える引数は作らない。

使い方 (tools/run-dashboard ディレクトリーで実行する):
    uv run run-dashboard
"""

import argparse
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from run_dashboard.app import DEFAULT_WEB_DIST_DIR, create_app

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
# tools/run-dashboard で実行したときの、第4章のサンプルの実行の記録のディレクトリー
DEFAULT_RUNS_DIR = "../../ch04_techniques/runs"
PORT_MIN, PORT_MAX = 1, 65535


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """起動引数を解釈し、次の属性を持つ Namespace を返す。

    runs_dir (pathlib.Path。既定 Path(DEFAULT_RUNS_DIR))、port (int。既定 DEFAULT_PORT)。
    argv が None なら sys.argv[1:] を使う。--port が整数として読めないか、PORT_MIN〜PORT_MAX の範囲の外なら、
    argparse のエラー (--port を含むメッセージを使い方とともに標準エラー出力に表示して SystemExit、終了コード 2) にする。
    待ち受けるアドレスの引数は作らない。
    """
    parser = argparse.ArgumentParser(
        prog="run-dashboard",
        description="第4章のサンプルの試行の記録を読む、読み取り専用の API サーバーを起動する",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path(DEFAULT_RUNS_DIR),
        help=f"実行の記録を置くディレクトリー (カレントディレクトリーからの相対。既定 {DEFAULT_RUNS_DIR})",
    )
    parser.add_argument(
        "--port",
        type=_port_number,
        default=DEFAULT_PORT,
        help=f"待ち受けるポート ({PORT_MIN}〜{PORT_MAX}、既定 {DEFAULT_PORT})",
    )
    return parser.parse_args(argv)


def _port_number(text: str) -> int:
    """--port の値 text を int にして返す (argparse の type)。

    整数として読めないか、PORT_MIN〜PORT_MAX の範囲の外なら argparse.ArgumentTypeError にする
    (argparse が `argument --port: <説明>` の形で表示して終了する)。
    """
    try:
        port = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
    if not PORT_MIN <= port <= PORT_MAX:
        raise argparse.ArgumentTypeError(f"must be {PORT_MIN}..{PORT_MAX}: {port}")
    return port


def main(argv: Sequence[str] | None = None) -> None:
    """起動引数を解釈し、サーバーを起動する (Ctrl+C で止まるまで戻らない)。

    --runs-dir がディレクトリーでなければ、そのパスと、--runs-dir で記録のディレクトリーを指定する旨を書いた説明で
    SystemExit を出す。ディレクトリーなら、uvicorn のモジュールの run を呼ぶ (import uvicorn して uvicorn.run(...)
    の形で呼ぶ。テストは uvicorn.run を差し替えて確かめる):
        uvicorn.run(app.create_app(runs_dir, app.DEFAULT_WEB_DIST_DIR), host=HOST, port=port)
    """
    args = parse_args(argv)
    runs_dir: Path = args.runs_dir
    if not runs_dir.is_dir():
        raise SystemExit(
            f"{runs_dir} ({runs_dir.resolve()}) is not a directory. "
            "Specify the directory of the run records with --runs-dir"
        )
    uvicorn.run(create_app(runs_dir, DEFAULT_WEB_DIST_DIR), host=HOST, port=args.port)

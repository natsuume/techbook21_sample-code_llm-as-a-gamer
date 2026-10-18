# techbook21_sample-code_llm-as-a-gamer

技術書典21 で頒布する技術同人誌『LLM as a Gamer』のサンプルコードリポジトリーです。

本書では、Nintendo Switch 2 の実機をコーディングエージェント (Claude Code など) に操作させます。映像は HDMI キャプチャー (Elgato 4K X) を Windows 側の ffmpeg.exe で受け取って WSL2 の Python に渡し、入力は Windows 側の PowerShell スクリプトから Bluetooth 経由で Raspberry Pi Pico 2 W に送ります。Pico 2 W は USB コントローラーとして Switch 2 に接続します。

書籍では要点だけを掲載しています。コードの全体はこのリポジトリーで参照してください。

## 動作環境

- Windows PC (Bluetooth 搭載) と WSL2 (動作を確認したディストリビューションは Ubuntu 24.04)
- Python 3.12 以上
- [uv](https://docs.astral.sh/uv/)

必要な機材と、Pico 2 W・ffmpeg の準備は [`ch01_setup/README.md`](./ch01_setup/README.md) で説明しています。

## セットアップ

リポジトリーの直下で依存パッケージを導入します。

```bash
uv sync
```

導入すると、次の 2 つのコマンドを `uv run` で実行できます。

- `switch2-remote-daemon`: キャプチャーとコントローラーのブリッジを起動したまま保持する常駐デーモン
- `switch2-remote`: デーモンに 1 行のコマンドを送るクライアント

## 構成

```text
pyproject.toml
src/switch2_remote/    各章で使う共通パッケージ
tests/                 共通パッケージのテスト
ch01_setup/            第1章のサンプル
```

### 共通パッケージ `src/switch2_remote`

| ファイル | 役割 |
|---|---|
| `ffmpeg_locator.py` | Windows 側に winget で導入した ffmpeg.exe を探す |
| `capture_command.py` | ffmpeg.exe のコマンド (720p NV12、デバイスの 60fps を出力側で間引く) を組み立て、標準出力からフレームを読み取る |
| `frame_buffer.py` | 受け取ったフレームを処理レートに間引き、直近の一定時間分を保持する |
| `capture.py` | ffmpeg.exe を起動したままにし、間引いたフレームを直近の一定時間分保持して、最新フレームを撮影する |
| `controller_state.py` | ボタン・スティックの指定を解釈し、押下状態を合成してゲームパッドのレポートを組み立てる |
| `controller.py` | 押し続けている入力と時間を指定した長押しを保持し、変化のたびにレポートをブリッジへ送る |
| `controller_bridge.py` | controller_bridge.ps1 を起動したまま保持し、送るレポートを差し替える |
| `controller_bridge.ps1` | Windows 側で COM ポートを開き、指定されたレポートを Pico 2 W に送り続ける |
| `commands.py` | デーモンが受け取る 1 行コマンドを解釈して振り分ける |
| `daemon.py` | Unix ソケットでコマンドを受け付ける常駐デーモン (`switch2-remote-daemon`) |
| `client.py` | デーモンにコマンドを送るクライアント (`switch2-remote`) |

### 章ディレクトリー

| ディレクトリー | 章 | 内容 | 使う共通パッケージ |
|---|---|---|---|
| [`ch01_setup`](./ch01_setup/) | 第1章 | 機材の準備、デーモンとクライアントの使い方、エージェント向けの指示ファイルの例 | `switch2_remote` 全体 |

## 開発用コマンド

```bash
uv run ruff format   # コードを整形する
uv run ruff check    # 静的解析を行う
uv run pytest        # テストを実行する (実機は使いません)
```

## 注意事項

- このリポジトリーのコードは、オフラインの個人実験での利用に限ります
- 利用規約で自動操作を禁止しているサービスやゲーム、オンライン対戦では使わないでください
- 利用によって生じたアカウントの制限、データの損失、機器の故障などの不利益は、利用者の自己責任となります

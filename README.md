# techbook21_sample-code_llm-as-a-gamer

技術書典21 で頒布する技術同人誌『LLM as a Gamer』のサンプルコードリポジトリーです。

本書では、Nintendo Switch 2 の実機をコーディングエージェント (Claude Code など) に操作させます。映像は HDMI キャプチャー (Elgato 4K X) を Windows 側の ffmpeg.exe で受け取って WSL2 の Python に渡し、入力は Windows 側の PowerShell スクリプトから Bluetooth 経由で Raspberry Pi Pico 2 W に送ります。Pico 2 W は USB コントローラーとして Switch 2 に接続します。

書籍では要点だけを掲載しています。コードの全体はこのリポジトリーで参照してください。

## 動作環境

- Windows PC (Bluetooth 搭載) と WSL2 (動作を確認したディストリビューションは Ubuntu 24.04)
- Python 3.12 以上
- [uv](https://docs.astral.sh/uv/)

必要な機材と、Pico 2 W・ffmpeg の準備は [`ch02_setup/README.md`](./ch02_setup/README.md) で説明しています。

## セットアップ

`ch02_setup` ディレクトリーで依存パッケージを導入します。

```bash
cd ch02_setup
uv sync
```

導入すると、`ch02_setup` ディレクトリーで次の 2 つのコマンドを `uv run` で実行できます。

- `switch2-remote-daemon`: キャプチャーとコントローラーのブリッジを起動したまま保持する常駐デーモン
- `switch2-remote`: デーモンに 1 行のコマンドを送るクライアント

## 構成

```text
ch02_setup/              第2章: 環境構築の手順書と、デーモン・クライアントの uv プロジェクト
  pyproject.toml
  src/switch2_remote/    デーモンとクライアントのパッケージ
  tests/                 パッケージのテスト
ch03_playtest/           第3章: 試行でエージェントに読ませた指示ファイル
ch04_techniques/         第4章: 型付きの判断を返すモデルと advisor でゲームを遊ぶエージェントの uv プロジェクト
  pyproject.toml
  .claude/skills/        advisor がゲームの情報を書き込む手順 (Skill)
  src/switch2_agent/     エージェントのパッケージ
  tests/                 パッケージのテスト
tools/run-dashboard/     第4章の試行の記録を見る読み取り専用のダッシュボード (API サーバーの uv プロジェクトと画面)
  pyproject.toml
  src/run_dashboard/     サーバーのパッケージ
  tests/                 パッケージのテスト
  web/                   画面 (Vite + React + TypeScript)
```

### 章ディレクトリー

| ディレクトリー | 章 | 内容 |
|---|---|---|
| [`ch02_setup`](./ch02_setup/) | 第2章 | 機材の準備、デーモンとクライアントの使い方と実装 |
| [`ch03_playtest`](./ch03_playtest/) | 第3章 | 試行でエージェントに読ませた指示ファイル (`CLAUDE.md`)。操作には `ch02_setup` のデーモンとクライアントを使います |
| [`ch04_techniques`](./ch04_techniques/) | 第4章 | 型付きの判断を返すモデル (Decision Model) と、Claude Code の advisor でゲームを遊ぶエージェント (`switch2-agent`) の使い方と実装。映像と入力には `ch02_setup` のパッケージを使います |

### ツール

| ディレクトリー | 内容 |
|---|---|
| [`tools/run-dashboard`](./tools/run-dashboard/) | 第4章の試行の記録を、終わった実行ごとに再生して見る読み取り専用のダッシュボード (API サーバーと画面) |

### パッケージ `ch02_setup/src/switch2_remote`

| ファイル | 役割 |
|---|---|
| `ffmpeg_locator.py` | Windows 側に winget で導入した ffmpeg.exe を探す |
| `capture_command.py` | ffmpeg.exe のコマンド (NV12、デバイスの 1280x720・60fps を出力側で間引く) を組み立て、標準出力からフレームを読み取る |
| `frame_buffer.py` | 受け取ったフレームを処理レートに間引き、最新の 1 枚を保持する |
| `capture.py` | ffmpeg.exe を起動したままにし、間引いたフレームのうち最新の 1 枚を保持して撮影する |
| `controller_state.py` | ボタン・スティックの指定を解釈し、押下状態を合成してゲームパッドのレポートを組み立てる |
| `controller.py` | 押し続けている入力と時間を指定した長押しを保持し、変化のたびにレポートをブリッジへ送る |
| `controller_bridge.py` | controller_bridge.ps1 を起動したまま保持し、送るレポートを差し替える |
| `controller_bridge.ps1` | Windows 側で COM ポートを開き、指定されたレポートを Pico 2 W に送る |
| `bridge_supervisor.py` | ブリッジの失敗を検知し、COM ポートの開き直しと Bluetooth 無線のオフ・オンで裏で復旧させる |
| `bluetooth_radio.py` | bluetooth_radio.ps1 を実行し、Windows の Bluetooth 無線をオフ・オンする |
| `bluetooth_radio.ps1` | Windows 側で Bluetooth 無線をオフにし、待ってからオンに戻す |
| `commands.py` | デーモンが受け取る 1 行コマンドを解釈して振り分ける |
| `daemon.py` | Unix ソケットでコマンドを受け付ける常駐デーモン (`switch2-remote-daemon`) |
| `client.py` | デーモンにコマンドを送るクライアント (`switch2-remote`) |

## 開発用コマンド

`ch02_setup` ディレクトリーで実行します。

```bash
uv run ruff format   # コードを整形する
uv run ruff check    # 静的解析を行う
uv run pytest        # テストを実行する (実機は使いません)
```

## 注意事項

- このリポジトリーのコードは、オフラインの個人実験での利用に限ります
- 利用規約で自動操作を禁止しているサービスやゲーム、オンライン対戦では使わないでください
- 利用によって生じたアカウントの制限、データの損失、機器の故障などの不利益は、利用者の自己責任となります

# 第1章 Switch 2 をコマンドで操作する環境を作る

第1章のサンプルです。Nintendo Switch 2 の画面を WSL2 から撮影し、WSL2 からボタン入力を送れる環境を作ります。操作には共通パッケージ [`switch2_remote`](../src/switch2_remote/) の 2 つのコマンドを使います。

- `switch2-remote-daemon`: キャプチャーとコントローラーのブリッジを起動したまま保持する常駐デーモン
- `switch2-remote`: デーモンに 1 行のコマンドを送り、応答を表示するクライアント

## 全体の構成

```text
Nintendo Switch 2
  ├─ HDMI → Elgato 4K X → USB → Windows PC
  │                               └─ ffmpeg.exe (DirectShow, 720p NV12)
  │                                    │ 標準出力パイプ
  │                                    ▼
  │                             WSL2: switch2-remote-daemon ◀── Unix ソケット ── switch2-remote
  │                                    │ 標準入出力
  │                                    ▼
  │                             Windows PC: controller_bridge.ps1
  │                                    │ Bluetooth (仮想 COM ポート)
  │                                    ▼
  └─ USB ←──────────────────── Raspberry Pi Pico 2 W
```

映像は Windows 側の ffmpeg.exe で受け取り、標準出力のパイプで WSL2 の Python に渡します。入力は WSL2 から Windows 側の PowerShell スクリプト (controller_bridge.ps1) に送り、Bluetooth の仮想 COM ポート経由で Pico 2 W に届けます。Pico 2 W は USB の有線コントローラーとして Switch 2 に接続します。

## 必要な機材とソフトウェア

- Nintendo Switch 2
- Raspberry Pi Pico 2 W
- データ通信に対応した USB ケーブル (Pico 2 W と Switch 2 の接続用)
- Elgato 4K X (HDMI キャプチャー)
- Bluetooth を搭載した Windows PC
- WSL2 (動作を確認したディストリビューションは Ubuntu 24.04)
- [uv](https://docs.astral.sh/uv/) (WSL2 側)
- [PlayAbility Adaptive Receiver](https://github.com/playAbilityTech/PlayAbility-Adaptive-Receiver) の Pico 2 W 向けファームウェア

## 1. Pico 2 W の準備

Pico 2 W に PlayAbility Adaptive Receiver のファームウェアを書き込みます。書き込み方法は PlayAbility Adaptive Receiver のリポジトリーの説明に従ってください。

書き込んだら、Web 設定ツールで次のとおり設定します。

- Emulated device type: `Switch gamepad`
- Bluetooth: 有効

Pico 2 W の LED は、Switch 2 との USB 接続ではなく主に Bluetooth の状態を示します。

| LED | 状態 |
|---|---|
| 消灯 | Bluetooth (RFCOMM) 未接続 |
| 点滅 | Bluetooth ペアリングモード |
| 点灯 | Bluetooth (RFCOMM) 接続中 |

## 2. Windows とのペアリング

Windows の Bluetooth 設定で Pico 2 W とペアリングします。ペアリングすると、Windows 上に仮想 COM ポートが 2 つ作られます。

```text
COM3  発信  PlayAbility Receiver ...
COM4  着信  PlayAbility Receiver ...
```

PC から Pico 2 W へ送信するときは、発信側の COM ポートを使います。着信側は使いません。

ブリッジ (`src/switch2_remote/controller_bridge.ps1`) は、既定で `COM3` を 115200bps で開きます。Windows の Bluetooth 仮想 COM ポートは最大 115200bps として扱われるため、これより速い速度を指定すると開けません。発信側の COM ポートが `COM3` 以外の番号になった場合は、controller_bridge.ps1 の先頭にある `param([string]$PortName = "COM3")` の値を書き換えてください。

## 3. ffmpeg の導入

Windows 側の PowerShell で ffmpeg を導入します。`--scope user` を付けると管理者権限なしで導入できます。

```powershell
winget install --scope user Gyan.FFmpeg
```

デーモンは、実行中の Windows ユーザー自身の `%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*\bin\ffmpeg.exe` を探し、版番号が最も新しいものを使います。

Elgato 4K X は DirectShow でプロセス排他になります。デーモンを起動する前に、Elgato Studio や OBS など、デバイスを使うアプリを終了してください。使用中のままだと ffmpeg.exe が `Could not run graph` で失敗します。

## 4. Python 環境の準備

WSL2 側で、このリポジトリーの直下に移動して依存パッケージを導入します。

```bash
uv sync
```

## 5. デーモンの起動

別の端末を開くか、バックグラウンドでデーモンを起動します。

```bash
uv run switch2-remote-daemon
```

ブリッジの起動 (COM ポートを開くまで最大 30 秒) とキャプチャーの起動が終わると、`daemon ready` と表示されます。デーモンは同時に 1 つだけ起動します。

## 6. クライアントの使い方

デーモンを起動したまま、別の端末からコマンドを送ります。

```bash
uv run switch2-remote status                  # 動作状況を確かめる
uv run switch2-remote tap RIGHT RIGHT A       # RIGHT、RIGHT、A を順に押す
uv run switch2-remote tap L+R                 # L と R を同時に押す
uv run switch2-remote hold B+RIGHT 2000       # B と RIGHT を同時に 2000 ミリ秒押す
uv run switch2-remote down LS:0,1             # 左スティックを上に倒したままにする
uv run switch2-remote up ALL                  # 押したままの入力をすべて離す
uv run switch2-remote shot /tmp/screen.png 5  # 画面が止まるまで最大 5 秒待ち、PNG で保存する
uv run switch2-remote quit                    # ブリッジとキャプチャーを止めてデーモンを終了する
```

入力のコマンドでは、ボタンとスティックを次の形で指定します。

- ボタン名: `A` `B` `X` `Y` `L` `R` `ZL` `ZR` `PLUS` `MINUS` `HOME` `UP` `RIGHT` `DOWN` `LEFT`。小文字で書いても受け付けます
- スティック: `LS:x,y` (左) / `RS:x,y` (右)。x は右、y は上を正とし、-1.0〜1.0 の小数で書きます (例: `LS:0.5,-1`)
- 同時押し: `+` でつなぎます (例: `A+B`、`B+LS:1,0`)。十字キーの 2 方向 (`UP+RIGHT` など) は斜めになります。`UP+DOWN` と `LEFT+RIGHT` はエラーです

| コマンド | 動作 |
|---|---|
| `tap CHORD [CHORD ...]` | CHORD (`+` でつないだ指定) を順に短く押します。1 つあたり押下 120 ミリ秒、離して 100 ミリ秒、次の CHORD までの待ちは 150 ミリ秒です。すべてを送り終えてから応答します |
| `hold CHORD MS` | CHORD を MS ミリ秒 (1〜60000) 押します。押し始めた時点で応答し、MS ミリ秒たつとデーモンが離します。押している間も他のコマンドを送れます |
| `down CHORD [CHORD ...]` | `up` で離すまで押したままにします。押したままのスティックを `down` で指定し直すと、新しい値に変わります |
| `up TARGET [TARGET ...]` | 指定したボタン、または `LS` / `RS` を離します。`down` と `hold` のどちらで押したものも離れます。`up ALL` はすべてを離します |
| `shot PATH [TIMEOUT]` | コマンドを受け取った後のフレームで、画面が 10 フレーム続けて止まるまで最大 TIMEOUT 秒 (省略時 5 秒、0 より大きく 60 以下) 待ち、PATH に PNG で保存します。相対パスはクライアントの作業ディレクトリーを基準に絶対パスへ直して送ります |
| `status` | 受信フレーム数、ffmpeg のフレーム落ち回数、キャプチャーの終了、ブリッジの生存、押したままの入力、`hold` の残り時間、`hold` を離すときの送信の失敗を返します |
| `quit` | ブリッジとキャプチャーを止めてデーモンを終了します |

送るレポートは、`down` で押したままの入力、`hold` で押している入力、`tap` で押している入力を重ねたものです。`tap` と `hold` が終わると、`down` で押したままの状態に戻ります。たとえば `down LS:0,1` の後に `tap X` を送ると、左スティックを上に倒したまま X を押せます。すでに押されているボタンやスティックを `tap` / `hold` で押そうとした場合や、十字キーの相反する方向が重なる場合は、何も送らずにエラーを返します。

応答の例を示します。

```text
$ uv run switch2-remote down LS:0,1
ok held=LS:0,1
$ uv run switch2-remote hold B 3000
ok hold B 3000ms
$ uv run switch2-remote status
ok frames=1234 ffmpeg_drops=0 ended=False bridge_alive=True held=LS:0,1 holds=B:2480ms last_error=none
$ uv run switch2-remote shot /tmp/screen.png 5
ok /tmp/screen.png settled=True waited=0.25s
```

`shot` の `settled=False` は、TIMEOUT までに画面が止まらなかったことを示します。その場合も、その時点の最新フレームを保存します。

応答が `error` で始まる場合、クライアントは終了コード 1 で終わります。`tap` の途中で失敗した場合は、送信済みの入力 (`already sent`) と、届いたかどうか確認できなかった入力 (`unconfirmed`) を応答に含めます。ブリッジとの通信が途切れると、デーモンはブリッジに終了を指示し (ブリッジは中立のレポートを送ってから終了します。1 秒以内に終わらなければプロセスを止めます)、`status` の `bridge_alive` が `False` になります。以後の入力は受け付けないので、デーモンを起動し直してください。`status` の `ended=True` は、ffmpeg.exe が止まってフレームが届かなくなったことを示します。

デーモンは、デーモンと同じユーザー以外からの接続を拒否します。クライアントも、接続先が自分と同じユーザーのプロセスでなければ送信しません。

## 7. Switch 2 で Pico 2 W を 1P に登録する

1. Switch 2 をスリープから復帰させます。Pico 2 W の入力では復帰できないため、本体を操作してください (後述の既知の問題を参照)
2. HOME メニューの「コントローラー」から「持ちかた/順番を変える」を開きます
3. Pico 2 W の L と R を同時に押して 1P にし、A で確定します

```bash
uv run switch2-remote tap L+R
uv run switch2-remote tap A
```

動作確認では、Pico 2 W を 1P にした状態で、手元の通常のコントローラーから HOME に戻れませんでした。操作を終えるときは、クライアントから `HOME` を送って HOME 画面まで戻してください。

## 既知の問題

### Pico 2 W の入力でスリープを解除できない

スリープ中 (Elgato 4K X 側は `NO SIGNAL`) に HOME の短押し、HOME の 500 ミリ秒の長押し、A を送っても復帰しませんでした。本体の操作で復帰させた後は、同じ経路の入力が届きます。原因は確認できていません。人が付かずに動かす場合は、Switch 2 をスリープさせない設定にするか、復帰の手段を別に用意してください。

### デーモン起動直後の入力が反映されないことがある

デーモンを起動し直した直後に、HOME 画面で RIGHT の短押し 2 回がカーソルに反映されなかったことがあります。その後の短押しは 1 回ずつ反映され、もう一度デーモンを起動し直したときは再現しませんでした。原因は確認できていません。起動直後は、決定を伴わない入力と撮影で反映を確かめてから操作を始めてください。

このとき RIGHT を 400 ミリ秒長押しすると、カーソルが 3 つ進みました。HOME メニューのキーリピートによるものかは確認できていませんが、カーソル移動には長押し (`hold` と `down`) を使わず、短押し (`tap`) を使ってください。また、HOME メニューのソフト一覧では、左端で LEFT を押すとカーソルが右端の「もっとみる」へ回り込みます。

### 静止判定が画面遷移の途中で成立することがある

`shot` は画面が止まったことだけを確かめます。次の画面では途中のフレームで静止と判定され、目的の画面より前の画面が保存されました。

- メニューを開いた直後の、無地の画面が一瞬止まる箇所
- ラウンド開始時にカードを配るアニメーションの途中

どちらも 1〜4 秒待って撮り直すと、目的の画面を撮れました。撮った画像が目的の画面かどうかは、見出しの文字などの目印で確かめてください。

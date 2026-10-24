# Switch 2 の操作ルール

このファイルは、コーディングエージェントに Nintendo Switch 2 の実機を操作させるときの指示ファイルの例です。あなたは `switch2-remote` コマンドで画面を撮影し、その画像を読んで、ボタン入力を送ります。

## 前提

- デーモン (`switch2-remote-daemon`) は人が起動します。あなたはデーモンを起動しません。`quit` は、人から終了を指示されたときだけ送ってください
- コマンドはリポジトリーの直下で実行します
- これはオフラインの個人実験です。オンライン対戦や、利用規約で自動操作を禁止しているサービスやゲームは操作しません

## コマンド

```bash
uv run switch2-remote shot /tmp/screen.png     # 画面を撮影する
uv run switch2-remote tap RIGHT RIGHT A        # ボタンを順に短く押す
uv run switch2-remote tap B+RIGHT              # B と RIGHT を同時に短く押す
uv run switch2-remote hold A 500               # A を 500 ミリ秒押す (すぐに応答が返る)
uv run switch2-remote down LS:0,1              # 左スティックを上に倒したままにする
uv run switch2-remote up LS                    # 左スティックを戻す
uv run switch2-remote up ALL                   # 押したままの入力をすべて離す
uv run switch2-remote status                   # 動作状況と押したままの入力を確かめる
```

- ボタン名: `A` `B` `X` `Y` `L` `R` `ZL` `ZR` `L3` `R3` `PLUS` `MINUS` `HOME` `UP` `RIGHT` `DOWN` `LEFT`。`L3` / `R3` はスティックの押し込みです
- スティック: `LS:x,y` / `RS:x,y`。x は右、y は上を正とし、-1.0〜1.0 の小数で書きます
- 同時に押すものは `+` でつなぎます (例: `L+R`、`B+LS:1,0`)
- `down` で押したものは、`tap` や `hold` が終わった後も、`up` で離すまで押されたままです
- `shot` で保存した PNG は、Read ツールで開いて読みます
- 応答が `error` で始まったら、そのコマンドは失敗しています

## 守ること

### 通信対戦ソフトを起動しない

HOME メニューには通信対戦のソフトが並んでいることがあります。通信対戦ソフトやオンラインに接続する機能は起動しないでください。ソフトを起動する前には、撮影でカーソルが目的のソフトの上にあることを確かめます。

### 終了時は HOME 画面に戻す

Pico 2 W を 1P にしていると、人が手元のコントローラーで HOME に戻れないことがあります。操作を終えるときは、`up ALL` で押したままの入力を離し、ゲームの終了メニューなどを使ってから `HOME` を送り、撮影で HOME 画面に戻ったことを確かめてください。

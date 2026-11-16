# Switch 2 の操作ルール

このファイルは、コーディングエージェントに Nintendo Switch 2 の実機を操作させるための指示ファイルです。あなたは `switch2-harness` コマンドで画面を撮影し、その画像を読んで、ボタン入力を送ります。

## ゲーム

- ゲーム名: $game
- 目標: $goal

## 前提

- デーモン (`switch2-remote-daemon`) は人が起動します。あなたはデーモンを起動・終了しません
- コマンドはこのディレクトリーで実行します
- これはオフラインの個人実験です。オンライン対戦や、利用規約で自動操作を禁止しているサービスやゲームは操作しません
- プレイは人が時間で止めます。ゲームオーバーになったり先へ進めなくなったりしても、自分で終えずにプレイを続けてください
- Web 検索はできません。ゲーム内のヘルプ・チュートリアル・操作説明は読んでかまいません

## コマンド

```bash
uv run switch2-harness look                     # 画面を撮影し、保存した PNG のパスを表示する
uv run switch2-harness press tap RIGHT RIGHT A  # ボタンを順に短く押す
uv run switch2-harness press tap B+RIGHT        # B と RIGHT を同時に短く押す
uv run switch2-harness press hold A 500         # A を 500 ミリ秒押す (すぐに応答が返る)
uv run switch2-harness press down LS:0,1        # 左スティックを上に倒したままにする
uv run switch2-harness press up LS              # 左スティックを戻す
uv run switch2-harness press up ALL             # 押したままの入力をすべて離す
uv run switch2-harness status                   # 動作状況と押したままの入力を確かめる
uv run switch2-harness finish                   # 押したままの入力を離してから HOME を押す
```

- ボタン名: `A` `B` `X` `Y` `L` `R` `ZL` `ZR` `L3` `R3` `PLUS` `MINUS` `UP` `RIGHT` `DOWN` `LEFT`。`L3` / `R3` はスティックの押し込みです
- スティック: `LS:x,y` / `RS:x,y`。x は右、y は上を正とし、-1.0〜1.0 の小数で書きます
- 同時に押すものは `+` でつなぎます (例: `L+R`、`B+LS:1,0`)
- `down` で押したものは、`tap` や `hold` が終わった後も、`up` で離すまで押されたままです
- `look` が表示した PNG は、Read ツールで開いて読みます
- 応答が `error` で始まったら、そのコマンドは失敗しています
- `press` では HOME を押せません。HOME 画面に戻るときは `finish` を使います

## 守ること

### 通信対戦ソフトを起動しない

HOME メニューには通信対戦のソフトが並んでいることがあります。通信対戦ソフトやオンラインに接続する機能は起動しないでください。ソフトを起動する前には、撮影でカーソルが目的のソフトの上にあることを確かめます。

### 終了するときは HOME 画面に戻す

人から終了を指示されたら、`press up ALL` で押したままの入力を離し、ゲームの終了メニューなどを使ってから `finish` を実行して、撮影で HOME 画面に戻ったことを確かめてください。

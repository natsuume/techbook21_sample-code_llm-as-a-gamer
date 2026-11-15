# 第4章 型付きの判断を返すモデルと advisor でゲームを遊ぶ

第4章のサンプルです。Nintendo Switch 2 の画面を、問いごとの確率を返すモデル (Decision Model) に送り続け、その確率からボタンとスティックの入力を決めて送ります。操作方法・攻略情報・目標と戦略・問題の解決の助言は、裏で動かす headless の Claude Code (advisor) が 4 つの役割に分かれて考え、役割ごとに決まったゲームの情報のファイルに書きます。このディレクトリーの uv プロジェクトにあるパッケージ [`switch2_agent`](./src/switch2_agent/) を、コマンド `switch2-agent` で実行します。

映像の受け取りとボタン入力の送信には、第2章のパッケージ [`switch2_remote`](../ch02_setup/src/switch2_remote/) をそのまま使います。第2章のデーモン (`switch2-remote-daemon`) は使いません。

## 全体の構成

```text
Nintendo Switch 2 ── HDMI ──▶ Elgato 4K X ──▶ Windows PC: ffmpeg.exe
        ▲                                              │ 標準出力パイプ
        │ USB                                          ▼
Raspberry Pi Pico 2 W                         WSL2: switch2-agent
        ▲                                       ├─ キャプチャー: フレームの履歴と、場面ごとの代表のフレームを持つ
        │ Bluetooth                             ├─ 速い層: 画像・ゲームの情報・問いを Decision Model に送り、確率から操作を決める
Windows PC: controller_bridge.ps1 ◀─────────────├─ PadOutput: 押下状態と短押しを合わせたレポートを送る
                                                └─ advisor (4 つの役割): 役割ごとに claude -p で game_notes/<ゲーム>/ の 1 ファイルを書き換える
```

1 つのプロセス (`switch2-agent`) が、次の 4 つを動かします。

- キャプチャー: 第2章の Capture に、フレームの履歴を足したものです。間引いたフレームを時刻つきで持ち、鮮明度の谷で場面を区切って、場面ごとに最も鮮明なフレームを「意味のあるフレーム」として残します。意味のあるフレームは、advisor に渡す画像に使います
- 速い層 (Pipeline): 要求を 1 つずつ Decision Model に送り、応答を受け取ったら次の要求に進みます。TypeSafe Jev (`--model jev`) では、直前の要求の応答が返るか、失敗してから 0.2 秒たつまで待ち、その後に次の要求に載せるフレームを選びます。OpenAI Decisions API (`--model decisions`) では待ちません。要求には、フレームの履歴の最新の 1 枚の画像、ゲームの情報の 4 ファイルの内容、直近 5 回の応答と送った操作の履歴 (操作と、それを送った時刻。Decision Model の失敗やブリッジの復旧待ちで押下状態を中立に戻したときは、中立に戻したことと、その理由と時刻)、系列情報 (系列情報の節を参照)、37 の問いを入れます。画像のラベルには、実行を始めてからの経過秒 (t) と何秒前の画像かを書きます。前回の操作 (前回の応答で決めた操作か、その後に中立に戻したこと) があれば、その前の画像か、後の画像かも書きます
- PadOutput: 速い層が決めた押下状態と短押しを合わせて、コントローラーのブリッジに送ります。短押しは 120 ミリ秒で離します
- advisor: 4 つの役割 (操作方法の更新、目標・戦略の更新、問題の解決の助言、攻略情報・Tips の更新) に分かれています
  - 役割ごとに Decision Model の問い (`advisor_<役割>`) があり、その問いが真になると、裏のスレッドでその役割の `claude -p` を実行します。同時に実行するのは役割ごとに 1 件で、役割どうしは並列に動きます (最大 4 件)。実行中の役割は、その役割の問いが真になっても呼びません。実行が失敗した役割 (`claude` を起動できない、タイムアウトした、終了コードが 0 以外など) も、失敗した実行が終わってから 60 秒は呼びません
  - 問いが真にならなくても、advisor が決まった秒数呼ばれていなければ、決めた役割を呼びます (advisor の強制の呼び出しの節を参照)
  - 書き換えるゲームの情報のファイルは、役割ごとに 1 つに決まっています。advisor は Skill の手順で受け持ちのファイルを書き換え、速い層は次の要求からその内容を読みます
  - advisor に渡す画像は、要求とは別に選びます。意味のあるフレームの直近 5 枚と、それより後のフレームの履歴から等間隔に選んだ 5 枚で、最大 10 枚です。1 回の要求で複数の役割を呼ぶときは、同じ画像を使い回します。呼ぶ役割が無ければ、画像を選びません

### 系列情報

要求に入れる直近 5 回の履歴は、Jev で約 2 秒分、Decisions API で約 3 秒分です。この履歴からは、同じ操作を何分も続けていることや、画面が長く変わっていないことは読み取れません。要求には、次の節からなる系列情報も入れます。使う節は起動引数 `--timeline` で選べます。既定ではすべての節を入れます。

| 節 (`--timeline` の名前) | 内容 |
|---|---|
| 操作のまとめ (`actions`) | 直近 `--timeline-window` 秒 (既定 120 秒) の操作を、同じ操作が続いた区間ごとにまとめたもの。区間ごとに、始まりと終わりの経過秒、続いた秒数、回数を書きます。スティックは傾きの大きさを除き、向きだけで比べます。区間は新しいほうから 8 つまで書きます |
| 画面の変化 (`screen`) | 最新の画面が、いつからほぼ変わっていないか。処理したフレームの縮小グレー画像を、画面が最後に変わったときのフレームと比べ、差分 (画素の差の平均) が `--screen-change-threshold` (既定 2.0) 以上になったら、画面が変わったとみなします |
| ゲームの情報の更新 (`notes`) | ゲームの情報の 4 ファイルが、それぞれ最後に書き換わった時刻 |
| advisor を呼んだ時刻 (`advisor_calls`) | 役割ごとに、advisor が最後に要求を受け付けた時刻 |
| 小目標の時刻 (`subgoals`) | strategy.md の小目標ごとに、立てた時刻と、状態が最後に変わった時刻。`小目標: <小目標> (状態: <状態>` の形の行から読みます。この形の行が無ければ、この節は入れません |

時刻は、実行を始めてからの経過秒 (t) で書きます。操作のまとめ以外の節では、何秒前かも書きます。系列情報は、直近の応答と送った操作の履歴の後ろに空行をはさんで足し、Jev では `operation_history`、Decisions API では履歴のテキストに入れます。

ゲームの情報の更新と小目標の時刻は、要求を組み立てるときに読んだ内容の変化から求めます。書き換わった時刻は、advisor がファイルを書き換えた後の、最初の要求の時刻になります。前の実行で書かれた小目標は、「実行の開始にはすでにあった」と書きます。

画面の変化の既定値 2.0 は、2026-11-14 の実機の記録から決めた値です。行き詰まっていた場面 (家の中で壁に向かって歩き続けた場面、ソフトの一覧で何も押さなかった場面、1-1 の土管の前で右を押し続けた場面) では、止まっている画面の差分が 0.8 以下 (時間の表示などの小さな変化を含む) でした。歩いたりスクロールしたりしている画面の差分は 4 以上でした。敵が動くゲームの画面 (誤って始めたマイティボンジャックで、何も押さなかった区間) では、主人公が止まっていても差分が 1.2〜2.1 になった区間があります。画面の変化の節が書くのは画面全体の変化で、主人公が動いたかどうかではありません。

### advisor の強制の呼び出し

2026-11-14 の実機の記録には、速い層が行き詰まりに気づかず、advisor の問いの確率が低いまま、advisor が 181〜397 秒呼ばれなかった実行が 3 回ありました。これに備え、どの役割の advisor も `--advisor-idle-seconds` 秒 (既定 60 秒) 要求を受け付けていなければ、問いの確率によらず、`--advisor-idle-roles` の役割 (既定は strategy と advice) を呼びます。この記録で、60 秒、呼ぶ役割を strategy だけとして見積もると、advisor が 60 秒より長く間を空けずに呼ばれていた 2 回の実行では一度も呼ばず、行き詰まっていた 3 回の実行では 2〜6 回呼びました。

- 数え始めるのは、どれかの役割の advisor が要求を受け付けた時刻です。まだどの役割も受け付けていなければ、判断のループを始めた時刻 (最初のフレームを待つ時間を含む) から数えます。advisor の問いで呼んで受け付けたときも、数え直します
- 実行中の役割と、失敗した実行の後の 60 秒の間の役割は呼びません
- 強制の呼び出しで呼んだ役割の context.md には、きっかけとして、定期の呼び出しで呼んだことと、その理由を書きます。同じ要求で問いが真になった役割は、問いで呼んだものとして扱い、きっかけには問いの確率を書きます
- `--advisor-idle-scope each` を付けると、役割ごとに数えます。その役割の advisor が `--advisor-idle-seconds` 秒受け付けていなければ、その役割を呼びます
- `--advisor-idle-seconds 0` を付けると、強制の呼び出しをしません

### 問いと操作の決め方

問いは 37 個で、答えの型は 2 つです。選択肢の問いは、「なし」(`none`) を含む選択肢の中から 1 つを選ぶ問いで、選択肢ごとの確率で答えさせます。確率の問いは、「真である確率」で答えさせます。同じボタンの押した状態と短押しや、スティックの左と右のように、同時には選ばない操作を選択肢の問いにして、矛盾する答えが出ないようにしています。

| 問い | 数 | 答えの型 | 選択肢または段階 | 操作 |
|---|---|---|---|---|
| `button_<ボタン>` | 17 | 選択肢 | `none` (押さない)、`hold` (押した状態にする)、`tap` (1 回だけ短く押す) | `hold` なら、そのボタンを押した状態にし、次の応答まで押したままにします。`tap` なら、そのボタンを 1 回だけ短く押します (120 ミリ秒)。直前の応答まで押した状態だったボタンは、100 ミリ秒離してから押します |
| `stick_<LS/RS>_<x/y>` | 4 | 選択肢 | x は `none` (左右には倒さない)、`left`、`right`。y は `none` (上下には倒さない)、`up`、`down` | スティックを、その軸の選んだ向きに倒します |
| `stick_<LS/RS>_<x/y>_at_least_<50/75/100>` | 12 | 確率 | 傾きの段階 (50%、75%、100%) | 倒す軸の傾きの大きさを決めます |
| `advisor_<役割>` (`advisor_controls`・`advisor_strategy`・`advisor_advice`・`advisor_tips`) | 4 | 確率 | - | その役割の advisor に、受け持ちのファイルの更新を求めます |

操作は、次の規則で決めます。

- ボタン: 押す側の合計 (`hold` の確率と `tap` の確率の和) が、`none` の確率より大きいときだけ押します。同じ値なら押しません。`none` の確率が 3 つの選択肢の中で最も高くても、押す側の合計が上回れば押します。押すときは、`hold` の確率が `tap` の確率以上なら押した状態にし、そうでなければ短押しにします (同じ値なら押した状態にします)
- 十字キー: 相反する方向 (UP と DOWN、LEFT と RIGHT) を、押した状態と短押しを合わせて両方とも押すことになったら、押す側の合計が高いほうを残します。同じ値なら両方とも外します
- スティックの方向: 軸 (x と y) ごとに、確率が最も高い選択肢を採ります。最も高い確率の選択肢が 2 つ以上あるとき (`none` と同じ値の場合も、左と右や上と下が同じ値の場合も) は、その軸には倒しません
- スティックの強さ: 倒す軸だけ計算します。その軸の 50%・75%・100% の問いの確率を p50・p75・p100 とし、上の段階が下の段階を超えないよう切り下げます (q50 = p50、q75 = p75 と q50 の小さいほう、q100 = p100 と q75 の小さいほう)。傾きの大きさは 0.25 + 0.25 × (q50 + q75 + q100) で、0.25〜1.0 になります。右と上は正、左と下は負の向きです
- advisor: `advisor_<役割>` の問いの確率が `--threshold-advisor` の値を超えた役割の advisor に、受け持ちのファイルの更新を求めます。しきい値は 4 問で共通です。ちょうど同じ値のときは求めません

選択肢の問いでは、`none` を先頭の選択肢に置いています。Jev は先頭の選択肢に寄りやすいと公式ドキュメント ([Model jaggedness](https://docs.typesafe.ai/model-jaggedness.md) の Choice option order、2026-11-13 に確認) に書かれているためです。Decision Model への役割の説明でも、迷ったら `none` を選ぶよう指示しています。OpenAI Decisions API が選択肢の問いへの回答を拒否 (refusal) したときは、`none` を選んだもの (`none` の確率を 1、ほかの選択肢を 0) として扱います。確率の問いへの回答の拒否は、確率 0 として扱います。

## 必要なもの

- 第2章の環境 (機材、Pico 2 W の準備、Windows とのペアリング、ffmpeg、Pico 2 W の 1P への登録)。[`../ch02_setup/README.md`](../ch02_setup/README.md) の手順で用意してください
- Decision Model の API キー。使うモデルに応じて、次の環境変数に設定します
  - TypeSafe Jev (`--model jev`): `TYPESAFE_API_KEY`
  - OpenAI Decisions API (`--model decisions`): `OPENAI_API_KEY`
- [Claude Code](https://code.claude.com/docs/) (WSL2 側で `claude` コマンドを実行できること)

`switch2-agent` は、キャプチャーとコントローラーのブリッジを自分で起動します。Elgato 4K X と COM ポートはどちらも同時に 1 つのプロセスしか使えないため、第2章のデーモン、Elgato Studio、OBS などは終了してから実行してください。

## セットアップ

WSL2 側で、このリポジトリーの `ch04_techniques` ディレクトリーに移動して依存パッケージを導入します。第2章のパッケージも、`../ch02_setup` から自動で導入されます。以降のコマンドも、このディレクトリーで実行します。

```bash
cd ch04_techniques
uv sync
```

## 実行

API キーを環境変数に設定し、ゲーム名と Decision Model を指定して実行します。ゲーム名には英数字、`_`、`-` だけを使えます。

```bash
export TYPESAFE_API_KEY=...
uv run switch2-agent --game <名前> --model jev
```

API キーは `.env` のファイルに書くこともできます。`switch2-agent` は `.env` を自分では読まないため、`uv run` に `--env-file .env` を付けて、uv に読み込ませます。見本の `.env.sample` を `.env` に写し、使うモデルの API キーを書いてから実行します。シェルで同じ名前の環境変数を設定してある場合は、シェルの値が使われます。`.env` は Git の管理から外しています (`.gitignore`)。`.env` を `ch04_techniques` に置くと advisor から読めると考えられるため、注意事項も確かめてください。

```bash
cp .env.sample .env
chmod 600 .env
uv run --env-file .env switch2-agent --game <名前> --model jev
```

advisor の Skill (`.claude/skills/`) を読み込ませるため、必ず `ch04_techniques` ディレクトリーで実行してください。別のディレクトリーで実行すると、説明を表示して終了します。

起動すると、ブリッジの起動 (COM ポートを開くまで最大 30 秒) とキャプチャーの起動の後に、速い層が要求を送り始めます。起動直後は、最初のフレームが届くまで最大 10 秒待ちます。止めるときは Ctrl+C を押します。止まるときは、押下状態を中立に戻してから、advisor、キャプチャー、ブリッジの順に止めます。advisor は 4 つの役割を同時に止め、実行中の役割は、`claude` のプロセスに終了を指示し、5 秒で終わらなければ強制的に終了させます。

次の場合は、押下状態を中立に戻して自動で止まります。

- キャプチャーが終わったとき、または最初のフレームが届いた後に新しいフレームが 2 秒届かなかったとき。Decision Model の応答を待つ間も、裏のスレッドが 0.1 秒ごとにキャプチャーの状態を確かめ、止まったと分かった時点で押下状態を中立に戻します。止まった後に届いた応答の操作は反映しません
- Decision Model の失敗 (HTTP のエラー、形の違う応答など) が `--max-consecutive-errors` 回続いたとき。失敗のたびに、押下状態を中立に戻して 1 秒待ちます
- コントローラーのブリッジが復旧できない状態 (第2章の `failed`) になったとき。ブリッジの復旧中 (第2章の `recovering`) は止まらず、押下状態を中立に戻して、復旧が終わるまで Decision Model に要求を送らずに待ちます

### 起動引数

| 引数 | 内容 | 既定値 | 範囲 |
|---|---|---|---|
| `--game NAME` | ゲーム名 (必須)。ゲームの情報は `game_notes/<NAME>/` に置きます | なし | 英数字、`_`、`-` |
| `--model {jev,decisions}` | Decision Model (必須)。`jev` は TypeSafe Jev、`decisions` は OpenAI Decisions API です | なし | `jev`、`decisions` |
| `--ffmpeg-fps N` | ffmpeg.exe が出力するフレームレート | 30 | 1〜60 の整数 |
| `--process-fps N` | 間引いて残すフレームレート | 10 | 1〜`--ffmpeg-fps` の値の整数 |
| `--history-seconds S` | フレームの履歴を持つ秒数。advisor に渡す画像のうち、意味のあるフレーム以外は、この秒数の範囲から等間隔に選びます | 5.0 | 0 より大きい有限の値 |
| `--image-width N` | 要求と advisor に渡す画像の幅。縦横比を保って縮小します | 1280 | 498〜1280 の整数 |
| `--jpeg-quality N` | 要求と advisor に渡す画像の JPEG の品質 | 85 | 1〜100 の整数 |
| `--threshold-advisor P` | `advisor_<役割>` の問いのしきい値 (4 問で共通) | 0.5 | 0〜1 |
| `--valley-ratio R` | 鮮明度が場面の山の何倍まで下がったら場面の切り替わり (谷) とみなすか。advisor に渡す意味のあるフレームの判定に使います | 0.5 | 0 と 1 を含まない範囲 |
| `--similarity-threshold D` | 意味のあるフレームが直前に残したものと似ているとみなす差分 (縮小したグレー画像の画素の差の平均) の上限。advisor に渡す意味のあるフレームの判定に使います | 0.15 | 0〜255 |
| `--advisor-timeout S` | advisor の 1 回の実行の上限 (秒。4 つの役割で共通) | 300 | 0 より大きい有限の値 |
| `--max-consecutive-errors N` | Decision Model の失敗がこの回数続いたら止まります | 5 | 1 以上の整数 |
| `--timeline SECTIONS` | 要求に入れる系列情報の節。`actions`・`screen`・`notes`・`advisor_calls`・`subgoals` をカンマで区切って指定します。`all` ならすべての節、`none` なら系列情報を入れません | `all` | 節の名前、`all`、`none` |
| `--timeline-window S` | 系列情報の操作のまとめに入れる直近の秒数 | 120 | 0 より大きく 3600 以下 |
| `--screen-change-threshold D` | 系列情報の画面の変化で、画面が変わったとみなす差分 (縮小したグレー画像の画素の差の平均) の下限 | 2.0 | 0〜255 |
| `--advisor-idle-seconds S` | advisor がこの秒数要求を受け付けていなければ、強制で呼びます。0 なら強制では呼びません | 60 | 0 以上の有限の値 |
| `--advisor-idle-scope {any,each}` | 強制の呼び出しで数える範囲。`any` はどの役割も呼ばれていない時間、`each` は役割ごとの時間を数えます | `any` | `any`、`each` |
| `--advisor-idle-roles ROLES` | 強制で呼ぶ役割。`controls`・`strategy`・`advice`・`tips` をカンマで区切って指定します | `strategy,advice` | 役割の名前 |
| `--save-requests` | 要求ごとの入出力を `runs/<YYYYmmdd-HHMMSS>/requests.jsonl` と `frames/` に書きます (ログの節を参照) | オフ | なし (付けるとオン) |
| `--record-dashboard` | ダッシュボードが読む記録 (処理したフレームの判定と縮小画像、送ったレポート、起動引数、要求の行、advisor の途中経過) を `runs/<YYYYmmdd-HHMMSS>/` に書きます (ログの節を参照) | オフ | なし (付けるとオン) |

範囲外の値や、`--process-fps` が `--ffmpeg-fps` より大きい指定では、起動せずにエラーを表示します。`--threshold-advisor` は「`advisor_<役割>` の問いの確率がこの値を超えたら真」の値で、4 つの役割で共通です。ちょうど同じ値は偽です。ボタンとスティックの問いには、しきい値はありません (問いと操作の決め方の節を参照)。`--image-width` の下限の 498 は、Jev が受け付ける画像の各辺の下限 (280 ピクセル) を 16:9 の高さで満たす幅です。

`--save-requests` を付けると、要求ごとに送った画像を保存するため、ログの容量が大きくなります。長時間の試行では付けないでください。

## ゲームの情報のファイルと Skill

ゲームの情報は、ゲームごとに次の 4 ファイルに書きます。起動時に、無いファイルを作ります。controls.md と strategy.md は、無いときと、中身が空か空白だけのときに、既定の内容を書きます。tips.md と advice.md は空のファイルとして作ります。中身のあるファイルは変えません。

| ファイル | 内容 | 書き込む advisor の役割 | Skill |
|---|---|---|---|
| `game_notes/<ゲーム>/controls.md` | ゲームの操作方法 (ボタンの割り当て、会話の送り方、メニューの開き方など) | 操作方法の更新 (`controls`) | `record-controls` |
| `game_notes/<ゲーム>/tips.md` | 攻略情報・Tips (敵の特徴、アイテム、ゲームの仕組み、注意点など) | 攻略情報・Tips の更新 (`tips`) | `record-tips` |
| `game_notes/<ゲーム>/strategy.md` | 目標・進行状況・戦略 | 目標・戦略の更新 (`strategy`) | `record-strategy` |
| `game_notes/<ゲーム>/advice.md` | 問題の解決の助言 | 問題の解決の助言 (`advice`) | `record-advice` |

strategy.md は、目標・進行状況・戦略の 3 つの節で書きます。目標は大目標・中目標・小目標の 3 段階で、それぞれに状態 (未着手・途中・達成・不成立) を付けます。進行状況には、どこまで進んだか、今いる場所と画面の状態、画面で確かめられる値を書き、戦略には、今の小目標に向けた進め方、押すべきボタンの方針、避けるべき操作、安全のための制限を書きます。

advice.md は、速い層が先に進めなくなったときの助言です。助言が当てはまる場面、起きている問題、原因の見立て、試す手順、試して効果が無かったことを書きます。Decision Model には、助言に書かれている場面に今の画面が当てはまるときだけ参考にするよう、役割の説明で指示しています。

controls.md と strategy.md の既定の内容は [`default_notes.py`](./src/switch2_agent/default_notes.py) にあり、特定のゲームに固有の説明は含めていません。

- controls.md: Nintendo Switch 2 のゲームに多い一般的なボタンの対応 (A で決定、B でキャンセルや戻る、+ でポーズやメニューなど)。どの行にも `(一般)` の印を付けて、このゲームではまだ確かめていないことを表します。Decision Model への役割の説明にも、`(一般)` の行の意味を書いています。操作方法の advisor は、`(一般)` の行のうち、このゲームで確かめたものを消し、確かめた結果を「このゲームで確かめた操作」の節に書きます (Skill の `record-controls`)
- strategy.md: 段階に分けた最初の目標。大目標は「ゲームを先に進める」、中目標は「基本の操作を覚え、ゲームを始めて最初の場面を進める」、最も細かい小目標は「操作方法を把握する」です。戦略の節には、ボタンとスティックを 1 つずつ試すことと、安全のための制限を書いています。小目標が達成されたら、目標・戦略の advisor が次の目標を立てます

以前の実行で作られた `progress.md` は、作らず、読まず、変えません。中身を残したい場合は、strategy.md の進行状況の節に人が写してください。

速い層は、要求のたびに 4 ファイルを読み直し、内容をそのまま要求に入れます。advisor の出力 (`claude -p` の答え) は、ゲームの情報の受け渡しには使いません。人が 4 ファイルを書いておくと、最初から手がかりとして使われます。

Skill は [`.claude/skills/`](./.claude/skills/) の 4 つの `SKILL.md` で、advisor は役割ごとに 1 つの Skill を使います。ファイルは要求に毎回入るため、strategy.md は 2500 字以内、advice.md は 1500 字以内、ほかは 3000 字以内を目安に短く保つよう、Skill に書いています。

advisor (`claude -p`) は、役割ごとに次の指定で実行します。例は strategy の役割の場合で、`Edit` と `Skill` には、その役割のファイルと Skill が 1 つずつ入ります。

```bash
claude -p <プロンプト> --output-format json --permission-mode dontAsk \
  --setting-sources project --settings '{"disableAllHooks": true}' \
  --allowedTools "Read(/game_notes/<ゲーム>/**),Read(/runs/<YYYYmmdd-HHMMSS>/**),Edit(/game_notes/<ゲーム>/strategy.md),Skill(record-strategy)" \
  --max-turns 30
```

`--record-dashboard` を付けたときは、`--output-format json` の代わりに `--output-format stream-json --verbose` を付けます。ほかの指定は同じです。

許可のルールで読み取りを許すのは、`game_notes/<ゲーム>/` と、その実行の `runs/<YYYYmmdd-HHMMSS>/` の下です (ほかに読める範囲は注意事項に書きます)。書き込みを許すのは役割のファイル 1 つだけで (strategy の役割なら strategy.md)、ほかの 3 ファイルは読むだけです。使える Skill も役割の Skill 1 つだけで、Bash は許しません。確認が要る操作のうち、許可のルールに無いものは、確認を求めずに拒否されます (`--permission-mode dontAsk`)。拒否された操作は、ログの `permission_denials` に残ります。advisor の `claude` のプロセスには、Decision Model の API キーの環境変数 (`TYPESAFE_API_KEY`・`OPENAI_API_KEY`) を渡しません。

## ログ

実行ごとに `runs/<YYYYmmdd-HHMMSS>/` を作り、次のファイルを書きます。

| ファイル | 内容 |
|---|---|
| `run.jsonl` | 1 行 1 件の JSON のログ。要求ごとの所要時間 (`select_s`・`encode_s`・`decide_s`・`apply_s`)、Jev で直前の要求の後に待った秒数 (`interval_wait_s`。待たなければ 0)、画像の枚数と大きさ、決めた操作、advisor の役割ごとの問いの確率 (`advisor_probabilities`)、advisor を求めた役割 (`advisor_requested`) と受け付けた役割 (`advisor_accepted`)、強制の呼び出しで求めた役割 (`advisor_forced`)、advisor の実行の結果 (役割の `role` を含む)、失敗などを書きます。同じ内容の要約を画面にも表示します。`--record-dashboard` を付けたときは、`start` の行に起動引数 (`args`) と時計の起点 (`origin_mono`。`time.monotonic()` の秒) を足し、終了時に記録の件数 (`record_end`。書いた件数と捨てた件数) を書きます。記録を書けなかったときは、その種類の記録をやめて `record_error` の行を書きます |
| `advisor-<役割>-001/` など (例: `advisor-strategy-001/`) | advisor の役割ごと、1 回の実行ごとの入出力 (連番は役割ごと)。advisor 用に選んだ最大 10 枚の画像 (`frame-01.jpg` など。意味のあるフレーム、フレームの履歴から選んだものの順で、どちらも古い順)、文脈 (`context.md`。役割と、呼んだきっかけを含む)、結果 (`result.json`。終了コード、所要時間、`claude -p` の出力、拒否された操作) |
| `advisor-<役割>-001/stream.jsonl` など | `--record-dashboard` を付けたときだけ書く、advisor の途中経過の JSON Lines。`claude -p` を `--output-format stream-json --verbose` で動かし、標準出力の行 (ツール呼び出し、ツールの結果、最後の `result` など) が 1 行届くたびに、読んだ時刻 (`mono`。`time.monotonic()` の秒) と、その行を JSON として読んだもの (`data`) を `stream.jsonl` の 1 行に書きます。JSON として読めない行は、`data` の代わりに元の文字列を `raw` に入れます。このときの `result.json` の `claude -p` の出力は、最後に届いた `result` の行です。`stream.jsonl` を書けなかったときは、`run.jsonl` に `record_error` の行を書き、advisor の実行は続けます |
| `frames.jsonl` | `--record-dashboard` を付けたときだけ書く、処理したフレーム (間引いた後、意味のあるフレームの判定に入れたフレーム) 1 枚ごとの JSON Lines。フレームの時刻 (`mono`)、鮮明度、直前のフレームとの差分 (`diff`)、判定の状態 (`phase`、今の区間の山、谷や回復とみなす鮮明度 `ref`)、そのフレームで起きた出来事 (`event`。谷に向かい始めた `fall`、谷を確定した `close`)、縮小画像のファイル名 (`thumb`) を書きます |
| `thumbs/` | `--record-dashboard` を付けたときだけ書く、処理したフレームごとの縮小画像 (320×180、JPEG の品質 70)。ファイル名は、フレームの時刻 (`mono`) をマイクロ秒にした整数です |
| `pad.jsonl` | `--record-dashboard` を付けたときだけ書く、コントローラーのブリッジにレポートを送ろうとした 1 回ごとの JSON Lines。送信が戻った時刻 (`mono`)、送ったきっかけ (`source`。判断の操作の `apply`、短押しの開始の `tap_start` と解除の `tap_release`、中立の `neutral` と `stop_output`)、送った押下状態 (`state`)、成否 (`ok`・`error`) を書きます |
| `requests.jsonl` | `--save-requests` か `--record-dashboard` を付けたときに書く、Decision Model への要求ごとの入出力の JSON Lines。1 行目は header で、`--model` の値、役割の説明と安全のための規則、37 の問い (名前、文、種類、答えの型、選択肢の問いの選択肢) を書きます。2 行目以降は要求ごとに 1 行で、要求に載せた 1 枚の画像のラベルとファイル名、ゲームの情報の 4 ファイルの内容、直近の応答と送った操作の履歴、系列情報 (`timeline_text`。系列情報を入れないときは空文字)、確率の問いの確率 (`probabilities`)、選択肢の問いの選択肢ごとの確率 (`choices`)、応答の本文、決めた操作、advisor の役割ごとの問いの確率 (`advisor_probabilities`)、advisor を求めた役割 (`advisor_requested`) と受け付けた役割 (`advisor_accepted`)、強制の呼び出しで求めた役割 (`advisor_forced`)、結果 (`ok`: 操作を送った、`error`: Decision Model か操作の送信の失敗、`discarded`: キャプチャーが止まったため応答を捨てた) を書きます。各行の `number` は `run.jsonl` の `decision` の `number` と同じで、2 つのログで同じ要求の行を対応づけられます。`--record-dashboard` を付けたときは、画像ごとに縮小画像のファイル名 (`thumb`) を足します。`--save-requests` を付けていなければ、画像を `frames/` に書かず、画像の `file` を null にします |
| `frames/` | `--save-requests` を付けたときだけ書く、要求に載せた画像 (JPEG)。ファイル名は、実行を始めてからの経過時間 (マイクロ秒) です。複数の要求に載った同じフレーム (失敗した要求の後に、同じフレームで送り直したときなど) は、1 回だけ保存します |

`frames.jsonl`・`pad.jsonl`・`stream.jsonl` の `mono` から `run.jsonl` の `start` の `origin_mono` を引くと、`requests.jsonl` の経過秒 (`t_sent` など) と同じ起点からの秒になります。

`game_notes/` と `runs/` の中身は Git の管理から外しています (`.gitignore`)。

## 開発用コマンド

`ch04_techniques` ディレクトリーで実行します。

```bash
uv run ruff format   # コードを整形する
uv run ruff check    # 静的解析を行う
uv run pytest        # テストを実行する (実機、ネットワーク、claude コマンドは使いません)
```

## 注意事項

- このサンプルは、オフラインの個人実験での利用に限ります。利用規約で自動操作を禁止しているサービスやゲーム、オンライン対戦では使わないでください
- 速い層は HOME ボタンも押せます。通信対戦のソフトを起動したり、本体の設定を変えたりしないよう、Decision Model と advisor の両方に安全のための規則を毎回渡していますが、守られる保証はありません。試行中は、人が画面を見ていてください
- Decision Model の API は、要求ごとに料金がかかります。速い層は、応答を受け取ると次の要求を送ります (Jev では直前の要求の応答が返るか、失敗してから 0.2 秒待ち、Decisions API では待ちません)。そのため、実行している間は要求を送り続けます
- 系列情報を入れると、要求の入力が増えます。2026-11-14 に保存済みの要求を Jev に送り直して確かめたところ、すべての節を入れた要求の入力は約 900〜1,200 トークン増え、応答までの時間は変わりませんでした。実機の試行で系列情報が速い層の操作をどう変えるかは、確かめていません
- Jev の文脈長は、要求全体で 64k トークン、問い 1 つあたり 32k トークンです。画像 1 枚が何トークンになるかが公開されていないため、画像 1 枚と 37 の問いが収まるかは確かめていません。超えた場合は HTTP 400 (`max_tokens_exceeded`) になり、Decision Model の失敗として扱います
- 4 つの役割の advisor (`claude -p`) は、同時に動くことがあります (最大 4 件)。そのぶん、Claude Code の利用枠や料金を使います。強制の呼び出しでは、advisor の問いが真にならなくても、advisor が `--advisor-idle-seconds` 秒 (既定 60 秒) 呼ばれていなければ、既定で 2 つの役割を呼びます。実機の試行で強制の呼び出しが何回起きるかは、確かめていません
- 要求と advisor に渡す画像の幅の既定値 1280 と JPEG の品質の既定値 85 は、判断の精度と要求の大きさの釣り合いを確かめる前の値です
- advisor の書き込みの許可 (`Edit(...)` のルール) では、Bash や、advisor が起動したプログラムによる書き込みは止められません。そのため Bash を許していません
- advisor はゲームの画面の画像を読むため、画面に写った文字で advisor への指示が注入されるおそれがあります。注入された指示に従うと、advisor は読んだファイルの内容を、その役割のゲームの情報のファイルに書き写せます。書き換えられるファイルは役割ごとに 1 つに限っていますが、どのファイルも、毎回の要求で Decision Model の API に送られます。そのため、許可のルールで読み取りを許すディレクトリーと書き込みを許すファイルを限り、advisor に API キーを渡していません
- Claude Code のドキュメントでは、作業ディレクトリー (`ch04_techniques`) の中のファイルの読み取りは、許可のルールが無くても承認されます。そのため advisor は、`ch04_techniques` の中のほかのファイルも読めると考えられます。実際に読める範囲は確かめていません
- `.env` を `ch04_techniques` に置くと、advisor はそのファイルも読めると考えられます。advisor の `claude` のプロセスには API キーの環境変数を渡していませんが、`.env` を読んで、画面の文字で注入された指示に従うと、API キーをゲームの情報のファイルに書き写せます。書き写された API キーは、毎回の要求で Decision Model の API に送られます。避けるには、API キーを書いたファイルを `ch04_techniques` に置かないでください。API キーは `export` で設定するか、`.env` を `ch04_techniques` の外に置いて `--env-file` にそのパスを指定します
- `--setting-sources project` で実行する人のユーザー側の設定を読まないようにしていますが、ユーザーの `~/.claude/CLAUDE.md` が読まれるかは確かめていません。advisor の動作は、実行する人の Claude Code の環境によって変わることがあります
- `--save-requests` を付けたときの `requests.jsonl` と `frames/` には、ゲームの画面の画像と、ゲームの情報の 4 ファイルの内容がそのまま残ります
- `--record-dashboard` を付けたときの `thumbs/` には、処理したフレームごとのゲームの画面の縮小画像が残ります (既定の `--process-fps 10` では 1 時間に 36,000 枚)。`requests.jsonl` には、ゲームの情報の 4 ファイルの内容がそのまま残ります
- `--record-dashboard` の記録 (`frames.jsonl` と `thumbs/`、`pad.jsonl`) は裏のスレッドで書き、書き終えていない記録を溜められる件数に上限があります (フレームは 600 件、レポートは 2000 件)。上限を超えた記録は捨て、捨てた件数を `run.jsonl` の `record_end` に書きます。フレームの記録は 1 件ごとに元の画像 (約 1.3MiB) を持つため、ディスクへの書き込みが止まると、最悪で約 790MiB のメモリーを使います
- `--record-dashboard` を付けたときの `stream.jsonl` には、advisor が読んだファイルの中身 (ツールの結果) と、ゲームの情報のファイルに書いた内容がそのまま残ります。読んだ画像も、ツールの結果に入って残ると考えられます (確かめていません)。画面の文字で注入された指示に従って、advisor が `ch04_techniques` の中のほかのファイル (`.env` など) を読んだ場合は、そのファイルの中身も残ります
- 利用によって生じたアカウントの制限、データの損失、機器の故障などの不利益は、利用者の自己責任となります

## 3 層のエージェントの骨組み (switch2-layers)

同じ uv プロジェクトに、surface・middle・deep の 3 層でゲームを遊ぶエージェントの骨組みのパッケージ [`switch2_layers`](./src/switch2_layers/) があります。コマンド `switch2-layers` で実行します。3 層は 1 つのプロセスの中で別々のスレッドとして動き、実行ディレクトリーのファイルで情報を受け渡します。

| 層 | 動く間隔 | 受け持ち |
|---|---|---|
| surface | 0.2〜2 秒ごと (待ち時間は middle の出力で決まります) | 直近 2 秒のフレームから最大 5 枚を選び、時刻と追えている box を書き込んだ画像、文書、middle の出力から、コントローラーの操作を決めて送ります (既定では TypeSafe の Jev に問います)。必要なら deep に文書の更新を求めます |
| middle | 前回の応答から `--middle-interval` 秒後 (既定 5 秒)。最初の実行は、最初の画像を `frames.jsonl` に記録した後です | 一定間隔で保存したフレーム、surface の記録 (`surface.jsonl`)、操作の記録 (`operations.jsonl`) から、場面の名前、詰まっているか、surface の待ち時間、系列情報、場面ごとの短期のタスクリストを決めます (既定では OpenAI の gpt-6-luna を呼びます)。初めての場面に名前を付けたら、その場面の操作説明と攻略情報の deep を求めます |
| deep | 求められたときと、同じ文書の前回の終了から `--deep-auto-seconds` 秒後 (既定 60 秒) | 操作説明・攻略情報・大方針・助言・マップの文書を書き直します。ジョブごとに `deep/<種類>-<連番>/` を作り、文書のコピーを `work/` に置きます |

この版の deep はフェイクで、モデルの API を呼ばず、文書を変えずに成功を返します。surface は、既定では TypeSafe の Jev を呼びます (`--surface jev`)。`--surface fake` を付けると surface はフェイクになり、何も押さない中立の操作を返して deep を求めません。middle は、既定では OpenAI の gpt-6-luna を呼びます (`--middle luna`)。`--middle fake` を付けると middle もフェイクになり、固定の場面名 `fake scene` と待ち時間 0.2 秒を返します。3 層ともフェイク (`--surface fake --middle fake`) でも、フレームの保存、操作の送信と記録、場面の一覧への追加、deep のジョブのディレクトリーの用意は動きます。各層は Protocol (`SurfaceDecider`・`MiddleAnalyzer`・`DeepRunner`) で受け取るため、フェイクをほかの実装に差し替えられます。

### サブコマンド

| コマンド | 内容 |
|---|---|
| `switch2-layers run [起動引数]` | 実機 (キャプチャーとコントローラーのブリッジ) につないで 3 層を動かします。Ctrl+C で止まります |
| `switch2-layers middle-once --run-dir DIR [--middle-window S] [--middle fake\|luna]` | 保存済みの実行ディレクトリーに対して middle を 1 回動かし、`middle/state.json` などの出力を書きます。初めての場面でも deep は起動せず、求める文書の種類を `middle/calls.jsonl` に書くだけです。`--middle` は run と同じです |
| `switch2-layers deep-once --run-dir DIR --doc KIND [--scene SCENE_ID]` | 保存済みの実行ディレクトリーに対して deep のジョブを 1 回、終わるまで動かします。`KIND` は `controls`・`tips`・`strategy`・`advice`・`map` のどれかです。`--scene` は `controls` と `tips` にだけ付けられます。付けなければ、`middle/state.json` の現在の場面 (無ければ共通の文書) を対象にします |
| `switch2-layers publish-note --run-dir DIR --note PATH --base-sha256 HASH\|none --from FILE` | deep が編集したファイル (`--from`) の内容で、`notes/` の文書 (`--note`) を置き換えます |

middle-once・deep-once・publish-note は実機を使わないため、保存済みの実行ディレクトリーに対して試せます。結果は標準出力に JSON の 1 行で書きます。run と middle-once は、既定の `--middle luna` では OpenAI の API キーを環境変数 `OPENAI_API_KEY` から読みます (「middle の実装」の節)。run は、既定の `--surface jev` では TypeSafe の API キーも環境変数 `TYPESAFE_API_KEY` から読みます (「surface の実装」の節)。

```bash
uv run --env-file .env switch2-layers run
uv run switch2-layers run --surface fake --middle fake
uv run --env-file .env switch2-layers middle-once --run-dir runs/<YYYYmmdd-HHMMSS>
uv run switch2-layers middle-once --run-dir runs/<YYYYmmdd-HHMMSS> --middle fake
uv run switch2-layers deep-once --run-dir runs/<YYYYmmdd-HHMMSS> --doc strategy
```

publish-note は、`notes/` の文書の今の SHA-256 (文書が無ければ `none`) が `--base-sha256` と一致したときだけ置き換え、終了コード 0 で終わります。一致しなければ置き換えずに、今の SHA-256 を標準出力に書いて終了コード 3 で終わります。`--note` は `notes/` の中のパス、`--from` は同じ実行の `deep/<種類>-<連番>/work/` の中のファイルに限ります。どちらもシンボリックリンクを解決した先で確かめ、範囲の外や通常のファイルでないものを指定すると、何も変えずに終了コード 2 で終わります。同じ実行の publish-note どうしは、`notes/.publish.lock` のロックを取り、比べてから置き換えるまでを 1 つずつ行います。

### middle の実装 (`--middle`)

run と middle-once は、`--middle` で middle の実装を選びます。

| 値 | 内容 |
|---|---|
| `luna` (既定) | OpenAI の gpt-6-luna を Responses API で呼びます。環境変数 `OPENAI_API_KEY` に API キーが要ります |
| `fake` | 固定の場面名 `fake scene` と待ち時間 0.2 秒を返すフェイクです。API キーは要りません |

`--middle luna` で `OPENAI_API_KEY` が無いとき (値が空か空白だけのときを含む) は、実機に触れず、実行ディレクトリーにも何も書かずに、エラーを表示して終了します (終了コード 2)。API キーは、`switch2-agent` と同じく `.env` に書き、`uv run --env-file .env` で読み込ませることもできます。

gpt-6-luna には、変わらない指示 (developer メッセージ) と、場面の一覧、前回の middle の出力、大方針、現在の場面の短期のタスクリストと操作説明、前回以降の surface と操作の記録の要約、場面転換の印、直近 `--middle-window` 秒の一定間隔のフレームから最大 4 枚 (時刻のラベル付き) を送ります。reasoning effort は `none` にし、出力は Structured Outputs の JSON Schema で受け取ります。変わらない指示の直後にキャッシュの区切りを置き、指示をプロンプトキャッシュから読ませます。応答の待ちは 30 秒までです。HTTP のエラー、完了しなかった応答、拒否、形の合わない出力は middle の失敗として数え、再試行せずに次の間隔で呼び直します。

`middle/calls.jsonl` の各行には、トークン数 (`usage`。入力・キャッシュから読んだ入力・キャッシュに書いた入力・出力・推論)、公式の単価から計算した概算の費用 (`cost_usd`。USD)、デバッグ用の応答の本文 (`response`。エラーの応答では本文の先頭を `error_body_head`) も書きます。

### surface の実装 (`--surface`)

run は、`--surface` で surface の実装を選びます。

| 値 | 内容 |
|---|---|
| `jev` (既定) | TypeSafe の Jev (`jev-1.14.0`) を呼びます。環境変数 `TYPESAFE_API_KEY` に API キーが要ります |
| `fake` | 何も押さない中立の操作を返し、deep を求めないフェイクです。API キーは要りません |

`--surface jev` で `TYPESAFE_API_KEY` が無いとき (値が空か空白だけのときを含む) は、実機に触れず、実行ディレクトリーにも何も書かずに、エラーを表示して終了します (終了コード 2)。API キーは、`OPENAI_API_KEY` と同じく `.env` に書き、`uv run --env-file .env` で読み込ませることもできます。

Jev には、役割と規則の説明、文書 (短期のタスクリスト・攻略情報・操作説明・助言)、middle の系列情報とその作成時刻、直近に送った操作、選んだ画像 (時刻のラベル付き) を送り、次の問いを 1 回の要求でまとめて問います。

| 問い | 型 | 数 | 使い方 |
|---|---|---|---|
| ボタン | Choice (`none`・`hold`・`tap`) | 押せるボタンごと | `hold` と `tap` の確率の和が `none` を超えたら押します。`hold` が `tap` 以上なら押し続け、そうでなければ短押しです |
| スティックの向き | Choice (`none`・負の向き・正の向き) | 2 本 × 横・縦 = 4 | 確率が最も高い向きに倒します (同率の最大は倒しません) |
| スティックの強さ | Noul (50%・75%・100% 以上か) | 12 | 3 つの確率から、傾きを 0.25〜1.0 に決めます |
| advisor 判定 | Noul | 操作説明・攻略情報・大方針・助言の 4 | 確率が `--threshold-advisor` を超えた文書の deep を求めます |
| 情報ラベル | Noul | 操作説明・攻略情報・大方針・詰まり・マップの 5 | 確率を `surface.jsonl` の `info_labels` に書きます |
| 場面転換 | Noul | 1 | 確率が 0.5 を超えたら、場面転換として次の middle に伝えます |
| box の存在 | Noul | box の対象ごと | 確率が 0.5 未満の対象の box は使いません |
| box | Box | 問い直す対象ごと | 最新のフレームでの対象の位置です |

box で取る対象は、`notes/scenes/<場面ID>/box_targets.md` から、`- 名前: 説明` の形の行を先頭から最大 8 個読みます。このファイルが無いか空なら `notes/common/box_targets.md` を、それも無いか空なら固定の 4 つ (自機・敵・敵の攻撃・目標地点) を使います。Jev が返した box は、キャプチャーのすべてのフレーム (約 10 fps) で、NCC のテンプレートマッチング (縮小 0.5 のグレースケール) で追います。Jev に box を問い直すのは、まだ box が無い対象、見失った対象、前回の問い直しから `--box-requery-every` 回の要求がたった対象だけです。

送る画像には、左上に時刻を、追えている対象には枠と名前を書き込みます。枠の色は、その要求の対象の一覧の中の順番で決まり、見失った対象があっても同じ対象は同じ色です。その要求で box を問い直す対象の枠と名前は、最新のフレームの画像には書き込みません (古いフレームには書き込みます)。

応答の待ちは 10 秒までです。HTTP のエラー、JSON として読めない応答、確率の欠け・範囲外・数でない値は surface の失敗として数え、再試行せずに次の要求で送り直します。Box の答えの座標か確信度が 0〜1 の外か、幅か高さが 0 以下のときは、その対象の box だけを無しとして扱い、ほかの問いの答えは使います。

### run の起動引数

`switch2-layers run` は、`switch2-agent` と同じく第2章の環境を使い、キャプチャーとコントローラーのブリッジを自分で起動します。用意するものと、第2章のデーモンなどを終了しておくことは、`switch2-agent` の「必要なもの」の節と同じです。キャプチャーのフレームレートは起動引数にせず、ffmpeg.exe の出力を 30 fps、間引いた後を 10 fps に固定しています。

| 引数 | 内容 | 既定値 | 範囲 |
|---|---|---|---|
| `--runs-dir DIR` | 実行ディレクトリーを作る親 | `runs` | なし |
| `--interval-fps N` | 一定間隔のフレームを `frames/interval/` に保存するフレームレート | 5 | 0 より大きく 10 以下 |
| `--interval-width N` | 一定間隔のフレームを縮小する幅 | 640 | 64〜1280 の整数 |
| `--max-surface-errors N` | surface の失敗がこの回数続いたら止まります | 5 | 1 以上の整数 |
| `--middle-interval S` | middle の前回の応答から次を始めるまでの秒数 | 5 | 1〜10 |
| `--middle-window S` | middle に渡す一定間隔のフレームの範囲 (直近の秒数) | 10 | 0 より大きい有限の値 |
| `--max-middle-errors N` | middle の失敗がこの回数続いたら止まります | 5 | 1 以上の整数 |
| `--deep-min-start-interval S` | 同じ文書の deep を、前回の起動からこの秒数たつまで起動しません | 15 | 0 以上の有限の値 |
| `--deep-auto-seconds S` | 同じ文書の前回の deep の終了からこの秒数たったら、自動で deep を呼びます | 60 | 0 より大きい有限の値で、`--deep-min-start-interval` 以上 |
| `--deep-timeout S` | deep のジョブの実行時間の上限 (秒) | 300 | 0 より大きい有限の値 |
| `--allow-home` | 押せるボタンに HOME を足します | オフ | なし (付けるとオン) |
| `--middle {fake,luna}` | middle の実装 (「middle の実装」の節)。`luna` は gpt-6-luna を呼び、環境変数 `OPENAI_API_KEY` が要ります | `luna` | `fake`、`luna` |
| `--surface {jev,fake}` | surface の実装 (「surface の実装」の節)。`jev` は TypeSafe の Jev を呼び、環境変数 `TYPESAFE_API_KEY` が要ります | `jev` | `jev`、`fake` |
| `--threshold-advisor P` | surface の advisor 判定のしきい値。確率がこれを超えた文書の deep を求めます | 0.5 | 0〜1 |
| `--box-requery-every N` | 追えている box も、前回の問い直しからこの回数の要求がたったら Jev に問い直します | 5 | 1 以上の整数 |

範囲外の値では、実機に触れずにエラーを表示して終了します (終了コード 2)。`--middle luna` で環境変数 `OPENAI_API_KEY` が無いとき、`--surface jev` で環境変数 `TYPESAFE_API_KEY` が無いときも、範囲の確かめの後に、実機に触れず、実行ディレクトリーも作らずに終了します (終了コード 2)。`--deep-auto-seconds` が `--deep-min-start-interval` より短いと、自動の呼び出しが最小の間隔で捨てられ続けるため、エラーにしています。

同じ文書の deep のジョブは、前のジョブが実行中でも、前回の起動から `--deep-min-start-interval` 秒たっていれば並行して起動します。間隔の中に来た要求は捨て、`run.jsonl` に `deep_skipped` を書きます。起動から `--deep-timeout` 秒たったジョブには停止を求め、5 秒たっても終わらなければ強制終了します。

### 実行ディレクトリー

run は、実行ごとに `runs/<YYYYmmdd-HHMMSS>/` を作ります (同じ名前があれば、末尾に `-2` などを付けます)。中に次のファイルを書きます。時刻は、実行を始めてからの経過秒 (t) を小数第 3 位 (ミリ秒) までに丸めた値です。

| ファイル | 内容 |
|---|---|
| `run.jsonl` | 起動 (`start`。起動引数を含む)、surface と middle の失敗、deep のジョブの開始・停止の要求・終了・捨てた要求、deep を求めたときの失敗 (`deep_request_error`)、保存を捨てた画像の件数 (`frames_dropped`)、停止 (`stop`。止まった理由を含む) を、1 行 1 件の JSON で書きます。`stop` の行の後には何も書きません |
| `surface.jsonl` | surface の要求ごとの記録。送った画像の時刻とファイル名、読んだ文書の場面、送った操作、ボタンの制限で外したボタン (`blocked_buttons`)、deep を求めた文書の種類、情報ラベル (`info_labels`)、最新のフレームの box (`boxes`。対象の名前・座標・確信度・出どころ `jev` か `tracker`)、場面転換、待ち時間、失敗の説明を書きます |
| `operations.jsonl` | コントローラーに操作を送ろうとした記録。きっかけ (`apply`・`neutral`・`stop`)、操作、成否を書きます |
| `frames.jsonl` | 保存した画像の一覧。時刻、種類 (`interval`・`surface`)、ファイル名、幅と高さを、画像を書き終えた後に 1 行ずつ足します |
| `frames/interval/` | 一定間隔で保存した画像 (JPEG)。ファイル名は、時刻をミリ秒にした 9 桁の数です (例: `000012345.jpg`) |
| `frames/surface/` | surface に送った画像 (JPEG。左上に時刻を、追えている対象の枠と名前を書き込んだもの)。時刻と、書き込んだ box の内容 (対象の名前と座標) の組ごとに 1 回だけ保存します。ファイル名は、その時刻で最初に保存した画像は `frames/interval/` と同じ 9 桁の数で、同じ時刻で書き込んだ box が違う 2 つ目以降は末尾に `-2`・`-3` … を付けます (例: `000012345-2.jpg`) |
| `middle/state.json` | middle の最新の出力 (場面、詰まっているか、surface の待ち時間、系列情報、次に読む記録の位置)。surface は要求ごとにこのファイルを読みます |
| `middle/calls.jsonl` | middle の実行ごとの記録。`--middle luna` では、トークン数、概算の費用、応答の本文も書きます |
| `deep/<種類>-<連番>/` | deep のジョブごとのディレクトリー (連番は種類ごと)。`job.json` (きっかけ、時刻、対象の文書とその SHA-256) と、対象の文書のコピーを置く `work/` があります |
| `notes/` | 文書 (次の表) |

`surface.jsonl` に書く画像のファイル名は、その要求で送った画像の保存を予約したものです。書き込みに失敗したときや、終了時に書き終えられずに捨てたときは、その画像はありません。書けた画像は `frames.jsonl` に載ります。画像の保存は裏のスレッドで行い、溜めておける件数 (64 件) を超えた分は捨てて、捨てた件数を `frames_dropped` に書きます。

`notes/` には、起動時に次の文書を作ります。

| ファイル | 内容 |
|---|---|
| `notes/common/controls.md` | 共通の操作説明。押せるボタンを、まだ確かめていない印 `unknown` を付けて並べます |
| `notes/common/tips.md` | 共通の攻略情報 (見出しだけ) |
| `notes/common/box_targets.md` | surface が box で取る対象の共通の一覧。固定の 4 つ (自機・敵・敵の攻撃・目標地点) を `- 名前: 説明` の行で並べます |
| `notes/scenes/<場面ID>/` | 場面ごとの操作説明 (`controls.md`)・攻略情報 (`tips.md`)・短期のタスクリスト (`tasks.md`)・box で取る対象 (`box_targets.md`)。起動時は空で、`tasks.md` は middle が書きます。場面の操作説明・攻略情報・box で取る対象が無いか空なら、surface は共通の文書を使います |
| `notes/strategy.md` | 大方針 (見出しだけ) |
| `notes/advice.md` | 詰まったときの助言 (見出しだけ) |
| `notes/map/` | マップの文書 (起動時は空) |
| `notes/scene_labels.json` | 場面の名前と場面 ID (`scene-001` の形) の一覧。middle が初めての場面を足します |

### 押せるボタン

surface が決めた操作は、コントローラーに送る直前に、押せるボタンに制限します。既定では HOME を押せません。`--allow-home` を付けると押せます。外したボタンは `surface.jsonl` の `blocked_buttons` に書きます。十字キーの相反する方向 (UP と DOWN、LEFT と RIGHT) を同時に押す指示は、両方とも外します。

短押しは 120 ミリ秒押して離します。同じボタンを離してから 100 ミリ秒たつまでは、次の短押しを始めずに待ちます。

### 止まる条件

次のどれかが起きると、操作を中立に戻して以後の操作を送らないようにし、3 層を止めて、`run.jsonl` の `stop` に理由を書きます。

| 条件 | `stop` の理由 |
|---|---|
| Ctrl+C を押した | `interrupted` |
| キャプチャーの出力が終わった | `capture ended` |
| 最後のフレームから 2 秒以上、新しいフレームが届かない | `no new frame for 2 seconds` |
| 起動から 10 秒たっても、最初のフレームが届かない | `no first frame within 10 seconds` |
| コントローラーのブリッジが復旧できない状態 (第2章の `failed`) になった | `bridge recovery failed` |
| surface の失敗が `--max-surface-errors` 回続いた | `surface failed <回数> times in a row` |
| middle の失敗が `--max-middle-errors` 回続いた | `middle failed <回数> times in a row` |

ブリッジの復旧中 (第2章の `recovering`) は止まりません。surface は操作を中立に戻し、復旧が終わるまで要求を送りません。surface は失敗するたびに操作を中立に戻して 1 秒待ちます。middle が失敗した回は、前回の出力を残します。

止まるときは、surface と middle のループの終わりを最大 5 秒ずつ待ち、実行中の deep のジョブに停止を求めて、5 秒たっても終わらなければ強制終了します。surface と middle は、応答を受け取った時点で停止が決まっていれば、その応答を捨てます。surface は操作を送らず、どちらも記録を書きません。`run` の終了コードは、Ctrl+C で止まったときは 0、ほかの理由では 1 です。

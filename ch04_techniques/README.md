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
- 速い層 (Pipeline): 要求を 1 つずつ Decision Model に送り、応答を受け取ったら次の要求に進みます。TypeSafe Jev (`--model jev`) では、直前の要求の応答が返るか、失敗してから 0.2 秒たつまで待ち、その後に次の要求に載せるフレームを選びます。OpenAI Decisions API (`--model decisions`) では待ちません。要求には、フレームの履歴の最新の 1 枚の画像、ゲームの情報の 4 ファイルの内容、直近 5 回の応答と送った操作の履歴 (操作と、それを送った時刻。Decision Model の失敗やブリッジの復旧待ちで押下状態を中立に戻したときは、中立に戻したことと、その理由と時刻)、37 の問いを入れます。画像のラベルには、実行を始めてからの経過秒 (t) と何秒前の画像かを書きます。前回の操作 (前回の応答で決めた操作か、その後に中立に戻したこと) があれば、その前の画像か、後の画像かも書きます
- PadOutput: 速い層が決めた押下状態と短押しを合わせて、コントローラーのブリッジに送ります。短押しは 120 ミリ秒で離します
- advisor: 4 つの役割 (操作方法の更新、目標・戦略の更新、問題の解決の助言、攻略情報・Tips の更新) に分かれています
  - 役割ごとに Decision Model の問い (`advisor_<役割>`) があり、その問いが真になると、裏のスレッドでその役割の `claude -p` を実行します。同時に実行するのは役割ごとに 1 件で、役割どうしは並列に動きます (最大 4 件)。実行中の役割は、その役割の問いが真になっても呼びません。実行が失敗した役割 (`claude` を起動できない、タイムアウトした、終了コードが 0 以外など) も、失敗した実行が終わってから 60 秒は呼びません
  - 書き換えるゲームの情報のファイルは、役割ごとに 1 つに決まっています。advisor は Skill の手順で受け持ちのファイルを書き換え、速い層は次の要求からその内容を読みます
  - advisor に渡す画像は、要求とは別に選びます。意味のあるフレームの直近 5 枚と、それより後のフレームの履歴から等間隔に選んだ 5 枚で、最大 10 枚です。1 回の要求で複数の役割を呼ぶときは、同じ画像を使い回します。呼ぶ役割が無ければ、画像を選びません

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
| `--save-requests` | 要求ごとの入出力を `runs/<YYYYmmdd-HHMMSS>/requests.jsonl` と `frames/` に書きます (ログの節を参照) | オフ | なし (付けるとオン) |
| `--record-dashboard` | ダッシュボードが読む記録として、advisor の途中経過を `runs/<YYYYmmdd-HHMMSS>/advisor-<役割>-001/stream.jsonl` などに書きます (ログの節を参照) | オフ | なし (付けるとオン) |

範囲外の値や、`--process-fps` が `--ffmpeg-fps` より大きい指定では、起動せずにエラーを表示します。`--threshold-advisor` は「`advisor_<役割>` の問いの確率がこの値を超えたら真」の値で、4 つの役割で共通です。ちょうど同じ値は偽です。ボタンとスティックの問いには、しきい値はありません (問いと操作の決め方の節を参照)。`--image-width` の下限の 498 は、Jev が受け付ける画像の各辺の下限 (280 ピクセル) を 16:9 の高さで満たす幅です。

`--save-requests` を付けると、要求ごとに送った画像を保存するため、ログの容量が大きくなります。長時間の試行では付けないでください。

## ゲームの情報のファイルと Skill

ゲームの情報は、ゲームごとに次の 4 ファイルに書きます。起動時に、無いファイルは空のファイルとして作ります。既にあるファイルは変えません。

| ファイル | 内容 | 書き込む advisor の役割 | Skill |
|---|---|---|---|
| `game_notes/<ゲーム>/controls.md` | ゲームの操作方法 (ボタンの割り当て、会話の送り方、メニューの開き方など) | 操作方法の更新 (`controls`) | `record-controls` |
| `game_notes/<ゲーム>/tips.md` | 攻略情報・Tips (敵の特徴、アイテム、ゲームの仕組み、注意点など) | 攻略情報・Tips の更新 (`tips`) | `record-tips` |
| `game_notes/<ゲーム>/strategy.md` | 目標・進行状況・戦略 | 目標・戦略の更新 (`strategy`) | `record-strategy` |
| `game_notes/<ゲーム>/advice.md` | 問題の解決の助言 | 問題の解決の助言 (`advice`) | `record-advice` |

strategy.md は、目標・進行状況・戦略の 3 つの節で書きます。目標は大目標・中目標・小目標の 3 段階で、それぞれに状態 (未着手・途中・達成・不成立) を付けます。進行状況には、どこまで進んだか、今いる場所と画面の状態、画面で確かめられる値を書き、戦略には、今の小目標に向けた進め方、押すべきボタンの方針、避けるべき操作、安全のための制限を書きます。

advice.md は、速い層が先に進めなくなったときの助言です。助言が当てはまる場面、起きている問題、原因の見立て、試す手順、試して効果が無かったことを書きます。Decision Model には、助言に書かれている場面に今の画面が当てはまるときだけ参考にするよう、役割の説明で指示しています。

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
| `run.jsonl` | 1 行 1 件の JSON のログ。要求ごとの所要時間 (`select_s`・`encode_s`・`decide_s`・`apply_s`)、Jev で直前の要求の後に待った秒数 (`interval_wait_s`。待たなければ 0)、画像の枚数と大きさ、決めた操作、advisor の役割ごとの問いの確率 (`advisor_probabilities`)、advisor を求めた役割 (`advisor_requested`) と受け付けた役割 (`advisor_accepted`)、advisor の実行の結果 (役割の `role` を含む)、失敗などを書きます。同じ内容の要約を画面にも表示します |
| `advisor-<役割>-001/` など (例: `advisor-strategy-001/`) | advisor の役割ごと、1 回の実行ごとの入出力 (連番は役割ごと)。advisor 用に選んだ最大 10 枚の画像 (`frame-01.jpg` など。意味のあるフレーム、フレームの履歴から選んだものの順で、どちらも古い順)、文脈 (`context.md`。役割を含む)、結果 (`result.json`。終了コード、所要時間、`claude -p` の出力、拒否された操作) |
| `advisor-<役割>-001/stream.jsonl` など | `--record-dashboard` を付けたときだけ書く、advisor の途中経過の JSON Lines。`claude -p` を `--output-format stream-json --verbose` で動かし、標準出力の行 (ツール呼び出し、ツールの結果、最後の `result` など) が 1 行届くたびに、読んだ時刻 (`mono`。`time.monotonic()` の秒) と、その行を JSON として読んだもの (`data`) を `stream.jsonl` の 1 行に書きます。JSON として読めない行は、`data` の代わりに元の文字列を `raw` に入れます。このときの `result.json` の `claude -p` の出力は、最後に届いた `result` の行です。`stream.jsonl` を書けなかったときは、`run.jsonl` に `record_error` の行を書き、advisor の実行は続けます |
| `requests.jsonl` | `--save-requests` を付けたときだけ書く、Decision Model への要求ごとの入出力の JSON Lines。1 行目は header で、`--model` の値、役割の説明と安全のための規則、37 の問い (名前、文、種類、答えの型、選択肢の問いの選択肢) を書きます。2 行目以降は要求ごとに 1 行で、要求に載せた 1 枚の画像のラベルとファイル名、ゲームの情報の 4 ファイルの内容、直近の応答と送った操作の履歴、確率の問いの確率 (`probabilities`)、選択肢の問いの選択肢ごとの確率 (`choices`)、応答の本文、決めた操作、advisor の役割ごとの問いの確率 (`advisor_probabilities`)、advisor を求めた役割 (`advisor_requested`) と受け付けた役割 (`advisor_accepted`)、結果 (`ok`: 操作を送った、`error`: Decision Model か操作の送信の失敗、`discarded`: キャプチャーが止まったため応答を捨てた) を書きます。各行の `number` は `run.jsonl` の `decision` の `number` と同じで、2 つのログで同じ要求の行を対応づけられます |
| `frames/` | `--save-requests` を付けたときだけ書く、要求に載せた画像 (JPEG)。ファイル名は、実行を始めてからの経過時間 (マイクロ秒) です。複数の要求に載った同じフレーム (失敗した要求の後に、同じフレームで送り直したときなど) は、1 回だけ保存します |

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
- Jev の文脈長は、要求全体で 64k トークン、問い 1 つあたり 32k トークンです。画像 1 枚が何トークンになるかが公開されていないため、画像 1 枚と 37 の問いが収まるかは確かめていません。超えた場合は HTTP 400 (`max_tokens_exceeded`) になり、Decision Model の失敗として扱います
- 4 つの役割の advisor (`claude -p`) は、同時に動くことがあります (最大 4 件)。そのぶん、Claude Code の利用枠や料金を使います
- 要求と advisor に渡す画像の幅の既定値 1280 と JPEG の品質の既定値 85 は、判断の精度と要求の大きさの釣り合いを確かめる前の値です
- advisor の書き込みの許可 (`Edit(...)` のルール) では、Bash や、advisor が起動したプログラムによる書き込みは止められません。そのため Bash を許していません
- advisor はゲームの画面の画像を読むため、画面に写った文字で advisor への指示が注入されるおそれがあります。注入された指示に従うと、advisor は読んだファイルの内容を、その役割のゲームの情報のファイルに書き写せます。書き換えられるファイルは役割ごとに 1 つに限っていますが、どのファイルも、毎回の要求で Decision Model の API に送られます。そのため、許可のルールで読み取りを許すディレクトリーと書き込みを許すファイルを限り、advisor に API キーを渡していません
- Claude Code のドキュメントでは、作業ディレクトリー (`ch04_techniques`) の中のファイルの読み取りは、許可のルールが無くても承認されます。そのため advisor は、`ch04_techniques` の中のほかのファイルも読めると考えられます。実際に読める範囲は確かめていません
- `.env` を `ch04_techniques` に置くと、advisor はそのファイルも読めると考えられます。advisor の `claude` のプロセスには API キーの環境変数を渡していませんが、`.env` を読んで、画面の文字で注入された指示に従うと、API キーをゲームの情報のファイルに書き写せます。書き写された API キーは、毎回の要求で Decision Model の API に送られます。避けるには、API キーを書いたファイルを `ch04_techniques` に置かないでください。API キーは `export` で設定するか、`.env` を `ch04_techniques` の外に置いて `--env-file` にそのパスを指定します
- `--setting-sources project` で実行する人のユーザー側の設定を読まないようにしていますが、ユーザーの `~/.claude/CLAUDE.md` が読まれるかは確かめていません。advisor の動作は、実行する人の Claude Code の環境によって変わることがあります
- `--save-requests` を付けたときの `requests.jsonl` と `frames/` には、ゲームの画面の画像と、ゲームの情報の 4 ファイルの内容がそのまま残ります
- `--record-dashboard` を付けたときの `stream.jsonl` には、advisor が読んだファイルの中身 (ツールの結果) と、ゲームの情報のファイルに書いた内容がそのまま残ります。読んだ画像も、ツールの結果に入って残ると考えられます (確かめていません)。画面の文字で注入された指示に従って、advisor が `ch04_techniques` の中のほかのファイル (`.env` など) を読んだ場合は、そのファイルの中身も残ります
- 利用によって生じたアカウントの制限、データの損失、機器の故障などの不利益は、利用者の自己責任となります

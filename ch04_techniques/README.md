# 第4章 型付きの判断を返すモデルと advisor でゲームを遊ぶ

第4章のサンプルです。Nintendo Switch 2 の画面を、問いごとに真である確率を返すモデル (Decision Model) に送り続け、その確率からボタンの入力を決めて送ります。戦略は、裏で動かす headless の Claude Code (advisor) が考え、ゲームの情報のファイルに書きます。このディレクトリーの uv プロジェクトにあるパッケージ [`switch2_agent`](./src/switch2_agent/) を、コマンド `switch2-agent` で実行します。

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
                                                └─ advisor: claude -p で game_notes/<ゲーム>/*.md を書き換える
```

1 つのプロセス (`switch2-agent`) が、次の 4 つを動かします。

- キャプチャー: 第2章の Capture に、フレームの履歴を足したものです。間引いたフレームを時刻つきで持ち、鮮明度の谷で場面を区切って、場面ごとに最も鮮明なフレームを「意味のあるフレーム」として残します
- 速い層 (Pipeline): 要求を 1 つずつ Decision Model に送り、応答を受け取ったらすぐ次の要求に進みます。要求には、前回の要求から今までのフレームから等間隔に最大 5 枚と、それより前の意味のあるフレームの直近 5 枚、ゲームの情報の 4 ファイルの内容、直近 5 回の応答と送った操作の履歴 (操作と、それを送った時刻。Decision Model の失敗やブリッジの復旧待ちで押下状態を中立に戻したときは、中立に戻したことと、その理由と時刻)、43 の問いを入れます。各画像のラベルには、実行を始めてからの経過秒 (t) と何秒前の画像かを書きます。前回の要求から今までの画像のラベルには、前回の応答で決めた操作を送る前の画像か、送った後の画像かも書きます
- PadOutput: 速い層が決めた押下状態と短押しを合わせて、コントローラーのブリッジに送ります。短押しは 120 ミリ秒で離します
- advisor: 速い層の「advisor に戦略の更新を求めるべきか」の問いが真になると、裏のスレッドで `claude -p` を実行します。同時に実行するのは 1 件だけです。advisor は Skill の手順でゲームの情報の 4 ファイルを書き換え、速い層は次の要求からその内容を読みます

### 問いと操作の決め方

問いは 43 個です。どれも「真である確率」で答えさせ、しきい値を超えたら真とみなします。

| 問い | 数 | 真のときの操作 |
|---|---|---|
| `press_<ボタン>` | 17 | そのボタンを押した状態にします。次の応答まで押したままです |
| `tap_<ボタン>` | 17 | そのボタンを 1 回だけ短く押します (120 ミリ秒)。直前の応答まで押した状態だったボタンは、100 ミリ秒離してから押します |
| `stick_<LS/RS>_<方向>` | 8 | スティックを倒します。確率をそのまま傾きにし、反対の方向も真なら差を傾きにします |
| `advisor` | 1 | advisor に戦略の更新を求めます |

同じボタンの `press` と `tap` が両方とも真なら、確率の高いほうだけを採ります。十字キーの相反する方向 (UP と DOWN、LEFT と RIGHT) が両方とも採られたら、確率の高いほうを残し、同じ確率なら両方とも外します。

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

起動すると、ブリッジの起動 (COM ポートを開くまで最大 30 秒) とキャプチャーの起動の後に、速い層が要求を送り始めます。起動直後は、最初のフレームが届くまで最大 10 秒待ちます。止めるときは Ctrl+C を押します。止まるときは、押下状態を中立に戻してから、advisor、キャプチャー、ブリッジの順に止めます。実行中の advisor は止めます (`claude` のプロセスに終了を指示し、5 秒で終わらなければ強制的に終了させます)。

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
| `--history-seconds S` | フレームの履歴を持つ秒数 | 5.0 | 0 より大きい有限の値 |
| `--image-width N` | 要求に載せる画像の幅。縦横比を保って縮小します | 1280 | 498〜1280 の整数 |
| `--jpeg-quality N` | 要求に載せる画像の JPEG の品質 | 85 | 1〜100 の整数 |
| `--threshold-press P` | `press_*` の問いのしきい値 | 0.5 | 0〜1 |
| `--threshold-tap P` | `tap_*` の問いのしきい値 | 0.5 | 0〜1 |
| `--threshold-stick P` | `stick_*` の問いのしきい値 | 0.5 | 0〜1 |
| `--threshold-advisor P` | `advisor` の問いのしきい値 | 0.5 | 0〜1 |
| `--valley-ratio R` | 鮮明度が場面の山の何倍まで下がったら場面の切り替わり (谷) とみなすか | 0.5 | 0 と 1 を含まない範囲 |
| `--similarity-threshold D` | 意味のあるフレームが直前に残したものと似ているとみなす差分 (縮小したグレー画像の画素の差の平均) の上限 | 0.15 | 0〜255 |
| `--advisor-timeout S` | advisor の 1 回の実行の上限 (秒) | 300 | 0 より大きい有限の値 |
| `--max-consecutive-errors N` | Decision Model の失敗がこの回数続いたら止まります | 5 | 1 以上の整数 |
| `--save-requests` | 要求ごとの入出力を `runs/<YYYYmmdd-HHMMSS>/requests.jsonl` と `frames/` に書きます (ログの節を参照) | オフ | なし (付けるとオン) |

範囲外の値や、`--process-fps` が `--ffmpeg-fps` より大きい指定では、起動せずにエラーを表示します。しきい値は「確率がこの値を超えたら真」の値で、ちょうど同じ値は偽です。`--image-width` の下限の 498 は、Jev が受け付ける画像の各辺の下限 (280 ピクセル) を 16:9 の高さで満たす幅です。

`--save-requests` を付けると、要求ごとに送った画像を保存するため、ログの容量が大きくなります。長時間の試行では付けないでください。

## ゲームの情報のファイルと Skill

ゲームの情報は、ゲームごとに次の 4 ファイルに書きます。起動時に、無いファイルは空のファイルとして作ります。既にあるファイルは変えません。

| ファイル | 内容 | 書き込む Skill |
|---|---|---|
| `game_notes/<ゲーム>/controls.md` | ゲームの操作方法 (ボタンの割り当て、会話の送り方、メニューの開き方など) | `record-controls` |
| `game_notes/<ゲーム>/tips.md` | 攻略情報・Tips (敵の特徴、ゲームの仕組み、注意点など) | `record-tips` |
| `game_notes/<ゲーム>/progress.md` | 進行状況 (どこまで進んだか、今いる場所、持ち物、目標の達成状況) | `record-progress` |
| `game_notes/<ゲーム>/strategy.md` | 戦略 (今の目標と進め方、押すべきボタンの方針、避けるべき操作) | `record-strategy` |

速い層は、要求のたびに 4 ファイルを読み直し、内容をそのまま要求に入れます。advisor の出力 (`claude -p` の答え) は、戦略の受け渡しには使いません。人が 4 ファイルを書いておくと、最初から手がかりとして使われます。

Skill は [`.claude/skills/`](./.claude/skills/) の 4 つの `SKILL.md` です。advisor は、`record-controls`・`record-tips`・`record-progress` で必要な種別を更新し、`record-strategy` で戦略を必ず更新します。ファイルは要求に毎回入るため、戦略は 1500 字以内、ほかは 3000 字以内を目安に短く保つよう、Skill に書いています。

advisor (`claude -p`) は、次の指定で実行します。

```bash
claude -p <プロンプト> --output-format json --permission-mode dontAsk \
  --setting-sources project --settings '{"disableAllHooks": true}' \
  --allowedTools "Read(/game_notes/<ゲーム>/**),Read(/runs/<YYYYmmdd-HHMMSS>/**),Edit(/game_notes/<ゲーム>/controls.md),Edit(/game_notes/<ゲーム>/tips.md),Edit(/game_notes/<ゲーム>/progress.md),Edit(/game_notes/<ゲーム>/strategy.md),Skill(record-controls),Skill(record-tips),Skill(record-progress),Skill(record-strategy)" \
  --max-turns 30
```

許可のルールで読み取りを許すのは、`game_notes/<ゲーム>/` と、その実行の `runs/<YYYYmmdd-HHMMSS>/` の下です (ほかに読める範囲は注意事項に書きます)。書き込みを許すのは `game_notes/<ゲーム>/` の 4 ファイルだけで、Bash は許しません。確認が要る操作のうち、許可のルールに無いものは、確認を求めずに拒否されます (`--permission-mode dontAsk`)。拒否された操作は、ログの `permission_denials` に残ります。advisor の `claude` のプロセスには、Decision Model の API キーの環境変数 (`TYPESAFE_API_KEY`・`OPENAI_API_KEY`) を渡しません。

## ログ

実行ごとに `runs/<YYYYmmdd-HHMMSS>/` を作り、次のファイルを書きます。

| ファイル | 内容 |
|---|---|
| `run.jsonl` | 1 行 1 件の JSON のログ。要求ごとの所要時間 (`select_s`・`encode_s`・`decide_s`・`apply_s`)、画像の枚数と大きさ、決めた操作、advisor の呼び出し、失敗などを書きます。同じ内容の要約を画面にも表示します |
| `advisor-001/` など | advisor の 1 回の実行ごとの入出力。advisor に渡した画像 (`frame-01.jpg` など)、文脈 (`context.md`)、結果 (`result.json`。終了コード、所要時間、`claude -p` の出力、拒否された操作) |
| `requests.jsonl` | `--save-requests` を付けたときだけ書く、Decision Model への要求ごとの入出力の JSON Lines。1 行目は header で、`--model` の値、役割の説明と安全のための規則、43 の問いを書きます。2 行目以降は要求ごとに 1 行で、画像のラベルとファイル名、ゲームの情報の 4 ファイルの内容、直近の応答と送った操作の履歴、43 の問いの確率、応答の本文、決めた操作、結果 (`ok`: 操作を送った、`error`: Decision Model か操作の送信の失敗、`discarded`: キャプチャーが止まったため応答を捨てた) を書きます。各行の `number` は `run.jsonl` の `decision` の `number` と同じで、2 つのログで同じ要求の行を対応づけられます |
| `frames/` | `--save-requests` を付けたときだけ書く、要求に載せた画像 (JPEG)。ファイル名は、実行を始めてからの経過時間 (マイクロ秒) です。複数の要求に載った同じフレームは、1 回だけ保存します |

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
- Decision Model の API は、要求ごとに料金がかかります。速い層は応答を受け取るとすぐ次の要求を送るため、実行している間は要求を送り続けます
- Jev の文脈長は、要求全体で 64k トークン、問い 1 つあたり 32k トークンです。画像 1 枚が何トークンになるかが公開されていないため、画像 10 枚と 43 の問いが収まるかは確かめていません。超えた場合は HTTP 400 (`max_tokens_exceeded`) になり、Decision Model の失敗として扱います
- 画像の幅の既定値 1280 と JPEG の品質の既定値 85 は、判断の精度と要求の大きさの釣り合いを確かめる前の値です
- advisor の書き込みの許可 (`Edit(...)` のルール) では、Bash や、advisor が起動したプログラムによる書き込みは止められません。そのため Bash を許していません
- advisor はゲームの画面の画像を読むため、画面に写った文字で advisor への指示が注入されるおそれがあります。注入された指示に従うと、advisor は読んだファイルの内容をゲームの情報のファイルに書き写せます。ゲームの情報のファイルは、毎回の要求で Decision Model の API に送られます。そのため、許可のルールで読み取りを許すディレクトリーと書き込みを許すファイルを限り、advisor に API キーを渡していません
- Claude Code のドキュメントでは、作業ディレクトリー (`ch04_techniques`) の中のファイルの読み取りは、許可のルールが無くても承認されます。そのため advisor は、`ch04_techniques` の中のほかのファイルも読めると考えられます。実際に読める範囲は確かめていません
- `.env` を `ch04_techniques` に置くと、advisor はそのファイルも読めると考えられます。advisor の `claude` のプロセスには API キーの環境変数を渡していませんが、`.env` を読んで、画面の文字で注入された指示に従うと、API キーをゲームの情報のファイルに書き写せます。書き写された API キーは、毎回の要求で Decision Model の API に送られます。避けるには、API キーを書いたファイルを `ch04_techniques` に置かないでください。API キーは `export` で設定するか、`.env` を `ch04_techniques` の外に置いて `--env-file` にそのパスを指定します
- `--setting-sources project` で実行する人のユーザー側の設定を読まないようにしていますが、ユーザーの `~/.claude/CLAUDE.md` が読まれるかは確かめていません。advisor の動作は、実行する人の Claude Code の環境に左右されることがあります
- `--save-requests` を付けたときの `requests.jsonl` と `frames/` には、ゲームの画面の画像と、ゲームの情報の 4 ファイルの内容がそのまま残ります
- 利用によって生じたアカウントの制限、データの損失、機器の故障などの不利益は、利用者の自己責任となります

# run-dashboard: 第4章の試行の記録を読む API サーバー

第4章のサンプル (`switch2-agent`) が書いた試行の記録 (`ch04_techniques/runs/<YYYYmmdd-HHMMSS>/`) を読み、画面に JSON で渡す読み取り専用のサーバーです。このディレクトリーの uv プロジェクトにあるパッケージ [`run_dashboard`](./src/run_dashboard/) を、コマンド `run-dashboard` で実行します。

## 読む記録

第4章の `switch2-agent` は、起動引数 `--record-dashboard` を付けると、フレームの判定・送ったレポート・要求ごとの入出力・advisor の途中経過を記録し、`--save-requests` を付けると要求ごとの入出力と画像を記録します。記録の形は [`ch04_techniques/README.md`](../../ch04_techniques/README.md) のログの節にあります。このサーバーはそれらの記録を読み、時刻をすべて経過秒 (第4章の Pipeline の起点からの秒) にそろえて返します。

- どちらの引数も付けていない実行や、記録の形が今の第4章と違う実行も一覧に出し、無い記録は「記録なし」として返す
- 問いの値や操作に採った数などの判定と、ゲームの情報の書式は、第4章のパッケージ `switch2_agent` の関数を呼んで求める (同じ計算を書き直さない)
- 記録はリクエストのたびに読み直す。実行中の記録は、読んだ時点で書き終わっている行までを返す (改行で終わっていない最後の行は、書いている途中とみなして読まない)
- 記録には何も書かない

## 必要なもの

- [uv](https://docs.astral.sh/uv/) と Python 3.12 以上
- このリポジトリーの第4章のパッケージ (`ch04_techniques`) と第2章のパッケージ (`ch02_setup`)。どちらも path 依存で使うため、このディレクトリーをリポジトリーの中から動かさずに使う

## セットアップ

このリポジトリーの `tools/run-dashboard` ディレクトリーに移動して依存パッケージを導入します。第4章と第2章のパッケージも、path 依存で自動で導入されます。以降のコマンドも、このディレクトリーで実行します。

```bash
cd tools/run-dashboard
uv sync
```

## 起動

```bash
uv run run-dashboard
```

127.0.0.1 の 8765 番ポートで待ち受けます。待ち受けるアドレスは 127.0.0.1 だけで、変えられません。止めるときは Ctrl+C を押します。

| 引数 | 内容 | 既定値 | 範囲 |
|---|---|---|---|
| `--runs-dir DIR` | 実行の記録を置くディレクトリー (カレントディレクトリーからの相対) | `../../ch04_techniques/runs` | ディレクトリー |
| `--port N` | 待ち受けるポート | 8765 | 1〜65535 の整数 |

`--runs-dir` がディレクトリーでない (第4章のサンプルをまだ実行していないなど) ときは、説明を表示して終了します。範囲外のポートを指定したときも、起動せずにエラーを表示します。

## 画面

画面はこのサーバーには含まれません。このディレクトリーの `web/dist/` に画面のビルドの出力 (`index.html` など) があれば、`/` で配信します。`web/dist/` が無ければ API だけを動かし、`/` では画面のビルドの手順を書いた文 (text/plain) を返します。

## API の一覧

| パス | 内容 |
|---|---|
| `GET /api/runs` | 実行の一覧 (ディレクトリーの名前の新しい順) |
| `GET /api/runs/{run}` | 実行の summary (起動引数、しきい値、問いの一覧、件数、advisor の実行の一覧、ゲームの情報の版の一覧、記録の有無、読み飛ばした行の数など) |
| `GET /api/runs/{run}/requests?from=&to=` | 送信時刻が区間に入る要求の一覧 (問いの値と操作に採った数を含む) |
| `GET /api/runs/{run}/requests/{n}` | 番号 `n` の要求の詳細 (画像、所要時間、問いごとの答えと採った操作、その時点のゲームの情報の版) |
| `GET /api/runs/{run}/frames?from=&to=` | 処理したフレームの判定 (`frames.jsonl`) |
| `GET /api/runs/{run}/pad?from=&to=` | コントローラーのブリッジに送ったレポート (`pad.jsonl`) |
| `GET /api/runs/{run}/events?from=&to=` | `run.jsonl` の `decision` 以外の行 (起動、advisor の実行の結果、失敗、記録の件数など) |
| `GET /api/runs/{run}/notes/{version}` | ゲームの情報の版 (4 ファイルの中身、変わったファイルの種類、それを書き換えた advisor の実行) |
| `GET /api/runs/{run}/advisor/{name}` | advisor の実行 (結果、途中経過、ディレクトリーのファイルの一覧) |
| `GET /api/runs/{run}/files/{path}` | 実行のディレクトリーの `thumbs/`・`frames/`・`advisor-*/` の下のファイル |

応答の形 (キーと値) は、[`src/run_dashboard/run_views.py`](./src/run_dashboard/run_views.py) の各関数の docstring に書いています。

- 時刻のキー (`t`、`t_sent` のように `t_` で始まる名前、frames の `peak_t` と `candidate_t`) は、すべて経過秒 (秒、小数) で返す
- `run.jsonl` の行の経過秒は、起点の行の経過秒に、その行との壁時計 (`HH:MM:SS.mmm`) の差を足して求める。起点は、ファイルの順でその行以前にある最後の `decision` の行 (同じ number の要求が `requests.jsonl` にある行。経過秒はその要求の受信時刻) で、そうした行が無ければ `start` の行 (経過秒 0)。`start` の行より前の行の起点は `start` の行
  - 日付は記録に無いため、起点の行との差が、起点より後の行では −60 秒から 24 時間 − 60 秒まで、`start` の行より前の行では −(24 時間 − 60 秒) から +60 秒までに収まるよう、24 時間を足すか引く
  - `requests.jsonl` の無い実行では起点が `start` の行だけになるため、壁時計と monotonic の時計がずれるマシンでは、ほかの記録の時刻からずれることがある
- `from`・`to` は経過秒の区間 (両端を含む)。どちらかを省くと、その側の制限を設けない
- 知らない実行、許していないパス、読めないファイル、無い番号・版・名前は 404。`from` が `to` より大きいときと、数でない値 (`nan`・`inf` を含む) は 422

## 記録の形の違い

実行ごとに、どの記録があるかは summary の `recorded` で返します。記録の無い種類の区間 API (`requests`・`frames`・`pad`・`events`) は `{"available": false, "items": []}` を返します。

| 実行 | 扱い |
|---|---|
| `--record-dashboard` を付けた実行 | すべての API が記録を返す |
| `--save-requests` だけを付けた実行 | 要求の API は返す。フレームの判定と送ったレポートは記録なし |
| どちらも付けていない実行 | 記録は `run.jsonl` と advisor のディレクトリーだけ。要求・フレームの判定・送ったレポートは記録なし。advisor の実行の時刻は `run.jsonl` の `advisor` の行から求める |
| 問いの形が今の第4章と違う実行 | `requests.jsonl` の header の問いに `answer_type` が無い形 (ボタンの押下と短押しが別々の確率の問い) と、advisor の役割ごとの 4 問 (`advisor_controls`・`advisor_strategy`・`advisor_advice`・`advisor_tips`) がそろっていない形 (advisor の問いが `advisor` の 1 問)。一覧には出すが、問いの値・advisor の実行・ゲームの情報の版は返さない (summary の `old_format` が true。要求の API は `reason` が `old_format`、notes と advisor の API は 404)。`run.jsonl` から求めるもの (events と summary の `decide_s_median`) は返す |

JSON として読めない行は読み飛ばし、種類ごとの件数を summary の `skipped_lines` に返します。

## 開発用コマンド

`tools/run-dashboard` ディレクトリーで実行します。

```bash
uv run ruff format   # コードを整形する
uv run ruff check    # 静的解析を行う
uv run pytest        # テストを実行する (合成した記録を使い、実際の runs/ は読みません)
```

## 注意事項

- 読み取り専用で、`runs/` には何も書かない
- 要求の Host ヘッダーの名前 (ポートを除く) が `127.0.0.1` か `localhost` でなければ 400 を返す (DNS rebinding で、ほかのサイトのページから記録を読まれないようにするため)
- 認証は無い。同じマシンのほかの利用者やプログラムも 127.0.0.1 のポートに接続できる
- `runs/` の中身 (ゲームの画面の縮小画像と要求の画像、要求に入れたゲームの情報の内容、advisor の文脈 `context.md` と途中経過 `stream.jsonl`) をそのまま返す。`stream.jsonl` には、advisor が読んだファイルの中身も残る (第4章の README の注意事項)。ほかの人と共有するマシンでは、起動しないか、使い終わったらすぐに止める

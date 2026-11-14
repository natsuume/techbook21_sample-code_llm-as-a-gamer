# run-dashboard: 第4章の試行の記録を見るダッシュボード

第4章のサンプル (`switch2-agent`) が書いた試行の記録 (`ch04_techniques/runs/<YYYYmmdd-HHMMSS>/`) を、終わった実行ごとに再生して見る読み取り専用のダッシュボードです。記録を読んで JSON で返す API サーバーと、[`web/`](./web/) の画面 (Vite + React + TypeScript) からなります。サーバーは、このディレクトリーの uv プロジェクトにあるパッケージ [`run_dashboard`](./src/run_dashboard/) を、コマンド `run-dashboard` で実行します。

## 読む記録

第4章の `switch2-agent` は、起動引数 `--record-dashboard` を付けると、フレームの判定・送ったレポート・要求ごとの入出力・advisor の途中経過を記録し、`--save-requests` を付けると要求ごとの入出力と画像を記録します。記録の形は [`ch04_techniques/README.md`](../../ch04_techniques/README.md) のログの節にあります。このサーバーはそれらの記録を読み、時刻をすべて経過秒 (第4章の Pipeline の起点からの秒) にそろえて返します。

- どちらの引数も付けていない実行や、記録の形が今の第4章と違う実行も一覧に出し、無い記録は「記録なし」として返す
- 問いの値や操作に採った数などの判定と、ゲームの情報の書式は、第4章のパッケージ `switch2_agent` の関数を呼んで求める (同じ計算を書き直さない)
- JSON Lines の記録 (`*.jsonl`) は、大きさ・更新時刻・inode が前回読んだときと同じなら、そのときに読んだ結果を使う。inode が同じで大きくなったファイル (実行中に追記されて伸びた記録) は、前回読んだ位置から後だけを読み足す。それ以外で変わっていれば (別のファイルに置き換わった・小さくなったなど)、ファイルを丸ごと読み直す。記録のファイルは追記だけで伸びる前提のため、同じ inode のまま書き換えて大きくしたファイルは見分けられない。読んだ結果は、最近使ったものを優先して、元のファイルの大きさの合計で 256 MiB まで (1,024 ファイルまで) 持つ。読んだ結果のメモリーは、元のファイルの大きさの数倍になることがある
- API ごとに、応答に必要なファイルだけを読む
- 実行中の記録は、読んだ時点で書き終わっている行までを返す (改行で終わっていない最後の行は、書いている途中とみなして読まない)
- 記録のファイル (`run.jsonl`・`requests.jsonl`・`result.json` など) がシンボリックリンクなら、たどらずに「記録なし」として扱う。advisor のディレクトリーがシンボリックリンクなら、advisor の実行の一覧に入れない (実行のディレクトリーの外のファイルを返さないため。第4章はシンボリックリンクを作らない)
- 記録には何も書かない

## 必要なもの

- [uv](https://docs.astral.sh/uv/) と Python 3.12 以上
- このリポジトリーの第4章のパッケージ (`ch04_techniques`) と第2章のパッケージ (`ch02_setup`)。どちらも path 依存で使うため、このディレクトリーをリポジトリーの中から動かさずに使う
- 画面をビルドするときは、Node.js (22.12 以上の 22 系、24 系、または 26 以上) と npm。Vite と Vitest が求める版で、Node.js 24.22 で動作を確かめた

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

画面は `web/` で依存パッケージを入れてビルドし、出力 (`index.html` など) を `web/dist/` に置きます。サーバーは `web/dist/` があれば `/` で画面を配信し、無ければ API だけを動かして、`/` では画面のビルドの手順を書いた文 (text/plain) を返します。

```bash
cd web
npm ci
npm run build
```

ビルドの後に `uv run run-dashboard` を起動し、ブラウザーで `http://127.0.0.1:8765/` を開きます。上部のバーで実行を選ぶと、その実行を最初の要求から表示します。要求の記録が無い実行 (`requests.jsonl` の無い実行と、記録の形が今の第4章と違う実行) は、実行の終わりの時点を表示します。

画面は、選んだ実行を終わった実行として 1 回だけ読みます。実行中の実行を選ぶと、選んだ時点までの記録を表示し、その後に書き足された分は表示しません。書き足された分を読むには、ページを読み込み直します。

## API の一覧

| パス | 内容 |
|---|---|
| `GET /api/runs` | 実行の一覧 (ディレクトリーの名前の新しい順。実行中か (`in_progress`) を含む) |
| `GET /api/runs/{run}` | 実行の summary (起動引数、しきい値、問いの一覧、件数、advisor の実行の一覧、ゲームの情報の版の一覧、記録の有無、読み飛ばした行の数、実行中か (`in_progress`) など) |
| `GET /api/runs/{run}/requests?from=&to=` | 送信時刻が区間に入る要求の一覧 (問いの値と操作に採った数を含む) と `cursor` |
| `GET /api/runs/{run}/requests/{n}` | 番号 `n` の要求の詳細 (画像、所要時間、問いごとの答えと採った操作、その時点のゲームの情報の版) |
| `GET /api/runs/{run}/frames?from=&to=` | 処理したフレームの判定 (`frames.jsonl`) と `cursor` |
| `GET /api/runs/{run}/pad?from=&to=` | コントローラーのブリッジに送ったレポート (`pad.jsonl`) と `cursor` |
| `GET /api/runs/{run}/events?from=&to=` | `run.jsonl` の `decision` 以外の行 (起動、advisor の実行の結果、失敗、記録の件数など) と `cursor` |
| `GET /api/runs/{run}/live?run=&requests=&frames=&pad=` | 実行中の記録を追うサーバー送信イベント (SSE)。下の「ライブ (`/live`)」 |
| `GET /api/runs/{run}/notes/{version}` | ゲームの情報の版 (4 ファイルの中身、変わったファイルの種類、それを書き換えた advisor の実行) |
| `GET /api/runs/{run}/advisor/{name}` | advisor の実行 (結果、途中経過、ディレクトリーのファイルの一覧) |
| `GET /api/runs/{run}/files/{path}` | 実行のディレクトリーの `thumbs/`・`frames/`・`advisor-*/` の下のファイル |

応答の形 (キーと値) は、[`src/run_dashboard/run_views.py`](./src/run_dashboard/run_views.py) の各関数の docstring に書いています。

- `in_progress` は、`run.jsonl` に終了の記録 (`record_end` の行) が無く、かつ `run.jsonl` が 10 秒以内に更新された実行で true。`record_end` は強制終了などで書かれないことがあるため、更新が 10 秒止まった実行も実行中としない
- 区間 API (`requests`・`frames`・`pad`・`events`) の `cursor` は、その API が読んだファイル (`events` は `run.jsonl`) の、読んだ最後の完全な行 (改行で終わった行) の次のバイトの位置。`from`・`to` によらない。ファイルが無ければ 0。`start` の行が無い、または `origin_mono` が無いために使えない `frames`・`pad`・`events` も 0 (`start` の行より前に書かれた行を、`/live` が `start` の行を読んだときに送るため)。`/live` のクエリーに渡す

- 時刻のキー (`t`、`t_sent` のように `t_` で始まる名前、frames の `peak_t` と `candidate_t`) は、すべて経過秒 (秒、小数) で返す
- `run.jsonl` の行の経過秒は、起点の行の経過秒に、その行との壁時計 (`HH:MM:SS.mmm`) の差を足して求める。起点は、ファイルの順でその行以前にある最後の `decision` の行 (同じ number の要求が `requests.jsonl` にある行。経過秒はその要求の受信時刻) で、そうした行が無ければ `start` の行 (経過秒 0)。`start` の行より前の行の起点は `start` の行
  - 日付は記録に無いため、起点の行との差が、起点より後の行では −60 秒から 24 時間 − 60 秒まで、`start` の行より前の行では −(24 時間 − 60 秒) から +60 秒までに収まるよう、24 時間を足すか引く
  - 壁時計と monotonic の時計がずれるマシンでは、`run.jsonl` の行の時刻が、ほかの記録の時刻からずれることがある。`requests.jsonl` の無い実行は、起点が `start` の行だけになるため、実行が長いほど差が大きくなりうる。`requests.jsonl` のある実行でも、起点の行の後で壁時計が飛ぶと、次の起点の行までの行には、飛んだ秒数の差が残る
- `from`・`to` は経過秒の区間 (両端を含む)。どちらかを省くと、その側の制限を設けない
- 知らない実行、許していないパス、読めないファイル、無い番号・版・名前は 404。`from` が `to` より大きいときと、数でない値 (`nan`・`inf` を含む) は 422

### ライブ (`/live`)

`GET /api/runs/{run}/live` は、実行のディレクトリーを 0.25 秒ごとに読み、追記された完全な行からイベントを作って送るサーバー送信イベント (`text/event-stream`) です。各イベントは `event: <種類>` と `data: <JSON の 1 行>` で、`id:`・`retry:` は付けません。区切りの空白の無い JSON で、ASCII 以外の文字と対になっていないサロゲートは `\uXXXX` のエスケープ、有限でない数は `null` にします。

クエリーの `run`・`requests`・`frames`・`pad` は、それぞれ `run.jsonl`・`requests.jsonl`・`frames.jsonl`・`pad.jsonl` の読み始める位置 (区間 API の `cursor` の値) です。その位置より後の行から送り、省いたファイルは接続した時点の終わりから送ります。負の数と整数として読めない値は 422、知らない実行は 404 です。1 回の読み取りでは `requests.jsonl` を `run.jsonl` より先に読みます (`run.jsonl` の行の経過秒は、`decision` の行と同じ番号の要求の受信時刻を起点にするため)。

| event | data | いつ送るか |
|---|---|---|
| `request` | 要求の区間 API の 1 件 | 送る `requests.jsonl` の要求の行ごと (`requests.jsonl` の無い実行と、問いの形が今の第4章と違う実行では送らない) |
| `run` | events の区間 API の 1 件 | 送る `run.jsonl` の行のうち、`decision` でなく経過秒の分かる行ごと |
| `frame` | frames の区間 API の 1 件 | 送る `frames.jsonl` の行ごと (`start` の行に `origin_mono` が無ければ送らない) |
| `pad` | pad の区間 API の 1 件 | 送る `pad.jsonl` の行ごと (同上) |
| `advisor_step` | advisor の実行の API の `steps` の 1 件に、advisor の実行の名前 `name` を足したもの | 接続した後に追記された `stream.jsonl` の行の途中経過ごと (接続した後にできた advisor のディレクトリーも追う) |
| `advisor_run` | summary の `advisor_runs` の 1 件 | 接続した直後にすべて。以後は、増えた実行と、値が変わった実行 (終わって `result.json` ができたときなど) |
| `note_version` | summary の `note_versions` の 1 件 | 接続した直後にすべて。以後は、増えた版と、値が変わった版 |
| `stats` | `{"t_end", "counts", "decide_s_median"}` (summary の同名の値) | 接続した直後と、値が変わったとき |
| `end` | `{"record_end": run.jsonl に record_end の行があるか}` | 実行中でなくなった読み取りの最後。送ったら接続を閉じる |

- `request`・`run`・`frame`・`pad`・`advisor_step` は、`start` の行を読んだ後に送る。`start` の行より前に書かれた行は、`start` の行を読んだときにファイルの順に送る
- `advisor_step` の `kind` は送った時点の値で、後で `permission_denied` に変わっても送り直さない (advisor の実行の API を読み直すと、変わった値になる)
- 問いの形が今の第4章と違う実行では、`request`・`advisor_step`・`advisor_run`・`note_version` を送らない (`run`・`stats`・`end` は送る)
- 接続した時点で実行中でなければ、位置より後の行と、`advisor_run`・`note_version` のすべてと `stats` を送ってから、すぐに `end` を送る
- イベントが 15 秒無いときは、途中の経路で接続が切られないよう、コメントの行 (`: ping`) を送る (FastAPI の SSE の機能)
- 接続ごとに、接続した時点で記録のファイルを先頭から読み直す (キャッシュを使わない)。クライアントが接続を切ると読むのをやめる

## 記録の形の違い

実行ごとに、どの記録があるかは summary の `recorded` で返します。記録の無い種類の区間 API (`requests`・`frames`・`pad`・`events`) は `{"available": false, "items": [], "cursor": 0}` を返します (`cursor` の規則は「API の一覧」の節)。

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

画面のコマンドは `web` ディレクトリーで実行します。

```bash
npm run dev      # 開発用のサーバーを起動する (既定は http://localhost:5173/。使用中なら別のポートになるので、表示された URL を開く。/api の要求は 127.0.0.1:8765 へ中継する)
npm test         # 純粋な関数 (区間・集計・差分・操作の文の解釈など) のテストを実行する
npm run check    # Biome で整形と静的解析を確かめる
npm run format   # Biome で整形する
npm run build    # 型検査をして web/dist/ に出力する
```

`npm run dev` を使うときは、別の端末で `uv run run-dashboard` を起動しておきます。

## 注意事項

- 読み取り専用で、`runs/` には何も書かない
- 要求の Host ヘッダーの名前 (ポートを除く) が `127.0.0.1` か `localhost` でなければ 400 を返す (DNS rebinding で、ほかのサイトのページから記録を読まれないようにするため)
- 認証は無い。同じマシンのほかの利用者やプログラムも 127.0.0.1 のポートに接続できる
- `runs/` の中身 (ゲームの画面の縮小画像と要求の画像、要求に入れたゲームの情報の内容、advisor の文脈 `context.md` と途中経過 `stream.jsonl`) をそのまま返す。`stream.jsonl` には、advisor が読んだファイルの中身も残る (第4章の README の注意事項)。ほかの人と共有するマシンでは、起動しないか、使い終わったらすぐに止める

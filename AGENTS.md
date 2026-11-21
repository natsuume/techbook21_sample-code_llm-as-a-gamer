# エージェント向けの案内

このリポジトリーで作業するコーディングエージェント (Codex など) への案内です。各章の詳しい説明は、章のディレクトリーの README にあります。ゲームをプレイするエージェント (`switch2-layers` と `switch2-harness` が起動する Codex) には、このファイルを読ませていません。

## 実機でプレイさせる (`switch2-layers run`)

人から「委任型で〇〇をプレイして」のように頼まれたときの手順です。Switch 2・キャプチャーデバイス・Pico 2 W を使います。

### 起動の前に確かめること

- 実機を使えるのは、一度に 1 つだけです。`pgrep -af '[s]witch2-layers run|[s]witch2-agent|[s]witch2-remote-daemon'` で、ほかの実行と第2章のデーモンが動いていないことを確かめます (1 文字目を `[]` で囲むのは、このコマンドを包んだシェル自身に当たらないようにするためです)。動いていたら、止めてよいかを人に確かめます。
- COM ポートとデバイスのフレームレートは、この PC に合わせた値が `ch04_techniques/.env` の `SWITCH2_COM_PORT`・`SWITCH2_DEVICE_FPS` に書いてあるものとして扱います。起動引数 `--com-port`・`--device-fps` は付けません (付けると `.env` の値より優先されます)。`.env` は API キーを含むので開きません。
- 遊ぶソフトの名前と Switch 2 のユーザーの名前は、人の依頼から決めます。依頼に無ければ、直近の `ch04_techniques/runs/*/orchestrator/instruction.md` に書かれた初期指示を参考にし、それでも決まらなければ人に確かめます。

### 起動する

`ch04_techniques` を作業ディレクトリーにして、次の形のコマンドを 1 つだけ実行します。

```bash
uv run --env-file .env switch2-layers run --mode delegate --agent codex --instruction '<ソフトの名前> を、ユーザー「<ユーザーの名前>」で起動して、ゲームを進めてください。ユーザー選択の画面では、必ず「<ユーザーの名前>」を選んでください。セーブデータがあれば続きから遊び、セーブデータを消したり、上書きして新しく始めたりしないでください。'
```

- `--mode delegate` は委任型、`--mode orchestrator` は監視型です。主ループのエージェントは `--agent codex` か `--agent claude` で選びます。
- `cd ch04_techniques && ...` のようにほかのコマンドとつながず、`--no-sync` も付けません。このコマンドの形 (`uv run --env-file .env switch2-layers run` で始まる形) に合わせて、承認の規則を置いている場合があるためです。
- 実機・ネットワーク・Windows の連携を使うため、サンドボックスの中では動きません。サンドボックスの外での実行を要求します。
- 止めるまで終わらないコマンドです。端末 (tty) のセッションで起動し、終わるのを待たずに、下の記録で様子を確かめます。

### 様子を確かめる

実行ディレクトリーは `ch04_techniques/runs/<日時>/` です (リポジトリーのルートから `ls -t ch04_techniques/runs | head -1` で、いちばん新しいものが分かります)。

| ファイル | 分かること |
|---|---|
| `orchestrator/turns.jsonl` | 主ループのターンごとの応答 (`response_json`) と、操作を実行したか (`outcome`) |
| `run.jsonl` | 委任の始まりと終わり (`delegate_start`・`delegate_end`) と、止まった理由 (`stop`) |
| `frames/latest/1.jpg` | 最新の画面 |

### 止める

起動した端末に Ctrl+C (SIGINT) を送ります。端末に送れないときは、`pgrep -af '[b]in/switch2-layers run'` で Python のプロセスの PID を調べ、`kill -INT <PID>` をサンドボックスの外で実行します (`uv run` のプロセスにも送ると、SIGINT が 2 回届くことがあるため、Python のプロセスだけに送ります)。SIGINT で止めると、押したままの入力を離してから終わります (終了コード 0)。

### 止まったときの表示と原因

| 表示 | 考えられる原因 | 対処 |
|---|---|---|
| `controller bridge failed: ...` (例: `neutral report failed: ... 書き込み操作がタイムアウトしました`) | COM ポートが Pico 2 W の発信側でない、または Pico 2 W がつながっていない | `SWITCH2_COM_PORT` の値を人に確かめます。COM ポートの番号の調べ方は、第2章の README の「2. Windows とのペアリング」にあります |
| `stopped: capture ended`・`no first frame within 10 seconds` | PC との USB の接続で、`SWITCH2_DEVICE_FPS` のフレームレートを受け取れない。または、ほかのソフト (OBS など) がキャプチャーデバイスを使っている | `SWITCH2_DEVICE_FPS` の値 (30 か 60) を人に確かめます |
| `SWITCH2_COM_PORT must be ...`・`SWITCH2_DEVICE_FPS must be ...` | `.env` の値の形が誤っている | 正しい値を人に書いてもらいます |

## 第2章のデーモンを使うもの

`switch2-harness` や第2章のクライアントは、第2章のデーモンを使います。デーモンは `ch02_setup` を作業ディレクトリーにして、`uv run --env-file ../ch04_techniques/.env switch2-remote-daemon` で起動します。デーモンと `switch2-layers run` を同時に動かすことはできません。

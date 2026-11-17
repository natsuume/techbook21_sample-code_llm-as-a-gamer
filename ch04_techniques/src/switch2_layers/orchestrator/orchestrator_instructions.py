"""主ループの Claude Code に --append-system-prompt で渡す変わらない指示の文と、引き継ぎの依頼の文。

変わらない指示は 2 つの版を持つ: --mode orchestrator の監視型の版 (orchestrator_instructions) と、--mode delegate の
委任型の版 (delegate_instructions)。引き継ぎの依頼の文 (handoff_request_text) は両方で同じものを使う。
どの文にもゲーム名や作品に固有の名前を書かない (ゲーム名を渡すのは、人の初期指示に書かれている場合だけ)。
"""

from switch2_remote.controller_state import BUTTON_ORDER

from switch2_layers.hardware.pad_output import MAX_HOLD_SECONDS
from switch2_layers.orchestrator.action_settle import ACTIONS_SETTLE_S
from switch2_layers.safety.safety_rules import SAFETY_RULES

# Claude が引き継ぎを書く、notes/ からの相対パス
HANDOFF_NOTE = "handoff.md"
# コードの手順 (tap_repeat・walk) の count の上限。操作の実行の部品が、これを超える count を JSON の誤りにする
PROCEDURE_MAX_COUNT = 100

# 答えの JSON の例 (監視の条件の文は、画面の見た目だけで書く)
DECISION_EXAMPLE = """\
```json
{
  "actions": [
    {"type": "press", "buttons": ["A"]},
    {"type": "press", "buttons": ["B", "LEFT"], "seconds": 0.5},
    {"type": "press", "sticks": {"left": {"x": 0.0, "y": 1.0}}, "seconds": 1.2},
    {"type": "wait", "seconds": 0.3},
    {"type": "procedure", "name": "walk", "args": {"direction": "UP", "count": 3, "seconds": 0.2, "interval_seconds": 0.1}},
    {"type": "macro", "name": "next_message"}
  ],
  "macros": {
    "set": {"next_message": [{"type": "press", "buttons": ["A"]}, {"type": "wait", "seconds": 0.5}]},
    "delete": ["old_name"]
  },
  "monitor": {
    "action": "create",
    "name": "dialog",
    "cases": [
      {"condition": "画面の下に会話の枠があり、右下に▼が出ている", "actions": [{"type": "press", "buttons": ["A"]}], "repeat": "every"},
      {"condition": "はい・いいえの選択肢が出ている", "actions": [{"type": "press", "buttons": ["B"]}], "repeat": "once"}
    ]
  }
}
```"""


def orchestrator_instructions() -> str:
    """変わらない指示の文を返す。次の 9 つをすべて書く:

    1. 役割: 送られた画面の画像と出来事だけを見て、ゲームを進める。毎ターン、テキストと、最新の 5 枚のフレームの画像
       (古い順。それぞれの前に時刻のラベル) が届くこと
    2. JSON の形: 毎ターンの応答の最後に ```json のブロックを 1 つ書き、コードはそのブロックだけを読む。
       最上位のキーは actions・macros・monitor (どれも省ける。ほかのキーは誤り)。操作は type が press・wait・
       procedure・macro の 4 種類。press の buttons に書けるボタンの名前 (switch2_remote.controller_state.BUTTON_ORDER
       のすべて)、1 つの press に十字キー (UP・RIGHT・DOWN・LEFT) は 1 つまで、sticks のキー left・right と x・y
       (-1〜1、長さ 1 以下)、seconds (省くと短押し。0 より大きく hardware.pad_output.MAX_HOLD_SECONDS 以下)。
       macros の set と delete。monitor の create (name・cases。条件は 1〜8 個で、それぞれ condition・actions・
       repeat (every か once))・stop・null。押せないボタンは実行のときに外れて、次の入力で知らされること。
       JSON に誤りがあれば、操作・マクロ・監視のどれも変わらず、誤りの説明が次の入力で届くこと
    3. コードの手順 (tap_repeat: button・count・interval_seconds、walk: direction・count・seconds・
       interval_seconds。どちらも count は 1 以上 PROCEDURE_MAX_COUNT 以下の整数で、超えると JSON の誤り)・マクロ
       (実行ディレクトリーの間残る。マクロの中から macro は呼べない)・監視 (同時に 1 つ。
       条件に当たると操作を実行する)・スキル (Skill のツールで .claude/skills/ のスキルを使う) の使い方。
       Claude の操作 (actions) の実行中は、監視が止まること
    4. 判断の続け方: 答えの actions が空でなければ、その操作の列がすべて終わり、最後にボタンとスティックを離してから
       action_settle.ACTIONS_SETTLE_S 秒 (0.3 秒) より後に撮った画面がそろってから、その画面を含む次の画面と出来事が
       届くこと (押す操作の無い actions なら、列が終わってから)。次の JSON の actions は、そのときに実行中の操作の列
       (監視の操作の列など) の残りを捨てて置き換えること。空の actions は「何もしない」の意味で、次の画面と出来事は
       すぐに届くこと (すぐ届くと書くのは、空の actions のときだけ)。監視に任せて待つときは、空の actions で答えること
    5. safety.safety_rules.SAFETY_RULES (文をそのまま入れる)
    6. 監視の条件の文は画面の見た目だけで書き、ゲーム名や作品に固有の名前を書かないこと
    7. notes/ に、分かったこと (操作の割り当て、攻略の情報、方針) を書くこと
    8. ゲームの内容の手順を、notes/skills/game-<名前>/SKILL.md にスキル (frontmatter に name と description) として
       書いてよいこと。コードが .claude/skills/ に写して読ませるため、書いたそのターンには使えないこと。
       コードが写すのは SKILL.md だけで、frontmatter の欄は name (ディレクトリーの名前と同じ) と description の
       1 行ずつだけ、本文にシェルのコマンドを実行する書き方を書かないこと (skill_copy.check_game_skill_text の規則)。
       frontmatter は `name: game-<名前>` と `description: "<どの場面で使うか>"` の 2 行で書くこと。
       守らないスキルは写されず、次の入力の「写さなかったスキル」の節で理由が届くこと
    9. フレームの読み方: frames/latest/1.jpg〜5.jpg は最新の 5 枚で、1.jpg が最新であること。いつ Read しても、
       その時点の最新の画面が読めること。それより古いフレームは frames/interval/ にあること (ファイル名は時刻の
       ミリ秒)
    """
    buttons = "・".join(BUTTON_ORDER)
    max_hold = f"{MAX_HOLD_SECONDS:g}"
    settle = f"{ACTIONS_SETTLE_S:g}"
    return f"""\
## 役割
あなたは、送られた画面の画像と出来事だけを見てゲームを進める主ループです。
毎ターン、テキストと、最新の 5 枚のフレームの画像 (古い順で、それぞれの前に時刻のラベル) が届きます。画像を見て次に何をするかを決め、操作を JSON で書きます。

## 答えの形
毎ターンの答えの最後に、```json のブロックを 1 つ書きます。コードは最後の ```json のブロックだけを読みます。
最上位のキーは "actions"・"macros"・"monitor" の 3 つで、どれも省けます。ほかのキーを書くと誤りになります。
例:
{DECISION_EXAMPLE}

操作 ("actions" と、マクロと監視の操作の列の要素) は "type" で 4 種類に分かれます。
- "press": "buttons" に押すボタンの名前を並べ、"sticks" の "left" か "right" に {{"x": 横, "y": 縦}} を書きます。x と y は -1 から 1 で、(x, y) の長さは 1 以下にします。"buttons" と "sticks" の少なくとも一方を書きます。
- "press" の "seconds" を省くと短押しになり、書くとその秒数だけ押し続けます。"seconds" は 0 より大きく {max_hold} 以下です。
- 1 つの "press" に書ける十字キー (UP・RIGHT・DOWN・LEFT) は 1 つまでです。
- ボタンの名前: {buttons}
- "wait": 何も押さずに "seconds" 秒待ちます。
- "procedure": コードの手順を "name" と "args" で呼びます。
- "macro": 登録したマクロを "name" で呼びます。
押せないボタンは実行のときに外れ、次の入力で知らされます。
JSON に誤りがあると、操作・マクロ・監視のどれも変わらず、次の入力の「前の JSON の誤り」の節に説明が届きます。

## コードの手順
- tap_repeat: "button" の短押しと "interval_seconds" 秒の待ちを "count" 回繰り返します。
- walk: "direction" (UP・RIGHT・DOWN・LEFT) を "seconds" 秒押し、"interval_seconds" 秒待つことを "count" 回繰り返します。
どちらの手順も、count は 1 以上 {PROCEDURE_MAX_COUNT} 以下の整数です ({PROCEDURE_MAX_COUNT} を超えると JSON の誤りになります)。interval_seconds は 0 以上 60 以下です。

## マクロ
"macros" の "set" に {{名前: 操作の列}} を書くとマクロを登録し、同じ名前があれば置き換えます。"delete" に名前を並べると消します。
マクロは実行が終わるまで残り、セッションが入れ替わっても使えます。マクロの中から "macro" は呼べません。今のマクロは毎ターンの入力に載ります。

## 監視
監視は、画面が条件に当たったときに決めた操作を実行する仕組みで、同時に 1 つだけ動きます。
"monitor" に {{"action": "create", "name": 名前, "cases": [...]}} を書くと、今の監視を止めてから新しい監視を作ります。
条件は 1 個から 8 個までで、それぞれに "condition" (条件の文)、"actions" (当たったときの操作の列)、"repeat" を書きます。"repeat" は、当たるたびに実行するなら "every"、1 回実行したら監視を終えるなら "once" です。
{{"action": "stop"}} で今の監視を止めます。"monitor" を省くか null にすると、監視は変わりません。
あなたが書いた "actions" の実行中は、監視は止まり、判定も操作もしません。
監視の条件の文は、画面の見た目だけで書きます。
条件の文には、ゲーム名や作品に固有の名前 (登場人物・地名など) を書きません。
条件に当たった回数と、監視の終わりは、次の入力の「前の判断の後の出来事」の節に載ります。

## 判断の続け方
答えの "actions" が空でなければ、その操作の列がすべて終わり、最後にボタンとスティックを離してから {settle} 秒より後に撮った画面がそろってから、その画面を含む次の画面と出来事が届きます。
押す操作の無い "actions" ("wait" だけ) なら、列が終わってから次の画面と出来事が届きます。
次の JSON の "actions" は、そのときに実行中の操作の列 (監視の操作の列など) の残りを捨てて置き換えます。
空の "actions" は「何もしない」の意味で、次の画面と出来事はすぐに届きます。
監視に任せて待つときは、空の "actions" で答えます。

## 安全
{SAFETY_RULES}
## notes/ に書くこと
notes/ に、分かったこと (ボタンなどの操作の割り当て、攻略の情報、方針) を Write と Edit で書きます。
notes/ はセッションが入れ替わっても残り、新しいセッションの最初の入力に notes/ のファイルの一覧が載ります。

## スキル
.claude/skills/ のスキルは、Skill のツールで使えます。本体の操作 (ユーザーの選択・ソフトの起動など) のスキルがあります。
ゲームの内容の手順は、notes/skills/game-<名前>/SKILL.md にスキルとして書いてかまいません。
コードが SKILL.md を .claude/skills/ に写して読ませるため、書いたそのターンには使えないことに注意します。
コードが写すのは SKILL.md だけで、frontmatter の欄は name と description の 2 つだけを 1 行ずつ書きます。
frontmatter は name: game-<名前> (ディレクトリーの名前と同じ) と description: "<どの場面で使うか>" の 2 行で書きます。
<名前> は英小文字・数字・ハイフンだけで書き、description の値は " で囲み、中に " と \\ を入れません。
本文に、シェルのコマンドを実行する書き方 (!` と ```!) を書きません。
決まりを守らないスキルは写されず、次の入力の「写さなかったスキル」の節に理由が届きます。

## 古いフレーム
frames/latest/1.jpg〜5.jpg は最新の 5 枚のフレームで、1.jpg が最新です。いつ Read しても、その時点の最新の画面が読めます。
それより古いフレームは frames/interval/ にあり、ファイル名は時刻のミリ秒です。
"""


def delegate_instructions() -> str:
    """--mode delegate の変わらない指示の文を返す。次の 8 つをすべて書く:

    1. 役割: orchestrator_instructions の 1 に加えて、ターンベースの場面は自分で操作し、ゲームの中の誘導の文・会話・
       出来事・助言を、委任のタスクと注意事項にまとめること。リアルタイムの場面では、意味のある行動の選択肢とタスク・
       注意事項を書いて、操作を Jev に委任すること
    2. JSON の形: 最上位のキーは actions・macros・delegate の 3 つ (どれも省ける。ほかのキーは誤りで、monitor も誤り)。
       操作・コードの手順・マクロの書き方と、押せるボタンの名前 (switch2_remote.controller_state.BUTTON_ORDER の
       すべて) は orchestrator_instructions の 2・3 と同じ。JSON に誤りがあれば、操作・マクロ・委任のどれも変わらず、
       誤りの説明が次の入力で届くこと
    3. delegate の 3 つの形と値の規則:
       start ({"action": "start", "tasks", "cautions", "choices"}。choices は {"name": マクロの名前, "description":
       意味のある行動の説明} の配列) は、同じ JSON の actions がすべて終わってから委任を始めること (actions が空なら
       すぐ)。start で省いたキーは前の委任の値を使い、前の委任が無いのに省くと誤りであること。委任中の start は誤りで、
       委任中の変更は update で行うこと。update で省いたキーは変わらないこと。stop ({"action": "stop"}) は委任を終えて
       操作権を戻すこと。答えを書く間に委任が終わっていたときの update と stop は誤りにならず、delegate だけが
       当たらないこと (同じ JSON の actions と macros は当たり、当たらなかったことは次の入力の出来事で届く)。
       choices は 1〜decision_parser.MAX_DELEGATE_CHOICES 個で、name は登録したマクロ、description は空でない文、同じ
       name は 2 回書けない、HOME を押すマクロは使えない。tasks は 1〜MAX_DELEGATE_TASKS 個、cautions は
       0〜MAX_DELEGATE_CAUTIONS 個の空でない文。委任中の choices に使っているマクロは macros の delete で消せないこと
    4. 委任の動き: Jev は choices から行動を 1 つ選び、選んだ行動のマクロを、実行中の行動の残りを捨てて始めること。
       Jev が「操作を返す」を続けて delegate_loop.RETURN_CONTROL_STREAK 回選ぶか、Jev の失敗が続くか、stop を書くと、
       操作権が戻り、次の入力の出来事に理由が届くこと。委任中も毎ターンの入力が届き、委任の節に、委任中かどうか・
       委任を始めた時刻・今のタスク・注意事項・行動・Jev が直近に選んだ行動とその時刻・「操作を返す」を続けて選んだ
       回数が載ること
    5. 委任中は、答えの最上位の actions が捨てられ、次の入力の出来事に知らされること。取り返すときは
       {"action": "stop"} を書くこと
    6. タスクと注意事項の書き方: tasks の最初に、その場面で最も重要なこと (例: 自分の HP を 0 にせずに、敵をすべて
       倒す) を書くこと。敵の攻撃を避けることが要る場面では、そのことを tasks に書くこと。敵の攻撃の動きを観測できたら、
       cautions に書くこと。タスク・注意事項・行動の説明には、ゲーム名や作品に固有の名前 (登場人物・地名など) を
       書かないこと
    7. 判断の続け方: orchestrator_instructions の 4 と同じ (監視の代わりに、委任中は actions が無いので次の画面と
       出来事がすぐ届く)
    8. safety.safety_rules.SAFETY_RULES (文をそのまま入れる)、notes/ に書くこと、スキル、古いフレームの読み方
       (orchestrator_instructions の 5・7・8・9 と同じ)
    """
    raise NotImplementedError("delegate_instructions")


def handoff_request_text() -> str:
    """セッションを入れ替える前に送る、引き継ぎの依頼のテキストを返す。

    notes/<HANDOFF_NOTE> に、今の目標・分かったこと・試して効かなかったこと・今の監視とマクロの意図を書き、
    actions の無い JSON (```json のブロック) で答えるよう頼む文。
    """
    return (
        "使ったトークンが多くなったため、このセッションを終えて新しいセッションに入れ替えます。"
        f"notes/{HANDOFF_NOTE} に、次のセッションへの引き継ぎを書いてください。\n"
        "書くこと: 今の目標、分かったこと、試して効かなかったこと、今の監視とマクロの意図。\n"
        "書き終えたら、actions の無い JSON (```json のブロックに {}) で答えてください。"
        "今の操作と監視は、入れ替えの間もそのまま続きます。"
    )

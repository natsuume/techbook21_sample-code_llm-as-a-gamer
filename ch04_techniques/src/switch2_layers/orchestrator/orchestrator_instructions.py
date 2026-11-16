"""主ループの Claude Code に --append-system-prompt で渡す変わらない指示の文と、引き継ぎの依頼の文。

どちらの文にもゲーム名や作品に固有の名前を書かない (ゲーム名を渡すのは、人の初期指示に書かれている場合だけ)。
"""

# Claude が引き継ぎを書く、notes/ からの相対パス
HANDOFF_NOTE = "handoff.md"
# コードの手順 (tap_repeat・walk) の count の上限。操作の実行の部品が、これを超える count を JSON の誤りにする
PROCEDURE_MAX_COUNT = 100


def orchestrator_instructions() -> str:
    """変わらない指示の文を返す。次の 9 つをすべて書く:

    1. 役割: 送られた画面の画像と出来事だけを見て、ゲームを進める
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
    4. 判断は休まず続き、次の JSON の actions は前の actions の残りを捨てて置き換えること。空の actions なら前の
       操作が続くこと。操作の実行中で続けさせたいときや、監視に任せて待つときは、空の actions で答えること
    5. safety.safety_rules.SAFETY_RULES (文をそのまま入れる)
    6. 監視の条件の文は画面の見た目だけで書き、ゲーム名や作品に固有の名前を書かないこと
    7. notes/ に、分かったこと (操作の割り当て、攻略の情報、方針) を書くこと
    8. ゲームの内容の手順を、notes/skills/game-<名前>/SKILL.md にスキル (frontmatter に name と description) として
       書いてよいこと。コードが .claude/skills/ に写して読ませるため、書いたそのターンには使えないこと。
       コードが写すのは SKILL.md だけで、frontmatter の欄は name (ディレクトリーの名前と同じ) と description の
       1 行ずつだけ、本文にシェルのコマンドを実行する書き方を書かないこと (skill_copy.check_game_skill_text の規則)。
       守らないスキルは写されず、次の入力の「写さなかったスキル」の節で理由が届くこと
    9. 古いフレームは frames/interval/ から必要なときだけ読むこと (読んだ画像もコンテキストに残るため)
    """
    raise NotImplementedError


def handoff_request_text() -> str:
    """セッションを入れ替える前に送る、引き継ぎの依頼のテキストを返す。

    notes/<HANDOFF_NOTE> に、今の目標・分かったこと・試して効かなかったこと・今の監視とマクロの意図を書き、
    actions の無い JSON (```json のブロック) で答えるよう頼む文。
    """
    raise NotImplementedError

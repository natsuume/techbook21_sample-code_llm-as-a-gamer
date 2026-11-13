"""advisor の役割の一覧。

advisor は役割ごとに分かれ、役割ごとに次のものを持つ:
    - きっかけになる Decision Model の問い (questions.build_questions の advisor の問い)
    - claude -p のプロセス (advisors.claude_code_headless.ClaudeCodeHeadlessAdvisor のインスタンス。
      役割ごとに同時に 1 件まで。役割どうしは並列に動いてよい)
    - 書き込めるゲームの情報のファイル 1 つと、使える Skill 1 つ
ほかのモジュールに依存しない (questions・game_notes・advisors から読まれるため)。
"""

from dataclasses import dataclass

CONTROLS_QUESTION_TEXT = (
    "今、advisor (操作方法をまとめる上位のモデル) に、操作方法のファイルの更新を求めるべきか。"
    "操作方法がまだ書かれていないとき、「(一般)」の行のように、このゲームでまだ確かめていない操作を試して"
    "画面の反応が分かったとき、書かれている操作と画面の反応が合わないとき、"
    "新しい種類の操作 (メニュー、会話、乗り物など) が画面に出たときに真にする。"
)
STRATEGY_QUESTION_TEXT = (
    "今、advisor (目標と戦略を考える上位のモデル) に、目標と戦略の更新を求めるべきか。"
    "目標や戦略がまだ書かれていないとき、今の目標を達成したとき、今の目標が達成できないと分かったとき、"
    "新しい場面に入って今の戦略が画面の状況に合わないときに真にする。"
)
ADVICE_QUESTION_TEXT = (
    "今、advisor (問題の解決を助ける上位のモデル) に、助言を求めるべきか。"
    "同じ操作や同じ失敗を繰り返して先に進めないとき、何をすればよいか分からないときに真にする。"
)
TIPS_QUESTION_TEXT = (
    "今、advisor (ゲームの情報をまとめる上位のモデル) に、攻略情報・Tips のファイルの更新を求めるべきか。"
    "敵、アイテム、ゲームの仕組み、注意点など、まだ書かれていないゲームの情報が画面に出たときに真にする。"
)
CONTROLS_TASK = (
    "ここまでの画面と操作の記録から、ゲームの操作方法 (ボタンの割り当て、会話の送り方、"
    "メニューの開き方など) を確かめ、操作方法のファイルを更新する。"
)
STRATEGY_TASK = (
    "今の目標 (大目標・中目標・小目標) が達成されたか、達成できないと分かったかを判断して記録し、"
    "次の目標と、目標に向けた進め方 (戦略) と進行状況を更新する。"
)
ADVICE_TASK = (
    "速い層が先に進めなくなっている原因を画面と操作の記録から考え、"
    "問題を解決するための具体的な手順を助言のファイルに書く。"
)
TIPS_TASK = (
    "画面に出たゲームの情報 (敵の特徴、アイテム、ゲームの仕組み、注意点など) を、"
    "攻略情報・Tips のファイルに記録する。"
)


@dataclass(frozen=True)
class AdvisorRole:
    """advisor の役割 1 つ。

    name            役割の名前 (英小文字)。Pipeline の advisors と、ログの advisor の項目のキーにも使う
    question_name   きっかけになる Decision Model の問いの name (`advisor_<name>`)
    question_text   その問いの文
    note_kind       この役割が書き込めるゲームの情報の種別 (game_notes.NOTE_KINDS の要素)
    skill           この役割が使える Skill の名前 (.claude/skills/<skill>/SKILL.md)
    title           日本語の役割名 (advisor への指示と context.md に書く)
    task            advisor への指示の中で、役割を説明する文
    """

    name: str
    question_name: str
    question_text: str
    note_kind: str
    skill: str
    title: str
    task: str


# advisor の役割。並びは問いの順と、ログの advisor の項目の順でもある
ADVISOR_ROLES = (
    AdvisorRole(
        name="controls",
        question_name="advisor_controls",
        question_text=CONTROLS_QUESTION_TEXT,
        note_kind="controls",
        skill="record-controls",
        title="操作方法の更新",
        task=CONTROLS_TASK,
    ),
    AdvisorRole(
        name="strategy",
        question_name="advisor_strategy",
        question_text=STRATEGY_QUESTION_TEXT,
        note_kind="strategy",
        skill="record-strategy",
        title="目標・戦略の更新",
        task=STRATEGY_TASK,
    ),
    AdvisorRole(
        name="advice",
        question_name="advisor_advice",
        question_text=ADVICE_QUESTION_TEXT,
        note_kind="advice",
        skill="record-advice",
        title="問題の解決の助言",
        task=ADVICE_TASK,
    ),
    AdvisorRole(
        name="tips",
        question_name="advisor_tips",
        question_text=TIPS_QUESTION_TEXT,
        note_kind="tips",
        skill="record-tips",
        title="攻略情報・Tips の更新",
        task=TIPS_TASK,
    ),
)

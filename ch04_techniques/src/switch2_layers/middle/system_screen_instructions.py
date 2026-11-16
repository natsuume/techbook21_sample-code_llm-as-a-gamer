"""本体の画面の middle (gpt-6-luna) の developer メッセージに置く変わらない指示 (SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS) と、
目的ごとの手順の文 (goal_text)。

ゲームの画面の middle の指示 (middle.luna_instructions.LUNA_DEVELOPER_INSTRUCTIONS) とは別の指示にする。
指示は入力によらず同じ文字列にする (要求で、この指示の直後に明示のキャッシュの区切りを置くため)。指定のソフトの名前と
ユーザーの名前、目的、画像、照合の結果、履歴などの毎回変わる内容は、指示に入れずに user メッセージに置く
(middle.system_screen_request)。目的ごとの手順の文 (goal_text) も、その回の目的の分だけを user メッセージに置く。

SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS に書く事項 (この順):
    1. middle がすること: Nintendo Switch 2 の本体の画面で、目的 (準備・復帰・片付け) を果たす操作の列を決める。
       ゲームの中の操作は決めない
    2. 入力の読み方: user メッセージの節 (middle.system_screen_request の節) ごとの中身
    3. 見本の画面ごとの見た目と、使う操作:
           home_menu          HOME メニュー。上の段がソフト、下の段が本体の機能。上の段のソフトの名前は、選んでいる
                              ソフトの上に出る。下の段のアイコンは、左から ONLINE・(C の字)・ニュース・ショップ・
                              アルバム・…・コントローラー・…・設定・電源の順に並ぶ
           controllers        HOME メニューの「コントローラー」。右側の「持ちかた/順番を変える」を選ぶ
           controller_order   持ちかた/順番を変える。L と R を同時に押し (1 つの press に L と R)、枠の 1 に
                              コントローラーが出たら A で確定する
           user_select        ユーザー選択。最初のカーソルはユーザーの並びの先頭にある。指定のユーザーの名前の上に
                              カーソルを合わせてから A を押す
       none は見本のどの画面でもない画面 (ゲームの画面・切り替わりの途中の画面など)
    4. 出力の書き方: observation (画面の様子とカーソルの位置)、operations (screen・type・buttons・seconds)、done。
       screen には、その操作を送る前に映っているはずの画面の名前を書く。操作は前から順に送られ、送る前の照合の結果の
       画面が screen と違えば、その操作から後は送られない
    5. してはいけない操作: ONLINE (Nintendo Switch Online)・ショップ・ニュース・設定・電源・アルバムなど、目的の手順に
       無いアイコンで A を押さない。指定のソフト以外を起動しない。指定のユーザー以外で決定しない。ユーザーの追加・
       ソフトの削除・データの削除に進まない。迷ったら操作の列を空にして done を偽にする
    6. 決定の A の置き方: A は操作の列の先頭にだけ置く。A を置くのは、今回の画像でカーソルが目的の項目にあると確かめた
       ときに限る。カーソルを動かした後に決定するときは、移動だけの列を返し、次の呼び出しで画像を見てから A を返す
       (2 つ目以降の操作に A があると、その操作の前で打ち切られる)
    7. カーソルの移動: 短押し (seconds を null) で行い、押し続けを使わない (押し続けるとカーソルが複数進むため)。
       十字キーは 1 回の press に 1 方向だけ。HOME メニューのソフトの一覧では、左端で LEFT を押すと右端の「もっとみる」へ
       回り込む。入力の直後の画面は、反映前や遷移の途中のことがある
    8. 安全のための規則: safety.safety_rules.SAFETY_RULES の文
    ソフトの名前とユーザーの名前は書かない (user メッセージで渡す)。

goal_text が返す、目的ごとの手順の文:
    prepare    HOME メニューの「コントローラー」→「持ちかた/順番を変える」でブリッジのコントローラーを 1P に登録し、
               HOME メニューに戻って指定のソフトを起動し、ユーザー選択で指定のユーザーを選ぶ。ゲームの画面が映ったら
               done を真にする。始めにゲームの画面が映っていれば HOME を押す
    recover    指定のソフトのゲームの画面に戻る。HOME メニューなら、中断中の指定のソフトを選んで A で再開する。
               中断中のソフトが無ければ起動し、ユーザー選択で指定のユーザーを選ぶ。持ちかた/順番を変える画面なら、
               L+R と A で登録する。ゲームの画面が映ったら done を真にする
    clean_up   HOME メニューに戻り、指定のソフトを終了する。HOME メニューに戻ったら done を真にする
    手順の文にも、ソフトの名前とユーザーの名前は入れない (「指定のソフト」「指定のユーザー」と書く)。
"""

from switch2_layers.middle.system_screen_types import SystemGoal

SYSTEM_SCREEN_DEVELOPER_INSTRUCTIONS = """\
あなたは、Nintendo Switch 2 の本体の画面で、目的を果たす操作の列を決める middle 層です。
"""


def goal_text(goal: SystemGoal) -> str:
    """goal の手順の文 (モジュールの docstring の「goal_text が返す、目的ごとの手順の文」) を返す。

    目的ごとに違う、空でない文を返す。ソフトの名前とユーザーの名前は含めない。
    """
    raise NotImplementedError

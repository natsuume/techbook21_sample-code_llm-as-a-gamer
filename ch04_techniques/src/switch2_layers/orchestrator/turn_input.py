"""主ループの 1 ターンの入力 (テキストと画像のユーザーメッセージ 1 つ) を組み立てる。

ファイルを読む関数 (list_note_files) のほかは、I/O を扱わない。

テキスト (build_turn_text) は、次の節を順に改行でつないだもの:
    ## 時刻                         t=<今の時刻> (frames.surface_frames.format_frame_label の形)
    ## 前の判断の後の出来事           describe_events
    ## 主ループの操作                 describe_running
    ## 前の操作の列                   describe_previous_actions (前のターンの操作の列を始めた時刻と終えた時刻。
                                    TurnContext.previous_actions が None (前のターンに操作が無かった) なら節を置かない)
    ## 画面の停滞 (コードの計測)       describe_progress_for_turn
    ## 前の JSON の誤り               describe_decision_error
    ## 写さなかったスキル              describe_skipped_skills
    ## 今の監視                       describe_monitor (--mode orchestrator のとき)
    ## 委任                           describe_delegation (--mode delegate のとき。今の監視の節の代わりに置く)
    ## 今のマクロ                     describe_macros
    ## 古いフレーム                   FRAMES_GUIDE_TEXT
--mode delegate のときは、出来事の OrchestratorEvents.delegation が None でない (それで委任の節を置くかを決める)。
セッションの最初の入力では、この前に build_session_opening_text の節を置く (人の初期指示・notes/ のファイルの一覧・
notes/handoff.md の中身)。人の初期指示が無いときも節は省かず、本文を NONE_TEXT にして、初期指示が無いことを伝える。
出来事の繰り越し (carry_over_events): result を受け取れなかった入力に載せた一度だけの出来事 (条件の当たり・監視の
終わり・委任の終わり・捨てた操作の数・委任中のため捨てた操作の数・当てなかった delegate の action・外したボタン) は、
次の入力で新しく取り出した出来事と合わせて渡す。
画像 (select_turn_frames): 入力を作る時点の最新の一定間隔のフレームのうち、新しいものから最大 TURN_MAX_IMAGES 枚を
古い順に並べたもの。主ループが入力を作るときに 1 回受け取った、同じ時点の frames/latest/ の画像
(frames.latest_frames.LatestFrames.snapshot。時刻を書き込んだ JPEG) を使う。セッションの最初のターンも同じ。
メッセージの内容 (build_turn_content): テキストのブロックの後に、画像ごとに時刻のラベルのテキストのブロックと画像の
ブロックを置く。
"""

import base64
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from switch2_layers.deep.deep_scheduler import PROGRESS_STATE_MAX_AGE_S
from switch2_layers.file_io import is_temporary_file_name
from switch2_layers.frames.latest_frames import LatestFrame
from switch2_layers.frames.surface_frames import format_frame_label
from switch2_layers.orchestrator.decision_types import (
    Action,
    DecisionError,
    MacroAction,
    MonitorCreate,
    PressAction,
    ProcedureAction,
    WaitAction,
)
from switch2_layers.orchestrator.orchestrator_events import (
    DelegationStatus,
    MainActionsRun,
    OrchestratorEvents,
)
from switch2_layers.orchestrator.skill_copy import SkippedGameSkill
from switch2_layers.progress.progress_state import ProgressState
from switch2_layers.progress.progress_text import (
    NO_PROGRESS_TEXT,
    describe_progress_values,
)

# 1 ターンに送る画像の最大の枚数 (frames/latest/ の最新の 5 枚)
TURN_MAX_IMAGES = 5
# 節の中身が無いときに書く文
NONE_TEXT = "無し"
# 古いフレームの節の文
FRAMES_GUIDE_TEXT = (
    "frames/latest/1.jpg〜5.jpg は最新の 5 枚 (1.jpg が最新) で、"
    "いつ Read してもその時点の最新の画面が読める。"
    "それより古いフレームは frames/interval/ (<tミリ秒9桁>.jpg) にある"
)
# 画像のブロックの media_type
IMAGE_MEDIA_TYPE = "image/jpeg"


@dataclass(frozen=True)
class TurnImage:
    """入力に入れる画像 1 枚。

    t      フレームの時刻
    file   元になった一定間隔のフレームの画像の、実行ディレクトリーからの相対パス (frames/interval/<tミリ秒9桁>.jpg。
           frames.jsonl の file。ラベルと turns.jsonl の images に書く)
    jpeg   送る画像のバイト列 (frames/latest/ に置いた、時刻を書き込んだ JPEG)
    """

    t: float
    file: str
    jpeg: bytes


@dataclass(frozen=True)
class TurnContext:
    """ふつうのターンのテキストの材料。

    now_t            今の時刻 (入力を作る時刻)
    events           前の判断以降の出来事と、今のマクロと監視 (OrchestratorController.take_events の結果に、
                     繰り越した出来事を carry_over_events で合わせたもの)
    progress         画面の停滞の計測の最新の値 (progress/state.json)。無ければ None
    previous_error   前のターンの JSON の誤り。前のターンに誤りが無かった・前のターンが無ければ None
    skipped_skills   最後にゲームの内容のスキルを写したとき (skill_copy.copy_game_skills) に、中身を確かめて
                     写さなかったスキル
    previous_actions 前のターンの操作の列の時刻 (主ループが終わるのを待った列。終わった列だけを渡す)。前のターンの
                     actions が空だった・JSON の誤り・引き継ぎの依頼・セッションの最初のターンなら None
    """

    now_t: float
    events: OrchestratorEvents
    progress: ProgressState | None
    previous_error: DecisionError | None
    skipped_skills: tuple[SkippedGameSkill, ...] = ()
    previous_actions: MainActionsRun | None = None


@dataclass(frozen=True)
class SessionOpening:
    """セッションの最初の入力に足す材料。

    initial_instruction   人の初期指示。無ければ None
    note_files            notes/ のファイルの notes/ からの相対パス (list_note_files の結果)
    handoff_text          notes/handoff.md の中身 (無ければ空文字列)
    """

    initial_instruction: str | None
    note_files: tuple[str, ...]
    handoff_text: str


def select_turn_frames(latest: Sequence[LatestFrame]) -> list[TurnImage]:
    """最新のフレームの画像 latest (新しい順。frames.latest_frames.LatestFrames.snapshot の値) から、入力に入れる画像を
    選んで古い順に返す (I/O なし)。

    latest の先頭 (新しいもの) から最大 TURN_MAX_IMAGES 枚を取り、古い順に並べ直して、それぞれを
    TurnImage(t=frame.t, file=frame.source_file, jpeg=frame.jpeg) にする。TURN_MAX_IMAGES 枚に満たなければある分だけ、
    latest が空なら空のリスト。
    """
    newest = list(latest[:TURN_MAX_IMAGES])
    return [
        TurnImage(t=frame.t, file=frame.source_file, jpeg=frame.jpeg)
        for frame in reversed(newest)
    ]


def action_to_json(action: Action) -> dict[str, object]:
    """操作 1 つを、主ループが書く JSON の形の辞書にする (今の監視とマクロの一覧に使う)。

    PressAction       {"type": "press", "buttons": [...]}。sticks が空でなければ
                      "sticks": {"left" | "right": {"x": x, "y": y}} を、seconds が None でなければ "seconds" を足す
    WaitAction        {"type": "wait", "seconds": 秒}
    ProcedureAction   {"type": "procedure", "name": 名前, "args": {引数}}
    MacroAction       {"type": "macro", "name": 名前}
    """
    if isinstance(action, PressAction):
        row: dict[str, object] = {"type": "press", "buttons": list(action.buttons)}
        if action.sticks:
            row["sticks"] = {
                side: {"x": x, "y": y} for side, (x, y) in action.sticks.items()
            }
        if action.seconds is not None:
            row["seconds"] = action.seconds
        return row
    if isinstance(action, WaitAction):
        return {"type": "wait", "seconds": action.seconds}
    if isinstance(action, ProcedureAction):
        return {"type": "procedure", "name": action.name, "args": dict(action.args)}
    if isinstance(action, MacroAction):
        return {"type": "macro", "name": action.name}
    raise TypeError(f"unknown action: {action!r}")


def _actions_json(actions: Sequence[Action]) -> list[dict[str, object]]:
    """操作の列を action_to_json の辞書のリストにする。"""
    return [action_to_json(action) for action in actions]


def describe_events(events: OrchestratorEvents) -> str:
    """前の判断の後の出来事を、1 件 1 行の文にする (改行でつなぐ)。1 件も無ければ NONE_TEXT。

    次の順に書く:
        条件の当たりごとに   監視 <monitor> の条件 C<case_index> (<condition>) に <count> 回当たり、操作 <actions を
                             action_to_json の JSON の配列にしたもの> を実行した
        監視の終わりごとに   監視 <name> が終わった (<reason>): <detail>
        委任の終わりごとに   委任が終わり、操作権が主ループに戻った (<reason>): <detail>
        当てなかった delegate の action ごとに (delegate_skipped_actions の順)
                             委任はすでに終わっていたため、delegate の <action (stop か update)> を当てなかった
        捨てた操作が 1 以上   新しい actions で捨てた前の操作: <discarded_action_count> 個
        委任中のため捨てた操作が 1 以上
                             委任中のため、操作を捨てた: <delegate_discarded_action_count> 個
        外したボタンがあれば  押せずに外したボタン: <blocked_buttons を ", " でつないだもの>
    JSON は json.dumps(…, ensure_ascii=False)。
    """
    lines = [
        f"監視 {hit.monitor} の条件 C{hit.case_index} ({hit.condition}) に {hit.count} 回当たり、"
        f"操作 {json.dumps(_actions_json(hit.actions), ensure_ascii=False)} を実行した"
        for hit in events.case_hits
    ]
    lines += [
        f"監視 {end.name} が終わった ({end.reason}): {end.detail}"
        for end in events.monitor_ends
    ]
    lines += [
        f"委任が終わり、操作権が主ループに戻った ({end.reason}): {end.detail}"
        for end in events.delegate_ends
    ]
    lines += [
        f"委任はすでに終わっていたため、delegate の {action} を当てなかった"
        for action in events.delegate_skipped_actions
    ]
    if events.discarded_action_count >= 1:
        lines.append(
            f"新しい actions で捨てた前の操作: {events.discarded_action_count} 個"
        )
    if events.delegate_discarded_action_count >= 1:
        lines.append(
            f"委任中のため、操作を捨てた: {events.delegate_discarded_action_count} 個"
        )
    if events.blocked_buttons:
        lines.append(f"押せずに外したボタン: {', '.join(events.blocked_buttons)}")
    return "\n".join(lines) if lines else NONE_TEXT


def describe_running(events: OrchestratorEvents) -> str:
    """主ループの操作の状態を 2 行の文にする: `実行中: はい` か `実行中: いいえ` と、`残りの操作: <remaining_action_count> 個`。"""
    running = "はい" if events.running else "いいえ"
    return f"実行中: {running}\n残りの操作: {events.remaining_action_count} 個"


def describe_previous_actions(run: MainActionsRun) -> str:
    """前のターンの操作の列 run (終わった列。ended_t が None でない) の時刻を 2 行の文にする。

        始めた時刻: <format_frame_label(run.started_t)>
        終えた時刻 (最後に離した時刻): <format_frame_label(終えた時刻)>
    終えた時刻は run.released_t。押す操作の無い列 (released_t が None) なら run.ended_t。
    run が終わっていなければ (started_t か ended_t が None なら) ValueError。
    """
    if run.started_t is None or run.ended_t is None:
        raise ValueError(f"the actions have not ended: {run!r}")
    end_t = run.ended_t if run.released_t is None else run.released_t
    return (
        f"始めた時刻: {format_frame_label(run.started_t)}\n"
        f"終えた時刻 (最後に離した時刻): {format_frame_label(end_t)}"
    )


def describe_progress_for_turn(progress: ProgressState | None, now_t: float) -> str:
    """画面の停滞の計測の値を文にする。

    progress が None か、now_t - progress.t が deep.deep_scheduler.PROGRESS_STATE_MAX_AGE_S を超える (古い) なら
    progress.progress_text.NO_PROGRESS_TEXT (ちょうどその秒数なら使う。古い値の扱いは deep のスケジューラーと同じ)。
    そうでなければ `計測した時刻: <format_frame_label(progress.t)>` の行と、
    progress.progress_text.describe_progress_values(progress) の 2 行。
    """
    if progress is None or now_t - progress.t > PROGRESS_STATE_MAX_AGE_S:
        return NO_PROGRESS_TEXT
    return (
        f"計測した時刻: {format_frame_label(progress.t)}\n"
        f"{describe_progress_values(progress)}"
    )


def describe_decision_error(error: DecisionError | None) -> str:
    """前の JSON の誤りを文にする。None なら NONE_TEXT、そうでなければ説明ごとに `- <説明>` の行。"""
    if error is None:
        return NONE_TEXT
    return "\n".join(f"- {message}" for message in error.messages)


def describe_skipped_skills(skipped: Sequence[SkippedGameSkill]) -> str:
    """写さなかったゲームの内容のスキルを、1 件 1 行の `- <name>: <reason>` の文にする。無ければ NONE_TEXT。

    Claude が notes/skills/<name>/SKILL.md を直せるよう、名前と理由をそのまま書く。
    """
    if not skipped:
        return NONE_TEXT
    return "\n".join(f"- {skill.name}: {skill.reason}" for skill in skipped)


def carry_over_events(
    carried: OrchestratorEvents | None, taken: OrchestratorEvents
) -> OrchestratorEvents:
    """result を受け取れなかった入力に載せた出来事 carried を、新しく取り出した出来事 taken と合わせて返す (I/O なし)。

    carried が None なら taken をそのまま返す。そうでなければ次の値の OrchestratorEvents を返す:
        case_hits                carried の後に taken を並べる (古い順)
        monitor_ends             carried の後に taken を並べる (古い順)
        delegate_ends            carried の後に taken を並べる (古い順)
        delegate_skipped_actions carried の後に taken を並べる (古い順。同じ名前も重ねて並べる)
        discarded_action_count   carried と taken の和
        delegate_discarded_action_count
                                 carried と taken の和
        blocked_buttons          carried の後に taken を並べ、2 回目以降に出てきたボタンを除く (重ねない)
        running・remaining_action_count・macros・monitor・delegation
                                 taken の値 (取り出した時点の値のため)
    """
    if carried is None:
        return taken
    blocked = tuple(dict.fromkeys((*carried.blocked_buttons, *taken.blocked_buttons)))
    return OrchestratorEvents(
        case_hits=(*carried.case_hits, *taken.case_hits),
        monitor_ends=(*carried.monitor_ends, *taken.monitor_ends),
        discarded_action_count=carried.discarded_action_count
        + taken.discarded_action_count,
        blocked_buttons=blocked,
        running=taken.running,
        remaining_action_count=taken.remaining_action_count,
        macros=taken.macros,
        monitor=taken.monitor,
        delegate_ends=(*carried.delegate_ends, *taken.delegate_ends),
        delegate_discarded_action_count=carried.delegate_discarded_action_count
        + taken.delegate_discarded_action_count,
        delegate_skipped_actions=(
            *carried.delegate_skipped_actions,
            *taken.delegate_skipped_actions,
        ),
        delegation=taken.delegation,
    )


def describe_monitor(monitor: MonitorCreate | None) -> str:
    """今の監視を JSON の文にする。None なら NONE_TEXT。

    {"name": 名前, "cases": [{"condition": 条件の文, "actions": [action_to_json の辞書, ...], "repeat": ...}, ...]} を
    json.dumps(…, ensure_ascii=False) にしたもの。
    """
    if monitor is None:
        return NONE_TEXT
    value = {
        "name": monitor.name,
        "cases": [
            {
                "condition": case.condition,
                "actions": _actions_json(case.actions),
                "repeat": case.repeat,
            }
            for case in monitor.cases
        ],
    }
    return json.dumps(value, ensure_ascii=False)


def describe_delegation(status: DelegationStatus) -> str:
    """委任の状況 status を、次の 7 行の文にする (改行でつなぐ):

        委任中: はい                                         (委任中でなければ `委任中: いいえ`)
        委任を始めた時刻: <format_frame_label(started_t)>       (started_t が None なら NONE_TEXT)
        タスク: <values.tasks の JSON の配列>                   (values が None なら NONE_TEXT。以下の 2 行も同じ)
        注意事項: <values.cautions の JSON の配列>
        行動: <values.choices を [{"name": 名前, "description": 説明}, ...] にした JSON の配列>
        Jev が直近に選んだ行動: <last_pick.name> (<format_frame_label(last_pick.t)>)   (last_pick が None なら NONE_TEXT)
        Jev が「操作を返す」を続けて選んだ回数: <return_streak>
    委任中でなければ、タスク・注意事項・行動は前の委任の値 (start で省いたキーに使う値) を書く。
    JSON は json.dumps(…, ensure_ascii=False)。
    """
    values = status.values

    def as_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False)

    started = (
        NONE_TEXT if status.started_t is None else format_frame_label(status.started_t)
    )
    pick = status.last_pick
    last_pick = (
        NONE_TEXT if pick is None else f"{pick.name} ({format_frame_label(pick.t)})"
    )
    lines = [
        f"委任中: {'はい' if status.active else 'いいえ'}",
        f"委任を始めた時刻: {started}",
        f"タスク: {NONE_TEXT if values is None else as_json(list(values.tasks))}",
        f"注意事項: {NONE_TEXT if values is None else as_json(list(values.cautions))}",
    ]
    if values is None:
        lines.append(f"行動: {NONE_TEXT}")
    else:
        choices = [
            {"name": choice.name, "description": choice.description}
            for choice in values.choices
        ]
        lines.append(f"行動: {as_json(choices)}")
    lines += [
        f"Jev が直近に選んだ行動: {last_pick}",
        f"Jev が「操作を返す」を続けて選んだ回数: {status.return_streak}",
    ]
    return "\n".join(lines)


def describe_macros(macros: Mapping[str, Sequence[Action]]) -> str:
    """今のマクロを JSON の文にする。空なら NONE_TEXT。

    {名前: [action_to_json の辞書, ...]} (名前の順) を json.dumps(…, ensure_ascii=False) にしたもの。
    """
    if not macros:
        return NONE_TEXT
    value = {name: _actions_json(macros[name]) for name in sorted(macros)}
    return json.dumps(value, ensure_ascii=False)


def build_turn_text(context: TurnContext) -> str:
    """ふつうのターンのテキスト (モジュールの docstring の節の並び) を返す。

    context.previous_actions が None なら「## 前の操作の列」の節を置かない。context.events.delegation が None なら
    「## 今の監視」の節を置き、None でなければ、同じ位置に「## 委任」の節 (describe_delegation) を置いて「## 今の監視」の
    節を置かない。ほかの節はいつも置く。
    """
    sections = [
        ("## 時刻", format_frame_label(context.now_t)),
        ("## 前の判断の後の出来事", describe_events(context.events)),
        ("## 主ループの操作", describe_running(context.events)),
    ]
    if context.previous_actions is not None:
        sections.append(
            ("## 前の操作の列", describe_previous_actions(context.previous_actions))
        )
    delegation = context.events.delegation
    sections += [
        (
            "## 画面の停滞 (コードの計測)",
            describe_progress_for_turn(context.progress, context.now_t),
        ),
        ("## 前の JSON の誤り", describe_decision_error(context.previous_error)),
        ("## 写さなかったスキル", describe_skipped_skills(context.skipped_skills)),
        (
            ("## 今の監視", describe_monitor(context.events.monitor))
            if delegation is None
            else ("## 委任", describe_delegation(delegation))
        ),
        ("## 今のマクロ", describe_macros(context.events.macros)),
        ("## 古いフレーム", FRAMES_GUIDE_TEXT),
    ]
    return "\n".join(f"{heading}\n{body}" for heading, body in sections)


def list_note_files(notes_dir: Path) -> tuple[str, ...]:
    """notes_dir の下の通常のファイルの、notes_dir からの / 区切りの相対パスを名前の順に返す。

    os.walk(followlinks=False) でたどり、シンボリックリンクと、置き換えの一時ファイル
    (file_io.is_temporary_file_name) は含めない。notes_dir が無ければ空のタプル。
    """
    files: list[str] = []
    for current, _directories, names in os.walk(notes_dir, followlinks=False):
        for name in names:
            path = Path(current) / name
            if is_temporary_file_name(name) or path.is_symlink() or not path.is_file():
                continue
            files.append(path.relative_to(notes_dir).as_posix())
    return tuple(sorted(files))


def build_session_opening_text(opening: SessionOpening) -> str:
    """セッションの最初の入力の、ふつうのターンのテキストの前に置く節を返す。次の節を順に改行でつなぐ:

    ## 人の初期指示                  opening.initial_instruction (None なら NONE_TEXT。初期指示が無いことを伝える)
    ## notes/ のファイル             note_files ごとに `- <相対パス>` の行 (無ければ NONE_TEXT)
    ## 引き継ぎ (notes/handoff.md)   handoff_text (空か空白だけなら NONE_TEXT)
    """
    instruction = opening.initial_instruction
    sections: list[tuple[str, str]] = [
        ("## 人の初期指示", NONE_TEXT if instruction is None else instruction)
    ]
    note_lines = "\n".join(f"- {note}" for note in opening.note_files)
    sections.append(("## notes/ のファイル", note_lines or NONE_TEXT))
    handoff = opening.handoff_text.rstrip()
    sections.append(("## 引き継ぎ (notes/handoff.md)", handoff or NONE_TEXT))
    return "\n".join(f"{heading}\n{body}" for heading, body in sections)


def image_label(image: TurnImage) -> str:
    """画像の前に置く時刻のラベル `<format_frame_label(image.t)> の画面 (<image.file>)` を返す。"""
    return f"{format_frame_label(image.t)} の画面 ({image.file})"


def build_turn_content(
    text: str, images: Sequence[TurnImage]
) -> list[dict[str, object]]:
    """ユーザーメッセージの内容のブロックの列を返す。

    [{"type": "text", "text": text}] の後に、images の順に、画像ごとに
    {"type": "text", "text": image_label(画像)} と
    {"type": "image", "source": {"type": "base64", "media_type": IMAGE_MEDIA_TYPE, "data": <jpeg の base64 (ASCII)>}}
    を足す。
    """
    content: list[dict[str, object]] = [{"type": "text", "text": text}]
    for image in images:
        content.append({"type": "text", "text": image_label(image)})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": IMAGE_MEDIA_TYPE,
                    "data": base64.b64encode(image.jpeg).decode("ascii"),
                },
            }
        )
    return content

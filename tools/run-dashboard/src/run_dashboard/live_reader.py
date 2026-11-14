"""実行中の実行のディレクトリーを接続ごとに読み、追記された完全な行から SSE のイベント (live_events) を作る。

1 つの接続 (GET /api/runs/{run}/live) ごとに 1 つの読み取りの状態 (LiveState) を持ち、open_live で最初の読み取り、
poll_live で 2 回目以降の読み取りを行う。1 回の読み取りは「状態と今の時刻を受け取り、送るイベントの列と次の状態を
返す」形で、待ちと時計は外から渡す (stream_live)。ファイルには何も書かない。

1 回の読み取りの手順 (open_live と poll_live で共通):
    1. run.jsonl の大きさ S を os.stat で取る (無い・シンボリックリンク・OSError なら、無いものとする)
    2. requests.jsonl を、今の位置から終わりまで読む
    3. run.jsonl を、今の位置から S まで読む (jsonl_lines.read_complete_lines の end=S)。手順 2 の後に書かれた
       decision の行を読まないため、読んだ decision の行の要求の行は、必ず手順 2 までに読めている
    4. frames.jsonl・pad.jsonl を、今の位置から終わりまで読む
    5. advisor の実行のディレクトリーの一覧 (run_directory.list_advisor_run_dirs)。実行ごとに、result.json を
       まだ読めていなければ run_records.read_result_json で読み (一度読めたら読み直さない)、stream.jsonl を
       今の位置から読む
    6. run.jsonl の更新時刻を os.stat で取る (実行中の判定に使う)
    JSON Lines は jsonl_lines.read_complete_lines を `jsonl_lines.read_complete_lines(...)` の形 (モジュールの属性)
    で呼んで読み、jsonl_lines.parse_lines で読む (テストがこの関数を差し替えて、読み取りの間に行を書き足す)。
    シンボリックリンク・無いファイル・OSError は、その読み取りでは行なし (位置は進めない。例外を出さない)。
    位置より小さくなったファイルは、行なしとして扱う。

接続時 (open_live) の位置:
    4 つの JSON Lines は位置 0 から今の終わりまで読む。cursor のあるファイルは、cursor より前 (end=cursor で読んだ
    行) を「状態だけの行」、cursor から後 (位置 cursor から読んだ行) を「送る行」にする。cursor が行の途中なら、その
    行はどちらにも入れない。cursor がファイルの大きさより大きければ、その位置より後に始まる行だけが送る行になる。
    cursor を省いた (None の) ファイルは、今の終わりまでのすべてを状態だけの行にする。advisor の stream.jsonl は、
    接続時にあったファイルはすべてを状態だけの行にし、接続後にできたファイル (接続時にディレクトリーがあって
    ファイルが無かったものを含む) は位置 0 から送る行にする。

状態に入れるもの (状態だけの行と送る行のどちらも):
    requests.jsonl  event が "header" の最初の行を header にする。event が "request" の行を
                    run_records.lighten_request_lines(..., previous=前の要求の行) で軽くして足し、
                    run_records.add_received_time で受信時刻を足す。t_sent・t_received (数のもの) を経過秒の最大値に入れる
    run.jsonl       行を足す。start の行 (event が "start" の最初の行) を読むまでは経過秒を求めない。start の行を
                    読んだら run_records.start_run_log_clock で時計を作り、それまでの行と新しい行の t を、start より
                    前は elapsed_before_start、start は 0.0、start より後は advance_run_log_clock で求める。以後は
                    ファイルの順に advance_run_log_clock。decision の行の decide_s (数のもの)、record_end の有無、
                    経過秒の最大値を持つ
    frames・pad     行の数 (JSON の object の行の数) と、数の mono (bool を除く) の最大値を持つ。start の行の
                    origin_mono が数なら、mono の最大値 - origin_mono を経過秒の最大値に入れる
    advisor         実行ごとに stream.jsonl の全行 (状態だけの行を含む) と、送った step の数

イベント:
    request・run・frame・pad・advisor_step は、start の行を読んだ後だけ作る。start の行を読む前の読み取りは送る行を
    ためておき、start の行を読んだ読み取りでファイルの順に作る (start の行の無い実行では作らない)。
    advisor_run・note_version・stats・end は、start の行によらず、読み取りのたびに下の規則で作る (stats の件数は
    start の行を読む前も数える)。
    request       送る要求の行ごとに run_views.build_request_item(行, run_views.request_item_context(header,
                  start の args))。requests.jsonl の無い実行と、古い形の実行 (question_values.is_old_format) では
                  作らない
    run           送る run.jsonl の行ごとに run_views.build_event_item(RunEvent) (None の行は作らない)
    frame・pad    送る行ごとに run_views.build_frame_item・build_pad_item。origin_mono が None なら作らない
    advisor_step  古い形でなければ、advisor のディレクトリーの順に、advisor_runs.build_steps(その実行の stream.jsonl
                  の全行, origin_mono, その実行の denied_tool_use_ids) のうちまだ送っていない分 (状態だけの行の step は
                  送ったものとして数える) を {"name": 実行の名前, "t", "kind", "tool", "summary"} にする。kind は
                  送った時点の値 (後で変わっても送り直さない)
    advisor_run   advisor_runs = [] if 古い形 else advisor_runs.build_advisor_runs(一覧, 要求の行, RunEvent, 状態の
                  result.json を返す関数) の各実行の run_views.summary_advisor_run。open_live はすべて、poll_live は
                  前に送った値 (名前で引く) と違うものと新しいものだけ。並びは advisor_runs の順
    note_version  [] if 古い形 else note_versions.build_note_versions(要求の行, advisor_runs) の各版の
                  run_views.summary_note_version。open_live はすべて、poll_live は前に送った値 (番号で引く) と
                  違うものと新しいものだけ。番号の昇順
    stats         {"t_end": run_views.max_elapsed([経過秒の最大値] + [各 advisor_run の t_end]),
                   "counts": {"requests": 要求の行の数, "frames": frames の行の数, "pad": pad の行の数},
                   "decide_s_median": decide_s の中央値 (無ければ None)}。open_live はいつも、poll_live は前に
                  送った値と違うときだけ
    end           run_activity.is_in_progress(手順 6 の更新時刻, record_end の有無, now) が偽なら
                  {"record_end": record_end の有無} を最後に足し、LivePoll.finished を真にする
1 回の読み取りの events の順は request → run → frame → pad → advisor_step → advisor_run → note_version →
stats → end。同じ種類の中はファイルの順 (advisor_step はディレクトリーの順の中でファイルの順)。
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from run_dashboard.live_events import LiveEvent

# 実行のディレクトリーを読む間隔 (秒)
LIVE_POLL_INTERVAL_S = 0.25


@dataclass(frozen=True)
class LiveCursors:
    """接続のクエリーの、読み始める位置 (バイトの位置。区間 API の cursor の値)。

    None は省いたファイル (接続した時点の完全な行の終わりから送る)。
    run       run.jsonl の位置 (クエリーの run)
    requests  requests.jsonl の位置
    frames    frames.jsonl の位置
    pad       pad.jsonl の位置
    """

    run: int | None = None
    requests: int | None = None
    frames: int | None = None
    pad: int | None = None


@dataclass(frozen=True)
class LiveState:
    """1 つの接続の読み取りの状態。

    フィールドは live_reader の中の約束で、呼び出し側は中を見ない・変えない。読み取りのたびに新しい状態を作り、
    渡された状態は変えない。
    """


@dataclass(frozen=True)
class LivePoll:
    """1 回の読み取りの結果。

    events    送るイベント (送る順)
    state     次の読み取りに渡す状態
    finished  end のイベントを送ったか (真なら、以後は読まない)
    """

    events: list[LiveEvent]
    state: LiveState
    finished: bool


def open_live(run_dir: Path, cursors: LiveCursors, now: float) -> LivePoll:
    """接続の最初の読み取りを行い、送るイベントと次の状態を返す (モジュールの docstring の手順と接続時の位置)。

    run_dir は照合済みの実行のディレクトリー (無ければ、すべてのファイルが無いものとして扱う)。cursors は接続の
    クエリーの位置、now は今の時刻 (time.time() と同じ時計の秒)。
    events は、cursor 以後の行のイベント、advisor_run と note_version のすべて、stats (と、実行中でなければ end)。
    cursors のどれかが負なら ValueError を出す。
    """
    raise NotImplementedError


def poll_live(state: LiveState, now: float) -> LivePoll:
    """2 回目以降の読み取りを行い、前の読み取りから後に追記された完全な行のイベントと、次の状態を返す。

    渡した state は変えない (ファイルが同じなら、同じ state で 2 回呼ぶと同じ結果になる)。finished の読み取りの
    state を渡すと、読まずに LivePoll([], state, True) を返す。
    """
    raise NotImplementedError


async def stream_live(
    run_dir: Path,
    cursors: LiveCursors,
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[object]],
    interval_s: float = LIVE_POLL_INTERVAL_S,
) -> AsyncIterator[LiveEvent]:
    """接続のイベントを順に返す非同期のジェネレーター。

    open_live を anyio.to_thread.run_sync で (スレッドで) 呼んでイベントを順に yield し、finished でなければ
    `await sleep(interval_s)` → poll_live (スレッド) → yield を繰り返す。finished になったら戻る。
    now は読み取りのたびに clock() で取る。poll_live はモジュールの属性として、呼ぶたびに名前で引いて呼ぶ
    (テストが呼んだ回数を数える関数に差し替える)。取り消し (クライアントが接続を切った) は sleep か
    スレッドの待ちで届き、それ以上は読まない。
    """
    raise NotImplementedError
    # 本体が yield を含む非同期のジェネレーターの関数にするための、到達しない yield
    yield

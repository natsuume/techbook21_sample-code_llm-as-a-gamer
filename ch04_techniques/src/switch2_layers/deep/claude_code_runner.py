"""deep の 1 回のジョブを、Claude Code (claude -p) を子プロセスとして起動して実行する DeepRunner (ClaudeCodeDeepRunner)。

1 回のジョブの流れ (run):
    1. map のジョブは Claude Code を起動せず、何も書かずに DeepResult(ok=True, message=MAP_SKIPPED_MESSAGE) を返す。
       DeepScheduler は、この message を run.jsonl の deep_end に書く
    2. job.json を読み (deep_prompt)、対象の文書とその親のディレクトリー (notes/ を含む) がシンボリックリンクでない
       ことを確かめる (notes.note_symlink_check)。リンクがあれば Claude Code を起動せず、DeepResult(ok=False,
       message=リンクだったパスを含む説明) を返す。DeepScheduler は、この message を run.jsonl の deep_end に書く
    3. プロンプト (deep_prompt) と、引数と環境変数 (claude_code_command) を作り、claude -p を子プロセスとして起動する。
       作業ディレクトリーは実行ディレクトリー。子プロセスは新しいセッション (プロセスグループ) の先頭にする
       (start_new_session=True。プロセスグループの ID は子プロセスの pid と同じ)
    4. 標準出力の stream-json を 1 行ずつ読み、ジョブのディレクトリーの stream.jsonl に書く (claude_stream_log)。
       標準エラー出力は別のスレッドで最後まで読む
    5. 子プロセスが終わったら、ジョブのディレクトリーの result.json に結果を書き、DeepResult を返す
deep は notes/ の対象の文書の本体を直接編集する。このクラスは文書を読み書きしない。
止め方:
    stop はプロセスグループに SIGTERM を、kill は SIGKILL を送り、どちらも待たずにすぐ戻る。止められた run は
    DeepResult(ok=False, stopped=True) を返す。claude は SIGTERM で終了コード 143 で終わり、result の行を出さない
    (https://code.claude.com/docs/en/headless、2026-11-15 に確認)。
    タイムアウトは DeepScheduler が判定し、stop・kill を呼ぶ (このクラスは自分では時間を測って止めない)。
外部に触れないテストのため、子プロセスの起動・シグナルの送り先・環境変数・時計を差し替えられる。
"""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO, Protocol

from switch2_layers.deep.claude_code_command import ClaudeCodeOptions
from switch2_layers.deep.deep_types import DeepJob, DeepResult

# map のジョブの DeepResult.message (run.jsonl の deep_end の message になる)
MAP_SKIPPED_MESSAGE = "map jobs do not start Claude Code; nothing was done"
# stop で止めた run の DeepResult.message
STOPPED_MESSAGE = "stopped"
# kill で止めた run の DeepResult.message
KILLED_MESSAGE = "killed"
# DeepResult.message に入れる、最後のメッセージの最大の文字数
RESULT_MESSAGE_MAX_CHARS = 300
# 子プロセスが終わった後に、標準エラー出力を読むスレッドの終わりを待つ上限 (秒)
STDERR_JOIN_TIMEOUT_S = 5.0


def symlink_refusal_message(link: Path) -> str:
    """対象の文書のパスにシンボリックリンクがあって起動しなかったときの DeepResult.message を返す。

    `Claude Code was not started: <link.as_posix()> is a symbolic link` (link は解決しないパス)。
    """
    raise NotImplementedError


class ClaudeProcess(Protocol):
    """起動した claude の子プロセス (subprocess.Popen[str] が満たす部分)。

    pid          プロセス ID。start_new_session=True で起動するため、プロセスグループの ID でもある
    stdout       標準出力 (テキスト)。行ごとに読む (for line in stdout)
    stderr       標準エラー出力 (テキスト)。read() で最後まで読む
    returncode   終了コード。終わっていなければ None
    """

    pid: int
    stdout: IO[str] | None
    stderr: IO[str] | None
    returncode: int | None

    def wait(self, timeout: float | None = None) -> int:
        """終わるのを待ち、終了コードを返す。"""
        ...


class ClaudeProcessStarter(Protocol):
    """子プロセスを起動する関数 (subprocess.Popen と同じ引数を受け取る)。"""

    def __call__(self, args: list[str], **kwargs: object) -> ClaudeProcess:
        """args を起動して ClaudeProcess を返す。起動できなければ OSError を出す。"""
        ...


class ClaudeCodeDeepRunner:
    """deep_types.DeepRunner の実装。ジョブごとに claude -p を 1 つ起動する。

    同じインスタンスで、別々のジョブ (job_dir) の run を並行して呼んでよい。run はジョブのスレッドから、
    stop と kill はスケジューラーのスレッドから呼ばれる。ジョブは job_dir の絶対パス (Path.absolute) で区別する。

    options                ClaudeCodeOptions (None なら既定値)
    start_process          子プロセスを起動する関数 (None なら subprocess.Popen。呼ぶときに subprocess.Popen を引く)
    signal_process_group   プロセスグループにシグナルを送る関数 (pgid, signal) (None なら os.killpg)
    environ                子プロセスの環境変数の元 (None なら run のときの os.environ)
    clock                  所要時間と stream.jsonl の elapsed_s を測る時計 (None なら time.monotonic)
    """

    def __init__(
        self,
        options: ClaudeCodeOptions | None = None,
        start_process: ClaudeProcessStarter | None = None,
        signal_process_group: Callable[[int, int], None] | None = None,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """属性を持つ。実行中のジョブの記録 (job_dir → 子プロセス) と、止める要求の記録を空にする。"""
        raise NotImplementedError

    def run(self, job: DeepJob, job_dir: Path) -> DeepResult:
        """job_dir (deep/<種類>-<連番>/。job.json を用意済み) で job を実行し、結果を返す。

        1. job.kind が map なら、子プロセスを起動せず、ファイルも書かずに DeepResult(ok=True, message=MAP_SKIPPED_MESSAGE)
        2. このジョブに stop か kill が既に呼ばれていれば (run より前に止める要求が来た)、起動せずに
           DeepResult(ok=False, stopped=True, message=kill が呼ばれていれば KILLED_MESSAGE、そうでなければ STOPPED_MESSAGE)
           を返す (result.json は書かない)
        3. 起動の用意。次の順に行う。どれかが例外 (Exception) を出したら起動せず、説明 =
           error_text.describe_exception(例外) を result.json の error に書いて DeepResult(ok=False, message=説明) を返す:
             context = deep_prompt.read_prompt_context(job_dir)
                       (context.run_dir は、実行ディレクトリーのシンボリックリンクを解決した絶対パス)
             link = notes.note_symlink_check.find_symlink_in_note_paths(context.run_dir / run_directory.NOTES_DIR,
                    context.notes)
             link が None でなければ (対象の文書か、その親のディレクトリー (notes/ を含む) がシンボリックリンク)、
             起動せず、説明 = symlink_refusal_message(link) を result.json の error に書いて
             DeepResult(ok=False, message=説明) を返す (stopped は False)
             command = claude_code_command.build_claude_command(deep_prompt.build_deep_prompt(context), context.run_dir,
                       context.notes, options)
             env = claude_code_command.build_child_env(environ, options.hidden_env_names)
           この 3 と 4 の起動の失敗で書く result.json は、claude_stream_log.build_result_record(returncode=None,
           duration_s=0.0, result_message=None, last_assistant_text=None, stopped=False, killed=False, error=説明,
           stderr="") の辞書
        4. ロックの中で止める要求を確かめ、来ていなければ start_process(command, cwd=context.run_dir, env=env,
           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
           errors="replace", start_new_session=True) で起動して、実行中のジョブとして記録する。
           ロックの中で確かめるのは、起動の直前に来た stop を取りこぼさないため (来ていれば 2 と同じに戻る)。
           start_process が OSError を出したら (claude が無いなど)、3 と同じく、説明 (describe_exception) を
           result.json の error に書いて DeepResult(ok=False, message=説明) を返す (returncode は None)
        5. 標準エラー出力を別のスレッド (daemon) で最後まで読む。標準出力を 1 行ずつ読み、届くたびに
           claude_stream_log.ClaudeStreamLog (job_dir/stream.jsonl) の add_line に、起動からの秒 (clock の差) とともに渡す
        6. 標準出力が終わったら wait で終了を待ち、標準エラー出力のスレッドの終わりを最大 STDERR_JOIN_TIMEOUT_S 秒待つ
        7. ロックの中で実行中の記録から外し (以後の stop・kill はシグナルを送らない)、stop・kill が呼ばれたかを読む
        8. job_dir/result.json に claude_stream_log.build_result_record の辞書を置き換えの書き込み
           (file_io.write_json_atomic) で書く。duration_s は起動から 6 の終わりまでの秒、stopped は stop か kill が
           呼ばれたら True、killed は kill が呼ばれたら True、error は stream.jsonl の書き込みの失敗があればその説明
        9. 戻り値:
             stop か kill が呼ばれた         DeepResult(ok=False, stopped=True, message=KILLED_MESSAGE か STOPPED_MESSAGE)
             終了コード 0 で、result の行があり、is_error が True でない
                                             DeepResult(ok=True, message=最後のメッセージの先頭 RESULT_MESSAGE_MAX_CHARS 文字
                                             (無ければ空文字列))
             それ以外                         DeepResult(ok=False, message=`claude exited with code <終了コード>` と、
                                             最後のメッセージがあれば `: <その先頭 RESULT_MESSAGE_MAX_CHARS 文字>`)
        result.json を書けない (OSError) ときは、logging で警告を書き、戻り値は 9 のとおりにする。
        """
        raise NotImplementedError

    def stop(self, job_dir: Path) -> None:
        """job_dir のジョブに止める要求を出し、止まるのを待たずにすぐ戻る。例外は出さない。

        - 子プロセスが実行中なら、signal_process_group(子プロセスの pid, signal.SIGTERM) を呼ぶ
        - まだ起動していなければ、止める要求だけを記録する (run は起動せずに stopped で戻る)
        - run が既に戻っていれば (実行中の記録から外れていれば)、シグナルを送らない
        signal_process_group の ProcessLookupError (既に終わったプロセスグループ) と、ほかの OSError は外に出さない
        (ProcessLookupError 以外は logging で警告を書く)。
        """
        raise NotImplementedError

    def kill(self, job_dir: Path) -> None:
        """job_dir のジョブを強制終了し、終わるのを待たずにすぐ戻る。例外は出さない。

        stop と同じ扱いで、送るシグナルを signal.SIGKILL にし、kill の要求として記録する。
        """
        raise NotImplementedError

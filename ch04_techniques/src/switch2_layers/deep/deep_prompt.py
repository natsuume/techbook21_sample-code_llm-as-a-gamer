"""deep (Claude Code) に渡すプロンプトを、パッケージの中のプロンプトのファイルとジョブごとの値から作る。

プロンプトのファイル (パッケージの switch2_layers/deep/prompts/ に置く。UTF-8 の Markdown):
    common.md      共通の部分 (役割・今回のジョブ・対象の文書・読んでよいもの・文書の書き方の手順・守ること)。
                   ${kind_instructions} の位置に種類ごとの指示を入れる
    controls.md    操作説明の指示
    tips.md        攻略情報の指示 (box_targets.md の書き方を含む)
    strategy.md    大方針の指示
    advice.md      詰まったときの助言の指示
マップ (map) の指示は無い (map のジョブは Claude Code を起動しない。deep.claude_code_runner)。

値の埋め方: string.Template の ${名前} の形の置き場所に、PROMPT_VALUE_NAMES の値を入れる
(Template.substitute。足りない値があれば KeyError)。種類ごとの指示も同じ値で埋めてから ${kind_instructions} に入れる。
Markdown の { } はそのまま残る。
埋めるのは、コードが決めた値 (パス・時刻・場面 ID・きっかけ) だけにする。文書・記録・画面から読んだ文 (文書の中身、
場面名、middle の出力など) はプロンプトに入れず、deep にファイルとして読ませる (画面の文字や記録の文による指示の注入を、
プロンプトの指示と混ぜないため)。

文書の書き方の手順 (common.md に書く): deep は対象の文書の本体 (notes/ の中のファイル) を Edit・Write で直接編集する。
同じ文書のジョブが並行して動くことがあるので、書く直前に本体を読み直し、ほかのジョブの更新を取り込んでから書く。
"""

from dataclasses import dataclass
from pathlib import Path

from switch2_layers.deep.deep_types import DocumentKind

# プロンプトのファイルを置くパッケージの中のディレクトリー (importlib.resources.files("switch2_layers.deep") / PROMPTS_DIR)
PROMPTS_PACKAGE = "switch2_layers.deep"
PROMPTS_DIR = "prompts"
COMMON_PROMPT_FILE = "common.md"
# 種類ごとの指示のファイル。map は無い
KIND_PROMPT_FILES: dict[DocumentKind, str] = {
    DocumentKind.CONTROLS: "controls.md",
    DocumentKind.TIPS: "tips.md",
    DocumentKind.STRATEGY: "strategy.md",
    DocumentKind.ADVICE: "advice.md",
}
# 種類の日本語の名前 (プロンプトの ${kind_title})
KIND_TITLES: dict[DocumentKind, str] = {
    DocumentKind.CONTROLS: "操作説明",
    DocumentKind.TIPS: "攻略情報",
    DocumentKind.STRATEGY: "大方針",
    DocumentKind.ADVICE: "詰まったときの助言",
}
# 時刻や場面が無いときにプロンプトに書く文字列
NOT_AVAILABLE_TEXT = "なし"
# 対象の文書が無いときに ${documents} に書く文字列
NO_DOCUMENTS_TEXT = "(対象の文書は無い)"
# プロンプトのファイルの ${名前} に入れる値の名前。prompt_values はちょうどこの名前の辞書を返す
PROMPT_VALUE_NAMES: tuple[str, ...] = (
    "kind_title",
    "trigger",
    "scene_id",
    "run_dir",
    "documents",
    "frames_log",
    "frames_dir",
    "previous_latest_frame_t",
    "latest_frame_t",
    "surface_log",
    "middle_state",
    "notes_dir",
    "scene_labels",
)
# 種類ごとの指示を入れる置き場所の名前 (common.md だけにある)
KIND_INSTRUCTIONS_NAME = "kind_instructions"


@dataclass(frozen=True)
class PromptDocument:
    """プロンプトに並べる対象の文書 1 つ。

    note   notes/ からの / 区切りの相対パス (job.json の documents の要素)
    path   本体のパス (<実行ディレクトリー>/notes/<note>)。deep が Edit・Write で直接編集する。まだ無い文書は Write で作る
    """

    note: str
    path: Path


@dataclass(frozen=True)
class DeepPromptContext:
    """プロンプトに埋める、1 つのジョブの値。

    kind                      文書の種類 (map 以外)
    trigger                   きっかけ (job.json の trigger。DeepTrigger の値)
    scene_id                  場面 ID (job.json の scene_id)。無ければ None
    run_dir                   実行ディレクトリー (シンボリックリンクを解決した絶対パス)
    documents                 対象の文書 (job.json の documents の順)
    previous_latest_frame_t   前回の同じ文書のジョブに渡した最新のフレームの時刻 (job.json)。無ければ None
    latest_frame_t            今回の最新のフレームの時刻 (job.json)。無ければ None
    """

    kind: DocumentKind
    trigger: str
    scene_id: str | None
    run_dir: Path
    documents: tuple[PromptDocument, ...]
    previous_latest_frame_t: float | None
    latest_frame_t: float | None

    @property
    def notes(self) -> tuple[str, ...]:
        """対象の文書の notes/ からの相対パスを、documents の順に返す。"""
        raise NotImplementedError


def load_prompt_template(file_name: str) -> str:
    """パッケージの prompts/ のファイル file_name を UTF-8 で読んで返す (importlib.resources で読む)。

    無いファイルなら FileNotFoundError をそのまま出す。
    """
    raise NotImplementedError


def read_prompt_context(job_dir: Path) -> DeepPromptContext:
    """ジョブのディレクトリー job_dir の job.json を読み、DeepPromptContext を作って返す。

    - job_dir をシンボリックリンクを解決した絶対パスにし、実行ディレクトリーは
      deep_job_directory.run_directory_of_job(解決した job_dir) とする
    - kind・trigger・scene_id・previous_latest_frame_t・latest_frame_t は job.json の値 (時刻は数か null)
    - documents は job.json の documents (notes/ からの相対パスの文字列のリスト) の順に、
      PromptDocument(note, 実行ディレクトリー / run_directory.NOTES_DIR / note) にする。
      note は claude_code_command.validate_document_note で確かめる
    次のときは ValueError にする (claude_code_command.RulePathError も ValueError):
        - job.json が読めない (deep_job_directory.read_job_file の ValueError)
        - kind が DocumentKind の値でないか map。trigger が DeepTrigger の値でない
        - scene_id が null でも場面 ID の形 (note_paths.is_scene_id) の文字列でもない
        - previous_latest_frame_t・latest_frame_t が null でも数 (真偽値を除く) でもない
        - documents がリストでないか、要素が文字列でない
        - note が validate_document_note で RulePathError
        - job_dir がジョブのディレクトリーの形でない (run_directory_of_job の ValueError)
    """
    raise NotImplementedError


def format_frame_time(t: float | None) -> str:
    """フレームの時刻をプロンプトに書く形にする。None なら NOT_AVAILABLE_TEXT、数なら `<小数第 3 位まで> 秒` (例: 12.345 秒)。"""
    raise NotImplementedError


def format_documents(documents: tuple[PromptDocument, ...]) -> str:
    """対象の文書の一覧を、プロンプトの ${documents} に入れる Markdown にする。文書ごとに次の 2 行 (documents の順):

        - notes/<note>
          - パス: <path>
    文書が無ければ NO_DOCUMENTS_TEXT の 1 行にする。行は改行 1 つでつなぎ、末尾に改行を付けない。
    """
    raise NotImplementedError


def prompt_values(context: DeepPromptContext) -> dict[str, str]:
    """プロンプトのファイルの ${名前} に入れる値の辞書 (キーは PROMPT_VALUE_NAMES と同じ) を返す。

        kind_title                KIND_TITLES[kind]
        trigger                   context.trigger
        scene_id                  context.scene_id (None なら NOT_AVAILABLE_TEXT)
        run_dir                   実行ディレクトリー
        documents                 format_documents(context.documents)
        frames_log                実行ディレクトリー/frames.jsonl
        frames_dir                実行ディレクトリー/frames
        previous_latest_frame_t   format_frame_time(context.previous_latest_frame_t)
        latest_frame_t            format_frame_time(context.latest_frame_t)
        surface_log               実行ディレクトリー/surface.jsonl
        middle_state              実行ディレクトリー/middle/state.json
        notes_dir                 実行ディレクトリー/notes
        scene_labels              実行ディレクトリー/notes/scene_labels.json
    パスは絶対パスの文字列 (Path.as_posix)。実行ディレクトリーの中の配置は run_directory.RunPaths に合わせる。
    """
    raise NotImplementedError


def build_deep_prompt(context: DeepPromptContext) -> str:
    """context の種類のプロンプトを作って返す。

    1. 種類ごとの指示 (KIND_PROMPT_FILES[kind]) を load_prompt_template で読み、prompt_values(context) で埋める
    2. 共通の部分 (COMMON_PROMPT_FILE) を、prompt_values(context) と kind_instructions (1 の結果) で埋める
    どちらも string.Template.substitute で埋める (値が足りなければ KeyError)。kind が map なら ValueError にする。
    結果の先頭は / でも - でもない (claude -p の引数として、スラッシュコマンドやオプションに読まれない)。
    """
    raise NotImplementedError

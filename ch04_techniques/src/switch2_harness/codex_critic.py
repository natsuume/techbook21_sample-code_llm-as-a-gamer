"""reflection_critic.CriticAgent の OpenAI Codex CLI (codex exec --json) の実装と、Codex の批評役に渡すプロンプト。

Codex のプレイヤーには Codex の批評役を使う。Claude Code の批評役との違いは次のとおり:
    - 撮影は、画像のファイルを codex exec の -i で添付する (codex_command.build_codex_critic_args)。プロンプトの文には、
      撮影の見出しの一覧と、画像をその順に添付したことを書く (codex_critic_prompt)
    - プロンプトは標準入力で渡す (位置引数のプロンプトは付けない)
    - シェルのコマンドと画像を開くツールを止め、プレイヤーの rules を読ませず (--ignore-rules)、どこにも書けない
      権限プロファイルで動かす。会話は残さない (--ephemeral)
    - プレイヤーの発言は agent.jsonl の agent_message (codex_log.agent_message_texts)、批評は最後のターンが成功して
      終わったときの最後の agent_message (codex_log.final_agent_message)
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from switch2_harness.codex_command import build_codex_critic_args, build_codex_env
from switch2_harness.codex_log import agent_message_texts, final_agent_message
from switch2_harness.reflection import SHOTS_HEADER, frame_label, records_text
from switch2_harness.reflection_critic import CriticCommand, CriticRequest

# 撮影の見出しの一覧の後に書く、添付した画像の説明
ATTACHED_IMAGES_NOTE = "撮影の画像は、この一覧の順に、このメッセージに添付しています。"


def codex_critic_prompt(request: CriticRequest) -> str:
    """Codex の批評役に標準入力で渡すプロンプトを返す (末尾は改行 1 つ)。

    次の 3 つを空行 1 つ ("\\n\\n") でつなぐ:
        1. request.instructions の末尾の改行を除いたもの
        2. 撮影の節: request.frames があれば
           `<reflection.SHOTS_HEADER>\\n\\n<frame_label(i, frame) を "\\n" でつないだもの>\\n\\n<ATTACHED_IMAGES_NOTE>`。
           無ければ `<SHOTS_HEADER>\\n\\n(なし)`
        3. reflection.records_text(action_lines, statements, notes_text) (Claude Code の批評役と同じ文)
    """
    if request.frames:
        labels = "\n".join(
            frame_label(i, frame) for i, frame in enumerate(request.frames, start=1)
        )
        shots = f"{SHOTS_HEADER}\n\n{labels}\n\n{ATTACHED_IMAGES_NOTE}"
    else:
        shots = f"{SHOTS_HEADER}\n\n(なし)"
    parts = [
        request.instructions.rstrip("\n"),
        shots,
        records_text(request.action_lines, request.statements, request.notes_text),
    ]
    return "\n\n".join(parts) + "\n"


@dataclass(frozen=True)
class CodexCritic:
    """CriticAgent の Codex の実装。codex_home は専用の CODEX_HOME (プレイヤーと同じもの)。"""

    codex_home: Path

    def statements(self, agent_log_lines: Sequence[str]) -> list[str]:
        """codex_log.agent_message_texts(agent_log_lines) を返す。"""
        return agent_message_texts(agent_log_lines)

    def build_command(self, request: CriticRequest) -> CriticCommand:
        """CriticCommand(build_codex_critic_args(request.frame_paths), codex_critic_prompt(request)) を返す。"""
        return CriticCommand(
            build_codex_critic_args(request.frame_paths), codex_critic_prompt(request)
        )

    def build_env(
        self, base_env: Mapping[str, str], workspace_root: Path
    ) -> dict[str, str]:
        """build_codex_env(base_env, self.codex_home, workspace_root) を返す。"""
        return build_codex_env(base_env, self.codex_home, workspace_root)

    def critique(self, output_lines: Sequence[str]) -> str | None:
        """codex_log.final_agent_message(output_lines) を返す。"""
        return final_agent_message(output_lines)

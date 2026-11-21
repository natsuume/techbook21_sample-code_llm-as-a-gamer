from pathlib import Path

import pytest

from switch2_layers.buttons import ALL_BUTTONS
from switch2_layers.hardware.pad_output import PadOutput
from switch2_layers.orchestrator.codex_instructions import (
    CODEX_FRAMES_GUIDE_TEXT,
    codex_delegate_instructions,
    codex_orchestrator_instructions,
)
from switch2_layers.orchestrator.codex_main_loop_session import (
    CodexMainLoopSessionFactory,
)
from switch2_layers.orchestrator.orchestrator_assembly import (
    OrchestratorLoopParts,
    build_orchestrator_loops,
)
from switch2_layers.orchestrator.orchestrator_instructions import (
    delegate_instructions,
    orchestrator_instructions,
)
from switch2_layers.orchestrator.turn_input import FRAMES_GUIDE_TEXT
from switch2_layers.records.run_log import RunLog
from switch2_layers.run_clock import RunClock
from switch2_layers.run_directory import RunPaths
from switch2_layers.settings import AgentKind, RunMode, RunSettings


class NullSender:
    def set_report(self, report: bytes) -> str:
        return "ack"


class NullMonitor:
    def ask(self, *args, **kwargs):
        raise AssertionError("the monitor must not be asked")


def starter(args, **kwargs):
    raise AssertionError("no process is started while assembling")


def signal_group(pgid: int, sig: int) -> None:
    raise AssertionError("no signal is sent while assembling")


def loop_parts(tmp_path: Path) -> OrchestratorLoopParts:
    root = tmp_path / "runs" / "20261121-120000"
    root.mkdir(parents=True)
    clock = RunClock(monotonic=lambda: 5.0, origin=0.0)
    return OrchestratorLoopParts(
        paths=RunPaths(root),
        clock=clock,
        run_log=RunLog(root / "run.jsonl", clock),
        pad_output=PadOutput(NullSender(), ALL_BUTTONS, start_timer=False),
        latest_frame=lambda: None,
        latest_frames=lambda: (),
        monitor=NullMonitor(),
        initial_instruction=None,
        on_stop=lambda reason: None,
        start_process=starter,
        signal_process_group=signal_group,
        environ={"PATH": "/usr/bin"},
        switch_skills_dir=tmp_path / "skills",
        buffered_frames=lambda: (),
    )


@pytest.mark.parametrize(
    "mode, instructions",
    [
        (RunMode.ORCHESTRATOR, orchestrator_instructions),
        (RunMode.DELEGATE, delegate_instructions),
    ],
)
def test_claude_agent_leaves_the_main_loop_session_parts_at_their_defaults(
    tmp_path, mode, instructions
):
    loops = build_orchestrator_loops(loop_parts(tmp_path), RunSettings(mode=mode))
    parts = loops.main_loop.parts
    assert parts.instructions == instructions()
    assert parts.session_factory is None
    assert parts.skills_dir is None
    assert parts.frames_guide_text == FRAMES_GUIDE_TEXT


@pytest.mark.parametrize(
    "mode, instructions",
    [
        (RunMode.ORCHESTRATOR, codex_orchestrator_instructions),
        (RunMode.DELEGATE, codex_delegate_instructions),
    ],
)
def test_codex_agent_passes_the_codex_factory_skills_dir_and_texts(
    tmp_path, mode, instructions
):
    parts = loop_parts(tmp_path)
    settings = RunSettings(
        mode=mode,
        agent=AgentKind.CODEX,
        codex_model="gpt-6.1-sol",
        orchestrator_effort="high",
    )
    main_parts = build_orchestrator_loops(parts, settings).main_loop.parts
    factory = main_parts.session_factory
    assert isinstance(factory, CodexMainLoopSessionFactory)
    assert factory.paths == parts.paths
    assert factory.instructions == instructions()
    assert factory.model == "gpt-6.1-sol"
    assert factory.effort == "high"
    assert factory.environ == {"PATH": "/usr/bin"}
    assert factory.start_process is starter
    assert factory.signal_process_group is signal_group
    assert factory.clock() == 5.0
    assert main_parts.instructions == instructions()
    assert main_parts.skills_dir == parts.paths.root / ".agents" / "skills"
    assert main_parts.frames_guide_text == CODEX_FRAMES_GUIDE_TEXT

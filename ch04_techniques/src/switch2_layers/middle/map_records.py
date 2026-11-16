"""middle のマップの記録 middle/map_records.jsonl の行の組み立てと、記録の場所の名前の読み取り。

行の形 (1 行が middle の 1 回の実行。キーは MAP_RECORD_KEYS の順):
    {"t": 判断に使った画像の時刻, "scene_id": 場面 ID か null, "place_name": 文字列か null,
     "current_tile": 座標か null,
     "connections": [{"from": 文字列, "to": 文字列, "how": 文字列, "from_tile": 座標か null,
                      "to_tile": 座標か null}, ...],
     "observations": [{"kind": "exit" | "landmark" | "blocked", "place": 文字列, "description": 文字列,
                       "tile": 座標か null}, ...]}
座標は {"x": 整数, "y": 整数} (notes.map_document.tile_position_to_json の形)。
- t は、その回に middle に送った画像 (MiddleInput.frames) のうち最新のものの時刻 (frames.jsonl の時刻)
- scene_id は、その回の出力を書いたときに決まった場面の ID (middle/state.json に書いた scene_id)
- place_name は、出力の place_name の前後の空白を除いたもの。出力の place_name が None か、除いて空なら null
- current_tile は、出力の current_tile。place_name が null なら null (middle/state.json の current_tile と同じ規則)
- connections と observations は、出力の new_connections と new_observations を、出力の順に並べたもの
次の回は記録しない (build_map_record が None を返す):
    - 画像を送らなかった回 (MiddleInput.frames が空)
    - place_name が null で、connections と observations が両方とも空の回
middle の 1 回分の処理 (middle_loop.run_middle_step) は、出力を書いた後に build_map_record で行を作り、None でなければ
file_io.append_json_line で追記する。追記の失敗は middle の失敗として数えず、logging で警告を書いて続ける。
"""

from pathlib import Path

from switch2_layers.file_io import read_json_lines
from switch2_layers.middle.middle_types import MiddleInput, MiddleOutput

# middle/map_records.jsonl の行のキー (この順に書く)
MAP_RECORD_KEYS: tuple[str, ...] = (
    "t",
    "scene_id",
    "place_name",
    "current_tile",
    "connections",
    "observations",
)


def build_map_record(
    middle_input: MiddleInput, scene_id: str | None, output: MiddleOutput
) -> dict[str, object] | None:
    """middle の 1 回の実行の、middle/map_records.jsonl の行 (モジュールの docstring の形) を返す。I/O を扱わない。

    middle_input   その回の入力 (frames が、middle に送った画像)
    scene_id       その回の出力を書いたときに決まった場面の ID。場面が決まらなければ None
    output         その回の出力
    t は middle_input.frames の t の最大。current_tile は output.current_tile の座標 (place_name が null なら null)。
    connections の要素は {"from": from_place, "to": to_place, "how": how, "from_tile": from_tile,
    "to_tile": to_tile}、observations の要素は {"kind": kind の値 (文字列), "place": place,
    "description": description, "tile": tile} の順のキーにし、値は書かれたまま (前後の空白も除かない) にする。
    座標は notes.map_document.tile_position_to_json の形、None は null にする。
    記録しない回 (モジュールの docstring) は None を返す。current_tile は、記録するかの判断に使わない。
    """
    if not middle_input.frames:
        return None
    place_name = (output.place_name or "").strip() or None
    if (
        place_name is None
        and not output.new_connections
        and not output.new_observations
    ):
        return None
    return {
        "t": max(frame.t for frame in middle_input.frames),
        "scene_id": scene_id,
        "place_name": place_name,
        "connections": [
            {
                "from": connection.from_place,
                "to": connection.to_place,
                "how": connection.how,
            }
            for connection in output.new_connections
        ],
        "observations": [
            {
                "kind": observation.kind.value,
                "place": observation.place,
                "description": observation.description,
            }
            for observation in output.new_observations
        ],
    }


def read_recorded_place_names(path: Path) -> list[str]:
    """middle/map_records.jsonl の path から、行の place_name を書かれた順に返す。

    file_io.read_json_lines で読む (ファイルが無ければ空のリスト。書きかけの最後の行と、読めない行は読み飛ばす)。
    ファイルを読めない (OSError。ディレクトリーなど) ときも空のリストを返す (記録が読めなくても middle の実行を
    失敗にしない)。
    place_name が無い行・文字列でない行 (null を含む)・前後の空白を除くと空になる行は飛ばす。
    返す名前は前後の空白を除いたもの。重複は除かない (重複は middle.place_names.collect_place_names が除く)。
    """
    try:
        rows = read_json_lines(path)
    except OSError:
        return []
    names: list[str] = []
    for row in rows:
        place_name = row.get("place_name")
        if isinstance(place_name, str) and place_name.strip():
            names.append(place_name.strip())
    return names

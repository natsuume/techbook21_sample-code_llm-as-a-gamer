"""実行の記録を読む読み取り専用の API サーバー (FastAPI のアプリ) と、画面 (web/dist/) の配信。

ルート:
    GET /api/runs                              実行の一覧 (名前の新しい順)
    GET /api/runs/{run}                        summary
    GET /api/runs/{run}/requests?from=&to=     要求の区間
    GET /api/runs/{run}/requests/{n}           要求の詳細
    GET /api/runs/{run}/frames?from=&to=       フレームの判定の区間
    GET /api/runs/{run}/pad?from=&to=          送ったレポートの区間
    GET /api/runs/{run}/events?from=&to=       run.jsonl の出来事の区間
    GET /api/runs/{run}/notes/{version}        ゲームの情報の版
    GET /api/runs/{run}/advisor/{name}         advisor の実行
    GET /api/runs/{run}/files/{path}           実行のディレクトリーの thumbs/・frames/・advisor-*/ の下のファイル
    GET /                                      画面 (web/dist/ があれば配信し、無ければ画面のビルドの手順の文)
Host の名前 (ポートを除く) が ALLOWED_HOSTS 以外の要求は、どのルートも 400。
知らない実行、許していないパス、読めないファイル、無い番号・版・名前は 404。from・to が数でない、
有限でない (nan・inf) ときと、from が to より大きいときは 422。リクエストごとに記録を読み直し (キャッシュしない)、
runs/ には何も書かない。
"""

from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from run_dashboard.advisor_runs import AdvisorRun, build_advisor_runs
from run_dashboard.run_directory import (
    find_run_dir,
    list_advisor_files,
    list_run_names,
    resolve_run_file,
)
from run_dashboard.run_records import (
    RunRecords,
    load_run,
    read_advisor_stream,
    read_result_json,
)
from run_dashboard.run_views import (
    build_advisor_detail,
    build_event_items,
    build_frame_items,
    build_note_detail,
    build_pad_items,
    build_request_detail,
    build_request_items,
    build_run_list_item,
    build_summary,
)

# 画面のビルドの出力 (tools/run-dashboard/web/dist)。このファイル (src/run_dashboard/app.py) からの相対で求める
DEFAULT_WEB_DIST_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"
# 受け付ける要求の Host の名前 (ポートを除く)。これ以外の Host の要求は 400 にする。DNS rebinding で、
# ほかのサイトのページから記録を読まれないようにするため (待ち受けは 127.0.0.1 だけでも、ブラウザーは
# 攻撃者のドメインの名前を 127.0.0.1 に向け直されると、そのドメインの Host で要求を送る)
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
# files の API の、ファイルの拡張子 (小文字にしたもの) → content-type
FILE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json",
    ".jsonl": "text/plain; charset=utf-8",
}
# FILE_MEDIA_TYPES に無い拡張子のファイルの content-type
DEFAULT_FILE_MEDIA_TYPE = "application/octet-stream"
# 画面のビルドの出力が無いときに / で返す文
BUILD_INSTRUCTIONS_TEXT = (
    "画面 (tools/run-dashboard/web/dist/) がまだありません。\n"
    "tools/run-dashboard/web で依存パッケージを入れて画面をビルドすると、ここで画面を配信します。\n"
    "API は /api/runs から使えます。\n"
)

# 区間の下限 (クエリーの from。Python の予約語のため、引数の名前は t_from にして alias で受け取る)
TimeFrom = Annotated[float | None, Query(alias="from", allow_inf_nan=False)]
# 区間の上限 (クエリーの to)
TimeTo = Annotated[float | None, Query(allow_inf_nan=False)]


def file_media_type(path: Path) -> str:
    """files の API で返すファイル path の content-type を、拡張子 (小文字にしたもの) で決めて返す。

    FILE_MEDIA_TYPES にあればその値、無ければ DEFAULT_FILE_MEDIA_TYPE。
    """
    return FILE_MEDIA_TYPES.get(path.suffix.lower(), DEFAULT_FILE_MEDIA_TYPE)


def check_time_range(t_from: float | None, t_to: float | None) -> None:
    """区間の from と to がどちらも指定されていて、from が to より大きければ HTTPException(422) を出す。"""
    if t_from is not None and t_to is not None and t_from > t_to:
        raise HTTPException(status_code=422, detail="from must not be greater than to")


def load_run_or_404(runs_dir: Path, run: str) -> RunRecords:
    """run_records.load_run(runs_dir, run) の記録を返す。知らない実行 (None) なら HTTPException(404) を出す。"""
    records = load_run(runs_dir, run)
    if records is None:
        raise HTTPException(status_code=404, detail=f"unknown run: {run}")
    return records


def load_advisor_runs(records: RunRecords) -> list[AdvisorRun]:
    """records の advisor の実行の一覧を、result.json を run_records.read_result_json で読んで返す。"""
    return build_advisor_runs(
        records.advisor_dirs, records.requests, records.run_events, read_result_json
    )


def create_app(runs_dir: Path, web_dist_dir: Path | None) -> FastAPI:
    """runs_dir の実行の記録を返す API のルートを登録した FastAPI のアプリを返す。

    Starlette の TrustedHostMiddleware(allowed_hosts=ALLOWED_HOSTS) を足し、Host の名前が ALLOWED_HOSTS 以外の
    要求は、どのルートも 400 にする。
    web_dist_dir があってディレクトリーなら、API のルートの後に、/ に StaticFiles(directory=web_dist_dir,
    html=True) を mount して画面を配信する (/ では index.html を返す)。無ければ (None か、ディレクトリーでなければ)、
    GET / で BUILD_INSTRUCTIONS_TEXT を text/plain で返す。CORS の設定は足さない。
    files の API は、ファイルを 1 回で読んだバイト列を Response(content=..., media_type=file_media_type(...)) で
    返す (Content-Length は返す本文の長さ)。FileResponse は Content-Length を stat の大きさで決めた後に
    ファイルの終わりまで読むため、送る間に伸びるファイル (書いている途中の stream.jsonl など) では、
    宣言した長さと本文の長さがずれる。読めなければ (OSError) 404。
    """
    app = FastAPI(title="run-dashboard")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

    @app.get("/api/runs")
    def get_runs() -> list[dict]:
        items = []
        for name in list_run_names(runs_dir):
            records = load_run(runs_dir, name)
            if records is not None:
                items.append(build_run_list_item(records))
        return items

    @app.get("/api/runs/{run}")
    def get_summary(run: str) -> dict:
        records = load_run_or_404(runs_dir, run)
        return build_summary(records, load_advisor_runs(records))

    @app.get("/api/runs/{run}/requests")
    def get_requests(run: str, t_from: TimeFrom = None, to: TimeTo = None) -> dict:
        check_time_range(t_from, to)
        return build_request_items(load_run_or_404(runs_dir, run), t_from, to)

    @app.get("/api/runs/{run}/requests/{n}")
    def get_request_detail(run: str, n: int) -> dict:
        records = load_run_or_404(runs_dir, run)
        detail = build_request_detail(records, n, load_advisor_runs(records))
        if detail is None:
            raise HTTPException(status_code=404, detail=f"unknown request: {n}")
        return detail

    @app.get("/api/runs/{run}/frames")
    def get_frames(run: str, t_from: TimeFrom = None, to: TimeTo = None) -> dict:
        check_time_range(t_from, to)
        return build_frame_items(load_run_or_404(runs_dir, run), t_from, to)

    @app.get("/api/runs/{run}/pad")
    def get_pad(run: str, t_from: TimeFrom = None, to: TimeTo = None) -> dict:
        check_time_range(t_from, to)
        return build_pad_items(load_run_or_404(runs_dir, run), t_from, to)

    @app.get("/api/runs/{run}/events")
    def get_events(run: str, t_from: TimeFrom = None, to: TimeTo = None) -> dict:
        check_time_range(t_from, to)
        return build_event_items(load_run_or_404(runs_dir, run), t_from, to)

    @app.get("/api/runs/{run}/notes/{version}")
    def get_note(run: str, version: int) -> dict:
        records = load_run_or_404(runs_dir, run)
        detail = build_note_detail(records, version, load_advisor_runs(records))
        if detail is None:
            raise HTTPException(status_code=404, detail=f"unknown version: {version}")
        return detail

    @app.get("/api/runs/{run}/advisor/{name}")
    def get_advisor(run: str, name: str) -> dict:
        records = load_run_or_404(runs_dir, run)
        advisor_dir = next(
            (item for item in records.advisor_dirs if item.name == name), None
        )
        if advisor_dir is None:
            raise HTTPException(status_code=404, detail=f"unknown advisor run: {name}")
        detail = build_advisor_detail(
            records,
            name,
            load_advisor_runs(records),
            read_advisor_stream(advisor_dir),
            list_advisor_files(advisor_dir),
        )
        if detail is None:
            raise HTTPException(status_code=404, detail=f"unknown advisor run: {name}")
        return detail

    @app.get("/api/runs/{run}/files/{path:path}")
    def get_run_file(run: str, path: str) -> Response:
        run_dir = find_run_dir(runs_dir, run)
        if run_dir is None:
            raise HTTPException(status_code=404, detail=f"unknown run: {run}")
        file_path = resolve_run_file(run_dir, path)
        if file_path is None:
            raise HTTPException(status_code=404, detail="file not found")
        try:
            content = file_path.read_bytes()
        except OSError:
            raise HTTPException(status_code=404, detail="file not readable") from None
        return Response(content=content, media_type=file_media_type(file_path))

    if web_dist_dir is not None and web_dist_dir.is_dir():
        # API のルートより後に mount する (先に登録したルートが優先される)
        app.mount("/", StaticFiles(directory=web_dist_dir, html=True), name="web")
    else:

        @app.get("/", response_class=PlainTextResponse)
        def get_build_instructions() -> str:
            return BUILD_INSTRUCTIONS_TEXT

    return app

// サーバー送信イベント (/live) で届いた記録を、TanStack Query のキャッシュ (summary・区間 API・advisor の実行の
// 詳細) の値に足す純粋な関数。渡した値は変えず、変わった値だけを新しいオブジェクトにして返す。
import type {
  AdvisorDetail,
  AdvisorStep,
  EventItem,
  FrameItem,
  LiveEnd,
  LiveEvent,
  PadItem,
  RangeResponse,
  RequestItem,
  RunSummary,
} from "../api/apiTypes.ts";

/** イベントを足すキャッシュの値。まだ読めていない (キャッシュに無い) ものは undefined */
export type LiveCaches = {
  summary: RunSummary | undefined;
  requests: RangeResponse<RequestItem> | undefined;
  frames: RangeResponse<FrameItem> | undefined;
  pad: RangeResponse<PadItem> | undefined;
  events: RangeResponse<EventItem> | undefined;
};

/**
 * 読み直すキャッシュ。
 * - summary: 実行の summary
 * - request: 要求 n の詳細
 * - advisor: advisor の実行 name の詳細
 * - note: ゲームの情報の版 version
 */
export type LiveInvalidation =
  | { type: "summary" }
  | { type: "request"; n: number }
  | { type: "advisor"; name: string }
  | { type: "note"; version: number };

/** applyLiveEvents の結果 */
export type LiveMergeResult = {
  /** イベントを足した後のキャッシュ。変わらなかったものは渡したオブジェクトのまま */
  caches: LiveCaches;
  /** 読み直すキャッシュ。同じものは 1 回だけ、最初に出た順 */
  invalidations: LiveInvalidation[];
  /** advisor_step を実行の名前ごとに、届いた順に分けたもの (name を除いた AdvisorStep) */
  advisorSteps: ReadonlyMap<string, AdvisorStep[]>;
  /** end が届いていればその data (複数なら最後)。届いていなければ null */
  end: LiveEnd | null;
};

/**
 * 届いた順のイベント events をキャッシュ caches に足す。
 * - 変わらなかったキャッシュは同じオブジェクトをそのまま返す。変わったキャッシュは新しいオブジェクト
 *   (items は新しい配列で、前の要素は同じオブジェクト)。渡したキャッシュは変えない
 * - request・frame・pad・run: requests・frames・pad・events の items の末尾に足す。available を true にし、
 *   reason を除く。cursor は変えない (cursor は区間 API を読み直したときにだけ変わる)。
 *   そのキャッシュが undefined なら、その種類のイベントは捨てる
 * - available が false から true に変わった種類があれば { type: "summary" } (questions・recorded・args などを
 *   読み直すため)
 * - run で event が "start" の行が届いたら { type: "summary" }
 * - request が 1 件以上届き、足す前の requests の items が空でなければ、足す前の最後の要求の
 *   { type: "request", n } (その要求の詳細を、decision の行が書かれる前に読んだかもしれないため)
 * - note_version: summary の note_versions の同じ version を置き換える。無ければ version の昇順の位置に挿す。
 *   置き換えて値が変わったら { type: "note", version }
 * - advisor_run: summary の advisor_runs の同じ name を置き換える。無ければ、役割の順 (ROLE_ORDER)、
 *   同じ役割の中は名前の末尾の連番 (数) の昇順の位置に挿す (連番を読めない名前はその役割の最後)。
 *   置き換えて値が変わったら { type: "advisor", name }
 * - advisor_step: キャッシュは変えず、advisorSteps に name ごとに足す
 * - stats: summary の t_end・counts・decide_s_median を置き換える (ほかのキーはそのまま)
 * - end: 結果の end に入れる (キャッシュは変えない)
 * - summary が undefined なら note_version・advisor_run・stats は捨てる
 */
export const applyLiveEvents = (
  _caches: LiveCaches,
  _events: readonly LiveEvent[],
): LiveMergeResult => {
  throw new Error("not implemented");
};

/**
 * advisor の実行の詳細 detail の steps の末尾に、steps を順に足す。
 * - detail.steps (と、この呼び出しで足したもの) に t・tool・summary が同じ step があれば足さない
 *   (kind は比べない。詳細を接続した後に読むと、届く step と重なるため)
 * - 1 件以上足したら steps_available を true にした新しいオブジェクトを返す。足さなければ detail をそのまま返す
 * - 渡した detail は変えない
 */
export const appendAdvisorSteps = (
  _detail: AdvisorDetail,
  _steps: readonly AdvisorStep[],
): AdvisorDetail => {
  throw new Error("not implemented");
};

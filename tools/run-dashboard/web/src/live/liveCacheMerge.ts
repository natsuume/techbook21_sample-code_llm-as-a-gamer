// サーバー送信イベント (/live) で届いた記録を、TanStack Query のキャッシュ (summary・区間 API) の値に足す純粋な関数と、
// advisor の実行の詳細の途中経過に、届いた途中経過を重なりを除いてつなぐ純粋な関数。渡した値は変えず、変わった値だけを
// 新しいオブジェクトにして返す。
import {
  type AdvisorStep,
  type EventItem,
  type FrameItem,
  type LiveEnd,
  type LiveEvent,
  type LiveStats,
  type PadItem,
  type RangeResponse,
  type RequestItem,
  ROLE_ORDER,
  type RunSummary,
  type SummaryAdvisorRun,
  type SummaryNoteVersion,
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

/** JSON の値 (null・真偽値・数・文字列・配列・object) が同じか */
const isJsonEqual = (left: unknown, right: unknown): boolean => {
  if (left === right) {
    return true;
  }
  if (typeof left !== "object" || typeof right !== "object" || left === null || right === null) {
    return false;
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => isJsonEqual(value, right[index]))
    );
  }
  const leftEntries = Object.entries(left);
  const rightRecord = Object.fromEntries(Object.entries(right));
  return (
    leftEntries.length === Object.keys(rightRecord).length &&
    leftEntries.every(
      ([key, value]) => Object.hasOwn(rightRecord, key) && isJsonEqual(value, rightRecord[key]),
    )
  );
};

/** 読み直すキャッシュを、同じものが 1 つになるように比べる文字列 */
const invalidationKey = (invalidation: LiveInvalidation): string => JSON.stringify(invalidation);

/** 区間 API の応答の items の末尾に足した応答 (available を true にし、reason を除く。cursor は変えない) */
const appendItems = <T>(
  range: RangeResponse<T> | undefined,
  items: readonly T[],
): RangeResponse<T> | undefined =>
  range === undefined || items.length === 0
    ? range
    : { available: true, items: [...range.items, ...items], cursor: range.cursor };

/** 役割の順の位置。知らない役割は最後 */
const roleRank = (run: Pick<SummaryAdvisorRun, "role">): number => {
  const index = ROLE_ORDER.indexOf(run.role);
  return index < 0 ? ROLE_ORDER.length : index;
};

/** 名前の末尾の連番 (数)。読めなければ Infinity (その役割の最後に並べる) */
const serialOf = (run: Pick<SummaryAdvisorRun, "name">): number => {
  const match = /(\d+)$/.exec(run.name);
  return match?.[1] === undefined ? Number.POSITIVE_INFINITY : Number(match[1]);
};

/** advisor の実行を、役割の順・同じ役割の中は連番の昇順に並べるときの比較 */
const compareAdvisorRuns = (left: SummaryAdvisorRun, right: SummaryAdvisorRun): number =>
  roleRank(left) - roleRank(right) || serialOf(left) - serialOf(right);

/**
 * 並んだ items の中の同じものを item に置き換え、無ければ並びの位置に挿した新しい配列を返す (replaced は置き換えて
 * 値が変わったか)。値が同じなら items をそのまま返す。same は existing が item と同じものか、
 * placedAfter は item を existing の後に並べるか (items は placedAfter が true の要素から先に並んでいる)
 */
const upsert = <T>(
  items: T[],
  item: T,
  same: (existing: T) => boolean,
  placedAfter: (existing: T) => boolean,
): { items: T[]; replaced: boolean } => {
  const index = items.findIndex(same);
  if (index >= 0) {
    return isJsonEqual(items[index], item)
      ? { items, replaced: false }
      : { items: items.with(index, item), replaced: true };
  }
  const position = items.findIndex((existing) => !placedAfter(existing));
  const next = [...items];
  next.splice(position < 0 ? next.length : position, 0, item);
  return { items: next, replaced: false };
};

/** summary に stats を入れた summary。値が同じなら summary をそのまま返す */
const withStats = (summary: RunSummary, stats: LiveStats): RunSummary =>
  summary.t_end === stats.t_end &&
  summary.decide_s_median === stats.decide_s_median &&
  isJsonEqual(summary.counts, stats.counts)
    ? summary
    : {
        ...summary,
        t_end: stats.t_end,
        counts: stats.counts,
        decide_s_median: stats.decide_s_median,
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
  caches: LiveCaches,
  events: readonly LiveEvent[],
): LiveMergeResult => {
  const invalidations: LiveInvalidation[] = [];
  const invalidated = new Set<string>();
  const invalidate = (invalidation: LiveInvalidation): void => {
    const key = invalidationKey(invalidation);
    if (!invalidated.has(key)) {
      invalidated.add(key);
      invalidations.push(invalidation);
    }
  };
  /** その種類に初めて足すとき、記録が無かった (available が false) なら summary を読み直す */
  const noteFirstItem = (range: RangeResponse<unknown>, addedCount: number): void => {
    if (addedCount === 0 && !range.available) {
      invalidate({ type: "summary" });
    }
  };

  const added = {
    requests: [] as RequestItem[],
    frames: [] as FrameItem[],
    pad: [] as PadItem[],
    events: [] as EventItem[],
  };
  let summary = caches.summary;
  const advisorSteps = new Map<string, AdvisorStep[]>();
  let end: LiveEnd | null = null;

  for (const event of events) {
    switch (event.kind) {
      case "request": {
        const requests = caches.requests;
        if (requests === undefined) {
          break;
        }
        noteFirstItem(requests, added.requests.length);
        const last = requests.items.at(-1);
        if (added.requests.length === 0 && last !== undefined) {
          invalidate({ type: "request", n: last.n });
        }
        added.requests.push(event.data);
        break;
      }
      case "frame":
        if (caches.frames !== undefined) {
          noteFirstItem(caches.frames, added.frames.length);
          added.frames.push(event.data);
        }
        break;
      case "pad":
        if (caches.pad !== undefined) {
          noteFirstItem(caches.pad, added.pad.length);
          added.pad.push(event.data);
        }
        break;
      case "run":
        if (caches.events !== undefined) {
          noteFirstItem(caches.events, added.events.length);
          if (event.data.event === "start") {
            invalidate({ type: "summary" });
          }
          added.events.push(event.data);
        }
        break;
      case "note_version": {
        if (summary === undefined) {
          break;
        }
        const version = event.data;
        const result = upsert<SummaryNoteVersion>(
          summary.note_versions,
          version,
          (existing) => existing.version === version.version,
          (existing) => existing.version < version.version,
        );
        if (result.items !== summary.note_versions) {
          summary = { ...summary, note_versions: result.items };
        }
        if (result.replaced) {
          invalidate({ type: "note", version: version.version });
        }
        break;
      }
      case "advisor_run": {
        if (summary === undefined) {
          break;
        }
        const run = event.data;
        const result = upsert<SummaryAdvisorRun>(
          summary.advisor_runs,
          run,
          (existing) => existing.name === run.name,
          (existing) => compareAdvisorRuns(existing, run) <= 0,
        );
        if (result.items !== summary.advisor_runs) {
          summary = { ...summary, advisor_runs: result.items };
        }
        if (result.replaced) {
          invalidate({ type: "advisor", name: run.name });
        }
        break;
      }
      case "advisor_step": {
        const { name, ...step } = event.data;
        const steps = advisorSteps.get(name);
        if (steps === undefined) {
          advisorSteps.set(name, [step]);
        } else {
          steps.push(step);
        }
        break;
      }
      case "stats":
        if (summary !== undefined) {
          summary = withStats(summary, event.data);
        }
        break;
      case "end":
        end = event.data;
        break;
    }
  }

  return {
    caches: {
      summary,
      requests: appendItems(caches.requests, added.requests),
      frames: appendItems(caches.frames, added.frames),
      pad: appendItems(caches.pad, added.pad),
      events: appendItems(caches.events, added.events),
    },
    invalidations,
    advisorSteps,
    end,
  };
};

/**
 * 1 つの接続で受け取った、summary 向けのイベントの最新の値 (上書き用の値)。ライブが起こした summary の読み直しの
 * 応答は、読み直しを始めた時点のサーバーの値で、その後に足した値を含まないことがあるため、読み直しの後に当て直す。
 * - advisorRuns: advisor_run の data を name ごとに (最後に届いた値)
 * - noteVersions: note_version の data を version ごとに (最後に届いた値)
 * - stats: 最後に届いた stats の data (届いていなければ null)
 */
export type SummaryOverrides = {
  advisorRuns: ReadonlyMap<string, SummaryAdvisorRun>;
  noteVersions: ReadonlyMap<number, SummaryNoteVersion>;
  stats: LiveStats | null;
};

/** 何も受け取っていない上書き用の値 */
export const EMPTY_SUMMARY_OVERRIDES: SummaryOverrides = {
  advisorRuns: new Map(),
  noteVersions: new Map(),
  stats: null,
};

/**
 * 上書き用の値 overrides に、届いた順のイベント events のうち summary 向けのもの (advisor_run・note_version・stats) を
 * 足した値を返す。advisor_run は name ごと、note_version は version ごとに後から届いた値で置き換え、stats は最後の
 * 1 つにする。summary 向けのイベントが無ければ overrides をそのまま返す。渡した値は変えない
 */
export const recordSummaryOverrides = (
  overrides: SummaryOverrides,
  events: readonly LiveEvent[],
): SummaryOverrides => {
  let advisorRuns: Map<string, SummaryAdvisorRun> | null = null;
  let noteVersions: Map<number, SummaryNoteVersion> | null = null;
  let stats = overrides.stats;
  let changed = false;
  for (const event of events) {
    switch (event.kind) {
      case "advisor_run":
        advisorRuns ??= new Map(overrides.advisorRuns);
        advisorRuns.set(event.data.name, event.data);
        changed = true;
        break;
      case "note_version":
        noteVersions ??= new Map(overrides.noteVersions);
        noteVersions.set(event.data.version, event.data);
        changed = true;
        break;
      case "stats":
        stats = event.data;
        changed = true;
        break;
      default:
        break;
    }
  }
  return changed
    ? {
        advisorRuns: advisorRuns ?? overrides.advisorRuns,
        noteVersions: noteVersions ?? overrides.noteVersions,
        stats,
      }
    : overrides;
};

/**
 * summary に上書き用の値 overrides を当て直した summary を返す。applyLiveEvents が summary に note_version・
 * advisor_run・stats を当てるのと同じ規則 (note_versions は version で置き換え・番号の昇順の位置に挿す、advisor_runs は
 * name で置き換え・役割の順と連番の昇順の位置に挿す、stats は t_end・counts・decide_s_median を置き換える) で、
 * note_version を番号の昇順に、advisor_run を届いた名前の順に、最後に stats を当てる。変わらなければ summary を
 * そのまま返す。渡した値は変えない
 */
export const applySummaryOverrides = (
  summary: RunSummary,
  overrides: SummaryOverrides,
): RunSummary => {
  const events: LiveEvent[] = [
    ...[...overrides.noteVersions.values()]
      .sort((left, right) => left.version - right.version)
      .map((data): LiveEvent => ({ kind: "note_version", data })),
    ...[...overrides.advisorRuns.values()].map(
      (data): LiveEvent => ({ kind: "advisor_run", data }),
    ),
    ...(overrides.stats === null ? [] : [{ kind: "stats", data: overrides.stats } as const]),
  ];
  if (events.length === 0) {
    return summary;
  }
  const { caches } = applyLiveEvents(
    { summary, requests: undefined, frames: undefined, pad: undefined, events: undefined },
    events,
  );
  return caches.summary ?? summary;
};

/** 2 つの step が、t・tool・summary で同じか (kind は比べない) */
const isSameStep = (left: AdvisorStep, right: AdvisorStep): boolean =>
  left.t === right.t && left.tool === right.tool && left.summary === right.summary;

/**
 * fetched の位置 start から後と streamed の先頭を、短い方の長さだけ要素ごとに比べて、すべて同じ step か
 * (start が fetched の長さなら、比べるものが無いため true)
 */
const matchesFrom = (
  fetched: readonly AdvisorStep[],
  streamed: readonly AdvisorStep[],
  start: number,
): boolean => {
  const length = Math.min(fetched.length - start, streamed.length);
  for (let index = 0; index < length; index += 1) {
    const fetchedStep = fetched[start + index];
    const streamedStep = streamed[index];
    if (
      fetchedStep === undefined ||
      streamedStep === undefined ||
      !isSameStep(fetchedStep, streamedStep)
    ) {
      return false;
    }
  }
  return true;
};

/**
 * advisor の実行の詳細の API の steps (fetched) と、ライブで届いた step を届いた順に並べたもの (streamed) を、
 * 重なりを除いてつないだ列を返す。
 * - 重なりの始まりは、fetched の位置 p (0 ≤ p ≤ fetched の長さ) のうち、fetched[p:] と streamed の先頭を短い方の
 *   長さ (min(fetched の長さ − p, streamed の長さ)) だけ要素ごとに t・tool・summary で比べて、すべて一致する最小の p
 *   (p が fetched の長さなら、いつも一致とみなす)。詳細はつないだ後に読み直すため、届いた step の先頭は詳細の途中か
 *   末尾にある。詳細を読み直すのが足し込みより後なら、詳細は届いた step より先に進んでいることがある
 * - 返す列は、fetched の後に streamed の (fetched の長さ − p) 件目より後を続けたもの (streamed が fetched[p:] に
 *   収まるなら fetched のまま)。重なった step は fetched のものを使う (kind は一致の判定に使わない。advisor が
 *   終わると、詳細の kind が permission_denied に変わりうるため)
 * - 同じ値の step が続いても、その数を保つ (同じ値の step をまとめない)
 * - 渡した配列は変えない
 */
export const mergeAdvisorSteps = (
  fetched: readonly AdvisorStep[],
  streamed: readonly AdvisorStep[],
): AdvisorStep[] => {
  let start = 0;
  while (start < fetched.length && !matchesFrom(fetched, streamed, start)) {
    start += 1;
  }
  return [...fetched, ...streamed.slice(fetched.length - start)];
};

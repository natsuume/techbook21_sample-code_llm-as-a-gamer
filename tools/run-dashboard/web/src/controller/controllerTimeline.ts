// コントローラー入力の時間の変化を、pad の記録 (送ったレポート) か要求の決めた操作から組み立てる純粋な関数。
// 再生位置の時刻の入力 (snapshot) と、タイムラインの入力のレーンに描く区間 (spans) を返す。
import type { EventItem, PadItem, PadSource, RequestItem } from "../api/apiTypes.ts";
import { indexRangeWithin, lastIndexAtOrBefore } from "../timeline/rangeSearch.ts";
import type { TimeRange } from "../timeline/viewWindow.ts";
import {
  BUTTON_NAMES,
  type ButtonName,
  type Operation,
  type PadState,
  parseOperation,
  parsePadState,
  type StickName,
  type StickValue,
} from "./operationText.ts";

/** 要求の操作の短押しを押している秒数 */
export const TAP_SECONDS = 0.12;
/** 押下 (hold) か短押し (tap) か */
export type InputKind = "hold" | "tap";
/** ある時刻のコントローラー入力。source は組み立てに使った記録 */
export type ControllerSnapshot = {
  buttons: { name: ButtonName; kind: InputKind }[];
  sticks: Partial<Record<StickName, StickValue>>;
  source: "pad" | "requests";
};
/** ボタンを押している区間 [start, end) */
export type ButtonSpan = { button: ButtonName; kind: InputKind; start: number; end: number };
/** スティックを倒している区間 [start, end)。magnitude は倒した大きさ (0〜1) */
export type StickSpan = { stick: StickName; start: number; end: number; magnitude: number };
export type InputSpans = { buttons: ButtonSpan[]; sticks: StickSpan[]; source: "pad" | "requests" };

/** スティックの名前。並びは区間の並べ方の順でもある */
const STICK_NAMES: readonly StickName[] = ["LS", "RS"];

type PressedButton = { name: ButtonName; kind: InputKind };
type PressedSticks = Partial<Record<StickName, StickValue>>;
/** ある時刻のボタンとスティックの入力 */
type InputState = { buttons: PressedButton[]; sticks: PressedSticks };
/** 入力が変わらない区間 [start, end) と、その間の入力 */
type InputSegment = InputState & { start: number; end: number };

const EMPTY_INPUT: InputState = { buttons: [], sticks: {} };

const buttonOrder = (name: ButtonName): number => BUTTON_NAMES.indexOf(name);

/** 倒しているスティックだけを返す ({ x: 0, y: 0 } は入れない) */
const tiltedSticks = (sticks: PressedSticks): PressedSticks => {
  const tilted: PressedSticks = {};
  for (const stick of STICK_NAMES) {
    const value = sticks[stick];
    if (value !== undefined && (value.x !== 0 || value.y !== 0)) {
      tilted[stick] = value;
    }
  }
  return tilted;
};

/** 入力を、呼び出し側が書き換えても覚えた値に影響しない新しいオブジェクトにして snapshot を作る */
const toSnapshot = (input: InputState, source: "pad" | "requests"): ControllerSnapshot => {
  const sticks: PressedSticks = {};
  for (const stick of STICK_NAMES) {
    const value = input.sticks[stick];
    if (value !== undefined) {
      sticks[stick] = { x: value.x, y: value.y };
    }
  }
  return { buttons: input.buttons.map((button) => ({ ...button })), sticks, source };
};

/** 昇順の sortedTimes のうち、t 以上の最初の値を返す (無ければ null) */
const firstTimeAtOrAfter = (sortedTimes: readonly number[], t: number): number | null => {
  const { start } = indexRangeWithin(sortedTimes, { from: t, to: Number.POSITIVE_INFINITY });
  return sortedTimes[start] ?? null;
};

/**
 * 入力が変わらない区間を start の昇順に受け取り、ボタンとスティックの区間にまとめる。
 * 長さが 0 の区間は飛ばす。隣り合う (前の end === 次の start) 同じボタン・同じ kind の区間と、同じスティック・
 * 同じ magnitude の区間を 1 つにつなぐ。並びは start の昇順、同じ start はボタンの BUTTON_NAMES の順
 * (スティックは LS、RS の順)
 */
const spansFromSegments = (
  segments: Iterable<InputSegment>,
  source: "pad" | "requests",
): InputSpans => {
  const buttons: ButtonSpan[] = [];
  const sticks: StickSpan[] = [];
  const lastButtonSpans = new Map<ButtonName, ButtonSpan>();
  const lastStickSpans = new Map<StickName, StickSpan>();
  for (const segment of segments) {
    if (!(segment.end > segment.start)) {
      continue;
    }
    for (const { name, kind } of segment.buttons) {
      const previous = lastButtonSpans.get(name);
      if (previous !== undefined && previous.kind === kind && previous.end === segment.start) {
        previous.end = segment.end;
      } else {
        const span: ButtonSpan = { button: name, kind, start: segment.start, end: segment.end };
        buttons.push(span);
        lastButtonSpans.set(name, span);
      }
    }
    for (const stick of STICK_NAMES) {
      const value = segment.sticks[stick];
      if (value === undefined) {
        continue;
      }
      const magnitude = Math.min(1, Math.hypot(value.x, value.y));
      const previous = lastStickSpans.get(stick);
      if (
        previous !== undefined &&
        previous.magnitude === magnitude &&
        previous.end === segment.start
      ) {
        previous.end = segment.end;
      } else {
        const span: StickSpan = { stick, start: segment.start, end: segment.end, magnitude };
        sticks.push(span);
        lastStickSpans.set(stick, span);
      }
    }
  }
  buttons.sort(
    (left, right) =>
      left.start - right.start || buttonOrder(left.button) - buttonOrder(right.button),
  );
  sticks.sort(
    (left, right) =>
      left.start - right.start ||
      STICK_NAMES.indexOf(left.stick) - STICK_NAMES.indexOf(right.stick),
  );
  return { buttons, sticks, source };
};

// ---- pad の記録 ----

/** pad の使う行 1 行。buttons は BUTTON_NAMES の順で、ボタンごとに tap か hold かを決めたもの */
type PadRow = InputState & { t: number; source: PadSource };
/** pad の使う行を t の昇順に並べたもの。times[i] は rows[i].t */
type PadTimeline = { times: number[]; rows: PadRow[] };

/** pad の配列ごとの、使う行と短押しの判定の結果 */
const padTimelineCache = new WeakMap<readonly PadItem[], PadTimeline>();

/**
 * pad の使う行 (ok === true、t !== null、状態を解釈できる行) を t の昇順 (同じ t は配列の順) に並べ、
 * 行ごとのボタンの tap と hold を決める。行を後ろから 1 回走査し、ボタンごとに「次にそのボタンが状態から
 * 消える行の source」を持つ (O(行数 × ボタン数))
 */
const buildPadTimeline = (pad: readonly PadItem[]): PadTimeline => {
  // 同じ状態の文は何度も現れるため、解釈した結果を文ごとに覚えて使う
  const parsedStates = new Map<string, PadState | null>();
  const usedRows: { t: number; source: PadSource; state: PadState }[] = [];
  for (const item of pad) {
    if (!item.ok || item.t === null) {
      continue;
    }
    let state = parsedStates.get(item.state);
    if (state === undefined) {
      state = parsePadState(item.state);
      parsedStates.set(item.state, state);
    }
    if (state !== null) {
      usedRows.push({ t: item.t, source: item.source, state });
    }
  }
  // Array.prototype.sort は安定なため、同じ t の行は配列の順のまま残る
  usedRows.sort((left, right) => left.t - right.t);

  // nextDisappearance[i] は、いま見ている行より後で BUTTON_NAMES[i] が最初に状態から消える行の source
  const nextDisappearance: (PadSource | undefined)[] = BUTTON_NAMES.map(() => undefined);
  const reversedRows: PadRow[] = [];
  for (let index = usedRows.length - 1; index >= 0; index -= 1) {
    const row = usedRows[index];
    if (row === undefined) {
      continue;
    }
    const buttons: PressedButton[] = [];
    BUTTON_NAMES.forEach((name, buttonIndex) => {
      if (row.state.buttons.includes(name)) {
        const kind = nextDisappearance[buttonIndex] === "tap_release" ? "tap" : "hold";
        buttons.push({ name, kind });
      } else {
        // この行より前の行から見ると、この行の状態に無いボタンは、この行で消える
        nextDisappearance[buttonIndex] = row.source;
      }
    });
    reversedRows.push({
      t: row.t,
      source: row.source,
      buttons,
      sticks: tiltedSticks(row.state.sticks),
    });
  }
  const rows = reversedRows.reverse();
  return { times: rows.map((row) => row.t), rows };
};

/** pad の配列の使う行と短押しの判定の結果を、覚えていればそれを、無ければ求めて覚えて返す */
const padTimelineOf = (pad: readonly PadItem[]): PadTimeline => {
  const cached = padTimelineCache.get(pad);
  if (cached !== undefined) {
    return cached;
  }
  const timeline = buildPadTimeline(pad);
  padTimelineCache.set(pad, timeline);
  return timeline;
};

/**
 * pad の記録から、時刻 t のコントローラー入力を返す (source は "pad")。
 * - 使う行は ok === true、t !== null、parsePadState(state) !== null のもの。t の昇順 (同じ t は配列の順)
 * - 行 i の状態は [t_i, t_{i+1}) に効く。最初の行より前は何も押していない
 * - 行 i のボタン b は、使う行のうち行 i より後で b が最初に状態から消える行の source が tap_release なら tap、
 *   それ以外 (apply・neutral・stop_output の行で消える、または最後まで消えない) なら hold。第4章の短押しは
 *   apply か tap_start の行で始まり、後の apply をまたいでも、離す時刻の tap_release の行で消える。押下のボタンは
 *   tap_release の行では消えない
 * - スティックは押下として扱い、{ x: 0, y: 0 } は入れない
 * - pad の配列ごとに、使う行と tap・hold の判定の結果を WeakMap に覚えて再利用する。呼び出し側は、一度渡した
 *   配列の中身を変えない (中身が変わったときは新しい配列を渡す)
 */
export const controllerAtFromPad = (pad: readonly PadItem[], t: number): ControllerSnapshot => {
  const { times, rows } = padTimelineOf(pad);
  const row = rows[lastIndexAtOrBefore(times, t)];
  return toSnapshot(row ?? EMPTY_INPUT, "pad");
};

/**
 * 選んだ要求の操作を pad の記録で示す時刻を返す (上の段のコントローラー入力に使う)。
 * 使う行 (controllerAtFromPad と同じ選び方) のうち、source が apply で position <= t < nextRequestTime の最初の
 * 行の t を返す (nextRequestTime が null なら上限を設けない)。該当する行が無ければ position を返す。
 * 第4章は要求の応答を受けた後に操作を送り、pad の行の時刻は送信が戻った後の時刻のため、再生位置 (要求の受信時刻)
 * では pad の記録にまだ前の操作が出ている。position は選んだ要求の再生位置、nextRequestTime は次の要求の再生位置。
 * 使う行は controllerAtFromPad と同じく pad の配列ごとに覚えて再利用する (配列の中身を変えずに渡す前提)
 */
export const padSnapshotTime = (
  pad: readonly PadItem[],
  position: number,
  nextRequestTime: number | null,
): number => {
  const { times, rows } = padTimelineOf(pad);
  const { start } = indexRangeWithin(times, { from: position, to: Number.POSITIVE_INFINITY });
  for (let index = start; index < rows.length; index += 1) {
    const row = rows[index];
    if (row === undefined || (nextRequestTime !== null && row.t >= nextRequestTime)) {
      break;
    }
    if (row.source === "apply") {
      return row.t;
    }
  }
  return position;
};

/**
 * pad の記録から、range と重なる入力の区間を range に切り詰めて返す (source は "pad")。
 * 行の選び方・効く区間・tap と hold の区別は controllerAtFromPad と同じ。最後の行は tEnd まで効く。
 * スティックの magnitude は min(1, hypot(x, y))。
 * 隣り合う同じボタン・同じ kind の区間 (前の end === 次の start) は 1 つにつなぐ。スティックは同じ magnitude の
 * ときだけつなぐ。並びは start の昇順、同じ start はボタンの BUTTON_NAMES の順。
 * range に効く行だけを二分探索で選んで区間を作る。使う行と tap・hold の判定の結果は controllerAtFromPad と同じく
 * pad の配列ごとに覚えて再利用する (配列の中身を変えずに渡す前提)
 */
export const inputSpansFromPad = (
  pad: readonly PadItem[],
  range: TimeRange,
  tEnd: number,
): InputSpans => {
  const { times, rows } = padTimelineOf(pad);
  // range.from に効いている行 (無ければ最初の行) から、range.to 以前に始まる最後の行まで
  const firstIndex = Math.max(0, lastIndexAtOrBefore(times, range.from));
  const lastIndex = lastIndexAtOrBefore(times, range.to);
  const segments: InputSegment[] = [];
  for (let index = firstIndex; index <= lastIndex; index += 1) {
    const row = rows[index];
    if (row === undefined) {
      continue;
    }
    const rowEnd = times[index + 1] ?? tEnd;
    segments.push({
      start: Math.max(row.t, range.from),
      end: Math.min(rowEnd, range.to),
      buttons: row.buttons,
      sticks: row.sticks,
    });
  }
  return spansFromSegments(segments, "pad");
};

// ---- 要求の記録 ----

/**
 * 第4章が中立のレポートを送る前後に run.jsonl に書く出来事。判断の失敗・キャプチャーの停止・接続の復旧待ちは
 * 中立に戻した直後に書く。割り込み (interrupted) は書いた直後に中立に戻し、接続の失敗 (pipeline_error) は
 * 書いた後に判断のループを抜け、終了処理で中立に戻す
 */
export const NEUTRAL_EVENTS: readonly string[] = [
  "decision_error",
  "capture_stopped",
  "bridge_wait",
  "interrupted",
  "pipeline_error",
];

/**
 * events から、第4章が中立に戻した時刻 (event が NEUTRAL_EVENTS の行の t) を昇順で返す。
 * 要求の記録は中立に戻した時刻を持たないため、要求から入力を組み立てるときにこの時刻を使う
 */
export const neutralTimesFromEvents = (events: readonly EventItem[]): number[] =>
  events
    .filter((event) => NEUTRAL_EVENTS.includes(event.event))
    .map((event) => event.t)
    .sort((left, right) => left - right);

/** 使う要求 1 件。held は押下のボタン (短押しにもあるボタンを除く)、taps は短押しのボタン */
type UsedRequest = {
  received: number;
  held: ButtonName[];
  sticks: PressedSticks;
  taps: ButtonName[];
};
/** 使う要求を受信時刻の昇順に並べたもの。times[i] は requests[i].received */
type RequestTimeline = { times: number[]; requests: UsedRequest[] };

/** 要求の配列ごとの、使う要求と解釈した操作 */
const requestTimelineCache = new WeakMap<readonly RequestItem[], RequestTimeline>();

/**
 * 使う要求 (result === "ok"、t_received !== null、操作の文を解釈できる要求) を、受信時刻の昇順 (同じ時刻は
 * 配列の順) に並べる。押下と短押しの両方にあるボタンは短押しにだけ入れる
 */
const buildRequestTimeline = (requests: readonly RequestItem[]): RequestTimeline => {
  // 同じ操作の文は何度も現れるため、解釈した結果を文ごとに覚えて使う
  const parsedActions = new Map<string, Operation | null>();
  const used: UsedRequest[] = [];
  for (const request of requests) {
    if (request.result !== "ok" || request.t_received === null || request.action === null) {
      continue;
    }
    let operation = parsedActions.get(request.action);
    if (operation === undefined) {
      operation = parseOperation(request.action);
      parsedActions.set(request.action, operation);
    }
    if (operation !== null) {
      const taps = operation.taps;
      used.push({
        received: request.t_received,
        held: operation.held.buttons.filter((name) => !taps.includes(name)),
        sticks: tiltedSticks(operation.held.sticks),
        taps,
      });
    }
  }
  // Array.prototype.sort は安定なため、同じ受信時刻の要求は配列の順のまま残る
  used.sort((left, right) => left.received - right.received);
  return { times: used.map((request) => request.received), requests: used };
};

/** 要求の配列の使う要求を、覚えていればそれを、無ければ求めて覚えて返す */
const requestTimelineOf = (requests: readonly RequestItem[]): RequestTimeline => {
  const cached = requestTimelineCache.get(requests);
  if (cached !== undefined) {
    return cached;
  }
  const timeline = buildRequestTimeline(requests);
  requestTimelineCache.set(requests, timeline);
  return timeline;
};

/**
 * 使う要求 index の押下とスティックが効く終わり (この時刻を含まない) を返す。次の使う要求の受信時刻と、
 * 受信時刻以上の最初の中立に戻す時刻の早いほう。どちらも無ければ openEnd
 */
const heldEndOf = (
  timeline: RequestTimeline,
  index: number,
  neutralTimes: readonly number[],
  openEnd: number,
): number => {
  const received = timeline.times[index] ?? Number.POSITIVE_INFINITY;
  const nextReceived = timeline.times[index + 1] ?? Number.POSITIVE_INFINITY;
  const neutral = firstTimeAtOrAfter(neutralTimes, received) ?? Number.POSITIVE_INFINITY;
  const end = Math.min(nextReceived, neutral);
  return end === Number.POSITIVE_INFINITY ? openEnd : end;
};

/**
 * 使う要求の短押しが効く終わり (この時刻を含まない) を返す。受信時刻 + TAP_SECONDS と、受信時刻以上の最初の
 * 中立に戻す時刻の早いほう
 */
const tapEndOf = (request: UsedRequest, neutralTimes: readonly number[]): number =>
  Math.min(
    request.received + TAP_SECONDS,
    firstTimeAtOrAfter(neutralTimes, request.received) ?? Number.POSITIVE_INFINITY,
  );

/**
 * 要求の操作から、時刻 t の入力を求める。最後の使う要求の押下は、その後に中立に戻す時刻が無ければ openEnd の
 * 前まで効く。短押しは、受信時刻 + TAP_SECONDS が t より後の要求をすべて見て決める
 */
const requestInputAt = (
  timeline: RequestTimeline,
  neutralTimes: readonly number[],
  t: number,
  openEnd: number,
): InputState => {
  const currentIndex = lastIndexAtOrBefore(timeline.times, t);
  const tapped = new Set<ButtonName>();
  for (let index = currentIndex; index >= 0; index -= 1) {
    const request = timeline.requests[index];
    // 受信時刻は昇順のため、ここで短押しが終わっていれば、それより前の要求の短押しも終わっている
    if (request === undefined || request.received + TAP_SECONDS <= t) {
      break;
    }
    if (t < tapEndOf(request, neutralTimes)) {
      for (const name of request.taps) {
        tapped.add(name);
      }
    }
  }
  const current = timeline.requests[currentIndex];
  const holding =
    current !== undefined && t < heldEndOf(timeline, currentIndex, neutralTimes, openEnd)
      ? current
      : null;
  const held = new Set(holding?.held ?? []);
  const buttons: PressedButton[] = [];
  for (const name of BUTTON_NAMES) {
    if (tapped.has(name)) {
      buttons.push({ name, kind: "tap" });
    } else if (held.has(name)) {
      buttons.push({ name, kind: "hold" });
    }
  }
  return { buttons, sticks: holding?.sticks ?? {} };
};

/**
 * 要求の決めた操作から、時刻 t のコントローラー入力を返す (source は "requests")。
 * - 使う要求は result === "ok"、t_received !== null、parseOperation(action) !== null のもの。ほかの要求は飛ばす
 * - neutralTimes は中立に戻した時刻 (neutralTimesFromEvents の結果。昇順)
 * - 使う要求 i の押下とスティックは、受信時刻 tr_i から、その後で最初に来る「次の使う要求の受信時刻」か
 *   「tr_i 以上の neutralTimes の時刻」の前まで効く。短押しは、tr_i + TAP_SECONDS と、tr_i 以上の最初の
 *   neutralTimes の時刻の、早いほうの前まで効く (第4章の中立は短押しも消す。次の要求では消えない)
 * - 中立に戻す時刻が tr_i と同じなら、要求 i の押下・スティック・短押しは効かない (サーバーは run.jsonl の時刻を
 *   decision の行の受信時刻を起点に求めるため、要求 i の後に同じミリ秒で書いた出来事は tr_i と同じ時刻になる)
 * - 同じボタンが押下と短押しの両方にあれば短押しとして扱う
 * - events の時刻は run.jsonl の壁時計から求めるため、要求の時刻と数十ミリ秒ずれることがある
 * - pad の記録が無い実行のための近似で、実際に送ったレポートとは一致しない (操作を送るまでの遅れ、短押しの
 *   開始を待つ間隔、送信の失敗は再現しない)。画面は source で組み立てに使った記録を表示する
 * - 要求の配列ごとに、使う要求と解釈した操作を WeakMap に覚えて再利用する。呼び出し側は、一度渡した配列の
 *   中身を変えない (中身が変わったときは新しい配列を渡す)
 */
export const controllerAtFromRequests = (
  requests: readonly RequestItem[],
  neutralTimes: readonly number[],
  t: number,
): ControllerSnapshot =>
  toSnapshot(
    requestInputAt(requestTimelineOf(requests), neutralTimes, t, Number.POSITIVE_INFINITY),
    "requests",
  );

/**
 * 要求の決めた操作から、range と重なる入力の区間を range に切り詰めて返す (source は "requests")。
 * 要求の選び方・中立に戻す時刻・効く区間・短押しの扱いは controllerAtFromRequests と同じ。最後の使う要求の押下は、
 * その後に中立に戻す時刻が無ければ tEnd まで効く。
 * つなぎ方と並びは inputSpansFromPad と同じ。
 * range に効く要求だけを二分探索で選んで区間を作る。使う要求と解釈した操作は controllerAtFromRequests と同じく
 * 要求の配列ごとに覚えて再利用する (配列の中身を変えずに渡す前提)
 */
export const inputSpansFromRequests = (
  requests: readonly RequestItem[],
  neutralTimes: readonly number[],
  range: TimeRange,
  tEnd: number,
): InputSpans => {
  const timeline = requestTimelineOf(requests);
  const { times } = timeline;
  // range.from に押下が効いている要求 (無ければ最初の要求) と、range.from に短押しが残っている前の要求から、
  // range.to 以前に受信した最後の要求まで
  let firstIndex = Math.max(0, lastIndexAtOrBefore(times, range.from));
  for (
    let previous = times[firstIndex - 1];
    previous !== undefined && previous + TAP_SECONDS > range.from;
    previous = times[firstIndex - 1]
  ) {
    firstIndex -= 1;
  }
  const lastIndex = lastIndexAtOrBefore(times, range.to);

  // 入力が変わりうる時刻 (受信時刻・押下の終わり・短押しの終わり) で range を区切る
  const boundaries = new Set<number>([range.from, range.to]);
  const addBoundary = (time: number): void => {
    if (time > range.from && time < range.to) {
      boundaries.add(time);
    }
  };
  for (let index = firstIndex; index <= lastIndex; index += 1) {
    const request = timeline.requests[index];
    if (request === undefined) {
      continue;
    }
    addBoundary(request.received);
    addBoundary(heldEndOf(timeline, index, neutralTimes, tEnd));
    addBoundary(tapEndOf(request, neutralTimes));
  }
  const sortedBoundaries = [...boundaries].sort((left, right) => left - right);

  const segments: InputSegment[] = [];
  for (let index = 0; index + 1 < sortedBoundaries.length; index += 1) {
    const start = sortedBoundaries[index];
    const end = sortedBoundaries[index + 1];
    if (start !== undefined && end !== undefined) {
      segments.push({ start, end, ...requestInputAt(timeline, neutralTimes, start, tEnd) });
    }
  }
  return spansFromSegments(segments, "requests");
};

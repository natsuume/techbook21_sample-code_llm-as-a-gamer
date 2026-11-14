// run-dashboard のサーバーの API が返す JSON の型。
// 時刻 (t、t_ で始まるキー、peak_t、candidate_t) はすべて経過秒 (number)。

/**
 * 区間 API (requests・frames・pad・events) の応答。記録が無ければ available が false で items は空。
 * cursor は、その API が読んだファイルの、読んだ最後の完全な行の次のバイト位置 (ファイルが無ければ 0。
 * events の API が読むファイルは run.jsonl)
 */
export type RangeResponse<T> = {
  available: boolean;
  reason?: "old_format";
  items: T[];
  cursor: number;
};

export type RunListItem = {
  run: string;
  game: string | null;
  model: string | null;
  started: string | null;
  recorded: { dashboard: boolean; requests: boolean };
  in_progress: boolean;
};

export type QuestionKind = "button" | "stick_direction" | "stick_strength" | "advisor";
export type QuestionItem = {
  name: string;
  text: string;
  kind: QuestionKind;
  answer_type: "choice" | "probability";
  choices: { name: string; description: string }[];
};
export type RoleName = "controls" | "strategy" | "advice" | "tips";
export const ROLE_ORDER: readonly RoleName[] = ["controls", "strategy", "advice", "tips"];
export type NoteKind = "controls" | "tips" | "strategy" | "advice";
export const NOTE_KIND_ORDER: readonly NoteKind[] = ["controls", "tips", "strategy", "advice"];

/** summary の advisor の実行。summary の advisor_runs は役割の順に並び、時刻の順ではない */
export type SummaryAdvisorRun = {
  name: string;
  role: RoleName;
  request: number | null;
  t_start: number | null;
  t_end: number | null;
  returncode: number | null;
  denials: number | null;
  result: string | null;
};
/** ゲームの情報の 1 ファイルの書き換え。by は advisor の実行の名前か "external" */
export type NoteChange = { kind: NoteKind; by: string };
export type SummaryNoteVersion = {
  version: number;
  t: number;
  request: number;
  initial: boolean;
  changed: NoteChange[];
};

/** 実行の summary。questions の順が、要求の p の並び */
export type RunSummary = {
  run: string;
  game: string | null;
  model: string | null;
  started: string | null;
  in_progress: boolean;
  args: Record<string, unknown> | null;
  thresholds: { advisor: number };
  thresholds_assumed: boolean;
  valley_ratio: number;
  similarity_threshold: number;
  questions: QuestionItem[];
  old_format: boolean;
  t_end: number | null;
  counts: { requests: number; frames: number; pad: number };
  decide_s_median: number | null;
  advisor_runs: SummaryAdvisorRun[];
  note_versions: SummaryNoteVersion[];
  recorded: { dashboard: boolean; requests: boolean; advisor_steps: boolean };
  dropped: unknown;
  skipped_lines: Record<string, number>;
};

export type RequestResult = "ok" | "error" | "discarded";
/** 要求の区間 API の 1 件。失敗した要求 (result が "error") は t_received・action・p・adopted_count が null */
export type RequestItem = {
  n: number;
  t_sent: number;
  t_received: number | null;
  result: RequestResult;
  action: string | null;
  advisor_requested: string[];
  advisor_accepted: string[];
  p: (number | null)[] | null;
  adopted_count: number | null;
};
export type AdoptedValue =
  | "hold"
  | "tap"
  | "rejected"
  | "left"
  | "right"
  | "up"
  | "down"
  | "requested"
  | null;
/** 要求に載せた画像。thumb と file は、そのまま files の API のパスに渡せる */
export type RequestImage = {
  label: string;
  t: number;
  bytes: number;
  thumb: string | null;
  file: string | null;
};
export type RequestDetail = RequestItem & {
  images: RequestImage[];
  timings: {
    select_s: number | null;
    encode_s: number | null;
    decide_s: number | null;
    apply_s: number | null;
  };
  payload_bytes: number | null;
  history_text: string;
  previous_operation_text: string | null;
  note_version: number | null;
  probabilities: Record<string, number> | null;
  choices: Record<string, Record<string, number>> | null;
  advisor_probabilities: Partial<Record<RoleName, number>> | null;
  adopted: Record<string, AdoptedValue> | null;
};
export type RequestDetailOldFormat = { available: false; reason: "old_format" };

export type FrameEvent =
  | null
  | { kind: "fall" }
  | {
      kind: "close";
      candidate_t: number | null;
      candidate_sharpness: number;
      kept: boolean;
      similar_diff: number | null;
    };
/** フレームの判定。thumb は、そのまま files の API のパスに渡せる */
export type FrameItem = {
  t: number | null;
  sharpness: number;
  diff: number | null;
  phase: "rising" | "falling";
  peak_t: number | null;
  peak_sharpness: number;
  ref: number;
  event: FrameEvent;
  thumb: string;
};

export type PadSource = "apply" | "tap_start" | "tap_release" | "neutral" | "stop_output";
export type PadItem = {
  t: number | null;
  source: PadSource;
  state: string;
  ok: boolean;
  error: string | null;
};

/** run.jsonl の decision 以外の行。t は負になることがあり、配列の順と t の順が食い違うことがある */
export type EventItem = { t: number; event: string; [key: string]: unknown };

export type NoteDetail = {
  version: number;
  t: number;
  changed: NoteChange[];
  files: Record<NoteKind, string> | null;
};

export type AdvisorStep = {
  t: number | null;
  kind: "tool_use" | "tool_result" | "permission_denied" | "result";
  tool: string | null;
  summary: string;
};
/** advisor の実行の詳細。files[].path は、そのまま files の API のパスに渡せる */
export type AdvisorDetail = {
  name: string;
  role: RoleName;
  request: number | null;
  t_start: number | null;
  t_end: number | null;
  status: "done" | "running";
  result_json: Record<string, unknown> | null;
  steps_available: boolean;
  steps: AdvisorStep[];
  files: { path: string; bytes: number }[];
};

/** /live の stats の data。summary の t_end・counts・decide_s_median と同じ値 */
export type LiveStats = Pick<RunSummary, "t_end" | "counts" | "decide_s_median">;
/** /live の advisor_step の data。advisor の実行の詳細の steps の 1 件に、advisor の実行の名前を足したもの */
export type LiveAdvisorStep = AdvisorStep & { name: string };
/** /live の end の data。record_end は run.jsonl に record_end の行があるか */
export type LiveEnd = { record_end: boolean };
/** /live のイベントの種類 → data の型 */
export type LiveEventDataOf = {
  request: RequestItem;
  frame: FrameItem;
  pad: PadItem;
  run: EventItem;
  note_version: SummaryNoteVersion;
  advisor_run: SummaryAdvisorRun;
  advisor_step: LiveAdvisorStep;
  stats: LiveStats;
  end: LiveEnd;
};
export type LiveEventKind = keyof LiveEventDataOf;
/** /live のイベントの種類のすべて */
export const LIVE_EVENT_KINDS: readonly LiveEventKind[] = [
  "request",
  "frame",
  "pad",
  "run",
  "note_version",
  "advisor_run",
  "advisor_step",
  "stats",
  "end",
];
/** /live の 1 件。kind で data の型が決まる */
export type LiveEvent = {
  [K in LiveEventKind]: { kind: K; data: LiveEventDataOf[K] };
}[LiveEventKind];
/**
 * /live のクエリーの位置 (それぞれ run.jsonl・requests.jsonl・frames.jsonl・pad.jsonl のバイト位置)。
 * null は省く (サーバーは接続した時点のファイルの終わりから送る)
 */
export type LiveCursors = {
  run: number | null;
  requests: number | null;
  frames: number | null;
  pad: number | null;
};

// 上の段のコントローラー入力に出す入力 (再生位置の時点の押しているボタンとスティック) を選び、文にする純粋な関数。
import type { PadItem, RangeResponse, RequestItem } from "../api/apiTypes.ts";
import {
  type ControllerSnapshot,
  controllerAtFromPad,
  controllerAtFromRequests,
  type InputKind,
  padSnapshotTime,
} from "./controllerTimeline.ts";
import type { ButtonName, StickName } from "./operationText.ts";

/** controllerAtPlayback に渡す記録と再生位置 */
export type PlaybackControllerInput = {
  pad: RangeResponse<PadItem>;
  requests: RangeResponse<RequestItem>;
  /** 中立に戻した時刻 (neutralTimesFromEvents の結果) */
  neutralTimes: readonly number[];
  /** 再生位置 */
  position: number;
  /** 選んでいる要求の次の要求の再生位置。無ければ null */
  nextRequestPosition: number | null;
};

const STICK_NAMES: readonly StickName[] = ["LS", "RS"];

/**
 * 上の段に出すコントローラー入力を返す。
 * - pad の記録がある (pad.available) なら controllerAtFromPad(pad.items, padSnapshotTime(pad.items, position,
 *   nextRequestPosition))。選んでいる要求の操作を送った時刻の入力
 * - そうでなく要求の記録がある (requests.available) なら controllerAtFromRequests(requests.items, neutralTimes,
 *   position)
 * - どちらも無ければ null (記録なし)
 * pad.items と requests.items は API の応答の配列をそのまま渡す (controllerTimeline の関数が配列ごとに結果を覚える)
 */
export const controllerAtPlayback = (input: PlaybackControllerInput): ControllerSnapshot | null => {
  if (input.pad.available) {
    const items = input.pad.items;
    return controllerAtFromPad(
      items,
      padSnapshotTime(items, input.position, input.nextRequestPosition),
    );
  }
  if (input.requests.available) {
    return controllerAtFromRequests(input.requests.items, input.neutralTimes, input.position);
  }
  return null;
};

/** 入力の文の部品。tap は短押しのボタン、hold は押下のボタン、sticks は倒しているスティック (いずれも並びは入力の順) */
export type ControllerParts = { tap: ButtonName[]; hold: ButtonName[]; sticks: StickName[] };

/** 入力を、短押しのボタン・押下のボタン・倒しているスティック ({ x: 0, y: 0 } は除く) に分ける */
export const controllerParts = (snapshot: ControllerSnapshot): ControllerParts => {
  const byKind = (kind: InputKind): ButtonName[] =>
    snapshot.buttons.filter((button) => button.kind === kind).map((button) => button.name);
  return {
    tap: byKind("tap"),
    hold: byKind("hold"),
    sticks: STICK_NAMES.filter((name) => {
      const value = snapshot.sticks[name];
      return value !== undefined && (value.x !== 0 || value.y !== 0);
    }),
  };
};

/**
 * 入力の文 "短押し <ボタン> · 押下 <ボタン> · スティック <スティック>" を返す。ボタンとスティックは ", " で
 * つなぐ。短押しと押下が無ければ "なし"、スティックが無ければ "中立"
 */
export const describeController = (snapshot: ControllerSnapshot): string => {
  const { tap, hold, sticks } = controllerParts(snapshot);
  const listOr = (names: readonly string[], empty: string): string =>
    names.length > 0 ? names.join(", ") : empty;
  return `短押し ${listOr(tap, "なし")} · 押下 ${listOr(hold, "なし")} · スティック ${listOr(sticks, "中立")}`;
};

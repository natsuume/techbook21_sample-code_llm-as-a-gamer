// timelineTexts のテスト。タイムラインの区間・値の 1 行・棒と印の説明・フレームのまとめの文を確かめる
import { describe, expect, it } from "vitest";
import type { RequestItem } from "../api/apiTypes.ts";
import {
  describeAdoptedCell,
  describeAdvisorSpan,
  describeButtonSpan,
  describeEventMark,
  describeFrame,
  describeFrameSummary,
  describeQuestionCell,
  describeRange,
  describeRequestBar,
  describeRewriteMark,
  describeStickSpan,
  playheadLabel,
} from "./timelineTexts.ts";

const request: RequestItem = {
  n: 12,
  t_sent: 1,
  t_received: 1.5,
  result: "ok",
  action: "押下 A / 短押し none",
  advisor_requested: ["strategy"],
  advisor_accepted: ["strategy"],
  p: null,
  adopted_count: 1,
};

describe("describeRange と playheadLabel", () => {
  it("表示中の区間を小数 1 桁、再生位置を小数 2 桁で書く", () => {
    expect(describeRange({ from: 1200, to: 1320 })).toBe("t=1,200.0–1,320.0 秒");
    expect(playheadLabel(12.345)).toBe("t=12.35");
  });
});

describe("describeAdoptedCell", () => {
  it("値があれば、区間・値・要求の番号を書く", () => {
    expect(describeAdoptedCell({ start: 12, value: 3, owner: 40 })).toBe(
      "t=12.0–12.5 秒 · 操作に採った数 3 · 要求 #40",
    );
  });

  it("値が無ければ要求なし", () => {
    expect(describeAdoptedCell({ start: 12.5, value: null, owner: null })).toBe(
      "t=12.5–13.0 秒 · 要求なし",
    );
  });
});

describe("describeQuestionCell", () => {
  it("値があれば、問いの名前・区間・最大値・要求の番号を書く", () => {
    expect(describeQuestionCell("button_A", { start: 0, value: 0.876, owner: 2 })).toBe(
      "button_A · t=0.0–0.5 秒 · 最大 0.88 · 要求 #2",
    );
  });

  it("値が無ければ要求なし", () => {
    expect(describeQuestionCell("button_A", { start: 0, value: null, owner: null })).toBe(
      "button_A · t=0.0–0.5 秒 · 要求なし",
    );
  });
});

describe("describeFrame", () => {
  it("鮮明度と谷とみなす値は小数 1 桁、差分は小数 2 桁で書く", () => {
    expect(describeFrame({ t: 3.2, sharpness: 1234.56, ref: 300, diff: 0.123 })).toBe(
      "t=3.20 · 鮮明度 1,234.6 · 谷とみなす値 300.0 · 直前との差分 0.12",
    );
  });

  it("差分が無ければ —", () => {
    expect(describeFrame({ t: 0, sharpness: 1, ref: 1, diff: null })).toContain("直前との差分 —");
  });
});

describe("describeFrameSummary", () => {
  it("谷の確定の回数と残したフレームの数を書く", () => {
    expect(describeFrameSummary({ closes: 3, kept: 2, falls: 4, pendingPeak: null })).toBe(
      "この区間: 谷の確定 3 回 · 残したフレーム 2 枚",
    );
  });

  it("確定待ちの山があれば、その時刻と鮮明度を足す", () => {
    expect(
      describeFrameSummary({
        closes: 0,
        kept: 0,
        falls: 1,
        pendingPeak: { t: 4.5, sharpness: 88 },
      }),
    ).toBe("この区間: 谷の確定 0 回 · 残したフレーム 0 枚 · 確定待ちの山 t=4.50 (88.0)");
  });
});

describe("describeRequestBar", () => {
  it("番号・時刻・結果・操作・呼んだ advisor を書く", () => {
    expect(describeRequestBar(request)).toBe(
      "要求 #12 · t=1.00–1.50 · ok · 押下 A / 短押し none · advisor を呼んだ (strategy)",
    );
  });

  it("失敗した要求は送信の時刻と操作なしを書く", () => {
    expect(
      describeRequestBar({
        ...request,
        t_received: null,
        result: "error",
        action: null,
        advisor_accepted: [],
      }),
    ).toBe("要求 #12 · t=1.00 · error · 操作なし");
  });
});

describe("入力の区間の説明", () => {
  it("ボタンは押下と短押しを区別して書く", () => {
    expect(describeButtonSpan({ button: "A", kind: "hold", start: 1, end: 2 })).toBe(
      "押下 A · t=1.00–2.00",
    );
    expect(describeButtonSpan({ button: "B", kind: "tap", start: 1, end: 1.12 })).toBe(
      "短押し B · t=1.00–1.12",
    );
  });

  it("スティックは倒した大きさを書く", () => {
    expect(describeStickSpan({ stick: "LS", start: 0, end: 1, magnitude: 0.4375 })).toBe(
      "LS 倒した大きさ 0.44 · t=0.00–1.00",
    );
  });
});

describe("describeAdvisorSpan と describeRewriteMark", () => {
  it("advisor の区間は名前と時刻、実行中は実行中を書く", () => {
    expect(
      describeAdvisorSpan({ name: "advisor-controls-001", start: 3.5, end: 15, running: true }),
    ).toBe("advisor-controls-001 · t=3.50–15.00 · 実行中");
  });

  it("書き換えは書いた advisor、external は advisor 以外と書く", () => {
    const rewrite = {
      version: 1,
      t: 6.5,
      request: 4,
      kind: "strategy" as const,
      by: "advisor-strategy-001",
      external: false,
    };
    expect(describeRewriteMark(rewrite)).toBe("strategy の書き換え t=6.50 · advisor-strategy-001");
    expect(describeRewriteMark({ ...rewrite, by: "external", external: true })).toBe(
      "strategy の書き換え t=6.50 · advisor 以外 (external)",
    );
  });
});

describe("describeEventMark", () => {
  it("点の印は名前と時刻、error があれば足す", () => {
    expect(describeEventMark({ kind: "decision_error", t: 2, error: "timeout" })).toBe(
      "Decision Model の失敗 t=2.00 · timeout",
    );
    expect(describeEventMark({ kind: "capture_stopped", t: 3, error: null })).toBe(
      "キャプチャーの停止 t=3.00",
    );
  });

  it("復旧待ちは区間と、待った秒数か終わりの記録なしを書く", () => {
    expect(
      describeEventMark({ kind: "bridge_wait", start: 1, end: 4, open: false, waitedS: 3 }),
    ).toBe("ブリッジの復旧待ち t=1.00–4.00 (3.00 秒)");
    expect(
      describeEventMark({ kind: "bridge_wait", start: 1, end: 9, open: true, waitedS: null }),
    ).toBe("ブリッジの復旧待ち t=1.00–9.00 (終わりの記録なし)");
  });
});

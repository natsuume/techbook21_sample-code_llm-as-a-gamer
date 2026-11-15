// liveStatus のテスト。接続の状態の文言、「ライブ」「最新に戻る」を押せない理由、ライブで開き直す判定を確かめる
import { describe, expect, it } from "vitest";
import {
  LIVE_STATUS_TEXTS,
  liveStatusText,
  liveToggleReason,
  reopenOutcome,
  resumeButtonReason,
  shouldReopenLive,
} from "./liveStatus.ts";

describe("liveStatusText", () => {
  it("つないでいなければ空文字、つなぐ途中は接続中", () => {
    expect(liveStatusText({ state: "idle" }, "replay", true)).toBe("");
    expect(liveStatusText({ state: "idle" }, "live", true)).toBe("");
    expect(liveStatusText({ state: "connecting" }, "live", true)).toBe(
      LIVE_STATUS_TEXTS.connecting,
    );
  });

  it.each([
    ["live", true, LIVE_STATUS_TEXTS.following],
    ["live", false, LIVE_STATUS_TEXTS.paused],
    ["replay", true, LIVE_STATUS_TEXTS.replaying],
  ] as const)("つながっていて表示が %s・follow が %s なら「%s」", (display, follow, expected) => {
    expect(liveStatusText({ state: "open" }, display, follow)).toBe(expected);
  });

  it("切れた直後 (失敗 0 回) は読み直してつなぎ直している", () => {
    expect(liveStatusText({ state: "reconnecting", failures: 0, retryInMs: 0 }, "live", true)).toBe(
      LIVE_STATUS_TEXTS.reconnecting,
    );
  });

  it("失敗が 1 回以上なら、次に試すまでの秒 (切り上げ) と失敗の回数", () => {
    expect(
      liveStatusText({ state: "reconnecting", failures: 1, retryInMs: 1500 }, "live", false),
    ).toBe("接続が切れた · 2 秒後につなぎ直す (1 回失敗)");
    expect(
      liveStatusText({ state: "reconnecting", failures: 4, retryInMs: 8000 }, "replay", true),
    ).toBe("接続が切れた · 8 秒後につなぎ直す (4 回失敗)");
  });

  it("end を受け取ったら、終了の記録の有無で終わり方を書く", () => {
    expect(liveStatusText({ state: "ended", recordEnd: true }, "replay", true)).toBe(
      LIVE_STATUS_TEXTS.recordEnded,
    );
    expect(liveStatusText({ state: "ended", recordEnd: false }, "replay", true)).toBe(
      LIVE_STATUS_TEXTS.stalled,
    );
  });
});

describe("liveToggleReason", () => {
  it("つないでいれば押せる (null)", () => {
    expect(liveToggleReason("active", null)).toBeNull();
  });

  it("実行中でない実行、終わった実行、止まった実行で理由を変える", () => {
    expect(liveToggleReason("none", null)).toBe(LIVE_STATUS_TEXTS.liveNotInProgress);
    expect(liveToggleReason("ended", "recordEnd")).toBe(LIVE_STATUS_TEXTS.liveRecordEnded);
    expect(liveToggleReason("ended", "stalled")).toBe(LIVE_STATUS_TEXTS.liveStalled);
  });
});

describe("resumeButtonReason", () => {
  it("終わった実行の表示と、最新を追っているときは理由を返す", () => {
    expect(resumeButtonReason("replay", true)).toBe(LIVE_STATUS_TEXTS.resumeInReplay);
    expect(resumeButtonReason("replay", false)).toBe(LIVE_STATUS_TEXTS.resumeInReplay);
    expect(resumeButtonReason("live", true)).toBe(LIVE_STATUS_TEXTS.resumeFollowing);
  });

  it("一時停止中は押せる (null)", () => {
    expect(resumeButtonReason("live", false)).toBeNull();
  });
});

describe("shouldReopenLive", () => {
  it.each(["none", "ended"] as const)(
    "%s になった後に読み終えた一覧が実行中なら true",
    (liveSession) => {
      expect(shouldReopenLive({ inProgress: true, readAt: 2000 }, liveSession, 1000)).toBe(true);
    },
  );

  it("一覧の値の変わり目を見ない (終わる前に読んだ一覧も実行中だったとき)", () => {
    // 終わる前に読んだ一覧は実行中だが、終わった時刻より前に読んだため開き直さない
    expect(shouldReopenLive({ inProgress: true, readAt: 500 }, "ended", 1000)).toBe(false);
    // 終わった後に読み終えた一覧も実行中のまま (値は変わっていない) なら開き直す
    expect(shouldReopenLive({ inProgress: true, readAt: 1500 }, "ended", 1000)).toBe(true);
  });

  it("つないでいれば false", () => {
    expect(shouldReopenLive({ inProgress: true, readAt: 2000 }, "active", 1000)).toBe(false);
  });

  it("終わった時刻以前 (ちょうど同じを含む) に読み終えた一覧なら false", () => {
    expect(shouldReopenLive({ inProgress: true, readAt: 1000 }, "ended", 1000)).toBe(false);
    expect(shouldReopenLive({ inProgress: true, readAt: 999 }, "none", 1000)).toBe(false);
  });

  it("一覧が実行中でなければ false", () => {
    expect(shouldReopenLive({ inProgress: false, readAt: 2000 }, "ended", 1000)).toBe(false);
  });

  it("一覧の値か、終わった時刻が無ければ false", () => {
    expect(shouldReopenLive(null, "ended", 1000)).toBe(false);
    expect(shouldReopenLive({ inProgress: true, readAt: 2000 }, "ended", null)).toBe(false);
  });
});

describe("reopenOutcome", () => {
  const RUN = "20260101-120000";

  it.each(["none", "ended"] as const)(
    "%s のまま同じ実行を選んでいて、すべて読み直せたら reopen",
    (liveSession) => {
      expect(reopenOutcome(true, RUN, { run: RUN, liveSession })).toBe("reopen");
    },
  );

  it.each(["none", "ended"] as const)(
    "%s のまま同じ実行を選んでいて、どれかが読めなければ waitForNextList (開き直さない)",
    (liveSession) => {
      expect(reopenOutcome(false, RUN, { run: RUN, liveSession })).toBe("waitForNextList");
    },
  );

  it("読み直しを待つ間に、選んでいる実行が替わったか、つないでいれば ignore", () => {
    expect(reopenOutcome(true, RUN, { run: "20260102-090000", liveSession: "ended" })).toBe(
      "ignore",
    );
    expect(reopenOutcome(false, RUN, { run: null, liveSession: "none" })).toBe("ignore");
    expect(reopenOutcome(true, RUN, { run: RUN, liveSession: "active" })).toBe("ignore");
    expect(reopenOutcome(false, RUN, { run: RUN, liveSession: "active" })).toBe("ignore");
  });

  it("読み直せなかった後に読み終えた一覧は、shouldReopenLive の規則のまま、もう一度開き直す", () => {
    // 終わった時刻 1000 の後に読み終えた一覧で開き直そうとし、読み直しに失敗した
    expect(shouldReopenLive({ inProgress: true, readAt: 2000 }, "ended", 1000)).toBe(true);
    expect(reopenOutcome(false, RUN, { run: RUN, liveSession: "ended" })).toBe("waitForNextList");
    // 終わった時刻を 1000 に戻すと、次に読み終えた一覧 (読み終えた時刻 7000) でもう一度開き直す
    expect(shouldReopenLive({ inProgress: true, readAt: 7000 }, "ended", 1000)).toBe(true);
  });
});

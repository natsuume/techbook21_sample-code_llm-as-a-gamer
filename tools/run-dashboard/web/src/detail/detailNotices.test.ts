// detailNotices のテスト。タブごとの「記録なし」「形が古いため表示しない」などの文言を確かめる
import { describe, expect, it } from "vitest";
import {
  advisorTabNotice,
  fileTabNotice,
  frameTabNotice,
  requestTabNotice,
} from "./detailNotices.ts";

describe("requestTabNotice", () => {
  it("古い形の実行は、形が古いため表示しない", () => {
    expect(requestTabNotice(true, { available: true }, true)).toBe("形が古いため表示しない");
    expect(requestTabNotice(false, { available: false, reason: "old_format" }, false)).toBe(
      "形が古いため表示しない",
    );
  });

  it("要求の記録が無ければ要求の記録なし、選んでいる要求が無ければ要求なし", () => {
    expect(requestTabNotice(false, { available: false }, false)).toBe("要求の記録なし");
    expect(requestTabNotice(false, { available: true }, false)).toBe("要求なし");
  });

  it("選んでいる要求があれば null", () => {
    expect(requestTabNotice(false, { available: true }, true)).toBeNull();
  });
});

describe("frameTabNotice", () => {
  it("記録が無いか、時刻のあるフレームが無ければ記録なし", () => {
    expect(frameTabNotice({ available: false }, 0)).toBe("記録なし");
    expect(frameTabNotice({ available: true }, 0)).toBe("記録なし");
  });

  it("古い形は、形が古いため表示しない", () => {
    expect(frameTabNotice({ available: false, reason: "old_format" }, 0)).toBe(
      "形が古いため表示しない",
    );
  });

  it("フレームがあれば null", () => {
    expect(frameTabNotice({ available: true }, 3)).toBeNull();
  });
});

describe("advisorTabNotice・fileTabNotice", () => {
  it("古い形の実行は、形が古いため表示しない", () => {
    expect(advisorTabNotice(true, 2, 0)).toBe("形が古いため表示しない");
    expect(fileTabNotice(true)).toBe("形が古いため表示しない");
  });

  it("advisor の実行も advisor 以外の書き換えも無ければ記録なし", () => {
    expect(advisorTabNotice(false, 0, 0)).toBe("記録なし");
    expect(advisorTabNotice(false, 0, 1)).toBeNull();
    expect(advisorTabNotice(false, 1, 0)).toBeNull();
    expect(fileTabNotice(false)).toBeNull();
  });
});

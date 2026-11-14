// タイムラインの目盛りのレーンに描く目盛り (位置・ラベル・ラベルの寄せ方) を、表示の区間から求める純粋な関数。
import { computeAxisTicks } from "./axisTicks.ts";
import { ratioInRange, type TimeRange } from "./viewWindow.ts";

/**
 * 目盛り 1 つ。ratio はトラックの幅に対する位置 (0〜1)、label は "t=<経過秒>"。
 * align はラベルの寄せ方 (start は目盛りから右へ、center は目盛りを中心に、end は目盛りから左へ)
 */
export type AxisTickMark = {
  t: number;
  ratio: number;
  label: string;
  align: "start" | "center" | "end";
};

/** ラベルを中央に寄せず、トラックの内側に寄せる両端からの比率 */
const EDGE_RATIO = 0.05;

/**
 * computeAxisTicks(range) の目盛りを、位置とラベルにして返す。
 * - ratio は ratioInRange(t, range)
 * - label は "t=" + t.toLocaleString("ja-JP") (例 "t=1,234.5")
 * - align は ratio < 0.05 なら start、ratio > 0.95 なら end、それ以外は center (ラベルがトラックからはみ出さないため)
 */
export const axisTickMarks = (range: TimeRange): AxisTickMark[] =>
  computeAxisTicks(range).map((t) => {
    const ratio = ratioInRange(t, range);
    const align = ratio < EDGE_RATIO ? "start" : ratio > 1 - EDGE_RATIO ? "end" : "center";
    return { t, ratio, label: `t=${t.toLocaleString("ja-JP")}`, align };
  });

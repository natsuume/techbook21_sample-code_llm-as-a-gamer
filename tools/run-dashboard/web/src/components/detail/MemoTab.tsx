// 詳細ペインのメモのタブ。タイムラインの色と形の凡例 (要求の棒・操作に採った数の帯・ヒートマップ・コントローラー入力・
// 折れ線・advisor の区間と書き換えの印・イベント) を出す。
import { joinClassNames } from "../../styles/joinClassNames.ts";
import content from "./DetailContent.module.css";
import styles from "./MemoTab.module.css";

/** 凡例の 1 項目。swatches は色の見本の class (MemoTab.module.css の名前) */
type LegendItem = { swatches: readonly string[]; text: string };
/** 凡例のまとまり */
type LegendGroup = { title: string; items: readonly LegendItem[] };

/** タイムラインのレーンごとの凡例 */
const LEGEND_GROUPS: readonly LegendGroup[] = [
  {
    title: "要求の棒 (送信から受信まで)",
    items: [
      { swatches: ["requestOther"], text: "要求" },
      { swatches: ["requestOperation"], text: "操作を送った要求" },
      { swatches: ["requestAdvisor"], text: "advisor を呼んだ要求 (高い棒)" },
      { swatches: ["requestSelected"], text: "選んでいる要求 (最も高い棒)" },
      { swatches: ["requestFailed"], text: "失敗・破棄した要求 (破線の枠)" },
    ],
  },
  {
    title: "操作に採った数の帯 (0.5 秒ごと)",
    items: [
      { swatches: ["adoptedZero"], text: "0" },
      { swatches: ["adoptedOne"], text: "1" },
      { swatches: ["adoptedMany"], text: "2 以上" },
    ],
  },
  {
    title: "問いのヒートマップ (0.5 秒ごとの最大値)",
    items: [
      { swatches: ["heat"], text: "問いの値 0 → 1" },
      { swatches: ["heatOver"], text: "advisor の問いで、しきい値を超えた値" },
    ],
  },
  {
    title: "コントローラー入力",
    items: [
      { swatches: ["inputHold"], text: "押下" },
      { swatches: ["inputTap"], text: "短押し" },
      { swatches: ["inputStick"], text: "スティック (濃さが倒した大きさ)" },
    ],
  },
  {
    title: "意味のあるフレームの折れ線",
    items: [
      { swatches: ["line"], text: "鮮明度" },
      { swatches: ["line", "lineRef"], text: "谷とみなす値 (破線)" },
      { swatches: ["peak"], text: "確定待ちの山" },
      { swatches: ["line", "lineDiff"], text: "直前のフレームとの差分" },
      { swatches: ["line", "lineThreshold"], text: "似ているとみなす上限 (破線)" },
    ],
  },
  {
    title: "advisor とファイル",
    items: [
      { swatches: ["advisorSpan"], text: "advisor の実行" },
      { swatches: ["advisorSpan", "advisorRunning"], text: "実行中の advisor (破線の枠)" },
      { swatches: ["rewrite"], text: "受け持ちの advisor の書き換え" },
      { swatches: ["rewriteExternal"], text: "advisor 以外の書き換え (枠だけの菱形)" },
    ],
  },
  {
    title: "イベント",
    items: [
      { swatches: ["decisionError"], text: "Decision Model の失敗 (丸)" },
      { swatches: ["captureStopped"], text: "キャプチャーの停止 (四角)" },
      { swatches: ["bridgeWait"], text: "ブリッジの復旧待ち" },
      { swatches: ["bridgeWait", "bridgeOpen"], text: "閉じていない復旧待ち (破線の枠)" },
    ],
  },
];

/** メモのタブ (凡例) */
export const MemoTab = () => (
  <>
    <h3 className={content.heading}>凡例</h3>
    {LEGEND_GROUPS.map((group) => (
      <section key={group.title} className={content.block}>
        <h4 className={content.subheading}>{group.title}</h4>
        <ul className={styles.legend}>
          {group.items.map((item) => (
            <li key={item.text} className={styles.item}>
              <span className={styles.swatchSlot} aria-hidden="true">
                <span
                  className={joinClassNames(
                    styles.swatch,
                    ...item.swatches.map((swatch) => styles[swatch]),
                  )}
                />
              </span>
              {item.text}
            </li>
          ))}
        </ul>
      </section>
    ))}
  </>
);

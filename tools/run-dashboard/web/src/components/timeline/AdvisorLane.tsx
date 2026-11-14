// タイムラインの advisor とファイルのレーン。役割ごとの 4 行に、advisor の実行の区間 (名前入りの button。押すと
// advisor のタブで開く) と、その役割が受け持つファイルの書き換えの印 (菱形の button。押すとファイルのタブでその版を
// 開く) を描く。区間と印は Tab で届く。
import type { NoteKind } from "../../api/apiTypes.ts";
import { ratioToPercent } from "../../format/formatProbability.ts";
import type { NoteRewrite } from "../../notes/noteHistory.ts";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import type { AdvisorRow, AdvisorSpan } from "../../timeline/advisorRows.ts";
import type { LaneNotice } from "../../timeline/laneNotice.ts";
import { cachedDescriber } from "../../timeline/textCache.ts";
import { describeAdvisorSpan, describeRewriteMark } from "../../timeline/timelineTexts.ts";
import { placePoint, placeSpan } from "../../timeline/trackPlacement.ts";
import type { TimeRange } from "../../timeline/viewWindow.ts";
import styles from "./AdvisorLane.module.css";
import { Lane, LaneNoticeText, LaneTrack } from "./Lane.tsx";

/** advisor の区間と書き換えの印の説明 (区間・書き換えごとに 1 回だけ作る) */
const describeSpan = cachedDescriber(describeAdvisorSpan);
const describeRewrite = cachedDescriber(describeRewriteMark);

export type AdvisorLaneProps = {
  /** advisorLaneNotice の結果。null なら役割ごとの行を描く */
  notice: LaneNotice;
  /** advisorRowsOf の結果 */
  rows: readonly AdvisorRow[];
  range: TimeRange;
  /** 詳細ペインの advisor のタブで開いている実行の名前 (タブが advisor でなければ null) */
  selectedName: string | null;
  /** 区間を押したときに、advisor の実行の名前で呼ぶ */
  onSelectAdvisor: (name: string) => void;
  /** 書き換えの印を押したときに、ファイルと版で呼ぶ */
  onSelectRewrite: (kind: NoteKind, version: number) => void;
};

/** advisor とファイルのレーン。見出しの行に、表示の区間の実行と書き換えの数を出す */
export const AdvisorLane = ({
  notice,
  rows,
  range,
  selectedName,
  onSelectAdvisor,
  onSelectRewrite,
}: AdvisorLaneProps) => {
  if (notice !== null) {
    return (
      <Lane label="advisor とファイル">
        <LaneNoticeText>{notice}</LaneNoticeText>
      </Lane>
    );
  }
  const visibleRows = rows.map((row) => ({
    role: row.role,
    spans: row.spans.flatMap((span) => {
      const placement = placeSpan(span.start, span.end, range);
      return placement === null ? [] : [{ span, placement }];
    }),
    rewrites: row.rewrites.flatMap((rewrite) => {
      const left = placePoint(rewrite.t, range);
      return left === null ? [] : [{ rewrite, left }];
    }),
  }));
  const spanCount = visibleRows.reduce((total, row) => total + row.spans.length, 0);
  const rewriteCount = visibleRows.reduce((total, row) => total + row.rewrites.length, 0);
  return (
    <div className={styles.group}>
      <Lane label="advisor とファイル">
        <LaneNoticeText>
          この区間: 実行 {spanCount.toLocaleString("ja-JP")} 件 · 書き換え{" "}
          {rewriteCount.toLocaleString("ja-JP")} 件
        </LaneNoticeText>
      </Lane>
      {visibleRows.map((row) => (
        <Lane key={row.role} label={row.role} level="sub">
          <LaneTrack size="advisor">
            {row.spans.map(({ span, placement }) => (
              <AdvisorSpanButton
                key={span.name}
                span={span}
                left={placement.left}
                width={placement.width}
                selected={span.name === selectedName}
                onSelect={onSelectAdvisor}
              />
            ))}
            {row.rewrites.map(({ rewrite, left }) => (
              <RewriteMarkButton
                key={`${rewrite.version}-${rewrite.kind}`}
                rewrite={rewrite}
                left={left}
                onSelect={onSelectRewrite}
              />
            ))}
          </LaneTrack>
        </Lane>
      ))}
    </div>
  );
};

type AdvisorSpanButtonProps = {
  span: AdvisorSpan;
  left: number;
  width: number;
  selected: boolean;
  onSelect: (name: string) => void;
};

/** advisor の実行の区間。実行中は破線の枠、詳細ペインで開いている実行は accent の枠 */
const AdvisorSpanButton = ({ span, left, width, selected, onSelect }: AdvisorSpanButtonProps) => {
  const description = describeSpan(span);
  return (
    <button
      type="button"
      className={joinClassNames(
        styles.span,
        span.running && styles.running,
        selected && styles.selected,
      )}
      style={{ left: ratioToPercent(left), width: ratioToPercent(width) }}
      aria-label={`${description} を advisor のタブで開く`}
      aria-current={selected ? "true" : undefined}
      title={description}
      onClick={() => onSelect(span.name)}
    >
      {span.name}
      {span.running ? " 実行中" : ""}
    </button>
  );
};

type RewriteMarkButtonProps = {
  rewrite: NoteRewrite;
  left: number;
  onSelect: (kind: NoteKind, version: number) => void;
};

/** 書き換えの印 (菱形)。受け持ちの advisor 以外による書き換えは、地 surface に danger の枠 */
const RewriteMarkButton = ({ rewrite, left, onSelect }: RewriteMarkButtonProps) => {
  const description = describeRewrite(rewrite);
  return (
    <button
      type="button"
      className={joinClassNames(styles.mark, rewrite.external && styles.external)}
      style={{ left: ratioToPercent(left) }}
      aria-label={`${description} をファイルのタブで開く`}
      title={description}
      onClick={() => onSelect(rewrite.kind, rewrite.version)}
    />
  );
};

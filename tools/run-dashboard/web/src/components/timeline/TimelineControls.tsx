// タイムラインの見出しの行の操作。前の要求・次の要求のボタン、問いのヒートマップの展開と畳む、表示する幅の select。
// ヒートマップの展開はいつでも押せる (描けない実行では、展開したレーンに「記録なし」などの文言を出す)。
import type { RangeResponse, RequestItem } from "../../api/apiTypes.ts";
import { requestStep } from "../../timeline/requestStep.ts";
import { parseViewWidth, VIEW_WIDTH_OPTIONS } from "../../timeline/viewWidthOptions.ts";
import type { ViewWidth } from "../../timeline/viewWindow.ts";
import { Button } from "../common/Button.tsx";
import { Icon } from "../common/Icon.tsx";
import { IconButton } from "../common/IconButton.tsx";
import { SelectField } from "../common/SelectField.tsx";

export type TimelineControlsProps = {
  requests: RangeResponse<RequestItem>;
  /** 選んでいる要求の番号 */
  selectedN: number | null;
  width: ViewWidth;
  /** ヒートマップを展開しているか */
  heatmapOpen: boolean;
  /** 問いの数 (summary.questions の数) */
  questionCount: number;
  /** ヒートマップのレーンの要素の id (展開のボタンの aria-controls) */
  heatmapId: string;
  /** 前後の要求のボタンを押したときに、移る先の要求の番号で呼ぶ */
  onStep: (n: number) => void;
  onToggleHeatmap: () => void;
  onWidthChange: (width: ViewWidth) => void;
};

/** 見出しの行の右に並べる操作 */
export const TimelineControls = ({
  requests,
  selectedN,
  width,
  heatmapOpen,
  questionCount,
  heatmapId,
  onStep,
  onToggleHeatmap,
  onWidthChange,
}: TimelineControlsProps) => {
  const previous = requestStep(requests, selectedN, -1);
  const next = requestStep(requests, selectedN, 1);
  return (
    <>
      <IconButton
        icon="prev"
        label="前の要求"
        disabledReason={previous.reason}
        onClick={() => {
          if (previous.n !== null) {
            onStep(previous.n);
          }
        }}
      />
      <IconButton
        icon="next"
        label="次の要求"
        disabledReason={next.reason}
        onClick={() => {
          if (next.n !== null) {
            onStep(next.n);
          }
        }}
      />
      <Button
        aria-expanded={heatmapOpen}
        aria-controls={heatmapOpen ? heatmapId : undefined}
        onClick={onToggleHeatmap}
      >
        <Icon name={heatmapOpen ? "collapse" : "expand"} />
        {heatmapOpen
          ? "畳む"
          : questionCount > 0
            ? `${questionCount.toLocaleString("ja-JP")} の問いを展開`
            : "問いを展開"}
      </Button>
      <SelectField
        label="幅"
        value={String(width)}
        onChange={(event) => {
          const selected = parseViewWidth(event.currentTarget.value);
          if (selected !== null) {
            onWidthChange(selected);
          }
        }}
      >
        {VIEW_WIDTH_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </SelectField>
    </>
  );
};

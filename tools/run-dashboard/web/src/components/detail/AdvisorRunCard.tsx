// advisor のタブの、advisor の実行 1 回のカード。名前・状態・時刻・呼んだ要求・終了コード・拒否された操作の数・
// advisor の答え・途中のツール呼び出し・その実行の間の書き換え・入出力のファイルを出す。
import { type Ref, useId } from "react";
import type { AdvisorDetail, AdvisorStep } from "../../api/apiTypes.ts";
import { useAdvisorDetail } from "../../api/queries.ts";
import { toQueryView } from "../../api/queryView.ts";
import {
  ADVISOR_STEP_KIND_TEXT,
  type AdvisorRunCard as AdvisorRunCardData,
  advisorTimeText,
  rewriteButtonText,
} from "../../detail/advisorRuns.ts";
import { fileNameOf } from "../../detail/requestFacts.ts";
import { formatBytes, formatSeconds } from "../../format/formatValues.ts";
import type { RunView } from "../../state/useRunView.ts";
import { useViewState } from "../../state/ViewStateContext.tsx";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import { NO_RECORD_NOTICE } from "../../timeline/laneNotice.ts";
import { Button } from "../common/Button.tsx";
import { Chip } from "../common/Chip.tsx";
import { EmptyState } from "../common/EmptyState.tsx";
import { ErrorState } from "../common/ErrorState.tsx";
import { Skeleton } from "../common/Skeleton.tsx";
import styles from "./AdvisorTab.module.css";
import content from "./DetailContent.module.css";

export type AdvisorRunCardProps = {
  view: RunView;
  card: AdvisorRunCardData;
  /** 詳細ペインで選んでいる実行か (accent の枠にする) */
  selected: boolean;
  /** 選んでいるカードの要素を入れる ref (スクロールして見せるため) */
  cardRef?: Ref<HTMLElement>;
};

/** advisor の実行のカード (article)。途中のツール呼び出しと入出力のファイルは advisor の実行の API から読む */
export const AdvisorRunCard = ({ view, card, selected, cardRef }: AdvisorRunCardProps) => {
  const { dispatch } = useViewState();
  const nameId = useId();
  const { run, rewrites } = card;
  const detailView = toQueryView(useAdvisorDetail(view.run, run.name));
  const detail = detailView.state === "ready" ? detailView.data : null;
  const running = detail === null ? card.running : detail.status === "running";
  const requestN = run.request;
  const requestSelectable = requestN !== null && view.requestIndexByN.has(requestN);

  return (
    <article
      ref={cardRef}
      className={joinClassNames(content.card, selected && content.selectedCard)}
      aria-labelledby={nameId}
      aria-current={selected ? "true" : undefined}
    >
      <div className={content.headRow}>
        <h5 id={nameId} className={content.cardName}>
          {run.name}
        </h5>
        <Chip tone={running ? "running" : "default"}>{running ? "実行中" : "完了"}</Chip>
      </div>
      <dl className={content.facts}>
        <dt>時刻</dt>
        <dd className={content.data}>{advisorTimeText(run)}</dd>
        <dt>呼んだ要求</dt>
        <dd>
          {requestSelectable && requestN !== null ? (
            <Button
              aria-label={`要求 #${requestN} を要求のタブで開く`}
              onClick={() => dispatch({ type: "selectRequest", n: requestN })}
            >
              #{requestN}
            </Button>
          ) : (
            <span className={content.data}>
              {requestN === null ? "—" : `#${requestN} (要求の記録なし)`}
            </span>
          )}
        </dd>
        <dt>終了コード</dt>
        <dd className={content.data}>{run.returncode ?? "—"}</dd>
        <dt>拒否された操作</dt>
        <dd className={content.data}>
          {run.denials === null ? "—" : `${run.denials.toLocaleString("ja-JP")} 件`}
        </dd>
      </dl>
      <section className={content.block}>
        <h6 className={content.subheading}>advisor の答え</h6>
        <div className={content.textBox}>
          <pre className={content.pre}>
            {run.result ?? (running ? "実行中のため、答えはまだ無い" : "—")}
          </pre>
        </div>
      </section>
      <section className={content.block}>
        <h6 className={content.subheading}>途中のツール呼び出し</h6>
        {detailView.state === "loading" || detailView.state === "idle" ? (
          <Skeleton height="4rem" />
        ) : null}
        {detailView.state === "error" ? (
          <ErrorState error={detailView.error} onRetry={detailView.retry} />
        ) : null}
        {detail === null ? null : <AdvisorSteps detail={detail} />}
      </section>
      <section className={content.block}>
        <h6 className={content.subheading}>
          この実行の間の書き換え ({rewrites.length.toLocaleString("ja-JP")} 回)
        </h6>
        {rewrites.length === 0 ? (
          <EmptyState>なし</EmptyState>
        ) : (
          <div className={content.buttonRow}>
            {rewrites.map((rewrite) => (
              <Button
                key={`${rewrite.version}-${rewrite.kind}`}
                className={content.wrapButton}
                onClick={() =>
                  dispatch({
                    type: "selectFile",
                    file: { type: "note", kind: rewrite.kind, version: rewrite.version },
                  })
                }
              >
                {rewriteButtonText(rewrite)}
              </Button>
            ))}
          </div>
        )}
      </section>
      <section className={content.block}>
        <h6 className={content.subheading}>入出力のファイル ({run.name}/)</h6>
        {detailView.state === "loading" || detailView.state === "idle" ? (
          <Skeleton height="var(--control-height)" />
        ) : null}
        {detailView.state === "error" ? (
          <ErrorState error={detailView.error} onRetry={detailView.retry} />
        ) : null}
        {detail === null ? null : <AdvisorFiles detail={detail} />}
      </section>
    </article>
  );
};

/** 途中のツール呼び出しの一覧。記録が無ければ「記録なし」 */
const AdvisorSteps = ({ detail }: { detail: AdvisorDetail }) => {
  if (!detail.steps_available) {
    return <EmptyState>{NO_RECORD_NOTICE}</EmptyState>;
  }
  if (detail.steps.length === 0) {
    return <EmptyState>ツール呼び出しなし</EmptyState>;
  }
  return (
    <ol className={styles.steps}>
      {detail.steps.map((step, position) => (
        <AdvisorStepRow key={stepKey(step, position)} step={step} />
      ))}
    </ol>
  );
};

/** 途中のツール呼び出しの行のキー (時刻・種類・ツールと、同じものが並んだときの通し番号) */
const stepKey = (step: AdvisorStep, position: number): string =>
  `${step.t ?? "none"}:${step.kind}:${step.tool ?? ""}:${position}`;

/** 途中のツール呼び出しの 1 行。拒否は danger の線と文字 */
const AdvisorStepRow = ({ step }: { step: AdvisorStep }) => (
  <li className={joinClassNames(styles.step, step.kind === "permission_denied" && styles.denied)}>
    <span className={styles.stepTime}>t={formatSeconds(step.t)}</span>
    <span className={styles.stepKind}>{ADVISOR_STEP_KIND_TEXT[step.kind]}</span>
    {step.tool === null ? null : <span className={styles.stepTool}>{step.tool}</span>}
    <span className={styles.stepSummary}>{step.summary}</span>
  </li>
);

/** 入出力のファイルのボタン。押すとファイルのタブでそのファイルを開く */
const AdvisorFiles = ({ detail }: { detail: AdvisorDetail }) => {
  const { dispatch } = useViewState();
  if (detail.files.length === 0) {
    return <EmptyState>ファイルなし</EmptyState>;
  }
  return (
    <div className={content.buttonRow}>
      {detail.files.map((file) => (
        <Button
          key={file.path}
          className={content.wrapButton}
          aria-label={`${file.path} をファイルのタブで開く`}
          onClick={() =>
            dispatch({ type: "selectFile", file: { type: "advisorFile", path: file.path } })
          }
        >
          {fileNameOf(file.path)} · {formatBytes(file.bytes)}
        </Button>
      ))}
    </div>
  );
};

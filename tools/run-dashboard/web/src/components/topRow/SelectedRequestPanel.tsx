// 上の段の「選んでいる要求」。決めた操作、判断の所要時間と画像、4 つの役割の advisor の問いの確率、問いの値の
// 上位 3 件を出し、「詳細」で詳細ペインの要求のタブを開く。
import type { RequestItem } from "../../api/apiTypes.ts";
import { useRequestDetail } from "../../api/queries.ts";
import { toQueryView } from "../../api/queryView.ts";
import { isOldFormatRequestDetail } from "../../api/requestDetailFormat.ts";
import {
  advisorProbabilityRows,
  decisionFacts,
  topQuestionRows,
} from "../../requests/selectedRequestSummary.ts";
import type { RunView } from "../../state/useRunView.ts";
import { useViewState } from "../../state/ViewStateContext.tsx";
import { Button } from "../common/Button.tsx";
import { Chip } from "../common/Chip.tsx";
import { EmptyState } from "../common/EmptyState.tsx";
import { ErrorState } from "../common/ErrorState.tsx";
import { Icon } from "../common/Icon.tsx";
import { Panel } from "../common/Panel.tsx";
import { ProbabilityRows } from "../common/ProbabilityRows.tsx";
import { Skeleton } from "../common/Skeleton.tsx";
import styles from "./SelectedRequestPanel.module.css";

/** 区画の見出し (要求の番号を付ける前) */
const TITLE = "選んでいる要求";
/** 確率の行 1 行の高さ */
const ROW_HEIGHT = "1.125rem";
/** 問いの値の上位を出す件数 */
const TOP_QUESTION_COUNT = 3;

export type SelectedRequestPanelProps = {
  /** 選んでいる実行の値。読み込み中は null (骨組みを出す) */
  view: RunView | null;
  className?: string;
};

/**
 * 選んでいる要求の区画。古い形の実行では「形が古いため表示しない」、要求の記録が無い実行では「要求の記録なし」、
 * 要求が 1 件も無い実行では「要求なし」
 */
export const SelectedRequestPanel = ({ view, className }: SelectedRequestPanelProps) => {
  if (view === null) {
    return (
      <Panel
        title={TITLE}
        actions={<Skeleton width="4.25rem" height="var(--control-height)" />}
        busy
        className={className}
      >
        <dl className={styles.facts}>
          <dt>決めた操作</dt>
          <dd>
            <Skeleton width="10rem" height={ROW_HEIGHT} />
          </dd>
          <dt>判断</dt>
          <dd>
            <Skeleton width="12rem" height={ROW_HEIGHT} />
          </dd>
        </dl>
        <section className={styles.block}>
          <h3 className={styles.subheading}>advisor の問い</h3>
          <Skeleton height="5.4375rem" />
        </section>
        <section className={styles.block}>
          <h3 className={styles.subheading}>問いの値の上位 {TOP_QUESTION_COUNT} 件</h3>
          <Skeleton height="4rem" />
        </section>
      </Panel>
    );
  }
  const request = view.selectedRequest;
  if (view.summary.old_format || !view.requests.available || request === null) {
    const message = view.summary.old_format
      ? "形が古いため表示しない"
      : !view.requests.available
        ? "要求の記録なし"
        : "要求なし";
    return (
      <Panel title={TITLE} className={className}>
        <EmptyState>{message}</EmptyState>
      </Panel>
    );
  }
  return <SelectedRequestContent view={view} request={request} className={className} />;
};

type SelectedRequestContentProps = {
  view: RunView;
  request: RequestItem;
  className?: string;
};

/** 選んでいる要求の中身。要求の詳細 (所要時間・画像・advisor の確率) は要求の詳細の API から読む */
const SelectedRequestContent = ({ view, request, className }: SelectedRequestContentProps) => {
  const { dispatch } = useViewState();
  const detailView = toQueryView(useRequestDetail(view.run, request.n));
  const detail =
    detailView.state === "ready" && !isOldFormatRequestDetail(detailView.data)
      ? detailView.data
      : null;
  const pending = detailView.state === "loading" || detailView.state === "idle";
  const facts = detail === null ? null : decisionFacts(detail);
  const topRows = topQuestionRows(view.summary.questions, request.p, TOP_QUESTION_COUNT);
  const threshold = view.summary.thresholds.advisor;

  return (
    <Panel
      title={`${TITLE} #${request.n}`}
      meta={<Chip>{request.result}</Chip>}
      actions={
        <Button onClick={() => dispatch({ type: "setTab", tab: "request" })}>
          詳細
          <Icon name="detail" size="small" />
        </Button>
      }
      busy={pending}
      className={className}
    >
      <dl className={styles.facts}>
        <dt>決めた操作</dt>
        <dd className={styles.data}>{request.action ?? "—"}</dd>
        <dt>判断</dt>
        <dd className={styles.data}>
          {pending ? (
            <Skeleton width="12rem" height={ROW_HEIGHT} />
          ) : facts === null ? (
            "—"
          ) : (
            <>
              <span className={styles.nowrap}>{facts.decide}</span>
              {" · "}
              <span className={styles.nowrap}>{facts.images}</span>
              {" · "}
              <span className={styles.nowrap}>{facts.bytes}</span>
            </>
          )}
        </dd>
      </dl>
      <section className={styles.block}>
        <h3 className={styles.subheading}>advisor の問い</h3>
        <div className={styles.advisorRows}>
          {pending ? <Skeleton height="5.4375rem" /> : null}
          {detailView.state === "error" ? (
            <ErrorState error={detailView.error} onRetry={detailView.retry} />
          ) : null}
          {detailView.state === "ready" && detail === null ? (
            <EmptyState>形が古いため表示しない</EmptyState>
          ) : null}
          {detail === null ? null : (
            <ProbabilityRows
              label="advisor の問いの確率"
              rows={advisorProbabilityRows(
                detail.advisor_probabilities,
                request.advisor_accepted,
              ).map((row) => ({
                key: row.role,
                name: row.role,
                value: row.value,
                threshold,
                note: row.accepted ? { text: "受け付けた", tone: "adopted" } : null,
              }))}
            />
          )}
        </div>
      </section>
      <section className={styles.block}>
        <h3 className={styles.subheading}>問いの値の上位 {TOP_QUESTION_COUNT} 件</h3>
        <div className={styles.topRows}>
          {topRows.length === 0 ? (
            <EmptyState>問いの値なし</EmptyState>
          ) : (
            <ProbabilityRows
              label="問いの値の上位"
              rows={topRows.map((row) => ({
                key: row.name,
                name: row.name,
                value: row.value,
                threshold: row.advisor ? threshold : null,
              }))}
            />
          )}
        </div>
      </section>
    </Panel>
  );
};

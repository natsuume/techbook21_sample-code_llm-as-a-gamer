// 上部のバーの実行の select。/api/runs の一覧を「run · game · model」(実行中なら「 · 実行中」を足す) の文言で出す。
// 一覧は一定の間隔で読み直す。読み直しに失敗しても、前に読めた一覧を出し続ける。
import { useRuns } from "../../api/queries.ts";
import { formatRunOption } from "../../runs/runOption.ts";
import { SelectField } from "../common/SelectField.tsx";

export type RunSelectProps = {
  /** 選んでいる実行の名前。選んでいなければ null */
  value: string | null;
  /** 実行を選んだときに、その名前と、一覧の in_progress (一覧に無ければ false) で呼ぶ */
  onSelect: (run: string, inProgress: boolean) => void;
  className?: string;
};

/**
 * aria-label が「実行」の select。option の value は実行の名前。一覧を読めていれば (読み直しに失敗した後も) option を
 * 出す。一度も読めていないときは、選べる実行の無い select に状態の文言 (読み込み中・読めなかった) を出す
 */
export const RunSelect = ({ value, onSelect, className }: RunSelectProps) => {
  const runs = useRuns();
  const items = runs.data;
  const placeholder =
    items !== undefined
      ? "実行を選ぶ"
      : runs.status === "error"
        ? "一覧を読み込めなかった"
        : "一覧を読み込み中";
  return (
    <SelectField
      label="実行"
      labelDisplay="hidden"
      className={className}
      value={value ?? ""}
      aria-busy={items === undefined && runs.status === "pending" ? true : undefined}
      onChange={(event) => {
        const selected = event.target.value;
        if (selected !== "") {
          onSelect(selected, items?.find((item) => item.run === selected)?.in_progress ?? false);
        }
      }}
    >
      <option value="" disabled>
        {placeholder}
      </option>
      {items?.map((run) => (
        <option key={run.run} value={run.run}>
          {formatRunOption(run)}
        </option>
      ))}
    </SelectField>
  );
};

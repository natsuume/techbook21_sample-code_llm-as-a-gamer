// 上部のバーの実行の select。/api/runs の一覧を「run · game · model」の文言で出す。
import { useRuns } from "../../api/queries.ts";
import { toQueryView } from "../../api/queryView.ts";
import { formatRunOption } from "../../runs/runOption.ts";
import { SelectField } from "../common/SelectField.tsx";

export type RunSelectProps = {
  /** 選んでいる実行の名前。選んでいなければ null */
  value: string | null;
  /** 実行を選んだときに、その名前で呼ぶ */
  onSelect: (run: string) => void;
  className?: string;
};

/**
 * aria-label が「実行」の select。option の value は実行の名前。一覧を読み込み中・読めなかったときは、
 * 選べる実行の無い select に状態の文言を出す (読み直しは画面の案内で行う)
 */
export const RunSelect = ({ value, onSelect, className }: RunSelectProps) => {
  const runs = toQueryView(useRuns());
  const placeholder =
    runs.state === "ready"
      ? "実行を選ぶ"
      : runs.state === "error"
        ? "一覧を読み込めなかった"
        : "一覧を読み込み中";
  return (
    <SelectField
      label="実行"
      labelDisplay="hidden"
      className={className}
      value={value ?? ""}
      aria-busy={runs.state === "loading" ? true : undefined}
      onChange={(event) => {
        if (event.target.value !== "") {
          onSelect(event.target.value);
        }
      }}
    >
      <option value="" disabled>
        {placeholder}
      </option>
      {runs.state === "ready"
        ? runs.data.map((run) => (
            <option key={run.run} value={run.run}>
              {formatRunOption(run)}
            </option>
          ))
        : null}
    </SelectField>
  );
};

// 2 つの文の行の差分 (同じ行・足した行・消した行) を求める純粋な関数。

export type DiffLine = { kind: "same" | "add" | "del"; text: string };

/** 行の配列の i 番目の行を返す (範囲の外は空の文字列) */
const lineAt = (lines: readonly string[], index: number): string => lines[index] ?? "";

/**
 * 共通の先頭と末尾を除いた部分の差分を、最長共通部分列 (LCS) の動的計画法 (O(n × m)) で求める。
 * lengths[i * (m + 1) + j] は before の i 行目以降と after の j 行目以降の LCS の長さ
 */
const diffByLcs = (beforeLines: readonly string[], afterLines: readonly string[]): DiffLine[] => {
  const n = beforeLines.length;
  const m = afterLines.length;
  const width = m + 1;
  const lengths = new Uint32Array((n + 1) * width);
  const lengthAt = (i: number, j: number): number => lengths[i * width + j] ?? 0;
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      lengths[i * width + j] =
        lineAt(beforeLines, i) === lineAt(afterLines, j)
          ? lengthAt(i + 1, j + 1) + 1
          : Math.max(lengthAt(i + 1, j), lengthAt(i, j + 1));
    }
  }
  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    const beforeLine = lineAt(beforeLines, i);
    const afterLine = lineAt(afterLines, j);
    if (beforeLine === afterLine) {
      lines.push({ kind: "same", text: beforeLine });
      i += 1;
      j += 1;
    } else if (lengthAt(i + 1, j) >= lengthAt(i, j + 1)) {
      lines.push({ kind: "del", text: beforeLine });
      i += 1;
    } else {
      lines.push({ kind: "add", text: afterLine });
      j += 1;
    }
  }
  for (; i < n; i += 1) {
    lines.push({ kind: "del", text: lineAt(beforeLines, i) });
  }
  for (; j < m; j += 1) {
    lines.push({ kind: "add", text: lineAt(afterLines, j) });
  }
  return lines;
};

/** 連続する same でない行のまとまりごとに、del をすべて add より前に並べ直す (同じ種類の中の順は保つ) */
const putDeletionsFirst = (lines: readonly DiffLine[]): DiffLine[] => {
  const ordered: DiffLine[] = [];
  let deletions: DiffLine[] = [];
  let additions: DiffLine[] = [];
  const flushChanges = (): void => {
    for (const line of deletions) {
      ordered.push(line);
    }
    for (const line of additions) {
      ordered.push(line);
    }
    deletions = [];
    additions = [];
  };
  for (const line of lines) {
    if (line.kind === "same") {
      flushChanges();
      ordered.push(line);
    } else if (line.kind === "del") {
      deletions.push(line);
    } else {
      additions.push(line);
    }
  }
  flushChanges();
  return ordered;
};

const sameLine = (text: string): DiffLine => ({ kind: "same", text });

/**
 * before と after を "\n" で行に分け、行の差分を返す。
 * - 最長共通部分列 (LCS) で same の行の数を最大にする
 * - 変わった箇所では del を add より前に並べる
 * - same と del の text を順に並べると before の行、same と add の text を順に並べると after の行に戻る
 */
export const diffLines = (before: string, after: string): DiffLine[] => {
  const beforeLines = before.split("\n");
  const afterLines = after.split("\n");
  const shorterLength = Math.min(beforeLines.length, afterLines.length);
  let prefixLength = 0;
  while (prefixLength < shorterLength && beforeLines[prefixLength] === afterLines[prefixLength]) {
    prefixLength += 1;
  }
  let suffixLength = 0;
  while (
    suffixLength < shorterLength - prefixLength &&
    beforeLines[beforeLines.length - 1 - suffixLength] ===
      afterLines[afterLines.length - 1 - suffixLength]
  ) {
    suffixLength += 1;
  }
  const middle = diffByLcs(
    beforeLines.slice(prefixLength, beforeLines.length - suffixLength),
    afterLines.slice(prefixLength, afterLines.length - suffixLength),
  );
  return [
    ...beforeLines.slice(0, prefixLength).map(sameLine),
    ...putDeletionsFirst(middle),
    ...beforeLines.slice(beforeLines.length - suffixLength).map(sameLine),
  ];
};

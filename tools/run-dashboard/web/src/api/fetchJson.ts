// run-dashboard のサーバーの API から JSON を読む関数と、読めなかったときの例外。

/** エラーの文に入れる、応答の本文の先頭の文字数 */
const BODY_HEAD_LENGTH = 200;

/** API が成功でない状態のコード (response.ok が false) を返したときの例外。status は HTTP の状態のコード */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * path の API を GET で読み、応答の JSON を T として返す (JSON の型は検査しない)。
 * response.ok が false なら、状態のコードと本文の先頭 200 文字を含む ApiError を投げる。
 * signal は読み込みを中断するときに使う (TanStack Query が渡す)
 */
export const fetchJson = async <T>(path: string, signal?: AbortSignal): Promise<T> => {
  const response = await fetch(path, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    const bodyHead = body.slice(0, BODY_HEAD_LENGTH);
    throw new ApiError(
      response.status,
      `HTTP ${response.status}: ${path}${bodyHead === "" ? "" : ` ${bodyHead}`}`,
    );
  }
  return (await response.json()) as T;
};

/** 画面の「読めなかった理由」に出す文を返す。Error ならその message、それ以外は String(error) */
export const describeError = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

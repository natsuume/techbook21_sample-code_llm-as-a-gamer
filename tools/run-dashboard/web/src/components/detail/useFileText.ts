// 実行のディレクトリーのファイル (advisor の入出力など) を、files の API からテキストとして読むフック。
import { skipToken, type UseQueryResult, useQuery } from "@tanstack/react-query";
import { filePath } from "../../api/apiPaths.ts";
import { ApiError } from "../../api/fetchJson.ts";

/** エラーの文に入れる、応答の本文の先頭の文字数 */
const BODY_HEAD_LENGTH = 200;

/** path のファイルを GET で読み、本文を文字列で返す。response.ok が false なら ApiError を投げる */
const fetchText = async (path: string, signal: AbortSignal): Promise<string> => {
  const response = await fetch(path, { signal });
  const body = await response.text();
  if (!response.ok) {
    const bodyHead = body.slice(0, BODY_HEAD_LENGTH);
    throw new ApiError(
      response.status,
      `HTTP ${response.status}: ${path}${bodyHead === "" ? "" : ` ${bodyHead}`}`,
    );
  }
  return body;
};

/** 実行 run のファイル path (files の API のパス) の中身を読む。queryKey は ["file", run, path]。path が null なら読まない */
export const useFileText = (run: string, path: string | null): UseQueryResult<string> =>
  useQuery({
    queryKey: ["file", run, path],
    queryFn: path === null ? skipToken : ({ signal }) => fetchText(filePath(run, path), signal),
  });

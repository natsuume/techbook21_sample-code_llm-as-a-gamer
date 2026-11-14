// CSS Modules の class 名をつないで、要素の className に渡す 1 つの文字列にする純粋な関数。

/** つなぐ class 名。CSS Modules の styles.foo は string | undefined のため undefined も受け付ける */
export type ClassNamePart = string | false | null | undefined;

/** 空でない文字列の class 名だけを、渡した順に半角の空白でつないで返す (false・null・undefined・"" は除く) */
export const joinClassNames = (...parts: readonly ClassNamePart[]): string =>
  parts.filter((part): part is string => typeof part === "string" && part !== "").join(" ");

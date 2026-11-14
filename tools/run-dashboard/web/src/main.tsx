// 画面の入口。フォントと全体の CSS を読み込み、データの取得 (TanStack Query)・選択の状態・選んでいる実行の値を
// 配る provider で包んで、index.html の #root に App を描く
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "@fontsource/jetbrains-mono/600.css";
import "@fontsource/noto-sans-jp/400.css";
import "@fontsource/noto-sans-jp/500.css";
import "@fontsource/noto-sans-jp/700.css";
import "./styles/tokens.css";
import "./styles/global.css";
import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import { createQueryClient } from "./api/queries.ts";
import { RunViewProvider } from "./state/RunViewProvider.tsx";
import { ViewStateProvider } from "./state/ViewStateContext.tsx";

const rootElement = document.getElementById("root");
if (rootElement === null) {
  throw new Error("index.html に #root の要素がない");
}

const queryClient = createQueryClient();

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ViewStateProvider>
        <RunViewProvider>
          <App />
        </RunViewProvider>
      </ViewStateProvider>
    </QueryClientProvider>
  </StrictMode>,
);

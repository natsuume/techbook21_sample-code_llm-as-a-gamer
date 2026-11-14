// 画面の入口。index.html の #root に App を描く
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.tsx";

const rootElement = document.getElementById("root");
if (rootElement === null) {
  throw new Error("index.html に #root の要素がない");
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// 詳細ペインの読み込み中の骨組み。読み込んだ後のタブの中身 (見出しの行と区画) に近い大きさの箱を並べる。
import type { DetailTab } from "../../state/viewState.ts";
import { Skeleton } from "../common/Skeleton.tsx";
import content from "./DetailContent.module.css";

/** 区画の骨組み。name は区画の名前 (React の key)、height は箱の高さ */
type SkeletonBlock = { name: string; height: string };

/** タブごとの区画の骨組み */
const BLOCKS: Record<Exclude<DetailTab, "memo">, readonly SkeletonBlock[]> = {
  request: [
    { name: "facts", height: "3rem" },
    { name: "images", height: "6rem" },
    { name: "answers", height: "14rem" },
    { name: "timings", height: "4rem" },
  ],
  frame: [
    { name: "image", height: "12rem" },
    { name: "neighbors", height: "3rem" },
    { name: "facts", height: "8rem" },
  ],
  advisor: [
    { name: "first", height: "16rem" },
    { name: "second", height: "16rem" },
  ],
  file: [
    { name: "list", height: "10rem" },
    { name: "viewer", height: "12rem" },
  ],
};

/** 見出しの行と、区画の骨組み (aria-hidden の箱) */
export const DetailSkeleton = ({ tab }: { tab: Exclude<DetailTab, "memo"> }) => (
  <>
    <div className={content.headRow}>
      <Skeleton width="8rem" height="1.375rem" />
      <Skeleton width="4.5rem" height="var(--control-height)" />
    </div>
    {BLOCKS[tab].map((block) => (
      <Skeleton key={block.name} height={block.height} />
    ))}
  </>
);

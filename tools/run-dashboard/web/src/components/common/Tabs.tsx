// タブの列 (role="tablist") と、選んでいるタブの中身 (role="tabpanel")。左右の矢印キー・Home・End でタブを移る。
import { type KeyboardEvent, type ReactNode, useId, useRef } from "react";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import styles from "./Tabs.module.css";
import { nextTabIndex } from "./tabKeyboard.ts";

/** タブ。id は選んだときに onSelect に渡す値、label はタブの文言 */
export type TabItem<T extends string> = { id: T; label: ReactNode };

export type TabsProps<T extends string> = {
  /** タブの列の名前 (tablist の aria-label) */
  label: string;
  items: readonly TabItem<T>[];
  /** 選んでいるタブの id */
  selected: T;
  /** タブを押したときと、矢印キーで移ったときに、そのタブの id で呼ぶ */
  onSelect: (id: T) => void;
  /** 選んでいるタブの中身 (tabpanel の中に置く) */
  children: ReactNode;
  /** 外枠 (タブの列と tabpanel を縦に並べる要素) に足す class */
  className?: string;
  /** tabpanel の要素に足す class (余白や縦のスクロールを指定する) */
  panelClassName?: string;
};

/**
 * タブの列と tabpanel。選んでいるタブだけが Tab で届き (ほかのタブは tabIndex={-1})、左右の矢印キー・Home・End で
 * 選ぶタブを変えてフォーカスを移す
 */
export const Tabs = <T extends string>({
  label,
  items,
  selected,
  onSelect,
  children,
  className,
  panelClassName,
}: TabsProps<T>) => {
  const baseId = useId();
  const panelId = `${baseId}-panel`;
  const tabId = (id: T): string => `${baseId}-tab-${id}`;
  const tabRefs = useRef(new Map<T, HTMLButtonElement>());

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = items.findIndex((item) => item.id === selected);
    const next = nextTabIndex(event.key, current, items.length);
    const nextItem = next === null ? undefined : items[next];
    if (nextItem === undefined) {
      return;
    }
    event.preventDefault();
    onSelect(nextItem.id);
    tabRefs.current.get(nextItem.id)?.focus();
  };

  return (
    <div className={joinClassNames(styles.tabs, className)}>
      <div role="tablist" aria-label={label} className={styles.list} onKeyDown={handleKeyDown}>
        {items.map((item) => (
          <button
            key={item.id}
            ref={(element) => {
              if (element === null) {
                tabRefs.current.delete(item.id);
              } else {
                tabRefs.current.set(item.id, element);
              }
            }}
            type="button"
            role="tab"
            id={tabId(item.id)}
            className={styles.tab}
            aria-selected={item.id === selected}
            aria-controls={panelId}
            tabIndex={item.id === selected ? 0 : -1}
            onClick={() => onSelect(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={panelId}
        aria-labelledby={tabId(selected)}
        className={joinClassNames(styles.panel, panelClassName)}
      >
        {children}
      </div>
    </div>
  );
};

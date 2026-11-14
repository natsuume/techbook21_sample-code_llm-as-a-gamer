// コントローラーの図 (viewBox 0 0 320 168)。15 個のキーと LS・RS のスティックを描き、押しているキーを塗る。
// 図の下と aria-label に「短押し … · 押下 … · スティック …」の文を出す。
import type { ControllerSnapshot, InputKind } from "../../controller/controllerTimeline.ts";
import type { ButtonName, StickName } from "../../controller/operationText.ts";
import { describeController } from "../../controller/playbackController.ts";
import { joinClassNames } from "../../styles/joinClassNames.ts";
import styles from "./ControllerFigure.module.css";

/** キーの rect。x・y・width・height・rx は viewBox の座標、text は図に書く文字 */
type KeyShape = {
  name: ButtonName;
  x: number;
  y: number;
  width: number;
  height: number;
  rx: number;
  text: string;
};

/** 図に描く 15 個のキー (L3・R3 はスティックの円で示す) */
const KEY_SHAPES: readonly KeyShape[] = [
  { name: "ZL", x: 12, y: 6, width: 56, height: 18, rx: 6, text: "ZL" },
  { name: "L", x: 12, y: 28, width: 56, height: 16, rx: 6, text: "L" },
  { name: "ZR", x: 252, y: 6, width: 56, height: 18, rx: 6, text: "ZR" },
  { name: "R", x: 252, y: 28, width: 56, height: 16, rx: 6, text: "R" },
  { name: "UP", x: 34, y: 98, width: 20, height: 20, rx: 4, text: "↑" },
  { name: "DOWN", x: 34, y: 138, width: 20, height: 20, rx: 4, text: "↓" },
  { name: "LEFT", x: 14, y: 118, width: 20, height: 20, rx: 4, text: "←" },
  { name: "RIGHT", x: 54, y: 118, width: 20, height: 20, rx: 4, text: "→" },
  { name: "MINUS", x: 112, y: 34, width: 28, height: 16, rx: 8, text: "−" },
  { name: "PLUS", x: 180, y: 34, width: 28, height: 16, rx: 8, text: "+" },
  { name: "HOME", x: 146, y: 62, width: 28, height: 28, rx: 14, text: "H" },
  { name: "X", x: 264, y: 38, width: 24, height: 24, rx: 12, text: "X" },
  { name: "Y", x: 240, y: 62, width: 24, height: 24, rx: 12, text: "Y" },
  { name: "A", x: 288, y: 62, width: 24, height: 24, rx: 12, text: "A" },
  { name: "B", x: 264, y: 86, width: 24, height: 24, rx: 12, text: "B" },
];

/** スティックの円。cx・cy は中心、press はスティックの押し込みのボタン */
type StickShape = { name: StickName; cx: number; cy: number; press: ButtonName };

const STICK_SHAPES: readonly StickShape[] = [
  { name: "LS", cx: 44, cy: 74, press: "L3" },
  { name: "RS", cx: 248, cy: 134, press: "R3" },
];

/** スティックの円の半径と、倒したときに点を動かす距離 */
const STICK_RADIUS = 22;
const STICK_TRAVEL = 14;
const STICK_DOT_RADIUS = 6;

const shapeClass = (kind: InputKind | undefined): string | undefined =>
  kind === "tap" ? styles.tap : kind === "hold" ? styles.hold : undefined;

const textClass = (kind: InputKind | undefined): string | undefined =>
  kind === "tap" ? styles.onTap : kind === "hold" ? styles.onHold : undefined;

export type ControllerFigureProps = {
  snapshot: ControllerSnapshot;
};

/** 押しているキーを、短押しは tap の色、押下は accent の色で塗った図と、その文 */
export const ControllerFigure = ({ snapshot }: ControllerFigureProps) => {
  const caption = describeController(snapshot);
  const kinds = new Map(snapshot.buttons.map((button) => [button.name, button.kind]));
  return (
    <figure className={styles.figure}>
      <svg
        className={styles.svg}
        viewBox="0 0 320 168"
        role="img"
        aria-label={`コントローラーの状態。${caption}`}
      >
        {KEY_SHAPES.map((key) => (
          <g key={key.name}>
            <rect
              className={joinClassNames(styles.key, shapeClass(kinds.get(key.name)))}
              x={key.x}
              y={key.y}
              width={key.width}
              height={key.height}
              rx={key.rx}
            />
            <text
              className={joinClassNames(styles.keyText, textClass(kinds.get(key.name)))}
              x={key.x + key.width / 2}
              y={key.y + key.height / 2 + 3.5}
              textAnchor="middle"
            >
              {key.text}
            </text>
          </g>
        ))}
        {STICK_SHAPES.map((stick) => {
          const value = snapshot.sticks[stick.name];
          const tilted = value !== undefined && (value.x !== 0 || value.y !== 0);
          const pressKind = kinds.get(stick.press);
          return (
            <g key={stick.name}>
              <circle
                className={joinClassNames(styles.key, shapeClass(pressKind))}
                cx={stick.cx}
                cy={stick.cy}
                r={STICK_RADIUS}
              />
              <text
                className={joinClassNames(styles.keyText, textClass(pressKind))}
                x={stick.cx}
                y={stick.cy - 10}
                textAnchor="middle"
              >
                {stick.name}
              </text>
              <circle
                className={tilted ? styles.stickDotOn : styles.stickDot}
                cx={stick.cx + (value?.x ?? 0) * STICK_TRAVEL}
                cy={stick.cy + 4 - (value?.y ?? 0) * STICK_TRAVEL}
                r={STICK_DOT_RADIUS}
              />
            </g>
          );
        })}
      </svg>
      <figcaption className={styles.caption}>{caption}</figcaption>
    </figure>
  );
};

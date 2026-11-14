// アイコンだけのボタン (2.125rem 四方)。文字が無いため、名前 (aria-label) を必ず渡す。
import { Button, type ButtonProps } from "./Button.tsx";
import { Icon, type IconName } from "./Icon.tsx";

/** IconButton の props。label 以外は Button と同じ (disabledReason で押せない理由を示せる) */
export type IconButtonProps = Omit<ButtonProps, "children" | "aria-label" | "shape"> & {
  icon: IconName;
  /** ボタンの名前。aria-label と、押せるときの title (カーソルを合わせたときの表示) にする */
  label: string;
};

/** アイコンを中に置いた四角の Button */
export const IconButton = ({ icon, label, title, ...rest }: IconButtonProps) => (
  <Button {...rest} shape="square" aria-label={label} title={title ?? label}>
    <Icon name={icon} />
  </Button>
);

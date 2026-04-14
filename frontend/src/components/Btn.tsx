import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from "react";
import { useTheme } from "../theme";

export type BtnVariant = "default" | "accent" | "ghost";
export type BtnSize = "sm" | "md";

export interface BtnProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "style"> {
  children?: ReactNode;
  variant?: BtnVariant;
  size?: BtnSize;
  style?: CSSProperties;
}

export function Btn({
  children, onClick, disabled, variant = "default", size = "md",
  style: sx, ...rest
}: BtnProps) {
  const t = useTheme();
  const s: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    border: "none",
    borderRadius: 8,
    cursor: disabled ? "not-allowed" : "pointer",
    fontWeight: 500,
    fontSize: size === "sm" ? 12 : 14,
    padding: size === "sm" ? "5px 10px" : "8px 16px",
    opacity: disabled ? 0.5 : 1,
    ...(variant === "accent"
      ? { background: t.accent, color: "#000" }
      : variant === "ghost"
        ? { background: "transparent", color: t.td }
        : { background: t.bg4, border: `1px solid ${t.border}`, color: t.text2 }),
    ...sx,
  };
  return (
    <button onClick={disabled ? undefined : onClick} style={s} {...rest}>
      {children}
    </button>
  );
}

import React, { createContext, useContext, useState } from "react";

export const ACCENT = { copper: "#C9793D", cyan: "#3FDCC8", danger: "#E15A4C", amber: "#E3A93A" };
export const CARD_SHADOW = "0 1px 2px rgba(0,0,0,0.35), 0 8px 20px -12px rgba(0,0,0,0.5)";

const PALETTE = {
  dark: { bg: "#080B0F", panel: "#141E2B", panelAlt: "#1A2534", raised: "#213042", border: "#2A3849", text: "#E9EEF3", muted: "#8996A6", faint: "#4B5A6B" },
  light: { bg: "#EEF1F4", panel: "#FFFFFF", panelAlt: "#F6F8FA", raised: "#FFFFFF", border: "#DCE2E8", text: "#151B22", muted: "#5C6773", faint: "#98A2AD" },
};

const ThemeCtx = createContext(null);

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState("dark");
  const T = PALETTE[theme];
  const cssVars = { "--bg": T.bg, "--panel": T.panel, "--panelAlt": T.panelAlt, "--raised": T.raised, "--border": T.border, "--text": T.text, "--muted": T.muted, "--faint": T.faint };
  const toggleTheme = () => setTheme((t) => (t === "dark" ? "light" : "dark"));
  return <ThemeCtx.Provider value={{ theme, toggleTheme, T, cssVars }}>{children}</ThemeCtx.Provider>;
}

// Use inside any page: const { T, theme, toggleTheme } = useTheme();
export function useTheme() {
  const ctx = useContext(ThemeCtx);
  if (!ctx) throw new Error("useTheme must be used inside <ThemeProvider> (AppShell already provides this)");
  return ctx;
}

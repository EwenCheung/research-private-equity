import { createContext, useEffect, useLayoutEffect, useState } from "react";

type Pref = "auto" | "light" | "dark";
const KEY = "theme";
const read = (): Pref => {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "auto";
  } catch {
    return "auto";
  }
};

const mq = () => window.matchMedia("(prefers-color-scheme: dark)");

/** Theme preference (auto follows the OS) and the scheme actually in effect. */
export function useTheme() {
  const [pref, setPref] = useState<Pref>(read);
  const [systemDark, setSystemDark] = useState(() => mq().matches);

  useEffect(() => {
    const m = mq();
    const on = () => setSystemDark(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);

  // Layout effect: the attribute must be set before any chart's passive effect reads the colour tokens.
  useLayoutEffect(() => {
    if (pref === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.dataset.theme = pref;
    try {
      localStorage.setItem(KEY, pref);
    } catch {
      /* private mode: preference just won't persist */
    }
  }, [pref]);

  const scheme = pref === "auto" ? (systemDark ? "dark" : "light") : pref;
  const cycle = () => setPref(pref === "auto" ? "light" : pref === "light" ? "dark" : "auto");
  return { pref, scheme: scheme as "light" | "dark", cycle };
}

/** The scheme in effect, so charts can re-read their colour tokens when it flips. */
export const SchemeContext = createContext<"light" | "dark">("light");

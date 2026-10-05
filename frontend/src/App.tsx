import type { ComponentType } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Login from "./Login";
import { SchemeContext, useTheme } from "./theme";

interface PageModule {
  meta: { title: string; path: string; order: number; devOnly?: boolean };
  default: ComponentType;
}

// Drop a file in src/pages/ exporting `meta` and a default component; there is no registry to edit.
const pages = Object.values(import.meta.glob<PageModule>("./pages/*.tsx", { eager: true }))
  .filter((p) => import.meta.env.DEV || !p.meta.devOnly)
  .sort((a, b) => a.meta.order - b.meta.order);

export default function App() {
  const { authed, logout } = useAuth();
  const theme = useTheme();

  if (authed === null) return null;
  if (!authed)
    return (
      <SchemeContext.Provider value={theme.scheme}>
        <Login />
      </SchemeContext.Provider>
    );

  return (
    <SchemeContext.Provider value={theme.scheme}>
      <div className="shell">
        <aside className="side">
          <div className="wordmark">
            Signal Monitor
            <small>Anthropic and its AI peers</small>
          </div>
          <nav className="nav" aria-label="Pages">
            {pages.map(({ meta }) => (
              <NavLink key={meta.path} to={meta.path}>
                {meta.title}
              </NavLink>
            ))}
          </nav>
          <div className="side-foot">
            <button className="linkish" onClick={theme.cycle}>
              Theme: {theme.pref}
            </button>
            <button className="linkish" onClick={logout}>
              Sign out
            </button>
          </div>
        </aside>
        <main className="main">
          <Routes>
            {pages.map(({ meta, default: Page }) => (
              <Route key={meta.path} path={meta.path} element={<Page />} />
            ))}
            <Route
              path="*"
              element={
                pages.length ? (
                  <Navigate to={pages[0].meta.path} replace />
                ) : (
                  <div className="awaiting">
                    <span>
                      <strong>No pages yet</strong>
                      Evidence pages appear here as each phase lands.
                    </span>
                  </div>
                )
              }
            />
          </Routes>
        </main>
      </div>
    </SchemeContext.Provider>
  );
}

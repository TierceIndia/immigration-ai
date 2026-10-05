import { useEffect, useState } from "react";
import { getToken, getRole, clearSession } from "./api.js";
import LoginPage from "./pages/LoginPage.jsx";
import SourcesDashboard from "./pages/SourcesDashboard.jsx";
import PipelineRunsPage from "./pages/PipelineRunsPage.jsx";
import SyncButton from "./components/SyncButton.jsx";

export default function App() {
  const [authed, setAuthed] = useState(() => Boolean(getToken()));
  const [role, setRole] = useState(() => getRole());
  const [view, setView] = useState("sources");

  useEffect(() => {
    if (!authed) setRole(null);
  }, [authed]);

  function handleLoggedIn(nextRole) {
    setRole(nextRole);
    setAuthed(true);
  }

  function handleLogout() {
    clearSession();
    setAuthed(false);
  }

  if (!authed) {
    return <LoginPage onLoggedIn={handleLoggedIn} />;
  }

  return (
    <>
      <header className="app-header">
        <h1>Immigration Knowledge — Admin</h1>
        <nav className="nav">
          <button
            className={view === "sources" ? "active" : ""}
            onClick={() => setView("sources")}
          >
            Sources
          </button>
          <button
            className={view === "runs" ? "active" : ""}
            onClick={() => setView("runs")}
          >
            Pipeline Runs
          </button>
        </nav>
        <div className="right">
          <SyncButton />
          <span className="muted" style={{ fontSize: 13 }}>
            {role}
          </span>
          <button className="btn-secondary" onClick={handleLogout}>
            Log out
          </button>
        </div>
      </header>
      <main className="app-main">
        {view === "sources" ? <SourcesDashboard /> : <PipelineRunsPage />}
      </main>
    </>
  );
}

import { useAuth } from "../auth/AuthContext.jsx";

export default function Topbar() {
  const { user, logout } = useAuth();
  return (
    <header className="topbar">
      <div className="topbar-spacer" />
      <div className="flex">
        <span className="muted">{user?.email}</span>
        <span className="badge badge-neutral">{user?.role}</span>
        <button className="btn btn-sm" onClick={logout}>
          Log out
        </button>
      </div>
    </header>
  );
}

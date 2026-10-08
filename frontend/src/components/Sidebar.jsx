import { NavLink } from "react-router-dom";

const LINKS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/sources", label: "Sources" },
  { to: "/review", label: "Review" },
  { to: "/rules", label: "Rules" },
  { to: "/documents", label: "Documents" },
  { to: "/applicants", label: "Applicants" },
  { to: "/scheduler", label: "Scheduler" },
];

export default function Sidebar() {
  return (
    <nav className="sidebar" aria-label="Main navigation">
      <div className="sidebar-brand">Immigration Knowledge</div>
      <ul className="sidebar-nav">
        {LINKS.map((link) => (
          <li key={link.to}>
            <NavLink
              to={link.to}
              end={link.end}
              className={({ isActive }) => "sidebar-link" + (isActive ? " sidebar-link-active" : "")}
            >
              {link.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

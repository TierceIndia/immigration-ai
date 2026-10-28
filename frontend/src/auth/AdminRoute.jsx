import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./AuthContext.jsx";

// Client-side role checks are UX only - the server (Depends(require_admin)
// on every admin route) is the real authority. This guard only decides what
// the browser shows; it never substitutes for a backend check.
export default function AdminRoute({ children }) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return null; // render nothing admin-related until /me resolves
  }
  if (status === "unauthed") {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return children;
}

export default function EmptyState({ title = "Nothing here yet", message, action }) {
  return (
    <div className="state-box">
      <div className="state-title">{title}</div>
      {message && <p>{message}</p>}
      {action}
    </div>
  );
}

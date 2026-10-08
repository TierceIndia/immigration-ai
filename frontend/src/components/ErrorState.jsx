export default function ErrorState({ error, onRetry }) {
  return (
    <div className="state-box">
      <div className="state-title">Something went wrong</div>
      <p>{error?.message || "Failed to load data."}</p>
      {onRetry && (
        <button className="btn" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

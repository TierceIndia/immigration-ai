export function TableSkeleton({ rows = 5, cols = 4 }) {
  return (
    <div className="table-wrap">
      <table>
        <tbody>
          {Array.from({ length: rows }).map((_, r) => (
            <tr key={r}>
              {Array.from({ length: cols }).map((_, c) => (
                <td key={c}>
                  <span className="skeleton" style={{ width: `${55 + ((r + c) % 3) * 12}%` }} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function LineSkeleton({ width = "100%" }) {
  return <span className="skeleton" style={{ width }} />;
}

import EmptyState from "./EmptyState.jsx";

// Every table in the app goes inside .table-wrap (overflow-x: auto) - a
// clipping bug was hit before when a wide table had no horizontal scroll
// container of its own.
export default function DataTable({ columns, rows, rowKey, emptyMessage = "No results.", emptyTitle = "Nothing to show" }) {
  if (!rows || rows.length === 0) {
    return <EmptyState title={emptyTitle} message={emptyMessage} />;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} style={c.width ? { width: c.width } : undefined}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((c) => (
                <td key={c.key}>{c.render ? c.render(row) : row[c.key]}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

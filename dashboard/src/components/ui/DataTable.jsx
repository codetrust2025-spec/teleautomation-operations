import React from 'react'

/**
 * A table that scrolls inside its card on tablets and, on phones, turns each
 * row into a small card of "label: value" lines (the Daily Ops roster pattern,
 * made shared).
 *
 *   columns: [{ key, header, render?(row), numeric?, primary? }]
 *     primary  the column that titles the row's card on phones (no label)
 *     numeric  right-aligned, tabular figures
 *   rows, rowKey(row), empty: text when there are no rows,
 *   cards: false keeps a real table on phones (wide data people compare across)
 */
export function DataTable({ columns, rows, rowKey, empty = 'Nothing to show.', loading = false, loadingText = 'Loading…', cards = true, caption, className = '', ...props }) {
  const list = rows || []
  return (
    <div className={['ui-table-wrap', className].filter(Boolean).join(' ')} {...props}>
      <table className={['ui-table', cards ? 'ui-table--cards' : ''].filter(Boolean).join(' ')}>
        {caption && <caption className="ui-field__label--hidden">{caption}</caption>}
        <thead>
          <tr>{columns.map(column => <th key={column.key} scope="col" className={column.numeric ? 'ui-table__num' : undefined}>{column.header}</th>)}</tr>
        </thead>
        <tbody>
          {(loading || list.length === 0) && (
            <tr className="ui-table__empty"><td colSpan={columns.length}>{loading ? loadingText : empty}</td></tr>
          )}
          {!loading && list.map((row, index) => (
            <tr key={rowKey ? rowKey(row) : index}>
              {columns.map(column => (
                <td
                  key={column.key}
                  data-label={column.header}
                  data-primary={column.primary ? '' : undefined}
                  className={column.numeric ? 'ui-table__num' : undefined}
                >
                  {column.render ? column.render(row) : row[column.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default DataTable

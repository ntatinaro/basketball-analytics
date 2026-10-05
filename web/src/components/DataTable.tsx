import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useRef, type MouseEvent } from "react";
import { useNavigate } from "react-router-dom";

/**
 * Sortable table. Tap a header to sort, tap again to reverse. Only the visible rows are
 * drawn (virtual scrolling), so a few thousand rows stay smooth on a phone. The first
 * column and the header stay fixed while scrolling.
 */
export function DataTable<T>({
  data,
  columns,
  sorting,
  onSortingChange,
  rowKey,
  autoHeight = false,
  label,
}: {
  data: T[];
  columns: ColumnDef<T, any>[];
  sorting: SortingState;
  onSortingChange: (s: SortingState) => void;
  rowKey: (row: T, index: number) => string;
  autoHeight?: boolean;
  label: string;
}) {
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: (updater) => onSortingChange(typeof updater === "function" ? updater(sorting) : updater),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    sortDescFirst: true,
    getRowId: (row, i) => rowKey(row, i),
  });
  const rows = table.getRowModel().rows;
  const parentRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  // Cells use plain links; one handler routes them in-app (cheaper than a router link per row).
  const onClick = (e: MouseEvent) => {
    const link = (e.target as HTMLElement).closest("a[href^='/']");
    if (!link || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    navigate(link.getAttribute("href")!);
  };
  const virtual = !autoHeight && rows.length > 60;
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 41,
    overscan: 6,
    enabled: virtual,
  });
  const items = virtual ? virtualizer.getVirtualItems() : [];
  const padTop = virtual && items.length ? items[0].start : 0;
  const padBottom = virtual && items.length ? virtualizer.getTotalSize() - items[items.length - 1].end : 0;
  const visible = virtual ? items.map((v) => rows[v.index]) : rows;
  const colCount = table.getVisibleLeafColumns().length;

  return (
    <div className={`table-wrap${autoHeight ? " auto-height" : ""}`} ref={parentRef} onClick={onClick}>
      <table aria-label={label}>
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => {
                const sorted = header.column.getIsSorted();
                const canSort = header.column.getCanSort();
                const meta = header.column.columnDef.meta as { title?: string; left?: boolean } | undefined;
                return (
                  <th
                    key={header.id}
                    scope="col"
                    className={[canSort ? "sortable" : "", sorted ? "sorted" : "", meta?.left ? "left" : ""].join(" ")}
                    title={meta?.title}
                    aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : canSort ? "none" : undefined}
                  >
                    {canSort ? (
                      // A real button, so headers can be sorted from the keyboard too.
                      <button type="button" className="sort-btn" onClick={header.column.getToggleSortingHandler()}>
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {sorted === "asc" ? " ▲" : sorted === "desc" ? " ▼" : ""}
                      </button>
                    ) : flexRender(header.column.columnDef.header, header.getContext())}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {padTop > 0 && (
            <tr aria-hidden>
              <td colSpan={colCount} style={{ height: padTop, padding: 0, border: 0 }} />
            </tr>
          )}
          {visible.map((row) => (
            <tr key={row.id}>
              {row.getVisibleCells().map((cell) => {
                const meta = cell.column.columnDef.meta as { left?: boolean } | undefined;
                return (
                  <td key={cell.id} className={meta?.left ? "left" : undefined}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                );
              })}
            </tr>
          ))}
          {padBottom > 0 && (
            <tr aria-hidden>
              <td colSpan={colCount} style={{ height: padBottom, padding: 0, border: 0 }} />
            </tr>
          )}
          {rows.length === 0 && (
            <tr>
              <td colSpan={colCount} className="left muted">
                No matches
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

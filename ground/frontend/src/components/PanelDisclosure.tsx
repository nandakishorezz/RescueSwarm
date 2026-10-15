import type { ReactNode } from "react";

export function PanelDisclosure({ id, title, open, onToggle, children, badge }: {
  id: string; title: string; open: boolean; onToggle: (id: string, open: boolean) => void; children: ReactNode; badge?: string;
}) {
  return <details className="panel-disclosure" open={open} onToggle={(event) => onToggle(id, event.currentTarget.open)}>
    <summary><span>{title}</span>{badge && <small>{badge}</small>}<i aria-hidden="true">⌄</i></summary>
    <div className="panel-disclosure-content">{children}</div>
  </details>;
}

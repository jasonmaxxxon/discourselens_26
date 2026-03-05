import type { ReactNode } from "react";

type Props = {
  title: string;
  action?: ReactNode;
  children: ReactNode;
};

export function SectionCard({ title, action, children }: Props) {
  return (
    <section className="section-card clay-card">
      <header className="section-head">
        <h3>{title}</h3>
        {action ? <div className="section-action">{action}</div> : null}
      </header>
      <div className="section-body">
        <div className="section-content">{children}</div>
      </div>
    </section>
  );
}

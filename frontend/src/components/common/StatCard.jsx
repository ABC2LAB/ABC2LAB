import { Braces, FileSearch, KeyRound, ShieldAlert } from 'lucide-react';

const icons = { pages: FileSearch, endpoints: Braces, parameters: KeyRound, critical: ShieldAlert };

export default function StatCard({ metric }) {
  const Icon = icons[metric.id] ?? FileSearch;
  return (
    <article className="stat-card">
      <div className={`stat-icon ${metric.tone}`}><Icon size={21} /></div>
      <div className="stat-meta"><span>{metric.label}</span><strong>{metric.value}</strong><small>{metric.helper}</small></div>
    </article>
  );
}

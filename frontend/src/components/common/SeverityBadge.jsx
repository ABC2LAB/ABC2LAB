const labels = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', info: 'Info' };

export default function SeverityBadge({ severity, dot = true }) {
  return <span className={`severity-badge ${severity}`}>{dot && <i />}{labels[severity]}</span>;
}

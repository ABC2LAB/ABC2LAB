import { CheckCircle2, Radar, ShieldAlert } from 'lucide-react';
import PageHeader from '../components/common/PageHeader.jsx';
import StatCard from '../components/common/StatCard.jsx';
import { recentActivities, summaryMetrics } from '../data/mockData.js';

const activityIcons = { scan: Radar, finding: ShieldAlert, resolved: CheckCircle2 };

export default function DashboardPage() {
  return (
    <>
      <PageHeader title="대시보드" />
      <section className="stats-grid">{summaryMetrics.map((metric) => <StatCard key={metric.id} metric={metric} />)}</section>
      <section className="mock-dashboard-grid">
        <article className="panel structure-panel"><div className="panel-header"><h2>웹 구조 요약</h2></div>
          <div className="mini-graph" aria-label="웹 구조 요약 그래프">
            <svg viewBox="0 0 430 245"><g className="mini-lines"><line x1="95" y1="120" x2="190" y2="52"/><line x1="95" y1="120" x2="205" y2="122"/><line x1="95" y1="120" x2="185" y2="194"/><line x1="190" y1="52" x2="302" y2="76"/><line x1="190" y1="52" x2="205" y2="122"/><line x1="205" y1="122" x2="302" y2="76"/><line x1="205" y1="122" x2="304" y2="148"/><line x1="185" y1="194" x2="304" y2="148"/><line x1="304" y1="148" x2="367" y2="195"/></g>
              <g className="mini-nodes"><circle className="user" cx="95" cy="120" r="12"/><circle className="user" cx="190" cy="52" r="12"/><circle className="page" cx="205" cy="122" r="11"/><circle className="page" cx="185" cy="194" r="11"/><circle className="endpoint" cx="302" cy="76" r="11"/><circle className="endpoint" cx="304" cy="148" r="11"/><circle className="resource" cx="367" cy="195" r="11"/></g></svg>
            <div className="mini-legend"><span><i className="user"/>User</span><span><i className="page"/>Page</span><span><i className="endpoint"/>Endpoint</span><span><i className="resource"/>Resource</span></div>
          </div>
        </article>
        <article className="panel"><div className="panel-header"><h2>최근 작업</h2></div>
          <div className="activity-list">{recentActivities.map((item) => { const Icon = activityIcons[item.type]; return <div key={item.id}><span className={`activity-icon ${item.type}`}><Icon size={17} /></span><div><strong>{item.title}</strong><p>{item.description}</p><small>{item.time}</small></div></div>; })}</div>
        </article>
      </section>
    </>
  );
}

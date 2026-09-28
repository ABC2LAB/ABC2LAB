import { BarChart3, Boxes, FileBarChart, FlaskConical, LayoutDashboard, Radar, ScanSearch, Settings, ShieldCheck, X } from 'lucide-react';
import { Link, useLocation } from 'react-router-dom';

const navigation = [
  { to: '/dashboard', label: '대시보드', icon: LayoutDashboard },
  { to: '/dashboard?view=status', label: '분석 현황', icon: Radar },
  { to: '/dashboard?view=structure', label: '웹 구조 분석', icon: BarChart3 },
  { to: '/knowledge-graph', label: 'Knowledge Graph', icon: Boxes },
  { to: '/vulnerabilities', label: '취약점 분석', icon: ShieldCheck },
  { to: '/dashboard?view=test', label: '테스트 실행', icon: FlaskConical },
  { to: '/dashboard?view=report', label: '결과 리포트', icon: FileBarChart },
  { to: '/dashboard?view=settings', label: '설정', icon: Settings },
];

export default function Sidebar({ open, onClose }) {
  const location = useLocation();
  const currentLocation = `${location.pathname}${location.search}`;

  return (
    <>
      {open && <button className="sidebar-backdrop" onClick={onClose} aria-label="메뉴 닫기" />}
      <aside className={`sidebar ${open ? 'is-open' : ''}`}>
        <div className="brand">
          <div className="brand-mark"><ScanSearch size={18} /></div>
          <div><strong>WebSec AI</strong></div>
          <button className="sidebar-close" onClick={onClose} aria-label="메뉴 닫기"><X size={20} /></button>
        </div>
        <nav className="nav-list" aria-label="주 메뉴">
          {navigation.map(({ to, label, icon: Icon }) => (
            <Link key={to} to={to} onClick={onClose} className={currentLocation === to ? 'nav-item active' : 'nav-item'}>
              <Icon size={19} /><span>{label}</span>
            </Link>
          ))}
        </nav>
        <div className="sidebar-footer"><span><i />시스템 정상</span><small>Mock data mode</small></div>
      </aside>
    </>
  );
}

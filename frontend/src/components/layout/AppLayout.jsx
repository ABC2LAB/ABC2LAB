import { Menu } from 'lucide-react';
import { useState } from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar.jsx';

export default function AppLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="app-shell">
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      <div className="app-frame">
        <header className="topbar">
          <button className="icon-button menu-button" onClick={() => setSidebarOpen(true)} aria-label="메뉴 열기">
            <Menu size={20} />
          </button>
          <strong>WebSec AI</strong>
        </header>
        <main className="page-content"><Outlet /></main>
      </div>
    </div>
  );
}

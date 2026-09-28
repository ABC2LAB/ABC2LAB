import { Navigate, Route, Routes } from 'react-router-dom';
import AppLayout from './components/layout/AppLayout.jsx';
import DashboardPage from './pages/DashboardPage.jsx';
import KnowledgeGraphPage from './pages/KnowledgeGraphPage.jsx';
import VulnerabilityDetailPage from './pages/VulnerabilityDetailPage.jsx';
import VulnerabilityListPage from './pages/VulnerabilityListPage.jsx';
import NotFoundPage from './pages/NotFoundPage.jsx';

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/knowledge-graph" element={<KnowledgeGraphPage />} />
        <Route path="/vulnerabilities" element={<VulnerabilityListPage />} />
        <Route path="/vulnerabilities/:vulnerabilityId" element={<VulnerabilityDetailPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

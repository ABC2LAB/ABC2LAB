import { Link } from 'react-router-dom';

export default function NotFoundPage() {
  return <div className="not-found"><strong>404</strong><h1>페이지를 찾을 수 없습니다</h1><Link to="/dashboard">대시보드로 이동</Link></div>;
}

import { Search } from 'lucide-react';
import PageHeader from '../components/common/PageHeader.jsx';
import GraphCanvas from '../components/graph/GraphCanvas.jsx';

export default function KnowledgeGraphPage() {
  return (
    <>
      <PageHeader title="Knowledge Graph" actions={<label className="page-search"><input placeholder="노드 검색..."/><Search size={17}/></label>} />
      <section className="panel graph-panel"><GraphCanvas /></section>
    </>
  );
}

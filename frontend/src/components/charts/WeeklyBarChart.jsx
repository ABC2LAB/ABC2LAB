export default function WeeklyBarChart({ data }) {
  const max = Math.max(...data.flatMap((item) => [item.found, item.resolved]));
  return (
    <div className="bar-chart">
      <div className="chart-legend"><span><i className="found" />발견</span><span><i className="resolved" />조치</span></div>
      <div className="bar-plot">
        {data.map((item) => (
          <div className="bar-column" key={item.label}>
            <div className="bars"><i className="found" style={{ height: `${(item.found / max) * 100}%` }} title={`발견 ${item.found}`} /><i className="resolved" style={{ height: `${(item.resolved / max) * 100}%` }} title={`조치 ${item.resolved}`} /></div>
            <span>{item.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

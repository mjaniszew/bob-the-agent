import { useQuery } from '@tanstack/react-query';
import api from '../api/client';

function Dashboard() {
  const { data: status, isLoading } = useQuery({
    queryKey: ['status'],
    queryFn: () => api.get('/api/status').then(res => res.data)
  });

  const { data: diagnostics } = useQuery({
    queryKey: ['diagnostics'],
    queryFn: () => api.get('/api/diagnostics').then(res => res.data)
  });

  if (isLoading) {
    return <div className="loading">Loading...</div>;
  }

  return (
    <div className="dashboard">
      <h1>Dashboard</h1>

      <div className="cards-grid">
        <div className="card">
          <h2>Task Summary</h2>
          {status && (
            <div className="stats">
              <div className="stat">
                <span className="stat-value">{status.tasks.total}</span>
                <span className="stat-label">Total</span>
              </div>
              <div className="stat">
                <span className="stat-value status-pending">{status.tasks.pending}</span>
                <span className="stat-label">Pending</span>
              </div>
              <div className="stat">
                <span className="stat-value status-running">{status.tasks.running}</span>
                <span className="stat-label">Running</span>
              </div>
              <div className="stat">
                <span className="stat-value status-completed">{status.tasks.completed}</span>
                <span className="stat-label">Completed</span>
              </div>
              <div className="stat">
                <span className="stat-value status-failed">{status.tasks.failed}</span>
                <span className="stat-label">Failed</span>
              </div>
            </div>
          )}
        </div>

        <div className="card">
          <h2>Schedules</h2>
          {status && (
            <div className="stats">
              <div className="stat">
                <span className="stat-value">{status.schedules.total}</span>
                <span className="stat-label">Total</span>
              </div>
              <div className="stat">
                <span className="stat-value">{status.schedules.enabled}</span>
                <span className="stat-label">Active</span>
              </div>
            </div>
          )}
        </div>

        <div className="card">
          <h2>System</h2>
          {status?.system && (
            <div className="system-info">
              <p><strong>Platform:</strong> {status.system.platform} {status.system.arch}</p>
              <p><strong>Uptime:</strong> {Math.floor(status.system.uptime / 3600)}h {Math.floor((status.system.uptime % 3600) / 60)}m</p>
              <p><strong>Memory:</strong> {((status.system.memory.used / status.system.memory.total) * 100).toFixed(1)}% used</p>
              <p><strong>CPU Cores:</strong> {status.system.cpu.cores}</p>
            </div>
          )}
        </div>

        <div className="card">
          <h2>Services</h2>
          {diagnostics?.services && (
            <div className="services-status">
              <div className="service">
                <span className={`service-status ${diagnostics.services.ollama ? 'online' : 'offline'}`}>
                  {diagnostics.services.ollama ? '●' : '○'}
                </span>
                <span>Ollama</span>
              </div>
              <div className="service">
                <span className={`service-status ${diagnostics.services.openclawGateway ? 'online' : 'offline'}`}>
                  {diagnostics.services.openclawGateway ? '●' : '○'}
                </span>
                <span>Agent Gateway</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default Dashboard;
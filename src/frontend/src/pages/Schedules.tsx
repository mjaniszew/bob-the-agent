import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import api from '../api/client';

interface Schedule {
  id: string;
  name: string;
  description: string;
  cron_expression: string;
  enabled: boolean;
  last_run?: string;
  next_run?: string;
}

function Schedules() {
  const [showForm, setShowForm] = useState(false);
  const [newSchedule, setNewSchedule] = useState({
    name: '',
    description: '',
    cron_expression: '',
    task_template: '{}'
  });

  const { data: schedules, isLoading, refetch } = useQuery({
    queryKey: ['schedules'],
    queryFn: () => api.get('/api/schedules').then(res => res.data)
  });

  const handleCreateSchedule = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.post('/api/schedules', {
        ...newSchedule,
        task_template: JSON.parse(newSchedule.task_template)
      });
      setShowForm(false);
      setNewSchedule({ name: '', description: '', cron_expression: '', task_template: '{}' });
      refetch();
    } catch (err) {
      console.error('Failed to create schedule:', err);
    }
  };

  const handleToggleSchedule = async (scheduleId: string, enabled: boolean) => {
    try {
      await api.patch(`/api/schedules/${scheduleId}`, { enabled: !enabled });
      refetch();
    } catch (err) {
      console.error('Failed to toggle schedule:', err);
    }
  };

  const handleDeleteSchedule = async (scheduleId: string) => {
    if (confirm('Are you sure you want to delete this schedule?')) {
      try {
        await api.delete(`/api/schedules/${scheduleId}`);
        refetch();
      } catch (err) {
        console.error('Failed to delete schedule:', err);
      }
    }
  };

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleString();
  };

  return (
    <div className="schedules-page">
      <div className="page-header">
        <h1>Schedules</h1>
        <button onClick={() => setShowForm(!showForm)}>
          {showForm ? 'Cancel' : 'New Schedule'}
        </button>
      </div>

      {showForm && (
        <div className="card schedule-form">
          <form onSubmit={handleCreateSchedule}>
            <div className="form-group">
              <label>Name</label>
              <input
                type="text"
                value={newSchedule.name}
                onChange={(e) => setNewSchedule({ ...newSchedule, name: e.target.value })}
                required
              />
            </div>

            <div className="form-group">
              <label>Description</label>
              <textarea
                value={newSchedule.description}
                onChange={(e) => setNewSchedule({ ...newSchedule, description: e.target.value })}
              />
            </div>

            <div className="form-group">
              <label>Cron Expression</label>
              <input
                type="text"
                placeholder="* * * * * (minute hour day month weekday)"
                value={newSchedule.cron_expression}
                onChange={(e) => setNewSchedule({ ...newSchedule, cron_expression: e.target.value })}
                required
              />
              <small>Format: minute hour day-of-month month day-of-week</small>
            </div>

            <div className="form-group">
              <label>Task Template (JSON)</label>
              <textarea
                value={newSchedule.task_template}
                onChange={(e) => setNewSchedule({ ...newSchedule, task_template: e.target.value })}
                rows={5}
              />
            </div>

            <button type="submit">Create Schedule</button>
          </form>
        </div>
      )}

      {isLoading ? (
        <div className="loading">Loading schedules...</div>
      ) : (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Cron</th>
                <th>Status</th>
                <th>Last Run</th>
                <th>Next Run</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {schedules?.schedules?.map((schedule: Schedule) => (
                <tr key={schedule.id}>
                  <td>{schedule.name}</td>
                  <td><code>{schedule.cron_expression}</code></td>
                  <td>
                    <span className={`status-badge ${schedule.enabled ? 'status-completed' : 'status-failed'}`}>
                      {schedule.enabled ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td>{formatDate(schedule.last_run)}</td>
                  <td>{formatDate(schedule.next_run)}</td>
                  <td>
                    <button onClick={() => handleToggleSchedule(schedule.id, schedule.enabled)}>
                      {schedule.enabled ? 'Disable' : 'Enable'}
                    </button>
                    {' '}
                    <button onClick={() => handleDeleteSchedule(schedule.id)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default Schedules;
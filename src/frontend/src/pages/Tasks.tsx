import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import api from '../api/client';

interface Task {
  id: string;
  name: string;
  description: string;
  status: string;
  created_at: string;
  scheduled_for?: string;
  result?: string;
}

function Tasks() {
  const [showForm, setShowForm] = useState(false);
  const [newTask, setNewTask] = useState({
    name: '',
    description: '',
    skill: '',
    parameters: '{}',
    scheduled_for: ''
  });

  const { data: tasks, isLoading, refetch } = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.get('/api/tasks').then(res => res.data)
  });

  const handleCreateTask = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.post('/api/tasks', {
        ...newTask,
        parameters: JSON.parse(newTask.parameters)
      });
      setShowForm(false);
      setNewTask({ name: '', description: '', skill: '', parameters: '{}', scheduled_for: '' });
      refetch();
    } catch (err) {
      console.error('Failed to create task:', err);
    }
  };

  const handleRunTask = async (taskId: string) => {
    try {
      await api.post(`/api/tasks/${taskId}/run`);
      refetch();
    } catch (err) {
      console.error('Failed to run task:', err);
    }
  };

  const handleDeleteTask = async (taskId: string) => {
    if (confirm('Are you sure you want to delete this task?')) {
      try {
        await api.delete(`/api/tasks/${taskId}`);
        refetch();
      } catch (err) {
        console.error('Failed to delete task:', err);
      }
    }
  };

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleString();
  };

  const getStatusClass = (status: string) => {
    return `status-badge status-${status}`;
  };

  return (
    <div className="tasks-page">
      <div className="page-header">
        <h1>Tasks</h1>
        <button onClick={() => setShowForm(!showForm)}>
          {showForm ? 'Cancel' : 'New Task'}
        </button>
      </div>

      {showForm && (
        <div className="card task-form">
          <form onSubmit={handleCreateTask}>
            <div className="form-group">
              <label>Name</label>
              <input
                type="text"
                value={newTask.name}
                onChange={(e) => setNewTask({ ...newTask, name: e.target.value })}
                required
              />
            </div>

            <div className="form-group">
              <label>Description</label>
              <textarea
                value={newTask.description}
                onChange={(e) => setNewTask({ ...newTask, description: e.target.value })}
              />
            </div>

            <div className="form-group">
              <label>Skill</label>
              <select
                value={newTask.skill}
                onChange={(e) => setNewTask({ ...newTask, skill: e.target.value })}
              >
                <option value="">Select skill...</option>
                <option value="web-search">Web Search</option>
                <option value="data-extraction">Data Extraction</option>
                <option value="math-operations">Math Operations</option>
                <option value="document-creation">Document Creation</option>
                <option value="notifications">Notifications</option>
              </select>
            </div>

            <div className="form-group">
              <label>Schedule (optional)</label>
              <input
                type="datetime-local"
                value={newTask.scheduled_for}
                onChange={(e) => setNewTask({ ...newTask, scheduled_for: e.target.value })}
              />
            </div>

            <button type="submit">Create Task</button>
          </form>
        </div>
      )}

      {isLoading ? (
        <div className="loading">Loading tasks...</div>
      ) : (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Status</th>
                <th>Created</th>
                <th>Scheduled</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {tasks?.tasks?.map((task: Task) => (
                <tr key={task.id}>
                  <td>{task.name}</td>
                  <td>
                    <span className={getStatusClass(task.status)}>{task.status}</span>
                  </td>
                  <td>{formatDate(task.created_at)}</td>
                  <td>{task.scheduled_for ? formatDate(task.scheduled_for) : '-'}</td>
                  <td>
                    <button onClick={() => handleRunTask(task.id)} disabled={task.status === 'running'}>
                      Run
                    </button>
                    {' '}
                    <button onClick={() => handleDeleteTask(task.id)}>Delete</button>
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

export default Tasks;
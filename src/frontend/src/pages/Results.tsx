import { useQuery } from '@tanstack/react-query';
import api from '../api/client';

function Results() {
  const { data: tasks, isLoading } = useQuery({
    queryKey: ['completed-tasks'],
    queryFn: () => api.get('/api/tasks?status=completed').then(res => res.data)
  });

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleString();
  };

  const downloadResult = (taskId: string, _taskName: string) => {
    // In a real implementation, this would download the result file
    window.open(`/api/tasks/${taskId}/result`, '_blank');
  };

  return (
    <div className="results-page">
      <h1>Results</h1>

      {isLoading ? (
        <div className="loading">Loading results...</div>
      ) : (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Task</th>
                <th>Completed</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {tasks?.tasks?.map((task: any) => (
                <tr key={task.id}>
                  <td>{task.name}</td>
                  <td>{formatDate(task.completed_at)}</td>
                  <td>
                    <button onClick={() => downloadResult(task.id, task.name)}>
                      Download
                    </button>
                  </td>
                </tr>
              ))}
              {(!tasks?.tasks || tasks.tasks.length === 0) && (
                <tr>
                  <td colSpan={3} style={{ textAlign: 'center' }}>No completed tasks yet</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default Results;
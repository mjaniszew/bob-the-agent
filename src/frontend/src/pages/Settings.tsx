function Settings() {
  return (
    <div className="settings-page">
      <h1>Settings</h1>

      <div className="card">
        <h2>Model Provider</h2>
        <div className="form-group">
          <label>Default Provider</label>
          <select>
            <option value="ollama">Ollama (Local)</option>
            <option value="anthropic">Anthropic (Cloud)</option>
            <option value="openai">OpenAI (Cloud)</option>
          </select>
        </div>

        <div className="form-group">
          <label>Ollama Model</label>
          <select>
            <option value="llama3.2">Llama 3.2</option>
            <option value="llama3.2:1b">Llama 3.2 1B (Fast)</option>
            <option value="codellama">Code Llama</option>
          </select>
        </div>
      </div>

      <div className="card">
        <h2>Notifications</h2>
        <div className="form-group">
          <label>
            <input type="checkbox" /> Enable Discord notifications
          </label>
        </div>
        <div className="form-group">
          <label>
            <input type="checkbox" /> Enable Email notifications
          </label>
        </div>
      </div>

      <div className="card">
        <h2>Web Interface</h2>
        <div className="form-group">
          <label>Port</label>
          <input type="number" value={81234} readOnly />
        </div>
        <div className="form-group">
          <label>
            <input type="checkbox" defaultChecked /> Enable authentication
          </label>
        </div>
      </div>

      <div className="card">
        <h2>About</h2>
        <p><strong>Mini Agent</strong> v1.0.0</p>
        <p>A containerized AI agent running 24/7 via Docker.</p>
        <p>Built with OpenClaw framework.</p>
      </div>
    </div>
  );
}

export default Settings;
import { NavLink } from 'react-router-dom';

interface LayoutProps {
  onLogout: () => void;
}

function Layout({ onLogout }: LayoutProps) {
  return (
    <div className="layout">
      <nav className="sidebar">
        <div className="logo">
          <h2>Mini Agent</h2>
        </div>
        <ul className="nav-links">
          <li>
            <NavLink to="/dashboard" className={({ isActive }) => isActive ? 'active' : ''}>
              Dashboard
            </NavLink>
          </li>
          <li>
            <NavLink to="/tasks" className={({ isActive }) => isActive ? 'active' : ''}>
              Tasks
            </NavLink>
          </li>
          <li>
            <NavLink to="/schedules" className={({ isActive }) => isActive ? 'active' : ''}>
              Schedules
            </NavLink>
          </li>
          <li>
            <NavLink to="/results" className={({ isActive }) => isActive ? 'active' : ''}>
              Results
            </NavLink>
          </li>
          <li>
            <NavLink to="/settings" className={({ isActive }) => isActive ? 'active' : ''}>
              Settings
            </NavLink>
          </li>
        </ul>
        <div className="nav-footer">
          <button onClick={onLogout} className="logout-btn">
            Sign Out
          </button>
        </div>
      </nav>
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}

import { Outlet } from 'react-router-dom';

export default Layout;
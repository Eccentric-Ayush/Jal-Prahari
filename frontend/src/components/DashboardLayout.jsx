import React from 'react';
import '../styles/dashboard.css';
import Sidebar from './Sidebar';

const DashboardLayout = ({ children }) => {
  return (
    <div className="dashboard-layout">
      <main className="dashboard-map-area">
        {children}
      </main>
      <aside className="dashboard-sidebar-area">
        <Sidebar />
      </aside>
    </div>
  );
};

export default DashboardLayout;

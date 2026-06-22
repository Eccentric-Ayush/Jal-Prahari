import React from 'react';
import AlertList from './AlertList';
import SensorDetails from './SensorDetails';
import ConnectionStatusBadge from './ConnectionStatusBadge';
import { useSelectedSensor } from '../hooks/useSelectedSensor';

const Sidebar = () => {
  const { selectedSensor } = useSelectedSensor();

  return (
    <div className="sidebar-container">
      <div className="sidebar-header">
        <h2>Analytics Panel</h2>
        <p>Urban Flood Monitoring</p>
        <ConnectionStatusBadge />
      </div>

      <div className="sidebar-content">
        <AlertList />
        
        {selectedSensor ? (
          <SensorDetails />
        ) : (
          <div className="sidebar-empty-state">
            <p>Click on a map sensor to view detailed analytics.</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default Sidebar;

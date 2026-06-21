import React from 'react';
import WarningBanner from './WarningBanner';
import { useRiskAlerts } from '../hooks/useRiskAlerts';

const AlertList = () => {
  const alerts = useRiskAlerts();

  if (alerts.length === 0) return null;

  return (
    <div className="alert-list-container">
      <div className="alert-list-header">
        <h3>Active Alerts ({alerts.length})</h3>
      </div>
      <div className="alert-list-scroll">
        {alerts.map((alert) => (
          <WarningBanner key={alert.sensor_id} alert={alert} />
        ))}
      </div>
    </div>
  );
};

export default AlertList;

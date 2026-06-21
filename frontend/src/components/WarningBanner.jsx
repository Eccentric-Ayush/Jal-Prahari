import React, { useContext } from 'react';
import { SensorContext } from '../context/SensorContext';

const WarningBanner = ({ alert }) => {
  const { dispatch } = useContext(SensorContext);

  // Determine styling based on risk level
  const isCritical = alert.risk_index >= 0.85;
  const bannerClass = isCritical ? 'warning-banner critical' : 'warning-banner high';

  const handleClick = () => {
    // Also let users click an alert to view that sensor
    dispatch({ type: 'SELECT_SENSOR', payload: alert });
  };

  return (
    <div className={bannerClass} onClick={handleClick}>
      <div className="warning-banner-icon">
        {isCritical ? '⚠️' : '🔔'}
      </div>
      <div className="warning-banner-content">
        <h4>Sensor #{alert.sensor_id}</h4>
        <div className="warning-banner-details">
          <span>Risk: {alert.risk_index.toFixed(2)} ({alert.risk_level})</span>
          <span className="warning-time">{new Date(alert.generated_at).toLocaleTimeString()}</span>
        </div>
      </div>
    </div>
  );
};

export default WarningBanner;

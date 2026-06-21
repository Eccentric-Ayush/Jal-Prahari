import React, { useContext } from 'react';
import TimeSeriesChart from './TimeSeriesChart';
import { useSelectedSensor } from '../hooks/useSelectedSensor';
import { SensorContext } from '../context/SensorContext';

const SensorDetails = () => {
  const { selectedSensor, sensorHistory, historyLoading, historyError, dispatch } = useSelectedSensor();

  if (!selectedSensor) return null;

  return (
    <div className="sensor-details-container">
      <div className="sensor-details-header">
        <div className="sensor-details-title">
          <h3>Sensor #{selectedSensor.sensor_id}</h3>
          <span className={`badge ${selectedSensor.risk_level?.toLowerCase()}`}>
            {selectedSensor.risk_level}
          </span>
        </div>
        <button 
          className="close-button"
          onClick={() => dispatch({ type: 'DESELECT_SENSOR' })}
        >
          ✕
        </button>
      </div>

      <div className="sensor-metrics-grid">
        <div className="metric-box">
          <span className="metric-label">Risk Index</span>
          <span className="metric-value">{selectedSensor.risk_index.toFixed(3)}</span>
        </div>
        <div className="metric-box">
          <span className="metric-label">Elevation</span>
          <span className="metric-value">{selectedSensor.elevation.toFixed(2)}m</span>
        </div>
      </div>

      <div className="chart-container">
        <h4>Recent History</h4>
        {historyLoading ? (
          <div className="chart-loading">Loading telemetry data...</div>
        ) : historyError ? (
          <div className="chart-error">{historyError?.message || String(historyError)}</div>
        ) : (
          <TimeSeriesChart historyData={sensorHistory} />
        )}
      </div>
    </div>
  );
};

export default SensorDetails;

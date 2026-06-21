import React from 'react';
import '../styles/sensors.css';

const LoadingOverlay = ({ loading, error, clusterCount }) => {
  if (error) {
    return (
      <div className="overlay-container error-overlay">
        <p>⚠️ Backend Error: {error?.message || String(error)}</p>
        <small>Retrying automatically...</small>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="overlay-container loading-overlay">
        <div className="spinner"></div>
        <p>Loading Digital Twin Data...</p>
      </div>
    );
  }

  if (clusterCount === 0) {
    return (
      <div className="overlay-container info-overlay">
        <p>No risk clusters found above current threshold.</p>
      </div>
    );
  }

  return null; // Don't render anything if data is successfully loaded and > 0
};

export default LoadingOverlay;

import React from 'react';
import { createRoot } from 'react-dom/client';
import maplibregl from 'maplibre-gl';
import '../styles/sensors.css';

/**
 * Functional component for the Popup content.
 */
export const RiskPopupContent = ({ cluster, generatedAt }) => {
  const getBadgeClass = (level) => {
    switch(level?.toUpperCase()) {
      case 'CRITICAL': return 'badge critical';
      case 'HIGH': return 'badge high';
      case 'MODERATE': return 'badge moderate';
      case 'LOW': return 'badge low';
      default: return 'badge';
    }
  };

  return (
    <div className="risk-popup-container">
      <div className="risk-popup-header">
        <span>Sensor #{cluster.sensor_id}</span>
        <span className={getBadgeClass(cluster.risk_level)}>{cluster.risk_level}</span>
      </div>
      <div className="risk-popup-row">
        <strong>Risk Index:</strong>
        <span>{cluster.risk_index.toFixed(3)}</span>
      </div>
      <div className="risk-popup-row">
        <strong>Elevation:</strong>
        <span>{cluster.elevation.toFixed(2)} m</span>
      </div>
      <div className="risk-popup-row">
        <strong>Generated:</strong>
        <span>{new Date(generatedAt).toLocaleTimeString()}</span>
      </div>
    </div>
  );
};

/**
 * Utility to mount a React component inside a MapLibre Popup.
 */
export const showRiskPopup = (map, feature, generatedAt) => {
  const coordinates = feature.geometry.coordinates.slice();
  const cluster = feature.properties;

  // Ensure that if the map is zoomed out such that multiple
  // copies of the feature are visible, the popup appears
  // over the copy being pointed to.
  while (Math.abs(map.getCenter().lng - coordinates[0]) > 180) {
    coordinates[0] += map.getCenter().lng > coordinates[0] ? 360 : -360;
  }

  const popupNode = document.createElement('div');
  const root = createRoot(popupNode);
  root.render(<RiskPopupContent cluster={cluster} generatedAt={generatedAt} />);

  new maplibregl.Popup({ closeButton: true, closeOnClick: true })
    .setLngLat(coordinates)
    .setDOMContent(popupNode)
    .addTo(map);
};

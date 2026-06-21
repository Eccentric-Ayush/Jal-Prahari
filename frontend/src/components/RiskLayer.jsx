import { useEffect, useRef } from 'react';
import { getRiskColorExpression, getRiskRadiusExpression } from '../utils/colorScale';
import { showRiskPopup } from './RiskPopup';

const RiskLayer = ({ map, clusters, generatedAt, dispatch }) => {
  const animationRef = useRef(null);

  useEffect(() => {
    if (!map || !clusters || clusters.length === 0) return;

    // 1. Convert Backend Response to GeoJSON
    // GeoJSON source updates are O(1) in the DOM, making them highly scalable 
    // for 5000+ nodes compared to injecting 5000 individual DOM Markers.
    const geojsonData = {
      type: 'FeatureCollection',
      features: clusters.map(cluster => ({
        type: 'Feature',
        geometry: {
          type: 'Point',
          coordinates: [cluster.longitude, cluster.latitude]
        },
        properties: cluster // includes sensor_id, risk_index, risk_level, elevation
      }))
    };

    if (map.getSource('risk-clusters')) {
      // Efficient Data Update: Just swap the source data payload
      map.getSource('risk-clusters').setData(geojsonData);
    } else {
      // Initial Setup: Create Source and Layers
      map.addSource('risk-clusters', {
        type: 'geojson',
        data: geojsonData
      });

      // Optional: A pulse layer specifically for CRITICAL nodes
      map.addLayer({
        id: 'risk-circles-pulse',
        type: 'circle',
        source: 'risk-clusters',
        filter: ['==', 'risk_level', 'CRITICAL'],
        paint: {
          'circle-color': '#FF4D4F',
          'circle-radius': 16,
          'circle-opacity': 0.4,
          'circle-stroke-width': 0,
          'circle-pitch-alignment': 'map'
        }
      });

      // Main circle layer for all nodes
      map.addLayer({
        id: 'risk-circles',
        type: 'circle',
        source: 'risk-clusters',
        paint: {
          'circle-color': getRiskColorExpression(),
          'circle-radius': getRiskRadiusExpression(),
          'circle-opacity': 0.8,
          'circle-stroke-width': 1.5,
          'circle-stroke-color': '#FFFFFF',
          'circle-pitch-alignment': 'map' // Renders circles flat on the 3D terrain
        }
      });

      // Interactivity: Click to open popup and update Sidebar context
      map.on('click', 'risk-circles', (e) => {
        if (e.features.length > 0) {
          const feature = e.features[0];
          showRiskPopup(map, feature, generatedAt);
          if (dispatch) {
            dispatch({ type: 'SELECT_SENSOR', payload: feature.properties });
          }
        }
      });

      // Pointer cursors for UX
      map.on('mouseenter', 'risk-circles', () => {
        map.getCanvas().style.cursor = 'pointer';
      });
      map.on('mouseleave', 'risk-circles', () => {
        map.getCanvas().style.cursor = '';
      });

      // Pulse Animation Loop (GPU friendly, updates paint property not DOM)
      let t = 0;
      const animatePulse = () => {
        t = (t + 0.05) % (Math.PI * 2);
        // Sinewave pulsing from 16px to 28px
        const pulseRadius = 16 + (Math.sin(t) + 1) * 6;
        // Fade opacity out as radius grows
        const pulseOpacity = Math.max(0.1, 0.6 - ((Math.sin(t) + 1) * 0.2));

        if (map.getLayer('risk-circles-pulse')) {
          map.setPaintProperty('risk-circles-pulse', 'circle-radius', pulseRadius);
          map.setPaintProperty('risk-circles-pulse', 'circle-opacity', pulseOpacity);
        }
        animationRef.current = requestAnimationFrame(animatePulse);
      };
      
      animatePulse();
    }

    return () => {
      // Cleanup is typically handled at the map level, but we clear animation frames
      // to avoid memory leaks if this component unmounts without destroying the map.
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [map, clusters, generatedAt]);

  return null; // This component doesn't render standard React DOM elements
};

export default RiskLayer;

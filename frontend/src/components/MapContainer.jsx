import React, { useRef, useEffect, useState } from 'react';
import maplibregl from 'maplibre-gl';
import '../styles/map.css';
import 'maplibre-gl/dist/maplibre-gl.css';

const MapContainer = () => {
  const mapContainerRef = useRef(null);
  const mapRef = useRef(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    // 1. Validate MapTiler Key
    const maptilerKey = import.meta.env.VITE_MAPTILER_KEY;
    if (!maptilerKey) {
      setError("MapTiler key is missing. Please check your .env file.");
      return;
    }

    // 2. Prevent Duplicate Initialization (Strict Mode Safe)
    if (mapRef.current) return;

    try {
      // 3. Initialize MapLibre
      const map = new maplibregl.Map({
        container: mapContainerRef.current,
        style: `https://api.maptiler.com/maps/streets-v2/style.json?key=${maptilerKey}`,
        center: [77.2090, 28.6139], // Centered on Delhi
        zoom: 11, // Close enough to see city detail
        pitch: 60, // Tilted camera for 3D perspective
        bearing: -20, // Slightly rotated for cinematic angle
        antialias: true // Essential for smooth 3D geometry rendering
      });

      mapRef.current = map;

      // 4. 3D Terrain Configuration
      map.on('load', () => {
        console.log("Map style loaded. Configuring 3D Terrain...");
        
        // Add global DEM source from MapTiler
        map.addSource('terrain-dem', {
          'type': 'raster-dem',
          'url': `https://api.maptiler.com/tiles/terrain-rgb-v2/tiles.json?key=${maptilerKey}`,
          'tileSize': 256
        });

        // Set terrain with exaggeration
        map.setTerrain({ 'source': 'terrain-dem', 'exaggeration': 1.5 });
        
        console.log("Terrain layer activated.");
      });

      // Handle initialization errors
      map.on('error', (e) => {
        console.error("MapLibre GL Error:", e.error?.message || e);
      });

    } catch (err) {
      console.error("Failed to initialize WebGL context:", err);
      setError("Failed to initialize the 3D map. Your browser may not support WebGL.");
    }

    // 5. Cleanup on Unmount (Prevents memory leaks)
    return () => {
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
  }, []);

  if (error) {
    return <div className="map-error-fallback">{error}</div>;
  }

  return <div ref={mapContainerRef} className="map-container" />;
};

export default MapContainer;

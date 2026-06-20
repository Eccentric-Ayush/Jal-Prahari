import React from 'react';
import MapContainer from './components/MapContainer';
import './styles/map.css';

function App() {
  return (
    <>
      {/* 
        Clean rendering flow: App acts as the layout root.
        Currently it only renders the full-screen map.
        Future overlays (Dashboard, Alerts) will sit as sibling components here
        using absolute positioning (z-index) over the MapContainer.
      */}
      <MapContainer />
    </>
  );
}

export default App;

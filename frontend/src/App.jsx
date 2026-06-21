import React from 'react';
import MapContainer from './components/MapContainer';
import './styles/map.css';

import { SensorProvider } from './context/SensorContext';
import DashboardLayout from './components/DashboardLayout';

function App() {
  return (
    <SensorProvider>
      <DashboardLayout>
        <MapContainer />
      </DashboardLayout>
    </SensorProvider>
  );
}

export default App;

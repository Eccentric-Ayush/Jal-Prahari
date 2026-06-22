// frontend/src/components/ConnectionStatusBadge.jsx
//
// Non-intrusive badge in the sidebar header that shows the current
// WebSocket connection mode.
//
// wsStatus values:
//   'connecting'  → pulsing grey dot "Connecting..."
//   'live'        → solid green dot "Live"
//   'reconnecting'→ pulsing amber dot "Reconnecting..."
//   'polling'     → solid blue dot "Polling"

import React, { useContext } from 'react';
import { SensorContext } from '../context/SensorContext';

const STATUS_CONFIG = {
  connecting:   { dot: '#64748b', label: 'Connecting…',   pulse: true  },
  live:         { dot: '#22c55e', label: 'Live',           pulse: false },
  reconnecting: { dot: '#f59e0b', label: 'Reconnecting…', pulse: true  },
  polling:      { dot: '#3b82f6', label: 'Polling',        pulse: false },
  error:        { dot: '#ef4444', label: 'Error',          pulse: false },
};

const ConnectionStatusBadge = () => {
  const { wsStatus } = useContext(SensorContext);
  const config = STATUS_CONFIG[wsStatus] || STATUS_CONFIG.connecting;

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '6px',
      fontSize: '0.75rem',
      color: '#94a3b8',
      marginTop: '4px',
    }}>
      <span style={{
        display: 'inline-block',
        width: '8px',
        height: '8px',
        borderRadius: '50%',
        backgroundColor: config.dot,
        animation: config.pulse ? 'badge-pulse 1.2s ease-in-out infinite' : 'none',
        flexShrink: 0,
      }} />
      <span>{config.label}</span>

      <style>{`
        @keyframes badge-pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50%       { opacity: 0.4; transform: scale(0.75); }
        }
      `}</style>
    </div>
  );
};

export default ConnectionStatusBadge;

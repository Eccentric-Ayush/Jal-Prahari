// frontend/src/context/SensorContext.jsx
//
// ─── Global state provider for the Jal-Prahari dashboard ─────────────────────
//
// State managed here:
//   riskData      — live cluster predictions (populated by WebSocket or HTTP fallback)
//   wsStatus      — 'live' | 'reconnecting' | 'polling' | 'connecting' | 'error'
//   selectedSensor — the cluster the user clicked on the map or in the alert list
//   sensorHistory  — time-series water-level data for the selected sensor
//   historyLoading — loading state for the history fetch
//   historyError   — error string if history fetch fails
//
// Why context rather than component-level state?
//   Both MapContainer (risk circles, popup) and Sidebar (alert list, chart)
//   need riskData simultaneously.  Hoisting to context means ONE WebSocket
//   connection and ONE set of clusters — no duplicate fetches.

import React, { createContext, useContext, useReducer } from 'react';
import { useRiskSocket } from '../hooks/useRiskSocket';

export const SensorContext = createContext();

// Convenience hook — eliminates the useContext(SensorContext) boilerplate
// in every consumer component.
export const useSensorContext = () => useContext(SensorContext);

// ─────────────────────────────────────────────────────────────────────────────
// Reducer
// ─────────────────────────────────────────────────────────────────────────────

const initialState = {
  selectedSensor: null,
  sensorHistory: null,
  historyLoading: false,
  historyError: null,
};

function sensorReducer(state, action) {
  switch (action.type) {
    case 'SELECT_SENSOR':
      return {
        ...state,
        selectedSensor: action.payload,
        // Reset history state when a new sensor is selected
        sensorHistory: null,
        historyError: null,
        historyLoading: false,
      };
    case 'SET_HISTORY_LOADING':
      return { ...state, historyLoading: action.payload };
    case 'SET_SENSOR_HISTORY':
      return {
        ...state,
        sensorHistory: action.payload,
        historyLoading: false,
        historyError: null,
      };
    case 'SET_HISTORY_ERROR':
      return {
        ...state,
        historyError: action.payload,
        historyLoading: false,
      };
    case 'DESELECT_SENSOR':
      return initialState;
    default:
      return state;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Provider
// ─────────────────────────────────────────────────────────────────────────────

export const SensorProvider = ({ children }) => {
  const [state, dispatch] = useReducer(sensorReducer, initialState);

  // ── Real-time data source ─────────────────────────────────────────────────
  // useRiskSocket opens ws://localhost:8000/ws/risk and falls back to HTTP
  // polling if the WebSocket is unavailable.  This is the SINGLE source of
  // truth for all risk cluster data across the entire dashboard.
  const { riskData, wsStatus } = useRiskSocket();

  const value = {
    // Sensor selection state
    ...state,
    dispatch,

    // Live prediction data (populated by WS or HTTP polling fallback)
    riskData,

    // Connection mode indicator — consumed by ConnectionStatusBadge
    wsStatus,
  };

  return (
    <SensorContext.Provider value={value}>
      {children}
    </SensorContext.Provider>
  );
};

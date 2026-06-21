import React, { createContext, useReducer } from 'react';
import { useRiskClusters } from '../hooks/useRiskClusters';

export const SensorContext = createContext();

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
        // Reset history when a new sensor is selected
        sensorHistory: null,
        historyError: null
      };
    case 'SET_HISTORY_LOADING':
      return { ...state, historyLoading: action.payload };
    case 'SET_SENSOR_HISTORY':
      return { 
        ...state, 
        sensorHistory: action.payload, 
        historyLoading: false, 
        historyError: null 
      };
    case 'SET_HISTORY_ERROR':
      return { 
        ...state, 
        historyError: action.payload, 
        historyLoading: false 
      };
    case 'DESELECT_SENSOR':
      return initialState;
    default:
      return state;
  }
}

export const SensorProvider = ({ children }) => {
  console.log("SensorProvider rendering");
  const [state, dispatch] = useReducer(sensorReducer, initialState);

  // Poll global predictions every 5 seconds at the Context level
  // This is the single source of truth for all dashboard and map components
  const riskData = useRiskClusters(0.0, 100, 5000);

  // Expose dispatch, internal state, and the polled riskData globally
  const value = {
    ...state,
    riskData,
    dispatch,
  };

  return (
    <SensorContext.Provider value={value}>
      {children}
    </SensorContext.Provider>
  );
};

import { useEffect, useContext } from 'react';
import { SensorContext } from '../context/SensorContext';
import { fetchSensorHistory } from '../services/historyService';

export const useSelectedSensor = () => {
  const { selectedSensor, sensorHistory, historyLoading, historyError, dispatch } = useContext(SensorContext);

  useEffect(() => {
    if (!selectedSensor) return;

    let isMounted = true;

    const loadHistory = async () => {
      dispatch({ type: 'SET_HISTORY_LOADING', payload: true });
      try {
        const historyData = await fetchSensorHistory(selectedSensor.sensor_id, 1, 50);
        if (isMounted) {
          dispatch({ type: 'SET_SENSOR_HISTORY', payload: historyData });
        }
      } catch (err) {
        if (isMounted) {
          dispatch({ type: 'SET_HISTORY_ERROR', payload: err.message });
        }
      }
    };

    loadHistory();

    return () => {
      isMounted = false;
    };
  }, [selectedSensor, dispatch]);

  return { selectedSensor, sensorHistory, historyLoading, historyError, dispatch };
};

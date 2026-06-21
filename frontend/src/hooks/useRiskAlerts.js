import { useMemo, useContext } from 'react';
import { SensorContext } from '../context/SensorContext';

export const useRiskAlerts = () => {
  const { riskData } = useContext(SensorContext);

  const activeAlerts = useMemo(() => {
    if (!riskData || !riskData.clusters) return [];
    
    return riskData.clusters
      .filter(cluster => cluster.risk_index > 0.7)
      .sort((a, b) => b.risk_index - a.risk_index); // highest risk first
  }, [riskData]);

  return activeAlerts;
};

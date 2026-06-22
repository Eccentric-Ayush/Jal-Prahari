import { useState, useEffect, useCallback } from 'react';
import { fetchRiskClusters } from '../services/predictionService';

export const useRiskClusters = (minRisk = 0.0, limit = 100, pollIntervalMs = 5000) => {
  const [data, setData] = useState({ generated_at: null, cluster_count: 0, clusters: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const pollData = useCallback(async () => {
    try {
      const result = await fetchRiskClusters(minRisk, limit);
      setData(result);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [minRisk, limit]);

  useEffect(() => {
    // Initial fetch
    pollData();

    // Set up polling interval
    const intervalId = setInterval(() => {
      pollData();
    }, pollIntervalMs);

    // Cleanup to prevent memory leaks when component unmounts or dependencies change
    return () => clearInterval(intervalId);
  }, [pollData, pollIntervalMs]);

  return { ...data, loading, error };
};

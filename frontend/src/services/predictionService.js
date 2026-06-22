export const fetchRiskClusters = async (minRisk = 0.0, limit = 100) => {
  try {
    const response = await fetch(`/api/predict/risk?min_risk=${minRisk}&limit=${limit}`);
    
    if (!response.ok) {
      throw new Error(`Failed to fetch risk clusters: ${response.status} ${response.statusText}`);
    }
    
    const data = await response.json();
    return data;
  } catch (error) {
    console.error("Prediction Service Error:", error);
    throw error;
  }
};

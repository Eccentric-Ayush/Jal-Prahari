export const fetchRiskClusters = async (minRisk = 0.0, limit = 100) => {
  try {
    const baseUrl = import.meta.env.VITE_API_BASE_URL || '';
    const response = await fetch(`${baseUrl}/api/predict/risk?min_risk=${minRisk}&limit=${limit}`);
    
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

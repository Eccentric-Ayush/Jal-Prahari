export const fetchSensorHistory = async (sensorId, page = 1, pageSize = 50) => {
  try {
    const baseUrl = import.meta.env.VITE_API_BASE_URL || '';
    const response = await fetch(`${baseUrl}/api/sensors/${sensorId}/history?page=${page}&page_size=${pageSize}`);
    
    if (!response.ok) {
      throw new Error(`Failed to fetch sensor history: ${response.status} ${response.statusText}`);
    }
    
    const data = await response.json();
    return data;
  } catch (error) {
    console.error("History Service Error:", error);
    throw error;
  }
};

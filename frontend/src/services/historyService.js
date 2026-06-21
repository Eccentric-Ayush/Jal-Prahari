export const fetchSensorHistory = async (sensorId, page = 1, pageSize = 50) => {
  try {
    const response = await fetch(`/api/sensors/${sensorId}/history?page=${page}&page_size=${pageSize}`);
    
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

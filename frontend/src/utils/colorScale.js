/**
 * Maps categorical risk levels to distinct hex colors.
 */
export const getCategoryColor = (riskLevel) => {
  switch (riskLevel?.toUpperCase()) {
    case 'CRITICAL': return '#FF4D4F'; // Red
    case 'HIGH': return '#FAAD14'; // Orange
    case 'MODERATE': return '#FADB14'; // Yellow
    case 'LOW': return '#52C41A'; // Green
    default: return '#A6A6A6'; // Gray for unknown
  }
};

/**
 * Alternative: Continuous gradient for smoother visual transitions.
 * Returns an array format compatible with MapLibre's 'interpolate' expression.
 */
export const getRiskColorExpression = () => {
  return [
    'interpolate',
    ['linear'],
    ['get', 'risk_index'],
    0.00, '#52C41A', // LOW
    0.25, '#52C41A',
    0.26, '#FADB14', // MODERATE
    0.50, '#FADB14',
    0.51, '#FAAD14', // HIGH
    0.75, '#FAAD14',
    0.76, '#FF4D4F', // CRITICAL
    1.00, '#FF4D4F'
  ];
};

/**
 * Dynamic radius based on risk_index. Higher risk = larger circle.
 */
export const getRiskRadiusExpression = () => {
  return [
    'interpolate',
    ['linear'],
    ['get', 'risk_index'],
    0.0, 6,
    0.5, 10,
    1.0, 16
  ];
};

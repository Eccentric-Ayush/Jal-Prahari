import React, { useMemo } from 'react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend
} from 'recharts';

const TimeSeriesChart = ({ historyData }) => {
  // Memoize formatting to prevent recalculating on every re-render
  // This helps when scaling to 5000+ total sensors and complex dashboard updates
  const chartData = useMemo(() => {
    if (!historyData || !historyData.history) return [];
    
    // API returns history in descending time order (newest first).
    // For charting (left to right = old to new), we reverse it.
    return [...historyData.history].reverse().map(log => ({
      ...log,
      formattedTime: new Date(log.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      water_level: Number(log.water_level.toFixed(2))
    }));
  }, [historyData]);

  if (chartData.length === 0) {
    return <div className="chart-empty">No historical data available.</div>;
  }

  return (
    <div className="time-series-chart">
      <div className="chart-meta">
        <small>Showing {historyData.history.length} of {historyData.total} readings</small>
      </div>
      <ResponsiveContainer width="100%" height={250}>
        <LineChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#333" />
          <XAxis 
            dataKey="formattedTime" 
            stroke="#888" 
            fontSize={12} 
            tickMargin={10} 
          />
          <YAxis 
            stroke="#888" 
            fontSize={12} 
            tickFormatter={(value) => `${value}m`} 
          />
          <Tooltip 
            contentStyle={{ backgroundColor: '#1a1a1a', border: '1px solid #333' }}
            itemStyle={{ color: '#00e5ff' }}
            labelStyle={{ color: '#ccc' }}
          />
          <Legend wrapperStyle={{ fontSize: '12px' }} />
          <Line 
            type="monotone" 
            dataKey="water_level" 
            name="Water Level (m)" 
            stroke="#00e5ff" 
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 6, fill: '#00e5ff', stroke: '#fff' }}
            isAnimationActive={false} // Disable animation for performance on frequent updates
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};

// React.memo prevents the chart from re-rendering unless historyData actually changes
export default React.memo(TimeSeriesChart);

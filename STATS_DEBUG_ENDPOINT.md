# Stats Debug Endpoint Documentation

## Overview
The `/api/v1/trades/stats/debug` endpoint provides comprehensive debugging information for troubleshooting issues with the trading statistics calculation.

## Endpoint
```
GET /api/v1/trades/stats/debug
```

## Authentication
Requires a valid authentication token (automatically handled by the frontend API client).

## Response Structure

The endpoint returns a detailed JSON object with the following sections:

### 1. **timestamp**
- ISO timestamp when the debug data was generated

### 2. **user_info**
- `user_id`: The authenticated user's ID
- `email`: User's email address
- `role`: User's role (admin, trader, etc.)

### 3. **database_queries**
- `total_count`: Total number of trades in the database for this user
- `fetched_count`: Number of trades successfully fetched
- `query_status`: Status of the database query ("success" or error)
- `status_breakdown`: Count of trades grouped by status (won, lost, etc.)

### 4. **cache_status**
- `cache_key`: The Redis cache key used for this user's stats
- `is_cached`: Boolean indicating if stats are currently cached
- `cached_value`: The cached statistics (if available)

### 5. **service_results**
- `stats`: The calculated statistics from the UserTradesService
- `is_none`: Boolean indicating if stats returned None
- `is_empty`: Boolean indicating if stats returned an empty object

### 6. **calculations**
Detailed breakdown of manual calculations performed on the raw data:
- `total_trades`: Total number of trades
- `trades_with_profit_data`: Trades that have profit data
- `win_count`: Number of winning trades
- `loss_count`: Number of losing trades
- `breakeven_count`: Number of breakeven trades
- `win_rate_percent`: Win rate as a percentage
- `total_pnl`: Total profit/loss
- `gross_profit`: Sum of all winning trades
- `gross_loss`: Sum of all losing trades
- `avg_win`: Average profit per winning trade
- `avg_loss`: Average loss per losing trade
- `largest_win`: Largest single win
- `largest_loss`: Largest single loss
- `profit_factor`: Ratio of gross profit to gross loss

### 7. **sample_data**
- `first_3_trades`: Array of the 3 most recent trades
- `last_3_trades`: Array of the 3 oldest trades
- `sample_profit_values`: Profit values from the first 5 trades

### 8. **date_analysis**
- `oldest_trade`: Timestamp of the oldest trade
- `newest_trade`: Timestamp of the most recent trade
- `total_span_days`: Number of days between oldest and newest trade

### 9. **errors**
Array of any errors encountered during the debug process. Each error includes:
- `stage`: Which stage of debugging failed
- `error`: Error message
- `traceback`: Full error traceback (if available)

## Usage Examples

### Frontend (TypeScript/React)
```typescript
import { api } from '@/services/api';

// Call the debug endpoint
const debugStats = async () => {
  try {
    const response = await api.trades.statsDebug();
    console.log('Debug Info:', response.data);
  } catch (error) {
    console.error('Debug endpoint error:', error);
  }
};
```

### Direct API Call (curl)
```bash
curl -X GET "https://your-api-url.com/api/v1/trades/stats/debug" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json"
```

### Browser Console
```javascript
// If you have the api object available
api.trades.statsDebug().then(res => console.log(res.data));
```

## Common Debugging Scenarios

### Scenario 1: Stats showing zero despite having trades
**Check:**
1. `database_queries.total_count` - Are trades being saved to the database?
2. `calculations.trades_with_profit_data` - Do trades have profit data?
3. `errors` - Are there any calculation errors?

### Scenario 2: Cached stats are stale
**Check:**
1. `cache_status.is_cached` - Is the data cached?
2. `cache_status.cached_value` - What are the cached values?
3. Compare `service_results.stats` with `calculations` - Do they match?

### Scenario 3: Discrepancy between frontend and backend
**Check:**
1. `service_results.stats` - What is the backend calculating?
2. `calculations` - Manual verification of the calculations
3. `sample_data` - Inspect actual trade data

### Scenario 4: Performance issues
**Check:**
1. `database_queries.total_count` - How many trades need to be processed?
2. `cache_status.is_cached` - Is caching working properly?
3. `date_analysis.total_span_days` - How much data is being processed?

## Error Handling

The endpoint is designed to be fault-tolerant. Even if individual sections fail, it will:
- Continue processing other sections
- Log errors in the `errors` array
- Return partial debug information
- Include full tracebacks for debugging

## Notes

- This endpoint is for debugging purposes only and should not be used in production UI
- The endpoint may return large amounts of data for users with many trades
- Consider adding pagination if debugging users with 1000+ trades
- Cache invalidation happens automatically when new trades are saved

## Related Endpoints

- `/api/v1/trades/stats` - Normal stats endpoint (production)
- `/api/v1/trades/history` - Trade history
- `/api/v1/trades/active` - Active trades

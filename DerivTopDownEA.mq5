//+------------------------------------------------------------------+
//|                                           TopDownEA.mq5          |
//|                   Multi-Timeframe Top-Down Strategy              |
//+------------------------------------------------------------------+
#property copyright "MaliBot 2026 - Aligned with Python Bot Config"
#property version   "1.00"
#property description "Top-Down Market Structure Analysis EA"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//+------------------------------------------------------------------+
//| Input Parameters                                                 |
//+------------------------------------------------------------------+
//--- Risk Management
input double   FixedLotSize = 0.1;              // Lot Size
input double   MaxRiskPercent = 1.0;            // Max Risk Per Trade (% of balance)
input int      MaxTradesPerDay = 30;            // Maximum Trades Per Day
input int      CooldownSeconds = 180;           // Cooldown Between Trades (seconds)

//--- Strategy Parameters
input int      RSI_Period = 14;                 // RSI Period
input int      RSI_BuyThreshold = 58;           // RSI Buy Threshold
input int      RSI_SellThreshold = 42;          // RSI Sell Threshold
input int      RSI_MaxThreshold = 75;           // RSI Overbought Limit
input int      RSI_MinThreshold = 25;           // RSI Oversold Limit

input int      ADX_Period = 14;                 // ADX Period
input int      ADX_Threshold = 25;              // Minimum ADX (Trend Strength)

input int      ATR_Period = 14;                 // ATR Period
input double   MomentumThreshold = 1.5;         // Momentum Close Threshold (x ATR)

//--- TP/SL Settings
input double   MinTPDistancePercent = 0.2;      // Minimum TP Distance (%)
input double   MaxSLDistancePercent = 0.5;      // Maximum SL Distance (%)
input double   MinRRRatio = 2.5;                // Minimum Risk:Reward Ratio

//--- Trailing Stop Settings (4-Tier System - Aligned with Python Config)
input bool     EnableMultiTierTrailing = true;  // Enable Multi-Tier Trailing Stop
input double   TrailTrigger1 = 25.0;            // Tier 1: Trigger at % Profit
input double   TrailStop1 = 8.0;                // Tier 1: Trail % Behind
input double   TrailTrigger2 = 40.0;            // Tier 2: Trigger at % Profit
input double   TrailStop2 = 12.0;               // Tier 2: Trail % Behind
input double   TrailTrigger3 = 60.0;            // Tier 3: Trigger at % Profit
input double   TrailStop3 = 18.0;               // Tier 3: Trail % Behind
input double   TrailTrigger4 = 100.0;           // Tier 4: Trigger at % Profit
input double   TrailStop4 = 25.0;               // Tier 4: Trail % Behind

//--- Swing Detection
input int      SwingLookback = 20;              // Candles for Swing Detection
input int      MinSwingWindow = 5;              // Minimum Window for Swing Points

//--- Early Exit Settings
input bool     EnableEarlyExit = false;         // Enable Early Exit (Fast Failure)
input int      EarlyExitTimeSeconds = 45;       // Early Exit Time Window
input double   EarlyExitLossPercent = 5.0;      // Early Exit at % Loss

//+------------------------------------------------------------------+
//| Global Variables                                                 |
//+------------------------------------------------------------------+
datetime lastTradeTime = 0;
int      tradesCount = 0;
int      consecutiveLosses = 0;
double   dailyPnL = 0.0;
datetime currentDate = 0;

ulong    activeTicket = 0;              // Active position ticket
double   trailingStopPrice = 0.0;       // Current trailing stop price
int      activeTrailingTier = 0;        // Active trailing tier (1-3)

//--- Indicator Handles
int handleRSI_5M;
int handleADX_5M;
int handleATR_1M;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   //--- Initialize indicators
   handleRSI_5M = iRSI(_Symbol, PERIOD_M5, RSI_Period, PRICE_CLOSE);
   handleADX_5M = iADX(_Symbol, PERIOD_M5, ADX_Period);
   handleATR_1M = iATR(_Symbol, PERIOD_M1, ATR_Period);
   
   if(handleRSI_5M == INVALID_HANDLE || handleADX_5M == INVALID_HANDLE || handleATR_1M == INVALID_HANDLE)
   {
      Print("❌ Failed to create indicator handles");
      return(INIT_FAILED);
   }
   
   //--- Set trade settings
   trade.SetExpertMagicNumber(123456);
   trade.SetDeviationInPoints(10);
   trade.SetTypeFilling(ORDER_FILLING_FOK);
   
   //--- Initialize date tracking
   currentDate = TimeCurrent();
   
   Print("✅ Top-Down EA Initialized Successfully");
   Print("   Strategy: Multi-Timeframe Market Structure Analysis");
   Print("   RSI Thresholds: ", RSI_BuyThreshold, "/", RSI_SellThreshold);
   Print("   ADX Threshold: ", ADX_Threshold);
   Print("   Min R:R Ratio: 1:", DoubleToString(MinRRRatio, 1));
   Print("   Max Trades/Day: ", MaxTradesPerDay);
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   //--- Release indicator handles
   IndicatorRelease(handleRSI_5M);
   IndicatorRelease(handleADX_5M);
   IndicatorRelease(handleATR_1M);
   
   Print("🛑 Top-Down EA Stopped");
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   //--- Check for new day (reset daily stats)
   ResetDailyStatsIfNewDay();
   
   //--- Monitor open positions first
   if(PositionsTotal() > 0)
   {
      MonitorOpenPositions();
      return; // Don't look for new trades while position is open
   }
   
   //--- Check if new bar formed on M5 (avoid multiple signals per bar)
   static datetime lastBar = 0;
   datetime currentBar = iTime(_Symbol, PERIOD_M5, 0);
   
   if(currentBar == lastBar)
      return;
      
   lastBar = currentBar;
   
   //--- Check if we can trade
   if(!CanTrade())
      return;
   
   //--- Analyze market using Top-Down approach
   string signal = TopDownAnalysis();
   
   if(signal == "BUY")
   {
      ExecuteTrade(ORDER_TYPE_BUY);
   }
   else if(signal == "SELL")
   {
      ExecuteTrade(ORDER_TYPE_SELL);
   }
}

//+------------------------------------------------------------------+
//| Reset daily statistics if new day                                |
//+------------------------------------------------------------------+
void ResetDailyStatsIfNewDay()
{
   datetime today = TimeCurrent();
   MqlDateTime todayStruct, currentStruct;
   
   TimeToStruct(today, todayStruct);
   TimeToStruct(currentDate, currentStruct);
   
   if(todayStruct.day != currentStruct.day)
   {
      Print("📅 New Trading Day - Resetting Stats");
      Print("   Yesterday: ", tradesCount, " trades, P&L: $", DoubleToString(dailyPnL, 2));
      
      currentDate = today;
      tradesCount = 0;
      dailyPnL = 0.0;
      consecutiveLosses = 0;
      lastTradeTime = 0;
   }
}

//+------------------------------------------------------------------+
//| Check if trading is allowed                                      |
//+------------------------------------------------------------------+
bool CanTrade()
{
   //--- Check if position already open
   if(PositionsTotal() > 0)
      return false;
   
   //--- Check circuit breaker (3 consecutive losses)
   if(consecutiveLosses >= 3)
   {
      Print("🛑 Circuit Breaker: ", consecutiveLosses, " consecutive losses");
      return false;
   }
   
   //--- Check daily trade limit
   if(tradesCount >= MaxTradesPerDay)
   {
      Print("⚠️ Daily limit reached: ", tradesCount, "/", MaxTradesPerDay);
      return false;
   }
   
   //--- Check cooldown period
   if(lastTradeTime > 0)
   {
      int elapsed = (int)(TimeCurrent() - lastTradeTime);
      if(elapsed < CooldownSeconds)
      {
         return false;
      }
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| Top-Down Market Analysis                                         |
//+------------------------------------------------------------------+
string TopDownAnalysis()
{
   //--- Phase 1: Directional Bias (Weekly + Daily)
   string weeklyTrend = DetermineTrend(PERIOD_W1);
   string dailyTrend = DetermineTrend(PERIOD_D1);
   
   string bias = "";
   
   if(weeklyTrend == "BULLISH" && dailyTrend == "BULLISH")
   {
      bias = "BULLISH";
   }
   else if(weeklyTrend == "BEARISH" && dailyTrend == "BEARISH")
   {
      bias = "BEARISH";
   }
   else
   {
      // Trend conflict - no trade
      return "NONE";
   }
   
   //--- Get indicator values
   double rsiArray[], adxArray[];
   ArraySetAsSeries(rsiArray, true);
   ArraySetAsSeries(adxArray, true);
   
   CopyBuffer(handleRSI_5M, 0, 0, 3, rsiArray);
   CopyBuffer(handleADX_5M, 0, 0, 3, adxArray);
   
   double rsi = rsiArray[0];
   double adx = adxArray[0];
   
   //--- ADX Filter (Trend Strength)
   if(adx < ADX_Threshold)
   {
      Print("⚠️ Trend Weak: ADX ", DoubleToString(adx, 1), " < ", ADX_Threshold);
      return "NONE";
   }
   
   //--- RSI Momentum Check
   if(bias == "BULLISH")
   {
      // Check RSI for UP signal
      if(rsi < RSI_BuyThreshold)
      {
         Print("⚠️ RSI too weak for BUY: ", DoubleToString(rsi, 1));
         return "NONE";
      }
      if(rsi > RSI_MaxThreshold)
      {
         Print("⚠️ RSI Overbought: ", DoubleToString(rsi, 1));
         return "NONE";
      }
      
      Print("✅ BULLISH Confluence: W1+D1 | RSI:", DoubleToString(rsi, 1), " ADX:", DoubleToString(adx, 1));
      return "BUY";
   }
   else if(bias == "BEARISH")
   {
      // Check RSI for DOWN signal
      if(rsi > RSI_SellThreshold)
      {
         Print("⚠️ RSI too weak for SELL: ", DoubleToString(rsi, 1));
         return "NONE";
      }
      if(rsi < RSI_MinThreshold)
      {
         Print("⚠️ RSI Oversold: ", DoubleToString(rsi, 1));
         return "NONE";
      }
      
      Print("✅ BEARISH Confluence: W1+D1 | RSI:", DoubleToString(rsi, 1), " ADX:", DoubleToString(adx, 1));
      return "SELL";
   }
   
   return "NONE";
}

//+------------------------------------------------------------------+
//| Determine Trend Using Market Structure                          |
//+------------------------------------------------------------------+
string DetermineTrend(ENUM_TIMEFRAMES timeframe)
{
   double highs[], lows[];
   ArraySetAsSeries(highs, true);
   ArraySetAsSeries(lows, true);
   
   int count = SwingLookback + 10;
   CopyHigh(_Symbol, timeframe, 0, count, highs);
   CopyLow(_Symbol, timeframe, 0, count, lows);
   
   //--- Find swing points
   double swingHighs[], swingLows[];
   ArrayResize(swingHighs, 0);
   ArrayResize(swingLows, 0);
   
   for(int i = MinSwingWindow; i < count - MinSwingWindow; i++)
   {
      bool isSwingHigh = true;
      bool isSwingLow = true;
      
      for(int j = 1; j <= MinSwingWindow; j++)
      {
         if(highs[i] <= highs[i-j] || highs[i] <= highs[i+j])
            isSwingHigh = false;
         if(lows[i] >= lows[i-j] || lows[i] >= lows[i+j])
            isSwingLow = false;
      }
      
      if(isSwingHigh)
      {
         int size = ArraySize(swingHighs);
         ArrayResize(swingHighs, size + 1);
         swingHighs[size] = highs[i];
      }
      
      if(isSwingLow)
      {
         int size = ArraySize(swingLows);
         ArrayResize(swingLows, size + 1);
         swingLows[size] = lows[i];
      }
   }
   
   //--- Check trend (Higher Highs + Higher Lows = Bullish)
   if(ArraySize(swingHighs) >= 2 && ArraySize(swingLows) >= 2)
   {
      double lastHigh = swingHighs[0];
      double prevHigh = swingHighs[1];
      double lastLow = swingLows[0];
      double prevLow = swingLows[1];
      
      if(lastHigh > prevHigh && lastLow > prevLow)
         return "BULLISH";
      else if(lastHigh < prevHigh && lastLow < prevLow)
         return "BEARISH";
   }
   
   return "NEUTRAL";
}

//+------------------------------------------------------------------+
//| Execute Trade with Dynamic TP/SL                                |
//+------------------------------------------------------------------+
void ExecuteTrade(ENUM_ORDER_TYPE orderType)
{
   double price = (orderType == ORDER_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) 
                                                  : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   
   //--- Find structure levels for TP/SL
   double tp = 0, sl = 0;
   
   if(orderType == ORDER_TYPE_BUY)
   {
      tp = FindNearestResistance(price);
      sl = FindNearestSupport(price);
   }
   else
   {
      tp = FindNearestSupport(price);
      sl = FindNearestResistance(price);
   }
   
   //--- Validate TP/SL distance
   double tpDistance = MathAbs(tp - price) / price * 100.0;
   double slDistance = MathAbs(price - sl) / price * 100.0;
   
   if(tpDistance < MinTPDistancePercent)
   {
      Print("⚠️ TP too close: ", DoubleToString(tpDistance, 3), "%");
      return;
   }
   
   if(slDistance > MaxSLDistancePercent)
   {
      Print("⚠️ SL too wide: ", DoubleToString(slDistance, 3), "% - Clamping");
      // Clamp SL to max distance
      if(orderType == ORDER_TYPE_BUY)
         sl = price * (1.0 - MaxSLDistancePercent / 100.0);
      else
         sl = price * (1.0 + MaxSLDistancePercent / 100.0);
      
      slDistance = MaxSLDistancePercent;
   }
   
   //--- Calculate R:R Ratio
   double rrRatio = tpDistance / slDistance;
   
   if(rrRatio < MinRRRatio)
   {
      Print("⚠️ R:R too low: 1:", DoubleToString(rrRatio, 2), " < 1:", DoubleToString(MinRRRatio, 1));
      return;
   }
   
   //--- Normalize prices
   tp = NormalizeDouble(tp, _Digits);
   sl = NormalizeDouble(sl, _Digits);
   
   //--- Calculate lot size based on risk
   double lotSize = CalculateLotSize(slDistance);
   
   //--- Execute trade
   bool result = trade.PositionOpen(_Symbol, orderType, lotSize, price, sl, tp, 
                                    "Top-Down R:R=" + DoubleToString(rrRatio, 2));
   
   if(result)
   {
      activeTicket = trade.ResultOrder();
      trailingStopPrice = 0.0;
      activeTrailingTier = 0;
      
      lastTradeTime = TimeCurrent();
      tradesCount++;
      
      Print("✅ Trade Opened: ", EnumToString(orderType));
      Print("   Price: ", DoubleToString(price, _Digits));
      Print("   TP: ", DoubleToString(tp, _Digits), " (", DoubleToString(tpDistance, 2), "%)");
      Print("   SL: ", DoubleToString(sl, _Digits), " (", DoubleToString(slDistance, 2), "%)");
      Print("   R:R: 1:", DoubleToString(rrRatio, 2));
      Print("   Lot: ", DoubleToString(lotSize, 2));
   }
   else
   {
      Print("❌ Trade Failed: ", trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Find Nearest Resistance Level                                    |
//+------------------------------------------------------------------+
double FindNearestResistance(double currentPrice)
{
   double highs[];
   ArraySetAsSeries(highs, true);
   
   // Use 4H timeframe for structure levels
   CopyHigh(_Symbol, PERIOD_H4, 0, 100, highs);
   
   // Find first swing high above current price
   for(int i = MinSwingWindow; i < 95; i++)
   {
      bool isSwing = true;
      for(int j = 1; j <= MinSwingWindow; j++)
      {
         if(highs[i] <= highs[i-j] || highs[i] <= highs[i+j])
            isSwing = false;
      }
      
      if(isSwing && highs[i] > currentPrice)
      {
         return highs[i];
      }
   }
   
   // Fallback: 0.5% above current price
   return currentPrice * 1.005;
}

//+------------------------------------------------------------------+
//| Find Nearest Support Level                                       |
//+------------------------------------------------------------------+
double FindNearestSupport(double currentPrice)
{
   double lows[];
   ArraySetAsSeries(lows, true);
   
   // Use 4H timeframe for structure levels
   CopyLow(_Symbol, PERIOD_H4, 0, 100, lows);
   
   // Find first swing low below current price
   for(int i = MinSwingWindow; i < 95; i++)
   {
      bool isSwing = true;
      for(int j = 1; j <= MinSwingWindow; j++)
      {
         if(lows[i] >= lows[i-j] || lows[i] >= lows[i+j])
            isSwing = false;
      }
      
      if(isSwing && lows[i] < currentPrice)
      {
         return lows[i];
      }
   }
   
   // Fallback: 0.5% below current price
   return currentPrice * 0.995;
}

//+------------------------------------------------------------------+
//| Calculate Lot Size Based on Risk                                |
//+------------------------------------------------------------------+
double CalculateLotSize(double slDistancePercent)
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskAmount = balance * (MaxRiskPercent / 100.0);
   
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   
   // Calculate SL in points
   double currentPrice = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double slDistance = currentPrice * (slDistancePercent / 100.0);
   double slPoints = slDistance / tickSize;
   
   // Calculate lot size
   double lotSize = riskAmount / (slPoints * tickValue);
   
   // Normalize to broker limits
   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   
   lotSize = MathMax(minLot, MathMin(lotSize, maxLot));
   lotSize = MathFloor(lotSize / lotStep) * lotStep;
   
   // Fallback to fixed lot if calculation fails
   if(lotSize < minLot)
      lotSize = FixedLotSize;
   
   return NormalizeDouble(lotSize, 2);
}

//+------------------------------------------------------------------+
//| Monitor Open Positions                                           |
//+------------------------------------------------------------------+
void MonitorOpenPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionGetString(POSITION_SYMBOL) == _Symbol)
      {
         double currentProfit = PositionGetDouble(POSITION_PROFIT);
         double currentPrice = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
                               ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                               : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         
         //--- Check early exit
         if(EnableEarlyExit)
         {
            if(CheckEarlyExit(ticket, currentPrice, currentProfit))
               continue;
         }
         
         //--- Update trailing stop
         if(EnableMultiTierTrailing)
         {
            UpdateTrailingStop(ticket, currentPrice, currentProfit);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Check Early Exit Condition                                       |
//+------------------------------------------------------------------+
bool CheckEarlyExit(ulong ticket, double currentPrice, double currentProfit)
{
   datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
   int elapsed = (int)(TimeCurrent() - openTime);
   
   if(elapsed > EarlyExitTimeSeconds)
      return false; // Past early exit window
   
   if(currentProfit >= 0)
      return false; // Not in loss
   
   // Calculate loss as % of balance
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double lossPercent = MathAbs(currentProfit) / balance * 100.0;
   
   if(lossPercent >= EarlyExitLossPercent)
   {
      Print("⚠️ Early Exit: Loss ", DoubleToString(lossPercent, 1), "% at ", elapsed, "s");
      trade.PositionClose(ticket);
      
      dailyPnL += currentProfit;
      consecutiveLosses++;
      
      return true;
   }
   
   return false;
}

//+------------------------------------------------------------------+
//| Update Multi-Tier Trailing Stop                                 |
//+------------------------------------------------------------------+
void UpdateTrailingStop(ulong ticket, double currentPrice, double currentProfit)
{
   double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
   double profitPercent = MathAbs(currentPrice - openPrice) / openPrice * 100.0;
   
   if(currentProfit <= 0)
      return; // Only trail in profit
   
   //--- Determine active tier (4-Tier System)
   int newTier = 0;
   double trailPercent = 0;
   
   if(profitPercent >= TrailTrigger4)
   {
      newTier = 4;
      trailPercent = TrailStop4;
   }
   else if(profitPercent >= TrailTrigger3)
   {
      newTier = 3;
      trailPercent = TrailStop3;
   }
   else if(profitPercent >= TrailTrigger2)
   {
      newTier = 2;
      trailPercent = TrailStop2;
   }
   else if(profitPercent >= TrailTrigger1)
   {
      newTier = 1;
      trailPercent = TrailStop1;
   }
   else
   {
      return; // Below minimum threshold
   }
   
   //--- Calculate new stop price
   ENUM_POSITION_TYPE posType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
   double priceDistance = currentPrice * (trailPercent / 100.0);
   double newStopPrice = 0;
   
   if(posType == POSITION_TYPE_BUY)
   {
      newStopPrice = currentPrice - priceDistance;
   }
   else
   {
      newStopPrice = currentPrice + priceDistance;
   }
   
   newStopPrice = NormalizeDouble(newStopPrice, _Digits);
   
   //--- Update if tighter than current
   double currentSL = PositionGetDouble(POSITION_SL);
   bool shouldUpdate = false;
   
   if(trailingStopPrice == 0)
   {
      shouldUpdate = true;
      Print("🛡️ Trailing Activated (Tier ", newTier, "): Stop @ ", DoubleToString(newStopPrice, _Digits));
   }
   else if(posType == POSITION_TYPE_BUY && newStopPrice > trailingStopPrice)
   {
      shouldUpdate = true;
   }
   else if(posType == POSITION_TYPE_SELL && newStopPrice < trailingStopPrice)
   {
      shouldUpdate = true;
   }
   
   if(shouldUpdate)
   {
      trailingStopPrice = newStopPrice;
      activeTrailingTier = newTier;
      
      double tp = PositionGetDouble(POSITION_TP);
      if(trade.PositionModify(ticket, newStopPrice, tp))
      {
         Print("🛡️ Trailing Updated (Tier ", newTier, "): Stop moved to ", DoubleToString(newStopPrice, _Digits));
      }
   }
}

//+------------------------------------------------------------------+
//| OnTrade Event Handler                                            |
//+------------------------------------------------------------------+
void OnTrade()
{
   // Check if position was closed
   if(PositionsTotal() == 0 && activeTicket > 0)
   {
      // Position was closed - get history
      HistorySelect(TimeCurrent() - 86400, TimeCurrent()); // Last 24h
      
      for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
      {
         ulong dealTicket = HistoryDealGetTicket(i);
         if(HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID) == activeTicket)
         {
            double profit = HistoryDealGetDouble(dealTicket, DEAL_PROFIT);
            
            dailyPnL += profit;
            
            if(profit > 0)
            {
               consecutiveLosses = 0;
               Print("✅ Trade Closed: Profit $", DoubleToString(profit, 2));
            }
            else
            {
               consecutiveLosses++;
               Print("❌ Trade Closed: Loss $", DoubleToString(profit, 2));
            }
            
            Print("   Daily P&L: $", DoubleToString(dailyPnL, 2));
            Print("   Trades Today: ", tradesCount, "/", MaxTradesPerDay);
            
            break;
         }
      }
      
      // Reset active trade tracking
      activeTicket = 0;
      trailingStopPrice = 0;
      activeTrailingTier = 0;
   }
}
//+------------------------------------------------------------------+

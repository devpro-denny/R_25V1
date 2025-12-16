# 🤖 Deriv R_25 Trading Bot

**Automated trading bot for Deriv's R_25 synthetic index with FastAPI REST API, real-time WebSocket monitoring, and Telegram notifications.**

---

## 📋 Table of Contents

- [Features](#-features)
- [Architecture](#-architecture)
- [Prerequisites](#-prerequisites)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Usage](#-usage)
- [API Documentation](#-api-documentation)
- [WebSocket API](#-websocket-api)
- [Authentication](#-authentication)
- [Deployment](#-deployment)
- [Trading Strategy](#-trading-strategy)
- [Risk Management](#-risk-management)
- [Monitoring](#-monitoring)
- [Troubleshooting](#-troubleshooting)
- [Contributing](#-contributing)
- [License](#-license)

---

## ✨ Features

### Core Trading
- ✅ **Automated Trading** - Executes trades based on technical analysis
- ✅ **Multi-Timeframe Analysis** - Analyzes 1m and 5m charts simultaneously
- ✅ **Technical Indicators** - RSI, ADX, ATR, MACD, Bollinger Bands
- ✅ **Risk Management** - Stop loss, take profit, trailing stops
- ✅ **Anti-Reversal Protection** - Prevents trading in volatile conditions

### API & Integration
- ✅ **REST API** - Full control via HTTP endpoints
- ✅ **WebSocket API** - Real-time updates and monitoring
- ✅ **JWT Authentication** - Secure access control
- ✅ **Telegram Notifications** - Real-time trade alerts
- ✅ **Interactive API Docs** - Swagger UI and ReDoc

### Monitoring & Analytics
- ✅ **Live Performance Metrics** - Win rate, P&L, trade statistics
- ✅ **Trade History** - Complete trade logging and analysis
- ✅ **Signal Tracking** - View all trading signals generated
- ✅ **Real-time Logs** - API access to bot logs

### Deployment Ready
- ✅ **Production Ready** - Configured for Render deployment
- ✅ **Environment-based Config** - Easy configuration via .env
- ✅ **Health Checks** - Monitoring endpoints for uptime services
- ✅ **Auto-start** - Bot starts automatically on deployment

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI Application                      │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐           │
│  │   REST API │  │ WebSocket  │  │    Auth    │           │
│  │  Endpoints │  │   Server   │  │   (JWT)    │           │
│  └────────────┘  └────────────┘  └────────────┘           │
│         │                │                │                  │
│         └────────────────┴────────────────┘                 │
│                          │                                   │
│  ┌───────────────────────▼────────────────────────┐        │
│  │              Bot Runner (Core)                  │        │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐    │        │
│  │  │ Strategy │  │   Risk   │  │  Trade   │    │        │
│  │  │ Engine   │  │ Manager  │  │  Engine  │    │        │
│  │  └──────────┘  └──────────┘  └──────────┘    │        │
│  └─────────────────────────────────────────────────┘        │
│                          │                                   │
│  ┌───────────────────────▼────────────────────────┐        │
│  │          External Integrations                  │        │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐    │        │
│  │  │  Deriv   │  │ Telegram │  │   Data   │    │        │
│  │  │   API    │  │  Notifier│  │  Fetcher │    │        │
│  │  └──────────┘  └──────────┘  └──────────┘    │        │
│  └─────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

### Key Components

| Component | Description |
|-----------|-------------|
| **FastAPI** | REST API and WebSocket server |
| **Bot Runner** | Manages bot lifecycle (start/stop/restart) |
| **Strategy Engine** | Technical analysis and signal generation |
| **Risk Manager** | Position sizing, stop loss, cooldowns |
| **Trade Engine** | Executes and monitors trades via Deriv API |
| **Data Fetcher** | Retrieves market data from Deriv |
| **Telegram Notifier** | Sends trade alerts to Telegram |
| **Auth System** | JWT-based authentication |

---

## 📦 Prerequisites

### Required
- **Python 3.13+** (or 3.10+)
- **Deriv Account** with API token
- **Telegram Bot** (optional but recommended)

### Optional
- **PostgreSQL** (for persistent storage)
- **Redis** (for caching, future enhancement)

---

## 🚀 Installation

### 1. Clone Repository

```bash
git clone https://github.com/yourusername/deriv-r25-trading-bot.git
cd deriv-r25-trading-bot
```

### 2. Create Virtual Environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/Mac
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Create `.env` file in project root:

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# Deriv API
DERIV_API_TOKEN=your_deriv_api_token_here
DERIV_APP_ID=your_deriv_app_id_here

# Telegram (optional)
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here

# Security
JWT_SECRET_KEY=generate_with_openssl_rand_hex_32
ADMIN_PASSWORD=your_secure_admin_password

# Application
ENVIRONMENT=development
BOT_AUTO_START=false
PORT=10000
```

### 5. Generate Secure JWT Secret

```bash
# Linux/Mac
openssl rand -hex 32

# Windows (PowerShell)
-join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
```

---

## ⚙️ Configuration

### Trading Parameters (`config.py`)

```python
# Symbol and Multiplier
SYMBOL = "R_25"
MULTIPLIER = 160

# Trade Sizing
FIXED_STAKE = 10.0         # Base stake amount
FIXED_TP = 2.0           # Take profit target
MAX_LOSS_PER_TRADE = 3.0  # Maximum loss per trade

# Risk Management
MAX_TRADES_PER_DAY = 50
MAX_DAILY_LOSS = 30.0

# Strategy Parameters
MINIMUM_SIGNAL_SCORE = 6   # Minimum score to trade (1-10)
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
ADX_THRESHOLD = 25

# Volatility Filters (ATR)
ATR_MIN_1M = 0.05
ATR_MAX_1M = 1.5
ATR_MIN_5M = 0.1
ATR_MAX_5M = 2.5
```

### API Settings (`app/core/settings.py`)

Configure via environment variables or directly in code:

```python
# Server
PORT = 10000
HOST = "0.0.0.0"

# Authentication
ENABLE_AUTHENTICATION = True
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours

# CORS
CORS_ORIGINS = ["*"]  # Restrict in production

# Bot
BOT_AUTO_START = False  # Set to True for production
```

---

## 💻 Usage

### Start the Server

```bash
# Development (with auto-reload)
python -m uvicorn app.main:app --host 0.0.0.0 --port 10000 --reload

# Production
python -m uvicorn app.main:app --host 0.0.0.0 --port 10000
```

Server will be available at:
- **API**: http://localhost:10000
- **Docs**: http://localhost:10000/docs
- **ReDoc**: http://localhost:10000/redoc
- **Health**: http://localhost:10000/health

### Using the API

#### 1. **Register/Login**

```bash
# Register new user
curl -X POST http://localhost:10000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "trader1",
    "password": "secure_password",
    "email": "trader@example.com"
  }'

# Login (get token)
curl -X POST http://localhost:10000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "password": "admin123"
  }'

# Response includes token:
{
  "user": {...},
  "token": {
    "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
    "token_type": "bearer"
  }
}
```

#### 2. **Control the Bot**

```bash
# Save token
TOKEN="your_token_here"

# Start bot
curl -X POST http://localhost:10000/api/v1/bot/start \
  -H "Authorization: Bearer $TOKEN"

# Get status
curl http://localhost:10000/api/v1/bot/status \
  -H "Authorization: Bearer $TOKEN"

# Stop bot
curl -X POST http://localhost:10000/api/v1/bot/stop \
  -H "Authorization: Bearer $TOKEN"

# Restart bot
curl -X POST http://localhost:10000/api/v1/bot/restart \
  -H "Authorization: Bearer $TOKEN"
```

#### 3. **Monitor Trading**

```bash
# Get active trades
curl http://localhost:10000/api/v1/trades/active \
  -H "Authorization: Bearer $TOKEN"

# Get trade history
curl http://localhost:10000/api/v1/trades/history?limit=50 \
  -H "Authorization: Bearer $TOKEN"

# Get statistics
curl http://localhost:10000/api/v1/trades/stats \
  -H "Authorization: Bearer $TOKEN"

# Get recent signals
curl http://localhost:10000/api/v1/monitor/signals?limit=20 \
  -H "Authorization: Bearer $TOKEN"

# Get performance metrics
curl http://localhost:10000/api/v1/monitor/performance \
  -H "Authorization: Bearer $TOKEN"
```

### Using WebSocket (Real-time Updates)

```javascript
// Connect to WebSocket
const ws = new WebSocket('ws://localhost:10000/ws/live');

ws.onopen = () => {
  console.log('✅ Connected to bot');
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('📨 Update:', data);
  
  // Handle different event types
  switch(data.type) {
    case 'bot_status':
      console.log('Bot status:', data.status);
      break;
    case 'signal':
      console.log('🚨 Trading signal:', data.signal);
      break;
    case 'trade_opened':
      console.log('📈 Trade opened:', data.trade);
      break;
    case 'trade_closed':
      console.log('💰 Trade closed. P&L:', data.pnl);
      break;
  }
};
```

### Using Swagger UI

1. Open http://localhost:10000/docs
2. Click **"Authorize"** button (🔒 icon)
3. Login to get token
4. Enter token in format: `Bearer YOUR_TOKEN`
5. Try API endpoints interactively

---

## 📚 API Documentation

### Authentication Endpoints

| Method | Endpoint | Description | Auth Required |
|--------|----------|-------------|---------------|
| POST | `/api/v1/auth/register` | Register new user | ❌ |
| POST | `/api/v1/auth/login` | Login and get JWT token | ❌ |
| GET | `/api/v1/auth/me` | Get current user info | ✅ |
| POST | `/api/v1/auth/logout` | Logout (client-side) | ✅ |

### Bot Control Endpoints

| Method | Endpoint | Description | Auth Required |
|--------|----------|-------------|---------------|
| POST | `/api/v1/bot/start` | Start trading bot | ✅ |
| POST | `/api/v1/bot/stop` | Stop trading bot | ✅ |
| POST | `/api/v1/bot/restart` | Restart trading bot | ✅ |
| GET | `/api/v1/bot/status` | Get bot status | ✅ |

### Trade Endpoints

| Method | Endpoint | Description | Auth Required |
|--------|----------|-------------|---------------|
| GET | `/api/v1/trades/active` | Get active trades | ✅ |
| GET | `/api/v1/trades/history` | Get trade history | ✅ |
| GET | `/api/v1/trades/stats` | Get trading statistics | ✅ |

### Monitoring Endpoints

| Method | Endpoint | Description | Auth Required |
|--------|----------|-------------|---------------|
| GET | `/api/v1/monitor/signals` | Get recent signals | ✅ |
| GET | `/api/v1/monitor/performance` | Get performance metrics | ✅ |
| GET | `/api/v1/monitor/logs` | Get recent logs | ✅ |
| GET | `/api/v1/monitor/debug` | Get debug info | ✅ |

### Health & Info

| Method | Endpoint | Description | Auth Required |
|--------|----------|-------------|---------------|
| GET | `/health` | Health check | ❌ |
| GET | `/` | API information | ❌ |
| GET | `/docs` | Swagger UI | ❌ |
| GET | `/redoc` | ReDoc UI | ❌ |

---

## 🔌 WebSocket API

### Connection

```
ws://localhost:10000/ws/live
wss://your-domain.com/ws/live  (Production with SSL)
```

### Event Types

| Event Type | Description | Example Payload |
|------------|-------------|-----------------|
| `connected` | Initial connection | `{"type": "connected", "message": "WebSocket connection established"}` |
| `bot_status` | Bot status changed | `{"type": "bot_status", "status": "running", "balance": 1000.0}` |
| `signal` | Trading signal detected | `{"type": "signal", "signal": "BUY", "score": 8, "confidence": 0.85}` |
| `trade_opened` | New trade opened | `{"type": "trade_opened", "trade": {...}}` |
| `trade_closed` | Trade closed | `{"type": "trade_closed", "pnl": 1.5, "status": "won"}` |
| `statistics` | Statistics update | `{"type": "statistics", "stats": {...}}` |
| `error` | Error occurred | `{"type": "error", "message": "Error details"}` |

---

## 🔐 Authentication

### JWT Token-Based Auth

The API uses JSON Web Tokens (JWT) for authentication:

1. **Login** to receive a token
2. **Store** the token securely (localStorage, cookies)
3. **Include** token in requests:
   - Header: `Authorization: Bearer YOUR_TOKEN`
4. **Token expires** after 24 hours (default)

### Default Credentials

```
Username: admin
Password: admin123
```

**⚠️ Change immediately in production!**

### Creating Additional Users

```bash
curl -X POST http://localhost:10000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "newuser",
    "password": "secure_password",
    "email": "user@example.com"
  }'
```

### Password Requirements

- Minimum 8 characters (configurable)
- Optional: uppercase, lowercase, digits, special characters
- Configured in `app/core/settings.py`

---

## 🌐 Deployment

### Deploy to Render

#### 1. **Prepare Repository**

```bash
git add .
git commit -m "Ready for deployment"
git push origin main
```

#### 2. **Create Render Service**

1. Go to https://render.com
2. Click **"New +"** → **"Web Service"**
3. Connect GitHub repository
4. Configure:
   - **Name**: `deriv-trading-bot`
   - **Environment**: `Python 3`
   - **Build Command**: `pip install --upgrade pip && pip install -r requirements.txt`
   - **Start Command**: `python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT`

#### 3. **Add Environment Variables**

```
ENVIRONMENT=production
BOT_AUTO_START=true
DERIV_API_TOKEN=your_token
DERIV_APP_ID=your_app_id
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
JWT_SECRET_KEY=<strong-random-key>
ADMIN_PASSWORD=<secure-password>
CORS_ORIGINS=*
```

#### 5. **Deploy**

Click **"Create Web Service"** - Deployment takes 5-10 minutes.

### Deploy to Other Platforms

<details>
<summary><b>Railway</b></summary>

```bash
# Install Railway CLI
npm i -g @railway/cli

# Login
railway login

# Initialize project
railway init

# Deploy
railway up
```

</details>

<details>
<summary><b>Heroku</b></summary>

```bash
# Create Procfile
echo "web: uvicorn app.main:app --host 0.0.0.0 --port $PORT" > Procfile

# Deploy
heroku create deriv-trading-bot
git push heroku main
heroku config:set DERIV_API_TOKEN=your_token
```

</details>

<details>
<summary><b>DigitalOcean App Platform</b></summary>

1. Connect GitHub repository
2. Configure app with `requirements.txt`
3. Set run command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Add environment variables

</details>

---

## 📊 Trading Strategy

### Signal Generation

The bot uses multiple technical indicators to generate trading signals:

#### 1. **RSI (Relative Strength Index)**
- Identifies overbought/oversold conditions
- Oversold (< 30): Potential BUY signal
- Overbought (> 70): Potential SELL signal

#### 2. **ADX (Average Directional Index)**
- Measures trend strength
- ADX > 25: Strong trend (trade-worthy)
- ADX < 25: Weak trend (avoid)

#### 3. **ATR (Average True Range)**
- Measures volatility
- Must be within acceptable range to trade
- Too high: Market too volatile (dangerous)
- Too low: Market too quiet (no opportunity)

#### 4. **MACD (Moving Average Convergence Divergence)**
- Confirms trend direction
- MACD crossovers validate signals

#### 5. **Bollinger Bands**
- Identifies price extremes
- Price near bands indicates potential reversal

#### 6. **Anti-Reversal Filter**
- Checks recent candle patterns
- Prevents trading during rapid reversals
- Requires 70% candles in same direction

### Signal Scoring System

Each signal receives a score from 1-10:
- **1-3**: Weak signal (no trade)
- **4-5**: Moderate signal (no trade)
- **6-7**: Good signal (trade cautiously)
- **8-9**: Strong signal (trade confidently)
- **10**: Perfect signal (rare, high confidence)

Default minimum score: **6**

---

## 🛡️ Risk Management

### Position Sizing
- **Fixed stake**: $1.00 per trade (configurable)
- **Multiplier**: 50x (Deriv synthetic index)
- **Risk/Reward**: 1:1.5 (risk $2, target $1.50 profit)

### Stop Loss & Take Profit
- **Take Profit**: +$1.50 (configurable)
- **Stop Loss**: -$2.00 (configurable)
- **Trailing Stop**: Activates at 75% of target
- **Early Exit**: Available at 80% of target

### Daily Limits
- **Max trades per day**: 10
- **Max daily loss**: $20
- **Cooldowns**:
  - 5 minutes after stop loss
  - 10 minutes after 3 consecutive losses

### Market Conditions
Bot automatically avoids trading when:
- ATR too high (excessive volatility)
- ATR too low (insufficient movement)
- Trend too weak (ADX < 25)
- Recent market reversal detected
- Daily limits reached

---

## 📈 Monitoring

### Telegram Notifications

Configure Telegram for real-time alerts:

1. **Create Bot**: Message @BotFather on Telegram
2. **Get Token**: Save the bot token
3. **Get Chat ID**: 
   - Message your bot
   - Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   - Find your `chat.id`
4. **Configure**: Add to `.env` file

**Notifications include:**
- 🤖 Bot started/stopped
- 🟢🔴 Trading signals detected
- 📈 Trades opened
- 💰 Trade results (win/loss)
- ⚠️ Errors and warnings

### Performance Metrics

Access via API or WebSocket:

```json
{
  "total_trades": 50,
  "winning_trades": 32,
  "losing_trades": 18,
  "win_rate": 64.0,
  "total_pnl": 15.50,
  "daily_pnl": 3.20,
  "trades_today": 5
}
```

### Logs

**View logs:**
```bash
# Via API
curl http://localhost:10000/api/v1/monitor/logs?lines=50 \
  -H "Authorization: Bearer $TOKEN"

# Local file
tail -f trading_bot.log

# Render dashboard
# View real-time logs in Render's log viewer
```

---

## 🔧 Troubleshooting

### Common Issues

<details>
<summary><b>Bot won't start</b></summary>

**Symptoms**: `"Bot failed to start"` error

**Solutions**:
1. Check environment variables are set
2. Verify Deriv API token is valid
3. Check logs for specific error
4. Ensure sufficient balance in account

```bash
# Test Deriv connection
python -c "import config; print('Token:', config.DERIV_API_TOKEN[:20])"
```

</details>

<details>
<summary><b>No signals generated</b></summary>

**Symptoms**: Empty signals array, no trades

**Causes**:
- Market volatility too high/low (ATR rejection)
- Signal score below threshold
- Trend too weak (ADX < 25)
- Anti-reversal filter active

**Solutions**:
1. Check debug endpoint for details
2. View logs for rejection reasons
3. Consider adjusting ATR limits
4. Wait for better market conditions

```bash
curl http://localhost:10000/api/v1/monitor/debug \
  -H "Authorization: Bearer $TOKEN"
```

</details>

<details>
<summary><b>Telegram notifications not working</b></summary>

**Solutions**:
1. Verify bot token is correct
2. Verify chat ID is correct
3. Check bot is started with `/start` command
4. Test token:

```bash
curl "https://api.telegram.org/bot<YOUR_TOKEN>/getMe"
```

</details>

<details>
<summary><b>WebSocket disconnects</b></summary>

**Causes**:
- Free tier on Render (connection limits)
- Network issues
- Bot restarted

**Solutions**:
1. Implement auto-reconnect in client
2. Upgrade to paid plan
3. Use heartbeat/ping mechanism

</details>

<details>
<summary><b>Authentication errors</b></summary>

**Symptoms**: 401 Unauthorized

**Solutions**:
1. Login again to get fresh token
2. Check token hasn't expired (24h default)
3. Verify token format: `Bearer <token>`
4. Check JWT secret matches in environment

</details>

---

## 🧪 Testing

### Run Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Run all tests
pytest

# Run specific test file
pytest tests/test_api.py

# Run with coverage
pytest --cov=app tests/
```

### Manual Testing Checklist

- [ ] Bot starts successfully
- [ ] Bot stops gracefully
- [ ] Signals are generated
- [ ] Trades execute correctly
- [ ] Telegram notifications work
- [ ] WebSocket updates in real-time
- [ ] Authentication works
- [ ] API endpoints respond correctly
- [ ] Logs are written properly
- [ ] Health check passes

---

## 📁 Project Structure

```
deriv-r50-trading-bot/
├── app/
│   ├── api/                    # API endpoints
│   │   ├── auth.py            # Authentication routes
│   │   ├── bot.py             # Bot control routes
│   │   ├── trades.py          # Trade routes
│   │   ├── monitor.py         # Monitoring routes
│   │   └── config.py          # Config routes
│   ├── bot/                    # Bot core
│   │   ├── runner.py          # Bot lifecycle manager
│   │   ├── state.py           # Bot state management
│   │   ├── events.py          # Event broadcasting
│   │   └── telegram_bridge.py # Telegram integration
│   ├── core/                   # Core utilities
│   │   ├── auth.py            # Authentication logic
│   │   ├── settings.py        # Application settings
│   │   ├── logging.py         # Logging configuration
│   │   └── serializers.py     # Data serialization
│   ├── schemas/                # Pydantic models
│   │   ├── auth.py            # Auth schemas
│   │   ├── bot.py             # Bot schemas
│   │   ├── trades.py          # Trade schemas
│   │   └── common.py          # Common schemas
│   ├── ws/                     # WebSocket
│   │   └── live.py            # Live updates WebSocket
│   └── main.py                 # FastAPI application
│
├── config.py                   # Trading configuration
├── data_fetcher.py            # Deriv data fetching
├── strategy.py                # Trading strategy
├── trade_engine.py            # Trade execution
├── risk_manager.py            # Risk management
├── indicators.py              # Technical indicators
├── telegram_notifier.py       # Telegram notifications
├── utils.py                   # Utility functions
│
├── requirements.txt           # Python dependencies
├── .env                       # Environment variables (not in git)
├── .env.example              # Environment template
├── .gitignore                # Git ignore rules
├── render.yaml               # Render configuration
├── README.md                 # This file
└── LICENSE                   # License file
```

---

## 🤝 Contributing

Contributions are welcome! Please follow these steps:

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. **Commit** changes (`git commit -m 'Add amazing feature'`)
4. **Push** to branch (`git push origin feature/amazing-feature`)
5. **Open** a Pull Request

### Development Guidelines

- Follow PEP 8 style guide
- Add docstrings to functions
- Write tests for new features
- Update README for API changes
- Keep commits atomic and descriptive


## ⚠️ Disclaimer

**IMPORTANT**: This trading bot is for educational and research purposes only.

- ❌ **NOT financial advice**
- ❌ **NO guarantee of profits**
- ✅ **Trade at your own risk**
- ✅ **Test thoroughly before live trading**
- ✅ **Use paper/demo accounts first**
- ✅ **Never invest more than you can afford to lose**

Trading involves substantial risk of loss. Past performance does not guarantee future results.


## 🙏 Acknowledgments

- [Deriv](https://deriv.com) - Trading platform
- [FastAPI](https://fastapi.tiangolo.com/) - Web framework
- [python-telegram-bot](https://python-telegram-bot.org/) - Telegram integration
- [TA-Lib](https://github.com/mrjbq7/ta-lib) - Technical analysis library

---

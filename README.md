# Deriv Multi-Asset Trading Bot

Automated trading bot for Deriv's volatility indices using top-down market structure analysis.

## Features

- Multi-asset monitoring (R_25, R_50, R_75, 1HZ50V, 1HZ75V)
- Global risk control: 1 active trade max across all assets
- Top-down strategy: Weekly/Daily bias + structure-based entries
- Auto-recovery on restart
- Telegram notifications
- REST API with authentication
- Cloud-deployable (Render.com, Railway.app)

## Deployment

### Render
1.  Connect your GitHub repository.
2.  Render will auto-detect `render.yaml`.
3.  Add environment variables in the dashboard.

### Railway
1.  Connect your GitHub repository.
2.  Railway will auto-detect `railway.json` and `Procfile`.
3.  Add environment variables in the dashboard.

## Quick Setup

### Prerequisites
- Python 3.10+
- Deriv account with API token
- Supabase account

### Installation

```bash
git clone <repo-url>
cd deriv-r25-trading-bot

python -m venv venv
# Windows: venv\Scripts\activate
# Linux/Mac: source venv/bin/activate

pip install -r requirements.txt
```

### Configuration

Create `.env` file:

```env
DERIV_API_TOKEN=your_token
DERIV_APP_ID=1089
SUPABASE_URL=your_url
SUPABASE_SERVICE_ROLE_KEY=your_key
SUPABASE_ANON_KEY=your_key
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

Run database setup:
```bash
# Copy contents of supabase_setup.sql and run in Supabase SQL Editor
```

### Running

```bash
uvicorn app.main:app --host 0.0.0.0 --port 10000 --reload
```

Access at `http://localhost:10000` and `http://localhost:10000/docs`

### Admin Setup

```bash
python create_admin.py your@email.com
```

## License

MIT


# Digital Twin Chatbot

A React-based chatbot that acts as your "digital twin" connecting to your Google Calendar, Gmail, and Notion to provide a unified conversational interface to your digital life.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         FRONTEND                                │
│                      (React + Vite)                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ Chat UI     │  │ Auth State  │  │ Service Connection UI   │  │
│  │ (Messages)  │  │ Management  │  │ (OAuth buttons/status)  │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ HTTP (REST API)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         BACKEND                                 │
│                    (Python + FastAPI)                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ OAuth2      │  │ Chat/LLM    │  │ Service Integrations    │  │
│  │ Manager     │  │ Controller  │  │ (Google, Notion APIs)   │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
│                              │                                  │
│                    ┌─────────┴─────────┐                        │
│                    │     Database      │                        │
│                    │   (PostgreSQL)    │                        │
│                    │  - User sessions  │                        │
│                    │  - OAuth tokens   │                        │
│                    └───────────────────┘                        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    EXTERNAL SERVICES                            │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌────────────┐ │
│  │ Google   │    │ Google   │    │ Notion   │    │ OpenAI     │ │
│  │ Calendar │    │ Gmail    │    │          │    │ GPT-5-nano | │
│  └──────────┘    └──────────┘    └──────────┘    └────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

### Frontend
| Technology | Purpose |
|------------|---------|
| **React 18** | UI framework |
| **JavaScript** | Language |
| **Vite** | Build tool & dev server |

### Backend
| Technology | Purpose |
|------------|---------|
| **Python 3.11+** | Runtime |
| **FastAPI** | Async web framework |
| **PostgreSQL** | Database (users, encrypted tokens) |
| **cryptography** | Token Fernet encryption |
| **httpx** | Async HTTP client |
| **OpenAI SDK** | LLM integration |

---

## OAuth2 Flow

### How It Works

```
User Browser          Your Backend           OAuth Provider
     │                     │                      │
     │ Click Connect       │                      │
     │────────────────────>│                      │
     │                     │                      │
     │   Redirect to OAuth │                      │
     │<────────────────────│                      │
     │                     │                      │
     │   OAuth Consent Screen                     │
     │───────────────────────────────────────────>|
     │                     │                      │
     │   Auth Code Callback│                      │
     │<───────────────────────────────────────────|
     │                     │                      │
     │   Redirect w/ code  │                      │
     │────────────────────>│                      │
     │                     │ Exchange code        │
     │                     │─────────────────────>│
     │                     │      Tokens          │
     │                     │<─────────────────────│
     │                     │                      │
     │  Set session cookie │                      │
     │<────────────────────│                      │
```

### Token Storage

```
┌─────────────────────────────────────────────┐
│              users table                    │
├─────────────────────────────────────────────┤
│ id (UUID)                                   │
│ name                                        │
│ created_at                                  │
| updated_at                                  |
└─────────────────────────────────────────────┘
                    │
                    │ 1:many
                    ▼
┌─────────────────────────────────────────────┐
│         oauth_connections table             │
├─────────────────────────────────────────────┤
│ id                                          │
│ user_id (FK)                                │
│ provider (google | notion)                  │
│ access_token_encrypted                      │
│ refresh_token_encrypted                     │
│ token_expiry                                │
│ scopes                                      │
└─────────────────────────────────────────────┘
```

- **Tokens encrypted at rest** using Fernet symmetric encryption
- **Refresh tokens** used to get new access tokens when expired
- **Backend handles refresh** automatically before API calls

---

## 🚀 Installation & Setup

### Prerequisites

- Python 3.11+
- Node.js 20+
- Docker (for PostgreSQL)

### 1. Clone & Setup Backend

```bash
cd backend

# Create virtual environment then Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

Create `backend/.env`:

```env
# Database
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/digitaltwin

GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret

NOTION_CLIENT_ID=your_notion_client_id
NOTION_CLIENT_SECRET=your_notion_client_secret

OPENAI_API_KEY=your_openai_api_key

SECRET_KEY=your-secret-key-change-in-production

FRONTEND_URL=http://localhost:5173
BACKEND_URL=http://localhost:8000
```

### 3. Set Up OAuth Apps

**Google Cloud Console** (https://console.cloud.google.com):
1. Create a new project
2. Enable Google Calendar API & Gmail API
3. Configure OAuth consent screen
4. Create OAuth 2.0 credentials (Web application)
5. Add redirect URI: `http://localhost:8000/auth/google/callback`

**Notion Developers** (https://www.notion.so/my-integrations):
1. Create a new public integration
2. Add redirect URI: `http://localhost:8000/auth/notion/callback`

### 4. Start Services

```bash
# Terminal 1: Start PostgreSQL
docker compose up -d

# Terminal 2: Start backend
cd backend
source venv/bin/activate
uvicorn app.main:app --reload --port 8000

# Terminal 3: Start frontend
cd frontend
npm install
npm run dev
```

### 5. Access the App

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- API Docs: http://localhost:8000/docs
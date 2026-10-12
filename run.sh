#!/bin/bash
set -e

# Start backend
cd backend
source .venv/bin/activate
uvicorn api.server:app --reload &
BACKEND_PID=$!
cd ..

# Start frontend
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

# Kill both on Ctrl+C
trap "kill $BACKEND_PID $FRONTEND_PID" EXIT INT TERM

wait
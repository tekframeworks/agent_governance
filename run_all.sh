#!/usr/bin/env bash
# Starts everything: LLM app (:8000), Guardian app (:8787), single-agent app (:8080),
# multi-agent app (:8090) and the Streamlit demo (:8501). Press Ctrl+C to stop all of them.
#
#   ./run_all.sh        free the five ports if something is still using them, then start
#   ./run_all.sh stop   only free the five ports (stops leftover copies) and exit
set -euo pipefail
cd "$(dirname "$0")"

PORTS="8000 8787 8080 8090 8501"

free_ports() {  # kill whatever (of yours) is listening on our ports
  for port in $PORTS; do
    if ss -ltn | grep ":$port " > /dev/null; then
      echo "Port $port is in use, stopping it..."
      fuser -k "$port/tcp" > /dev/null 2>&1 || true
    fi
  done
  for _ in $(seq 1 10); do  # give the processes a moment to exit
    busy=""
    for port in $PORTS; do
      if ss -ltn | grep ":$port " > /dev/null; then busy="$busy $port"; fi
    done
    [ -z "$busy" ] && return 0
    sleep 1
  done
  echo "Could not free port(s):$busy. They may belong to another user: check with  lsof -i :<port>"
  exit 1
}

if [ "${1:-}" = "stop" ]; then
  free_ports
  echo "Ports $PORTS are free."
  exit 0
fi

if [ ! -d .venv ]; then
  echo "Missing .venv. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
source .venv/bin/activate

if ! grep -q '^OPENAI_API_KEY=.\+' .env 2>/dev/null; then
  echo "OPENAI_API_KEY is not set. Run: cp .env.example .env  and put your key in .env"
  exit 1
fi

free_ports

mkdir -p logs
pids=()
cleanup() {
  echo
  echo "Stopping all apps..."
  kill "${pids[@]}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait_for() {  # wait until http://127.0.0.1:$1/docs answers
  for _ in $(seq 1 30); do
    curl -sf "http://127.0.0.1:$1/docs" >/dev/null && return 0
    sleep 1
  done
  echo "Nothing came up on port $1. Check the .out files in logs/"
  exit 1
}

uvicorn llm:app --host 127.0.0.1 --port 8000 > logs/llm_app.out 2>&1 &
pids+=($!)
wait_for 8000
echo "LLM app          (app 2)  up on http://127.0.0.1:8000"

uvicorn guardian:app --host 127.0.0.1 --port 8787 > logs/guardian.out 2>&1 &
pids+=($!)
wait_for 8787
echo "Guardian app     (ACS)    up on http://127.0.0.1:8787"

uvicorn main:app --host 127.0.0.1 --port 8080 > logs/agent_app.out 2>&1 &
pids+=($!)
wait_for 8080
echo "Single agent     (app 1)  up on http://127.0.0.1:8080"

uvicorn multi_agents:app --host 127.0.0.1 --port 8090 > logs/multi_agents.out 2>&1 &
pids+=($!)
wait_for 8090
echo "Multi-agent      (app 1b) up on http://127.0.0.1:8090"

streamlit run demo.py --server.address 127.0.0.1 --server.port 8501 --server.headless true \
  --browser.gatherUsageStats false > logs/demo.out 2>&1 &
pids+=($!)
wait_for_streamlit() {
  for _ in $(seq 1 30); do
    curl -sf "http://127.0.0.1:8501/_stcore/health" >/dev/null && return 0
    sleep 1
  done
  echo "Streamlit did not start. Check logs/demo.out"
  exit 1
}
wait_for_streamlit
echo "Streamlit demo            up on http://127.0.0.1:8501"
echo
echo "Combined call log: logs/calls.log   (Ctrl+C to stop everything)"

wait -n  # exit as soon as any of them stops
echo "One of the apps stopped. See the .out files in logs/"

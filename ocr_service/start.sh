#!/bin/bash
# PaddleOCR Service Startup Script
PORT=${OCR_SERVICE_PORT:-8521}
LOG_FILE="ocr_service.log"
PID_FILE="ocr_service.pid"

start() {
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "OCR Service is already running (PID: $(cat $PID_FILE))"
        exit 1
    fi
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    echo "Starting PaddleOCR Service on port $PORT..."
    cd "$SCRIPT_DIR"
    nohup python3 app.py > "$LOG_FILE" 2>&1 &
    PID=$!
    echo $PID > "$PID_FILE"
    echo "Started (PID: $PID)"
    echo "Log: $LOG_FILE"
    sleep 2
    if curl -s http://127.0.0.1:$PORT/health > /dev/null 2>&1; then
        echo "Service is healthy"
    else
        echo "Warning: health check failed. Check log for details."
    fi
}

stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        echo "Stopping OCR Service (PID: $PID)..."
        kill $PID 2>/dev/null; rm -f "$PID_FILE"
        echo "Stopped"
    else
        echo "OCR Service is not running"
    fi
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "OCR Service is running (PID: $(cat $PID_FILE))"
        curl -s http://127.0.0.1:$PORT/health 2>/dev/null || echo "(health endpoint not responding)"
    else
        echo "OCR Service is not running"
    fi
}

case "${1:-start}" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 1; start ;;
    status)  status ;;
    *)       echo "Usage: $0 {start|stop|restart|status}" ;;
esac

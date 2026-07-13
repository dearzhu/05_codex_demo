#!/bin/bash
# Reset all data (database + vector store)
DIR="$(cd "$(dirname "$0")" && pwd)"
echo "Resetting knowledge base data..."
rm -f "$DIR/data/kb.db"
rm -rf "$DIR/data/chromadb"
mkdir -p "$DIR/data/chromadb"
echo "Done. Data cleared. Run start_api.sh to create fresh data."

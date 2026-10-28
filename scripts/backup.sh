#!/bin/bash
# Database backup script for immigration-knowledge
# Usage: ./scripts/backup.sh [output-file]

set -e

OUTPUT_FILE="${1:-immigration-$(date +%Y%m%d-%H%M%S).dump}"

echo "Backing up database to $OUTPUT_FILE..."
docker compose -f docker-compose.prod.yml exec -T postgres pg_dump -U immigration -d immigration -Fc > "$OUTPUT_FILE"
echo "Backup completed: $OUTPUT_FILE"

# Backup raw snapshots
SNAPSHOT_FILE="raw-snapshots-$(date +%Y%m%d-%H%M%S).tgz"
echo "Backing up raw snapshots to $SNAPSHOT_FILE..."
docker run --rm -v immigration-knowledge-prod_raw_snapshots:/raw:ro -v "$(pwd)":/backup alpine tar czf "/backup/$SNAPSHOT_FILE" -C /raw .
echo "Raw snapshots backup completed: $SNAPSHOT_FILE"
EOF

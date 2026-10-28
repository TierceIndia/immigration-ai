#!/bin/bash
# Database restore script for immigration-knowledge
# Usage: ./scripts/restore.sh <backup-file>

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <backup-file>"
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "Error: Backup file $BACKUP_FILE not found"
    exit 1
fi

echo "Restoring database from $BACKUP_FILE..."
echo "WARNING: This will overwrite the current database!"
read -p "Are you sure? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo "Restore cancelled"
    exit 1
fi

# Create new database
docker compose -f docker-compose.prod.yml exec -T postgres psql -U immigration -d immigration -c "DROP DATABASE IF EXISTS immigration_restore;"
docker compose -f docker-compose.prod.yml exec -T postgres psql -U immigration -d immigration -c "CREATE DATABASE immigration_restore;"
docker compose -f docker-compose.prod.yml exec -T postgres pg_restore -U immigration -d immigration_restore "$BACKUP_FILE"
echo "Restore completed to database: immigration_restore"
echo "Verify row counts before switching to production database"
EOF

#!/usr/bin/env bash
# SQLite 정본 백업. cron 으로 주기 실행. sqlite3 온라인 백업(.backup)이라
# 서버가 켜져 있어 WAL 에 쓰는 중에도 일관 스냅샷을 뜬다(파일 cp 는 손상 위험).
set -euo pipefail

DB="/home/vf/VF_scrim_Tracker/data/scrim.db"
DEST="/home/vf/backups"
RETAIN_DAYS=14

mkdir -p "$DEST"
STAMP="$(date +%Y%m%d-%H%M%S)"
sqlite3 "$DB" ".backup '$DEST/scrim-$STAMP.db'"

# 보존기간 초과분 정리.
find "$DEST" -name 'scrim-*.db' -mtime +"$RETAIN_DAYS" -delete
